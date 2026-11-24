import { Autocomplete, Button, Group, Input } from "@mantine/core";
import type { AnyFieldApi } from "@tanstack/react-form";
import { useState } from "react";
import type { FieldProps } from "../schema";
import { isNullable } from "../schema";
import type { TextJSONSchema } from "../schema/types";
import { useFieldDomId } from "./dict-entry-form";
import { FieldChrome } from "./field-chrome";
import { fieldError } from "./field-error";
import { ClearableTextarea } from "@/components/common/clearable-input";
import {
  filterCandidates,
  resolveEnterChoice,
  selectedLabel,
  useMappingCandidates,
  type VendorKind,
} from "@/lib/media/mapping-candidates";

/**
 * 单值片商字段 → 片商表的哪个分类.
 *
 * 制作商 / 发行商 / 系列在这里都是**单值**, 不做拖动排序; 候选只用来省打字, 取的是片商表里的
 * 原始名. 多值的那份是 `series_names`, 走数组字段.
 */
const VENDOR_KIND_BY_FIELD: Record<string, VendorKind> = {
  studio: "studio",
  publisher: "publisher",
  series: "series",
};

/** 从字段路径里取最后一段 (`fields.studio` → `studio`). */
function leafFieldName(path: string): string {
  return path.slice(path.lastIndexOf(".") + 1);
}

/**
 * Generic text input field. Also serves as the fallback for unrecognized schema types.
 * Accepts the broad SchemaFieldProps since it may receive composed or untyped schemas.
 *
 * `x-long` renders a taller textarea (rows=8) for long-form text (notes, logs,
 * body text) that benefits from more vertical space.
 *
 * The multiline branch must use `Textarea`: a bare `Input component="textarea"`
 * inherits Input's fixed height (`--input-size` = `--input-height`), so `rows`
 * has no effect and overflowing content only shows a scrollbar.
 */
export function TextField({
  name,
  label,
  description,
  schema,
  form,
  variant,
}: FieldProps<TextJSONSchema>) {
  const id = useFieldDomId(name);
  const nullable = isNullable(schema);
  const multiline = schema["x-long"] === true;
  // schema 给了格式提示就写进输入框, 不再用「(not set)」。放在行内才不会把同一行的字段顶歪.
  const placeholder = schema.placeholder ?? (nullable ? "(not set)" : undefined);

  // 制作商 / 发行商: 唯一值, 给候选省打字, 不做排序.
  const vendorKind = VENDOR_KIND_BY_FIELD[leafFieldName(name)];
  // 没有对应分类时传占位组并关掉请求, 免得每个文本字段都白拉一份候选.
  const vendorOptions = useMappingCandidates("vendor", vendorKind !== undefined, vendorKind);

  return (
    <form.Field name={name}>
      {(field: AnyFieldApi) => (
        <FieldChrome
          variant={variant}
          htmlFor={id}
          // 带候选的字段断开标签联动: 否则点标题那一整段都会把焦点送进输入框, 看起来像点框外也有反应.
          linkLabel={vendorKind === undefined}
          label={label}
          description={description}
          error={fieldError(field)}
        >
          <Group gap="xs" wrap="nowrap" align={multiline ? "flex-start" : "center"}>
            {multiline ? (
              <ClearableTextarea
                id={id}
                rows={8}
                value={(field.state.value as string) ?? ""}
                onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) =>
                  field.handleChange(e.target.value || (nullable ? null : ""))
                }
                placeholder={placeholder}
                style={{ flex: 1 }}
              />
            ) : vendorKind !== undefined ? (
              <VendorAutocomplete
                id={id}
                nullable={nullable}
                options={vendorOptions}
                value={(field.state.value as string) ?? ""}
                onChange={(value: string) => field.handleChange(value || (nullable ? null : ""))}
                placeholder={placeholder}
                style={{ flex: 1 }}
              />
            ) : (
              <Input
                id={id}
                value={(field.state.value as string) ?? ""}
                onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
                  field.handleChange(e.target.value || (nullable ? null : ""))
                }
                placeholder={placeholder}
                style={{ flex: 1 }}
              />
            )}
            {nullable && field.state.value != null && (
              <Button
                type="button"
                variant="outline"
                size="compact-sm"
                onClick={() => field.handleChange(null)}
              >
                Clear
              </Button>
            )}
          </Group>
        </FieldChrome>
      )}
    </form.Field>
  );
}

/**
 * 片商单值输入: 值是**原名** (写库用), 框里显示 `译名 (原名)`.
 *
 * 不能把关着状态的框直接设成候选值: `Autocomplete` 的 `value` 同时是「输入内容」, 写成
 * `译名 (原名)` 后用户一编辑就会带着括号提交, 而库里按原名索引. 因此照标签的做法分成
 * 「正在输入」与「未输入」两态 —— 未输入时显示 `selectedLabel`, 一旦开始输入就换成原词.
 */
function VendorAutocomplete({
  id,
  nullable,
  options,
  value,
  onChange,
  placeholder,
  style,
}: {
  id: string;
  nullable: boolean;
  options: ReturnType<typeof useMappingCandidates>;
  value: string;
  onChange: (next: string) => void;
  placeholder?: string;
  style?: React.CSSProperties;
}) {
  const [search, setSearch] = useState("");

  return (
    <Autocomplete
      id={id}
      value={search || selectedLabel(options, value)}
      onChange={(next) => {
        setSearch(next);
        // 手输时按原词写回; 点选由 onOptionSubmit 覆盖成原名.
        onChange(next);
      }}
      // 已按原名 / 译名 / 别名筛过, 关掉 Mantine 自己的过滤.
      filter={({ options: filtered }) => filtered}
      data={filterCandidates(options, search || selectedLabel(options, value)).map(
        (option) => option.label,
      )}
      onOptionSubmit={(picked) => {
        const hit = options.find((option) => option.label === picked);
        if (hit) {
          setSearch("");
          onChange(hit.value);
        }
      }}
      onKeyDownCapture={(event) => {
        if (event.key !== "Enter") return;
        if (event.nativeEvent.isComposing) return;
        const choice = resolveEnterChoice(search, options);
        if (choice === null) return;
        event.preventDefault();
        event.stopPropagation();
        setSearch("");
        onChange(choice.value || (nullable ? "" : ""));
      }}
      onBlur={() => setSearch("")}
      placeholder={placeholder}
      style={style}
      openOnFocus={false}
      comboboxProps={{ withinPortal: true }}
    />
  );
}
