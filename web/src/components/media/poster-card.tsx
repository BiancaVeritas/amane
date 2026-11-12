import { AspectRatio, Box, Card, Center, Group, Stack, Text } from "@mantine/core";
import {
  IconCalendar,
  IconGenderFemale,
  IconGenderMale,
  IconStar,
  IconUser,
} from "@tabler/icons-react";
import { Link } from "@tanstack/react-router";
import { memo, useState } from "react";
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
export const PosterCard = memo(function PosterCard({
  item,
  actorBirthday,
  imageUrl,
  imageRatio = 0.7,
  imageFit = "cover",
}: PosterCardProps) {
  const { t } = useTranslation("metadata");
  const [errored, setErrored] = useState(false);
  const selectedImageUrl =
    imageUrl !== undefined ? imageUrl : (item.poster_url ?? item.poster_urls?.[0]);
  const imageSource = selectedImageUrl ? item.image_sources?.[selectedImageUrl] : undefined;
  const posterUrl = proxyImageUrl(selectedImageUrl, imageSource);
  const showImage = posterUrl && !errored;
  const actors = (item.actors ?? []).slice(0, 2);
  const actorsLabel = item.actors?.join(" / ") ?? "";
  const releaseLabel = formatReleaseYearMonth(item.release);
  const ageThen = ageAtRelease(actorBirthday, item.release);

  return (
    <Link
      to="/meta/$metadataId"
      params={{ metadataId: String(item.id) }}
      style={{ textDecoration: "none", color: "inherit", display: "block" }}
    >
      <Card padding={0} radius="md" withBorder style={{ overflow: "hidden" }}>
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
          <Text size="sm" fw={600} ff="monospace" lineClamp={1} title={item.number}>
            {item.number}
          </Text>
          <Text size="xs" c="dimmed" lineClamp={1} title={item.title ?? undefined}>
            {item.title ?? ""}
          </Text>
          <Box
            title={actorsLabel || undefined}
            style={{
              display: "grid",
              gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1fr)",
              gridTemplateRows: "20px",
              gap: 4,
              minHeight: 20,
            }}
          >
            {Array.from({ length: 2 }, (_, index) => {
              const name = actors[index];
              if (!name) return <Box key={`empty-${index}`} />;
              const gender = item.actor_genders?.[name];
              const GenderIcon =
                gender === "female" ? IconGenderFemale : gender === "male" ? IconGenderMale : null;
              return (
                <Box
                  key={`${index}-${name}`}
                  style={{
                    minWidth: 0,
                    width: "100%",
                    display: "flex",
                    alignItems: "center",
                    gap: 4,
                    padding: "0 6px",
                    borderRadius: 999,
                    background: "var(--mantine-color-default-hover)",
                    overflow: "hidden",
                  }}
                >
                  <Box
                    style={{
                      width: 12,
                      flex: "0 0 12px",
                      display: "flex",
                      justifyContent: "flex-start",
                    }}
                  >
                    {GenderIcon ? (
                      <GenderIcon
                        size={12}
                        color={
                          gender === "female"
                            ? "var(--mantine-color-pink-5)"
                            : "var(--mantine-color-blue-5)"
                        }
                      />
                    ) : null}
                  </Box>
                  <Text size="xs" fw={500} c="grape" lineClamp={1} style={{ minWidth: 0, flex: 1 }}>
                    {name}
                  </Text>
                </Box>
              );
            })}
          </Box>
        </Stack>
      </Card>
    </Link>
  );
});
