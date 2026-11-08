import { AspectRatio, Center, SimpleGrid, Skeleton, Stack, Text } from "@mantine/core";
import type { MetadataResponse } from "@/client/types.gen";
import { PosterCard } from "./poster-card";

interface PosterGridProps {
  cardSize?: "xs" | "sm" | "small" | "medium" | "default" | "large" | "xl" | "xxl";
  items: MetadataResponse[];
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
    // 固定列宽: 用 1fr 会按剩余空间拉伸, 同屏卡片宽窄不一; 固定宽度保证每张一致,
    // 代价是右侧可能留一条不满列的空隙.
    gridTemplateColumns: `repeat(auto-fill, minmax(min(100%, ${minCardWidth}px), ${minCardWidth}px))`,
    justifyContent: "start",
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
