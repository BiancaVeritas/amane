import { AspectRatio, Center, SimpleGrid, Skeleton, Stack, Text } from "@mantine/core";
import type { MetadataResponse } from "@/client/types.gen";
import { PosterCard } from "./poster-card";

interface PosterGridProps {
  cardSize?: "xs" | "sm" | "small" | "medium" | "default" | "large" | "xl" | "xxl";
  items: MetadataResponse[];
  /** 演员显示名映射; 有映射时海报只显示中文名. */
  actorDisplayNames?: Record<string, string>;
  loading?: boolean;
  emptyMessage?: string;
  /** 传入时海报显示演员出演时年龄. */
  actorBirthday?: string | null;
  /** 逐条覆盖卡片图片与宽高比 (语义检索的剧照结果). */
  imageUrlOf?: (item: MetadataResponse) => string | null | undefined;
  imageRatioOf?: (item: MetadataResponse) => number | undefined;
  /** 骨架屏及未覆盖条目的默认窗口比例. */
  defaultImageRatio?: number;
  /** 图片填充方式; fill 拉伸填满, contain 完整等比例展示. */
  imageFit?: "cover" | "contain" | "fill";
}

export function PosterGrid({
  items,
  actorDisplayNames,
  loading = false,
  emptyMessage,
  actorBirthday,
  imageUrlOf,
  imageRatioOf,
  defaultImageRatio = 0.7,
  imageFit = "cover",
  cardSize = "default",
}: PosterGridProps) {
  const minCardWidth = {
    xs: 112,
    sm: 136,
    small: 160,
    medium: 184,
    default: 208,
    large: 240,
    xl: 272,
    xxl: 304,
  }[cardSize];
  const gridStyle = {
    // 列宽用 1fr: 卡片按剩余空间均分放大, 始终铺满整行, 不留右侧空隙.
    // 下限仍是 minCardWidth, 窄屏时卡片不会被压得过小.
    gridTemplateColumns: `repeat(auto-fill, minmax(min(100%, ${minCardWidth}px), 1fr))`,
    // 同一行内各卡片等高: 标题/演员行有无与长短不同会让卡片高度参差, 叠起来就上下不齐.
    alignItems: "stretch",
  };

  if (loading) {
    return (
      <SimpleGrid cols={1} spacing="md" style={gridStyle}>
        {Array.from({ length: 12 }, (_, i) => (
          <Stack key={i} gap={2}>
            <AspectRatio ratio={defaultImageRatio}>
              <Skeleton radius="md" />
            </AspectRatio>
            <Skeleton h={12} w="70%" />
          </Stack>
        ))}
      </SimpleGrid>
    );
  }

  if (items.length === 0) {
    return (
      <Center py="xl">
        <Text c="dimmed" size="sm">
          {emptyMessage}
        </Text>
      </Center>
    );
  }

  return (
    <SimpleGrid cols={1} spacing="md" style={gridStyle}>
      {items.map((item) => (
        <PosterCard
          actorDisplayNames={actorDisplayNames}
          key={item.id}
          item={item}
          actorBirthday={actorBirthday}
          imageUrl={imageUrlOf?.(item)}
          imageRatio={imageRatioOf?.(item) ?? defaultImageRatio}
          imageFit={imageFit}
        />
      ))}
    </SimpleGrid>
  );
}
