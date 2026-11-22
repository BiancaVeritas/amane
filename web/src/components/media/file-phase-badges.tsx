import { Badge, Group } from "@mantine/core";
import {
  IconBadgeHd,
  IconBlur,
  IconDroplet,
  IconEye,
  IconLockOpen,
  IconSubtitles,
} from "@tabler/icons-react";
import type { TFunction } from "i18next";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import type { FilePhaseSummary, Mosaic } from "@/client/types.gen";
import { OverlayChip, OverlayChipLabel } from "@/components/media/overlay-chip";
import { exhaustiveRecord } from "@/lib/exhaustive";

export type FilePhaseLike = {
  has_subtitle?: boolean;
  /** 条目相位才有: 有码 / 无码由条目类型决定, 与挂载文件无关. */
  censored?: boolean;
  uncensored?: boolean;
  mosaics?: Array<Mosaic>;
  /** 文件相位: 这一份文件自己的马赛克标记. */
  mosaic?: Mosaic | null;
  definition?: string | null;
  /** 文件名含独立的 LTX 标记; 与 mosaic 无关, 单独成角标. */
  ltx?: boolean;
  /** 条目标签; 「無碼」「無修正」这类字样也视为无码. */
  tags?: readonly string[];
  /** 条目标题; FC2 / heydouga 这类站内自编号的标题里也常写「無修正」. */
  title?: string | null;
};

type PhaseChipKey = "sub" | "cen" | "u" | "crack" | "leak" | "ltx" | "def";

type PhaseChip = { key: PhaseChipKey; label: string };

const PHASE_CHIP = exhaustiveRecord<PhaseChipKey>()({
  sub: {
    color: "orange",
    Icon: IconSubtitles,
    iconColor: "var(--mantine-color-orange-4)",
  },
  cen: {
    color: "gray",
    Icon: IconBlur,
    iconColor: "var(--mantine-color-gray-4)",
  },
  u: {
    color: "red",
    Icon: IconEye,
    iconColor: "var(--mantine-color-red-4)",
  },
  crack: {
    color: "grape",
    Icon: IconLockOpen,
    iconColor: "var(--mantine-color-grape-3)",
  },
  leak: {
    color: "teal",
    Icon: IconDroplet,
    iconColor: "var(--mantine-color-teal-3)",
  },
  ltx: {
    // 与「破解」同一副外观: 两者都是「可以去码」的同一类副本, 没必要用两套图标区分.
    color: "grape",
    Icon: IconLockOpen,
    iconColor: "var(--mantine-color-grape-3)",
  },
  def: {
    color: "blue",
    Icon: IconBadgeHd,
    iconColor: "var(--mantine-color-blue-4)",
  },
});

/** 有码按性质给: 带无码标记的副本算无码, 不亮有码; 条目 overlay 与文件行共用. */
function showsCensored(phase: FilePhaseLike): boolean {
  return phase.censored === true;
}

/** 标签 / 标题里表示「无码」的写法. FC2、heydouga 这类站内自编号的番号不带马赛克
 * 信息, 只能靠这些字样区分; 「無」单字会命中「無料」, 因此只收方括号包住的写法. */
const UNCENSORED_TAGS = ["無碼", "无码", "無修正", "无修正", "無修", "无修"];
const UNCENSORED_BRACKETED = /[[【(（]\s*無\s*[\]】)）]/;

/** 文本里有没有无码字样. */
function textSaysUncensored(text: string | null | undefined): boolean {
  if (!text) return false;
  if (UNCENSORED_BRACKETED.test(text)) return true;
  return UNCENSORED_TAGS.some((word) => text.includes(word));
}

/** 标签或标题里有没有无码字样. */
function metadataSaysUncensored(phase: FilePhaseLike): boolean {
  if (phase.tags?.some((tag) => textSaysUncensored(tag))) return true;
  return textSaysUncensored(phase.title);
}

/** 无码: 标签或标题明说无码/无修正时直接点亮; 否则用条目布尔值, 再退回看这一份文件自己的标记. */
function showsUncensored(phase: FilePhaseLike): boolean {
  if (metadataSaysUncensored(phase)) return true;
  if (phase.uncensored != null) return phase.uncensored;
  return phase.mosaic === "uncensored";
}

function hasMosaic(phase: FilePhaseLike, mosaic: Mosaic): boolean {
  if (phase.mosaics?.includes(mosaic)) return true;
  return phase.mosaic === mosaic;
}

