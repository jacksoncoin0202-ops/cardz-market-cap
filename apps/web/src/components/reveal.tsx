"use client";

import { useEffect, useRef, type ReactNode } from "react";
import "@/app/styles/reveal.css";

/*
 * FE05 WS3 —— 全站**唯一**一個 IntersectionObserver（DESIGN.md §4.3）。
 * module 級 lazy 開，第一個 subscriber 先 new；之後所有 <Reveal> 同 history-chart
 * 嘅 draw-in 共用同一個 instance。每個 target 播一次就 unobserve（once）。
 * IO 之外仲有一個共用嘅 rAF scroll sweep 兜底（原因見下面 attachSweep 嗰段），
 * 一樣係全站一個，pendingTargets 空咗就自己拆走。
 *
 * SSR 契約：呢個 component **唔會**喺 server render 加任何隱藏 class。
 * 全部判斷喺 mount 之後嘅 effect 入面行，所以 view-source / 唔行 JS 嘅 AI crawler
 * 見到嘅永遠係 opacity 1 嘅完整內容 —— 卡頁同 hub 頁係 GEO 主力頁，
 * 「動畫未觸發 = 一片空白」呢種做法喺呢個 repo 係硬禁。
 */

/* 手機靜態、桌面先加戲（DESIGN.md §3.3，同 .fade-up-desktop 同一個斷點） */
const DESKTOP_QUERY = "(min-width: 981px)";
const REDUCED_QUERY = "(prefers-reduced-motion: reduce)";
/* 上下各縮 10%：真係入到視窗先播，唔係啱啱擦到邊就開始 */
const ROOT_MARGIN = "-10% 0px -10% 0px";

let observer: IntersectionObserver | null = null;
const pendingTargets = new Map<Element, () => void>();

function fire(el: Element) {
  const run = pendingTargets.get(el);
  if (!run) return;
  pendingTargets.delete(el);
  observer?.unobserve(el);
  run();
}

function sharedObserver(): IntersectionObserver {
  if (observer) return observer;
  observer = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      if (!entry.isIntersecting) continue;
      fire(entry.target);
    }
  }, { rootMargin: ROOT_MARGIN });
  return observer;
}

/*
 * 為咗兜 IO 一個**唔係 bug 嘅硬限制**：IO 淨係喺 threshold 跨界嗰陣先派 entry。
 * 一下 trackpad fling（或者 PageDown ×2、async scroll restoration）由「元素喺視窗
 * 下面（ratio 0）」直接跳到「元素喺視窗上面（ratio 0）」，ratio 由頭到尾冇變過 →
 * callback 一次都唔 fire，個 section 就永遠停喺 opacity 0。
 * 實測：卡頁 @1280 一下 `wheel(0,1400)`，`.detail-metrics`（市值 / PSA 10 價 / 鑑定數量）
 * 由頭黑到落底，4/4 重現。喺 IO callback 度加「已經捲過咗頭」條件係**冇用**嘅
 * （試過，一樣 opacity 0）—— 因為根本冇 callback。
 *
 * 所以要一個 rAF 節流嘅 scroll sweep 兜底：全站共用一個 listener（唔係一個 component
 * 一個），pendingTargets 一空就自己拆走，即係首屏之後好快就冇任何 listener 掛住。
 */
const REVEAL_LINE = 0.9; /* 同 ROOT_MARGIN 下邊嗰 -10% 對齊 */
let sweepAttached = false;
let sweepQueued = false;

function sweep() {
  sweepQueued = false;
  const line = window.innerHeight * REVEAL_LINE;
  for (const el of [...pendingTargets.keys()]) {
    /* top 過咗條線就播 —— 包括「啱啱入到視窗」同「一下跳咗過頭」兩種情況 */
    if (el.getBoundingClientRect().top < line) fire(el);
  }
  if (!pendingTargets.size) detachSweep();
}

