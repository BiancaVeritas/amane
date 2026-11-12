import { Input } from "@mantine/core";
import { useId } from "@mantine/hooks";
import { type ChangeEvent, useCallback, useLayoutEffect, useMemo, useRef } from "react";
import { tokenizeJinja } from "./jinja-highlight";
import classes from "./multiline-template-input.module.css";

interface MultilineTemplateInputProps {
  label: string;
  description?: string;
  placeholder?: string;
  value: string;
  onChange: (event: ChangeEvent<HTMLTextAreaElement>) => void;
  /** 初始可见行数; 高度由用户拖动右下角自行调整. */
  rows?: number;
  /** 是否叠语法高亮层; 关掉就是普通多行文本框. */
  highlight?: boolean;
}

/**
 * 多行模板输入.
 *
 * 高亮用「透明 textarea + 同位着色层」. 两层的一切排版参数都写在 CSS 模块里共用
 * (见 ``.layer, .input``): 逐项写死而不是 inherit, 因为 ``<pre>`` 与 ``<textarea>``
 * 的浏览器默认字体不同, 靠继承对不齐. 改样式只能改那一段.
 *
 * 选区时**不隐藏**着色层: 文字仍由它上色, 靠 CSS 把 textarea 的选区背景压成
 * 半透明高亮, 这样既能看清选中范围, 也不会因为透明文字只剩一片蓝底.
 */
export function MultilineTemplateInput({
  label,
  description,
  placeholder,
  value,
  onChange,
  rows = 3,
  highlight = true,
}: MultilineTemplateInputProps) {
  const uid = useId();
  const areaRef = useRef<HTMLTextAreaElement>(null);
  const layerRef = useRef<HTMLPreElement>(null);

  const tokens = useMemo(
    () => (highlight && value ? tokenizeJinja(value) : []),
    [highlight, value],
  );

  /** 滚轮/拖动滚动条后把着色层跟着位移, 否则长内容会和高亮错开. */
  const syncScroll = useCallback((): void => {
    const area = areaRef.current;
    const layer = layerRef.current;
    if (area == null || layer == null) {
      return;
    }
    layer.scrollTop = area.scrollTop;
    layer.scrollLeft = area.scrollLeft;
  }, []);

  // 内容变化后滚动位置可能被浏览器复位, 每次都同步一次; 滚动本身由 onScroll 兜住.
  useLayoutEffect(syncScroll);

  return (
    <Input.Wrapper label={label} description={description} id={uid}>
      <div className={classes.shell}>
        {highlight && (
          <pre ref={layerRef} className={classes.layer} aria-hidden>
            {tokens.map((token, index) => (
              <span key={index} className={classes[token.kind]}>
                {token.text}
              </span>
            ))}
            {/* 末尾补一个换行占位: 否则内容以换行结尾时 pre 少一行, 高度和 textarea 对不上. */}
            {"\n"}
          </pre>
        )}
        <textarea
          ref={areaRef}
          id={uid}
          className={classes.input}
          value={value}
          placeholder={placeholder}
          onChange={onChange}
          onScroll={syncScroll}
          rows={rows}
          spellCheck={false}
          autoComplete="off"
          // 模板是代码: 换行与空格都影响结果, 关掉自动纠正与大写.
          autoCorrect="off"
          autoCapitalize="off"
        />
      </div>
    </Input.Wrapper>
  );
}
