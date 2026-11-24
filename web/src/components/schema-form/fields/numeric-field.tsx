import { NumberInput, TextInput } from "@mantine/core";
import type { AnyFieldApi } from "@tanstack/react-form";
import { useState } from "react";
import { formatRuntimeInput, parseRuntimeInput, runtimeMinutes } from "@/lib/runtime-input";
import type { FieldProps, NumericJSONSchema } from "../schema";
import { getEffectiveType, isNullable } from "../schema";
import { useFieldDomId } from "./dict-entry-form";
import { FieldChrome } from "./field-chrome";
import { fieldError } from "./field-error";

export function NumericField({
  name,
  label,
  description,
  schema,
  form,
  variant,
}: FieldProps<NumericJSONSchema>) {
  const id = useFieldDomId(name);
  const nullable = isNullable(schema);
  const effectiveSchema = getEffectiveType(schema);
  const isInteger = typeof effectiveSchema === "object" && effectiveSchema.type === "integer";

  return (
    <form.Field name={name}>
      {(field: AnyFieldApi) => (
        <FieldChrome
          variant={variant}
          htmlFor={id}
          label={label}
          description={description}
          error={fieldError(field)}
        >
          <NumberInput
            id={id}
            allowDecimal={!isInteger}
            value={field.state.value == null ? "" : (field.state.value as number)}
            onChange={(value) => {
              if (value === "") {
                field.handleChange(nullable ? null : 0);
                return;
              }
              field.handleChange(
                typeof value === "string"
                  ? isInteger
                    ? Number.parseInt(value, 10)
                    : Number.parseFloat(value)
                  : value,
              );
            }}
          />
        </FieldChrome>
      )}
    </form.Field>
  );
}

/**
 * 时长字段: 存的是分钟, 但允许按时钟串填写.
 *
 * `runtime` 与 `runtime_seconds` 是一对数: 秒是精确值, 分钟由它向上取整.
 * 直接改分钟会丢掉秒, 所以这里同时写两个字段 —— 用户填 `1:40:45` 时
 * 分钟自动是 101. 只填 `456` 这类纯数字仍按分钟理解, 与旧习惯一致.
 */
export function RuntimeField({
  name,
  label,
  description,
  form,
  variant,
}: FieldProps<NumericJSONSchema>) {
  const id = useFieldDomId(name);
  const secondsName = `${name}_seconds`;
  // 输入框里保留用户敲的原文: 受控成数字会让 `1:40:` 这种中间态被吃掉.
  const [draft, setDraft] = useState<string | null>(null);

  return (
    <form.Field name={name}>
      {(field: AnyFieldApi) => (
        <form.Field name={secondsName}>
          {(secondsField: AnyFieldApi) => {
            const minutes = field.state.value == null ? null : (field.state.value as number);
            const seconds =
              secondsField.state.value == null ? null : (secondsField.state.value as number);
            const shown = draft ?? formatRuntimeInput(minutes, seconds);

            const commit = (raw: string) => {
              setDraft(raw);
              const parsed = parseRuntimeInput(raw);
              // 认不出就留在本地, 保存时空值会被拦下; 认得出才写回表单.
              if (!parsed) return;
              field.handleChange(runtimeMinutes(parsed.seconds));
              secondsField.handleChange(parsed.seconds);
            };

            return (
              <FieldChrome
                variant={variant}
                htmlFor={id}
                label={label}
                // 说明放输入框里作 placeholder: 摆在标签下方会把这一格顶高,
                // 与同一行其它字段的输入框对不齐.
                error={fieldError(field)}
              >
                <TextInput
                  id={id}
                  value={shown}
                  onChange={(event) => commit(event.currentTarget.value)}
                  placeholder="分钟或 时:分:秒，如 120 / 1:40:45"
                  title={description}
                  inputMode="text"
                />
              </FieldChrome>
            );
          }}
        </form.Field>
      )}
    </form.Field>
  );
}
