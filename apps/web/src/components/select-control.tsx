"use client";

import { Check, ChevronDown } from "lucide-react";
import { useCallback, useEffect, useId, useRef, useState } from "react";
import { tap } from "@/lib/haptic";

interface SelectControlProps<T extends string> {
  value: T;
  options: readonly T[];
  labels: Record<T, string>;
  onChange: (value: T) => void;
  label: string;
  icon?: React.ReactNode;
  className?: string;
  /* 選項文字本身係外語（語言名「日本語」「한국어」）就俾 BCP-47 tag，讀屏先會用啱把聲 */
  optionLang?: Partial<Record<T, string>>;
  /* 額外落喺 <ul> 嘅 class（例：貨幣選單要 scroll + 闊啲），`.select-menu` 個殼唔郁 */
  menuClassName?: string;
  /* 有 groupOf 就喺每組頭插一行 heading（`role="presentation"`，唔入 option 序號）；
     回 null / undefined = 呢個 option 唔屬任何組（例：釘咗喺頂嘅 USD），唔出 heading */
  groupOf?: (item: T) => string | null | undefined;
  groupLabels?: Record<string, string>;
  /* 自訂一個 option 嘅內容（符號 / code / 名）。冇就出返 `labels[item]`。 */
  renderOption?: (item: T) => React.ReactNode;
}

/* 收埋 menu 前留 120ms 俾 `.select-menu-exit` 做退場動畫（CSS 由 globals.css 負責） */
const MENU_EXIT_MS = 120;

export function SelectControl<T extends string>({ value, options, labels, onChange, label, icon, className, optionLang, menuClassName, groupOf, groupLabels, renderOption }: SelectControlProps<T>) {
  const [open, setOpen] = useState(false);
  const [closing, setClosing] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);
  const rootRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const exitTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const baseId = useId();
  const listboxId = `${baseId}-list`;
  const labelId = `${baseId}-label`;
  const valueId = `${baseId}-value`;

  const clearExit = useCallback(() => {
    if (exitTimer.current) clearTimeout(exitTimer.current);
    exitTimer.current = null;
  }, []);

  const close = useCallback(() => {
    setOpen(false);
    setClosing(true);
    clearExit();
    exitTimer.current = setTimeout(() => {
      setClosing(false);
      exitTimer.current = null;
    }, MENU_EXIT_MS);
  }, [clearExit]);

  const toggle = (next: boolean) => {
    if (!next) { close(); return; }
    /* 退場途中再開：即刻取消退場，唔好等 timer 拆走個 menu */
    clearExit();
    setClosing(false);
    setOpen(true);
    setActiveIndex(Math.max(0, options.indexOf(value)));
  };

  useEffect(() => clearExit, [clearExit]);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) close();
    };
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [close, open]);

  const select = (item: T) => {
    tap.select();
    onChange(item);
    close();
    buttonRef.current?.focus();
  };

  const onKeyDown = (event: React.KeyboardEvent) => {
    if (!open) {
      if (event.key === "ArrowDown" || event.key === "ArrowUp" || event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        toggle(true);
      }
      return;
    }
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActiveIndex((index) => (index + 1) % options.length);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActiveIndex((index) => (index - 1 + options.length) % options.length);
    } else if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      select(options[activeIndex]);
    } else if (event.key === "Escape") {
      event.preventDefault();
      close();
      buttonRef.current?.focus();
    } else if (event.key === "Home") {
      event.preventDefault();
      setActiveIndex(0);
    } else if (event.key === "End") {
      event.preventDefault();
      setActiveIndex(options.length - 1);
    }
  };

  /* 鍵盤行到見唔到嘅選項就捲入視窗。`block: "nearest"` 喺已經見到嗰陣係 no-op，
     所以 pointer hover 改 activeIndex 唔會扯到個 list 跳。 */
  useEffect(() => {
    if (!open) return;
    document.getElementById(`${listboxId}-${activeIndex}`)?.scrollIntoView({ block: "nearest" });
  }, [open, activeIndex, listboxId]);

  const activeId = `${listboxId}-${activeIndex}`;
  const menuVisible = open || closing;

  return (
    <div ref={rootRef} className={`select-control ${className ?? ""}`.trim()}>
      {icon}
      {/* combobox pattern：焦點留喺 trigger，activedescendant 指住 listbox 入面高亮嗰個；
          讀屏會讀「Language, English」——label 收埋、value 見得到。 */}
      <span id={labelId} className="sr-only">{label}</span>
      <button
        ref={buttonRef}
        type="button"
        role="combobox"
        aria-labelledby={`${labelId} ${valueId}`}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listboxId : undefined}
        aria-activedescendant={open ? activeId : undefined}
        className="select-trigger"
        onClick={() => toggle(!open)}
        onKeyDown={onKeyDown}
      >
        <span id={valueId} lang={optionLang?.[value]}>{labels[value]}</span>
        <ChevronDown aria-hidden="true" className="select-chevron" data-open={open} size={12} strokeWidth={2} />
      </button>
      {menuVisible && (
        <ul
          className={`select-menu${closing ? " select-menu-exit" : ""}${menuClassName ? ` ${menuClassName}` : ""}`}
          role="listbox"
          id={listboxId}
          aria-labelledby={labelId}
          aria-hidden={closing || undefined}
        >
          {/* 分組 heading 唔佔 option 序號：`index` 仍然係 options 嘅扁平位置，
              所以 id / activeIndex / 鍵盤上下同冇分組嗰陣一模一樣。 */}
          {options.flatMap((item, index) => {
            const group = groupOf?.(item) ?? null;
            const nodes: React.ReactNode[] = [];
            if (group !== null && (index === 0 || (groupOf?.(options[index - 1]) ?? null) !== group)) {
              nodes.push(
                <li key={`g-${group}`} role="presentation" className="select-group-label">{groupLabels?.[group] ?? group}</li>,
              );
            }
            nodes.push(
              <li
                key={item}
                id={`${listboxId}-${index}`}
                role="option"
                lang={optionLang?.[item]}
                aria-selected={item === value}
                data-active={index === activeIndex}
                className="select-option"
                onPointerEnter={() => setActiveIndex(index)}
                onClick={() => select(item)}
              >
                {renderOption ? renderOption(item) : <span>{labels[item]}</span>}
                {item === value && <Check aria-hidden="true" size={12} strokeWidth={2.2} className="select-check" />}
              </li>,
            );
            return nodes;
          })}
        </ul>
      )}
    </div>
  );
}
