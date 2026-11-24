import { ActionIcon, Autocomplete, Group, ScrollArea } from "@mantine/core";
import { IconPlus } from "@tabler/icons-react";
import type { AnyFieldApi } from "@tanstack/react-form";
import { useState } from "react";
import { useNarrowViewport } from "@/hooks/use-narrow-viewport";
import { ClearableTextInput, ClearableTextarea } from "@/components/common/clearable-input";
import {
  type CandidateGroup,
  type CandidateOption,
  filterCandidates,
  resolveEnterChoice,
  selectedLabel,
  useMappingCandidates,
  type VendorKind,
} from "@/lib/media/mapping-candidates";
import type { ArrayFieldProps, JSONSchemaObject } from "../schema";
import { isOrdered } from "../schema";
import { useFieldDomId } from "./dict-entry-form";
import { DraggableChips } from "./draggable-chips";
import { FieldChrome } from "./field-chrome";

/**
 * 数组字段该用哪一组的候选补全.
 *
 * 标签 / 演员来自各自的名字表; 系列来自片商表的 series 分类. 其它数组 (如 extrafanart_urls)
 * 保持纯文本输入 —— 它们没有可复用的名字表, 硬套补全只会给出无关建议.
 */
const CANDIDATE_GROUP_BY_FIELD: Record<string, { group: CandidateGroup; vendorKind?: VendorKind }> =
  {
    tags: { group: "tag" },
    actors: { group: "actor" },
    series_names: { group: "vendor", vendorKind: "series" },
  };

/**
 * 从字段路径里取最后一段.
 *
 * ``name`` 是带前缀的完整路径 (``fields.actors`` / ``scraping.download_resources``),
 * 按它整体查表永远匹配不到, 取末段才是字段本身的键.
 */
function leafFieldName(path: string): string {
  return path.slice(path.lastIndexOf(".") + 1);
}

/** 取字段路径的前缀 (`fields.series_names` → `fields.`); 没有前缀时返回空串. */
function prefixOf(path: string): string {
  const dot = path.lastIndexOf(".");
  return dot < 0 ? "" : path.slice(0, dot + 1);
}

export function SimpleArrayField({
  name,
  label,
  description,
  schema,
  form,
  variant,
}: ArrayFieldProps<JSONSchemaObject>) {
  const id = useFieldDomId(name);
  const ordered = isOrdered(schema);
  const long = schema["x-long"] === true;
  // name 带前缀 (fields.actors), 要取末段才能对上表.
  const candidateSpec = CANDIDATE_GROUP_BY_FIELD[leafFieldName(name)];
  // 没有对应组时传一个不会被用到的占位, 并用 enabled=false 关掉请求 —— 否则会白拉一份标签.
  const candidates = useMappingCandidates(
    candidateSpec?.group ?? "tag",
    candidateSpec !== undefined,
    candidateSpec?.vendorKind,
  );
  /**
   * 多系列对应到主系列字段的路径.
   *
   * 库里 `series` 是 `series_names[0]` 的投影 (见 `repos/metadata.py` 的写入逻辑), 编辑界面把两者
   * 都放出来; 改动多系列时要顺手更新主系列, 否则拖完排序主系列会停在旧值上.
   */
  const seriesSibling = leafFieldName(name) === "series_names" ? `${prefixOf(name)}series` : null;

  return (
    <form.Field name={name}>
      {(field: AnyFieldApi) => {
        const value = Array.isArray(field.state.value) ? (field.state.value as string[]) : [];

        /**
         * 多系列的改动要同时把第一项写进主系列.
         *
         * 库里 `series` 是 `series_names[0]` 的投影, 但表单里是两个独立输入, 拖动排序后如果不
         * 跟着写, 主系列会停在旧值上 (看起来像"拖了但没生效"). 这个联动只认第一项, 用户在主系列
         * 里手填的内容会被它覆盖 —— 这正是"第一项即主系列"的定义.
         */
        const handleArrayChange = (next: string[]) => {
          field.handleChange(next);
          if (seriesSibling !== null) form.setFieldValue(seriesSibling, next[0] ?? null);
        };

        const control = ordered ? (
          <OrderedArrayBody
            value={value}
            onChange={handleArrayChange}
            long={long}
            candidates={candidates}
          />
        ) : long ? (
          // x-long, unordered: 多行文本域, 一行一个值
          <MultilineArrayInput id={id} value={value} onChange={handleArrayChange} />
        ) : (
          <CommaArrayInput id={id} value={value} onChange={handleArrayChange} />
        );

        return (
          <FieldChrome
            variant={variant}
            htmlFor={id}
            // 带候选的字段断开标签联动: 否则点标题那一整段都会把焦点送进输入框.
            linkLabel={candidates.length === 0}
            label={label}
            description={description}
          >
            {control}
          </FieldChrome>
        );
      }}
    </form.Field>
  );
}

