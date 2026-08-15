"use client";

import { useEffect, useRef, type ReactNode } from "react";
import { ArrowDown, ArrowUp } from "lucide-react";
import type { SortDir } from "@/lib/list-explore";

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
  const Icon = dir === "asc" ? ArrowUp : ArrowDown;
  return (
    <th className={className} aria-sort={ariaSort}>
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

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "/") return;
      const target = event.target as HTMLElement | null;
      if (target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable)) return;
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
          value={query}
          placeholder={placeholder}
          autoComplete="off"
          spellCheck={false}
          onChange={(event) => onQueryChange(event.target.value)}
        />
        {query ? (
          <button type="button" className="explore-clear" onClick={() => onQueryChange("")}>
            {clearLabel}
          </button>
        ) : null}
      </label>
      {resultLabel ? <p className="explore-count">{resultLabel}</p> : null}
      <div className="explore-sort-chips" role="group" aria-label={searchLabel}>
        {sortKeys.map((item) => (
          <button
            key={item.key}
            type="button"
            aria-pressed={sort === item.key}
            onClick={() => onSort(item.key)}
          >
            {item.label}
          </button>
        ))}
        {sort !== "rank" && (
          <button
            type="button"
            className="explore-dir"
            onClick={() => onSort(sort)}
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
