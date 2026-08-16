"use client";

import { useEffect, useRef, useState } from "react";

/* moumen ticket-ticker 概念：數字由 0 滾去目標值，ease-out 漸停，唔係閃變。
   初始 state 用真實值，唔用 0 —— server render 同無 JS 訪客（連爬蟲）見到嘅
   係真市值；由 0 起跳嘅動畫喺 useEffect 入面行，即係只喺 hydrate 之後先發生。 */
export function CapTicker({ value, format }: { value: number; format: (n: number) => string }) {
  const [display, setDisplay] = useState(value);
  const rafRef = useRef<number | null>(null);
  const firedRef = useRef(false);

  useEffect(() => {
    /* 淨係首次 mount 滾一次；之後 value 更新直接顯示新值，唔再重播動畫 */
    if (firedRef.current) {
      setDisplay(value);
      return;
    }
    firedRef.current = true;
    const start = performance.now();
    const duration = 900;
    const from = 0;
    const tick = (now: number) => {
      const p = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(1 - p, 3);
      setDisplay(from + (value - from) * eased);
      if (p < 1) rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
    return () => { if (rafRef.current) cancelAnimationFrame(rafRef.current); };
  }, [value]);

  return <span className="cap-ticker" aria-label={format(value)}>{format(display)}</span>;
}
