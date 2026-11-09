import {
  ActionIcon,
  Box,
  Button,
  Group,
  Image,
  Modal,
  SimpleGrid,
  Stack,
  Text,
} from "@mantine/core";
import { IconTrash } from "@tabler/icons-react";
import { useTranslation } from "react-i18next";
import { proxyImageUrl } from "@/lib/utils";

export interface ImagePickerDialogProps {
  opened: boolean;
  onClose: () => void;
  /** 候选图地址; 顺序即编号, 不随选择变化. */
  images: string[];
  /** 正在使用的那张, 用于高亮. */
  selectedUrl?: string | null;
  title: string;
  onPick: (url: string) => void;
  onRemove?: (url: string) => void;
}

/**
 * 从候选池里挑一张, 或把某张移出候选池.
 *
 * 编号固定按数组位置: 删掉第 4 张后, 原来的第 5 张顺位成第 4 张, 不留空号.
 */
export function ImagePickerDialog({
  opened,
  onClose,
  images,
  selectedUrl,
  title,
  onPick,
  onRemove,
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
              const isSelected = selectedUrl != null && url === selectedUrl;
              return (
                <Box
                  key={`${url}-${String(index)}`}
                  style={{
                    position: "relative",
                    border: isSelected
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
                  {onRemove && (
                    <ActionIcon
                      variant="filled"
                      color="red"
                      size="sm"
                      radius="xl"
                      style={{ position: "absolute", top: 4, right: 4 }}
                      aria-label={String(t("detail.imagePicker.remove"))}
                      onClick={(e) => {
                        // 只删图, 不触发选中.
                        e.stopPropagation();
                        onRemove(url);
                      }}
                    >
                      <IconTrash size={12} />
                    </ActionIcon>
                  )}
                  <Group justify="space-between" px={6} py={4} gap={4} wrap="nowrap">
                    <Text size="xs" c="dimmed" ff="monospace">
                      {index + 1}
                    </Text>
                    {isSelected && (
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
