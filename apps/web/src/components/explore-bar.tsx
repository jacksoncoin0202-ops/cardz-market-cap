"use client";

import { startTransition, useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { ArrowDown, ArrowUp, ChevronsUpDown, X } from "lucide-react";
import { tap } from "@/lib/haptic";
import { copy } from "@/lib/i18n";
import type { SortDir } from "@/lib/list-explore";
import { useMarketSettings } from "@/lib/use-market-settings";

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
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const { locale } = useMarketSettings();
  const t = copy[locale];

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
    <div className="explore-bar">
      <label className="explore-search">
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
          onKeyDown={(event) => {
            if (event.key !== "Escape") return;
            event.preventDefault();
            clear();
            event.currentTarget.blur();
          }}
        />
        {text ? (
          <button type="button" className="explore-clear" aria-label={clearLabel} onClick={clear}>
            <X aria-hidden="true" size={14} strokeWidth={2.2} />
          </button>
        ) : (
          /* 「/」快捷鍵提示：CSS 只喺 (hover: hover) 顯示、focus 時收起 */
          <kbd className="explore-kbd" aria-hidden="true">/</kbd>
        )}
      </label>
      {/* 常駐 live region：篩選數字改變會被讀屏播報；空時靠 CSS :empty 收埋個 gap */}
      <p className="explore-count" role="status" aria-live="polite">{resultLabel ?? null}</p>
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
    </div>
  );
}
