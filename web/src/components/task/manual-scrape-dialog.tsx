import { Alert, Button, Group, Image, Loader, Modal, Stack, Text, TextInput } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconInfoCircle, IconSearch } from "@tabler/icons-react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { previewScrape, submitTask } from "@/client/sdk.gen";
import type { ContentType, ScrapePreviewItem } from "@/client/types.gen";
import { extractErrorMessage } from "@/lib/api-error";
import { proxyImageUrl } from "@/lib/utils";
import { Z_INDEX } from "@/lib/z-index";

interface ManualScrapeDialogProps {
  opened: boolean;
  onClose: () => void;
  /**
   * 绑定的条目. 给了就把范围钉在这一条上, 番号与类型都按它走, 不再靠输入推断.
   * 片库详情页从这里进来.
   */
  metadata?: { id: number; number: string; content_type: ContentType } | null;
  /**
   * 从任务详情页进来时, 沿用那个任务手上的文件.
   *
   * 用户点手动搜索的场景几乎都是「原来那条任务刮失败了」, 失败任务里就记着这部片的文件.
   * 不传下去的话, 新任务既不知道该落盘哪个文件, 也派不出整理 —— 文件就永远停在未整理.
   */
  mediaFileId?: number | null;
  /**
   * 落库方式:
   * - `force` (默认): 按选中的来源重置字段与来源顺序, 选中站点排第一.
   * - `append`: 只把选中的来源链接追到 `source_urls` 末尾, 不动既有链接与字段.
   */
  mode?: "force" | "append";
}

/**
 * 手动搜索刮削.
 *
 * 两段式: 先把各来源命中的结果列出来, 用户挑一个再落库. 直接按番号刮会在多结果时替
 * 用户做主, 猜错了还得再擦一遍; 让用户点一下省掉这轮往返.
 */
