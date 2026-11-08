import { ActionIcon, Popover, Select, Stack, Text } from "@mantine/core";
import { IconDots } from "@tabler/icons-react";
import { useTranslation } from "react-i18next";
import type { PageSize, PageSizeKey } from "@/lib/page-size";
import { PAGE_SIZE_OPTIONS } from "@/lib/page-size";
import { useUIStore, type CardSizeKey } from "@/stores/ui";

const CARD_SIZES = ["xs", "sm", "small", "medium", "default", "large", "xl", "xxl"] as const;

export function BrowseDisplaySettings({
  pageSizeKey,
  onPageSizeChanged,
}: {
  pageSizeKey: PageSizeKey;
  onPageSizeChanged: () => void;
}) {
  const { t } = useTranslation("common");
  const pageSize = useUIStore((s) => s.pageSizes[pageSizeKey]);
  const setPageSize = useUIStore((s) => s.setPageSize);
  const cardSizeKey: CardSizeKey = pageSizeKey === "metaList" ? "meta" : "actors";
  const cardSize = useUIStore((s) => s.cardSizes[cardSizeKey]);
  const setCardSize = useUIStore((s) => s.setCardSize);
  const cardSizeLabels = [
    t("browseSettings.sizes.0"),
    t("browseSettings.sizes.1"),
    t("browseSettings.sizes.2"),
    t("browseSettings.sizes.3"),
    t("browseSettings.sizes.4"),
    t("browseSettings.sizes.5"),
    t("browseSettings.sizes.6"),
    t("browseSettings.sizes.7"),
  ] as const;

  return (
    <Popover position="bottom-end" shadow="md" withArrow>
      <Popover.Target>
        <ActionIcon variant="default" size={36} aria-label={t("browseSettings.title")}>
          <IconDots size={18} />
        </ActionIcon>
      </Popover.Target>
      <Popover.Dropdown>
        <Stack gap="sm" miw={220}>
          <Text fw={600} size="sm">
            {t("browseSettings.title")}
          </Text>
          <Select
            label={t("browseSettings.cardSize")}
            value={cardSize}
            onChange={(value) => {
              if (value != null && CARD_SIZES.some((size) => size === value)) {
                setCardSize(cardSizeKey, value as (typeof CARD_SIZES)[number]);
              }
            }}
            data={CARD_SIZES.map((value, index) => ({
              value,
              label: cardSizeLabels[index]!,
            }))}
            allowDeselect={false}
          />
          <Select
            label={t("browseSettings.pageSize")}
            value={String(pageSize)}
            onChange={(value) => {
              if (value != null) {
                setPageSize(pageSizeKey, Number(value) as PageSize);
                onPageSizeChanged();
              }
            }}
            data={PAGE_SIZE_OPTIONS[pageSizeKey].map((value) => ({
              value: String(value),
              label: String(value),
            }))}
            allowDeselect={false}
          />
        </Stack>
      </Popover.Dropdown>
    </Popover>
  );
}
