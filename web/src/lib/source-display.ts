/** Canonical user-facing labels for source identifiers whose casing is intentional. */
const SOURCE_DISPLAY_NAMES: Readonly<Record<string, string>> = {
  "1pondo": "1pondo",
  pacopacomama: "Pacopacomama",
  caribbeancom: "Caribbeancom",
  caribbeancompr: "Caribbeancompr",
  tokyo_hot: "Tokyo-Hot",
};

/** Display-only mapping. Never use this for routing, storage, external IDs, or URLs. */
export function displaySourceName(value: string): string {
  return SOURCE_DISPLAY_NAMES[value] ?? value;
}
