import {
  Alert,
  Badge,
  Box,
  Button,
  Checkbox,
  Group,
  Modal,
  Pagination,
  Paper,
  SegmentedControl,
  Select,
  Stack,
  Table,
  Text,
  Title,
} from "@mantine/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createFileRoute, stripSearchParams } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { z } from "zod";
import { useNarrowViewport } from "@/hooks/use-narrow-viewport";
import { apiFetch } from "@/lib/api-token";
import { ClearableTextInput } from "@/components/common/clearable-input";

/**
 * 分类名称的中文映射表.
 *
 * 页面是唯一入口, 三个滑动选项各是一组**互相独立**的表: 标题、说明、筛选项与可用动作都跟着
 * 当前选项走 (之后再加新组时不需要动这里的渲染). 片商那一组的制作商 / 发行商 / 系列合成一张
 * 表: 同一个词只占一行, 同一次翻译, 不因它同时出现在两个分类里就分成两条.
 */

const GROUPS = ["tag", "actor", "vendor"] as const;
type Group = (typeof GROUPS)[number];

const STATUS_FILTERS = ["pending", "mapped", "archived", "all"] as const;
const LANGUAGE_FILTERS = ["all", "ja", "en", "zh", "other"] as const;
const FREQUENCY_FILTERS = ["all", "high"] as const;

const mappingsSearchSchema = z.object({
  group: z.enum(GROUPS).catch("tag").default("tag"),
  status: z.enum(STATUS_FILTERS).catch("pending").default("pending"),
  language: z.enum(LANGUAGE_FILTERS).catch("all").default("all"),
  frequency: z.enum(FREQUENCY_FILTERS).catch("all").default("all"),
  search: z.string().catch("").default(""),
  page: z.coerce.number().int().min(1).catch(1).default(1),
});

export const Route = createFileRoute("/mappings")({
  validateSearch: mappingsSearchSchema,
  // 默认参数不进地址栏; group 是人手分享的入口, 保留.
  search: {
    middlewares: [
      stripSearchParams({
        status: "pending",
        language: "all",
        frequency: "all",
        search: "",
        page: 1,
      }),
    ],
  },
  component: MappingsPage,
});

interface Entry {
  original: string;
  translation: string | null;
  language: string;
  usage_count: number;
  sources: string[];
  /** 归档标记; 只有标签组用得上. */
  archived?: boolean;
  /** 手动新增: 分类表里没有这个名字也保留. */
  manual?: boolean;
  /** 片商组: 这一条身兼哪几个分类 (studio / publisher / series). */
  roles?: string[];
}

interface Result {
  items: Entry[];
  total: number;
  stats: { all: number; pending: number; mapped: number; archived: number };
  /** 该组管的分类; 用于给每行标出它是制作商还是发行商. */
  groups: { kind: string; label: string }[];
  /** 该组是否有归档区 (只有标签有). */
  archive: boolean;
}

const PAGE_SIZE = 30;
const BASE = "/api/mappings";

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await apiFetch(url, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) throw new Error(await response.text());
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

