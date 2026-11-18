import { Button, Code, Group, Input, Modal, Stack, Text } from "@mantine/core";
import type * as React from "react";
import { useState } from "react";
import { useNarrowViewport } from "@/hooks/use-narrow-viewport";
import type { FieldVariant } from "../schema";
import classes from "./field-chrome.module.css";

/**
 * Layout direction of the label/description relative to the control.
 *
 * - `vertical` (default) - label + description above, control below.
 *   Used by text/numeric/enum/path fields.
 * - `horizontal` - label + description on the left, control on the right.
 *   Used by bool field (Switch is small enough to share a row).
 *   窄屏 (<sm) 纵向排列, 见 `field-chrome.module.css`.
 */
export type FieldChromeLayout = "vertical" | "horizontal";

interface FieldChromeProps {
  /** Visual mode. `bare` returns children unchanged with zero chrome. */
  variant?: FieldVariant;
  /** Label/control layout direction. */
  layout?: FieldChromeLayout;
  /** Maps the label's `htmlFor` to the inner control's `id` (for clickable labels). */
  htmlFor?: string;
  /** Resolved label text (i18n already applied by FieldRouter). */
  label: string;
  /** Optional description shown under the label. */
  description?: string;
  /** The actual input control. */
  children: React.ReactNode;
  /** 校验错误消息 (来自 field.state.meta.errors). 有值时在控件下方红字展示. */
  error?: string;
}

/**
 * Shared chrome wrapper for leaf field components.
 *
 * Centralizes the `variant === "bare"` short-circuit and the default
 * label/description/container styling so each leaf field defines only
 * its control, not the surrounding form-row layout.
 */
export function FieldChrome({
  variant = "default",
  layout = "vertical",
  htmlFor,
  label,
  description,
  children,
  error,
}: FieldChromeProps) {
  const narrow = useNarrowViewport();
  const [numberMappingHelpOpen, setNumberMappingHelpOpen] = useState(false);
  const isNumberMappingRules = label === "番号映射规则" || label === "Number Mapping Rules";

  if (variant === "bare") {
    return (
      <>
        {children}
        {error && (
          <Text size="xs" c="red" mt={4}>
            {error}
          </Text>
        )}
      </>
    );
  }

  // 对齐只能经 `align` 属性给, 样式表压不过 Mantine 写出的内联值 (除非 `!important`).
  // 断点与 field-chrome.module.css 的媒体查询同源, 都由 sm 决定.
  if (layout === "horizontal") {
    return (
      <Group
        className={classes.horizontalRow}
        justify="space-between"
        align={narrow ? "flex-start" : "center"}
        wrap="nowrap"
        gap="md"
        py="xs"
      >
        <Stack gap={2}>
          <Input.Label htmlFor={htmlFor} size="sm" fw={500}>
            {label}
          </Input.Label>
          {description && (
            <Text size="xs" c="dimmed">
              {description}
            </Text>
          )}
        </Stack>
        <Stack gap={4} align={narrow ? "flex-start" : "flex-end"} className={classes.controlRow}>
          {children}
          {error && <Input.Error size="xs">{error}</Input.Error>}
        </Stack>
      </Group>
    );
  }

  // vertical (default)
  // Input.Wrapper only injects description→control margin onto descendant
  // Input via context. Select / NumberInput nest their own wrapper (so they
  // never see it), and Button / Checkbox groups are not Input at all — those
  // fields would sit flush against the description.
  return (
    <Stack gap="xs" py="xs">
      <Stack gap={2}>
        <Group gap="xs" align="center">
          <Input.Label htmlFor={htmlFor} size="sm" fw={500}>
            {label}
          </Input.Label>
          {isNumberMappingRules && (
            <Button
              size="compact-xs"
              variant="subtle"
              onClick={() => setNumberMappingHelpOpen(true)}
            >
              规则说明
            </Button>
          )}
        </Group>
        {description && !isNumberMappingRules && (
          <Text size="xs" c="dimmed">
            {description}
          </Text>
        )}
      </Stack>
      {isNumberMappingRules && (
        <Modal
          opened={numberMappingHelpOpen}
          onClose={() => setNumberMappingHelpOpen(false)}
          title="番号映射规则说明"
          size="xl"
          centered
        >
          <div
            style={{
              display: "grid",
              gridTemplateColumns: narrow ? "minmax(0, 1fr)" : "repeat(2, minmax(0, 1fr))",
              gap: "var(--mantine-spacing-lg)",
            }}
          >
            <div>
              <Stack gap="sm">
                <Text fw={700}>左侧：匹配正则表达式</Text>
                <Text size="sm" c="dimmed">
                  左边用于判断原番号是否符合规则。建议使用 ^ 和 $
                  限定完整番号，避免只匹配到番号的一部分。
                </Text>
                <Text size="sm">
                  <Code>^</Code> 匹配字符串开头；<Code>$</Code> 匹配字符串结尾。
                </Text>
                <Text size="sm">
                  <Code>()</Code>{" "}
                  捕获一段内容，供右侧替换表达式引用；从左到右依次编号为第1组、第2组……
                </Text>
                <Text size="sm">
                  <Code>[0-9]+</Code> 匹配一个或多个数字；<Code>[A-Z]+</Code>{" "}
                  匹配一个或多个英文字母。
                </Text>
                <Text size="sm">
                  <Code>-</Code> 匹配短横线；<Code>\.</Code>{" "}
                  匹配普通点号（点号在正则中有特殊含义，需要转义）。
                </Text>
                <Text fw={600} size="sm">
                  示例
                </Text>
                <Code block>^(RED)-([0-9]+)$</Code>
                <Text size="sm" c="dimmed">
                  匹配 RED-163，捕获组1为 RED，捕获组2为 163。不会匹配 PRED-163，也不会匹配
                  RED-163-extra。
                </Text>
              </Stack>
            </div>
            <div>
              <Stack gap="sm">
                <Text fw={700}>右侧：替换表达式</Text>
                <Text size="sm" c="dimmed">
                  右边用于生成实际发送给来源网站检索的新番号，可以组合固定文字和左侧捕获的内容。
                </Text>
                <Text size="sm">
                  <Code>\g&lt;1&gt;</Code> 引用左侧第1个捕获组；<Code>\g&lt;2&gt;</Code>{" "}
                  引用第2个捕获组，以此类推。
                </Text>
                <Text size="sm">直接输入的字母、数字和符号会作为固定文本保留。</Text>
                <Text fw={600} size="sm">
                  示例1：删除短横线
                </Text>
                <Code block>^(RED)-([0-9]+)$ =&gt; RED\g&lt;2&gt;</Code>
                <Text size="sm" c="dimmed">
                  RED-163 → RED163
                </Text>
                <Text fw={600} size="sm">
                  示例2：更换系列前缀
                </Text>
                <Code block>^(RED)-([0-9]+)$ =&gt; PRED-\g&lt;2&gt;</Code>
                <Text size="sm" c="dimmed">
                  RED-163 → PRED-163
                </Text>
              </Stack>
            </div>
          </div>
          <Text size="xs" c="dimmed" mt="lg">
            规则按列表顺序执行，第一条匹配成功后停止；没有匹配时保留原番号。每行格式为：匹配正则表达式
            =&gt; 替换表达式。
          </Text>
        </Modal>
      )}
      {children}
      {error && <Input.Error size="xs">{error}</Input.Error>}
    </Stack>
  );
}