function mosaicChips(phase: FilePhaseLike, t: TFunction<"metadata">): PhaseChip[] {
  const chips: PhaseChip[] = [];
  // 有码 / 无码二选一: 无码的证据成立就不再是有码. 后端聚合已互斥, 这里再判一次是因为
  // 「無碼」「無修正」标签也能点亮无码 —— 标签与条目类型组合起来同样不能亮两枚.
  const uncensored = showsUncensored(phase);
  if (showsCensored(phase) && !uncensored) {
    chips.push({ key: "cen", label: t("filePhase.censored") });
  }
  if (uncensored) {
    chips.push({ key: "u", label: t("filePhase.uncensored") });
  }
  if (hasMosaic(phase, "cracked")) {
    chips.push({ key: "crack", label: t("filePhase.cracked") });
  }
  if (hasMosaic(phase, "leaked")) {
    chips.push({ key: "leak", label: t("filePhase.leaked") });
  }
  if (phase.ltx === true) {
    chips.push({ key: "ltx", label: " LTX " });
  }
  return chips;
}

function mediaChips(phase: FilePhaseLike, t: TFunction<"metadata">): PhaseChip[] {
  const chips: PhaseChip[] = [];
  if (phase.definition) {
    chips.push({ key: "def", label: phase.definition });
  }
  if (phase.has_subtitle) {
    chips.push({ key: "sub", label: t("filePhase.subtitle") });
  }
  return chips;
}

function PhaseOverlayChip({ chip }: { chip: PhaseChip }) {
  const meta = PHASE_CHIP[chip.key];
  const monospace = chip.key === "def";
  return (
    <OverlayChip>
      <meta.Icon size={12} color={meta.iconColor} />
      <OverlayChipLabel ff={monospace ? "monospace" : undefined} lts={monospace ? 0.3 : undefined}>
        {chip.label}
      </OverlayChipLabel>
    </OverlayChip>
  );
}

interface FilePhaseBadgesProps {
  phase: FilePhaseLike | FilePhaseSummary | null | undefined;
  size?: "xs" | "sm";
}

/** 条目相位与文件行共用: 有码 / 无码取条目相位, 破解 / 流出 / 中字 / 分辨率取文件行. */
export function FilePhaseBadges({ phase, size = "xs" }: FilePhaseBadgesProps) {
  const { t } = useTranslation("metadata");
  if (phase == null) return null;

  const media = mediaChips(phase, t);
  const badges = [
    ...media.filter((chip) => chip.key === "sub"),
    ...mosaicChips(phase, t),
    ...media.filter((chip) => chip.key === "def"),
  ];
  if (badges.length === 0) return null;

  const iconSize = size === "sm" ? 12 : 10;
  return (
    <Group gap={4} wrap="wrap">
      {badges.map((badge) => {
        const meta = PHASE_CHIP[badge.key];
        return (
          <Badge
            key={badge.key}
            size={size}
            color={meta.color}
            variant="filled"
            leftSection={<meta.Icon size={iconSize} />}
          >
            {badge.label}
          </Badge>
        );
      })}
    </Group>
  );
}

const CORNER_GROUP = { zIndex: 1, maxWidth: "68%" } as const;

interface FilePhaseOverlayProps {
  phase: FilePhaseLike | FilePhaseSummary | null | undefined;
  /** 左下角顶部追加 (出演年龄): 排在中字/分辨率之上. */
  topExtra?: ReactNode;
  /** 左下角底部追加: 排在中字/分辨率之下. */
  bottomExtra?: ReactNode;
}

/** 海报/封面 CSS 水印. 左上马赛克组, 左下中字+分辨率; 样式对齐评分/日期 chip. */
export function FilePhaseOverlay({ phase, topExtra, bottomExtra }: FilePhaseOverlayProps) {
  const { t } = useTranslation("metadata");
  const mosaic = phase != null ? mosaicChips(phase, t) : [];
  const media = phase != null ? mediaChips(phase, t) : [];

  return (
    <>
      {mosaic.length > 0 && (
        <Group
          pos="absolute"
          top={6}
          left={6}
          gap={4}
          wrap="nowrap"
          // 有码 / LTX 那组按内容等宽, 右边缘才齐; 内容长的照常超出.
          align="stretch"
          style={{ ...CORNER_GROUP, flexDirection: "column" }}
        >
          {mosaic.map((chip) => (
            <PhaseOverlayChip key={chip.key} chip={chip} />
          ))}
        </Group>
      )}
      {(media.length > 0 || topExtra != null || bottomExtra != null) && (
        <Group
          pos="absolute"
          bottom={6}
          left={6}
          gap={4}
          wrap="nowrap"
          // 分辨率 / 中字 / 年龄 保持各自内容宽度, 不被 1080p 之类撑成一样宽.
          align="flex-start"
          style={{ ...CORNER_GROUP, flexDirection: "column" }}
        >
          {/* 顺序即视觉顺序: 年龄在上, 中字/分辨率在下. */}
          {topExtra}
          {media.map((chip) => (
            <PhaseOverlayChip key={chip.key} chip={chip} />
          ))}
          {bottomExtra}
        </Group>
      )}
    </>
  );
}
