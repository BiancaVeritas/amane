import {
  Alert,
  Badge,
  Box,
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
import { useNarrowViewport } from "@/hooks/use-narrow-viewport";

export const Route = createFileRoute("/tag-mappings")({ component: TagMappingsPage });

type Entry = {
  original: string;
  translation: string | null;
  language: string;
  usage_count: number;
  sources: string[];
  /** 归档标记. 「全部词条」里混着归档项, 行内操作要按它分别给. */
  archived?: boolean;
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
  // 窄屏改卡片布局: 七列的表格挤在手机宽度里会把标签压成竖排.
  const narrow = useNarrowViewport("md");
  const [language, setLanguage] = useState("all");
  const [frequency, setFrequency] = useState("all");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [selected, setSelected] = useState<string[]>([]);
  const selectionAnchor = useRef<string | null>(null);
  const [suggestError, setSuggestError] = useState<string | null>(null);
  const importRef = useRef<HTMLInputElement>(null);
  const archivedView = status === "archived";
  const query = useQuery({
    queryKey: ["tag-mappings", status, language, frequency, search, page],
    queryFn: () =>
      request<Result>(
        // 归档是一个独立的维度: 归档区里也能按语言/搜索筛, 所以状态与归并分开传.
        `/api/tag-mappings?status=${archivedView ? "all" : status}&archived=${archivedView}&language=${language}&frequency=${frequency}&search=${encodeURIComponent(search)}&offset=${(page - 1) * PAGE_SIZE}&limit=${PAGE_SIZE}`,
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
  // 归档区里同一个按钮改叫「还原」: 把词条放回主列表, 映射与计数一直没丢.
  const restore = useMutation({
    mutationFn: (original: string) =>
      request<void>(`/api/tag-mappings/restore/${encodeURIComponent(original)}`, {
        method: "POST",
      }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["tag-mappings"] }),
  });
  /** 清掉译名再归档: 服务端拒绝「带译名归档」, 这两步要连着做. */
  async function clearTranslationThenArchive(original: string) {
    await request<void>("/api/tag-mappings", {
      method: "PUT",
      body: JSON.stringify({ original, translation: null }),
    });
    await request<void>(`/api/tag-mappings/${encodeURIComponent(original)}`, { method: "DELETE" });
    setEdits((old) => {
      const next = { ...old };
      delete next[original];
      return next;
    });
    void qc.invalidateQueries({ queryKey: ["tag-mappings"] });
  }
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
    // 逐项按它自己的状态分流: 「全部词条」里同一批选中可能既有归档也有非归档.
    const results = await Promise.allSettled(
      targets.map((original) => {
        const isArchived = entries.find((item) => item.original === original)?.archived;
        return isArchived
          ? request<void>(`/api/tag-mappings/restore/${encodeURIComponent(original)}`, {
              method: "POST",
            })
          : request<void>(`/api/tag-mappings/${encodeURIComponent(original)}`, {
              method: "DELETE",
            });
      }),
    );
    const succeeded = targets.filter((_original, index) => results[index]?.status === "fulfilled");
    setSelected((old) => old.filter((original) => !succeeded.includes(original)));
    setEdits((old) => {
      const next = { ...old };
      for (const original of succeeded) delete next[original];
      return next;
    });
    const verb = archivedView ? "批量还原" : "批量归档";
    setSuggestError(
      results.some((result) => result.status === "rejected")
        ? `${verb}完成：成功 ${succeeded.length} 项，失败 ${targets.length - succeeded.length} 项`
        : null,
    );
    void qc.invalidateQueries({ queryKey: ["tag-mappings"] });
  }

  const entries = query.data?.items ?? [];
  // 「全部词条」里归档与非归档混在一页, 批量按钮要看选中项的实际状态决定动作.
  const selectedAllArchived =
    selected.length > 0 &&
    selected.every((original) => entries.find((item) => item.original === original)?.archived);
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
            // 归档区: 本来就是中文、不需要译名的那些. 仍然参与映射与计数, 只是不占主列表.
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
            // 归档区没有译名可确认: 那些词条本来就是中文, 不给填译名.
            archivedView ||
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
          {/* 「全部词条」里两种状态都有, 所以看选中项: 全是归档的就还原, 否则归档.
              叫「删除」会让人以为词条真的没了. */}
          {selectedAllArchived ? "批量还原" : "批量归档"}
        </Button>
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
      <Paper withBorder radius="md" style={{ overflowX: narrow ? undefined : "auto" }}>
        {narrow ? (
          // 窄屏用卡片: 七列的表格挤在手机宽度里会把标签压成竖排, 换行也没用.
          <Stack gap={0}>
            {entries.map((entry) => {
              const pendingTranslation = edits[entry.original] ?? entry.translation ?? "";
              const isSelected = selected.includes(entry.original);
              return (
                <Stack
                  key={entry.original}
                  gap="xs"
                  p="sm"
                  style={{ borderBottom: "1px solid var(--mantine-color-default-border)" }}
                >
                  <Group gap="xs" wrap="nowrap" align="flex-start">
                    <Checkbox
                      aria-label={`选择 ${entry.original}`}
                      checked={isSelected}
                      onChange={(event) => {
                        const checked = event.currentTarget.checked;
                        selectionAnchor.current = entry.original;
                        setSelected((old) =>
                          checked
                            ? [...new Set([...old, entry.original])]
                            : old.filter((value) => value !== entry.original),
                        );
                      }}
                    />
                    <Box style={{ flex: 1, minWidth: 0 }}>
                      <Text fw={500} size="sm" style={{ wordBreak: "break-word" }}>
                        {entry.original}
                      </Text>
                      <Group gap="xs" mt={2}>
                        <Text size="xs" c="dimmed">
                          {(
                            { ja: "日文", en: "英文", zh: "中文", other: "其他" } as Record<
                              string,
                              string
                            >
                          )[entry.language] ?? entry.language}
                        </Text>
                        <Text size="xs" c="dimmed">
                          ×{entry.usage_count}
                        </Text>
                        {entry.sources?.length ? (
                          <Text size="xs" c="dimmed" lineClamp={1} style={{ minWidth: 0 }}>
                            {entry.sources.join("、")}
                          </Text>
                        ) : null}
                      </Group>
                    </Box>
                  </Group>
                  {entry.archived ? (
                    <Group gap="xs" justify="space-between">
                      <Text size="xs" c="dimmed">
                        无需译名
                      </Text>
                      <Button
                        size="xs"
                        variant="light"
                        onClick={() => restore.mutate(entry.original)}
                        loading={restore.isPending}
                      >
                        还原
                      </Button>
                    </Group>
                  ) : (
                    <Stack gap="xs">
                      <TextInput
                        size="sm"
                        value={pendingTranslation}
                        placeholder="填写中文译名"
                        onChange={(e) => {
                          const value = e.currentTarget.value;
                          setEdits((old) => ({ ...old, [entry.original]: value }));
                        }}
                      />
                      <Group gap="xs" justify="flex-end">
                        <Button
                          size="xs"
                          onClick={() => save.mutate(entry)}
                          loading={save.isPending}
                        >
                          确认保存
                        </Button>
                        <Button
                          size="xs"
                          variant="subtle"
                          color="red"
                          disabled={pendingTranslation.trim().length > 0}
                          onClick={() => {
                            if (entry.translation) {
                              void clearTranslationThenArchive(entry.original);
                              return;
                            }
                            remove.mutate(entry.original);
                          }}
                        >
                          归档
                        </Button>
                      </Group>
                    </Stack>
                  )}
                </Stack>
              );
            })}
          </Stack>
        ) : (
          <Table
            striped
            highlightOnHover
            verticalSpacing="sm"
            style={{ tableLayout: "fixed", width: "100%" }}
          >
            <Table.Thead>
              <Table.Tr>
                <Table.Th style={{ width: 35 }}>
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
                      const checked = event.currentTarget.checked;
                      selectionAnchor.current = null;
                      setSelected((old) =>
                        checked
                          ? [...new Set([...old, ...entries.map((entry) => entry.original)])]
                          : old.filter(
                              (value) => !entries.some((entry) => entry.original === value),
                            ),
                      );
                    }}
                  />
                </Table.Th>
                <Table.Th style={{ width: "31%" }}>原始标签</Table.Th>
                <Table.Th style={{ width: "5%" }}>语言</Table.Th>
                <Table.Th style={{ width: "5%" }}>使用次数</Table.Th>
                <Table.Th style={{ width: "29%" }}>来源</Table.Th>
                <Table.Th style={{ width: "30%" }}>中文译名</Table.Th>
                <Table.Th style={{ width: 150 }}>操作</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {entries.map((entry) => {
                // 输入框里的当前值: 判据要跟着用户输入走, 不能等保存后才变.
                const pendingTranslation = edits[entry.original] ?? entry.translation ?? "";
                return (
                  <Table.Tr key={entry.original}>
                    <Table.Td>
                      <Checkbox
                        aria-label={`选择 ${entry.original}`}
                        checked={selected.includes(entry.original)}
                        onChange={(event) => {
                          const checked = event.currentTarget.checked;
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
                            const range = entries
                              .slice(start, end + 1)
                              .map((item) => item.original);
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
                        {/* 「全部词条」里混着归档项, 给个标记才分得清哪些已被挪走. */}
                        {entry.archived && (
                          <Badge size="xs" color="gray" variant="light">
                            已归档
                          </Badge>
                        )}
                      </Group>
                    </Table.Td>
                    <Table.Td>
                      {(
                        { ja: "日文", en: "英文", zh: "中文", other: "其他" } as Record<
                          string,
                          string
                        >
                      )[entry.language] ?? entry.language}
                    </Table.Td>
                    <Table.Td>{entry.usage_count}</Table.Td>
                    <Table.Td>
                      <Text size="xs" c="dimmed">
                        {entry.sources?.join("、") || "—"}
                      </Text>
                    </Table.Td>
                    <Table.Td style={{ paddingLeft: 0, paddingRight: 0 }}>
                      {entry.archived ? (
                        // 归档项没有「填译名」这件事: 这些词条本来就是中文, 只是不占主列表.
                        <Text size="xs" c="dimmed">
                          无需译名
                        </Text>
                      ) : (
                        // 输入框铺满整列: 与右侧「确认保存」之间不留空档.
                        <TextInput
                          size="sm"
                          styles={{
                            root: { width: "100%" },
                            wrapper: { width: "100%" },
                            input: { width: "100%" },
                          }}
                          value={edits[entry.original] ?? entry.translation ?? ""}
                          placeholder="填写中文译名"
                          onChange={(e) => {
                            const value = e.currentTarget.value;
                            setEdits((old) => ({ ...old, [entry.original]: value }));
                          }}
                        />
                      )}
                    </Table.Td>
                    <Table.Td style={{ paddingLeft: 6, paddingRight: 0 }}>
                      {/* 按钮靠左排: 列内靠右会把它推离左侧输入框, 中间就空出一条 */}
                      <Group gap="xs" wrap="nowrap" justify="flex-start">
                        {entry.archived ? (
                          <Button
                            size="xs"
                            variant="light"
                            onClick={() => restore.mutate(entry.original)}
                            loading={restore.isPending}
                          >
                            还原
                          </Button>
                        ) : (
                          <>
                            <Button
                              size="xs"
                              onClick={() => save.mutate(entry)}
                              loading={save.isPending}
                            >
                              确认保存
                            </Button>
                            <Button
                              size="xs"
                              variant="subtle"
                              color="red"
                              // 判据取输入框里的当前值, 不取已保存的译名: 用户把译名清掉的那一刻
                              // 就该能点归档, 不该还逼他先按保存 —— 那样会先落进「未映射」,
                              // 还得再翻回去找.
                              disabled={pendingTranslation.trim().length > 0}
                              title={
                                pendingTranslation.trim()
                                  ? "已映射的词条不能归档, 请先清空中文译名"
                                  : undefined
                              }
                              onClick={() => {
                                // 输入框已清空但库里还有译名: 归档时一并清掉, 否则服务端会拒.
                                if (entry.translation) {
                                  void clearTranslationThenArchive(entry.original);
                                  return;
                                }
                                remove.mutate(entry.original);
                              }}
                            >
                              归档
                            </Button>
                          </>
                        )}
                      </Group>
                    </Table.Td>
                  </Table.Tr>
                );
              })}
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
        )}
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
