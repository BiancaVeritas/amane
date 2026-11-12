import { Alert, Button, Group, Image, Loader, Modal, Stack, Text, TextInput } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconInfoCircle, IconSearch } from "@tabler/icons-react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { previewScrape, submitTask } from "@/client/sdk.gen";
import type { ScrapePreviewItem } from "@/client/types.gen";
import { extractErrorMessage } from "@/lib/api-error";
import { proxyImageUrl } from "@/lib/utils";
import { Z_INDEX } from "@/lib/z-index";

interface ManualScrapeDialogProps {
  opened: boolean;
  onClose: () => void;
}

/**
 * 手动搜索刮削.
 *
 * 两段式: 先把各来源命中的结果列出来, 用户挑一个再落库. 直接按番号刮会在多结果时替
 * 用户做主, 猜错了还得再擦一遍; 让用户点一下省掉这轮往返.
 */
export function ManualScrapeDialog({ opened, onClose }: ManualScrapeDialogProps) {
  const { t } = useTranslation(["tasks", "common"]);
  const qc = useQueryClient();
  const [input, setInput] = useState("");
  const [results, setResults] = useState<ScrapePreviewItem[] | null>(null);

  const search = useMutation({
    mutationFn: async (raw: string) => {
      const value = raw.trim();
      if (!value) {
        throw new Error(t("manualScrape.emptyInput"));
      }
      // 粘整条地址时取末段当番号; UUID 也照原样交给后端.
      const looksLikeUrl = /^https?:\/\//i.test(value);
      const number = looksLikeUrl
        ? decodeURIComponent(value.split("/").filter(Boolean).pop() ?? "")
        : value;
      const response = await previewScrape({
        body: { number },
        throwOnError: true,
      });
      return response.data?.items ?? [];
    },
    onSuccess: (items) => {
      setResults(items);
      if (items.length === 0) {
        notifications.show({ message: t("manualScrape.noResult"), color: "orange" });
      }
    },
    onError: (err) => {
      notifications.show({
        message: extractErrorMessage(err, t("manualScrape.failed")),
        color: "red",
      });
    },
  });

  const apply = useMutation({
    mutationFn: async (item: ScrapePreviewItem) => {
      // 带上选中的站点: 后端据此把该来源排到第一位.
      return submitTask({
        body: {
          type: "scrape",
          number: item.number,
          manual: true,
          preferred_site: item.site,
        },
        throwOnError: true,
      });
    },
    onSuccess: () => {
      notifications.show({ message: t("manualScrape.submitted"), color: "blue" });
      void qc.invalidateQueries({ queryKey: ["tasks"] });
      reset();
      onClose();
    },
    onError: (err) => {
      notifications.show({
        message: extractErrorMessage(err, t("manualScrape.failed")),
        color: "red",
      });
    },
  });

  const reset = () => {
    setInput("");
    setResults(null);
  };

  return (
    <Modal
      opened={opened}
      onClose={() => {
        reset();
        onClose();
      }}
      title={t("manualScrape.title")}
      size="xl"
      zIndex={Z_INDEX.ruleEditor}
    >
      <Stack gap="sm">
        <Alert variant="light" color="blue" icon={<IconInfoCircle size={16} />}>
          <Text size="xs">{t("manualScrape.hint")}</Text>
        </Alert>
        <Group gap="xs" align="flex-end" wrap="nowrap">
          <TextInput
            label={t("manualScrape.label")}
            placeholder={t("manualScrape.placeholder")}
            value={input}
            onChange={(event) => setInput(event.currentTarget.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && input.trim() && !search.isPending) {
                search.mutate(input);
              }
            }}
            style={{ flex: 1 }}
            autoFocus
          />
          <Button
            leftSection={<IconSearch size={14} />}
            disabled={!input.trim()}
            loading={search.isPending}
            onClick={() => search.mutate(input)}
          >
            {t("manualScrape.search")}
          </Button>
        </Group>

        {search.isPending && (
          <Group justify="center" p="md">
            <Loader size="sm" />
          </Group>
        )}

        {results != null && results.length > 0 && (
          <Stack gap={6}>
            <Text size="sm" fw={600}>
              {t("manualScrape.pickOne", { count: results.length })}
            </Text>
            {results.map((item, index) => (
              <Group
                key={`${item.site}-${index}`}
                gap="sm"
                wrap="nowrap"
                p="xs"
                style={{ border: "1px solid var(--mantine-color-default-border)", borderRadius: 6 }}
              >
                {(item.thumb_url ?? item.poster_url) ? (
                  <Image
                    src={proxyImageUrl(item.thumb_url ?? item.poster_url ?? null)}
                    w={64}
                    h={40}
                    fit="cover"
                    radius={4}
                    alt=""
                  />
                ) : (
                  <div style={{ width: 64, height: 40 }} />
                )}
                <Stack gap={2} style={{ flex: 1, minWidth: 0 }}>
                  <Text size="sm" fw={500} truncate>
                    {item.title ?? item.number}
                  </Text>
                  <Text size="xs" c="dimmed" truncate>
                    {[item.site, item.number, item.release, item.studio]
                      .filter(Boolean)
                      .join(" · ")}
                  </Text>
                </Stack>
                <Button
                  size="xs"
                  variant="light"
                  loading={apply.isPending}
                  onClick={() => apply.mutate(item)}
                >
                  {t("manualScrape.useThis")}
                </Button>
              </Group>
            ))}
          </Stack>
        )}

        <Group justify="flex-end" gap="xs">
          <Button variant="default" onClick={onClose}>
            {t("common:actions.cancel")}
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
