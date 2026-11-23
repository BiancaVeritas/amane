import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { listMappingCandidatesOptions } from "@/client/@tanstack/react-query.gen";

/** 映射组里可用于补全的一类. */
export type CandidateGroup = "tag" | "actor" | "vendor";

/** 片商组内部的分类; 一个名字可能身兼多类, 各自的候选互不相同. */
export type VendorKind = "studio" | "publisher" | "series";

export interface CandidateOption {
  /** 原始名 —— **写进条目的是它**. 译名只是同一件事的提示, 不能拿来落库. */
  value: string;
  /** 展示文本: `原始名 (译名)`; 无译名时就是原始名. */
  label: string;
  /** 纯译名, 供按中文输入时匹配. */
  display: string;
}

/**
 * 分类补全的候选: 库里存的**原始名**.
 *
 * 不能直接用 `GET /facets/tag`: 那边返回的是映射后的译文 (无修正), 而条目里存的是原始名
 * (無修正 / 完全無修正). 用译文填进去等于把库里没有的名字写进去, 所以走
 * `GET /mappings/{group}/candidates`, 它成对给出 `{original, label}`.
 *
 * ``vendorKind`` 只对 `group === "vendor"` 有意义: 制作商与发行商共用一张片商表, 要按分类
 * 取各自的候选.
 */
export function useMappingCandidates(
  group: CandidateGroup,
  enabled = true,
  vendorKind?: VendorKind,
): CandidateOption[] {
  const { data } = useQuery({
    ...listMappingCandidatesOptions({
      path: { group },
      query: { limit: 500, ...(vendorKind ? { kind: vendorKind } : {}) },
    }),
    enabled,
  });
  return useMemo(
    () =>
      (data?.items ?? []).map((item) => ({
        value: item.original,
        label:
          item.label && item.label !== item.original
            ? `${item.original} (${item.label})`
            : item.original,
        display: item.label || item.original,
      })),
    [data],
  );
}

/** 按关键词过滤候选: 原名与译名都参与匹配. */
export function filterCandidates(
  options: CandidateOption[],
  term: string,
  limit = 30,
): CandidateOption[] {
  const needle = term.trim().toLocaleLowerCase();
  if (!needle) return options.slice(0, limit);
  return options
    .filter(
      (option) =>
        option.value.toLocaleLowerCase().includes(needle) ||
        option.display.toLocaleLowerCase().includes(needle),
    )
    .slice(0, limit);
}

/**
 * 已选中项的展示文本: **只显示译名**, 不带括号注释.
 *
 * 下拉里要 `原始名 (译名)` 才选得准 (同名译名的多个原名要靠原始名区分); 选中之后
 * 值已经定下, 再挂一遍原名只是噪音, 用户要看的是"这条标签叫什么".
 */
export function selectedLabel(options: CandidateOption[], value: string): string {
  const hit = options.find((option) => option.value === value);
  return hit ? hit.display : value;
}

/**
 * 回车时该把什么并入当前选择.
 *
 * 返回选中的值; `null` 表示什么都不做.
 *
 * 判定顺序:
 * 1. 与某个候选的**原始名**精确相同 → 选它 (用户明确要这一条);
 * 2. 与其**译名**精确相同 → 选该候选 (照界面上的中文输入也能命中);
 * 3. 唯一命中一个候选 (只输入了片段) → 选它;
 * 4. 否则返回原词, 作为新值加入.
 */
export function resolveEnterChoice(
  search: string,
  options: CandidateOption[],
): { value: string; created: boolean } | null {
  const term = search.trim();
  if (!term) return null;
  const folded = term.toLocaleLowerCase();
  const exact = options.find((option) => option.value === term);
  if (exact) return { value: exact.value, created: false };
  const byLabel = options.find((option) => option.display.toLocaleLowerCase() === folded);
  if (byLabel) return { value: byLabel.value, created: false };
  const matches = options.filter((option) => option.label.toLocaleLowerCase().includes(folded));
  if (matches.length === 1) return { value: matches[0].value, created: false };
  return { value: term, created: true };
}
