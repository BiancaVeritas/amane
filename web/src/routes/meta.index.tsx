import {
  ActionIcon,
  Alert,
  Badge,
  Box,
  Button,
  Group,
  SegmentedControl,
  Stack,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import {
  IconArrowLeft,
  IconArrowRight,
  IconFilter,
  IconSearch,
  IconSparkles,
  IconTable,
  IconX,
} from "@tabler/icons-react";
import { useQuery } from "@tanstack/react-query";
import { createFileRoute, stripSearchParams } from "@tanstack/react-router";
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  getFacetOptions,
  listLibrariesOptions,
  listMetadataOptions,
  semanticStatusOptions,
} from "@/client/@tanstack/react-query.gen";
import type {
  EmbeddingKind,
  FacetKind,
  ListMetadataData,
  MetadataSortField,
  SemanticHitInfo,
} from "@/client/types.gen";
import { BrowsePageShell } from "@/components/common/browse-page-shell";
import { HintedActionIcon } from "@/components/common/hinted-action-icon";
import { ListPagination } from "@/components/common/list-pagination";
import { ListDefaultActions } from "@/components/common/list-default-actions";
import { BrowseDisplaySettings } from "@/components/common/browse-display-settings";
import { SortMenu } from "@/components/common/sort-menu";
import { FacetBadge } from "@/components/media/facet-badge";
import {
  FacetFilterControls,
  type FilePhaseFilters,
  type HasFilesFilter,
} from "@/components/media/facet-filter-controls";
import { MetaTable } from "@/components/media/meta-table";
import { PosterGrid } from "@/components/media/poster-grid";
import { activeFacetFilters, addFacetId, type FacetFilters, removeFacetId } from "@/lib/facets";
import { useNarrowViewport } from "@/hooks/use-narrow-viewport";
import { EMBEDDING_KINDS } from "@/lib/exhaustive-maps";
import { metaSearchSchema, METADATA_SORT_OPTIONS } from "@/lib/media/browse";
import { metaListDefaults } from "@/lib/nav-defaults";
import { useUIStore } from "@/stores/ui";
import classes from "./meta.module.css";

const SEMANTIC_KIND_OPTIONS = ["all", ...EMBEDDING_KINDS] as const;

/** 分段控件回传 string; "all" 与未知值均视为缺省 (封面与剧照都参与). */
function toEmbeddingKind(value: string): EmbeddingKind | undefined {
  return EMBEDDING_KINDS.find((kind) => kind === value);
}

export const Route = createFileRoute("/meta/")({
  validateSearch: metaSearchSchema,
  search: { middlewares: [stripSearchParams({ view: "grid", page: 1 })] },
  component: MetaIndexPage,
});

function parseHasFiles(value: "true" | "false" | undefined): HasFilesFilter {
  if (value === "true") return true;
  if (value === "false") return false;
  return null;
}

function triToSearch(value: HasFilesFilter): "true" | "false" | undefined {
  if (value === true) return "true";
  if (value === false) return "false";
  return undefined;
}