export function ManualScrapeDialog({
  opened,
  onClose,
  metadata,
  mediaFileId,
  mode = "force",
}: ManualScrapeDialogProps) {
  const { t } = useTranslation(["tasks", "common"]);
  const qc = useQueryClient();
  /**
   * 输入框的初值.
   *
   * 「添加来源链接」不自动填: 那一步是手动补一条别处的链接, 预填当前番号只会让人误用.
   * 强制手动刮削把范围钉在当前条目上, 预填番号省一次输入.
   */
  const defaultInput = mode === "append" ? "" : (metadata?.number ?? "");
  const [input, setInput] = useState(defaultInput);
  const [results, setResults] = useState<ScrapePreviewItem[] | null>(null);

  // 弹窗常驻渲染树, 切模式不会重建组件: 只靠 useState 初值会让「添加来源链接」沿用
  // 上一次强制刮削填好的番号. 打开时按当前模式重置一次输入.
  const prevOpened = useRef(false);
  useEffect(() => {
    if (opened && !prevOpened.current) {
      setInput(defaultInput);
      setResults(null);
    }
    prevOpened.current = opened;
  }, [opened, defaultInput]);

  // 绑定条目时, 范围钉在这一条上: 番号与类型都按它走, 不让用户改.
  // 「添加来源链接」例外: 它要手动填别处的番号, 钉死就填不进去了.
  const boundNumber = mode === "append" ? null : (metadata?.number ?? null);
  const boundType = mode === "append" ? null : (metadata?.content_type ?? null);

  // 添加来源链接只要链接: 番号 / UUID 在这个口径下没有意义.
  const looksLikeUrl = /^https?:\/\//i.test(input.trim());
  const appendNeedsUrl = mode === "append" && input.trim().length > 0 && !looksLikeUrl;

  const search = useMutation({
    mutationFn: async (raw: string) => {
      const value = raw.trim();
      if (!value) {
        throw new Error(t("manualScrape.emptyInput"));
      }
      // 添加来源链接必须是完整地址: 它只抓这一个页面, 没有地址就无从抓起.
      if (mode === "append" && !/^https?:\/\//i.test(value)) {
        throw new Error(t("manualScrape.appendNeedsUrl"));
      }
      // 原样交给后端: 地址由后端打开页面读番号. 前端截末段当番号是错的 ——
      // javdb 的 /v/dgVq9 里根本没有番号, 那样哪个来源都搜不到.
      const response = await previewScrape({
        // 只有「添加来源链接」才只解析这一个地址 (它要的是补一条来源, 多问一圈只会多出
        // 并列结果). 手动刮削贴地址与贴番号同义: 地址先读出番号, 再拿番号问各来源.
        body: {
          number: value,
          content_type: boundType,
          only_that_url: mode === "append",
        },
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
      // 把这次搜索命中的站点全部带上, 选中的排第一.
      //
      // 搜索阶段已经按番号问过全部路由了, 刮削阶段再回类型路由问一遍纯属重复; 而且番号
      // 判断出的类型对选中的站未必成立 (javbus 属有码, 而东京热的 n 系列被判成无码).
      // 用户的搜索结果就是这次的范围: 命中几个站就抓几个.
      const hits = Array.from(new Set([item.site, ...(results ?? []).map((hit) => hit.site)]));
      return submitTask({
        body: {
          type: "scrape",
          // 绑定条目时把范围钉在这条记录上, 否则按选中的番号走.
          number: boundNumber ?? item.number,
          metadata_id: metadata?.id ?? null,
          // 从失败任务进来时沿用它的文件: 刮削成功后要接着把这个文件整理落盘, 缺了它就派不出
          // 整理任务, 文件永远停在未整理.
          media_id: mediaFileId ?? null,
          content_type: boundType,
          manual: true,
          preferred_site: item.site,
          search_sites: hits,
          append_sources: mode === "append",
          // 钉住选中那一条的页面地址: 它可能属于别的类型路由, 按路由去找根本找不到这一家.
          // 给了地址就照它抓 —— 与用户手贴链接同一个口径.
          // 「添加来源链接」用输入框里的地址, 手动刮削用选中项自己的地址.
          source_url: mode === "append" ? input.trim() : item.source_url,
          // 手动刮削要覆盖元数据: 留空集才会重新抓取, 否则命中 raw 快照等于什么都没改.
          use_cache: [],
        },
        throwOnError: true,
      });
    },
    onSuccess: () => {
      notifications.show({ message: t("manualScrape.submitted"), color: "blue" });
      void qc.invalidateQueries({ queryKey: ["tasks"] });
      // 结果落到条目上, 详情页与列表都要重取, 否则看着像没生效.
      void qc.invalidateQueries({ queryKey: ["metadata"] });
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
    // 绑定了条目就回到它的番号, 而不是清空: 清空后再打开会一直显示空输入框,
    // 让人以为上次那条没被记住.
    setInput(defaultInput);
    setResults(null);
  };

  return (
    <Modal
      opened={opened}
      onClose={() => {
        reset();
        onClose();
      }}
      title={mode === "append" ? t("manualScrape.appendTitle") : t("manualScrape.title")}
      size="xl"
      zIndex={Z_INDEX.ruleEditor}
    >
      <Stack gap="sm">
        <Alert variant="light" color="blue" icon={<IconInfoCircle size={16} />}>
          <Text size="xs">
            {mode === "append" ? t("manualScrape.appendHint") : t("manualScrape.hint")}
          </Text>
        </Alert>
        <Group gap="xs" align="flex-end" wrap="nowrap">
          <TextInput
            label={mode === "append" ? t("manualScrape.appendLabel") : t("manualScrape.label")}
            placeholder={
              mode === "append"
                ? t("manualScrape.appendPlaceholder")
                : t("manualScrape.placeholder")
            }
            value={input}
            onChange={(event) => setInput(event.currentTarget.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && input.trim() && !search.isPending) {
                search.mutate(input);
              }
            }}
            style={{ flex: 1 }}
            autoFocus
            error={appendNeedsUrl ? t("manualScrape.appendNeedsUrl") : undefined}
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
