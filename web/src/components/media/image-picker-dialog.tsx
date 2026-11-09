import { Box, Button, Image, Modal, SimpleGrid, Stack, Text } from "@mantine/core";
import { useTranslation } from "react-i18next";
import { proxyImageUrl } from "@/lib/utils";

export interface ImageInfoItem {
  url: string;
  source?: string;
  width?: number | null;
  height?: number | null;
}

export interface ImagePickerDialogProps {
  opened: boolean;
  onClose: () => void;
  /** 候选图; 顺序固定, 选中不打乱. */
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
 * 每张只标注来源与分辨率, 不显示序号、不标「当前」—— 选中即应用, 界面不承担额外状态.
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
        {images.length === 0 ? (
          <Text size="sm" c="dimmed">
            {t("detail.imagePicker.empty")}
          </Text>
        ) : (
          <SimpleGrid cols={{ base: 2, sm: 3, md: 4 }} spacing="sm">
            {images.map((url, index) => {
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
                    <Button
                      variant="filled"
                      color="red"
                      size="compact-xs"
                      style={{ position: "absolute", top: 4, right: 4 }}
                      onClick={(e) => {
                        // 只删图, 不触发选中.
                        e.stopPropagation();
                        onRemove(url);
                      }}
                    >
                      {t("detail.imagePicker.remove")}
                    </Button>
                  )}
                  <Stack gap={0} px={6} py={4}>
                    <Text size="xs" fw={500} lineClamp={1}>
                      {info?.source ?? ""}
                    </Text>
                    <Text size="xs" c="dimmed" ff="monospace">
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
