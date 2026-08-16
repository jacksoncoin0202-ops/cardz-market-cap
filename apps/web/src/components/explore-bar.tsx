"use client";

import { startTransition, useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { ArrowDown, ArrowUp, ChevronsUpDown, X } from "lucide-react";
import { tap } from "@/lib/haptic";
import { copy } from "@/lib/i18n";
import type { SortDir } from "@/lib/list-explore";
import { useMarketSettings } from "@/lib/use-market-settings";
import { useMediaQuery } from "@/lib/use-media-query";

export function SortHeader({
  label,
  sortKey,
  activeKey,
  dir,
  onSort,
  className,
  children,
}: {
  label: string;
  sortKey: string;
  activeKey: string;
  dir: SortDir;
  onSort: (key: string) => void;
  className?: string;
  children?: ReactNode;
}) {
  const active = activeKey === sortKey;
  const ariaSort = sortKey === "rank"
    ? (activeKey === "rank" ? "descending" : "none")
    : active ? (dir === "asc" ? "ascending" : "descending") : "none";
  /* 非 active 欄出中性 ChevronsUpDown（CSS .sort-caret 32% opacity）；只有 active 先出方向箭嘴 */
  const Icon = !active ? ChevronsUpDown : dir === "asc" ? ArrowUp : ArrowDown;
  return (
    <th scope="col" className={className} aria-sort={ariaSort}>
      <button type="button" className="sort-th" onClick={() => onSort(sortKey)}>
        <span>{label}</span>
        {sortKey !== "rank" && (
          <Icon aria-hidden="true" className={`sort-caret${active ? " sort-caret-active" : ""}`} size={11} strokeWidth={2.4} />
        )}
      </button>
      {children}
    </th>
  );
}

/* 打字後等 220ms 先寫 URL：URL 係 shareable / back-forward 真身，但每粒鍵都 router.replace
   會令 caret 卡頓（每次都係一次 RSC navigation）。 */
const QUERY_DEBOUNCE_MS = 220;

/* globals.css 嘅手機 explore 段係 `@media (max-width: 680px)`；兩邊要同一個斷點，
   唔係就會出現「JS 當手機、CSS 當桌面」嘅半截版面。 */
const MOBILE_BAR_QUERY = "(max-width: 680px)";

/* 讀屏播報獨立 debounce：q 每 220ms 寫一次 URL，跟住播就變成打字期間連珠炮發。
   ~1s 靜咗先播一次，而且播嘅係人話（「148 張卡牌」）唔係「148 / 1599」。 */
const ANNOUNCE_DEBOUNCE_MS = 1000;

function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || target.isContentEditable;
}

