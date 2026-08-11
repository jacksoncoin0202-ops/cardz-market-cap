import { copy, localizedCardLanguage } from "@/lib/i18n";
import type { Locale, MarketCardView } from "@/lib/types";

/*
 * 印刷版本（printing identity）嘅共用顯示件。兩個 surface（card-detail 個
 * identity list、heatmap 彈卡個 CardFacts）全部行呢度，唔准各自砌一套。
 * 榜頁（rankings）唔畫呢啲欄。
 *
 * 三條硬規矩：
 *  1. 冇值就乜都唔畫 —— 唔准出 `t.status.unavailable`，唔准出空 chip。
 *     snapshot mapper 已經幫手將 `""` 正規化成 `null`，呢度淨係認 null。
 *  2. `rarityCode`、`parallelCode`、`printingCode` 唔屬於 frontend field type，
 *     所以 component 冇可能意外將佢哋放入 DOM。
 *  3. `editionCode`（卡包名）曾係 owner 紅線（433 張入面 36 張已出街嘅行係錯對）。
 *     2026-08-02 owner 改決定：淨准出喺內頁同熱力圖彈卡，Top 100 表照舊唔出，
 *     **前提係「DB 錯對由另一條線修緊」**。
 *
 *     2026-08-11 查實嗰條線唔可能存在，所以呢欄收返。`catalog_printing_identity`
 *     唔係顯示表，係指紋表：佢十個欄（含 edition_code）係 printing_sha() 嘅
 *     casefold 前像，validate_psa_identity_repair.py 由 live 行重算再對
 *     canonical_printing_sha256。即係
 *       (a) 冇得由權威重述 —— 一改就郁 606 行已鎖死嘅 hash，同今朝
 *           set_name 撞嘅係同一堵牆（見 docs/POSTMORTEM_PSA_AUTHORITY_20260811.md）；
 *       (b) 亦都冇權威可跟 —— 全 repo 冇一個 writer 寫過真值：g10_ingest 派
 *           `"edition": None`、converge_printing_identity 派 `""`、rebuild_036
 *           mint 陣派 `''`、new_era_db_tidy 補 `'unknown'`。剩低嗰批「似層層」
 *           嘅值係一次性貼落去、冇 lineage 嘅。
 *     實測 1286 張出街卡：606 張畫到呢行，其中 136 張畫「Unknown」，130 張只係
 *     將上一行已經畫咗嘅 set 名細階重講一次。所以呢個唔係「等 DB 修好」，係
 *     display 由頭到尾綁錯咗去指紋欄。指紋自己嗰套用字留返畀 hash 用。
 */

export type PrintIdentityField = "setCode" | "finishCode";

/*
 * 內頁專用白名單（card-detail identity list ＋ heatmap CardFacts）。
 * 卡包名（editionCode）2026-08-11 收返，理由見上面第 3 條。
 *
 * 呢度得一條 list，係特登嘅。之前有三條（`DEFAULT_FIELDS`、`PUBLIC_PRINT_FIELDS`、
 * `DETAIL_PRINT_FIELDS`），三條嘅內容一模一樣，前兩條零叫方 —— 即係三個名扮住
 * 「三種 surface 各有各政策」，實情零政策。所以下面 `fields` 冇 default value：
 * 唔准再有一個「唔傳就自動有」嘅隱形白名單。
 */
export const DETAIL_PRINT_FIELDS: PrintIdentityField[] = ["setCode", "finishCode"];

function fieldLabel(field: PrintIdentityField, locale: Locale): string {
  const labels = copy[locale].labels;
  if (field === "setCode") return labels.setCode;
  return labels.finish;
}

function displayableIdentityValue(field: PrintIdentityField, value: string): string | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  // producer placeholder — do not show as if it were a real finish.
  if (field === "finishCode" && trimmed.toLowerCase() === "unknown") return null;
  return trimmed;
}

/**
 * 畀 `<dl>` 版面（card-detail、heatmap CardFacts）用嘅純數據版。
 * 完全冇資料就回 `[]`，叫方直接唔好畫個 section。
 */
export function printIdentityRows(
  card: MarketCardView,
  locale: Locale,
  fields: PrintIdentityField[],
): Array<{ key: string; label: string; value: string }> {
  const labels = copy[locale].labels;
  const rows: Array<{ key: string; label: string; value: string }> = [];
  if (card.cardLanguage) {
    rows.push({ key: "language", label: labels.language, value: localizedCardLanguage(card.cardLanguage, locale) });
  }
  const identity = card.printingIdentity;
  if (identity) {
    for (const field of fields) {
      const raw = identity[field];
      if (raw) {
        const value = displayableIdentityValue(field, raw);
        if (value) rows.push({ key: field, label: fieldLabel(field, locale), value });
      }
    }
  }
  return rows;
}