function ActiveFacetChip({
  kind,
  id,
  onClear,
}: {
  kind: FacetKind;
  id: number;
  onClear: () => void;
}) {
  const { data } = useQuery(getFacetOptions({ path: { kind, facet_id: id } }));
  const { t } = useTranslation("metadata");
  return (
    <Group gap={4} wrap="nowrap">
      <FacetBadge
        kind={kind}
        id={id}
        name={`${t(`browse.kinds.${kind}`)}: ${data?.name ?? `#${id}`}`}
        variant="outline"
      />
      <ActionIcon size="sm" variant="subtle" color="gray" onClick={onClear} aria-label="clear">
        <IconX size={14} />
      </ActionIcon>
    </Group>
  );
}

function ActiveHasFilesChip({ hasFiles, onClear }: { hasFiles: boolean; onClear: () => void }) {
  const { t } = useTranslation("metadata");
  return (
    <ActiveTriChip
      label={`${t("search.hasFiles")}: ${hasFiles ? t("search.hasFilesYes") : t("search.hasFilesNo")}`}
      onClear={onClear}
    />
  );
}

function ActiveLibrariesChip({
  ids,
  nameById,
  onClear,
}: {
  ids: number[];
  nameById: Map<number, string>;
  onClear: () => void;
}) {
  const { t } = useTranslation("metadata");
  const names = ids.map((id) => nameById.get(id) ?? `#${id}`).join(", ");
  return <ActiveTriChip label={`${t("search.library")}: ${names}`} onClear={onClear} />;
}

function ActiveVrChip({ vr, onClear }: { vr: boolean; onClear: () => void }) {
  const { t } = useTranslation("metadata");
  return (
    <ActiveTriChip
      label={`${t("search.vr")}: ${vr ? t("search.yes") : t("search.no")}`}
      onClear={onClear}
    />
  );
}

function ActiveTriChip({ label, onClear }: { label: string; onClear: () => void }) {
  return (
    <Group gap={4} wrap="nowrap">
      <Badge variant="outline">{label}</Badge>
      <ActionIcon size="sm" variant="subtle" color="gray" onClick={onClear} aria-label="clear">
        <IconX size={14} />
      </ActionIcon>
    </Group>
  );
}

function MetaIndexPage() {
  const { t } = useTranslation(["metadata", "common", "savedQueries"]);
  const search = Route.useSearch();
  const semanticMode = search.semantic === 1;
  const semanticQuery = semanticMode && search.q ? search.q : undefined;
  const semanticActive = semanticQuery != null;
  const navigate = Route.useNavigate();
  const activePageSizeKey =
    search.view === "cover" ? "metaCover" : search.view === "grid" ? "metaGrid" : "metaList";
  const listLimit = useUIStore((s) => s.pageSizes[activePageSizeKey]);
  const gridCardSize = useUIStore((s) => s.cardSizes.metaGrid);
  const coverCardSize = useUIStore((s) => s.cardSizes.metaCover);
  const cardSize = search.view === "cover" ? coverCardSize : gridCardSize;
  const rememberedView = useUIStore((s) => s.metaView);
  const setRememberedView = useUIStore((s) => s.setMetaView);

  useEffect(() => {
    if (search.view !== rememberedView) {
      void navigate({ search: (prev) => ({ ...prev, view: rememberedView, page: 1 }) });
    }
  }, [search.view, rememberedView, navigate]);
  const libraryIds = search.library_id ?? [];
  const { data: librariesData } = useQuery(listLibrariesOptions());
  const libraries = librariesData?.items ?? [];
  const libraryNameById = new Map(libraries.map((library) => [library.id, library.name]));
  const metaSort = useUIStore((s) => s.metaSort);
  const setMetaSort = useUIStore((s) => s.setMetaSort);
  const narrowViewport = useNarrowViewport("md");

  const hasFiles = parseHasFiles(search.has_files);
  const vr = parseHasFiles(search.vr);
  const filePhase: FilePhaseFilters = {
    has_subtitle: parseHasFiles(search.has_subtitle),
    uncensored: parseHasFiles(search.uncensored),
    mosaic: search.mosaic ?? null,
    definition: search.definition ?? null,
    content_type: search.content_type ?? null,
  };
  const phaseActive =
    filePhase.has_subtitle !== null ||
    filePhase.uncensored !== null ||
    filePhase.mosaic != null ||
    filePhase.definition != null ||
    filePhase.content_type != null;
  const [searchInput, setSearchInput] = useState(search.q ?? "");
  // 筛选条件可以保留在 URL 中, 但每次进入媒体资料页时筛选面板都从关闭状态开始.
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const filters: FacetFilters = {
    actor_id: search.actor_id,
    director_id: search.director_id,
    tag_id: search.tag_id,
    studio_id: search.studio_id,
    publisher_id: search.publisher_id,
    series_id: search.series_id,
    user_tag_id: search.user_tag_id,
  };

  const filterParams = {
    library_id: libraryIds.length > 0 ? libraryIds : undefined,
    has_files: hasFiles === null ? undefined : hasFiles,
    vr: vr === null ? undefined : vr,
    has_subtitle: filePhase.has_subtitle === null ? undefined : filePhase.has_subtitle,
    uncensored: filePhase.uncensored === null ? undefined : filePhase.uncensored,
    mosaic: filePhase.mosaic ?? undefined,
    definition: filePhase.definition ?? undefined,
    content_type: filePhase.content_type ?? undefined,
    ...filters,
    ...(search.saved_query_id != null ? { saved_query_id: search.saved_query_id } : {}),
  };

  const semanticParams: NonNullable<ListMetadataData["query"]> =
    semanticQuery != null
      ? { semantic_query: semanticQuery, semantic_kind: search.kind ?? "all" }
      : {};

  const listQueryParams = {
    search: semanticActive ? undefined : search.q || undefined,
    sort_by: semanticActive ? undefined : search.sort_by,
    order: semanticActive ? undefined : search.order,
    ...semanticParams,
    ...filterParams,
  };

  const isList = search.view === "list";
  const { data: semanticStatus, refetch: refetchSemanticStatus } =
    useQuery(semanticStatusOptions());
  const semanticAvailable = semanticStatus?.available === true;

  // 进入语义模式时重新获取: 索引任务可能刚在别处完成, 缓存的状态会过期.
  useEffect(() => {
    if (semanticMode) void refetchSemanticStatus();
  }, [semanticMode, refetchSemanticStatus]);

  // URL 未显式携带排序时, 恢复上次记忆; URL 显式携带排序时, 以 URL 为准同步记忆.
  // 必须区分“没有排序参数”和“用户主动选择默认排序”, 否则显式选择默认值会被旧记忆覆盖.
  useEffect(() => {
    const currentBy = search.sort_by ?? "updated_at";
    const currentOrder = search.order ?? "desc";
    const hasExplicitSort = search.sort_by != null || search.order != null;
    const storedMatchesCurrent = metaSort.sort_by === currentBy && metaSort.order === currentOrder;

    if (!hasExplicitSort) {
      const storedIsDefault = metaSort.sort_by === "updated_at" && metaSort.order === "desc";
      if (!storedIsDefault) {
        void navigate({
          search: (prev) => ({
            ...prev,
            sort_by: metaSort.sort_by,
            order: metaSort.order,
            page: 1,
          }),
        });
      }
    } else if (!storedMatchesCurrent) {
      setMetaSort({ sort_by: currentBy, order: currentOrder });
    }
  }, [search.sort_by, search.order, metaSort, setMetaSort, navigate]);

  const semanticNotice =
    !semanticActive || semanticStatus == null
      ? null
      : !semanticStatus.enabled
        ? t("semantic.disabled")
        : !semanticStatus.search_ready
          ? t("semantic.modelMissing", {
              size: Math.round((semanticStatus.download_size ?? 0) / 1024 / 1024),
            })
          : !semanticStatus.has_embeddings
            ? t("semantic.noEmbeddings")
            : null;
  // 语义状态未返回或不可检索时不发送列表请求, 避免先按普通排序渲染一屏.
  const listEnabled = !semanticActive || (semanticStatus != null && semanticNotice == null);

  const gridOffset = (search.page - 1) * listLimit;
  const gridQuery = useQuery({
    ...listMetadataOptions({
      query: {
        ...listQueryParams,
        offset: gridOffset,
        limit: listLimit,
      },
    }),
    enabled: !isList && listEnabled,
  });

  const listOffset = (search.page - 1) * listLimit;
  const listQuery = useQuery({
    ...listMetadataOptions({
      query: {
        ...listQueryParams,
        offset: listOffset,
        limit: listLimit,
      },
    }),
    enabled: isList && listEnabled,
  });

  const gridItems = gridQuery.data?.items ?? [];
  const total = isList ? (listQuery.data?.total ?? 0) : (gridQuery.data?.total ?? 0);
  const semanticHits = useMemo(() => {
    const map = new Map<number, SemanticHitInfo>();
    if (!semanticActive) return map;
    if (isList) {
      for (const hit of listQuery.data?.semantic_hits ?? []) map.set(hit.id, hit);
    } else {
      for (const hit of gridQuery.data?.semantic_hits ?? []) map.set(hit.id, hit);
    }
    return map;
  }, [semanticActive, isList, listQuery.data, gridQuery.data]);

  function handleSearchChange(v: string) {
    setSearchInput(v);
    if (semanticMode) return;
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      void navigate({ search: (prev) => ({ ...prev, q: v || undefined, page: 1 }) });
    }, 300);
  }

  function submitSearch() {
    const value = searchInput.trim();
    void navigate({ search: (prev) => ({ ...prev, q: value || undefined, page: 1 }) });
  }

  function toggleSemantic() {
    // 切换搜索方式时清空输入, 避免将关键词作为画面描述提交, 或反向提交.
    setSearchInput("");
    void navigate({
      search: (prev) => ({
        ...prev,
        q: undefined,
        semantic: semanticMode ? undefined : 1,
        kind: undefined,
        page: 1,
      }),
    });
  }

  function appendFacet(kind: FacetKind, id: number) {
    void navigate({
      search: (prev) => {
        const next = addFacetId(
          {
            actor_id: prev.actor_id,
            director_id: prev.director_id,
            tag_id: prev.tag_id,
            studio_id: prev.studio_id,
            publisher_id: prev.publisher_id,
            series_id: prev.series_id,
            user_tag_id: prev.user_tag_id,
          },
          kind,
          id,
        );
        return { ...prev, ...next, page: 1 };
      },
    });
  }

  function clearFacet(kind: FacetKind, id: number) {
    void navigate({
      search: (prev) => {
        const next = removeFacetId(
          {
            actor_id: prev.actor_id,
            director_id: prev.director_id,
            tag_id: prev.tag_id,
            studio_id: prev.studio_id,
            publisher_id: prev.publisher_id,
            series_id: prev.series_id,
            user_tag_id: prev.user_tag_id,
          },
          kind,
          id,
        );
        return { ...prev, ...next, page: 1 };
      },
    });
  }

  function setHasFilesFilter(value: HasFilesFilter) {
    void navigate({
      search: (prev) => ({
        ...prev,
        has_files: triToSearch(value),
        page: 1,
      }),
    });
  }

  function setLibraryFilter(ids: number[]) {
    void navigate({
      search: (prev) => ({ ...prev, library_id: ids.length > 0 ? ids : undefined, page: 1 }),
    });
  }

  function setVrFilter(value: HasFilesFilter) {
    void navigate({
      search: (prev) => ({
        ...prev,
        vr: triToSearch(value),
        page: 1,
      }),
    });
  }

  function setFilePhaseFilter(value: FilePhaseFilters) {
    void navigate({
      search: (prev) => ({
        ...prev,
        has_subtitle: triToSearch(value.has_subtitle),
        uncensored: triToSearch(value.uncensored),
        mosaic: value.mosaic ?? undefined,
        definition: value.definition ?? undefined,
        content_type: value.content_type ?? undefined,
        page: 1,
      }),
    });
  }

  function handleSort(field: MetadataSortField) {
    const nextOrder =
      (search.sort_by ?? "updated_at") === field
        ? (search.order ?? "desc") === "asc"
          ? "desc"
          : "asc"
        : "desc";
    // 状态更新必须在 navigate 的 search updater 外执行, 保证排序记忆不依赖路由回调时机.
    setMetaSort({ sort_by: field, order: nextOrder });
    void navigate({
      search: (prev) => ({ ...prev, sort_by: field, order: nextOrder, page: 1 }),
    });
  }

  const active = activeFacetFilters(filters);
  const hasActiveFilters =
    active.length > 0 ||
    hasFiles !== null ||
    vr !== null ||
    phaseActive ||
    search.saved_query_id != null;

  // 三种展示模式共用有界页面高度; 列表由 ListToolbar 内部滚动, 海报/封面由下方网格容器滚动.
  return (
    <BrowsePageShell
      fill
      searchMaxWidth={560}
      title={<Title order={2}>{t("common:nav.meta")}</Title>}
      actions={<ListDefaultActions update={{ key: "meta", value: metaListDefaults(search) }} />}
      viewSwitch={
        <SegmentedControl
          value={search.view}
          onChange={(v) => {
            const nextView = v === "list" || v === "cover" ? v : "grid";
            setRememberedView(nextView);
            void navigate({
              search: (prev) => ({ ...prev, view: nextView, page: 1 }),
            });
          }}
          data={[
            { value: "grid", label: t("view.grid") },
            { value: "cover", label: t("view.cover") },
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
        <Group gap="xs" wrap="nowrap" w="100%">
          <div
            className={
              semanticMode
                ? `${classes.searchBox} ${classes.flowRing} ${classes.semanticInput}`
                : classes.searchBox
            }
          >
            <TextInput
              value={searchInput}
              onChange={(e) => handleSearchChange(e.currentTarget.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") submitSearch();
              }}
              placeholder={semanticMode ? t("semantic.placeholder") : t("search.placeholder")}
              leftSection={
                semanticMode ? (
                  <span className={classes.glowIcon}>
                    <IconSparkles size={16} />
                  </span>
                ) : (
                  <IconSearch size={16} />
                )
              }
              rightSection={
                semanticMode ? (
                  <ActionIcon
                    variant="subtle"
                    color="gray"
                    aria-label={t("semantic.submit")}
                    disabled={searchInput.trim().length === 0}
                    onClick={submitSearch}
                  >
                    <IconArrowRight size={16} />
                  </ActionIcon>
                ) : undefined
              }
              rightSectionPointerEvents="all"
            />
          </div>
          {semanticMode ? (
            <Button
              variant="default"
              leftSection={<IconArrowLeft size={16} />}
              onClick={toggleSemantic}
            >
              {t("semantic.back")}
            </Button>
          ) : semanticAvailable ? (
            <Button
              variant="default"
              className={classes.semanticToggle}
              leftSection={
                <span className={classes.glowIcon}>
                  <IconSparkles size={16} />
                </span>
              }
              onClick={toggleSemantic}
            >
              <span className={classes.gradientText}>{t("semantic.toggle")}</span>
            </Button>
          ) : null}
        </Group>
      }
      extras={
        <>
          {/* 窄屏的筛选在底部面板里常驻展开, 该开关只在宽屏有意义. */}
          {narrowViewport ? null : (
            <HintedActionIcon
              variant={advancedOpen ? "filled" : "default"}
              size={36}
              onClick={() => setAdvancedOpen((v) => !v)}
              label={t("search.advanced")}
            >
              <IconFilter size={16} />
            </HintedActionIcon>
          )}
          {semanticMode && (
            <SegmentedControl
              value={search.kind ?? "all"}
              onChange={(v) =>
                void navigate({
                  search: (prev) => ({ ...prev, kind: toEmbeddingKind(v), page: 1 }),
                })
              }
              data={SEMANTIC_KIND_OPTIONS.map((value) => ({
                value,
                label: t(`semantic.kinds.${value}`),
              }))}
            />
          )}
          {!isList && !semanticMode && (
            <SortMenu
              options={METADATA_SORT_OPTIONS.map((option) => ({
                value: option.value,
                label: t(option.labelKey),
              }))}
              sortBy={search.sort_by}
              order={search.order}
              defaultSortBy="updated_at"
              defaultOrder="desc"
              onChange={(sort_by, order) => {
                setMetaSort({ sort_by, order });
                void navigate({ search: (prev) => ({ ...prev, sort_by, order, page: 1 }) });
              }}
            />
          )}
          <BrowseDisplaySettings
            pageSizeKey={activePageSizeKey}
            cardSizeKeyOverride={search.view === "cover" ? "metaCover" : "metaGrid"}
            onPageSizeChanged={() => void navigate({ search: (prev) => ({ ...prev, page: 1 }) })}
          />
        </>
      }
      filterPanel={
        <FacetFilterControls
          opened={narrowViewport || advancedOpen}
          filters={filters}
          onSelect={appendFacet}
          libraryIds={libraryIds}
          libraries={libraries}
          onLibrariesChange={setLibraryFilter}
          hasFiles={hasFiles}
          onHasFilesChange={setHasFilesFilter}
          vr={vr}
          onVrChange={setVrFilter}
          filePhase={filePhase}
          onFilePhaseChange={setFilePhaseFilter}
        />
      }
    >
      {hasActiveFilters && (
        <Group gap="xs">
          {search.saved_query_id != null && (
            <Group gap={4} wrap="nowrap">
              <Badge variant="outline">
                {t("common:nav.savedQueries")}: #{search.saved_query_id}
              </Badge>
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
              <ActionIcon
                size="sm"
                variant="subtle"
                color="gray"
                onClick={() =>
                  void navigate({
                    search: (prev) => ({ ...prev, saved_query_id: undefined, page: 1 }),
                  })
                }
                aria-label="clear"
              >
                <IconX size={14} />
              </ActionIcon>
            </Group>
          )}
          {libraryIds.length > 0 && (
            <ActiveLibrariesChip
              ids={libraryIds}
              nameById={libraryNameById}
              onClear={() => setLibraryFilter([])}
            />
          )}
          {hasFiles !== null && (
            <ActiveHasFilesChip hasFiles={hasFiles} onClear={() => setHasFilesFilter(null)} />
          )}
          {vr !== null && <ActiveVrChip vr={vr} onClear={() => setVrFilter(null)} />}
          {filePhase.has_subtitle !== null && (
            <ActiveTriChip
              label={`${t("search.hasSubtitle")}: ${filePhase.has_subtitle ? t("search.yes") : t("search.no")}`}
              onClear={() => setFilePhaseFilter({ ...filePhase, has_subtitle: null })}
            />
          )}
          {filePhase.uncensored !== null && (
            <ActiveTriChip
              label={`${t("search.uncensored")}: ${filePhase.uncensored ? t("search.yes") : t("search.no")}`}
              onClear={() => setFilePhaseFilter({ ...filePhase, uncensored: null })}
            />
          )}
          {filePhase.mosaic != null && (
            <ActiveTriChip
              label={`${t("search.mosaic")}: ${t(`search.mosaics.${filePhase.mosaic}`)}`}
              onClear={() => setFilePhaseFilter({ ...filePhase, mosaic: null })}
            />
          )}
          {filePhase.definition != null && (
            <ActiveTriChip
              label={`${t("search.definition")}: ${filePhase.definition}`}
              onClear={() => setFilePhaseFilter({ ...filePhase, definition: null })}
            />
          )}
          {filePhase.content_type != null && (
            <ActiveTriChip
              label={`${t("search.contentType")}: ${t(`search.contentTypes.${filePhase.content_type}`)}`}
              onClear={() => setFilePhaseFilter({ ...filePhase, content_type: null })}
            />
          )}
          {active.map(({ kind, id }) => (
            <ActiveFacetChip
              key={`${kind}-${id}`}
              kind={kind}
              id={id}
              onClear={() => clearFacet(kind, id)}
            />
          ))}
        </Group>
      )}

      {semanticNotice != null ? (
        <Alert color="blue" title={t("semantic.unavailableTitle")} maw={720} mx="auto" w="100%">
          {semanticNotice}
        </Alert>
      ) : isList ? (
        <MetaTable
          key={listLimit}
          items={listQuery.data?.items ?? []}
          isLoading={listQuery.isLoading}
          total={total}
          page={search.page}
          sortBy={search.sort_by}
          order={search.order}
          onPageChange={(page) => void navigate({ search: (prev) => ({ ...prev, page }) })}
          onSort={handleSort}
        />
      ) : (
        <Stack
          gap="sm"
          style={{ flex: 1, minHeight: 0, minWidth: 0, justifyContent: "space-between" }}
        >
          <Box
            style={{
              flex: 1,
              minHeight: 0,
              minWidth: 0,
              overflowY: "auto",
              overflowX: "hidden",
            }}
          >
            <PosterGrid
              cardSize={cardSize}
              items={gridItems}
              loading={gridQuery.isLoading && gridItems.length === 0}
              emptyMessage={t("empty")}
              imageUrlOf={(item) => {
                const hit = semanticHits.get(item.id);
                if (hit?.kind === "still") return hit.resource_url;
                return search.view === "cover" ? (item.cover_urls?.[0] ?? null) : undefined;
              }}
              imageRatioOf={(item) =>
                semanticHits.get(item.id)?.kind === "still" || search.view === "cover"
                  ? 16 / 9
                  : undefined
              }
              defaultImageRatio={search.view === "cover" ? 16 / 9 : 0.7}
              imageFit={search.view === "cover" ? "fill" : "cover"}
            />
          </Box>
          <Box
            style={{
              marginTop: "auto",
              position: "sticky",
              bottom: 0,
              zIndex: 2,
              background: "var(--mantine-color-body)",
              paddingBlock: "var(--mantine-spacing-xs)",
              flexShrink: 0,
            }}
          >
            <ListPagination
              totalPages={Math.ceil(total / listLimit)}
              page={search.page}
              onChange={(page) => void navigate({ search: (prev) => ({ ...prev, page }) })}
            />
          </Box>
        </Stack>
      )}
    </BrowsePageShell>
  );
}
