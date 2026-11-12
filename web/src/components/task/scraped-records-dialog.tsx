import {
  ActionIcon,
  Alert,
  Box,
  Button,
  Group,
  Loader,
  Modal,
  ScrollArea,
  Stack,
  Text,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconCornerDownRight, IconDatabase, IconInfoCircle, IconTrash } from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { clearScrapedFilenames, listScrapedFilenames } from "@/client/sdk.gen";
import { extractErrorMessage } from "@/lib/api-error";
import { Z_INDEX } from "@/lib/z-index";
import classes from "./scraped-records-dialog.module.css";

interface ScrapedRecordsDialogProps {
  opened: boolean;
  onClose: () => void;
}

const RECORDS_KEY = ["scraped-filenames"] as const;

/**
 * 文件记录: 刮过的文件名登记表.
 *
 * 同名文件在这里有记录就不再进入刮削; 清掉记录只是下次不再拦截, 与正在跑的任务无关.
 */
export function ScrapedRecordsDialog({ opened, onClose }: ScrapedRecordsDialogProps) {
  const { t } = useTranslation(["tasks", "common"]);
  const qc = useQueryClient();

  const { data, isLoading } = useQuery({
    queryKey: RECORDS_KEY,
    enabled: opened,
    queryFn: async () => {
      const { data: payload } = await listScrapedFilenames({ query: { limit: 1000 } });
      return payload ?? [];
    },
  });

  const removeOne = useMutation({
    mutationFn: async (filename: string) => {
      const { data: payload } = await clearScrapedFilenames({ query: { filename } });
      return payload?.cleared ?? 0;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: RECORDS_KEY });
    },
    onError: (err) =>
      notifications.show({
        message: extractErrorMessage(err, t("common:toast.operationFailed")),
        color: "red",
      }),
  });

  const clear = useMutation({
    mutationFn: async () => {
      const { data: payload } = await clearScrapedFilenames();
      return payload?.cleared ?? 0;
    },
    onSuccess: (cleared) => {
      notifications.show({
        message: t("scrapedRecords.cleared", { count: cleared }),
        color: "blue",
      });
      void qc.invalidateQueries({ queryKey: RECORDS_KEY });
    },
    onError: (err) =>
      notifications.show({
        message: extractErrorMessage(err, t("common:toast.operationFailed")),
        color: "red",
      }),
  });

  const items = data ?? [];

  return (
    <Modal
      opened={opened}
      onClose={onClose}
      title={t("scrapedRecords.title")}
      size="lg"
      zIndex={Z_INDEX.ruleEditor}
    >
      <Stack gap="sm">
        <Alert variant="light" color="blue" icon={<IconInfoCircle size={16} />}>
          <Text size="xs">{t("scrapedRecords.hint")}</Text>
        </Alert>

        <Group justify="space-between">
          <Text size="sm" c="dimmed">
            {t("scrapedRecords.count", { count: items.length })}
          </Text>
          <Button
            size="xs"
            variant="light"
            color="red"
            leftSection={<IconTrash size={14} />}
            loading={clear.isPending}
            disabled={items.length === 0}
            onClick={() => clear.mutate()}
          >
            {t("scrapedRecords.clear")}
          </Button>
        </Group>

        {isLoading ? (
          <Group justify="center" py="md">
            <Loader size="sm" />
          </Group>
        ) : items.length === 0 ? (
          <Text size="sm" c="dimmed" ta="center" py="md">
            {t("scrapedRecords.empty")}
          </Text>
        ) : (
          // 用 list 而不是 Stack: 每行做成独立可悬停的条目, 右侧删除按钮固定宽度,
          // 不会和 ScrollArea 的滚动条挤在一起.
          <ScrollArea h={340} type="auto" offsetScrollbars scrollbarSize={8}>
            <Box className={classes.list}>
              {items.map((item) => (
                <Group key={item.filename} className={classes.row} wrap="nowrap" gap="xs">
                  <IconDatabase size={13} color="var(--mantine-color-dimmed)" />
                  <Box className={classes.names}>
                    <Text size="xs" ff="monospace" className={classes.name}>
                      {item.source_path || item.filename}
                    </Text>
                    <Group gap={4} wrap="nowrap" className={classes.name}>
                      <IconCornerDownRight size={11} color="var(--mantine-color-dimmed)" />
                      {item.target_path ? (
                        <Text size="xs" ff="monospace" c="teal" className={classes.name}>
                          {item.target_path}
                        </Text>
                      ) : (
                        <Text size="xs" c="dimmed">
                          {t("scrapedRecords.notOrganized")}
                        </Text>
                      )}
                    </Group>
                  </Box>
                  <ActionIcon
                    size="sm"
                    variant="subtle"
                    color="red"
                    aria-label={t("scrapedRecords.removeOne")}
                    loading={removeOne.isPending && removeOne.variables === item.filename}
                    onClick={() => removeOne.mutate(item.filename)}
                  >
                    <IconTrash size={13} />
                  </ActionIcon>
                </Group>
              ))}
            </Box>
          </ScrollArea>
        )}
      </Stack>
    </Modal>
  );
}
