import { Group, Modal, Stack, Switch, Text } from "@mantine/core";
import { useTranslation } from "react-i18next";
import { Z_INDEX } from "@/lib/z-index";

export interface CropTypesDialogProps {
  opened: boolean;
  onClose: () => void;
  /** 已放行的类型; 与后端 ``scraping.poster_crop_types`` 一致. */
  selected: string[];
  /** 可选类型, 由类型路由的键生成. */
  options: string[];
  onChange: (next: string[]) => void;
}

/**
 * 自动裁切的类型开关.
 *
 * 选项不写死: 取类型路由的键, 新增类型路由时这里自动多一项. 文案也复用类型路由的选项,
 * 免得同一个类型名在两处各维护一份.
 */
export function CropTypesDialog({
  opened,
  onClose,
  selected,
  options,
  onChange,
}: CropTypesDialogProps) {
  const { t } = useTranslation("settings");
  const chosen = new Set(selected);

  return (
    <Modal
      opened={opened}
      onClose={onClose}
      title={t("cropTypes.dialogTitle")}
      size="min(94vw, 32rem)"
      centered
      zIndex={Z_INDEX.cropTypes}
    >
      <Stack gap="sm">
        <Text size="sm" c="dimmed">
          {t("cropTypes.dialogHint")}
        </Text>
        {options.map((type) => (
          <Group key={type} justify="space-between" wrap="nowrap" gap="md">
            <Text size="sm">
              {/* 类型名复用类型路由的选项文案, 不再单独维护一份. */}
              {t(`fields.scraping.content_routes.options.${type}` as never, {
                defaultValue: type,
              })}
            </Text>
            <Switch
              checked={chosen.has(type)}
              onChange={(e) => {
                const next = new Set(chosen);
                if (e.currentTarget.checked) next.add(type);
                else next.delete(type);
                onChange([...next]);
              }}
              aria-label={type}
            />
          </Group>
        ))}
      </Stack>
    </Modal>
  );
}