/** 按行拆分并规范化; 空行与首尾空白不算值. */
function parseLines(text: string): string[] {
  return text
    .split("\n")
    .map((s) => s.trim())
    .filter(Boolean);
}

/** 按逗号或换行拆分并规范化; 空项与首尾空白不算值. */
function parseCommas(text: string): string[] {
  return text
    .split(/[,\n]/)
    .map((s) => s.trim())
    .filter(Boolean);
}

function sameItems(a: string[], b: string[]): boolean {
  return a.length === b.length && a.every((item, i) => item === b[i]);
}

interface ArrayInputProps {
  id: string;
  value: string[];
  onChange: (value: string[]) => void;
}

function joinCommas(items: string[]): string {
  return items.join(", ");
}

function joinLines(items: string[]): string {
  return items.join("\n");
}

interface ArrayTextDraft {
  text: string;
  /** 输入时更新文本, 解析结果与表单值不同则立即写回表单. */
  edit: (text: string) => void;
  /** 失焦时解析文本并写回表单, 同时把文本规范化为 `join` 的结果. */
  commit: () => void;
}

/**
 * 列表文本与表单值之间的编辑状态.
 *
 * 输入时写回表单值, 保存条才能反映编辑中的改动. 文本保留输入原样: 若每次输入都把解析结果
 * 回填, 尾随分隔符与空格会被立即丢弃, 无法在末尾继续追加; 因此仅在解析结果与表单值内容
 * 不一致时才重新同步文本.
 */
function useArrayTextDraft(
  value: string[],
  onChange: (value: string[]) => void,
  parse: (text: string) => string[],
  join: (items: string[]) => string,
): ArrayTextDraft {
  const [text, setText] = useState(() => join(value));
  const [synced, setSynced] = useState(value);

  // 表单值由外部改变 (重置 / 加载) 时重新同步文本. 编辑期间写回的值与解析结果内容相同, 文本因此不被覆盖.
  if (!sameItems(synced, value)) {
    setSynced(value);
    if (!sameItems(parse(text), value)) {
      setText(join(value));
    }
  }

  const edit = (next: string) => {
    setText(next);
    const items = parse(next);
    if (!sameItems(items, value)) {
      onChange(items);
    }
  };

  const commit = () => {
    const items = parse(text);
    // 解析结果未变化时, 文本仍可能残留尾随分隔符; 失焦后按解析结果规范化文本.
    setText(join(items));
    if (!sameItems(items, value)) {
      onChange(items);
    }
  };

  return { text, edit, commit };
}

function CommaArrayInput({ id, value, onChange }: ArrayInputProps) {
  const { text, edit, commit } = useArrayTextDraft(value, onChange, parseCommas, joinCommas);

  return (
    <ClearableTextInput
      id={id}
      value={text}
      onChange={(e) => edit(e.target.value)}
      onBlur={commit}
      placeholder="Comma-separated values"
    />
  );
}

/** x-long 的多行列表输入. */
function MultilineArrayInput({ id, value, onChange }: ArrayInputProps) {
  const { text, edit, commit } = useArrayTextDraft(value, onChange, parseLines, joinLines);

  return (
    <ClearableTextarea
      id={id}
      value={text}
      onChange={(e) => edit(e.target.value)}
      onBlur={commit}
      placeholder="One value per line"
      rows={8}
    />
  );
}

interface OrderedArrayBodyProps {
  value: string[];
  onChange: (v: string[]) => void;
  /** x-long: render the chip list inside a fixed-height scrollable container. */
  long?: boolean;
  /** 可选的候选 (标签 / 演员来自映射表); 为空时退化成纯文本输入. */
  candidates?: CandidateOption[];
}

