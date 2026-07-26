"use client";

/* interactionkit Tooltip 移植（https://github.com/armondschneider/interactionkit, MIT）：
   hover/focus 開、mouseleave 80ms delay 收、touch tap toggle、AnimatePresence 過場。
   表頭 cell 有 overflow hidden → bubble 用 position: fixed 對準 trigger。 */
import { AnimatePresence, motion } from "framer-motion";
import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";

const CLOSE_DELAY = 80;

export function Tooltip({ label, text, side = "bottom" }: { label: string; text: string; side?: "top" | "bottom" }) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null);
  const rootRef = useRef<HTMLSpanElement>(null);
  const bubbleRef = useRef<HTMLSpanElement>(null);
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const tooltipId = useId();

  const cancelClose = () => {
    if (closeTimer.current) { clearTimeout(closeTimer.current); closeTimer.current = null; }
  };
  const scheduleClose = () => {
    cancelClose();
    closeTimer.current = setTimeout(() => setOpen(false), CLOSE_DELAY);
  };

  useEffect(() => () => cancelClose(), []);

  useLayoutEffect(() => {
    if (!open || !rootRef.current) return;
    const anchor = rootRef.current.getBoundingClientRect();
    const gap = 8;
    const width = bubbleRef.current?.offsetWidth ?? 210;
    const height = bubbleRef.current?.offsetHeight ?? 60;
    let left = anchor.right - width + 8;
    left = Math.max(8, Math.min(left, window.innerWidth - width - 8));
    const top = side === "top" ? Math.max(8, anchor.top - height - gap) : anchor.bottom + gap;
    setPos({ left, top });
  }, [open, side]);

  useEffect(() => {
    if (!open) return;
    const onDown = (event: PointerEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") setOpen(false); };
    const onScroll = () => setOpen(false);
    document.addEventListener("pointerdown", onDown);
    document.addEventListener("keydown", onKey);
    window.addEventListener("scroll", onScroll, { once: true, passive: true });
    return () => {
      document.removeEventListener("pointerdown", onDown);
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("scroll", onScroll);
    };
  }, [open]);

  return (
    <span
      className="tooltip"
      ref={rootRef}
      onMouseEnter={() => { cancelClose(); setOpen(true); }}
      onMouseLeave={scheduleClose}
      onFocus={() => { cancelClose(); setOpen(true); }}
      onBlur={scheduleClose}
    >
      <button
        type="button"
        className="tooltip-trigger"
        aria-label={label}
        aria-expanded={open}
        aria-describedby={open ? tooltipId : undefined}
        onPointerDown={(event) => {
          if (event.pointerType !== "touch") return;
          event.preventDefault();
          event.stopPropagation();
          setOpen((value) => !value);
        }}
        onClick={(event) => { event.preventDefault(); event.stopPropagation(); }}
      >
        ?
      </button>
      <AnimatePresence mode="wait" initial={false}>
        {open && pos && (
          <motion.span
            key="bubble"
            id={tooltipId}
            role="tooltip"
            className="tooltip-bubble"
            ref={bubbleRef}
            style={{ left: pos.left, top: pos.top }}
            initial={{ opacity: 0, y: side === "top" ? 4 : -4, scale: 0.96, filter: "blur(4px)" }}
            animate={{ opacity: 1, y: 0, scale: 1, filter: "blur(0px)" }}
            exit={{ opacity: 0, y: side === "top" ? 4 : -4, scale: 0.96, filter: "blur(4px)" }}
            transition={{ duration: 0.18, ease: [0.16, 1, 0.3, 1] }}
          >
            {text}
          </motion.span>
        )}
      </AnimatePresence>
    </span>
  );
}
