import {
  ActionIcon,
  Button,
  Group,
  Modal,
  NumberInput,
  Select,
  Stack,
  Table,
  Text,
  TextInput,
  Tooltip,
} from "@mantine/core";
import { IconPhoto, IconPlus, IconTrash } from "@tabler/icons-react";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { listFacetsOptions } from "@/client/@tanstack/react-query.gen";
import { CropCalibrateDialog } from "./crop-calibrate-dialog";
import { useTranslation } from "react-i18next";

/** 后端 ``PosterCropRule`` 的等价形状; 字段全部可选, 裁切窗口按封面比例 0~1. */
export interface CropRule {
  name?: string;
  series?: string | null;
  number_prefix?: string | null;
  filename_keyword?: string | null;
  studio?: string | null;
  publisher?: string | null;
  left: number;
  top: number;
  width: number;
  height: number;
}

interface CropRulesDialogProps {
  opened: boolean;
  onClose: () => void;
  rules: CropRule[];
  onChange: (rules: CropRule[]) => void;
}

const BLANK: CropRule = { left: 0.65, top: 0, width: 0.35, height: 1 };

/** facet 响应 → 下拉框选项; 顶层函数, 免得每次渲染重建. */
function facetNames(payload: { items?: { name: string }[] } | undefined): string[] {
  return (payload?.items ?? []).map((item) => item.name);
}

/**
 * 精准裁切规则编辑.
 *
 * 命中条件 (系列 / 番号前缀 / 供应商 / 发行商) 填了就全部满足才算命中, 全空视为不启用 ——
 * 否则一条空规则会盖住人脸检测却毫无依据.
 */
