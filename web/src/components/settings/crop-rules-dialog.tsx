import {
  ActionIcon,
  Button,
  Group,
  Modal,
  NumberInput,
  Select,
  SimpleGrid,
  Stack,
  Switch,
  Table,
  Text,
  TextInput,
} from "@mantine/core";
import { IconPhoto, IconPlus, IconTrash } from "@tabler/icons-react";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { listFacetsOptions } from "@/client/@tanstack/react-query.gen";
import { Z_INDEX } from "@/lib/z-index";
import { CropCalibrateDialog } from "./crop-calibrate-dialog";

/** 后端 ``PosterCropRule`` 的等价形状; 字段全部可选, 裁切窗口按封面比例 0~1. */
export interface CropRule {
  name?: string;
  series?: string | null;
  number_prefix?: string | null;
  filename_keyword?: string | null;
  studio?: string | null;
  publisher?: string | null;
  use_aspect: boolean;
  aspect: number;
  align: "left" | "center" | "right" | "custom";
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
const BLANK: CropRule = {
  use_aspect: false,
  aspect: 0.7,
  align: "right",
  left: 0.65,
  top: 0,
  width: 0.35,
  height: 1,
};

/** 列表里的「窗口」摘要: 开了宽高比时那四个数不参与计算, 不该显示出来误导人. */
function describeWindow(rule: CropRule): string {
  if (!rule.use_aspect) {
    return `left=${rule.left} top=${rule.top} w=${rule.width} h=${rule.height}`;
  }
  const base = `aspect=${rule.aspect} align=${rule.align}`;
  return rule.align === "custom"
    ? `${base} left=${rule.left} top=${rule.top} h=${rule.height}`
    : base;
}

/** facet 响应 → 下拉框选项; 顶层函数, 免得每次渲染重建. */
function facetNames(payload: { items?: { name: string }[] } | undefined): string[] {
  return (payload?.items ?? []).map((item) => item.name);
}

/**
 * 选项里补上当前值.
 *
 * facet 列表是分页的 (默认只回 50 条), 详情页预填的系列/制作商很可能不在这一页里;
 * 不补的话 Mantine 找不到对应项, 输入框就显示成空的, 看着像没导入.
 */
function withCurrent(names: string[], current: string | null | undefined): string[] {
  if (!current || names.includes(current)) {
    return names;
  }
  return [...names, current];
}

/**
 * 精准裁切规则的**列表**.
 *
 * 规则的增改在独立的 ``CropRuleEditorDialog`` 里做 —— 列表与编辑各占一层, 关掉编辑就回到
 * 列表, 不会把两者挤在同一个弹框里互相干扰.
 */
/** 规则编辑器的下拉一次取够: 系列可能有几十上百条, 默认 50 条会把要选的那条挤掉. */
const FACET_PICK_LIMIT = 1000;

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
        size="min(96vw, 76rem)"
        centered
        zIndex={Z_INDEX.ruleList}
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
                  <Table.Th style={{ width: "55%" }}>{t("cropRules.match")}</Table.Th>
                  <Table.Th>{t("cropRules.window")}</Table.Th>
                  <Table.Th w={100} />
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {rules.map((rule, at) => (
                  <Table.Tr key={`${describe(rule)}-${String(at)}`}>
                    <Table.Td>
                      {/* 条件可能很长: 单行显示, 超出用省略号, 全文放 Tooltip. */}
                      <Text size="sm" lineClamp={1} title={describe(rule)}>
                        {describe(rule) || t("cropRules.noCondition")}
                      </Text>
                    </Table.Td>
                    <Table.Td>
                      <Text size="xs" ff="monospace" c="dimmed">
                        {describeWindow(rule)}
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
export function CropRuleEditorDialog({
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
    ...listFacetsOptions({ path: { kind: "series" }, query: { limit: FACET_PICK_LIMIT } }),
    enabled: opened,
  });
  const { data: studioFacets } = useQuery({
    ...listFacetsOptions({ path: { kind: "studio" }, query: { limit: FACET_PICK_LIMIT } }),
    enabled: opened,
  });
  const { data: publisherFacets } = useQuery({
    ...listFacetsOptions({ path: { kind: "publisher" }, query: { limit: FACET_PICK_LIMIT } }),
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
        zIndex={Z_INDEX.ruleEditor}
      >
        <Stack gap="md">
          <Group grow>
            <Select
              comboboxProps={{ zIndex: Z_INDEX.dropdown }}
              label={t("fields.scraping.poster_crop_rules.$.series.label")}
              searchable
              clearable
              data={withCurrent(facetNames(seriesFacets), draft.series)}
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
              comboboxProps={{ zIndex: Z_INDEX.dropdown }}
              label={t("fields.scraping.poster_crop_rules.$.studio.label")}
              searchable
              clearable
              data={withCurrent(facetNames(studioFacets), draft.studio)}
              value={draft.studio ?? null}
              onChange={(v) => setDraft({ ...draft, studio: v })}
            />
            <Select
              comboboxProps={{ zIndex: Z_INDEX.dropdown }}
              label={t("fields.scraping.poster_crop_rules.$.publisher.label")}
              searchable
              clearable
              data={withCurrent(facetNames(publisherFacets), draft.publisher)}
              value={draft.publisher ?? null}
              onChange={(v) => setDraft({ ...draft, publisher: v })}
            />
          </Group>

          {/* 两种给窗口的方式二选一: 开了按宽高比, 关了用下面四个比例值. */}
          <Switch
            label={t("cropRules.useAspect")}
            description={t("cropRules.useAspectHint")}
            checked={draft.use_aspect}
            onChange={(e) => setDraft({ ...draft, use_aspect: e.currentTarget.checked })}
          />

          <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
            <NumberInput
              label={t("cropRules.aspect")}
              min={0.1}
              max={3}
              step={0.01}
              decimalScale={3}
              disabled={!draft.use_aspect}
              value={draft.aspect}
              onChange={(v) => setDraft({ ...draft, aspect: typeof v === "number" ? v : 0.7 })}
            />
            <Select
              comboboxProps={{ zIndex: Z_INDEX.dropdown }}
              label={t("cropRules.align")}
              disabled={!draft.use_aspect}
              data={[
                { value: "left", label: t("cropRules.alignLeft") },
                { value: "center", label: t("cropRules.alignCenter") },
                { value: "right", label: t("cropRules.alignRight") },
                { value: "custom", label: t("cropRules.alignCustom") },
              ]}
              value={draft.align}
              onChange={(v) => setDraft({ ...draft, align: (v ?? "right") as CropRule["align"] })}
            />
          </SimpleGrid>

          {/* 靠左/居中/靠右时窗口完全由宽高比推出 (上边距 0、整高), 这四个数不参与计算,
              所以置灰而不是隐藏 —— 保持字段位置稳定, 也让人看得出它们仍然存在.
              只有「自定义」才让人直接调它们. */}
          <Group grow>
            <NumberInput
              label={t("fields.scraping.poster_crop_rules.$.left.label")}
              min={0}
              max={1}
              step={0.01}
              decimalScale={3}
              disabled={draft.use_aspect && draft.align !== "custom"}
              value={draft.left}
              onChange={(v) => setDraft({ ...draft, left: typeof v === "number" ? v : 0 })}
            />
            <NumberInput
              label={t("fields.scraping.poster_crop_rules.$.top.label")}
              min={0}
              max={1}
              step={0.01}
              decimalScale={3}
              disabled={draft.use_aspect && draft.align !== "custom"}
              value={draft.top}
              onChange={(v) => setDraft({ ...draft, top: typeof v === "number" ? v : 0 })}
            />
            <NumberInput
              label={t("fields.scraping.poster_crop_rules.$.width.label")}
              min={0}
              max={1}
              step={0.01}
              decimalScale={3}
              disabled={draft.use_aspect}
              value={draft.width}
              onChange={(v) => setDraft({ ...draft, width: typeof v === "number" ? v : 0 })}
            />
            <NumberInput
              label={t("fields.scraping.poster_crop_rules.$.height.label")}
              min={0}
              max={1}
              step={0.01}
              decimalScale={3}
              disabled={draft.use_aspect && draft.align !== "custom"}
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
