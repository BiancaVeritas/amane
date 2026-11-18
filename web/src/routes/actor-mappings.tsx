import {
  Alert,
  Button,
  Checkbox,
  Group,
  Paper,
  Pagination,
  Select,
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

export const Route = createFileRoute("/actor-mappings")({ component: ActorMappingsPage });
type Entry = { id: string; name: string; aliases: string[] };
type Conflict = { group_id: number; reason: "id" | "name"; old?: Entry; new?: Entry };
const emptyEntry = (): Entry => ({ id: "", name: "", aliases: [] });
function parseAliases(value: string): string[] {
  return value
    .split(/[,，]/)
    .map((item) => item.trim())
    .filter(Boolean);
}
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
async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await apiFetch(url, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) throw new Error(await response.text());
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}
function ActorMappingsPage() {
  const qc = useQueryClient();
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [draft, setDraft] = useState<Entry>(emptyEntry());
  const [draftAliasesText, setDraftAliasesText] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editDraft, setEditDraft] = useState<Entry>(emptyEntry());
  const [editAliasesText, setEditAliasesText] = useState("");
  const [conflictAliasesText, setConflictAliasesText] = useState<Record<string, string>>({});
  const [message, setMessage] = useState<string | null>(null);
  const [conflicts, setConflicts] = useState<Conflict[]>([]);
  const [conflictSearch, setConflictSearch] = useState("");
  const [conflictPage, setConflictPage] = useState(1);
  const [selected, setSelected] = useState<string[]>([]);
  const [startColumn, setStartColumn] = useState("1");
  const [skipHeader, setSkipHeader] = useState(false);
  const [mode, setMode] = useState<"merge" | "replace">("merge");
  const jsonRef = useRef<HTMLInputElement>(null);
  const tableRef = useRef<HTMLInputElement>(null);
  const pageSize = 30;
  const conflictPageSize = 10;
  const query = useQuery({
    queryKey: ["actor-mappings", search, page],
    queryFn: () =>
      request<{ items: Entry[]; total: number }>(
        `/api/actor-mappings?search=${encodeURIComponent(search)}&limit=${pageSize}&offset=${(page - 1) * pageSize}`,
      ),
  });
  useEffect(() => {
    void request<Conflict[]>("/api/actor-mappings/exceptions")
      .then(setConflicts)
      .catch((error) => setMessage(`异常列表读取失败：${errorText(error)}`));
  }, []);
  const pageCount = Math.max(1, Math.ceil((query.data?.total ?? 0) / pageSize));
  const conflictNeedle = conflictSearch.trim();
  const filteredConflicts = conflicts.filter(
    (item) =>
      !conflictNeedle ||
      [item.old?.id ?? "", item.old?.name ?? "", item.new?.id ?? "", item.new?.name ?? ""].some(
        (value) => value === conflictSearch.trim(),
      ),
  );
  const conflictPageCount = Math.max(1, Math.ceil(filteredConflicts.length / conflictPageSize));
  const visibleConflicts = filteredConflicts.slice(
    (conflictPage - 1) * conflictPageSize,
    conflictPage * conflictPageSize,
  );
  useEffect(() => {
    if (!message) return;
    const timer = window.setTimeout(() => setMessage(null), 5000);
    return () => window.clearTimeout(timer);
  }, [message]);
  const refresh = () => void qc.invalidateQueries({ queryKey: ["actor-mappings"] });
  const saveNew = useMutation({
    mutationFn: () =>
      request<Entry>("/api/actor-mappings", {
        method: "PUT",
        body: JSON.stringify({ ...draft, aliases: parseAliases(draftAliasesText) }),
      }),
    onSuccess: () => {
      setDraft(emptyEntry());
      setDraftAliasesText("");
      setMessage("新增演员映射成功");
      refresh();
    },
    onError: (error) => setMessage(`新增失败：${errorText(error)}`),
  });
  const saveEdit = useMutation({
    mutationFn: (entry: Entry) =>
      request<Entry>("/api/actor-mappings", {
        method: "PUT",
        body: JSON.stringify({ ...entry, original_id: editingId }),
      }),
    onSuccess: () => {
      setEditingId(null);
      setEditDraft(emptyEntry());
      setEditAliasesText("");
      setMessage("演员映射修改已保存");
      refresh();
    },
    onError: (error) => setMessage(`保存失败：${errorText(error)}`),
  });
  const saveConflict = useMutation({
    mutationFn: (payload: { entry: Entry; group_id: number; side: "old" | "new" }) =>
      request<Conflict[]>("/api/actor-mappings/exceptions/save", {
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
    mutationFn: (id: string) =>
      request(`/api/actor-mappings/${encodeURIComponent(id)}`, { method: "DELETE" }),
    onSuccess: () => {
      setMessage("演员映射已删除");
      refresh();
    },
    onError: (error) => setMessage(`删除失败：${errorText(error)}`),
  });
  const batchRemove = useMutation({
    mutationFn: () =>
      request<{ deleted: number }>("/api/actor-mappings/batch-delete", {
        method: "POST",
        body: JSON.stringify({ ids: selected }),
      }),
    onSuccess: (result) => {
      setMessage(`已删除 ${result.deleted} 条`);
      setSelected([]);
      refresh();
    },
    onError: (error) => setMessage(`批量删除失败：${errorText(error)}`),
  });
  function updateConflict(groupId: number, side: "old" | "new", patch: Partial<Entry>) {
    setConflicts((items) => {
      const next = items.map((item) =>
        item.group_id !== groupId
          ? item
          : { ...item, [side]: item[side] ? { ...item[side]!, ...patch } : undefined },
      );
      const changed = next.find((item) => item.group_id === groupId);
      if (changed?.[side])
        void request<Conflict[]>("/api/actor-mappings/exceptions", {
          method: "PUT",
          body: JSON.stringify({ group_id: groupId, side, entry: changed[side] }),
        })
          .then(setConflicts)
          .catch((error) => setMessage(`异常记录暂存失败：${errorText(error)}`));
      return next;
    });
  }
  function deleteConflict(groupId: number, side: "old" | "new") {
    void request<Conflict[]>("/api/actor-mappings/exceptions", {
      method: "PUT",
      body: JSON.stringify({ group_id: groupId, side, entry: null }),
    })
      .then(setConflicts)
      .catch((error) => setMessage(`异常记录删除失败：${errorText(error)}`));
  }
  async function importJson(file: File) {
    try {
      let data: Record<string, { name: string; aliases?: string[] }>;
      try {
        data = JSON.parse(await file.text()) as typeof data;
      } catch (error) {
        throw new Error(`JSON 文件格式错误，尚未提交导入：${errorText(error)}`, { cause: error });
      }
      const result = await request<{ added?: string[]; conflicts?: Conflict[]; replaced?: number }>(
        "/api/actor-mappings/import",
        { method: "POST", body: JSON.stringify({ data, mode }) },
      );
      if (mode === "replace") {
        setConflicts(await request<Conflict[]>("/api/actor-mappings/exceptions"));
        setMessage(`完整替换完成，共 ${result.replaced ?? 0} 条`);
      } else {
        setConflicts(await request<Conflict[]>("/api/actor-mappings/exceptions"));
        setConflictPage(1);
        setMessage(
          `新增 ${result.added?.length ?? 0} 条；异常 ${result.conflicts?.length ?? 0} 组`,
        );
      }
      refresh();
    } catch (error) {
      setMessage(`JSON 导入失败：${errorText(error)}`);
    }
  }
  async function importTable(file: File) {
    const body = new FormData();
    body.append("file", file);
    body.append("start_column", startColumn);
    body.append("skip_header", String(skipHeader));
    try {
      const response = await apiFetch("/api/actor-mappings/import-table", { method: "POST", body });
      if (!response.ok) throw new Error(await response.text());
      const result = (await response.json()) as {
        converted: number;
        added: string[];
        conflicts: Conflict[];
      };
      setConflicts(await request<Conflict[]>("/api/actor-mappings/exceptions"));
      setConflictPage(1);
      setMessage(
        `整理导入 ${result.converted} 条；新增 ${result.added.length} 条；异常 ${result.conflicts.length} 组`,
      );
      refresh();
    } catch (error) {
      setMessage(`表格导入失败：${errorText(error)}`);
    }
  }
  const conflictCount = conflicts.reduce(
    (sum, item) => sum + Number(Boolean(item.old)) + Number(Boolean(item.new)),
    0,
  );
  return (
    <Stack gap="md" pb={100}>
      <div>
        <Title order={2}>演员映射管理</Title>
        <Text c="dimmed" size="sm">
          管理 Minnano 女优数字 ID、标准姓名及别名；演员搜索仅精确匹配标准姓名。
        </Text>
      </div>
      <Paper p="md" withBorder>
        <Stack gap="sm">
          <Group grow align="end">
            <TextInput
              label="演员搜索"
              placeholder="输入完整标准姓名（精确匹配）"
              value={search}
              onChange={(event) => {
                setSearch(event.currentTarget.value);
                setPage(1);
                setSelected([]);
              }}
            />
            <Text size="sm" c="dimmed">
              共 {query.data?.total ?? 0} 条
            </Text>
          </Group>
          <Group align="end">
            <Select
              aria-label="导入模式"
              value={mode}
              onChange={(value) => value && setMode(value as "merge" | "replace")}
              data={[
                { value: "merge", label: "合并导入" },
                { value: "replace", label: "全体替换" },
              ]}
              w={130}
            />
            <Button
              color={mode === "replace" ? "orange" : "blue"}
              variant="light"
              onClick={() => {
                if (mode === "replace" && !window.confirm("全体替换会覆盖全部演员映射，确定继续？"))
                  return;
                jsonRef.current?.click();
              }}
            >
              JSON 导入
            </Button>
            <input
              ref={jsonRef}
              hidden
              type="file"
              accept=".json,application/json"
              onChange={(event) => {
                const file = event.currentTarget.files?.[0];
                if (file) void importJson(file);
                event.currentTarget.value = "";
              }}
            />
            <Button component="a" variant="light" href="/api/actor-mappings/export?format=json">
              JSON 导出
            </Button>
            <Button component="a" variant="light" href="/api/actor-mappings/export?format=csv">
              CSV 导出
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
          </Group>
        </Stack>
      </Paper>
      {message && <Alert>{message}</Alert>}
      <Paper p="md" withBorder>
        <Stack gap="sm">
          <Title order={4}>新增演员映射</Title>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1fr) minmax(0, 2fr) auto",
              gap: "var(--mantine-spacing-sm)",
              alignItems: "end",
            }}
          >
            <TextInput
              label="ID"
              placeholder="Minnano 数字 ID"
              value={draft.id}
              onChange={(event) => setDraft({ ...draft, id: event.currentTarget.value })}
            />
            <TextInput
              label="标准姓名"
              value={draft.name}
              onChange={(event) => setDraft({ ...draft, name: event.currentTarget.value })}
            />
            <TextInput
              label="别名（逗号分隔）"
              placeholder="AAA, BBB，CCC"
              value={draftAliasesText}
              onChange={(event) => setDraftAliasesText(event.currentTarget.value)}
            />
            <Button
              loading={saveNew.isPending}
              disabled={!/^\d{1,12}$/.test(draft.id) || !draft.name.trim()}
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
          <Paper p="md" withBorder>
            <Group justify="space-between" mb="sm">
              <Title order={4}>现有演员映射</Title>
              <Group>
                <Text size="sm">已选 {selected.length} 条</Text>
                <Button
                  size="xs"
                  color="red"
                  variant="light"
                  disabled={!selected.length}
                  loading={batchRemove.isPending}
                  onClick={() => {
                    if (window.confirm(`确定删除选中的 ${selected.length} 条演员映射？`))
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
              <Table.ScrollContainer minWidth={950}>
                <Table striped highlightOnHover>
                  <Table.Thead>
                    <Table.Tr>
                      <Table.Th>
                        <Checkbox
                          aria-label="选择当前页"
                          checked={
                            (query.data?.items.length ?? 0) > 0 &&
                            selected.length === (query.data?.items.length ?? 0)
                          }
                          indeterminate={
                            selected.length > 0 && selected.length < (query.data?.items.length ?? 0)
                          }
                          onChange={(event) =>
                            setSelected(
                              event.target.checked
                                ? (query.data?.items ?? []).map((item) => item.id)
                                : [],
                            )
                          }
                        />
                      </Table.Th>
                      <Table.Th>ID</Table.Th>
                      <Table.Th>标准姓名</Table.Th>
                      <Table.Th>别名</Table.Th>
                      <Table.Th>操作</Table.Th>
                    </Table.Tr>
                  </Table.Thead>
                  <Table.Tbody>
                    {(query.data?.items ?? []).map((entry) => (
                      <Table.Tr key={entry.id}>
                        <Table.Td>
                          <Checkbox
                            checked={selected.includes(entry.id)}
                            onChange={(event) => {
                              const checked = event.target.checked;
                              setSelected((current) =>
                                checked
                                  ? [...current, entry.id]
                                  : current.filter((id) => id !== entry.id),
                              );
                            }}
                          />
                        </Table.Td>
                        {editingId === entry.id ? (
                          <>
                            <Table.Td>
                              <TextInput
                                size="xs"
                                value={editDraft.id}
                                onChange={(e) =>
                                  setEditDraft({ ...editDraft, id: e.currentTarget.value })
                                }
                              />
                            </Table.Td>
                            <Table.Td>
                              <TextInput
                                size="xs"
                                value={editDraft.name}
                                onChange={(e) =>
                                  setEditDraft({ ...editDraft, name: e.currentTarget.value })
                                }
                              />
                            </Table.Td>
                            <Table.Td>
                              <TextInput
                                size="xs"
                                value={editAliasesText}
                                onChange={(e) => setEditAliasesText(e.currentTarget.value)}
                              />
                            </Table.Td>
                            <Table.Td>
                              <Group gap="xs">
                                <Button
                                  size="xs"
                                  loading={saveEdit.isPending}
                                  onClick={() =>
                                    saveEdit.mutate({
                                      ...editDraft,
                                      aliases: parseAliases(editAliasesText),
                                    })
                                  }
                                >
                                  保存
                                </Button>
                                <Button
                                  size="xs"
                                  variant="default"
                                  onClick={() => setEditingId(null)}
                                >
                                  取消
                                </Button>
                              </Group>
                            </Table.Td>
                          </>
                        ) : (
                          <>
                            <Table.Td>{entry.id}</Table.Td>
                            <Table.Td>{entry.name}</Table.Td>
                            <Table.Td>{entry.aliases.join(" ")}</Table.Td>
                            <Table.Td>
                              <Group gap="xs">
                                <Button
                                  size="xs"
                                  variant="light"
                                  onClick={() => {
                                    setEditingId(entry.id);
                                    setEditDraft({ ...entry, aliases: [...entry.aliases] });
                                    setEditAliasesText(entry.aliases.join(", "));
                                  }}
                                >
                                  编辑
                                </Button>
                                <Button
                                  size="xs"
                                  color="red"
                                  variant="subtle"
                                  onClick={() => remove.mutate(entry.id)}
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
                每页 30 条；当前第 {page} / {pageCount} 页
              </Text>
              <Pagination
                total={pageCount}
                value={page}
                onChange={(value) => {
                  setPage(value);
                  setSelected([]);
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
                  variant="subtle"
                  color="red"
                  disabled={conflicts.length === 0}
                  onClick={() => {
                    void request("/api/actor-mappings/exceptions/clear", { method: "POST" }).then(
                      () => setConflicts([]),
                    );
                  }}
                >
                  清空异常列表
                </Button>
              </Group>
              <TextInput
                placeholder="精确搜索 ID 或标准姓名"
                value={conflictSearch}
                onChange={(event) => {
                  setConflictSearch(event.currentTarget.value);
                  setConflictPage(1);
                }}
              />
              <Text size="sm" c="dimmed">
                ID
                或标准姓名重复时，原有记录和导入记录都会暂存到异常区；别名允许重复。选择要保留的一条编辑后保存进入正式列表，再删除另一条。保存时只检查正式映射中的
                ID。
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
                        {item.reason === "id" ? "ID 重复" : "标准姓名重复"}
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
                                  gridTemplateColumns:
                                    "minmax(100px,1fr) minmax(150px,1fr) minmax(200px,2fr)",
                                  gap: 8,
                                }}
                              >
                                <TextInput
                                  label="ID"
                                  value={entry.id}
                                  onChange={(e) =>
                                    updateConflict(item.group_id, side, {
                                      id: e.currentTarget.value,
                                    })
                                  }
                                />
                                <TextInput
                                  label="标准姓名"
                                  value={entry.name}
                                  onChange={(e) =>
                                    updateConflict(item.group_id, side, {
                                      name: e.currentTarget.value,
                                    })
                                  }
                                />
                                <TextInput
                                  label="别名（逗号分隔）"
                                  value={
                                    conflictAliasesText[`${item.group_id}:${side}`] ??
                                    entry.aliases.join(", ")
                                  }
                                  onChange={(e) =>
                                    setConflictAliasesText((current) => ({
                                      ...current,
                                      [`${item.group_id}:${side}`]: e.currentTarget.value,
                                    }))
                                  }
                                />
                              </div>
                              <Group justify="flex-end">
                                <Button
                                  size="xs"
                                  loading={saveConflict.isPending}
                                  onClick={() =>
                                    saveConflict.mutate({
                                      entry: {
                                        ...entry,
                                        aliases: parseAliases(
                                          conflictAliasesText[`${item.group_id}:${side}`] ??
                                            entry.aliases.join(", "),
                                        ),
                                      },
                                      group_id: item.group_id,
                                      side,
                                    })
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
