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
  /**
   * 展示文本 `译名 (原名)`: 界面到处用中文, 先给译名才好认; 括号里的原名让人知道落库的
   * 到底是哪个词. 无译名时就是原名.
   */
  label: string;
  /** 纯译名, 供按中文输入时匹配. */
  display: string;
  /** 该演员的别名 (仅演员组有值). 输入别名要折回主名 —— 库里按主名索引. */
  aliases: string[];
}

/**
 * 模糊命中 (片段匹配) 自动采用所需的最短长度.
 *
 * 一个字太容易误伤: 用户想新建一个只有单字的演员名时, 回车会被吞成某个候选. 两个字起
 * 才有足够的辨识度; 单字仍然出现在下拉里, 由用户自己点.
 */
export const MIN_FUZZY_LENGTH = 2;

/** 按 `译名 (原名)` 拼展示文本; 没有译名或译名与原名相同时只留原名. */
function candidateLabel(original: string, translated: string): string {
  if (!translated || translated === original) return original;
  return `${translated} (${original})`;
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
        label: candidateLabel(item.original, item.label),
        display: item.label || item.original,
        aliases: item.aliases ?? [],
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
        option.display.toLocaleLowerCase().includes(needle) ||
        option.aliases.some((alias) => alias.toLocaleLowerCase().includes(needle)),
    )
    .slice(0, limit);
}

/**
 * 已选项的展示文本, 与下拉一致: `译名 (原名)`.
 *
 * 选中后仍要能看出落库的是哪个原名 —— 同一个译名常由多个原名映射而来, 只显示译名就分不清.
 */
export function selectedLabel(options: CandidateOption[], value: string): string {
  const hit = options.find((option) => option.value === value);
  return hit ? hit.label : value;
}

/**
 * 回车时该把什么并入当前选择.
 *
 * 返回选中的值; `null` 表示什么都不做.
 *
 * 判定顺序:
 * 1. 与某个候选的**原始名**精确相同 → 选它 (用户明确要这一条);
 * 2. 与其**译名**精确相同 → 选该候选 (照界面上的中文输入也能命中);
 * 3. 与其**别名**精确相同 → 选该候选 (别名不是独立条目, 归到主名);
 * 4. 唯一命中一个候选**且输入不止一个字** → 选它;
 * 5. 否则返回原词, 作为新值加入.
 *
 * 前三条都是整串相等, 一个字也能安全命中 (`遥` 是某人的完整别名就归他).
 *
 * 第 4 条是模糊命中, 必须够长: 一个字 (`瑶`) 会让用户没法新建只有这个字的演员名 —— 而
 * 库里完全可能就有「瑶」这个人. 短词交给下拉让用户自己点, 回车只按原词新建.
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
  // 别名精确命中: 用户记得的常是别名 (「水谷結衣」), 而库里按主名索引. 归到主名,
  // 不然会多出一条本人根本不存在的演员.
  const byAlias = options.find((option) =>
    option.aliases.some((alias) => alias.toLocaleLowerCase() === folded),
  );
  if (byAlias) return { value: byAlias.value, created: false };
  // 模糊命中只在够长时才自动采用, 否则单字会把用户想新建的名字吞掉.
  if ([...term].length < MIN_FUZZY_LENGTH) return { value: term, created: true };
  // 原名 / 译名 / 别名任一片段命中且唯一. 别名同样参与 —— 否则别名片段会被当成新词.
  const matches = options.filter(
    (option) =>
      option.label.toLocaleLowerCase().includes(folded) ||
      option.aliases.some((alias) => alias.toLocaleLowerCase().includes(folded)),
  );
  if (matches.length === 1) return { value: matches[0].value, created: false };
  return { value: term, created: true };
}
