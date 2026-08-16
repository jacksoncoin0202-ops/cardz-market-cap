import type { Locale, MarketCardView } from "./types";

/*
 * 卡名顯示規則（owner 2026-08-16）：轉咗語言就全站用當地語言名 —— 熱力圖 hover 預覽、
 * bottom sheet、tile aria、排行榜、卡頁 H1／<title>／JSON-LD 全部同一個 helper。
 *
 * 來源：`card.name[locale]` 由 server-snapshot.ts applyCardNameOverlays 由
 * data/editorial/card-names-by-id.json 畫上去（Pokémon 官方標準譯名，例如「皮卡丘」，唔係港式）。
 * 冇譯名／en locale → 退返 PSA/GemRate 英文 officialName。
 */
export function displayCardName(
  card: Pick<MarketCardView, "officialName" | "name">,
  locale: Locale,
  fallback = "",
): string {
  if (locale === "en") return card.officialName || card.name?.en || fallback;
  return card.name?.[locale] || card.officialName || card.name?.en || fallback;
}
