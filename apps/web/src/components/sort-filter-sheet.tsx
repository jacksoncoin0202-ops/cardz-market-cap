"use client";

import { useState } from "react";
import { Sheet } from "./ui/sheet";
import { tap } from "@/lib/haptic";
import { copy, localizedCardLanguage } from "@/lib/i18n";
import type { SortDir } from "@/lib/list-explore";
import type { PrintLangFilter } from "@/lib/use-market-settings";
import type { Locale, PrintLanguage } from "@/lib/types";

/*
 * 手機排序／篩選 bottom sheet（設計稿 §設計（手機）4）。
 *
 * 一個手勢一次寫入：三個控件全部只改 sheet 入面嘅草稿，撳「套用」先至行一次
 * `update({ sort, dir, printLang, page: 1 })`。以前排序 chips 每撳一下就一次
 * router.replace，換三樣嘢 = 三次 RSC navigation，仲會同搜尋框 debounce 撞。
 *
 * modal 機制（scroll lock / focus trap / Escape / backdrop / 拉落收 / 還 focus）全部
 * 由共用 <Sheet> 負責，同 heatmap 個 CardDialog 行同一份 code，呢度只出內容。
 */
export interface SortFilterValue {
  sort: string;
  dir: SortDir;
  printLang: PrintLangFilter;
}

interface SortFilterBodyProps {
  onClose: () => void;
  locale: Locale;
  sortKeys: Array<{ key: string; label: string }>;
  value: SortFilterValue;
  /* 榜上真係有嘅印刷語言；≤1 種就唔出呢一段 */
  availableLanguages: PrintLanguage[];
  onApply: (next: SortFilterValue) => void;
  onReset: () => void;
}

export function SortFilterSheet({ open, ...body }: SortFilterBodyProps & { open: boolean }) {
  return (
    <Sheet
      open={open}
      onClose={body.onClose}
      variant="sheet"
      backdropClassName="sheet-backdrop"
      panelClassName="bottom-sheet sort-sheet"
      ariaLabelledBy="sort-sheet-title"
    >
      <SortFilterBody {...body} />
    </Sheet>
  );
}

function SortFilterBody({ onClose, locale, sortKeys, value, availableLanguages, onApply, onReset }: SortFilterBodyProps) {
  const t = copy[locale];
  /* 草稿只活喺 sheet 入面。<Sheet> 只喺 open 期間 mount 呢個 body，所以每次開都由
     傳入嘅 URL 現值重新起錶，唔使 effect 去 sync（sync effect 會同「用戶啱啱改咗草稿」打交）。 */
  const [draft, setDraft] = useState<SortFilterValue>(value);
  const langOptions: PrintLangFilter[] = ["all", ...availableLanguages];
  return (
    <>
      <div className="sheet-drag-region">
        <div className="sheet-handle" />
        <button type="button" className="sheet-close" onClick={onClose}>{t.labels.close}</button>
      </div>
      <h3 id="sort-sheet-title" className="sort-sheet-title">{t.labels.sortSheetTitle}</h3>
      <section className="sort-sheet-group">
        <h4>{t.labels.sortBy}</h4>
        <div className="sort-sheet-options" role="group" aria-label={t.labels.sortBy}>
          {sortKeys.map((item) => (
            <button
              key={item.key}
              type="button"
              aria-pressed={draft.sort === item.key}
              onClick={() => {
                if (draft.sort !== item.key) tap.select();
                setDraft((prev) => ({ ...prev, sort: item.key }));
              }}
            >
              {item.label}
            </button>
          ))}
        </div>
      </section>
      {/* rank 榜嘅方向由排名本身決定，冇得反轉，所以淨係非 rank 先出方向 */}
      {draft.sort !== "rank" ? (
        <section className="sort-sheet-group">
          <h4>{t.labels.sortDirection}</h4>
          <div className="sort-sheet-options" role="group" aria-label={t.labels.sortDirection}>
            {([["desc", t.labels.sortHighToLow], ["asc", t.labels.sortLowToHigh]] as Array<[SortDir, string]>).map(([key, label]) => (
              <button
                key={key}
                type="button"
                aria-pressed={draft.dir === key}
                onClick={() => {
                  if (draft.dir !== key) tap.select();
                  setDraft((prev) => ({ ...prev, dir: key }));
                }}
              >
                {label}
              </button>
            ))}
          </div>
        </section>
      ) : null}
      {availableLanguages.length > 1 ? (
        <section className="sort-sheet-group">
          <h4>{t.labels.language}</h4>
          <div className="sort-sheet-options" role="group" aria-label={t.labels.language}>
            {langOptions.map((lang) => (
              <button
                key={lang}
                type="button"
                aria-pressed={draft.printLang === lang}
                onClick={() => {
                  if (draft.printLang !== lang) tap.select();
                  setDraft((prev) => ({ ...prev, printLang: lang }));
                }}
              >
                {lang === "all" ? t.labels.languageFilterAll : localizedCardLanguage(lang, locale)}
              </button>
            ))}
          </div>
        </section>
      ) : null}
      <div className="sort-sheet-actions">
        <button
          type="button"
          className="sort-sheet-reset"
          onClick={() => { tap.select(); onReset(); onClose(); }}
        >
          {t.labels.resetFilters}
        </button>
        <button
          type="button"
          className="sort-sheet-apply"
          onClick={() => { tap.select(); onApply(draft); onClose(); }}
        >
          {t.labels.applyFilters}
        </button>
      </div>
    </>
  );
}
