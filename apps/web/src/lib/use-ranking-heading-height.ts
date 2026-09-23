"use client";

import { useEffect, type RefObject } from "react";

/*
 * 桌面榜單表頭釘喺 sticky 榜標題底下（FE05 A1，rankings.tsx 同 box-rankings.tsx 共用）。
 *
 * `.ranking-heading` 桌面係 sticky（top = --header-height），實高隨語言／闊度／掣數變
 * （實測 en 1440 闊 = 153.94px），所以唔可以喺 CSS 寫死。呢度量佢實高，寫落 parent
 * （`.rankings-section`）嘅 `--ranking-heading-h`；globals.css `.desktop-ranking-table thead th`
 * 用 `top: calc(var(--header-height) + var(--ranking-heading-h, 0px))`。
 *
 * 用 getBoundingClientRect 唔用 offsetHeight：offsetHeight 會 round（153.94 → 154），
 * 表頭同標題之間漏 0.06px 條縫，捲過嘅行會喺度閃。未量到（SSR／JS 未跑）= 0px：
 * 表頭釘喺 --header-height，匿喺 z-index 較高嘅標題後面，唔會浮喺半空。
 */
export function useRankingHeadingHeight(ref: RefObject<HTMLElement | null>): void {
  useEffect(() => {
    const heading = ref.current;
    const section = heading?.parentElement;
    if (!heading || !section) return;
    const write = () => section.style.setProperty("--ranking-heading-h", `${heading.getBoundingClientRect().height}px`);
    write();
    const observer = new ResizeObserver(write);
    observer.observe(heading, { box: "border-box" });
    return () => {
      observer.disconnect();
      section.style.removeProperty("--ranking-heading-h");
    };
  }, [ref]);
}
