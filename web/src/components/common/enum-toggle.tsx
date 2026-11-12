import { Button, SegmentedControl, Select } from "@mantine/core";
import { useLayoutEffect, useRef, useState } from "react";
import { useNarrowViewport } from "@/hooks/use-narrow-viewport";
import classes from "./enum-toggle.module.css";

type EnumToggleBase<T extends string | number> = {
  options: readonly T[];
  getLabel: (opt: T, index: number) => string;
  disabled?: boolean;
  /** Stretch to the parent row; default hugs label width. */
  fullWidth?: boolean;
};

type EnumToggleSingle<T extends string | number> = EnumToggleBase<T> & {
  multiple?: false;
  value: T | null | undefined;
  onChange: (next: T) => void;
};

type EnumToggleMultiple<T extends string | number> = EnumToggleBase<T> & {
  multiple: true;
  value: readonly T[] | null | undefined;
  onChange: (next: T[]) => void;
};

/**
 * Segmented enum: sliding indicator, item separators; `fullWidth` fills the row.
 * 标签在容器内放不下时改用 `Select` —— SegmentedControl 自带 `overflow: hidden`,
 * 被裁掉的段点不到. 多选分支改用按钮组, 窄屏允许换行.
 */
export function EnumToggle<T extends string | number>(
  props: EnumToggleSingle<T> | EnumToggleMultiple<T>,
) {
  const { options, getLabel, multiple, disabled, fullWidth = false } = props;
  const narrow = useNarrowViewport();

  const data = options.map((opt, i) => ({ value: String(opt), label: getLabel(opt, i) }));
  const containerRef = useRef<HTMLDivElement>(null);
  const measureRef = useRef<HTMLDivElement>(null);
  const [clipped, setClipped] = useState(false);

  // 始终观察稳定容器；可见控件切换为 Select 时，测量节点仍然保留.
  // 隐藏的 SegmentedControl 提供完整内容宽度，容器变化时重新判断是否放得下.
  useLayoutEffect(() => {
    if (multiple) return;
    const container = containerRef.current;
    const measureControl = measureRef.current;
    if (container === null || measureControl === null) return;
    const measure = () => {
      if (!container.isConnected || container.clientWidth === 0) return;
      setClipped(measureControl.scrollWidth > container.clientWidth + 1);
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(container);
    observer.observe(measureControl);
    return () => observer.disconnect();
  }, [multiple]);

  if (multiple) {
    return (
      <Button.Group
        className={classes.group}
        // `maxWidth` 限制分组宽度, 使窄屏换行发生在可视区域内.
        w={fullWidth ? "100%" : "max-content"}
        maw="100%"
        style={{ flexWrap: narrow ? "wrap" : undefined }}
      >
        {options.map((opt, i) => {
          const selected = (props.value ?? []).includes(opt);
          return (
            <Button
              key={String(opt)}
              type="button"
              size="compact-sm"
              variant={selected ? "filled" : "default"}
              disabled={disabled}
              // 窄屏按钮按内容宽度排列; 逐项 flex:1 会把同排按钮拉成等宽并重新压出省略号.
              style={fullWidth && !narrow ? { flex: 1 } : undefined}
              onClick={() => {
                const arr = [...(props.value ?? [])];
                const idx = arr.indexOf(opt);
                if (idx >= 0) arr.splice(idx, 1);
                else arr.push(opt);
                props.onChange(arr);
              }}
            >
              {getLabel(opt, i)}
            </Button>
          );
        })}
      </Button.Group>
    );
  }

  return (
    <div
      ref={containerRef}
      style={{
        position: "relative",
        display: fullWidth ? "block" : "inline-block",
        width: fullWidth ? "100%" : "max-content",
        maxWidth: "100%",
        minWidth: 0,
      }}
    >
      <div
        aria-hidden="true"
        style={{
          position: "absolute",
          top: 0,
          left: 0,
          width: "max-content",
          visibility: "hidden",
          pointerEvents: "none",
          whiteSpace: "nowrap",
        }}
      >
        <SegmentedControl
          ref={measureRef}
          size="sm"
          radius="sm"
          withItemsBorders
          disabled
          w="max-content"
          maw="none"
          styles={{ control: { flex: "0 0 auto" } }}
          value={props.value == null ? "" : String(props.value)}
          data={data}
        />
      </div>
      {clipped ? (
        <Select
          size="sm"
          disabled={disabled}
          w="100%"
          allowDeselect={false}
          comboboxProps={{ withinPortal: true }}
          value={props.value == null ? null : String(props.value)}
          onChange={(v) => {
            const next = options.find((opt) => String(opt) === v);
            if (next === undefined || props.value === next) return;
            props.onChange(next);
          }}
          data={data}
        />
      ) : (
        <SegmentedControl
          size="sm"
          radius="sm"
          withItemsBorders
          fullWidth={fullWidth}
          disabled={disabled}
          w={fullWidth ? "100%" : "max-content"}
          maw="100%"
          styles={{
            control: fullWidth ? undefined : { flex: "0 0 auto" },
          }}
          value={props.value == null ? "" : String(props.value)}
          onChange={(v) => {
            const next = options.find((opt) => String(opt) === v);
            if (next === undefined || props.value === next) return;
            props.onChange(next);
          }}
          data={data}
        />
      )}
    </div>
  );
}
