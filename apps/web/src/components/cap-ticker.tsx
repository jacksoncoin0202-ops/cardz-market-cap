"use client";

import { useLayoutEffect, useRef } from "react";
import { tickerEase, tickerStart } from "@/lib/ticker-start";

/* moumen ticket-ticker 概念：數字由 0 滾去目標值，ease-out 漸停，唔係閃變。
   JSX 出真實值，唔用 0 —— server render 同無 JS 訪客（連爬蟲）見到嘅係真市值；
   動畫喺 layout effect 入面用 ref 直寫 textContent（唔係每 frame setState，
   唔會令 parent / 兄弟跟住 re-render），即係只喺 hydrate 之後先發生。
   之後 value 再變（heatmap 拉 slider / 換期間）由「而家顯示緊嘅值」滾去新值，
   唔會跳字；prefers-reduced-motion 直接落最終值。 */
const FIRST_RUN_MS = 900;
const RETARGET_MS = 420;

export function CapTicker({ value, format }: { value: number; format: (n: number) => string }) {
  const ref = useRef<HTMLSpanElement>(null);
  const rafRef = useRef<number | null>(null);
  const shownRef = useRef<number | null>(null); // 而家 DOM 顯示緊嘅數（null = 未滾過）
  const formatRef = useRef(format);
  useLayoutEffect(() => { formatRef.current = format; }); // 排喺動畫 effect 前面，同一 commit 先更新

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    /* React 只 own 個 text node；我哋改 nodeValue（唔用 textContent 換 node），
       React 下次 diff 仲揾得返佢。 */
    const write = (n: number) => {
      const text = formatRef.current(n);
      const node = el.firstChild;
      if (node && node.nodeType === Node.TEXT_NODE) node.nodeValue = text;
      else el.textContent = text;
    };
    if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const from = tickerStart(shownRef.current, value, formatRef.current);
    if (reduced || from === value) {
      shownRef.current = value;
      write(value);
      return;
    }
    const duration = shownRef.current === null ? FIRST_RUN_MS : RETARGET_MS;
    const start = performance.now();
    write(from);
    const tick = (now: number) => {
      const eased = tickerEase(now - start, duration);
      const current = eased >= 1 ? value : from + (value - from) * eased;
      shownRef.current = current;
      write(current);
      rafRef.current = eased < 1 ? requestAnimationFrame(tick) : null;
    };
    rafRef.current = requestAnimationFrame(tick);
    return () => {
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    };
  }, [value]);

  return <span ref={ref} className="cap-ticker" aria-label={format(value)}>{format(value)}</span>;
}
