import {
  ActionIcon,
  Badge,
  Button,
  Group,
  Menu,
  SegmentedControl,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import {
  IconChevronDown,
  IconFilter,
  IconRefresh,
  IconSearch,
  IconTable,
  IconUpload,
  IconX,
} from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createFileRoute, stripSearchParams } from "@tanstack/react-router";
import type { ParseKeys } from "i18next";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { notifications } from "@mantine/notifications";
import { extractErrorMessage } from "@/lib/api-error";
import { confirm } from "@/lib/confirm";
import {
  getFacetOptions,
  listActorsOptions,
  listTasksQueryKey,
  submitTaskMutation,
} from "@/client/@tanstack/react-query.gen";
import type { ActorGender, ActorSortField } from "@/client/types.gen";
import { BrowsePageShell } from "@/components/common/browse-page-shell";
import { HintedActionIcon } from "@/components/common/hinted-action-icon";
import { ListPagination } from "@/components/common/list-pagination";
import { ListDefaultActions } from "@/components/common/list-default-actions";
import { BrowseDisplaySettings } from "@/components/common/browse-display-settings";
import { SortMenu } from "@/components/common/sort-menu";
import { ActorFilterControls } from "@/components/media/actor-filter-controls";
import { ActorGrid } from "@/components/media/actor-grid";
import { ActorTable } from "@/components/media/actor-table";
import {
  ACTOR_RANGE_FILTERS,
  type ActorFilterKey,
  type ActorFilterPatch,
  type ActorFilterValues,
  actorFilterValuesFromSearch,
  actorListQueryFromSearch,
  actorsBrowseSearchSchema,
  clearActorFilterKeys,
  DEFAULT_ACTOR_GENDER_FILTER,
  formatActorRangeChip,
  hasActiveActorFilters,
  hasNonDefaultActorFilters,
  mergeActorFilterPatch,
  rangeValue,
  replaceActorFilters,
} from "@/lib/actors/browse";
import { useNarrowViewport } from "@/hooks/use-narrow-viewport";
import { exhaustiveRecord } from "@/lib/exhaustive";
import { ACTOR_SORT_FIELDS } from "@/lib/exhaustive-maps";
import { actorListDefaults } from "@/lib/nav-defaults";
import { useUIStore } from "@/stores/ui";

const ACTOR_SORT_I18N_KEY = exhaustiveRecord<ActorSortField>()({
  name: "manage.name",
  count: "manage.count",
  updated_at: "columns.updated",
  has_image: "actors.hasImage",
  birthday: "browse.person.birthday",
  height: "browse.person.height",
  bust: "browse.person.bust",
  waist: "browse.person.waist",
  hip: "browse.person.hip",
  cup: "browse.person.cup",
} as const satisfies Record<ActorSortField, ParseKeys<"metadata">>);

const GENDER_I18N = {
  female: "browse.person.genderFemale",
  male: "browse.person.genderMale",
  unknown: "browse.person.genderUnknown",
} as const satisfies Record<ActorGender, ParseKeys<"metadata">>;

export const Route = createFileRoute("/actors/")({
  validateSearch: actorsBrowseSearchSchema,
  search: {
    middlewares: [
      stripSearchParams({
        view: "grid",
        page: 1,
        sort_by: "count",
        order: "desc",
        gender: [...DEFAULT_ACTOR_GENDER_FILTER],
      }),
    ],
  },
  component: ActorsIndexPage,
});

function ActiveFilterChip({ label, onClear }: { label: string; onClear: () => void }) {
  return (
    <Group gap={4} wrap="nowrap">
      <Badge variant="outline">{label}</Badge>
      <ActionIcon size="sm" variant="subtle" color="gray" onClick={onClear} aria-label="clear">
        <IconX size={14} />
      </ActionIcon>
    </Group>
  );
}

/** 标签名经单条分类查询, 与片库的芯片同形. */
function ActiveUserTagChip({ tagId, onClear }: { tagId: number; onClear: () => void }) {
  const { t } = useTranslation("metadata");
  const { data } = useQuery(getFacetOptions({ path: { kind: "user_tag", facet_id: tagId } }));
  return (
    <ActiveFilterChip label={`${t("detail.userTags")}: ${data?.name ?? tagId}`} onClear={onClear} />
  );
}

