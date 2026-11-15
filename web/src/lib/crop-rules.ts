/**
 * 精准裁切规则的形状与并入逻辑.
 *
 * 从 ``crop-rules-dialog.tsx`` 拆到这里: 组件文件同时导出组件和普通函数会让 React Fast
 * Refresh 判定为「导出不兼容」, 改一次就得整页刷新.
 */

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

/**
 * 规则「条件」的指纹: 只由匹配条件构成, 不含裁切窗口.
 *
 * 条件相同的两条规则永远是同一条 —— 保留两份只会让「后写的那条生效」变成靠顺序碰运气,
 * 用户在设置里也分不清哪条在起作用. 保存时用它判断该覆盖还是新增.
 */
export function cropRuleConditionKey(rule: CropRule): string {
  // 空串与 null 都表示「这条不限制」, 归一到同一种写法再比.
  const norm = (value: string | null | undefined): string => (value ?? "").trim();
  return [
    norm(rule.series),
    norm(rule.number_prefix),
    norm(rule.filename_keyword),
    norm(rule.studio),
    norm(rule.publisher),
  ].join("\u0000");
}

/**
 * 把一条规则并入既有列表: 条件相同则原位覆盖, 否则追加.
 *
 * 原位覆盖而不是删了再追加: 规则按顺序匹配, 换位置会改变它与其它规则的先后关系.
 */
export function upsertCropRule(rules: CropRule[], rule: CropRule): CropRule[] {
  const key = cropRuleConditionKey(rule);
  const index = rules.findIndex((existing) => cropRuleConditionKey(existing) === key);
  if (index < 0) return [...rules, rule];
  const next = [...rules];
  next[index] = rule;
  return next;
}
