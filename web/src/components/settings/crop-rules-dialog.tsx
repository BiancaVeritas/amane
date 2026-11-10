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
} from "@mantine/core";
import { IconPhoto, IconPlus, IconTrash } from "@tabler/icons-react";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { listFacetsOptions } from "@/client/@tanstack/react-query.gen";
import { CropCalibrateDialog } from "./crop-calibrate-dialog";

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

/** 新规则的初始窗口: 右侧一条竖版. */
const BLANK: CropRule = { left: 0.65, top: 0, width: 0.35, height: 1 };

/** facet 响应 → 下拉框选项; 顶层函数, 免得每次渲染重建. */
function facetNames(payload: { items?: { name: string }[] } | undefined): string[] {
  return (payload?.items ?? []).map((item) => item.name);
}

/**
 * 精准裁切规则的**列表**.
 *
 * 规则的增改在独立的 ``CropRuleEditorDialog`` 里做 —— 列表与编辑各占一层, 关掉编辑就回到
 * 列表, 不会把两者挤在同一个弹框里互相干扰.
 */
export function CropRulesDialog({ opened, onClose, rules, onChange }: CropRulesDialogProps) {
  const { t } = useTranslation(["settings", "common"]);
  const [editing, setEditing] = useState<{ rule: CropRule; index: number | null } | null>(null);

  const describe = (rule: CropRule) =>
    [
      rule.series
        ? `${t("fields.scraping.poster_crop_rules.$.series.label")}=${rule.series}`
        : null,
      rule.number_prefix
        ? `${t("fields.scraping.poster_crop_rules.$.number_prefix.label")}=${rule.number_prefix}`
        : null,
      rule.filename_keyword
        ? `${t("fields.scraping.poster_crop_rules.$.filename_keyword.label")}=${rule.filename_keyword}`
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

  const remove = (at: number) => {
    onChange(rules.filter((_, i) => i !== at));
  };

  const commit = (rule: CropRule) => {
    if (editing?.index === null || editing?.index === undefined) {
      onChange([...rules, rule]);
    } else {
      const next = [...rules];
      next[editing.index] = rule;
      onChange(next);
    }
    setEditing(null);
  };

  return (
    <>
      <Modal
        opened={opened}
        onClose={onClose}
        title={t("cropRules.dialogTitle")}
        size="min(92vw, 60rem)"
        centered
      >
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
                  <Table.Th w={100} />
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
                        <Button
                          size="compact-xs"
                          variant="light"
                          onClick={() => setEditing({ rule: { ...rule }, index: at })}
                        >
                          {t("common:actions.edit")}
                        </Button>
                        <ActionIcon
                          color="red"
                          variant="subtle"
                          size="sm"
                          onClick={() => remove(at)}
                        >
                          <IconTrash size={14} />
                        </ActionIcon>
                      </Group>
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          )}

          <Group>
            <Button
              leftSection={<IconPlus size={16} />}
              variant="light"
              onClick={() => setEditing({ rule: { ...BLANK }, index: null })}
            >
              {t("cropRules.add")}
            </Button>
          </Group>
        </Stack>
      </Modal>

      <CropRuleEditorDialog
        opened={editing !== null}
        initial={editing?.rule ?? BLANK}
        onClose={() => setEditing(null)}
        onSubmit={commit}
      />
    </>
  );
}

/** 单条规则的编辑窗口: 命中条件 + 裁切窗口 + 从图片标定. */
function CropRuleEditorDialog({
  opened,
  initial,
  onClose,
  onSubmit,
}: {
  opened: boolean;
  initial: CropRule;
  onClose: () => void;
  onSubmit: (rule: CropRule) => void;
}) {
  const { t } = useTranslation(["settings", "common"]);
  const [draft, setDraft] = useState<CropRule>(initial);
  const [calibrating, setCalibrating] = useState(false);
  // 每次打开都以传入值重建, 避免上一轮残留.
  const [session, setSession] = useState<CropRule | null>(null);
  if (opened && (session === null || session !== initial)) {
    setSession(initial);
    setDraft(initial);
  }
  if (!opened && session !== null) {
    setSession(null);
  }

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

  return (
    <>
      <Modal
        opened={opened}
        onClose={onClose}
        title={t("cropRules.editorTitle")}
        size="min(92vw, 56rem)"
        centered
        zIndex={300}
      >
        <Stack gap="md">
          <Group grow>
            <Select
              label={t("fields.scraping.poster_crop_rules.$.series.label")}
              searchable
              clearable
              data={facetNames(seriesFacets)}
              value={draft.series ?? null}
              onChange={(v) => setDraft({ ...draft, series: v })}
            />
            <TextInput
              label={t("fields.scraping.poster_crop_rules.$.number_prefix.label")}
              placeholder="ABP"
              value={draft.number_prefix ?? ""}
              onChange={(e) => setDraft({ ...draft, number_prefix: e.currentTarget.value || null })}
            />
          </Group>
          <TextInput
            label={t("fields.scraping.poster_crop_rules.$.filename_keyword.label")}
            description={t("cropRules.filenameKeywordHint")}
            placeholder="BD"
            value={draft.filename_keyword ?? ""}
            onChange={(e) =>
              setDraft({ ...draft, filename_keyword: e.currentTarget.value || null })
            }
          />
          <Group grow>
            <Select
              label={t("fields.scraping.poster_crop_rules.$.studio.label")}
              searchable
              clearable
              data={facetNames(studioFacets)}
              value={draft.studio ?? null}
              onChange={(v) => setDraft({ ...draft, studio: v })}
            />
            <Select
              label={t("fields.scraping.poster_crop_rules.$.publisher.label")}
              searchable
              clearable
              data={facetNames(publisherFacets)}
              value={draft.publisher ?? null}
              onChange={(v) => setDraft({ ...draft, publisher: v })}
            />
          </Group>

          <Group grow>
            <NumberInput
              label={t("fields.scraping.poster_crop_rules.$.left.label")}
              min={0}
              max={1}
              step={0.01}
              decimalScale={3}
              value={draft.left}
              onChange={(v) => setDraft({ ...draft, left: typeof v === "number" ? v : 0 })}
            />
            <NumberInput
              label={t("fields.scraping.poster_crop_rules.$.top.label")}
              min={0}
              max={1}
              step={0.01}
              decimalScale={3}
              value={draft.top}
              onChange={(v) => setDraft({ ...draft, top: typeof v === "number" ? v : 0 })}
            />
            <NumberInput
              label={t("fields.scraping.poster_crop_rules.$.width.label")}
              min={0}
              max={1}
              step={0.01}
              decimalScale={3}
              value={draft.width}
              onChange={(v) => setDraft({ ...draft, width: typeof v === "number" ? v : 0 })}
            />
            <NumberInput
              label={t("fields.scraping.poster_crop_rules.$.height.label")}
              min={0}
              max={1}
              step={0.01}
              decimalScale={3}
              value={draft.height}
              onChange={(v) => setDraft({ ...draft, height: typeof v === "number" ? v : 0 })}
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
            <Button variant="default" onClick={onClose}>
              {t("common:actions.cancel")}
            </Button>
            <Button onClick={() => onSubmit(draft)}>{t("common:actions.confirm")}</Button>
          </Group>
        </Stack>
      </Modal>

      <CropCalibrateDialog
        opened={calibrating}
        onClose={() => setCalibrating(false)}
        onCalibrated={(win) => setDraft({ ...draft, ...win })}
      />
    </>
  );
}
