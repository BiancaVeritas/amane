/** 非 null 对象守卫 (含数组 / Date 等); 用于边界解析而非 `as Record`. */
export function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null;
}

/**
 * 将外链图片 URL 包装为后端代理 URL, 绕过浏览器 CORS / 防盗链限制.
 * 仅 http(s) 协议经代理, 其他 (data:, blob:, 相对路径) 原样返回.
 */
export function proxyImageUrl(
  url: string | null | undefined,
  source?: string | null,
): string | undefined {
  if (!url) return undefined;
  if (/^https?:\/\//i.test(url)) {
    const params = new URLSearchParams({ url });
    if (source) params.set("source", source);
    return `/api/resources/proxy?${params.toString()}`;
  }
  return url;
}

const FILE_SIZE_UNITS = ["B", "KB", "MB", "GB", "TB"] as const;

export function formatFileSize(bytes: number | null | undefined): string {
  if (bytes == null) return "-";
  let value = bytes;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < FILE_SIZE_UNITS.length - 1) {
    value /= 1024;
    unitIndex++;
  }
  return `${value.toFixed(unitIndex === 0 ? 0 : 1)} ${FILE_SIZE_UNITS[unitIndex]}`;
}