export function ExploreBar({
  query,
  onQueryChange,
  placeholder,
  searchLabel,
  clearLabel,
  resultLabel,
  sortKeys,
  sort,
  dir,
  onSort,
  highToLow,
  lowToHigh,
  onSearchFocus,
  onOpenSortSheet,
  sortSheetLabel,
  sortSheetActive = false,
  inlineCountLabel,
  announceLabel,
  filterChips,
  sticky = false,
}: {
  query: string;
  onQueryChange: (value: string) => void;
  placeholder: string;
  searchLabel: string;
  clearLabel: string;
  resultLabel: string | null;
  sortKeys: Array<{ key: string; label: string }>;
  sort: string;
  dir: SortDir;
  onSort: (key: string) => void;
  highToLow: string;
  lowToHigh: string;
  onSearchFocus?: () => void;
  /*
   * 手機瘦身（設計稿 §設計（手機）1）：caller 傳咗 `onOpenSortSheet` 先切去
   * 「搜尋框 ＋ 排序掣」一行版 —— chips／方向掣全部唔 render。
   * 冇傳嘅 caller 照出返 chips 版（拆走 chips 而又冇 sheet 就冇得排序）。
   */
  onOpenSortSheet?: () => void;
  sortSheetLabel?: string;
  /* 非預設排序／語言：掣右邊出一粒 --accent 點 */
  sortSheetActive?: boolean;
  /* 手機框內計數（「148 張」）。桌面唔傳就跌返 `resultLabel`（「148 / 1599」）——
     Phase C 拆走咗獨立一行 `.explore-count`，兩邊而家同一個 slot。 */
  inlineCountLabel?: string | null;
  /* 讀屏文案，同視覺計數分開（見 ANNOUNCE_DEBOUNCE_MS） */
  announceLabel?: string | null;
  /* 非預設篩選 chip：撳 × 即刻寫返 URL（一粒 chip 一次 update） */
  filterChips?: Array<{ key: string; label: string; removeLabel: string; onRemove: () => void }>;
  /* 搜尋模式先釘住 toolbar */
  sticky?: boolean;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const { locale } = useMarketSettings();
  const t = copy[locale];
  const isMobileBar = useMediaQuery(MOBILE_BAR_QUERY);
  const lean = isMobileBar && Boolean(onOpenSortSheet);
  /* 計數永遠喺框內（設計稿 §設計（電腦））：手機用短文案，桌面用「{shown} / {total}」。 */
  const inlineCount = lean ? inlineCountLabel ?? resultLabel : resultLabel;

  /* input 值行 local state；URL q 只係 debounce 之後嘅副本。
     lastCommitted 記住「我自己寫出去嘅值」——URL 彈返嚟同佢一樣就唔好覆蓋緊打嘅字；
     唔一樣（back/forward、外部清除）先同步入 input。 */
  const [text, setText] = useState(query);
  const lastCommitted = useRef(query);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const cancelPending = useCallback(() => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
  }, []);

  const commit = useCallback((value: string) => {
    cancelPending();
    /* update() 會將全空白當冇 q，所以 committed 值都跟住 normalise，唔會回彈時清走用戶啱啱打嘅空格 */
    lastCommitted.current = value.trim() ? value : "";
    startTransition(() => onQueryChange(value));
  }, [cancelPending, onQueryChange]);

  useEffect(() => {
    if (query === lastCommitted.current) return;
    lastCommitted.current = query;
    setText(query);
  }, [query]);

  useEffect(() => cancelPending, [cancelPending]);

  /* 播報值滯後一拍：文字未定就唔好入 live region（入咗即刻播）。 */
  const liveText = announceLabel ?? resultLabel ?? null;
  const [announced, setAnnounced] = useState<string | null>(null);
  useEffect(() => {
    const id = setTimeout(() => setAnnounced(liveText), ANNOUNCE_DEBOUNCE_MS);
    return () => clearTimeout(id);
  }, [liveText]);

  const onInput = (value: string) => {
    setText(value);
    cancelPending();
    timer.current = setTimeout(() => commit(value), QUERY_DEBOUNCE_MS);
  };

  const clear = () => {
    setText("");
    commit("");
  };

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "/" || event.metaKey || event.ctrlKey || event.altKey || event.isComposing) return;
      if (isEditableTarget(event.target)) return;
      event.preventDefault();
      inputRef.current?.focus();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <div className={`explore-bar${lean ? " explore-bar-lean" : ""}${lean && sticky ? " explore-bar-sticky" : ""}`}>
      <div className="explore-search">
        <label className="explore-search-field">
        <span className="sr-only">{searchLabel}</span>
        <input
          ref={inputRef}
          type="search"
          inputMode="search"
          enterKeyHint="search"
          value={text}
          placeholder={placeholder}
          autoComplete="off"
          autoCorrect="off"
          autoCapitalize="none"
          spellCheck={false}
          onChange={(event) => onInput(event.target.value)}
          onFocus={onSearchFocus}
          onKeyDown={(event) => {
            if (event.key !== "Escape") return;
            event.preventDefault();
            clear();
            event.currentTarget.blur();
          }}
        />
        </label>
        {/* 計數入框（設計稿 #8）——以前佔獨立一行，出現／消失令列表跳 52px */}
        {inlineCount ? <span className="explore-inline-count">{inlineCount}</span> : null}
        {text ? (
          <button type="button" className="explore-clear" aria-label={clearLabel} onClick={clear}>
            <X aria-hidden="true" size={14} strokeWidth={2.2} />
          </button>
        ) : (
          /* 「/」快捷鍵提示：CSS 只喺 (hover: hover) 顯示、focus 時收起 */
          <kbd className="explore-kbd" aria-hidden="true">/</kbd>
        )}
      </div>
      {lean && onOpenSortSheet ? (
        <button
          type="button"
          className="explore-sort-trigger"
          aria-haspopup="dialog"
          onClick={() => { tap.select(); onOpenSortSheet(); }}
        >
          <ChevronsUpDown aria-hidden="true" size={13} strokeWidth={2.2} />
          <span>{sortSheetLabel ?? t.labels.sortBy}</span>
          {sortSheetActive ? <span className="explore-sort-dot" aria-hidden="true" /> : null}
        </button>
      ) : null}
      {/* chips 冇 role="group"：每粒自己個 aria-label 已經係「移除 X」，再加個組名只會讀多一次 */}
      {lean && filterChips?.length ? (
        <div className="explore-active-chips">
          {filterChips.map((chip) => (
            <button
              key={chip.key}
              type="button"
              aria-label={chip.removeLabel}
              onClick={() => { tap.select(); chip.onRemove(); }}
            >
              <span>{chip.label}</span>
              <X aria-hidden="true" size={12} strokeWidth={2.4} />
            </button>
          ))}
        </div>
      ) : null}
      {/* 讀屏播報同視覺計數分開：呢個 region 唔會跟 debounce 每次寫 URL 就播（見 ANNOUNCE_DEBOUNCE_MS） */}
      <p className="sr-only" role="status" aria-live="polite">{announced}</p>
      {!lean ? (
      <div className="explore-sort-chips" role="group" aria-label={t.labels.sortBy}>
        {sortKeys.map((item) => {
          const active = sort === item.key;
          const Caret = !active ? ChevronsUpDown : dir === "asc" ? ArrowUp : ArrowDown;
          return (
            <button
              key={item.key}
              type="button"
              aria-pressed={active}
              onClick={() => { tap.select(); onSort(item.key); }}
            >
              <span>{item.label}</span>
              {item.key !== "rank" && (
                <Caret aria-hidden="true" className={`sort-caret${active ? " sort-caret-active" : ""}`} size={11} strokeWidth={2.4} />
              )}
            </button>
          );
        })}
        {sort !== "rank" && (
          <button
            type="button"
            className="explore-dir"
            onClick={() => { tap.select(); onSort(sort); }}
            aria-label={dir === "asc" ? lowToHigh : highToLow}
          >
            {dir === "asc" ? <ArrowUp aria-hidden="true" size={13} strokeWidth={2.2} /> : <ArrowDown aria-hidden="true" size={13} strokeWidth={2.2} />}
            <span>{dir === "asc" ? lowToHigh : highToLow}</span>
          </button>
        )}
      </div>
      ) : null}
    </div>
  );
}
