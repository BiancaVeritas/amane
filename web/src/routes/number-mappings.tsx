import {
  Alert,
  Button,
  Checkbox,
  Divider,
  Group,
  Modal,
  Paper,
  Pagination,
  Select,
  SimpleGrid,
  Stack,
  Table,
  Text,
  TextInput,
  Tabs,
  Title,
} from "@mantine/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import { apiFetch } from "@/lib/api-token";

export const Route = createFileRoute("/number-mappings")({ component: NumberMappingsPage });
type Source = "caribbeancompr" | "d2pass" | "caribbeancom";
type Entry = {
  key: string;
  id: string | number;
  title?: string | null;
  actor?: string | null;
  override_title?: string | null;
};
type Conflict = {
  group_id: number;
  key: string;
  old_key: string;
  reason: string;
  old?: Entry;
  new?: Entry;
};
function errorText(error: unknown): string {
  const raw = error instanceof Error ? error.message : String(error);
  try {
    const parsed = JSON.parse(raw) as { detail?: unknown };
    if (typeof parsed.detail === "string") return parsed.detail;
  } catch {
    /* plain text */
  }
  return raw.replace(/^Error: /, "");
}
const SOURCES: { value: Source; label: string; hint: string }[] = [
  {
    value: "caribbeancompr",
    label: "caribbeancompr",
    hint: "HEYZO 番号使用四位数字，不带 HEYZO 前缀",
  },
  {
    value: "d2pass",
    label: "d2pass",
    hint: "HEYZO 番号使用四位数字，不带 HEYZO 前缀；ID 必须为整数",
  },
  {
    value: "caribbeancom",
    label: "caribbeancom",
    hint: "使用 Amane 解析后的番号匹配；字母大小写不敏感，符号和数字必须精确一致",
  },
];
async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await apiFetch(url, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) throw new Error(await response.text());
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}
const emptyEntry = (): Entry => ({ key: "", id: "", actor: "", title: "" });
function NumberMappingsPage() {
  const qc = useQueryClient();
  const [source, setSource] = useState<Source>("caribbeancompr");
  const [helpOpened, setHelpOpened] = useState(false);
  const [search, setSearch] = useState("");
  const [mappingPage, setMappingPage] = useState(1);
  const mappingPageSize = 30;
  const [draft, setDraft] = useState<Entry>(emptyEntry());
  const [editingKey, setEditingKey] = useState<string | null>(null);
  const [editDraft, setEditDraft] = useState<Entry>(emptyEntry());
  const [conflicts, setConflicts] = useState<Conflict[]>([]);
  const [conflictSearch, setConflictSearch] = useState("");
  const [conflictPage, setConflictPage] = useState(1);
  const conflictPageSize = 10;
  const [message, setMessage] = useState<string | null>(null);
  const [selectedKeys, setSelectedKeys] = useState<string[]>([]);
  const fileRef = useRef<HTMLInputElement>(null);
  const tableRef = useRef<HTMLInputElement>(null);
  const [startColumn, setStartColumn] = useState("1");
  const [skipHeader, setSkipHeader] = useState(false);
  const [importMode, setImportMode] = useState<"merge" | "replace">("merge");
  useEffect(() => {
    if (!message) return;
    const timer = window.setTimeout(() => setMessage(null), 5000);
    return () => window.clearTimeout(timer);
  }, [message]);
  const selectedSource = SOURCES.find((item) => item.value === source)!;
  const query = useQuery({
    queryKey: ["number-mappings", source, search, mappingPage],
    queryFn: () =>
      request<{ items: Entry[]; total: number }>(
        `/api/number-mappings?source=${source}&search=${encodeURIComponent(search)}&limit=${mappingPageSize}&offset=${(mappingPage - 1) * mappingPageSize}`,
      ),
  });
  useEffect(() => {
    void request<Conflict[]>(`/api/number-mappings/exceptions?source=${source}`)
      .then(setConflicts)
      .catch((error) => setMessage(`异常列表读取失败：${errorText(error)}`));
  }, [source]);
  const pageCount = Math.max(1, Math.ceil((query.data?.total ?? 0) / mappingPageSize));
  const filteredConflicts = conflicts.filter(
    (item) =>
      !conflictSearch ||
      [
        item.key,
        item.old_key,
        String(item.old?.id ?? ""),
        String(item.new?.id ?? ""),
        item.old?.actor ?? "",
        item.new?.actor ?? "",
        item.old?.title ?? "",
        item.new?.title ?? "",
      ].some((value) => value.toLowerCase().includes(conflictSearch.toLowerCase())),
  );
  const conflictPageCount = Math.max(1, Math.ceil(filteredConflicts.length / conflictPageSize));
  const visibleConflicts = filteredConflicts.slice(
    (conflictPage - 1) * conflictPageSize,
    conflictPage * conflictPageSize,
  );
  const refresh = () => void qc.invalidateQueries({ queryKey: ["number-mappings"] });
  const saveNew = useMutation({
    mutationFn: () =>
      request<Entry>(`/api/number-mappings?source=${source}`, {
        method: "PUT",
        body: JSON.stringify(draft),
      }),
    onSuccess: () => {
      setDraft(emptyEntry());
      setMessage("新增映射成功");
      refresh();
    },
    onError: (error) => setMessage(`新增失败：${errorText(error)}`),
  });
  const saveEdit = useMutation({
    mutationFn: (entry: Entry) =>
      request<Entry>(`/api/number-mappings?source=${source}`, {
        method: "PUT",
        body: JSON.stringify({ ...entry, original_key: editingKey }),
      }),
    onSuccess: () => {
      setEditingKey(null);
      setEditDraft(emptyEntry());
      setMessage("映射修改已保存");
      refresh();
    },
    onError: (error) => setMessage(`保存失败：${errorText(error)}`),
  });
  const saveConflict = useMutation({
    mutationFn: (payload: { entry: Entry; group_id: number; side: "old" | "new" }) =>
      request<Conflict[]>(`/api/number-mappings/exceptions/save?source=${source}`, {
        method: "POST",
        body: JSON.stringify({
          group_id: payload.group_id,
          side: payload.side,
          entry: payload.entry,
        }),
      }),
    onSuccess: (items) => {
      setConflicts(items);
      setMessage("异常记录已保存并加入正式映射");
      refresh();
    },
    onError: (error) => setMessage(`异常记录保存失败：${errorText(error)}`),
  });
  const remove = useMutation({
    mutationFn: (key: string) =>
      request(`/api/number-mappings/${encodeURIComponent(key)}?source=${source}`, {
        method: "DELETE",
      }),
    onSuccess: () => {
      setMessage("映射已删除");
      refresh();
    },
    onError: (error) => setMessage(`删除失败：${errorText(error)}`),
  });
  const batchRemove = useMutation({
    mutationFn: () =>
      request<{ deleted: number }>(`/api/number-mappings/batch-delete?source=${source}`, {
        method: "POST",
        body: JSON.stringify({ keys: selectedKeys }),
      }),
    onSuccess: (result) => {
      setMessage(`已删除 ${result.deleted} 条`);
      setSelectedKeys([]);
      refresh();
    },
    onError: (error) => setMessage(`批量删除失败：${errorText(error)}`),
  });
  function changeId(entry: Entry, value: string): Entry {
    return {
      ...entry,
      id: source === "d2pass" && value.trim() !== "" && /^\d+$/.test(value) ? Number(value) : value,
    };
  }
  function updateConflict(groupId: number, side: "old" | "new", patch: Partial<Entry>) {
    setConflicts((items) => {
      const next = items.map((item) =>
        item.group_id !== groupId
          ? item
          : { ...item, [side]: item[side] ? { ...item[side]!, ...patch } : undefined },
      );
      const changed = next.find((item) => item.group_id === groupId);
      if (changed?.[side])
        void request<Conflict[]>(`/api/number-mappings/exceptions?source=${source}`, {
          method: "PUT",
          body: JSON.stringify({ group_id: groupId, side, entry: changed[side] }),
        })
          .then(setConflicts)
          .catch((error) => setMessage(`异常记录暂存失败：${errorText(error)}`));
      return next;
    });
  }
  function deleteConflict(groupId: number, side: "old" | "new") {
    void request<Conflict[]>(`/api/number-mappings/exceptions?source=${source}`, {
      method: "PUT",
      body: JSON.stringify({ group_id: groupId, side, entry: null }),
    })
      .then(setConflicts)
      .catch((error) => setMessage(`异常记录删除失败：${errorText(error)}`));
  }
  async function importTable(file: File) {
    const body = new FormData();
    body.append("file", file);
    body.append("start_column", startColumn);
    body.append("skip_header", String(skipHeader));
    try {
      const response = await apiFetch(`/api/number-mappings/import-table?source=${source}`, {
        method: "POST",
        body,
      });
      if (!response.ok) throw new Error(await response.text());
      const result = (await response.json()) as {
        converted: number;
        added: string[];
        conflicts: Conflict[];
      };
      setConflicts(await request<Conflict[]>(`/api/number-mappings/exceptions?source=${source}`));
      setConflictPage(1);
      setMessage(
        `整理导入 ${result.converted} 条；新增 ${result.added.length} 条；异常 ${result.conflicts.length} 组`,
      );
      refresh();
    } catch (error) {
      setMessage(`表格导入失败：${errorText(error)}`);
    }
  }
  async function importFile(file: File) {
    try {
      let data: Record<
        string,
        { id: string | number; title?: string; actor?: string; override_title?: string }
      >;
      try {
        data = JSON.parse(await file.text()) as typeof data;
      } catch (error) {
        throw new Error(`JSON 文件格式错误，尚未提交导入：${errorText(error)}`, { cause: error });
      }
      const result = await request<{ added: string[]; conflicts: Conflict[] }>(
        `/api/number-mappings/import?source=${source}`,
        { method: "POST", body: JSON.stringify({ data, mode: importMode }) },
      );
      if (importMode === "replace") {
        setConflicts(await request<Conflict[]>(`/api/number-mappings/exceptions?source=${source}`));
        setMessage("完整替换完成；导入文件未包含的旧映射已移除");
      } else {
        setConflicts(await request<Conflict[]>(`/api/number-mappings/exceptions?source=${source}`));
        setConflictPage(1);
        setMessage(`新增 ${result.added.length} 条；异常 ${result.conflicts.length} 组`);
      }
      refresh();
    } catch (error) {
      setMessage(`导入失败：${errorText(error)}`);
    }
  }
  const conflictCount = conflicts.reduce(
    (sum, item) => sum + Number(Boolean(item.old)) + Number(Boolean(item.new)),
    0,
  );
  return (
    <Stack gap="md" pb={100}>
      <Modal
        opened={helpOpened}
        onClose={() => setHelpOpened(false)}
        title="番号映射管理 · 使用说明"
        size="xl"
        centered
      >
        <Stack gap="md">
          <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
            <Paper withBorder p="md" radius="md">
              <Stack gap="sm">
                <Group justify="space-between" align="center">
                  <Title order={4}>caribbeancom</Title>
                  <Text size="xs" fw={700} c="blue">
                    caribbeancompr 专用映射
                  </Text>
                </Group>
                <Text fw={600} size="sm">
                  映射表用途
                </Text>
                <Text size="sm">
                  本映射表专供 Caribbeancom Index 刮削源使用，用于查找 Caribbeancompr 网站上非 HEYZO
                  番号的影片页面。不能用于其他刮削源。
                </Text>
                <Divider />
                <Text fw={600} size="sm">
                  番号匹配规则
                </Text>
                <Text size="sm">
                  使用 Amane 获取并整理后的影片番号进行匹配，而不是直接使用本地视频完整文件名。
                </Text>
                <Text size="sm">• 番号中的英文字母大小写不敏感。</Text>
                <Text size="sm">• 番号中的符号和数字必须精确匹配，不能省略或改变。</Text>
                <Divider />
                <Text fw={600} size="sm">
                  ID 的作用
                </Text>
                <Text size="sm">
                  填写 Caribbeancompr 网站对应影片详情页面的商品 ID，用于定位影片网页。
                </Text>
              </Stack>
            </Paper>
            <Paper withBorder p="md" radius="md">
              <Stack gap="sm">
                <Group justify="space-between" align="center">
                  <Title order={4}>caribbeancompr / d2pass</Title>
                  <Text size="xs" fw={700} c="blue">
                    HEYZO 专用映射
                  </Text>
                </Group>
                <Text fw={600} size="sm">
                  映射表用途
                </Text>
                <Text size="sm">
                  <b>caribbeancompr：</b>专供 Caribbeancompr 刮削源使用，用于查找该来源的 HEYZO
                  影片。
                </Text>
                <Text size="sm">
                  <b>d2pass：</b>专供 D2Pass 刮削源使用，用于查找该来源的 HEYZO 影片。
                </Text>
                <Text size="sm">两套映射分别由各自的刮削源读取，不能混用。</Text>
                <Divider />
                <Text fw={600} size="sm">
                  番号填写规则
                </Text>
                <Text size="sm">
                  只填写 HEYZO 番号末尾的四位数字，不带 HEYZO-
                  前缀；数字必须完整、准确，不能少位或多位。
                </Text>
                <Divider />
                <Text fw={600} size="sm">
                  ID 的作用
                </Text>
                <Text size="sm">
                  填写对应网站影片详情页面的商品 ID，用于定位番号对应的影片网页。
                </Text>
              </Stack>
            </Paper>
          </SimpleGrid>
          <Paper withBorder p="md" radius="md">
            <Stack gap="sm">
              <Title order={4}>导入与编辑注意事项</Title>
              <Text size="sm">
                • 新增、编辑及导入映射时，字段排列顺序应按照映射编辑界面显示的顺序填写。
              </Text>
              <Text size="sm">
                • 请根据当前选择的映射来源填写番号和 ID，不同来源的格式和用途不同。
              </Text>
              <Text size="sm">• 导入时请确保番号与对应 ID 正确关联，避免错误映射。</Text>
              <Divider />
              <Title order={4}>两套番号映射系统的区别</Title>
              <Text size="sm">
                <b>设置 → 影片刮削 → 番号映射规则：</b>
                用于自动检索刮削时转换搜索番号，将原始番号转换为提交给刮削源检索的番号。
              </Text>
              <Text size="sm">
                <b>番号映射管理（当前页面）：</b>
                用于对应刮削源读取专用外置映射表，根据映射规则查找影片网页及商品 ID。
              </Text>
              <Text size="sm" fw={700}>
                两套系统相互独立，互不影响。设置中的番号映射规则不会修改本页面的外置映射表，也不会改变外置映射表的匹配键；本页面的映射数据也不会反过来修改自动检索使用的搜索番号。
              </Text>
            </Stack>
          </Paper>
        </Stack>
      </Modal>
      <Group justify="space-between" align="center">
        <div>
          <Title order={2}>番号映射管理</Title>
          <Text c="dimmed" size="sm">
            独立管理三个刮削源的外置番号映射文件。
          </Text>
        </div>
        <Button variant="light" onClick={() => setHelpOpened(true)}>
          使用说明
        </Button>
      </Group>
      <Paper p="md" withBorder>
        <Stack gap="sm">
          <Group grow align="end">
            <Select
              label="映射来源"
              value={source}
              data={SOURCES.map(({ value, label }) => ({ value, label }))}
              onChange={(value) => {
                if (value) {
                  setSource(value as Source);
                  setMappingPage(1);
                  setConflicts([]);
                  setSelectedKeys([]);
                  setEditingKey(null);
                }
              }}
            />
            <TextInput
              label="番号搜索"
              placeholder="输入番号片段"
              value={search}
              onChange={(event) => {
                setSearch(event.currentTarget.value);
                setMappingPage(1);
                setSelectedKeys([]);
              }}
            />
          </Group>
          <Text size="sm" c="dimmed">
            {selectedSource.hint}
          </Text>
          <Group>
            <Select
              aria-label="JSON 导入模式"
              value={importMode}
              onChange={(value) => value && setImportMode(value as "merge" | "replace")}
              data={[
                { value: "merge", label: "合并导入" },
                { value: "replace", label: "完整替换" },
              ]}
              w={130}
            />
            <Button
              variant="light"
              color={importMode === "replace" ? "orange" : "blue"}
              onClick={() => {
                if (
                  importMode === "replace" &&
                  !window.confirm("完整替换会删除导入文件中未包含的所有旧映射，确定继续？")
                )
                  return;
                fileRef.current?.click();
              }}
            >
              JSON 导入
            </Button>
            <input
              ref={fileRef}
              hidden
              type="file"
              accept=".json,application/json"
              onChange={(event) => {
                const file = event.currentTarget.files?.[0];
                if (file) void importFile(file);
                event.currentTarget.value = "";
              }}
            />
            <Button
              component="a"
              variant="light"
              href={`/api/number-mappings/export?source=${source}`}
            >
              JSON 导出
            </Button>
            <Button variant="light" onClick={() => tableRef.current?.click()}>
              Excel / CSV 整理导入
            </Button>
            <input
              ref={tableRef}
              hidden
              type="file"
              accept=".csv,.xlsx,.xlsm"
              onChange={(event) => {
                const file = event.currentTarget.files?.[0];
                if (file) void importTable(file);
                event.currentTarget.value = "";
              }}
            />
            <TextInput
              label="数据区域起始列（1=A）"
              type="number"
              value={startColumn}
              onChange={(event) => setStartColumn(event.currentTarget.value)}
              w={130}
            />
            <Checkbox
              checked={skipHeader}
              onChange={(event) => setSkipHeader(event.target.checked)}
              label="跳过首行表头"
            />
            <Text size="sm" c="dimmed">
              共 {query.data?.total ?? 0} 条
            </Text>
          </Group>
        </Stack>
      </Paper>
      {message && <Alert>{message}</Alert>}
      <Paper p="md" withBorder>
        <Stack gap="sm">
          <Title order={4}>新增番号映射</Title>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(3, minmax(0, 1fr)) minmax(0, 2fr) auto",
              gap: "var(--mantine-spacing-sm)",
              alignItems: "end",
            }}
          >
            <TextInput
              label="番号"
              value={draft.key}
              onChange={(e) => setDraft({ ...draft, key: e.currentTarget.value })}
            />
            <TextInput
              label="ID"
              value={String(draft.id)}
              onChange={(e) => setDraft(changeId(draft, e.currentTarget.value))}
            />
            <TextInput
              label="演员（可选）"
              value={draft.actor ?? ""}
              onChange={(e) => setDraft({ ...draft, actor: e.currentTarget.value })}
            />
            <TextInput
              label="标题（可选）"
              value={draft.title ?? ""}
              onChange={(e) => setDraft({ ...draft, title: e.currentTarget.value })}
            />
            <Button
              loading={saveNew.isPending}
              disabled={
                !draft.key.trim() ||
                draft.id === "" ||
                (source === "d2pass" &&
                  (typeof draft.id !== "number" || !Number.isSafeInteger(draft.id)))
              }
              onClick={() => saveNew.mutate()}
            >
              新增
            </Button>
          </div>
        </Stack>
      </Paper>
      <Tabs defaultValue="existing" keepMounted={false}>
        <Tabs.List>
          <Tabs.Tab value="existing">现有映射</Tabs.Tab>
          <Tabs.Tab value="conflicts">异常映射 / 冲突复查（{conflictCount}）</Tabs.Tab>
        </Tabs.List>
        <Tabs.Panel value="existing" pt="md">
          <Paper withBorder p="md">
            <Group justify="space-between" mb="sm">
              <Title order={4}>现有映射</Title>
              <Group>
                <Text size="sm">已选 {selectedKeys.length} 条</Text>
                <Button
                  size="xs"
                  color="red"
                  variant="light"
                  disabled={selectedKeys.length === 0}
                  loading={batchRemove.isPending}
                  onClick={() => {
                    if (window.confirm(`确定删除选中的 ${selectedKeys.length} 条映射？`))
                      batchRemove.mutate();
                  }}
                >
                  批量删除
                </Button>
              </Group>
            </Group>
            {query.isLoading ? (
              <Text>加载中…</Text>
            ) : (
              <Table.ScrollContainer minWidth={1100}>
                <Table striped highlightOnHover>
                  <Table.Thead>
                    <Table.Tr>
                      <Table.Th>
                        <Checkbox
                          aria-label="选择当前页"
                          checked={
                            (query.data?.items.length ?? 0) > 0 &&
                            selectedKeys.length === (query.data?.items.length ?? 0)
                          }
                          indeterminate={
                            selectedKeys.length > 0 &&
                            selectedKeys.length < (query.data?.items.length ?? 0)
                          }
                          onChange={(event) =>
                            setSelectedKeys(
                              event.target.checked
                                ? (query.data?.items ?? []).map((item) => item.key)
                                : [],
                            )
                          }
                        />
                      </Table.Th>
                      <Table.Th>番号</Table.Th>
                      <Table.Th>ID</Table.Th>
                      <Table.Th>演员</Table.Th>
                      <Table.Th>标题</Table.Th>
                      <Table.Th>操作</Table.Th>
                    </Table.Tr>
                  </Table.Thead>
                  <Table.Tbody>
                    {(query.data?.items ?? []).map((entry) => (
                      <Table.Tr key={entry.key}>
                        <Table.Td>
                          <Checkbox
                            checked={selectedKeys.includes(entry.key)}
                            onChange={(event) => {
                              const checked = event.target.checked;
                              setSelectedKeys((current) =>
                                checked
                                  ? [...current, entry.key]
                                  : current.filter((key) => key !== entry.key),
                              );
                            }}
                          />
                        </Table.Td>
                        {editingKey === entry.key ? (
                          <>
                            <Table.Td>
                              <TextInput
                                size="xs"
                                value={editDraft.key}
                                onChange={(e) =>
                                  setEditDraft({ ...editDraft, key: e.currentTarget.value })
                                }
                              />
                            </Table.Td>
                            <Table.Td>
                              <TextInput
                                size="xs"
                                value={String(editDraft.id)}
                                onChange={(e) =>
                                  setEditDraft(changeId(editDraft, e.currentTarget.value))
                                }
                              />
                            </Table.Td>
                            <Table.Td>
                              <TextInput
                                size="xs"
                                value={editDraft.actor ?? ""}
                                onChange={(e) =>
                                  setEditDraft({ ...editDraft, actor: e.currentTarget.value })
                                }
                              />
                            </Table.Td>
                            <Table.Td>
                              <TextInput
                                size="xs"
                                value={editDraft.title ?? ""}
                                onChange={(e) =>
                                  setEditDraft({ ...editDraft, title: e.currentTarget.value })
                                }
                              />
                            </Table.Td>
                            <Table.Td>
                              <Group gap="xs">
                                <Button
                                  size="xs"
                                  loading={saveEdit.isPending}
                                  onClick={() => saveEdit.mutate(editDraft)}
                                >
                                  保存
                                </Button>
                                <Button
                                  size="xs"
                                  variant="default"
                                  onClick={() => setEditingKey(null)}
                                >
                                  取消
                                </Button>
                              </Group>
                            </Table.Td>
                          </>
                        ) : (
                          <>
                            <Table.Td>{entry.key}</Table.Td>
                            <Table.Td>{entry.id}</Table.Td>
                            <Table.Td>{entry.actor ?? ""}</Table.Td>
                            <Table.Td>{entry.title ?? ""}</Table.Td>
                            <Table.Td>
                              <Group gap="xs">
                                <Button
                                  size="xs"
                                  variant="light"
                                  onClick={() => {
                                    setEditingKey(entry.key);
                                    setEditDraft({ ...entry });
                                  }}
                                >
                                  编辑
                                </Button>
                                <Button
                                  size="xs"
                                  color="red"
                                  variant="subtle"
                                  onClick={() => remove.mutate(entry.key)}
                                >
                                  删除
                                </Button>
                              </Group>
                            </Table.Td>
                          </>
                        )}
                      </Table.Tr>
                    ))}
                  </Table.Tbody>
                </Table>
              </Table.ScrollContainer>
            )}
            <Group justify="space-between">
              <Text size="sm">
                每页 30 条；当前第 {mappingPage} / {pageCount} 页
              </Text>
              <Pagination
                total={pageCount}
                value={mappingPage}
                onChange={(value) => {
                  setMappingPage(value);
                  setSelectedKeys([]);
                }}
              />
            </Group>
          </Paper>
        </Tabs.Panel>
        <Tabs.Panel value="conflicts" pt="md">
          <Paper p="md" withBorder>
            <Stack gap="sm">
              <Group justify="space-between">
                <Title order={4}>异常映射 / 冲突复查（{conflictCount} 条）</Title>
                <Button
                  size="xs"
                  color="red"
                  variant="subtle"
                  disabled={!conflicts.length}
                  onClick={() => {
                    void request(`/api/number-mappings/exceptions/clear?source=${source}`, {
                      method: "POST",
                    }).then(() => setConflicts([]));
                  }}
                >
                  清空异常列表
                </Button>
              </Group>
              <TextInput
                placeholder="搜索番号、ID、演员或标题"
                value={conflictSearch}
                onChange={(e) => {
                  setConflictSearch(e.currentTarget.value);
                  setConflictPage(1);
                }}
              />
              <Text size="sm" c="dimmed">
                发生冲突的原记录已从正式映射暂存到异常区；选择一条修改后保存进入正式列表，再删除不需要的另一条。保存只与正式映射列表校验。
              </Text>
              {visibleConflicts.length === 0 ? (
                <Text c="dimmed" size="sm">
                  暂无待复查异常
                </Text>
              ) : (
                visibleConflicts.map((item) => (
                  <Paper key={item.group_id} p="sm" withBorder>
                    <Stack gap="sm">
                      <Text fw={700}>
                        冲突组 #{item.group_id} ·{" "}
                        {item.reason.includes("id") ? "ID 重复" : "番号重复"}
                      </Text>
                      {(["old", "new"] as const).map((side) => {
                        const entry = item[side];
                        if (!entry) return null;
                        return (
                          <Paper key={side} p="sm" bg="var(--mantine-color-dark-6)" withBorder>
                            <Stack gap="xs">
                              <Text fw={600}>{side === "old" ? "原有记录" : "待导入记录"}</Text>
                              <div
                                style={{
                                  display: "grid",
                                  gridTemplateColumns: "repeat(2,minmax(0,1fr))",
                                  gap: 8,
                                }}
                              >
                                <TextInput
                                  label="番号"
                                  value={entry.key}
                                  onChange={(e) =>
                                    updateConflict(item.group_id, side, {
                                      key: e.currentTarget.value,
                                    })
                                  }
                                />
                                <TextInput
                                  label="ID"
                                  value={String(entry.id)}
                                  onChange={(e) =>
                                    updateConflict(
                                      item.group_id,
                                      side,
                                      changeId(entry, e.currentTarget.value),
                                    )
                                  }
                                />
                                <TextInput
                                  label="演员"
                                  value={entry.actor ?? ""}
                                  onChange={(e) =>
                                    updateConflict(item.group_id, side, {
                                      actor: e.currentTarget.value,
                                    })
                                  }
                                />
                                <TextInput
                                  label="标题"
                                  value={entry.title ?? ""}
                                  onChange={(e) =>
                                    updateConflict(item.group_id, side, {
                                      title: e.currentTarget.value,
                                    })
                                  }
                                />
                              </div>
                              <Group justify="flex-end">
                                <Button
                                  size="xs"
                                  loading={saveConflict.isPending}
                                  onClick={() =>
                                    saveConflict.mutate({ entry, group_id: item.group_id, side })
                                  }
                                >
                                  保存
                                </Button>
                                <Button
                                  size="xs"
                                  color="red"
                                  variant="light"
                                  onClick={() => deleteConflict(item.group_id, side)}
                                >
                                  删除
                                </Button>
                              </Group>
                            </Stack>
                          </Paper>
                        );
                      })}
                    </Stack>
                  </Paper>
                ))
              )}
              <Group justify="space-between">
                <Text size="xs" c="dimmed">
                  共 {filteredConflicts.length} 组异常
                </Text>
                <Pagination
                  size="sm"
                  total={conflictPageCount}
                  value={conflictPage}
                  onChange={setConflictPage}
                />
              </Group>
            </Stack>
          </Paper>
        </Tabs.Panel>
      </Tabs>
    </Stack>
  );
}
