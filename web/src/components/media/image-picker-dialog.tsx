import { Box, Button, Group, Image, Modal, SimpleGrid, Stack, Text } from "@mantine/core";
import { useTranslation } from "react-i18next";
import { proxyImageUrl } from "@/lib/utils";

export interface ImagePickerDialogProps {
  opened: boolean;
  onClose: () => void;
  /** 候选图地址; 点击其中一张即视为选中. */
  images: string[];
  /** 已选中的地址 (当前封面/海报), 用于标注. */
  currentUrl?: string | null;
  title: string;
  onPick: (url: string) => void;
}

/**
 * 从候选图里挑一张.
 *
 * 剧照可能十几张, 直接采用第一张等于让用户没得选; 这里给出网格预览, 点哪张用哪张.
 */
export function ImagePickerDialog({
  opened,
  onClose,
  images,
  currentUrl,
  title,
  onPick,
}: ImagePickerDialogProps) {
  const { t } = useTranslation(["metadata", "common"]);

  return (
    <Modal opened={opened} onClose={onClose} title={title} size="xl" centered>
      <Stack gap="sm">
        {images.length === 0 ? (
          <Text size="sm" c="dimmed">
            {t("detail.imagePicker.empty")}
          </Text>
        ) : (
          <SimpleGrid cols={{ base: 2, sm: 3, md: 4 }} spacing="sm">
            {images.map((url, index) => {
              const src = proxyImageUrl(url);
              const isCurrent = currentUrl != null && url === currentUrl;
              return (
                <Box
                  key={`${url}-${String(index)}`}
                  style={{
                    border: isCurrent
                      ? "2px solid var(--mantine-color-blue-5)"
                      : "1px solid var(--mantine-color-default-border)",
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
                  <Group justify="space-between" px={6} py={4} gap={4} wrap="nowrap">
                    <Text size="xs" c="dimmed" ff="monospace">
                      {index + 1}
                    </Text>
                    {isCurrent && (
                      <Text size="xs" c="blue">
                        {t("detail.imagePicker.current")}
                      </Text>
                    )}
                  </Group>
                </Box>
              );
            })}
          </SimpleGrid>
        )}
      </Stack>
    </Modal>
  );
}
