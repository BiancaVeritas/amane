import { Textarea } from "@mantine/core";
import { useId } from "@mantine/hooks";
import type { ChangeEvent } from "react";

interface MultilineTemplateInputProps {
  label: string;
  description?: string;
  placeholder?: string;
  value: string;
  onChange: (event: ChangeEvent<HTMLTextAreaElement>) => void;
  /** 初始可见行数; 高度由用户拖动右下角自行调整. */
  rows?: number;
}

/**
 * 多行模板输入.
 *
 * Jinja2 模板常有条件分支, 单行框要横向滚动才看得全. 这里用固定行数 + 可拖拽的原生
 * textarea: 高度交给用户, 内容超出走纵向滚动条, 免得长模板把整页顶开.
 *
 * 不做语法高亮: 着色层与输入层靠复制度量对齐, 多行折行时两者行高与断行位置难以保持一致,
 * 一旦错位比不高亮更难用.
 */
export function MultilineTemplateInput({
  label,
  description,
  placeholder,
  value,
  onChange,
  rows = 3,
}: MultilineTemplateInputProps) {
  const uid = useId();
  return (
    <Textarea
      id={uid}
      label={label}
      description={description}
      placeholder={placeholder}
      value={value}
      onChange={onChange}
      rows={rows}
      spellCheck={false}
      autoComplete="off"
      // 模板是代码: 换行与空格都影响结果, 关掉自动纠正与大写.
      autoCorrect="off"
      autoCapitalize="off"
      styles={{
        input: {
          fontFamily: "var(--mantine-font-family-monospace)",
          fontSize: "0.8125rem",
          // 右下角可拖拽调高; 内容超出时纵向滚动, 不自动撑高.
          resize: "vertical",
          overflowY: "auto",
        },
      }}
    />
  );
}
