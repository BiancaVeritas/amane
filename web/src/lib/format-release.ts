/** 发行日 → 年月日 (YYYY-MM-DD); 缺少日时保留 YYYY-MM; 仅有年份时返回 YYYY; 无法解析则原样返回. */
export function formatReleaseYearMonth(release: string | null | undefined): string | null {
  if (!release) return null;
  const trimmed = release.trim();
  if (!trimmed) return null;
  const match = /^(\\d{4})(?:[-/.](\\d{1,2})(?:[-/.](\\d{1,2}))?)?/.exec(trimmed);
  if (!match) return trimmed;
  const year = match[1];
  const month = match[2];
  const day = match[3];
  if (!month) return year;
  const yearMonth = `${year}-${month.padStart(2, "0")}`;
  return day ? `${yearMonth}-${day.padStart(2, "0")}` : yearMonth;
}
