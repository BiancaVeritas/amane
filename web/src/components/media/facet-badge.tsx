import { Badge, type MantineColor } from "@mantine/core";
import type { ReactNode } from "react";
import { Link } from "@tanstack/react-router";
import type { ActorGender, FacetKind } from "@/client/types.gen";
import { GenderMark } from "@/components/media/gender-mark";
import { metaSearchForFacet } from "@/lib/facets";
import classes from "./facet-badge.module.css";

const KIND_COLOR: Record<FacetKind, MantineColor> = {
  actor: "grape",
  director: "indigo",
  tag: "teal",
  studio: "blue",
  publisher: "cyan",
  series: "orange",
  user_tag: "pink",
};

/** 去掉锚点行高, 让徽章高度就是 flex 项高度. */
const BADGE_LINK_STYLE = { textDecoration: "none", display: "inline-flex", lineHeight: 1 } as const;

/** 有映射时渲染 `显示名 (原名)`, 否则只渲染原名. */
function renderName(name: string, displayName: string | null | undefined): ReactNode {
  if (!displayName || displayName === name) {
    return name;
  }
  return (
    <>
      {displayName}
      <span className={classes.originalName}> ({name})</span>
    </>
  );
}

interface FacetBadgeProps {
  kind: FacetKind;
  /** Facet id - 未知 (未建立投影索引) 时为 null/undefined, 渲染为不可点击的纯文本徽章. */
  id: number | null | undefined;
  name: string;
  /** 仅 actor: 显示名映射; 有值时渲染为 `显示名 (原名)`. */
  displayName?: string | null;
  /** 仅 actor: 来自 Actor 实体. unknown / 缺省不画符号. */
  gender?: ActorGender | null;
  /** "catalog": 分类实体页 (actor → /actors/$id); "meta": 片库列表并附带该 facet 过滤. @default "catalog" */
  mode?: "meta" | "catalog";
  variant?: "filled" | "light" | "outline" | "dot" | "transparent";
  size?: "xs" | "sm" | "md" | "lg";
}

/** 默认为实体页; `mode="meta"` 时进入片库筛选. */
export function FacetBadge({
  kind,
  id,
  name,
  displayName,
  gender,
  mode = "catalog",
  variant = "light",
  size = "sm",
}: FacetBadgeProps) {
  const color = KIND_COLOR[kind];
  // 名称字段统一保留数据本身的大小写；不得由 Badge 默认样式自动转成大写。
  const leftSection =
    kind === "actor" && (gender === "female" || gender === "male") ? (
      <GenderMark gender={gender} size={12} />
    ) : undefined;

  if (id == null) {
    return (
      <Badge color={color} variant={variant} size={size} leftSection={leftSection} tt="none">
        <span>{renderName(name, displayName)}</span>
      </Badge>
    );
  }

  if (mode === "catalog") {
    if (kind === "actor") {
      return (
        <Link to="/actors/$actorId" params={{ actorId: String(id) }} style={BADGE_LINK_STYLE}>
          <Badge
            component="span"
            color={color}
            variant={variant}
            size={size}
            leftSection={leftSection}
            tt="none"
            style={{ cursor: "pointer" }}
          >
            <span>{renderName(name, displayName)}</span>
          </Badge>
        </Link>
      );
    }
    return (
      <Link
        to="/catalog/$kind/$facetId"
        params={{ kind, facetId: String(id) }}
        style={BADGE_LINK_STYLE}
      >
        <Badge
          component="span"
          color={color}
          variant={variant}
          size={size}
          tt="none"
          style={{ cursor: "pointer" }}
        >
          <span>{renderName(name, displayName)}</span>
        </Badge>
      </Link>
    );
  }

  return (
    <Link to="/meta" search={metaSearchForFacet(kind, id)} style={BADGE_LINK_STYLE}>
      <Badge
        component="span"
        color={color}
        variant={variant}
        size={size}
        leftSection={leftSection}
        tt="none"
        style={{ cursor: "pointer" }}
      >
        <span>{renderName(name, displayName)}</span>
      </Badge>
    </Link>
  );
}
