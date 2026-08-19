"use client";

import Link from "next/link";
import { useEffect, useId, useRef, useState } from "react";
import { Search, X } from "lucide-react";
import { handleCardImageError } from "./card-image";
import { loadCatalog, prefetchCatalog } from "@/lib/catalog-client";
import { CATALOG_HEADER_CAP, displayCatalogName, searchCatalog } from "@/lib/catalog-search";
import { tap } from "@/lib/haptic";
import { copy } from "@/lib/i18n";
import { useMarketSettings } from "@/lib/use-market-settings";
import type { CatalogEntry } from "@/lib/types";

function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || target.isContentEditable;
}

export function SiteSearch() {
  const { locale, href } = useMarketSettings();
  const t = copy[locale];
  const listId = useId();
  const rootRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [entries, setEntries] = useState<CatalogEntry[] | null>(null);
  const [active, setActive] = useState(0);

  const hits = text.trim() && entries
    ? searchCatalog(entries, text, locale, { limit: CATALOG_HEADER_CAP })
    : [];
  const noResults = Boolean(text.trim()) && !hits.length;

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    loadCatalog()
      .then((payload) => {
        if (!cancelled) setEntries(payload.entries);
      })
      .catch(() => {
        if (!cancelled) setEntries([]);
      });
    return () => {
      cancelled = true;
    };
  }, [open]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 打字重設 highlight：改成 derived state 會令鍵盤上下鍵嘅選擇被每次 render 沖走，係行為改動唔係 lint 修。
    setActive(0);
  }, [text]);

  useEffect(() => {
    if (!open) return;
    const onPointer = (event: PointerEvent) => {
      if (rootRef.current?.contains(event.target as Node)) return;
      setOpen(false);
    };
    document.addEventListener("pointerdown", onPointer);
    return () => document.removeEventListener("pointerdown", onPointer);
  }, [open]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "/" || event.metaKey || event.ctrlKey || event.altKey || event.isComposing) return;
      if (isEditableTarget(event.target)) return;
      if (document.querySelector(".explore-search input")) return;
      event.preventDefault();
      prefetchCatalog();
      setOpen(true);
      queueMicrotask(() => inputRef.current?.focus());
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const close = () => {
    setOpen(false);
    setText("");
  };

  return (
    <div className="site-search" ref={rootRef} data-open={open ? "true" : "false"}>
      <button
        type="button"
        className="select-control site-search-toggle"
        aria-label={t.labels.searchLabel}
        aria-expanded={open}
        aria-controls={listId}
        title={t.labels.searchLabel}
        onClick={() => {
          tap.select();
          prefetchCatalog();
          setOpen((current) => {
            const next = !current;
            if (next) queueMicrotask(() => inputRef.current?.focus());
            return next;
          });
        }}
      >
        <Search aria-hidden="true" className="control-icon" strokeWidth={1.7} />
      </button>
      {open ? (
        <div className="site-search-panel" role="search">
          <label className="site-search-field">
            <span className="sr-only">{t.labels.searchLabel}</span>
            <input
              ref={inputRef}
              type="search"
              inputMode="search"
              enterKeyHint="search"
              value={text}
              placeholder={t.labels.searchPlaceholder}
              autoComplete="off"
              autoCorrect="off"
              autoCapitalize="none"
              spellCheck={false}
              aria-autocomplete="list"
              aria-controls={listId}
              aria-activedescendant={hits[active] ? `${listId}-${hits[active].kind}-${hits[active].id}` : undefined}
              onChange={(event) => setText(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Escape") {
                  event.preventDefault();
                  close();
                  return;
                }
                if (event.key === "ArrowDown" && hits.length) {
                  event.preventDefault();
                  setActive((index) => (index + 1) % hits.length);
                  return;
                }
                if (event.key === "ArrowUp" && hits.length) {
                  event.preventDefault();
                  setActive((index) => (index - 1 + hits.length) % hits.length);
                  return;
                }
                if (event.key === "Enter" && hits[active]) {
                  event.preventDefault();
                  window.location.assign(href(hits[active].href));
                }
              }}
            />
            {text ? (
              <button type="button" className="explore-clear" aria-label={t.labels.searchClear} onClick={() => setText("")}>
                <X aria-hidden="true" size={14} strokeWidth={2.2} />
              </button>
            ) : null}
          </label>
          {/*
            入圍門檻**未打字就要見到**（owner 2026-08-19：「一定有人問點解搵唔到嗰張卡」）。
            擺喺 empty state 度唔夠 —— 嗰陣人已經覺得個站壞咗。有結果嗰陣照出：搵到 A 卡
            嘅人下一秒就會搵 B 卡，規矩留喺原位好過閃走。
          */}
          {/* 搵唔到嗰陣唔出：下面 empty state 已經連「唔係故障」一齊講埋同一條規矩，
              兩句排住講同一件事會似 bug。 */}
          {!noResults ? <p className="site-search-note">{t.labels.searchPopRule}</p> : null}
          {noResults ? (
            <div className="site-search-empty">
              <p>{t.labels.noSearchResults}</p>
              <p className="empty-state-hint">{t.labels.searchUnqualified}</p>
            </div>
          ) : null}
          {hits.length ? (
            <ul className="site-search-hits" id={listId} role="listbox">
              {hits.map((entry, index) => {
                const name = displayCatalogName(entry, locale, t.status.unavailable);
                const rankLabel = entry.kind === "box"
                  ? t.nav.box
                  : entry.marketRank > 0 ? `#${entry.marketRank}` : t.labels.awaitingFreshPrice;
                return (
                  <li key={`${entry.kind}:${entry.id}`} role="presentation">
                    <Link
                      id={`${listId}-${entry.kind}-${entry.id}`}
                      role="option"
                      aria-selected={index === active}
                      className="site-search-hit"
                      href={href(entry.href)}
                      onClick={() => close()}
                      onMouseEnter={() => setActive(index)}
                    >
                      <span className="ranking-thumb">
                        {/* `.ranking-thumb` 個框 CSS 已經釘死 42×60（手機 52×72）、img `width/height:100%`，
                            所以呢度冇 CLS 風險，唔使補 intrinsic dims。要補嘅係 lazy：搜尋結果可以拉到好長，
                            預設 eager 會即刻拉晒每一格嘅縮圖。 */}
                        <img src={entry.image.url} alt="" loading="lazy" decoding="async" onError={handleCardImageError} />
                      </span>
                      <span className="catalog-hit-copy">
                        <strong>{name}</strong>
                        <span className="catalog-hit-meta">
                          <span>{rankLabel}</span>
                          {entry.collectorNumber ? <span>{entry.collectorNumber}</span> : null}
                          <span>{entry.tcg}</span>
                        </span>
                      </span>
                    </Link>
                  </li>
                );
              })}
            </ul>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
