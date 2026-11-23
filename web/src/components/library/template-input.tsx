import { Input } from "@mantine/core";
import { useId } from "@mantine/hooks";
import { useCallback, useLayoutEffect, useRef, type ChangeEvent, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import {
  tokenizeTemplate,
  type TemplateCatalog,
  type TemplateToken,
  type TemplateTokenKind,
} from "./template-highlight";
import classes from "./template-input.module.css";

const KIND_CLASS = {
  text: undefined,
  group: classes.group,
  brace: classes.brace,
  name: classes.name,
  punct: classes.punct,
  mapKey: classes.mapKey,
  mapValue: classes.mapValue,
  unknown: classes.error,
  error: classes.error,
} as const satisfies Record<TemplateTokenKind, string | undefined>;

/**
 * 模板里引用了目录中不存在的占位符名, 去重后按出现顺序返回.
 *
 * 只认 {@link nameKind} 判定的 `unknown` —— 语法错误 (未闭合、空名、非法映射) 不走这里,
 * 那种情况没法用"名字不存在"来解释.
 */
function uniqueNames(tokens: readonly TemplateToken[]): string[] {
  const seen = new Set<string>();
  for (const token of tokens) {
    if (token.kind === "unknown" && !seen.has(token.text.trim())) {
      seen.add(token.text.trim());
    }
  }
  return [...seen];
}

function copyInputMetrics(input: HTMLInputElement, highlight: HTMLElement): void {
  const box = input.parentElement;
  if (box == null) {
    return;
  }
  highlight.style.top = `${box.offsetTop}px`;
  highlight.style.left = `${box.offsetLeft}px`;
  highlight.style.width = `${box.offsetWidth}px`;
  highlight.style.height = `${box.offsetHeight}px`;
  const style = getComputedStyle(input);
  highlight.style.font = style.font;
  highlight.style.letterSpacing = style.letterSpacing;
  highlight.style.padding = style.padding;
  highlight.style.borderStyle = "solid";
  highlight.style.borderWidth = style.borderWidth;
  highlight.style.borderColor = "transparent";
  highlight.style.boxSizing = style.boxSizing;
  highlight.style.lineHeight = style.lineHeight;
  highlight.scrollLeft = input.scrollLeft;
}

interface TemplateInputProps {
  label?: ReactNode;
  description?: ReactNode;
  placeholder?: string;
  value: string;
  onChange: (event: ChangeEvent<HTMLInputElement>) => void;
  catalog?: TemplateCatalog;
  id?: string;
  /** 置灰并禁止编辑; 着色层一并隐藏, 否则会盖住灰底. */
  disabled?: boolean;
}

/** 路径 / STRM 模板输入: 透明字叠着色层, 外观与 TextInput 相同. */
export function TemplateInput({
  label,
  description,
  placeholder,
  value,
  onChange,
  catalog,
  id,
  disabled = false,
}: TemplateInputProps) {
  const uid = useId(id);
  const { t } = useTranslation("library");
  const inputRef = useRef<HTMLInputElement>(null);
  const highlightRef = useRef<HTMLDivElement>(null);

  const sync = useCallback((): void => {
    const input = inputRef.current;
    const highlight = highlightRef.current;
    if (input == null || highlight == null) {
      return;
    }
    copyInputMetrics(input, highlight);
  }, []);

  useLayoutEffect(() => {
    const input = inputRef.current;
    const highlight = highlightRef.current;
    if (input == null || highlight == null) {
      return;
    }
    const box = input.parentElement;
    if (box == null) {
      return;
    }
    // Accordion Collapse 从高度 0 展开. 仅在挂载或 value 变化时同步尺寸, 会读到空盒子;
    // 有值时输入框透明, 着色层 overflow 裁掉文字. 盒子尺寸变化后再同步.
    sync();
    const observer = new ResizeObserver(sync);
    observer.observe(box);
    return () => {
      observer.disconnect();
    };
    // `value` 不在 keepStyles 里, 但值变化会改文字长度进而改着色层宽度, 必须重同步;
    // 因此这条是刻意多出来的依赖, 不是漏写.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sync, value]);

  const filled = value.length > 0;
  const tokens = filled ? tokenizeTemplate(value, catalog) : [];
  /**
   * 模板里引用了目录中不存在的占位符.
   *
   * 典型场景: 删掉一个自定义占位符, 但模板里还写着 `{它}`. 着色层只是标红, 不说明原因,
   * 用户点保存后才在别处看到报错. 这里直接把名字列出来.
   */
  const unknown = uniqueNames(tokens);

  return (
    <Input.Wrapper
      label={label}
      description={description}
      id={uid}
      error={
        unknown.length > 0
          ? t("template.unknownPlaceholders", { names: unknown.join(", ") })
          : undefined
      }
    >
      <div className={classes.shell}>
        <div
          ref={highlightRef}
          className={classes.highlight}
          aria-hidden={disabled || undefined}
          hidden={disabled}
        >
          <span className={classes.highlightInner}>
            {tokens.map((token, index) => (
              <span key={index} className={KIND_CLASS[token.kind]}>
                {token.text}
              </span>
            ))}
          </span>
        </div>
        <Input
          id={uid}
          ref={inputRef}
          value={value}
          placeholder={placeholder}
          spellCheck={false}
          autoComplete="off"
          disabled={disabled}
          classNames={{
            input: filled && !disabled ? `${classes.input} ${classes.inputFilled}` : classes.input,
          }}
          onChange={(event) => {
            onChange(event);
            requestAnimationFrame(sync);
          }}
          onScroll={sync}
        />
      </div>
    </Input.Wrapper>
  );
}
