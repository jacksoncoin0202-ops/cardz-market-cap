"use client";

import { Check, ChevronDown } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";

interface SelectControlProps<T extends string> {
  value: T;
  options: readonly T[];
  labels: Record<T, string>;
  onChange: (value: T) => void;
  label: string;
  icon?: React.ReactNode;
  className?: string;
}

export function SelectControl<T extends string>({ value, options, labels, onChange, label, icon, className }: SelectControlProps<T>) {
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);
  const rootRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const listboxId = useId();

  const toggle = (next: boolean) => {
    setOpen(next);
    if (next) setActiveIndex(Math.max(0, options.indexOf(value)));
  };

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [open]);

  const select = (item: T) => {
    onChange(item);
    setOpen(false);
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
      setOpen(false);
      buttonRef.current?.focus();
    } else if (event.key === "Home") {
      event.preventDefault();
      setActiveIndex(0);
    } else if (event.key === "End") {
      event.preventDefault();
      setActiveIndex(options.length - 1);
    }
  };

  const activeId = `${listboxId}-${activeIndex}`;

  return (
    <div ref={rootRef} className={`select-control ${className ?? ""}`.trim()}>
      {icon}
      <button
        ref={buttonRef}
        type="button"
        aria-label={label}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listboxId : undefined}
        className="select-trigger"
        onClick={() => toggle(!open)}
        onKeyDown={onKeyDown}
      >
        <span>{labels[value]}</span>
        <ChevronDown aria-hidden="true" className="select-chevron" data-open={open} size={12} strokeWidth={2} />
      </button>
      {open && (
        <ul className="select-menu" role="listbox" id={listboxId} aria-label={label} aria-activedescendant={activeId}>
          {options.map((item, index) => (
            <li
              key={item}
              id={`${listboxId}-${index}`}
              role="option"
              aria-selected={item === value}
              data-active={index === activeIndex}
              className="select-option"
              onPointerEnter={() => setActiveIndex(index)}
              onClick={() => select(item)}
            >
              <span>{labels[item]}</span>
              {item === value && <Check aria-hidden="true" size={12} strokeWidth={2.2} className="select-check" />}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
