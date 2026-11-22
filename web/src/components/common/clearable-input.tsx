import {
  CloseButton,
  Textarea,
  TextInput,
  type TextareaProps,
  type TextInputProps,
} from "@mantine/core";
import { useRef } from "react";

/**
 * 带一键清空的文本输入.
 *
 * Mantine 9 的 `TextInput` / `Textarea` 没有内置清除按钮 (内部 `__clearable` 是私有属性,
 * 且必须自带 `__clearSection` 才会渲染), 所以这里统一封一层: 有值时在右侧渲染关闭按钮.
 *
 * 只在**受控**输入上显示按钮: 非受控拿不到当前值, 也就不知道该不该显示. 此时退化成原生输入,
 * 不额外渲染任何东西.
 */

/** 有内容才显示清除按钮; 空值与 `null` 都算空. */
function hasValue(value: unknown): boolean {
  if (value === null || value === undefined) return false;
  return String(value).length > 0;
}

/**
 * 清空节点并让 React 走一遍正常的 `onChange`.
 *
 * 直接给 `onChange` 塞空串不行 —— 它的契约是收事件, 调用方普遍写 `e.currentTarget.value`.
 * 直接改 `.value` 也不行 —— React 在元素上装了 value tracker, 认不出这种改动, 不会回调.
 * 用原型上的原始 setter 写入再派发 `input`, React 才认为值真的变了, 于是回调 `onChange`;
 * 效果与用户手动删空一致, 受控与非受控都成立.
 */
function clearThroughDom(element: HTMLInputElement | HTMLTextAreaElement): void {
  const prototype =
    element instanceof HTMLTextAreaElement
      ? HTMLTextAreaElement.prototype
      : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(prototype, "value")?.set?.call(element, "");
  element.dispatchEvent(new Event("input", { bubbles: true }));
}

type ClearButtonProps = {
  /** 要清空的元素; 为空时不渲染按钮. */
  target: (() => HTMLInputElement | HTMLTextAreaElement | null) | null;
  disabled?: boolean;
};

function ClearButton({ target, disabled }: ClearButtonProps) {
  if (target === null || disabled) return undefined;
  return (
    <CloseButton
      size="sm"
      variant="transparent"
      aria-label="clear"
      // 输入可能位于可点击的行内 (如筛选项), 别让点清除顺带触发外层.
      onClick={(event) => {
        event.stopPropagation();
        const element = target();
        if (element !== null) clearThroughDom(element);
      }}
      style={{ pointerEvents: "all" }}
    />
  );
}

export function ClearableTextInput({
  value,
  onChange,
  disabled,
  rightSection,
  ...rest
}: TextInputProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  // 调用方自带 rightSection 时不覆盖: 那里可能有图标或计数.
  const section =
    rightSection !== undefined ? (
      rightSection
    ) : hasValue(value) && onChange !== undefined ? (
      <ClearButton target={() => inputRef.current} disabled={disabled} />
    ) : undefined;

  return (
    <TextInput
      {...rest}
      ref={inputRef}
      value={value}
      onChange={onChange}
      disabled={disabled}
      rightSection={section}
    />
  );
}

export function ClearableTextarea({
  value,
  onChange,
  disabled,
  rightSection,
  ...rest
}: TextareaProps) {
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const section =
    rightSection !== undefined ? (
      rightSection
    ) : hasValue(value) && onChange !== undefined ? (
      <ClearButton target={() => inputRef.current} disabled={disabled} />
    ) : undefined;

  return (
    <Textarea
      {...rest}
      ref={inputRef}
      value={value}
      onChange={onChange}
      disabled={disabled}
      rightSection={section}
    />
  );
}
