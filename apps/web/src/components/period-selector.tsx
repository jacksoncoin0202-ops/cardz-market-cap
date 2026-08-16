"use client";

/* interactionkit Tabs 移植（https://github.com/armondschneider/interactionkit, MIT）：
   選中項底下 layoutId 滑動膠囊。每個 instance 用 useId 做 layoutId 後綴，
   避免同頁多個 selector 嘅 pill 互相飛越。 */
import { motion } from "framer-motion";
import { useCallback, useEffect, useId, useRef, useState } from "react";
import { ChevronDown } from "lucide-react";
import { tap } from "@/lib/haptic";
import { copy } from "@/lib/i18n";
import { marketWindows } from "@/lib/types";
import { useMarketSettings } from "@/lib/use-market-settings";

export function PeriodSelector({ compact = false }: { compact?: boolean }) {
  const { locale, period, update } = useMarketSettings();
  const t = copy[locale];
  const pillId = `period-pill-${useId()}`;
  const activePeriod = period;
  return (
    <div className={`period-selector${compact ? " period-selector-compact" : ""}`} role="group" aria-label={t.labels.change}>
      {marketWindows.map((item) => (
        <button
          key={item}
          type="button"
          aria-pressed={activePeriod === item}
          onClick={() => {
            /* 已選中嗰個再撳唔震：冇嘢變就唔好扮有回饋 */
            if (item !== activePeriod) tap.select();
            update({ period: item });
          }}
        >
          {activePeriod === item && (
            <motion.span
              layoutId={pillId}
              className="period-pill"
              transition={{ type: "spring", bounce: 0.18, duration: 0.35 }}
            />
          )}
          <span className="period-label">{t.periods[item]}</span>
        </button>
      ))}
    </div>
  );
}

/*
 * 手機榜標題行用嘅時段 popover。同頁熱力圖已經有一個 PeriodSelector 揸住同一條 URL period，
 * 榜上再排六個掣係同一個控件出兩次、又食走一行高度；收埋做「6M ▾」。
 * markup 跟 explore-bar 個 scope menu：<ul role="listbox"> + <button role="option">。
 *
 * 掣**唔准**自己霸一行（owner 2026-08-17）：由 rankings.tsx 擺入 .ranking-heading，
 * 同 h2 同一行右邊。六個選項行 3×2 等闊格（.period-menu-list），唔再係長短不一嘅直行。
 */
export function PeriodMenu() {
  const { locale, period, update } = useMarketSettings();
  const t = copy[locale];
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  /* Escape／揀完一律還 focus 落 trigger，唔好掉咗落 <body>（鍵盤同讀屏會迷路）。
     撳出面收嗰個唔搶 focus——用戶手指已經落咗第二個掣度。 */
  const close = useCallback((refocus: boolean) => {
    setOpen(false);
    if (refocus) triggerRef.current?.focus();
  }, []);
  useEffect(() => {
    if (!open) return;
    const onPointer = (event: PointerEvent) => {
      if (rootRef.current?.contains(event.target as Node)) return;
      close(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.stopPropagation();
      close(true);
    };
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [close, open]);
  return (
    <div className="period-menu" ref={rootRef}>
      <button
        ref={triggerRef}
        type="button"
        className="period-menu-trigger"
        aria-expanded={open}
        aria-haspopup="listbox"
        aria-label={t.labels.pricePeriod}
        onClick={() => { tap.select(); setOpen((value) => !value); }}
      >
        <span>{t.periods[period]}</span>
        <ChevronDown aria-hidden="true" size={12} strokeWidth={2.2} />
      </button>
      {open ? (
        <ul className="select-menu period-menu-list" role="listbox" aria-label={t.labels.pricePeriod}>
          {marketWindows.map((item) => (
            <li key={item} role="presentation">
              <button
                type="button"
                className="period-option"
                role="option"
                aria-selected={item === period}
                onClick={() => {
                  if (item !== period) tap.select();
                  update({ period: item });
                  close(true);
                }}
              >
                {t.periods[item]}
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
