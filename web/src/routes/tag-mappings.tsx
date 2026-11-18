import {
  Alert,
  Badge,
  Button,
  Checkbox,
  Group,
  Pagination,
  Paper,
  Select,
  Stack,
  Table,
  Text,
  TextInput,
  Title,
  Tooltip,
} from "@mantine/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import { useRef, useState } from "react";
import { apiFetch } from "@/lib/api-token";

export const Route = createFileRoute("/tag-mappings")({ component: TagMappingsPage });

type Entry = {
  original: string;
  translation: string | null;
  language: string;
  usage_count: number;
  sources: string[];
  archived: boolean;
};
type Result = {
  items: Entry[];
  total: number;
  stats: { all: number; pending: number; mapped: number; archived: number };
};
const PAGE_SIZE = 30;

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await apiFetch(url, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) throw new Error(await response.text());
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

function TagMappingsPage() {
  const qc = useQueryClient();
  const [status, setStatus] = useState("pending");
  const [language, setLanguage] = useState("all");
  const [frequency, setFrequency] = useState("all");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [selected, setSelected] = useState<string[]>([]);
  const selectionAnchor = useRef<string | null>(null);
  const [suggestError, setSuggestError] = useState<string | null>(null);
  const importRef = useRef<HTMLInputElement>(null);
  const mappingState = useQuery({
    queryKey: ["tag-mapping-state"],
    queryFn: () => request<{ enabled: boolean }>("/api/tag-mappings/state"),
  });
  const toggleMapping = useMutation({
    mutationFn: (enabled: boolean) =>
      request<{ enabled: boolean }>("/api/tag-mappings/state", {
        method: "PUT",
        body: JSON.stringify({ enabled }),
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["tag-mapping-state"] });
      void qc.invalidateQueries();
    },
  });
  const query = useQuery({
    queryKey: ["tag-mappings", status, language, frequency, search, page],
    queryFn: () =>
      request<Result>(
        `/api/tag-mappings?status=${status}&language=${language}&frequency=${frequency}&search=${encodeURIComponent(search)}&offset=${(page - 1) * PAGE_SIZE}&limit=${PAGE_SIZE}`,
      ),
  });
  const save = useMutation({
    mutationFn: (entry: Entry) =>
      request<Entry>("/api/tag-mappings", {
        method: "PUT",
        body: JSON.stringify({
          original: entry.original,
          translation: edits[entry.original] ?? entry.translation,
        }),
      }),
    onSuccess: (_result, entry) => {
      setEdits((old) => {
        const next = { ...old };
        delete next[entry.original];
        return next;
      });
      setSelected((old) => old.filter((value) => value !== entry.original));
      void qc.invalidateQueries({ queryKey: ["tag-mappings"] });
    },
  });
  const suggest = useMutation({
    mutationFn: (originals: string[]) =>
      request<{ suggestions: Record<string, string> }>("/api/tag-mappings/suggest", {
        method: "POST",
        body: JSON.stringify({ originals }),
      }),
    onSuccess: (result) => {
      setEdits((old) => ({ ...old, ...result.suggestions }));
      setSuggestError(null);
    },
    onError: (error) => setSuggestError(String(error)),
  });
  const importDictionary = useMutation({
    mutationFn: async (file: File) =>
      request<{ imported: number }>("/api/tag-mappings/import", {
        method: "POST",
        body: JSON.stringify(JSON.parse(await file.text())),
      }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["tag-mappings"] }),
  });
  const archiveOne = useMutation({
    mutationFn: (original: string) =>
      request<{ archived: boolean }>(`/api/tag-mappings/${encodeURIComponent(original)}/archive`, {
        method: "PUT",
      }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["tag-mappings"] }),
  });
  const restoreOne = useMutation({
    mutationFn: (original: string) =>
      request<{ archived: boolean }>(`/api/tag-mappings/${encodeURIComponent(original)}/restore`, {
        method: "PUT",
      }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["tag-mappings"] }),
  });

  async function batchConfirmSelected() {
    const targets = selected.filter((original) =>
      Object.prototype.hasOwnProperty.call(edits, original),
    );
    if (!targets.length) return;
    const results = await Promise.allSettled(
      targets.map((original) =>
        request<Entry>("/api/tag-mappings", {
          method: "PUT",
          body: JSON.stringify({ original, translation: edits[original] }),
        }),
      ),
    );
    const succeeded = targets.filter((_original, index) => results[index]?.status === "fulfilled");
    setEdits((old) => {
      const next = { ...old };
      for (const original of succeeded) delete next[original];
      return next;
    });
    setSelected((old) => old.filter((original) => !succeeded.includes(original)));
    setSuggestError(
      results.some((result) => result.status === "rejected")
        ? `批量确认完成：成功 ${succeeded.length} 项，失败 ${targets.length - succeeded.length} 项`
        : null,
    );
    void qc.invalidateQueries({ queryKey: ["tag-mappings"] });
  }

  async function batchArchiveSelected() {
    if (!selected.length) return;
    try {
      const result = await request<{ archived: number; skipped: number }>(
        "/api/tag-mappings/archive",
        {
          method: "POST",
          body: JSON.stringify({ originals: selected }),
        },
      );
      setSelected([]);
      selectionAnchor.current = null;
      setSuggestError(
        `批量归档完成：成功归档 ${result.archived} 条，跳过 ${result.skipped} 条（已有中文译名或词条不存在）`,
      );
      void qc.invalidateQueries({ queryKey: ["tag-mappings"] });
    } catch (error) {
      setSuggestError(String(error));
    }
  }

  async function batchRestoreSelected() {
    if (!selected.length) return;
    try {
      const result = await request<{ restored: number }>("/api/tag-mappings/restore", {
        method: "POST",
        body: JSON.stringify({ originals: selected }),
      });
      setSelected([]);
      selectionAnchor.current = null;
      setSuggestError(`批量还原完成：成功还原 ${result.restored} 条`);
      void qc.invalidateQueries({ queryKey: ["tag-mappings"] });
    } catch (error) {
      setSuggestError(String(error));
    }
  }

  const entries = query.data?.items ?? [];
  const pages = Math.max(1, Math.ceil((query.data?.total ?? 0) / PAGE_SIZE));
  const mappingEnabled = mappingState.data?.enabled ?? true;
  return (
    <Stack gap="md" pb={120}>
      <Group justify="space-between" align="center">
        <div>
          <Group align="center" gap="sm">
            <Title order={2}>标签中文映射</Title>
            <Button
              size="sm"
              variant={mappingEnabled ? "filled" : "light"}
              color={mappingEnabled ? "green" : "gray"}
              loading={toggleMapping.isPending || mappingState.isLoading}
              disabled={mappingState.isLoading}
              onClick={() => toggleMapping.mutate(!mappingEnabled)}
            >
              {mappingEnabled ? "已启用" : "已停用"}
            </Button>
          </Group>
          <Text c="dimmed" size="sm">
            原始标签保留不变；确认映射后，影片标签展示使用中文结果。
          </Text>
          <Text c="dimmed" size="xs">
            开启：已映射标签显示中文，未映射标签保留原文。关闭：所有标签均显示原文，NFO
            也写入原文；映射词典不会被删除。
          </Text>
        </div>
        <Group>
          <Button
            variant="light"
            onClick={() => suggest.mutate(selected)}
            disabled={!selected.length || selected.length > 30 || suggest.isPending}
            loading={suggest.isPending}
          >
            请求 LLM 建议（{selected.length}）
          </Button>
          <Button
            variant="light"
            onClick={() => importRef.current?.click()}
            loading={importDictionary.isPending}
          >
            导入词典
          </Button>
          <input
            ref={importRef}
            hidden
            type="file"
            accept="application/json,.json"
            onChange={(event) => {
              const file = event.currentTarget.files?.[0];
              if (file) importDictionary.mutate(file);
              event.currentTarget.value = "";
            }}
          />
          <Button component="a" href="/api/tag-mappings/export" variant="light">
            导出词典
          </Button>
        </Group>
      </Group>
      <Group grow align="end">
        <Select
          label="词条状态"
          value={status}
          onChange={(v) => {
            setStatus(v ?? "pending");
            setPage(1);
            setSelected([]);
            selectionAnchor.current = null;
          }}
          data={[
            { value: "pending", label: "待映射" },
            { value: "mapped", label: "已映射" },
            { value: "archived", label: "已归档" },
            { value: "all", label: "全部词条" },
          ]}
        />
        <Select
          label="原始语言"
          value={language}
          onChange={(v) => {
            setLanguage(v ?? "all");
            setPage(1);
            setSelected([]);
            selectionAnchor.current = null;
          }}
          data={[
            { value: "all", label: "全部语言" },
            { value: "ja", label: "日文" },
            { value: "en", label: "英文" },
            { value: "zh", label: "中文" },
            { value: "other", label: "其他/未知" },
          ]}
        />
        <Select
          label="使用频率"
          value={frequency}
          onChange={(v) => {
            setFrequency(v ?? "all");
            setPage(1);
            setSelected([]);
            selectionAnchor.current = null;
          }}
          data={[
            { value: "all", label: "全部频率" },
            { value: "high", label: "高频（至少 5 次）" },
          ]}
        />
        <TextInput
          label="搜索原文或中文译名"
          value={search}
          onChange={(e) => {
            setSearch(e.currentTarget.value);
            setPage(1);
            setSelected([]);
            selectionAnchor.current = null;
          }}
        />
      </Group>
      <Group gap="xs">
        <Button
          size="xs"
          disabled={
            !selected.length ||
            !selected.some((original) => Object.prototype.hasOwnProperty.call(edits, original))
          }
          onClick={() => void batchConfirmSelected()}
        >
          批量确认
        </Button>
        {status === "archived" ? (
          <Button size="xs" disabled={!selected.length} onClick={() => void batchRestoreSelected()}>
            批量还原
          </Button>
        ) : (
          <Button
            size="xs"
            color="gray"
            variant="light"
            disabled={!selected.length}
            onClick={() => void batchArchiveSelected()}
          >
            批量归档
          </Button>
        )}
        <Text size="xs" c="dimmed">
          已选择 {selected.length} 项
        </Text>
      </Group>
      <Group gap="xs">
        <Badge color="orange">待映射 {query.data?.stats.pending ?? 0}</Badge>
        <Badge color="teal">已映射 {query.data?.stats.mapped ?? 0}</Badge>
        <Badge color="gray">已归档 {query.data?.stats.archived ?? 0}</Badge>
        <Badge>全部 {query.data?.stats.all ?? 0}</Badge>
        <Text size="xs" c="dimmed">
          默认按使用次数从高到低排列
        </Text>
      </Group>
      {query.isError && <Alert color="red">加载失败：{String(query.error)}</Alert>}
      {suggestError && <Alert color="red">{suggestError}</Alert>}
      <Text size="xs" c="dimmed">
        每页显示 30 个词条；勾选起点后按住 Shift 再勾选终点，可选择范围。每次最多选择 30
        个词条请求翻译建议；建议只填入编辑框，必须逐条点击“确认保存”才会入库。
      </Text>
      <Paper withBorder radius="md" style={{ overflowX: "auto" }}>
        <Table striped highlightOnHover miw={760} verticalSpacing="sm">
          <Table.Thead>
            <Table.Tr>
              <Table.Th>
                <Checkbox
                  aria-label="选择全部当前页"
                  checked={
                    entries.length > 0 &&
                    entries.every((entry) => selected.includes(entry.original))
                  }
                  indeterminate={
                    entries.some((entry) => selected.includes(entry.original)) &&
                    !entries.every((entry) => selected.includes(entry.original))
                  }
                  onChange={(event) => {
                    const checked =
                      event.target instanceof HTMLInputElement && event.target.checked;
                    selectionAnchor.current = null;
                    setSelected((old) =>
                      checked
                        ? [...new Set([...old, ...entries.map((entry) => entry.original)])]
                        : old.filter((value) => !entries.some((entry) => entry.original === value)),
                    );
                  }}
                />
              </Table.Th>
              <Table.Th>原始标签</Table.Th>
              <Table.Th>语言</Table.Th>
              <Table.Th>使用次数</Table.Th>
              <Table.Th>来源</Table.Th>
              <Table.Th>中文译名</Table.Th>
              <Table.Th>操作</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {entries.map((entry) => (
              <Table.Tr key={entry.original}>
                <Table.Td>
                  <Checkbox
                    aria-label={`选择 ${entry.original}`}
                    checked={selected.includes(entry.original)}
                    onChange={(event) => {
                      const checked =
                        event.target instanceof HTMLInputElement && event.target.checked;
                      const shiftKey = (event.nativeEvent as MouseEvent).shiftKey;
                      const anchor = selectionAnchor.current;
                      const anchorIndex = anchor
                        ? entries.findIndex((item) => item.original === anchor)
                        : -1;
                      const currentIndex = entries.findIndex(
                        (item) => item.original === entry.original,
                      );
                      if (shiftKey && anchorIndex >= 0 && currentIndex >= 0) {
                        const start = Math.min(anchorIndex, currentIndex);
                        const end = Math.max(anchorIndex, currentIndex);
                        const range = entries.slice(start, end + 1).map((item) => item.original);
                        setSelected((old) =>
                          checked
                            ? [...new Set([...old, ...range])]
                            : old.filter((value) => !range.includes(value)),
                        );
                      } else {
                        selectionAnchor.current = entry.original;
                        setSelected((old) =>
                          checked
                            ? [...new Set([...old, entry.original])]
                            : old.filter((value) => value !== entry.original),
                        );
                      }
                    }}
                  />
                </Table.Td>
                <Table.Td>
                  <Group gap="xs" wrap="nowrap">
                    <Text fw={500}>{entry.original}</Text>
                    {entry.archived ? (
                      <Badge size="xs" color="gray">
                        已归档
                      </Badge>
                    ) : entry.translation ? (
                      <Badge size="xs" color="teal">
                        已映射
                      </Badge>
                    ) : null}
                  </Group>
                </Table.Td>
                <Table.Td>
                  {(
                    { ja: "日文", en: "英文", zh: "中文", other: "其他" } as Record<string, string>
                  )[entry.language] ?? entry.language}
                </Table.Td>
                <Table.Td>{entry.usage_count}</Table.Td>
                <Table.Td>
                  <Text size="xs" c="dimmed">
                    {entry.sources?.join("、") || "—"}
                  </Text>
                </Table.Td>
                <Table.Td>
                  <TextInput
                    size="sm"
                    value={edits[entry.original] ?? entry.translation ?? ""}
                    placeholder={entry.archived ? "无需译名" : "填写中文译名"}
                    onChange={(e) => {
                      const value = e.currentTarget.value;
                      setEdits((old) => ({ ...old, [entry.original]: value }));
                    }}
                  />
                </Table.Td>
                <Table.Td>
                  <Group gap="xs" wrap="nowrap">
                    <Button size="xs" onClick={() => save.mutate(entry)} loading={save.isPending}>
                      确认保存
                    </Button>
                    {entry.archived ? (
                      <Button
                        size="xs"
                        variant="light"
                        onClick={() => restoreOne.mutate(entry.original)}
                      >
                        还原
                      </Button>
                    ) : (
                      <Tooltip
                        label="已映射的词条不能归档，请先清空中文译名"
                        color="white"
                        c="black"
                        withArrow
                        disabled={!(edits[entry.original] ?? entry.translation ?? "").trim()}
                      >
                        <span>
                          <Button
                            size="xs"
                            variant="subtle"
                            color="gray"
                            disabled={!!(edits[entry.original] ?? entry.translation ?? "").trim()}
                            onClick={() => archiveOne.mutate(entry.original)}
                          >
                            归档
                          </Button>
                        </span>
                      </Tooltip>
                    )}
                  </Group>
                </Table.Td>
              </Table.Tr>
            ))}
            {!entries.length && (
              <Table.Tr>
                <Table.Td colSpan={7}>
                  <Text c="dimmed" ta="center" py="lg">
                    没有符合条件的词条
                  </Text>
                </Table.Td>
              </Table.Tr>
            )}
          </Table.Tbody>
        </Table>
      </Paper>
      <Group justify="space-between">
        <Text size="sm" c="dimmed">
          共 {query.data?.total ?? 0} 条
        </Text>
        <Pagination
          total={pages}
          value={page}
          onChange={(nextPage) => {
            setPage(nextPage);
            selectionAnchor.current = null;
          }}
        />
      </Group>
      <Alert color="blue" variant="light">
        LLM
        建议只填入编辑框，不会自动保存；现有片库词条在打开列表时从标签分类表同步，并按引用次数排序。
      </Alert>
    </Stack>
  );
}
