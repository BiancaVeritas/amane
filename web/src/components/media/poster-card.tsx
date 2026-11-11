import { AspectRatio, Box, Card, Center, Group, Stack, Text } from "@mantine/core";
import { IconCalendar, IconStar, IconUser } from "@tabler/icons-react";
import { Link } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { MetadataResponse } from "@/client/types.gen";
import { FilePhaseOverlay } from "@/components/media/file-phase-badges";
import { OverlayChip, OverlayChipLabel } from "@/components/media/overlay-chip";
import { ProxyImage } from "@/components/media/proxy-image";
import { VrOverlayChip } from "@/components/media/vr-badge";
import { ageAtRelease } from "@/lib/format-birthday";
import { formatReleaseYearMonth } from "@/lib/format-release";
import { proxyImageUrl } from "@/lib/utils";

interface PosterCardProps {
  item: MetadataResponse;
  /** 演员显示名映射; 有映射的演员在海报上只显示中文名, 不附原始名. */
  actorDisplayNames?: Record<string, string>;
  /** 传入时按影片发行日标注演员当时年龄 (演员详情出演墙). */
  actorBirthday?: string | null;
  /** 覆盖卡片图片 (语义检索命中剧照); 未给则使用条目的海报. */
  imageUrl?: string | null;
  /** 图片宽高比; 剧照/封面按 16:9 显示, 默认为海报竖版宽高比. */
  imageRatio?: number;
  /** 图片显示适配方式; fill 会拉伸图片填满展示框, contain 会完整等比例显示. */
  imageFit?: "cover" | "contain" | "fill";
}

/** 加载失败退化为番号占位块. */
export function PosterCard({
  item,
  actorDisplayNames,
  actorBirthday,
  imageUrl,
  imageRatio = 0.7,
  imageFit = "cover",
}: PosterCardProps) {
  const { t } = useTranslation("metadata");
  const [errored, setErrored] = useState(false);
  const posterUrl = proxyImageUrl(
    imageUrl !== undefined ? imageUrl : (item.poster_url ?? item.poster_urls?.[0]),
  );
  const showImage = posterUrl && !errored;
  const actors = item.actors ?? [];
  // 顿号作并列分隔, 中日文里本就常用, 比间隔号窄且不突兀.
  // 海报空间小, 有映射时只放中文名; 原始名在详情页看得到.
  const shownActors = actors.map((name) => actorDisplayNames?.[name] ?? name);
  const actorsLabel = shownActors.length > 0 ? shownActors.join("、") : null;
  const releaseLabel = formatReleaseYearMonth(item.release);
  const ageThen = ageAtRelease(actorBirthday, item.release);

  return (
    <Link
      to="/meta/$metadataId"
      params={{ metadataId: String(item.id) }}
      style={{ textDecoration: "none", color: "inherit", display: "block", height: "100%" }}
    >
      <Card padding={0} radius="md" withBorder style={{ overflow: "hidden", height: "100%" }}>
        <Box pos="relative">
          <AspectRatio ratio={imageRatio}>
            {showImage ? (
              <ProxyImage
                src={posterUrl}
                alt={item.number}
                loading="lazy"
                referrerPolicy="no-referrer"
                style={{
                  display: "block",
                  width: "100%",
                  height: "100%",
                  objectFit: imageFit,
                  background:
                    imageFit === "contain" ? "var(--mantine-color-default-hover)" : undefined,
                }}
                onError={() => setErrored(true)}
              />
            ) : (
              <Center bg="var(--mantine-color-default-hover)" h="100%">
                <Text size="sm" ff="monospace" c="dimmed" ta="center" px="xs">
                  {item.number}
                </Text>
              </Center>
            )}
          </AspectRatio>
          <FilePhaseOverlay
            phase={item.file_phase}
            bottomExtra={
              ageThen != null ? (
                <OverlayChip>
                  <IconUser size={12} color="var(--mantine-color-grape-3)" />
                  <OverlayChipLabel>{t("actors.ageShort", { age: ageThen })}</OverlayChipLabel>
                </OverlayChip>
              ) : undefined
            }
          />
          {/* 与评分同角成组: 左上角被相位水印 (马赛克标记) 占用, 单独放会与它重叠. */}
          {(item.vr || item.score != null) && (
            <Group
              pos="absolute"
              top={6}
              right={6}
              gap={4}
              wrap="nowrap"
              align="flex-end"
              style={{ flexDirection: "column" }}
            >
              {item.vr && <VrOverlayChip />}
              {item.score != null && (
                <OverlayChip>
                  <IconStar
                    size={11}
                    color="var(--mantine-color-yellow-4)"
                    fill="var(--mantine-color-yellow-4)"
                  />
                  <OverlayChipLabel>{item.score.toFixed(1)}</OverlayChipLabel>
                </OverlayChip>
              )}
            </Group>
          )}
          {releaseLabel && (
            <Box pos="absolute" bottom={6} right={6}>
              <OverlayChip>
                <IconCalendar size={12} color="var(--mantine-color-cyan-3)" />
                <OverlayChipLabel ff="monospace" lts={0.3}>
                  {releaseLabel}
                </OverlayChipLabel>
              </OverlayChip>
            </Box>
          )}
        </Box>
        <Stack gap={4} p="xs">
          <Text size="sm" fw={600} ff="monospace" lineClamp={1}>
            {item.number}
          </Text>
          {/* 标题恒占两行高: 用 rem 而非 em —— em 相对自身字号, 换算后仍随字号变化.
              固定高度 + 显式行高, 使 1 行与 2 行的标题占同样空间. */}
          <Text
            size="xs"
            c="dimmed"
            lineClamp={2}
            title={item.title ?? undefined}
            style={{ height: "2.4rem", lineHeight: "1.2rem" }}
          >
            {item.title ?? ""}
          </Text>
          {/* 演员行恒占 20px: 没有演员的条目少这一行, 整行卡片底部就不齐. */}
          <Group gap={5} wrap="nowrap" align="center" title={actorsLabel ?? undefined} h={20}>
            {actorsLabel && (
              <>
                <Box
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    flexShrink: 0,
                    width: 18,
                    height: 18,
                    borderRadius: "var(--mantine-radius-sm)",
                    background: "var(--mantine-color-grape-filled)",
                    color: "var(--mantine-color-white)",
                  }}
                >
                  <IconUser size={11} stroke={2.2} />
                </Box>
                <Text
                  size="xs"
                  fw={600}
                  lineClamp={1}
                  style={{
                    minWidth: 0,
                    flex: 1,
                    color: "light-dark(var(--mantine-color-grape-7), var(--mantine-color-grape-3))",
                  }}
                >
                  {actorsLabel}
                </Text>
              </>
            )}
          </Group>
        </Stack>
      </Card>
    </Link>
  );
}
