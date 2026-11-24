/**
 * 时长输入的解析.
 *
 * 用户手填时长时有几种常见写法: 纯分钟数 (`100`), 时钟串 (`1:40:45`),
 * 拿句点 / 空格分隔 (`1.40.45` / `1 40 45`), 或带单位 (`1h40m45s`).
 * 都接受, 统一折成「总秒数」—— 与刮削侧的 `runtime_seconds` 同一口径,
 * 分钟由它向上取整得出, 显示与 NFO 才不会因为各自换算而对不上.
 *
 * 各段只看位数配比, 不做 `<= 59` 校验: `1:99` 就是 1 分 99 秒, 合 159 秒 = 2 分 39 秒.
 * 拦下它只会让人重打一遍, 而进位本来就是相加的自然结果.
 */

/** 解析结果: `seconds` 为总秒数, `null` 表示这一格没填; 无法识别时返回 `undefined`. */
export type ParsedRuntime = { seconds: number } | null | undefined;

const CLOCK = /^(?:(\d+):)?(\d+):(\d+)$/;
const DOTTED = /^(\d+)[.\s](\d+)[.\s](\d+)$/;
const DOTTED_SHORT = /^(\d+)[.\s](\d+)$/;
const WITH_UNITS = /^(?:(\d+)\s*h)?\s*(?:(\d+)\s*m)?\s*(?:(\d+)\s*s)?$/i;

/** 各段直接相加; 分秒进位由加法自然完成, 不额外校验范围. */
function partsSeconds(hours: number, minutes: number, seconds: number): number {
  return hours * 3600 + minutes * 60 + seconds;
}

export function parseRuntimeInput(raw: string): ParsedRuntime {
  const value = raw.trim();
  if (!value) return null;

  const clock = CLOCK.exec(value);
  if (clock) {
    return withSeconds(partsSeconds(Number(clock[1] ?? 0), Number(clock[2]), Number(clock[3])));
  }

  const dotted = DOTTED.exec(value);
  if (dotted) {
    return withSeconds(partsSeconds(Number(dotted[1]), Number(dotted[2]), Number(dotted[3])));
  }

  // 两段式一律「分钟.秒」: 短视频时长以分钟计, `69.18` 是 69 分 18 秒.
  // 想写小时用三段式 (`1:40:45`) 或带单位 (`1h40m`), 两段式不当小时用.
  // 这里不走 toSeconds: 它按时钟串校验 (`分 <= 59`), 而分钟位本来就允许 69 这种值.
  const short = DOTTED_SHORT.exec(value);
  if (short) {
    return withSeconds(partsSeconds(0, Number(short[1]), Number(short[2])));
  }

  const units = WITH_UNITS.exec(value);
  if (units && (units[1] || units[2] || units[3])) {
    return withSeconds(
      partsSeconds(Number(units[1] ?? 0), Number(units[2] ?? 0), Number(units[3] ?? 0)),
    );
  }

  // 纯数字按分钟: 沿用旧口径, 老用户习惯直接填 120.
  if (!/^\d+$/.test(value)) return undefined;
  const minutes = Number(value);
  return minutes > 0 ? { seconds: minutes * 60 } : undefined;
}

/** 0 秒与缺省同义; `undefined` 继续向上传递「无法识别」. */
function withSeconds(seconds: number | undefined): ParsedRuntime {
  if (seconds === undefined) return undefined;
  return seconds > 0 ? { seconds } : undefined;
}

/**
 * 秒数 → 输入框里回显的写法, 统一成 `时:分:秒` / `分:秒`.
 *
 * 整分钟也补 `:00`: 同一格里 `1:01` 与 `1:09:18` 混排时长度不一, 看着零散; 更重要的是
 * 两段式会被 ``parseRuntimeInput`` 读成「分:秒」(`1:01` = 61 秒), 整分钟写成两段式会
 * 让「打开看着是 1 分 1 秒, 保存却变成 1 小时 1 分」这种事发生.
 */
export function formatRuntimeInput(minutes?: number | null, seconds?: number | null): string {
  const total = seconds && seconds > 0 ? seconds : minutes && minutes > 0 ? minutes * 60 : 0;
  if (!total) return "";
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h > 0) return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  return `${m}:${String(s).padStart(2, "0")}`;
}

/** 秒数 → 分钟 (向上取整); 与后端 `runtime` 的算法一致. */
export function runtimeMinutes(seconds: number): number {
  return Math.ceil(seconds / 60);
}