/** x-ordered mode body: draggable chips + add input. Chrome handled by parent. */
function OrderedArrayBody({ value, onChange, long, candidates = [] }: OrderedArrayBodyProps) {
  const [newItem, setNewItem] = useState("");
  // 触屏设备无法执行 HTML5 拖拽, 窄屏改由上移 / 下移按钮调整顺序.
  const narrow = useNarrowViewport();

  const add = (raw: string) => {
    const choice = candidates.length > 0 ? resolveEnterChoice(raw, candidates) : null;
    // 有候选时按「原始名 / 译名」解析; 没候选 (或解析不出) 就用原词.
    const trimmed = (choice?.value ?? raw).trim();
    if (trimmed && !value.includes(trimmed)) onChange([...value, trimmed]);
    setNewItem("");
  };

  /**
   * 清空输入框, 但排在 Mantine 自己的回写之后.
   *
   * `Autocomplete` 的 `onOptionSubmit` 内部会再调一次 `handleValueChange(label)`, 把选中项的
   * label 写回输入框 —— 直接在回调里 `setNewItem("")` 会被它盖掉, 表现为"选了之后字还在".
   * 用 rAF 等这一轮更新落地再清.
   */
  const clearAfterSubmit = () => {
    requestAnimationFrame(() => setNewItem(""));
  };

  const chips = (
    <DraggableChips
      items={value}
      getKey={(item) => item}
      // 已选项显示 `译名 (原名)`: 同一个译名可能对应多个原名, 不带原名就分不清落库的是哪个.
      getLabel={(item) => selectedLabel(candidates, item)}
      onChange={onChange}
      onDelete={(item) => onChange(value.filter((v) => v !== item))}
      onMove={narrow ? onChange : undefined}
    />
  );

  return (
    <>
      {long ? (
        <ScrollArea.Autosize mah={200} type="auto">
          {chips}
        </ScrollArea.Autosize>
      ) : (
        chips
      )}
      <Group gap={6} mt={4} wrap="nowrap">
        {candidates.length > 0 ? (
          <Autocomplete
            size="xs"
            value={newItem}
            onChange={setNewItem}
            // 已按原名 / 译名 / 别名筛过, 关掉 Mantine 自己的过滤: 它会拿输入值再匹配一遍
            // 标签文本, 而别名 (如「凤羽瑾瑶」) 不在标签里, 会被它滤掉.
            filter={({ options }) => options}
            data={filterCandidates(candidates, newItem).map((option) => option.label)}
            onOptionSubmit={(label) => {
              // 选中的是 `译名 (原名)` 形式, 取回对应候选拿原名.
              const hit = candidates.find((option) => option.label === label);
              add(hit ? hit.value : label);
              clearAfterSubmit();
            }}
            // 回车在下拉打开时会被组件自己的键盘逻辑先吃掉 (它读到高亮项就 clickSelectedOption),
            // 因此在 capture 阶段拦: 先于 Mantine 的 target props 处理, 才能保证手打的新词
            // 一定能加进去. 候选里已有的词走 resolveEnterChoice 同样会被正确解析.
            onKeyDownCapture={(e) => {
              if (e.key !== "Enter") return;
              if (e.nativeEvent.isComposing) return;
              if (!newItem.trim()) return;
              e.preventDefault();
              e.stopPropagation();
              add(newItem);
            }}
            placeholder="从已有选择, 或输入新名字"
            style={{ flex: 1 }}
            // 关掉「聚焦即开」: 点标签 / 描述会让输入框获焦, 那时不该弹候选.
            // 点击输入框本身由组件自己的 onClick 展开, 不需要额外接管 —— 受控 dropdownOpened
            // 会连带把它的 openDropdown 变成空操作, 下拉就再也打不开了.
            openOnFocus={false}
            comboboxProps={{ withinPortal: true }}
          />
        ) : (
          <ClearableTextInput
            size="xs"
            value={newItem}
            onChange={(e) => setNewItem(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                add(newItem);
              }
            }}
            placeholder="Add item..."
            style={{ flex: 1 }}
          />
        )}
        <ActionIcon
          variant="default"
          size="sm"
          onClick={() => add(newItem)}
          disabled={!newItem.trim()}
          aria-label="Add item"
        >
          <IconPlus size={14} />
        </ActionIcon>
      </Group>
    </>
  );
}
