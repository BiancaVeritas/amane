import { ActionIcon, Box, Button, Image, Modal, SimpleGrid, Stack, Text } from "@mantine/core";
import { IconTrash } from "@tabler/icons-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { proxyImageUrl } from "@/lib/utils";

export interface ImageInfoItem {
  url: string;
  source?: string;
  width?: number | null;
  height?: number | null;
  /** 排序用: 封面 → 剧照 → 自定义. */
  kind?: "cover" | "still" | "custom" | "other";
}

export interface ImagePickerDialogProps {
  opened: boolean;
  onClose: () => void;
  /** 候选图; 选中不打乱相对次序. */
  images: string[];
  /** 每张的来源与分辨率, 由 ``image-info`` 端点提供; 缺失时该行留空. */
  infos?: Map<string, ImageInfoItem>;
  title: string;
  onPick: (url: string) => void;
  onRemove?: (url: string) => void;
}

/**
 * 从候选池里挑一张, 或把某张移出候选池.
 *
 * 顺序**完全照传入的 ``images``**: 后端已按候选池的顺序给出 (选过的图留在原位),
 * 这里再排一次会让「点一下、顺序就变」—— 看过哪张是一眼认出来的, 重排等于让人重新找.
 */
export function ImagePickerDialog({
  opened,
  onClose,
  images,
  infos,
  title,
  onPick,
  onRemove,
}: ImagePickerDialogProps) {
  const { t } = useTranslation(["metadata", "common"]);
  // 删除按钮平时压到 5%, 只有指向的那一格才显出来.
  const [hovered, setHovered] = useState<string | null>(null);

  const ordered = images;

  return (
    <Modal
      opened={opened}
      onClose={onClose}
      title={title}
      size="xl"
      centered
      // 选图框可能从另一个 Modal (裁切) 里打开, 需要压在上面才能点到图.
      zIndex={400}
    >
      <Stack gap="sm">
        {ordered.length === 0 ? (
          <Text size="sm" c="dimmed">
            {t("detail.imagePicker.empty")}
          </Text>
        ) : (
          <SimpleGrid cols={{ base: 2, sm: 3, md: 4 }} spacing="sm">
            {ordered.map((url, index) => {
              const src = proxyImageUrl(url);
              const info = infos?.get(url);
              const size = info?.width && info.height ? `${info.width}×${info.height}` : null;
              return (
                <Box
                  key={`${url}-${String(index)}`}
                  style={{
                    position: "relative",
                    border: "1px solid var(--mantine-color-default-border)",
                    borderRadius: "var(--mantine-radius-sm)",
                    overflow: "hidden",
                  }}
                  onMouseEnter={() => setHovered(url)}
                  onMouseLeave={() => setHovered((cur) => (cur === url ? null : cur))}
                >
                  <Button
                    variant="transparent"
                    p={0}
                    h="auto"
                    w="100%"
                    onClick={() => onPick(url)}
                    aria-label={t("detail.imagePicker.pick", { index: index + 1 })}
                  >
                    <Image src={src} alt="" h={120} fit="cover" w="100%" />
                  </Button>
                  {onRemove && (
                    <ActionIcon
                      variant="light"
                      color="red"
                      size="md"
                      radius="md"
                      // 平时 5% 几乎看不见, 指向这张图时才显出来 —— 否则整屏都是红块.
                      style={{
                        position: "absolute",
                        top: 4,
                        right: 4,
                        opacity: hovered === url ? 1 : 0.05,
                        transition: "opacity 120ms",
                      }}
                      aria-label={String(t("detail.imagePicker.remove"))}
                      onClick={(e) => {
                        // 只删图, 不触发选中.
                        e.stopPropagation();
                        onRemove(url);
                      }}
                    >
                      <IconTrash size={14} />
                    </ActionIcon>
                  )}
                  <Stack gap={0} px={6} py={4} align="center">
                    <Text size="xs" fw={500} lineClamp={1} ta="center">
                      {info?.source ?? ""}
                    </Text>
                    <Text size="xs" c="dimmed" ff="monospace" ta="center">
                      {size ?? ""}
                    </Text>
                  </Stack>
                </Box>
              );
            })}
          </SimpleGrid>
        )}
      </Stack>
    </Modal>
  );
}