function MappingsPage() {
  const { t } = useTranslation(["metadata", "common"]);
  const qc = useQueryClient();
  const navigate = Route.useNavigate();
  const search = Route.useSearch();
  const group = search.group;
  // 窄屏改卡片布局: 七列的表格挤在手机宽度里会把名字压成竖排.
  const narrow = useNarrowViewport("md");
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [selected, setSelected] = useState<string[]>([]);
  const selectionAnchor = useRef<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const importRef = useRef<HTMLInputElement>(null);
  const [manual, setManual] = useState<{ original: string; translation: string } | null>(null);
  // 搜索框自己持有输入值: 每敲一个键就写一次 URL 会打断输入法的候选组合, 中文根本打不出来.
  const [searchInput, setSearchInput] = useState(search.search);
  const searchDebounce = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const archivedView = search.status === "archived";
  const queryKey = [
    "mappings",
    group,
    search.status,
    search.language,
    search.frequency,
    search.search,
    search.page,
  ];

  const query = useQuery({
    queryKey,
    queryFn: () => {
      const params = new URLSearchParams({
        // 归档是一个独立的维度: 归档区里也能按语言 / 搜索筛, 所以状态与归并分开传.
        status: archivedView ? "all" : search.status,
        archived: String(archivedView),
        language: search.language,
        frequency: search.frequency,
        search: search.search,
        offset: String((search.page - 1) * PAGE_SIZE),
        limit: String(PAGE_SIZE),
      });
      return request<Result>(`${BASE}/${group}?${params}`);
    },
  });

  // 归档区只有标签有; 其它组不显示这一档, 也不允许停在它上面.
  // 三组都有归档区 (后端 /mappings/groups 声明); 数据未到时先按「有」渲染, 免得筛选先塌一下.
  const archivable = query.data?.archive ?? true;
  const invalidate = () => void qc.invalidateQueries({ queryKey: ["mappings"] });

  const save = useMutation({
    mutationFn: (input: { original: string; translation: string | null }) =>
      request<Entry>(`${BASE}/${group}`, { method: "PUT", body: JSON.stringify(input) }),
    onSuccess: (_result, input) => {
      setEdits((old) => {
        const next = { ...old };
        delete next[input.original];
        return next;
      });
      setSelected((old) => old.filter((value) => value !== input.original));
      invalidate();
    },
    onError: (error) => setMessage(String(error)),
  });

  const suggest = useMutation({
    mutationFn: (originals: string[]) =>
      request<{ suggestions: Record<string, string> }>(`${BASE}/${group}/suggest`, {
        method: "POST",
        body: JSON.stringify({ originals }),
      }),
    onSuccess: (result) => {
      setEdits((old) => ({ ...old, ...result.suggestions }));
      setMessage(null);
    },
    onError: (error) => setMessage(String(error)),
  });

  const importDictionary = useMutation({
    mutationFn: async (file: File) =>
      request<{ imported: number }>(`${BASE}/${group}/import`, {
        method: "POST",
        body: JSON.stringify(JSON.parse(await file.text())),
      }),
    onSuccess: invalidate,
    onError: (error) => setMessage(String(error)),
  });

  const restore = useMutation({
    mutationFn: (original: string) =>
      request<void>(`${BASE}/${group}/restore/${encodeURIComponent(original)}`, { method: "POST" }),
    onSuccess: invalidate,
    onError: (error) => setMessage(String(error)),
  });

  // 卸载时收掉未触发的防抖, 免得定时器在页面已经离开后还去改 URL.
  useEffect(
    () => () => {
      if (searchDebounce.current) clearTimeout(searchDebounce.current);
    },
    [],
  );

  const entries = query.data?.items ?? [];
  const columnCount = 7;
  const pages = Math.max(1, Math.ceil((query.data?.total ?? 0) / PAGE_SIZE));
  // 「全部词条」里归档与非归档混在一页, 批量按钮要看选中项的实际状态决定动作.
  const selectedAllArchived =
    archivable &&
    selected.length > 0 &&
    selected.every((original) => entries.find((item) => item.original === original)?.archived);
  // 片商组里一个词可能身兼多类; 表里只占一行, 用徽章标出归属.
  const kindLabel = (kind: string): string => {
    switch (kind) {
      case "studio":
        return t("mappings.kinds.studio");
      case "publisher":
        return t("mappings.kinds.publisher");
      case "series":
        return t("mappings.kinds.series");
      case "tag":
        return t("mappings.kinds.tag");
      case "actor":
        return t("mappings.kinds.actor");
      default:
        return kind;
    }
  };
  const entryRoles = (entry: Entry): string[] => entry.roles ?? [];

  const updateSearch = (patch: Partial<typeof search>) => {
    void navigate({ search: (prev) => ({ ...prev, ...patch }) });
    // 筛选变化即回到第一页、清掉选择: 翻在第三页却只剩两条时, 空页会让人以为词条没了.
    setSelected([]);
    selectionAnchor.current = null;
  };

  const toggleSelected = (entry: Entry, checked: boolean, shiftKey: boolean) => {
    const anchor = selectionAnchor.current;
    const anchorIndex = anchor ? entries.findIndex((item) => item.original === anchor) : -1;
    const currentIndex = entries.findIndex((item) => item.original === entry.original);
    if (shiftKey && anchorIndex >= 0 && currentIndex >= 0) {
      const start = Math.min(anchorIndex, currentIndex);
      const end = Math.max(anchorIndex, currentIndex);
      const range = entries.slice(start, end + 1).map((item) => item.original);
      setSelected((old) =>
        checked ? [...new Set([...old, ...range])] : old.filter((value) => !range.includes(value)),
      );
      return;
    }
    selectionAnchor.current = entry.original;
    setSelected((old) =>
      checked
        ? [...new Set([...old, entry.original])]
        : old.filter((value) => value !== entry.original),
    );
  };

  /** 清掉译名再归档: 服务端拒绝「带译名归档」, 这两步要连着做. */
  async function clearTranslationThenArchive(original: string) {
    await request<void>(`${BASE}/${group}`, {
      method: "PUT",
      body: JSON.stringify({ original, translation: null }),
    });
    await request<void>(`${BASE}/${group}/${encodeURIComponent(original)}`, { method: "DELETE" });
    invalidate();
  }

  async function batchConfirmSelected() {
    const targets = selected.filter((original) =>
      Object.prototype.hasOwnProperty.call(edits, original),
    );
    if (!targets.length) return;
    const results = await Promise.allSettled(
      targets.map((original) =>
        request<Entry>(`${BASE}/${group}`, {
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
    setMessage(
      results.some((result) => result.status === "rejected")
        ? `批量确认完成：成功 ${succeeded.length} 项，失败 ${targets.length - succeeded.length} 项`
        : null,
    );
    invalidate();
  }

  async function batchToggleSelected() {
    const targets = [...selected];
    if (!targets.length) return;
    const results = await Promise.allSettled(
      targets.map((original) => {
        const isArchived = entries.find((item) => item.original === original)?.archived;
        return isArchived
          ? request<void>(`${BASE}/${group}/restore/${encodeURIComponent(original)}`, {
              method: "POST",
            })
          : request<void>(`${BASE}/${group}/${encodeURIComponent(original)}`, { method: "DELETE" });
      }),
    );
    const succeeded = targets.filter((_original, index) => results[index]?.status === "fulfilled");
    setSelected((old) => old.filter((original) => !succeeded.includes(original)));
    setEdits((old) => {
      const next = { ...old };
      for (const original of succeeded) delete next[original];
      return next;
    });
    setMessage(
      results.some((result) => result.status === "rejected")
        ? `批量操作完成：成功 ${succeeded.length} 项，失败 ${targets.length - succeeded.length} 项`
        : null,
    );
    invalidate();
  }

  // 语言列与筛选下拉共用同一份文案; 未知取值原样显示, 不猜.
  const languageLabel = (code: string): string => {
    switch (code) {
      case "ja":
        return t("mappings.languages.ja");
      case "en":
        return t("mappings.languages.en");
      case "zh":
        return t("mappings.languages.zh");
      case "other":
        return t("mappings.languages.other");
      default:
        return code;
    }
  };

  return (
    <Stack gap="md" pb={120}>
      {/* 滑动条在最上面: 它决定整页是哪张表, 下面的标题与筛选都随它变.
          用 Group 居中, 宽度随内容自适应 —— 三个选项只有几个字, 拉满会让它们散得很开. */}
      <Group justify="center">
        <SegmentedControl
          size="lg"
          value={group}
          onChange={(value) => {
            const next = GROUPS.find((candidate) => candidate === value) ?? "tag";
            // 换组时筛选与分页都回到默认: 上一组的筛选条件多半不适用于下一组.
            void navigate({ search: { group: next } });
            setSelected([]);
            setEdits({});
            setMessage(null);
            // 搜索框的本地值跟着回默认, 否则会残留上一组输入的关键字.
            setSearchInput("");
            if (searchDebounce.current) clearTimeout(searchDebounce.current);
            selectionAnchor.current = null;
          }}
          data={GROUPS.map((value) => ({ value, label: t(`mappings.tabs.${value}`) }))}
        />
      </Group>

      <Group justify="space-between" align="flex-start" wrap="wrap">
        <div>
          <Title order={2}>{t(`mappings.tabs.${group}`)}</Title>
          <Text c="dimmed" size="sm">
            {t("mappings.subtitle")}
          </Text>
        </div>
        <Group gap="xs">
          <Button
            variant="light"
            onClick={() => suggest.mutate(selected)}
            disabled={!selected.length || selected.length > 30 || suggest.isPending}
            loading={suggest.isPending}
          >
            {t("mappings.suggest", { count: selected.length })}
          </Button>
          <Button variant="light" onClick={() => setManual({ original: "", translation: "" })}>
            {t("mappings.manual")}
          </Button>
          <Button
            variant="light"
            onClick={() => importRef.current?.click()}
            loading={importDictionary.isPending}
          >
            {t("mappings.import")}
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
          <Button component="a" href={`${BASE}/${group}/export`} variant="light">
            {t("mappings.export")}
          </Button>
        </Group>
      </Group>

      <Group grow align="end">
        <Select
          label={t("mappings.status")}
          value={search.status}
          onChange={(value) =>
            updateSearch({ status: STATUS_FILTERS.find((v) => v === value) ?? "pending", page: 1 })
          }
          data={STATUS_FILTERS.map((value) => ({
            value,
            label: t(`mappings.statuses.${value}`),
          }))}
        />
        <Select
          label={t("mappings.language")}
          value={search.language}
          onChange={(value) =>
            updateSearch({ language: LANGUAGE_FILTERS.find((v) => v === value) ?? "all", page: 1 })
          }
          data={LANGUAGE_FILTERS.map((value) => ({
            value,
            label: t(`mappings.languages.${value}`),
          }))}
        />
        <Select
          label={t("mappings.frequency")}
          value={search.frequency}
          onChange={(value) =>
            updateSearch({
              frequency: FREQUENCY_FILTERS.find((v) => v === value) ?? "all",
              page: 1,
            })
          }
          data={FREQUENCY_FILTERS.map((value) => ({
            value,
            label: t(`mappings.frequencies.${value}`),
          }))}
        />
        <ClearableTextInput
          label={t("mappings.search")}
          value={searchInput}
          onChange={(event) => {
            const value = event.currentTarget.value;
            setSearchInput(value);
            // 输入法组合期间只改本地值, 停下 300ms 后才落到 URL 触发查询.
            if (searchDebounce.current) clearTimeout(searchDebounce.current);
            searchDebounce.current = setTimeout(() => {
              updateSearch({ search: value, page: 1 });
            }, 300);
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
          {t("mappings.batchConfirm")}
        </Button>
        <Button
          size="xs"
          color="red"
          variant="light"
          disabled={!selected.length}
          onClick={() => void batchToggleSelected()}
        >
          {selectedAllArchived ? t("mappings.batchRestore") : t("mappings.batchArchive")}
        </Button>
        <Text size="xs" c="dimmed">
          {t("mappings.selected", { count: selected.length })}
        </Text>
      </Group>

      <Group gap="xs">
        <Badge color="orange">
          {t("mappings.stats.pending", { count: query.data?.stats.pending ?? 0 })}
        </Badge>
        <Badge color="teal">
          {t("mappings.stats.mapped", { count: query.data?.stats.mapped ?? 0 })}
        </Badge>
        <Badge color="gray">
          {t("mappings.stats.archived", { count: query.data?.stats.archived ?? 0 })}
        </Badge>
        <Badge>{t("mappings.stats.all", { count: query.data?.stats.all ?? 0 })}</Badge>
        <Text size="xs" c="dimmed">
          {t("mappings.sortedByUsage")}
        </Text>
      </Group>

      {query.isError && <Alert color="red">加载失败：{String(query.error)}</Alert>}
      {message && <Alert color="red">{message}</Alert>}
      <Text size="xs" c="dimmed">
        {t("mappings.hint")}
      </Text>

      <Paper withBorder radius="md" style={{ overflowX: narrow ? undefined : "auto" }}>
        {narrow ? (
          <Stack gap={0}>
            {entries.map((entry) => (
              <EntryCard
                key={entry.original}
                entry={entry}
                group={group}
                roles={entryRoles(entry)}
                kindLabel={kindLabel}
                languageLabel={languageLabel}
                draft={edits[entry.original] ?? entry.translation ?? ""}
                selected={selected.includes(entry.original)}
                onSelect={(checked) => toggleSelected(entry, checked, false)}
                onDraft={(value) => setEdits((old) => ({ ...old, [entry.original]: value }))}
                onSave={() =>
                  save.mutate({
                    original: entry.original,
                    translation: edits[entry.original] ?? null,
                  })
                }
                onClear={() => void clearTranslationThenArchive(entry.original)}
                onRestore={() => restore.mutate(entry.original)}
              />
            ))}
            {!entries.length && (
              <Text c="dimmed" ta="center" py="lg">
                {t("mappings.empty")}
              </Text>
            )}
          </Stack>
        ) : (
          <Table
            striped
            highlightOnHover
            verticalSpacing="sm"
            style={{ tableLayout: "fixed", width: "100%" }}
          >
            <Table.Thead style={{ whiteSpace: "nowrap" }}>
              <Table.Tr>
                <Table.Th style={{ width: 35 }}>
                  <Checkbox
                    aria-label={t("mappings.selectAll")}
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
                <Table.Th style={{ width: "32%" }}>{t("mappings.original")}</Table.Th>
                {/* 窄列收紧左右内边距: 5% 的宽度里默认内边距占掉大半, 文字只能往外挤. */}
                <Table.Th style={{ width: "5%", paddingInline: 4 }} ta="center">
                  {t("mappings.languageColumn")}
                </Table.Th>
                <Table.Th style={{ width: "5%", paddingInline: 4 }} ta="center">
                  {t("mappings.usage")}
                </Table.Th>
                <Table.Th style={{ width: "28%" }}>{t("mappings.sources")}</Table.Th>
                <Table.Th style={{ width: "30%" }}>{t("mappings.translation")}</Table.Th>
                <Table.Th style={{ width: 170 }}>{t("mappings.actions")}</Table.Th>
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
                        aria-label={t("mappings.select", { name: entry.original })}
                        checked={selected.includes(entry.original)}
                        onChange={(event) =>
                          toggleSelected(
                            entry,
                            event.currentTarget.checked,
                            (event.nativeEvent as MouseEvent).shiftKey,
                          )
                        }
                      />
                    </Table.Td>
                    <Table.Td>
                      {/* 单行不换行: 名字被列宽截断时用省略号收尾, 悬停给出全名.
                          徽章不参与压缩, 否则长名字会把它们挤变形. */}
                      <Group gap="xs" wrap="nowrap">
                        <Text fw={500} truncate title={entry.original} style={{ minWidth: 0 }}>
                          {entry.original}
                        </Text>
                        <Group gap={4} wrap="nowrap" style={{ flexShrink: 0 }}>
                          {entry.archived && (
                            <Badge size="xs" color="gray" variant="light">
                              {t("mappings.statuses.archived")}
                            </Badge>
                          )}
                          {entry.manual && (
                            <Badge size="xs" color="blue" variant="light">
                              {t("mappings.manual")}
                            </Badge>
                          )}
                          {/* 片商组: 一个词可能同时是制作商与发行商, 标出来就不必分两行看. */}
                          {entryRoles(entry).map((role) => (
                            <Badge key={role} size="xs" color="grape" variant="light">
                              {kindLabel(role)}
                            </Badge>
                          ))}
                        </Group>
                      </Group>
                    </Table.Td>
                    <Table.Td style={{ paddingInline: 4 }} ta="center">
                      <Text size="sm" truncate title={languageLabel(entry.language)}>
                        {languageLabel(entry.language)}
                      </Text>
                    </Table.Td>
                    <Table.Td style={{ paddingInline: 4 }} ta="center">
                      {entry.usage_count}
                    </Table.Td>
                    <Table.Td>
                      <Text
                        size="xs"
                        c="dimmed"
                        truncate
                        title={entry.sources?.join("、") || undefined}
                      >
                        {entry.sources?.join("、") || "—"}
                      </Text>
                    </Table.Td>
                    <Table.Td style={{ paddingLeft: 0, paddingRight: 0 }}>
                      {entry.archived ? (
                        <Text size="xs" c="dimmed">
                          {t("mappings.noTranslation")}
                        </Text>
                      ) : (
                        <ClearableTextInput
                          size="sm"
                          styles={{
                            root: { width: "100%" },
                            wrapper: { width: "100%" },
                            input: { width: "100%" },
                          }}
                          value={pendingTranslation}
                          placeholder={t("mappings.translationPlaceholder")}
                          onChange={(event) => {
                            const value = event.currentTarget.value;
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
                            {t("mappings.restore")}
                          </Button>
                        ) : (
                          <>
                            <Button
                              size="xs"
                              onClick={() =>
                                save.mutate({
                                  original: entry.original,
                                  translation: edits[entry.original] ?? null,
                                })
                              }
                              loading={save.isPending}
                            >
                              {t("mappings.save")}
                            </Button>
                            <Button
                              size="xs"
                              variant="subtle"
                              color="red"
                              // 判据取输入框里的当前值, 不取已保存的译名: 用户把译名清掉的那一刻
                              // 就该能点归档, 不该还逼他先按保存.
                              disabled={pendingTranslation.trim().length > 0}
                              title={
                                pendingTranslation.trim()
                                  ? t("mappings.cannotArchiveMapped")
                                  : undefined
                              }
                              onClick={() => void clearTranslationThenArchive(entry.original)}
                            >
                              {t("mappings.archive")}
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
                  <Table.Td colSpan={columnCount}>
                    <Text c="dimmed" ta="center" py="lg">
                      {t("mappings.empty")}
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
          {t("mappings.total", { count: query.data?.total ?? 0 })}
        </Text>
        <Pagination
          total={pages}
          value={search.page}
          onChange={(nextPage) => {
            void navigate({ search: (prev) => ({ ...prev, page: nextPage }) });
            selectionAnchor.current = null;
          }}
        />
      </Group>

      <Alert color="blue" variant="light">
        {t("mappings.llmHint")}
      </Alert>
      {group === "vendor" && (
        <Alert color="grape" variant="light">
          {t("mappings.vendorHint")}
        </Alert>
      )}

      <Modal
        opened={manual !== null}
        onClose={() => setManual(null)}
        title={t("mappings.manualTitle")}
        centered
      >
        <Stack gap="sm">
          <Text size="xs" c="dimmed">
            {t("mappings.manualHint")}
          </Text>
          <ClearableTextInput
            label={t("mappings.original")}
            value={manual?.original ?? ""}
            onChange={(event) => {
              const value = event.currentTarget.value;
              setManual((prev) => (prev ? { ...prev, original: value } : prev));
            }}
            data-autofocus
          />
          <ClearableTextInput
            label={t("mappings.translation")}
            value={manual?.translation ?? ""}
            onChange={(event) => {
              const value = event.currentTarget.value;
              setManual((prev) => (prev ? { ...prev, translation: value } : prev));
            }}
          />
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setManual(null)}>
              {t("common:actions.cancel")}
            </Button>
            <Button
              disabled={!manual?.original.trim()}
              loading={save.isPending}
              onClick={() => {
                if (!manual) return;
                save.mutate(
                  {
                    original: manual.original.trim(),
                    translation: manual.translation.trim() || null,
                  },
                  { onSuccess: () => setManual(null) },
                );
              }}
            >
              {t("mappings.save")}
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  );
}

interface EntryCardProps {
  entry: Entry;
  group: Group;
  roles: string[];
  kindLabel: (kind: string) => string;
  languageLabel: (code: string) => string;
  draft: string;
  selected: boolean;
  onSelect: (checked: boolean) => void;
  onDraft: (value: string) => void;
  onSave: () => void;
  onClear: () => void;
  onRestore: () => void;
}

/** 窄屏卡片: 与表格同一组字段, 只是改成纵向堆叠. */
function EntryCard({
  entry,
  roles,
  kindLabel,
  languageLabel,
  draft,
  selected,
  onSelect,
  onDraft,
  onSave,
  onClear,
  onRestore,
}: EntryCardProps) {
  const { t } = useTranslation(["metadata", "common"]);
  return (
    <Stack
      gap="xs"
      p="sm"
      style={{ borderBottom: "1px solid var(--mantine-color-default-border)" }}
    >
      <Group gap="xs" wrap="nowrap" align="flex-start">
        <Checkbox
          aria-label={t("mappings.select", { name: entry.original })}
          checked={selected}
          onChange={(event) => onSelect(event.currentTarget.checked)}
        />
        <Box style={{ flex: 1, minWidth: 0 }}>
          <Text fw={500} size="sm" style={{ wordBreak: "break-word" }}>
            {entry.original}
          </Text>
          <Group gap="xs" mt={2}>
            <Text size="xs" c="dimmed">
              {languageLabel(entry.language)}
            </Text>
            <Text size="xs" c="dimmed">
              ×{entry.usage_count}
            </Text>
            {roles.map((role) => (
              <Badge key={role} size="xs" color="grape" variant="light">
                {kindLabel(role)}
              </Badge>
            ))}
            {/* 来源只有标签组会记; 别的组这一行没有任何内容, 不必留个空位. */}
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
            {t("mappings.noTranslation")}
          </Text>
          <Button size="xs" variant="light" onClick={onRestore}>
            {t("mappings.restore")}
          </Button>
        </Group>
      ) : (
        <Stack gap="xs">
          <ClearableTextInput
            size="sm"
            value={draft}
            placeholder={t("mappings.translationPlaceholder")}
            onChange={(event) => onDraft(event.currentTarget.value)}
          />
          <Group gap="xs" justify="flex-end">
            <Button size="xs" onClick={onSave}>
              {t("mappings.save")}
            </Button>
            <Button
              size="xs"
              variant="subtle"
              color="red"
              disabled={draft.trim().length > 0}
              onClick={onClear}
            >
              {t("mappings.archive")}
            </Button>
          </Group>
        </Stack>
      )}
    </Stack>
  );
}
