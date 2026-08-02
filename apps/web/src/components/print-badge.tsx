import { copy, localizedCardLanguage } from "@/lib/i18n";
import type { Locale, MarketCardView } from "@/lib/types";

/*
 * 印刷版本（printing identity）嘅共用顯示件。三個 surface（rankings / card-detail /
 * heatmap facts）全部行呢度，唔准各自砌一套。
 *
 * 三條硬規矩：
 *  1. 冇值就乜都唔畫 —— 唔准出 `t.status.unavailable`，唔准出空 chip。
 *     snapshot mapper 已經幫手將 `""` 正規化成 `null`，呢度淨係認 null。
 *  2. `printingCode` 唔准出街：佢嘅公開詞彙（base / sp / mb / pb）未定義。
 *  3. `editionCode`（卡包名）曾係 owner 紅線（433 張入面 36 張已出街嘅行係錯對）。
 *     2026-08-02 owner 改決定：淨准出喺內頁同熱力圖彈卡（DETAIL_PRINT_FIELDS），
 *     Top 100 表同 grader chips 照舊唔出；DB 錯對由另一條線修緊。
 */

export type PrintIdentityField = "editionCode" | "setCode" | "rarityCode" | "parallelCode" | "finishCode";

const DEFAULT_FIELDS: PrintIdentityField[] = ["setCode", "rarityCode", "parallelCode", "finishCode"];

/*
 * 真係出街嗰批欄位。`parallelCode` 剔走：12 對已出街孖卡（24 張，含 Wartortle EN rank 57、
 * Erika's Invitation）喺 DB 嘅 parallel 係反轉嘅，未修好之前唔准畫。
 * 所有 surface（rankings / card-detail / heatmap / grader）一律傳呢條白名單，
 * 唔好各自寫 array —— 之後 DB 修好，改呢一行就全站生效。
 */
export const PUBLIC_PRINT_FIELDS: PrintIdentityField[] = ["setCode", "rarityCode", "finishCode"];

/*
 * 內頁專用白名單（card-detail identity list ＋ heatmap CardFacts）：公開欄位之上
 * 加埋卡包名（editionCode，owner 2026-08-02 批准）。唔准用喺 Top 100 表 / grader chips。
 */
export const DETAIL_PRINT_FIELDS: PrintIdentityField[] = ["editionCode", "setCode", "rarityCode", "finishCode"];

function fieldLabel(field: PrintIdentityField, locale: Locale): string {
  const labels = copy[locale].labels;
  if (field === "editionCode") return labels.packSource;
  if (field === "setCode") return labels.setCode;
  if (field === "rarityCode") return labels.rarity;
  if (field === "parallelCode") return labels.parallel;
  return labels.finish;
}

/* 卡包名喺 DB 係全細階英文（"booster pack awakening of the new era"），出街前 title-case。
   停用詞（of / the / and 等）除句首外維持細階；"ex" 跟官方 Pokémon ex 寫法唔大階。 */
const PACK_LOWERCASE_WORDS = new Set(["of", "the", "and", "or", "in", "ex"]);

function displayPackName(editionCode: string): string {
  return editionCode
    .split(/\s+/)
    .map((word, index) =>
      index > 0 && PACK_LOWERCASE_WORDS.has(word) ? word : word.charAt(0).toUpperCase() + word.slice(1),
    )
    .join(" ");
}

/**
 * 版本 pill（日文版 / 英文版）。`card.cardLanguage` 係 null 就回 `null`。
 * `Copy.languages` 只出裸字（「日文」），所以用 `labels.printLanguage` template 夾。
 */
export function PrintLanguageBadge({
  card,
  locale,
  compact,
}: {
  card: MarketCardView;
  locale: Locale;
  compact?: boolean;
}) {
  const language = card.cardLanguage;
  if (!language) return null;
  const text = copy[locale].labels.printLanguage.replace(
    "{language}",
    localizedCardLanguage(language, locale),
  );
  const tone = language === "ja" ? "print-badge--ja" : language === "en" ? "print-badge--en" : "";
  return (
    <span className={`print-badge${tone ? ` ${tone}` : ""}${compact ? " print-badge--compact" : ""}`}>
      {text}
    </span>
  );
}

/**
 * 行內 chips（set code / rarity / parallel / finish）。淨係畫有值嘅欄；
 * 一條都冇就回 `null`，唔會留低一個空 `.print-chips` 容器。
 *
 * set code 一定係獨立一粒 chip，唔准同 collector number 串埋一齊出
 * （已出街嘅 One Piece 有 4 張 set_code 同編號前綴唔一致，串埋會似 bug）。
 */
export function PrintAttributeChips({
  card,
  locale,
  fields = DEFAULT_FIELDS,
  className,
}: {
  card: MarketCardView;
  locale: Locale;
  fields?: PrintIdentityField[];
  className?: string;
}) {
  const identity = card.printingIdentity;
  if (!identity) return null;
  const chips = fields
    .map((field) => ({ field, value: identity[field] }))
    .filter((chip): chip is { field: PrintIdentityField; value: string } => Boolean(chip.value));
  if (chips.length === 0) return null;
  return (
    <span className={className ? `print-chips ${className}` : "print-chips"}>
      {chips.map((chip) => {
        const label = fieldLabel(chip.field, locale);
        return (
          <span key={chip.field} className="print-chip" title={label} aria-label={`${label}: ${chip.value}`}>
            {chip.value}
          </span>
        );
      })}
    </span>
  );
}

/**
 * 畀 `<dl>` 版面（card-detail、heatmap CardFacts）用嘅純數據版。
 * 完全冇資料就回 `[]`，叫方直接唔好畫個 section。
 */
export function printIdentityRows(
  card: MarketCardView,
  locale: Locale,
  fields: PrintIdentityField[] = DEFAULT_FIELDS,
): Array<{ key: string; label: string; value: string }> {
  const labels = copy[locale].labels;
  const rows: Array<{ key: string; label: string; value: string }> = [];
  if (card.cardLanguage) {
    rows.push({ key: "language", label: labels.language, value: localizedCardLanguage(card.cardLanguage, locale) });
  }
  const identity = card.printingIdentity;
  if (identity) {
    for (const field of fields) {
      const value = identity[field];
      if (value) {
        rows.push({ key: field, label: fieldLabel(field, locale), value: field === "editionCode" ? displayPackName(value) : value });
      }
    }
  }
  return rows;
}
