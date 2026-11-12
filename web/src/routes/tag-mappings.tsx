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
};
type Result = {
  items: Entry[];
  total: number;
  stats: { all: number; pending: number; mapped: number };
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
  const remove = useMutation({
    mutationFn: (original: string) =>
      request<void>(`/api/tag-mappings/${encodeURIComponent(original)}`, { method: "DELETE" }),
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

  async function batchDeleteSelected() {
    const targets = [...selected];
    if (!targets.length) return;
    const results = await Promise.allSettled(
      targets.map((original) =>
        request<void>(`/api/tag-mappings/${encodeURIComponent(original)}`, { method: "DELETE" }),
      ),
    );
    const succeeded = targets.filter((_original, index) => results[index]?.status === "fulfilled");
    setSelected((old) => old.filter((original) => !succeeded.includes(original)));
    setEdits((old) => {
      const next = { ...old };
      for (const original of succeeded) delete next[original];
      return next;
    });
    setSuggestError(
      results.some((result) => result.status === "rejected")
        ? `批量删除完成：成功 ${succeeded.length} 项，失败 ${targets.length - succeeded.length} 项`
        : null,
    );
    void qc.invalidateQueries({ queryKey: ["tag-mappings"] });
  }

  const entries = query.data?.items ?? [];
  const pages = Math.max(1, Math.ceil((query.data?.total ?? 0) / PAGE_SIZE));
  return (
    <Stack gap="md" pb={120}>
      <Group justify="space-between" align="center">
        <div>
          <Title order={2}>标签中文映射</Title>
          <Text c="dimmed" size="sm">
            原始标签保留不变；确认映射后，影片标签展示使用中文结果。
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
        <Button
          size="xs"
          color="red"
          variant="light"
          disabled={!selected.length}
          onClick={() => void batchDeleteSelected()}
        >
          批量删除
        </Button>
        <Text size="xs" c="dimmed">
          已选择 {selected.length} 项
        </Text>
      </Group>
      <Group gap="xs">
        <Badge color="orange">待映射 {query.data?.stats.pending ?? 0}</Badge>
        <Badge color="teal">已映射 {query.data?.stats.mapped ?? 0}</Badge>
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
                  <Text fw={500}>{entry.original}</Text>
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
                    placeholder="填写中文译名"
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
                    <Button
                      size="xs"
                      variant="subtle"
                      color="red"
                      onClick={() => remove.mutate(entry.original)}
                    >
                      删除
                    </Button>
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
