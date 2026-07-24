"use client";

import { useLayoutEffect, useEffect, useRef, useState } from "react";

/* interactionkit tooltip 概念：自訂 bubble，click / long-press / focus 都開到；
   手機撳吓即開，撳出面即關，唔靠 :hover。
   bubble 用 position: fixed 對準 trigger（表頭 cell overflow hidden，absolute 會被裁） */
export function InfoTip({ label, text }: { label: string; text: string }) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null);
  const rootRef = useRef<HTMLSpanElement>(null);
  const bubbleRef = useRef<HTMLSpanElement>(null);

  useLayoutEffect(() => {
    if (!open || !rootRef.current) return;
    const anchor = rootRef.current.getBoundingClientRect();
    const width = bubbleRef.current?.offsetWidth ?? 210;
    const gap = 8;
    let left = anchor.right - width + 8;
    left = Math.max(8, Math.min(left, window.innerWidth - width - 8));
    setPos({ left, top: anchor.bottom + gap });
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onDown = (event: PointerEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") setOpen(false); };
    document.addEventListener("pointerdown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <span className="info-tip" ref={rootRef}>
      <button
        type="button"
        className="info-tip-trigger"
        aria-label={label}
        aria-expanded={open}
        onClick={(event) => { event.preventDefault(); event.stopPropagation(); setOpen((v) => !v); }}
      >
        ?
      </button>
      {open && pos && (
        <span className="info-tip-bubble" role="tooltip" ref={bubbleRef} style={{ left: pos.left, top: pos.top }}>
          {text}
        </span>
      )}
    </span>
  );
}