export function CropRulesDialog({ opened, onClose, rules, onChange }: CropRulesDialogProps) {
  const { t } = useTranslation(["settings", "common"]);
  const [editing, setEditing] = useState<CropRule | null>(null);
  const [index, setIndex] = useState<number | null>(null);
  const [calibrating, setCalibrating] = useState(false);
  // 系列 / 制作商 / 发行商都从已有分类里选, 免得手打的名字与刮削结果对不上.
  const { data: seriesFacets } = useQuery({
    ...listFacetsOptions({ path: { kind: "series" } }),
    enabled: opened,
  });
  const { data: studioFacets } = useQuery({
    ...listFacetsOptions({ path: { kind: "studio" } }),
    enabled: opened,
  });
  const { data: publisherFacets } = useQuery({
    ...listFacetsOptions({ path: { kind: "publisher" } }),
    enabled: opened,
  });

  const startAdd = () => {
    setEditing({ ...BLANK });
    setIndex(null);
  };

  const startEdit = (rule: CropRule, at: number) => {
    setEditing({ ...rule });
    setIndex(at);
  };

  const commit = () => {
    if (editing === null) return;
    const next = [...rules];
    if (index === null) next.push(editing);
    else next[index] = editing;
    onChange(next);
    setEditing(null);
    setIndex(null);
  };

  const remove = (at: number) => {
    onChange(rules.filter((_, i) => i !== at));
  };

  const describe = (rule: CropRule) =>
    [
      rule.series
        ? `${t("fields.scraping.poster_crop_rules.$.series.label")}=${rule.series}`
        : null,
      rule.number_prefix
        ? `${t("fields.scraping.poster_crop_rules.$.number_prefix.label")}=${rule.number_prefix}`
        : null,
      rule.studio
        ? `${t("fields.scraping.poster_crop_rules.$.studio.label")}=${rule.studio}`
        : null,
      rule.publisher
        ? `${t("fields.scraping.poster_crop_rules.$.publisher.label")}=${rule.publisher}`
        : null,
    ]
      .filter(Boolean)
      .join(" · ");

  return (
    <Modal opened={opened} onClose={onClose} title={t("cropRules.dialogTitle")} size="xl" centered>
      <Stack gap="sm">
        <Text size="sm" c="dimmed">
          {t("cropRules.dialogHint")}
        </Text>

        {rules.length === 0 ? (
          <Text size="sm" c="dimmed">
            {t("cropRules.empty")}
          </Text>
        ) : (
          <Table striped withTableBorder>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>{t("cropRules.match")}</Table.Th>
                <Table.Th>{t("cropRules.window")}</Table.Th>
                <Table.Th w={80} />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {rules.map((rule, at) => (
                <Table.Tr key={`${describe(rule)}-${String(at)}`}>
                  <Table.Td>
                    <Text size="sm">{describe(rule) || t("cropRules.noCondition")}</Text>
                  </Table.Td>
                  <Table.Td>
                    <Text size="xs" ff="monospace" c="dimmed">
                      {`left=${rule.left} top=${rule.top} w=${rule.width} h=${rule.height}`}
                    </Text>
                  </Table.Td>
                  <Table.Td>
                    <Group gap={4} justify="flex-end" wrap="nowrap">
                      <Button size="compact-xs" variant="light" onClick={() => startEdit(rule, at)}>
                        {t("common:actions.edit")}
                      </Button>
                      <ActionIcon color="red" variant="subtle" size="sm" onClick={() => remove(at)}>
                        <IconTrash size={14} />
                      </ActionIcon>
                    </Group>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        )}

        {editing === null ? (
          <Group>
            <Button leftSection={<IconPlus size={16} />} variant="light" onClick={startAdd}>
              {t("cropRules.add")}
            </Button>
          </Group>
        ) : (
          <Stack gap="xs">
            <Group grow>
              <Select
                label={t("fields.scraping.poster_crop_rules.$.series.label")}
                searchable
                clearable
                data={facetNames(seriesFacets)}
                value={editing.series ?? null}
                onChange={(v) => setEditing({ ...editing, series: v })}
              />
              <TextInput
                label={t("fields.scraping.poster_crop_rules.$.number_prefix.label")}
                placeholder="ABP"
                value={editing.number_prefix ?? ""}
                onChange={(e) =>
                  setEditing({ ...editing, number_prefix: e.currentTarget.value || null })
                }
              />
            </Group>
            <TextInput
              label={t("fields.scraping.poster_crop_rules.$.filename_keyword.label")}
              description={t("cropRules.filenameKeywordHint")}
              placeholder="BD"
              value={editing.filename_keyword ?? ""}
              onChange={(e) =>
                setEditing({ ...editing, filename_keyword: e.currentTarget.value || null })
              }
            />
            <Group grow>
              <Select
                label={t("fields.scraping.poster_crop_rules.$.studio.label")}
                searchable
                clearable
                data={facetNames(studioFacets)}
                value={editing.studio ?? null}
                onChange={(v) => setEditing({ ...editing, studio: v })}
              />
              <Select
                label={t("fields.scraping.poster_crop_rules.$.publisher.label")}
                searchable
                clearable
                data={facetNames(publisherFacets)}
                value={editing.publisher ?? null}
                onChange={(v) => setEditing({ ...editing, publisher: v })}
              />
            </Group>
            <Group grow>
              <NumberInput
                label={t("fields.scraping.poster_crop_rules.$.left.label")}
                min={0}
                max={1}
                step={0.01}
                decimalScale={3}
                value={editing.left}
                onChange={(v) => setEditing({ ...editing, left: typeof v === "number" ? v : 0 })}
              />
              <NumberInput
                label={t("fields.scraping.poster_crop_rules.$.top.label")}
                min={0}
                max={1}
                step={0.01}
                decimalScale={3}
                value={editing.top}
                onChange={(v) => setEditing({ ...editing, top: typeof v === "number" ? v : 0 })}
              />
              <NumberInput
                label={t("fields.scraping.poster_crop_rules.$.width.label")}
                min={0}
                max={1}
                step={0.01}
                decimalScale={3}
                value={editing.width}
                onChange={(v) => setEditing({ ...editing, width: typeof v === "number" ? v : 0 })}
              />
              <NumberInput
                label={t("fields.scraping.poster_crop_rules.$.height.label")}
                min={0}
                max={1}
                step={0.01}
                decimalScale={3}
                value={editing.height}
                onChange={(v) => setEditing({ ...editing, height: typeof v === "number" ? v : 0 })}
              />
            </Group>
            <Group justify="flex-end">
              <Button
                variant="light"
                leftSection={<IconPhoto size={16} />}
                onClick={() => setCalibrating(true)}
              >
                {t("cropCalibrate.title")}
              </Button>
              <Button variant="default" onClick={() => setEditing(null)}>
                {t("common:actions.cancel")}
              </Button>
              <Tooltip label={t("cropRules.commitHint")}>
                <Button onClick={commit}>{t("common:actions.confirm")}</Button>
              </Tooltip>
            </Group>
          </Stack>
        )}
      </Stack>

      <CropCalibrateDialog
        opened={calibrating}
        onClose={() => setCalibrating(false)}
        onCalibrated={(win) => {
          if (editing !== null) setEditing({ ...editing, ...win });
        }}
      />
    </Modal>
  );
}
