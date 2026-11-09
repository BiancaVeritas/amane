/** 图片来源标签: 从 URL 推断站点名, 无法识别时留空. */

/** 站点域名片段 → 展示名. 顺序敏感: 先匹配更具体的域名. */
const DOMAIN_LABELS: ReadonlyArray<readonly [string, string]> = [
  ["caribbeancompr", "Caribbeancom"],
  ["caribbeancom", "Caribbeancom"],
  ["1pondo", "1Pondo"],
  ["10musume", "10Musume"],
  ["pacopacomama", "Pacopacomama"],
  ["tokyo-hot", "Tokyo-Hot"],
  ["tokyohot", "Tokyo-Hot"],
  ["heyzo", "HEYZO"],
  ["d2pass", "D2Pass"],
  ["aventertainments", "AVEntertainments"],
  ["javbus", "JavBus"],
  ["javdb", "JavDB"],
  ["jdbstatic", "JavDB"],
  ["dmm.co.jp", "DMM"],
  ["airav", "Airav"],
  ["themoviedb", "TheMovieDb"],
  ["gfriends", "GFriends"],
  ["tushyraw", "TushyRaw"],
  ["tushy", "Tushy"],
  ["theporndb", "ThePornDB"],
  ["wikipedia", "Wikipedia"],
  ["wikimedia", "Wikipedia"],
  ["minnano", "Minnano"],
];

/**
 * 图片地址 → 来源展示名; 认不出返回空串.
 *
 * 上传件与裁切派生件都是内部地址, 没有站点信息, 分别标为「上传」与「裁切」.
 */
export function imageSourceLabel(url: string): string {
  if (!url) return "";
  if (url.startsWith("/api/resources/")) {
    // 内部地址无法回溯到原始站点, 只能给出大类.
    return "";
  }
  const lower = url.toLowerCase();
  for (const [needle, label] of DOMAIN_LABELS) {
    if (lower.includes(needle)) {
      return label;
    }
  }
  return "";
}