function queueSweep() {
  if (sweepQueued) return;
  sweepQueued = true;
  requestAnimationFrame(sweep);
}

function attachSweep() {
  if (sweepAttached) return;
  sweepAttached = true;
  window.addEventListener("scroll", queueSweep, { passive: true });
  window.addEventListener("resize", queueSweep, { passive: true });
}

function detachSweep() {
  if (!sweepAttached) return;
  sweepAttached = false;
  window.removeEventListener("scroll", queueSweep);
  window.removeEventListener("resize", queueSweep);
}

/*
 * 三個閘全部喺 mount 之後行，唔夠一個就完全唔做嘢（唔加 class、唔 observe）：
 *   1. reduced-motion → no-op
 *   2. <981px → no-op（手機第一屏要即刻見到內容）
 *   3. 已經喺視窗入面 → no-op。首屏永遠唔准動；而且「見到咗先由頭播」＝
 *      由現狀跳返起點嗰下閃，比冇動畫更差。
 * 回傳 cleanup（有 arm 先有）；`onReveal` 保證最多 call 一次。
 */
export function revealOnce(el: Element, onReveal: () => void): (() => void) | undefined {
  if (typeof window === "undefined" || typeof IntersectionObserver === "undefined") return;
  if (window.matchMedia(REDUCED_QUERY).matches) return;
  if (!window.matchMedia(DESKTOP_QUERY).matches) return;
  if (el.getBoundingClientRect().top < window.innerHeight) return;
  const io = sharedObserver();
  pendingTargets.set(el, onReveal);
  io.observe(el);
  attachSweep();
  return () => {
    pendingTargets.delete(el);
    io.unobserve(el);
    if (!pendingTargets.size) detachSweep();
  };
}

/* 只開放真係用得着嘅 block 級 tag：<Reveal> 唔應該包住 inline 或者 list item */
type RevealTag = "div" | "section" | "article" | "aside" | "header";

/*
 * 用法：`<Reveal as="section" className="detail-metrics">` —— 有現成 section 就直接
 * 由佢做 reveal target（零多餘 DOM）；冇就退返一個 <div> wrapper。
 * 每頁 ≤ 8 個、section 級；ranking row / heatmap tile / sparkline 一律唔准（DESIGN.md §4）。
 */
export function Reveal({ as = "div", className, children, ...rest }: {
  as?: RevealTag;
  className?: string;
  id?: string;
  "aria-label"?: string;
  "aria-labelledby"?: string;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    /*
     * 播完就拆晒兩個 class（同 chart 個 data-draw="done" 一樣係「入場係一次性、
     * 完咗要清場」）：`fade-up ... both` 會永遠留低 `transform: matrix(1,0,0,1,0,0)`，
     * 一個非 none 嘅 transform = 成個 section 變咗 position:fixed 後代嘅 containing
     * block + stacking context，將來喺入面擺 popover / menu 會由零查起。
     * animationend 會冒泡，所以要認住 target 同 keyframe 名先落手。
     */
    let onEnd: ((event: AnimationEvent) => void) | undefined;
    const stop = revealOnce(el, () => {
      el.classList.add("reveal-in");
      onEnd = (event) => {
        if (event.target !== el || event.animationName !== "fade-up") return;
        el.removeEventListener("animationend", onEnd!);
        onEnd = undefined;
        el.classList.remove("reveal-pending", "reveal-in");
      };
      el.addEventListener("animationend", onEnd);
    });
    /* 冇 arm（reduced / 手機 / 已經睇到）就一個 class 都唔加，元素維持 opacity 1 */
    if (!stop) return;
    el.classList.add("reveal-pending");
    return () => {
      stop();
      if (onEnd) el.removeEventListener("animationend", onEnd);
      el.classList.remove("reveal-pending", "reveal-in");
    };
  }, []);
  const Tag = as as "div";
  return <Tag ref={ref} className={className} {...rest}>{children}</Tag>;
}
