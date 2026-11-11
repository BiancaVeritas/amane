import {
  ActionIcon,
  Anchor,
  Button,
  Group,
  Loader,
  Modal,
  Pagination,
  Autocomplete,
  Paper,
  Stack,
  Table,
  Text,
  TextInput,
  Tooltip,
} from "@mantine/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  IconCheck,
  IconDownload,
  IconPlus,
  IconTrash,
  IconUpload,
  IconX,
} from "@tabler/icons-react";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { listActors } from "@/client/sdk.gen";
import { apiFetch } from "@/lib/api-token";
import { Z_INDEX } from "@/lib/z-index";

interface Entry {
  original: string;
  translation: string | null;
}
interface Result {
  items: Entry[];
  total: number;
  stats: { all: number; pending: number; mapped: number };
}

const PAGE_SIZE = 30;
const BASE = "/api/actor-name-mappings";

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await apiFetch(url, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return response.json() as Promise<T>;
}

interface ActorNameMappingDialogProps {
  opened: boolean;
  onClose: () => void;
}

/**
 * 演员显示名映射管理.
 *
 * 只改展示: 搜索、去重与刮削比对仍用原始名, 所以这里改错不会影响索引里的人是谁.
 */
export function ActorNameMappingDialog({ opened, onClose }: ActorNameMappingDialogProps) {
  const { t } = useTranslation(["metadata", "common"]);
  const qc = useQueryClient();
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [edits, setEdits] = useState<Record<string, string>>({});
  /** 已选中但还没落盘的原名; 关掉窗口即丢弃, 不会在后端留下空记录. */
  const [pending, setPending] = useState<string[]>([]);
  /** 输入框当前值; 选中后会立刻清空, 所以它只在一次选择内非空. */
  const [picked, setPicked] = useState("");
  /**
   * 重建计数.
   *
   * Autocomplete 选定某项后会把该标签写回内部输入框, 只把受控值设空并不够; 改 key 让
   * 组件重建, 输入框才会真正回到占位符状态.
   */
  const [resetToken, setResetToken] = useState(0);
  /** 手动新增: 不依赖索引里已有的演员名. null 表示未展开. */
  const [manual, setManual] = useState<{ original: string; translation: string } | null>(null);
  const importRef = useRef<HTMLInputElement>(null);
  const pickerRef = useRef<HTMLInputElement>(null);

  const query = useQuery({
    queryKey: ["actor-name-mappings", search, page],
    queryFn: () => {
      const params = new URLSearchParams({
        search,
        offset: String((page - 1) * PAGE_SIZE),
        limit: String(PAGE_SIZE),
      });
      return request<Result>(`${BASE}?${params}`);
    },
    enabled: opened,
  });

  const invalidate = () => void qc.invalidateQueries({ queryKey: ["actor-name-mappings"] });

  // 可加入映射的候选演员: 已经有映射的不再列出, 免得重复添加一项同样的东西.
  const candidates = useQuery({
    queryKey: ["actor-name-mapping-candidates"],
    queryFn: async () => {
      const response = await listActors({ query: { limit: 1000 } });
      return response.data?.items ?? [];
    },
    enabled: opened,
  });

  const save = useMutation({
    mutationFn: (input: Entry) =>
      request(`${BASE}`, {
        method: "PUT",
        body: JSON.stringify({ original: input.original, translation: input.translation }),
      }),
    onSuccess: (_data, input) => {
      // 落盘了就交给服务端列表, 本页的待填项随之清掉.
      setPending((prev) => prev.filter((name) => name !== input.original));
      invalidate();
      void qc.invalidateQueries({ queryKey: ["actors"] });
    },
  });

  const remove = useMutation({
    mutationFn: (original: string) =>
      request(`${BASE}/${encodeURIComponent(original)}`, { method: "DELETE" }),
    onSuccess: () => {
      invalidate();
      void qc.invalidateQueries({ queryKey: ["actors"] });
    },
  });

  const serverItems = query.data?.items ?? [];
  /** 待填项排在最前面, 让刚选中的那一行立刻可见. */
  const items: Entry[] = [
    ...pending.map((original) => ({ original, translation: null })),
    ...serverItems.filter((item) => !pending.includes(item.original)),
  ];
  const total = query.data?.total ?? 0;
  const stats = query.data?.stats;

  // 已映射的原名不再出现在候选里; 已映射但还没填译名的允许继续编辑, 所以只排除有译名的.
  const mappedNames = new Set(
    serverItems.filter((item) => item.translation).map((item) => item.original),
  );
  const actorOptions = (candidates.data ?? [])
    .filter((actor) => !mappedNames.has(actor.name) && !pending.includes(actor.name))
    .map((actor) => ({
      value: actor.name,
      label: actor.display_name ? `${actor.display_name} (${actor.name})` : actor.name,
    }));

  return (
    <Modal
      opened={opened}
      onClose={() => {
        // 没落盘的待填项随窗口一起丢弃, 不在后端留下没有译名的记录.
        setPending([]);
        setEdits({});
        setPicked("");
        onClose();
      }}
      title={t("actorNameMapping.title")}
      size="xl"
      zIndex={Z_INDEX.ruleEditor}
    >
      <Stack gap="sm">
        <Group justify="space-between" align="flex-start" wrap="nowrap">
          <Text size="xs" c="dimmed" style={{ flex: 1 }}>
            {t("actorNameMapping.hint")}
          </Text>
          <Group gap="xs" wrap="nowrap">
            <Button
              size="xs"
              variant="light"
              leftSection={<IconDownload size={14} />}
              component="a"
              href={`${BASE}/export`}
              download="actor_name_mappings.json"
            >
              {t("actorNameMapping.export")}
            </Button>
            <Button
              size="xs"
              variant="light"
              leftSection={<IconUpload size={14} />}
              onClick={() => importRef.current?.click()}
            >
              {t("actorNameMapping.import")}
            </Button>
          </Group>
        </Group>
        <Group justify="space-between">
          <Autocomplete
            key={resetToken}
            ref={pickerRef}
            placeholder={t("actorNameMapping.pickActor")}
            data={actorOptions.map((option) => option.label)}
            value={picked}
            // 选中后不自动聚焦: 重新聚焦会让 openOnFocus 把下拉立刻又弹开.
            openOnFocus={false}
            onChange={(value) => {
              // 不用 onOptionSubmit: Mantine 会在该回调之后把 label 写回输入框, 清空会被覆盖.
              // 这里改为认「输入值正好等于某个选项」, 选中即登记并清空.
              setPicked(value);
              const hit = actorOptions.find((option) => option.label === value);
              if (!hit) {
                return;
              }
              // 只在前端登记待填项: 立刻写后端会留下一条没有译名的记录, 关掉窗口也不消失.
              setPending((prev) => (prev.includes(hit.value) ? prev : [...prev, hit.value]));
              setEdits((prev) => ({ ...prev, [hit.value]: prev[hit.value] ?? "" }));
              setPicked("");
              setResetToken((token) => token + 1);
            }}
            style={{ flex: 1 }}
            // 弹框里下拉默认层级不够, 会被下面的表格压住.
            comboboxProps={{ withinPortal: true, zIndex: Z_INDEX.dropdown, shadow: "md" }}
            maxDropdownHeight={280}
          />
          <TextInput
            size="xs"
            placeholder={t("actorNameMapping.filterExisting")}
            value={search}
            onChange={(e) => {
              setSearch(e.currentTarget.value);
              setPage(1);
            }}
            style={{ width: 160 }}
          />
          <Group gap="xs">
            {stats && (
              <Text size="xs" c="dimmed">
                {t("actorNameMapping.stats", { mapped: stats.mapped, all: stats.all })}
              </Text>
            )}
            <input
              ref={importRef}
              type="file"
              accept="application/json"
              hidden
              onChange={(e) => {
                const file = e.currentTarget.files?.[0];
                e.currentTarget.value = "";
                if (!file) {
                  return;
                }
                void file.text().then(async (text) => {
                  await request(`${BASE}/import`, { method: "POST", body: text });
                  invalidate();
                  void qc.invalidateQueries({ queryKey: ["actors"] });
                });
              }}
            />
          </Group>
        </Group>

        {manual && (
          <Paper withBorder p="xs">
            <Group gap="xs" align="flex-end" wrap="nowrap">
              <TextInput
                label={t("actorNameMapping.original")}
                placeholder={t("actorNameMapping.manualOriginal")}
                value={manual.original}
                onChange={(e) => {
                  // 先取值再进 setState: 函数式更新是延迟执行的, 那时事件已回收, 读 currentTarget 会拿到 null.
                  const value = e.currentTarget.value;
                  setManual((prev) => (prev ? { ...prev, original: value } : prev));
                }}
                style={{ flex: 1 }}
              />
              <TextInput
                label={t("actorNameMapping.translation")}
                placeholder={t("actorNameMapping.manualTranslation")}
                value={manual.translation}
                onChange={(e) => {
                  const value = e.currentTarget.value;
                  setManual((prev) => (prev ? { ...prev, translation: value } : prev));
                }}
                style={{ flex: 1 }}
              />
              <Button
                disabled={!manual.original.trim()}
                loading={save.isPending}
                onClick={() => {
                  const original = manual.original.trim();
                  save.mutate(
                    { original, translation: manual.translation.trim() || null },
                    { onSuccess: () => setManual(null) },
                  );
                }}
              >
                {t("actorNameMapping.save")}
              </Button>
            </Group>
          </Paper>
        )}

        {query.isLoading ? (
          <Group justify="center" p="md">
            <Loader size="sm" />
          </Group>
        ) : items.length === 0 ? (
          <Text size="sm" c="dimmed" ta="center" py="md">
            {t("actorNameMapping.empty")}
          </Text>
        ) : (
          <Table striped highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>{t("actorNameMapping.original")}</Table.Th>
                <Table.Th>{t("actorNameMapping.translation")}</Table.Th>
                <Table.Th w={80} />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {items.map((item) => {
                const draft = edits[item.original] ?? item.translation ?? "";
                const dirty = draft !== (item.translation ?? "");
                return (
                  <Table.Tr key={item.original}>
                    <Table.Td>
                      <Text size="sm">{item.original}</Text>
                    </Table.Td>
                    <Table.Td>
                      <TextInput
                        size="xs"
                        value={draft}
                        placeholder={t("actorNameMapping.placeholder")}
                        onChange={(e) => {
                          const value = e.currentTarget.value;
                          setEdits((prev) => ({ ...prev, [item.original]: value }));
                        }}
                      />
                    </Table.Td>
                    <Table.Td>
                      <Group gap={4} wrap="nowrap">
                        <Tooltip
                          label={t("actorNameMapping.save")}
                          zIndex={Z_INDEX.tooltip}
                          multiline
                          maw={220}
                        >
                          <ActionIcon
                            size="sm"
                            color="blue"
                            variant={dirty ? "filled" : "subtle"}
                            disabled={!dirty}
                            loading={save.isPending}
                            onClick={() =>
                              save.mutate({
                                original: item.original,
                                translation: draft.trim() || null,
                              })
                            }
                          >
                            <IconCheck size={14} />
                          </ActionIcon>
                        </Tooltip>
                        <Tooltip
                          label={t("actorNameMapping.clear")}
                          zIndex={Z_INDEX.tooltip}
                          multiline
                          maw={220}
                        >
                          <ActionIcon
                            size="sm"
                            color="red"
                            variant="subtle"
                            onClick={() => remove.mutate(item.original)}
                          >
                            <IconTrash size={14} />
                          </ActionIcon>
                        </Tooltip>
                      </Group>
                    </Table.Td>
                  </Table.Tr>
                );
              })}
            </Table.Tbody>
          </Table>
        )}

        {total > PAGE_SIZE && (
          <Group justify="center">
            <Pagination
              size="sm"
              total={Math.ceil(total / PAGE_SIZE)}
              value={page}
              onChange={setPage}
            />
          </Group>
        )}

        <Group justify="space-between">
          <Anchor
            size="xs"
            href="https://github.com/sqzw-x/amane"
            target="_blank"
            rel="noreferrer"
            style={{ visibility: "hidden" }}
          >
            .
          </Anchor>
          <Group gap="xs">
            <Tooltip
              label={t("actorNameMapping.manualHint")}
              zIndex={Z_INDEX.tooltip}
              multiline
              maw={240}
            >
              <Button
                variant="light"
                leftSection={<IconPlus size={14} />}
                onClick={() => setManual(manual ? null : { original: "", translation: "" })}
              >
                {t("actorNameMapping.manual")}
              </Button>
            </Tooltip>
            <Button variant="default" onClick={onClose} leftSection={<IconX size={14} />}>
              {t("common:actions.close")}
            </Button>
          </Group>
        </Group>
      </Stack>
    </Modal>
  );
}
