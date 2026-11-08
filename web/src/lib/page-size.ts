/** 各列表表面的页大小选项 - catalog 词云上限对齐 facets API (1000), 其余对齐 media/metadata/tasks (200). */
export const PAGE_SIZE_OPTIONS = {
  metaList: [10, 20, 30, 40, 50, 60, 70, 80, 90, 100],
  catalogList: [20, 50, 100, 200],
  catalogKind: [50, 100, 200, 500, 1000],
  actorsList: [10, 20, 30, 40, 50, 60, 70, 80, 90, 100],
  libraryMedia: [30, 50, 100, 500],
  feedItems: [20, 50, 100, 200],
  feedSources: [20, 50, 100, 200],
  tasks: [20, 50, 100, 200],
  savedQuery: [20, 50, 100, 200],
} as const;

export type PageSizeKey = keyof typeof PAGE_SIZE_OPTIONS;

export type PageSize = (typeof PAGE_SIZE_OPTIONS)[PageSizeKey][number];

export const DEFAULT_PAGE_SIZES = {
  metaList: 30,
  catalogList: 50,
  catalogKind: 500,
  actorsList: 30,
  libraryMedia: 30,
  feedItems: 50,
  feedSources: 50,
  tasks: 50,
  savedQuery: 50,
} as const satisfies { [K in PageSizeKey]: (typeof PAGE_SIZE_OPTIONS)[K][number] };

export function clampPageSize(key: PageSizeKey, value: number): PageSize {
  const options = PAGE_SIZE_OPTIONS[key] as readonly number[];
  if (options.includes(value)) {
    return value as PageSize;
  }
  return DEFAULT_PAGE_SIZES[key];
}