function ActorsIndexPage() {
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  const { t } = useTranslation(["metadata", "common", "savedQueries"]);
  const queryClient = useQueryClient();
  const [actorPushMode, setActorPushMode] = useState<"full" | "incremental">("full");
  const actorPush = useMutation({
    ...submitTaskMutation(),
    onSuccess: () => {
      notifications.show({ message: t("actors.syncSubmitted"), color: "blue" });
      void queryClient.invalidateQueries({ queryKey: listTasksQueryKey() });
    },
    onError: (error) =>
      notifications.show({
        message: extractErrorMessage(error, t("common:toast.operationFailed")),
        color: "red",
      }),
  });
  const listLimit = useUIStore((s) => s.pageSizes.actorsList);
  const cardSize = useUIStore((s) => s.cardSizes.actors);
  const actorListSort = useUIStore((s) => s.actorListSort);
  const setActorListSort = useUIStore((s) => s.setActorListSort);
  const rememberedView = useUIStore((s) => s.actorsView);
  const setRememberedView = useUIStore((s) => s.setActorsView);

  useEffect(() => {
    if (search.view !== rememberedView) {
      void navigate({ search: (prev) => ({ ...prev, view: rememberedView, page: 1 }) });
    }
  }, [search.view, rememberedView, navigate]);

  useEffect(() => {
    const currentBy = search.sort_by;
    const currentOrder = search.order;
    const isDefault = currentBy === "count" && currentOrder === "desc";
    const storedIsDefault = actorListSort.sort_by === "count" && actorListSort.order === "desc";
    if (isDefault && !storedIsDefault) {
      void navigate({
        search: (prev) => ({
          ...prev,
          sort_by: actorListSort.sort_by,
          order: actorListSort.order,
          page: 1,
        }),
      });
    } else if (
      !isDefault &&
      (actorListSort.sort_by !== currentBy || actorListSort.order !== currentOrder)
    ) {
      setActorListSort({ sort_by: currentBy, order: currentOrder });
    }
  }, [search.sort_by, search.order, actorListSort, setActorListSort, navigate]);

  const filters = actorFilterValuesFromSearch(search);
  const hasActiveFilters = hasActiveActorFilters(filters) || search.saved_query_id != null;
  const hasNonDefaultFilters = hasNonDefaultActorFilters(filters) || search.saved_query_id != null;

  const [searchInput, setSearchInput] = useState(search.q ?? "");
  const [advancedOpen, setAdvancedOpen] = useState(hasNonDefaultFilters);
  const narrowViewport = useNarrowViewport("md");
  const debounceRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const isList = search.view === "list";
  const offset = (search.page - 1) * listLimit;
  const listQueryParams = actorListQueryFromSearch(search);

  // list 仅 list 视图订阅; grid 不再并行预热, 避免和无限滚动/头像抢连接.
  const listQuery = useQuery({
    ...listActorsOptions({
      query: {
        ...listQueryParams,
        offset,
        limit: listLimit,
      },
    }),
    enabled: isList,
  });

  const gridQuery = useQuery({
    ...listActorsOptions({
      query: {
        ...listQueryParams,
        offset,
        limit: listLimit,
      },
    }),
    enabled: !isList,
  });

  const listItems = listQuery.data?.items ?? [];
  const listTotal = listQuery.data?.total ?? 0;
  const gridItems = gridQuery.data?.items ?? [];
  const gridTotal = gridQuery.data?.total ?? 0;
  const total = isList ? listTotal : gridTotal;

  function handleSearchChange(v: string) {
    setSearchInput(v);
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      void navigate({
        search: (prev) => ({ ...prev, q: v || undefined, page: 1 }),
      });
    }, 300);
  }

  function onSort(field: ActorSortField) {
    void navigate({
      search: (prev) => {
        const nextOrder =
          prev.sort_by === field
            ? prev.order === "asc"
              ? "desc"
              : "asc"
            : field === "count" || field === "has_image" || field === "updated_at"
              ? "desc"
              : "asc";
        setActorListSort({ sort_by: field, order: nextOrder });
        return { ...prev, sort_by: field, order: nextOrder, page: 1 };
      },
    });
  }

  function applyFilterPatch(patch: ActorFilterPatch) {
    void navigate({
      search: (prev) => mergeActorFilterPatch(prev, patch),
    });
  }

  function applyFilters(next: ActorFilterValues) {
    void navigate({
      search: (prev) => replaceActorFilters(prev, next),
    });
  }

  function clearFilterKeys(keys: readonly ActorFilterKey[]) {
    void navigate({
      search: (prev) => clearActorFilterKeys(prev, keys),
    });
  }

  return (
    <BrowsePageShell
      fill={isList}
      title={<Title order={2}>{t("actors.title")}</Title>}
      actions={<ListDefaultActions update={{ key: "actors", value: actorListDefaults(search) }} />}
      viewSwitch={
        <SegmentedControl
          value={search.view}
          onChange={(v) => {
            const nextView = v === "list" ? "list" : "grid";
            setRememberedView(nextView);
            void navigate({
              search: (prev) => ({ ...prev, view: nextView, page: 1 }),
            });
          }}
          data={[
            { value: "grid", label: t("view.grid") },
            { value: "list", label: t("view.list") },
          ]}
        />
      }
      summary={
        <Text size="sm" c="dimmed">
          {t("common:pagination.totalItems", { count: total })}
        </Text>
      }
      search={
        <TextInput
          value={searchInput}
          onChange={(e) => handleSearchChange(e.currentTarget.value)}
          placeholder={t("actors.searchPlaceholder")}
          leftSection={<IconSearch size={16} />}
          w="100%"
        />
      }
      extras={
        <>
          <Group gap={0} wrap="nowrap">
            <Button
              size="sm"
              variant="light"
              leftSection={
                actorPushMode === "full" ? <IconRefresh size={16} /> : <IconUpload size={16} />
              }
              loading={actorPush.isPending}
              onClick={async () => {
                if (actorPushMode === "full") {
                  const ok = await confirm({
                    title: t("actors.fullSyncConfirmTitle"),
                    message: t("actors.fullSyncConfirmMessage"),
                    confirmLabel: t("actors.fullSync"),
                    danger: false,
                  });
                  if (!ok) return;
                }
                actorPush.mutate({ body: { type: "actor_push", mode: actorPushMode } });
              }}
              style={{ borderTopRightRadius: 0, borderBottomRightRadius: 0 }}
            >
              {actorPushMode === "full" ? t("actors.fullSync") : t("actors.incrementalSync")}
            </Button>
            <Menu position="bottom-end" shadow="md" width={190}>
              <Menu.Target>
                <Button
                  size="sm"
                  variant="light"
                  px={8}
                  aria-label={t("actors.chooseSyncMode")}
                  disabled={actorPush.isPending}
                  style={{ borderTopLeftRadius: 0, borderBottomLeftRadius: 0, borderLeft: 0 }}
                >
                  <IconChevronDown size={16} />
                </Button>
              </Menu.Target>
              <Menu.Dropdown>
                <Menu.Item
                  leftSection={actorPushMode === "full" ? <IconRefresh size={14} /> : undefined}
                  onClick={() => setActorPushMode("full")}
                >
                  {t("actors.fullSync")}
                </Menu.Item>
                <Menu.Item
                  leftSection={
                    actorPushMode === "incremental" ? <IconUpload size={14} /> : undefined
                  }
                  onClick={() => setActorPushMode("incremental")}
                >
                  {t("actors.incrementalSync")}
                </Menu.Item>
              </Menu.Dropdown>
            </Menu>
          </Group>
          {/* 窄屏的筛选在底部面板里常驻展开, 该开关只在宽屏有意义. */}
          {narrowViewport ? null : (
            <HintedActionIcon
              variant={advancedOpen || hasNonDefaultFilters ? "filled" : "default"}
              size={36}
              onClick={() => setAdvancedOpen((v) => !v)}
              label={t("search.advanced")}
            >
              <IconFilter size={16} />
            </HintedActionIcon>
          )}
          {!isList && (
            <SortMenu
              options={ACTOR_SORT_FIELDS.map((f) => ({
                value: f,
                label: t(ACTOR_SORT_I18N_KEY[f]),
              }))}
              sortBy={search.sort_by}
              order={search.order}
              defaultSortBy="count"
              defaultOrder="desc"
              onChange={(nextSortBy, nextOrder) =>
                void navigate({
                  search: (prev) => ({
                    ...prev,
                    sort_by: nextSortBy,
                    order: nextOrder,
                    page: 1,
                  }),
                })
              }
            />
          )}
          <BrowseDisplaySettings
            pageSizeKey="actorsList"
            onPageSizeChanged={() => void navigate({ search: (prev) => ({ ...prev, page: 1 }) })}
          />
        </>
      }
      filterPanel={
        <ActorFilterControls
          opened={narrowViewport || advancedOpen}
          committed={filters}
          onApply={applyFilters}
        />
      }
    >
      {hasActiveFilters && (
        <Group gap="xs">
          {search.saved_query_id != null && (
            <Group gap={4} wrap="nowrap">
              <ActiveFilterChip
                label={`${t("common:nav.savedQueries")}: #${search.saved_query_id}`}
                onClear={() =>
                  void navigate({
                    search: (prev) => ({ ...prev, saved_query_id: undefined, page: 1 }),
                  })
                }
              />
              <ActionIcon
                size="sm"
                variant="subtle"
                component="a"
                href={`/saved-queries/${search.saved_query_id}`}
                target="_blank"
                rel="noreferrer"
                aria-label={t("savedQueries:openData")}
              >
                <IconTable size={14} />
              </ActionIcon>
            </Group>
          )}
          {filters.gender.map((g) => (
            <ActiveFilterChip
              key={g}
              label={t(GENDER_I18N[g])}
              onClear={() => applyFilterPatch({ gender: filters.gender.filter((x) => x !== g) })}
            />
          ))}
          {ACTOR_RANGE_FILTERS.map((range) => {
            const min = rangeValue(filters, range.min);
            const max = rangeValue(filters, range.max);
            if (min == null && max == null) return null;
            return (
              <ActiveFilterChip
                key={range.min}
                label={formatActorRangeChip(t(range.labelKey), min, max)}
                onClear={() => clearFilterKeys([range.min, range.max])}
              />
            );
          })}
          {filters.birthplace != null && (
            <ActiveFilterChip
              label={`${t("browse.person.birthplace")}: ${filters.birthplace}`}
              onClear={() => clearFilterKeys(["birthplace"])}
            />
          )}
          {filters.user_tag_id != null && (
            <ActiveUserTagChip
              tagId={filters.user_tag_id}
              onClear={() => applyFilterPatch({ user_tag_id: undefined })}
            />
          )}
          {filters.has_person != null && (
            <ActiveFilterChip
              label={
                filters.has_person === "true"
                  ? t("actors.filterHasPerson")
                  : t("actors.filterNoPerson")
              }
              onClear={() => applyFilterPatch({ has_person: undefined })}
            />
          )}
          {filters.has_image != null && (
            <ActiveFilterChip
              label={
                filters.has_image === "true"
                  ? t("actors.filterHasImage")
                  : t("actors.filterNoImage")
              }
              onClear={() => applyFilterPatch({ has_image: undefined })}
            />
          )}
        </Group>
      )}

      {isList ? (
        <ActorTable
          key={listLimit}
          items={listItems}
          isLoading={listQuery.isLoading}
          total={listTotal}
          page={search.page}
          sortBy={search.sort_by}
          order={search.order}
          onPageChange={(page) => void navigate({ search: (prev) => ({ ...prev, page }) })}
          onSort={onSort}
        />
      ) : (
        <>
          <ActorGrid
            cardSize={cardSize}
            items={gridItems}
            loading={gridQuery.isLoading && gridItems.length === 0}
            emptyMessage={t("actors.empty")}
          />
          <ListPagination
            totalPages={Math.ceil(gridTotal / listLimit)}
            page={search.page}
            onChange={(page) => void navigate({ search: (prev) => ({ ...prev, page }) })}
          />
        </>
      )}
    </BrowsePageShell>
  );
}
