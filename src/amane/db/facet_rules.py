"""单跳 alias + block. 表内保持规范形; apply 只查一次, 不做多跳递归."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from .models import SCRAPE_FACET_KINDS, FacetKind, FacetRuleAction, Metadata


@dataclass(frozen=True, slots=True)
class RuleEntry:
    action: FacetRuleAction
    target_name: str | None = None


type RulesBySource = Mapping[str, RuleEntry]
type RulesByKind = Mapping[FacetKind, RulesBySource]


def resolve_name(name: str, rules: RulesBySource) -> str | None:
    """单跳解析: 未命中原样返回; alias 换 target; block 或 alias 终态被 block 则 None."""
    rule = rules.get(name)
    if rule is None:
        return name
    if rule.action == FacetRuleAction.BLOCK:
        return None
    target = rule.target_name
    if target is None:
        return None
    target_rule = rules.get(target)
    if target_rule is not None and target_rule.action == FacetRuleAction.BLOCK:
        return None
    return target


def apply_list(names: list[str], rules: RulesBySource) -> list[str]:
    """保序去重; 空串跳过."""
    out: list[str] = []
    for name in names:
        if not name:
            continue
        resolved = resolve_name(name, rules)
        if resolved is None:
            continue
        if resolved not in out:
            out.append(resolved)
    return out


def apply_scalar(value: str | None, rules: RulesBySource) -> str | None:
    """空或 block → None."""
    if value is None or not value:
        return None
    return resolve_name(value, rules)


def apply_metadata_facet_fields(meta: Metadata, rules_by_kind: RulesByKind) -> None:
    """就地改写六个爬取侧分类真值字段."""
    meta.actors = apply_list(meta.actors, rules_by_kind.get(FacetKind.ACTOR, {}))
    meta.directors = apply_list(meta.directors, rules_by_kind.get(FacetKind.DIRECTOR, {}))
    meta.tags = apply_list(meta.tags, rules_by_kind.get(FacetKind.TAG, {}))
    meta.studio = apply_scalar(meta.studio, rules_by_kind.get(FacetKind.STUDIO, {}))
    meta.publisher = apply_scalar(meta.publisher, rules_by_kind.get(FacetKind.PUBLISHER, {}))
    series_values = meta.series_names or ([meta.series] if meta.series else [])
    meta.series_names = apply_list(series_values, rules_by_kind.get(FacetKind.SERIES, {}))
    meta.series = meta.series_names[0] if meta.series_names else None


def empty_rules_by_kind() -> dict[FacetKind, dict[str, RuleEntry]]:
    return {kind: {} for kind in SCRAPE_FACET_KINDS}
