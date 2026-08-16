"use client";

import { useEffect, useRef, type CSSProperties, type ReactNode } from "react";

/*
 * 卡圖 holo / tilt / spotlight 嘅 pointer 管線（FE05 WS2）。
 *
 * 三條硬規矩：
 * 1. **唔改 `card-image.tsx`。** 佢係 100 格 heatmap 嘅 hot path（`card-image.tsx:27`
 *    註解：每 render 建新 closure 會令 props 逐次唔同、`memo` 白做）。要加效果就喺外面
 *    包一層 wrapper，唔好加 prop。
 * 2. **transform 落 `.card-art` 呢個內層 wrapper，唔准落 `.detail-art`。**
 *    `.detail-art` 係定位／背景嗰層，喺桌面會 sticky；transform 落佢會整個 containing
 *    block 一齊郁。
 * 3. **一 frame 一次寫。** pointermove 淨係記低座標 + 排一個 rAF；rect 讀取同 CSS var
 *    寫入全部喺 rAF 入面做（讀→寫同一 frame，唔會逼出額外 layout）。
 *
 * 手機／粗指針／reduced-motion 完全唔 attach listener —— 唔係「attach 咗再唔做嘢」，
 * 係根本冇 listener（DESIGN.md §3.3：手機靜態、桌面先加戲）。
 */

/* 最大傾斜角。再大就會見到卡邊離開 mask，睇落似貼紙唔似卡。 */
const MAX_TILT_DEG = 6;

function isTiltAllowed(): boolean {
  if (typeof window === "undefined" || !window.matchMedia) return false;
  return window.matchMedia("(hover: hover) and (pointer: fine)").matches
    && !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

export function CardArt({ maskSrc, children }: { maskSrc: string; children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  /* fail-closed 嘅真正入口：`variants["600"]` 有機會係空字串（`??` 唔會 fall through
     落空字串），寫落去就變 `url("")` —— 呢個係合法 custom property value，CSS 側
     `var(--card-art-src, …)` 個 fallback 唔會 fire，瀏覽器反而會攞成版 HTML 當 mask。
     所以空／全空白一律**唔寫個 var**，等 CSS 落返 1×1 透明 GIF。 */
  const src = maskSrc?.trim() ? maskSrc.trim() : "";

  useEffect(() => {
    const el = ref.current;
    if (!el) return;

    const fine = window.matchMedia("(hover: hover) and (pointer: fine)");
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)");
    let detach: (() => void) | null = null;

    const attach = () => {
      if (detach) return;
      let frame = 0;
      let clientX = 0;
      let clientY = 0;

      const paint = () => {
        frame = 0;
        const rect = el.getBoundingClientRect();
        if (!rect.width || !rect.height) return;
        const px = Math.min(1, Math.max(0, (clientX - rect.left) / rect.width));
        const py = Math.min(1, Math.max(0, (clientY - rect.top) / rect.height));
        el.style.setProperty("--px", px.toFixed(4));
        el.style.setProperty("--py", py.toFixed(4));
        /* 上邊 = 向後仰，所以 tilt-x 同 py 反向；tilt-y 跟 px 同向。 */
        el.style.setProperty("--tilt-x", `${((0.5 - py) * 2 * MAX_TILT_DEG).toFixed(2)}deg`);
        el.style.setProperty("--tilt-y", `${((px - 0.5) * 2 * MAX_TILT_DEG).toFixed(2)}deg`);
      };

      const onMove = (event: PointerEvent) => {
        clientX = event.clientX;
        clientY = event.clientY;
        if (!frame) frame = requestAnimationFrame(paint);
      };
      const onEnter = (event: PointerEvent) => {
        el.dataset.artState = "active";
        onMove(event);
      };
      const onLeave = () => {
        if (frame) { cancelAnimationFrame(frame); frame = 0; }
        /* 先落 idle（短 transition + 拆 will-change 由 CSS 負責），再歸零；
           兩步喺同一個 task 入面，所以 transition 一定食到。 */
        el.dataset.artState = "idle";
        el.style.setProperty("--px", "0.5");
        el.style.setProperty("--py", "0.5");
        el.style.setProperty("--tilt-x", "0deg");
        el.style.setProperty("--tilt-y", "0deg");
      };

      el.addEventListener("pointerenter", onEnter);
      el.addEventListener("pointermove", onMove);
      el.addEventListener("pointerleave", onLeave);
      el.dataset.artState = "idle";
      detach = () => {
        if (frame) cancelAnimationFrame(frame);
        el.removeEventListener("pointerenter", onEnter);
        el.removeEventListener("pointermove", onMove);
        el.removeEventListener("pointerleave", onLeave);
        delete el.dataset.artState;
        el.style.removeProperty("--px");
        el.style.removeProperty("--py");
        el.style.removeProperty("--tilt-x");
        el.style.removeProperty("--tilt-y");
      };
    };

    const sync = () => {
      if (isTiltAllowed()) attach();
      else if (detach) { detach(); detach = null; }
    };

    sync();
    /* 用戶中途開系統「減少動態效果」、或者插／拔滑鼠，即刻跟住變。 */
    fine.addEventListener("change", sync);
    reduce.addEventListener("change", sync);
    return () => {
      fine.removeEventListener("change", sync);
      reduce.removeEventListener("change", sync);
      if (detach) detach();
    };
  }, []);

  return (
    <div
      ref={ref}
      className="card-art"
      /* mask 用同一張 _600 WebP：卡係透明畫布，sheen 只准喺卡形之內出（DESIGN.md §5）。
         SSR 就寫落去，所以未 hydrate 都已經有靜態 holo，唔會 hydrate 完先「著」。
         冇來源 = 唔出 attribute（見上面 `src`），fallback 交返 CSS。 */
      style={src ? ({ "--card-art-src": `url("${src}")` } as CSSProperties) : undefined}
    >
      {children}
    </div>
  );
}

/*
 * 相關卡格仔嘅 hover spotlight（桌面 only，冇 tilt）。
 * 一個 listener 掛喺容器度做 delegation —— 唔准每張卡自己 attach（一頁最多 18 張）。
 * 呢個 wrapper 只出一個冇樣式嘅 <div>，所以 `related-cards.tsx` 一行都唔使改。
 */
export function SpotlightScope({ children }: { children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const host = ref.current;
    if (!host || !isTiltAllowed()) return;

    let frame = 0;
    let target: HTMLElement | null = null;
    let clientX = 0;
    let clientY = 0;

    const paint = () => {
      frame = 0;
      if (!target) return;
      const rect = target.getBoundingClientRect();
      if (!rect.width || !rect.height) return;
      target.style.setProperty("--px", ((clientX - rect.left) / rect.width).toFixed(4));
      target.style.setProperty("--py", ((clientY - rect.top) / rect.height).toFixed(4));
    };

    const onMove = (event: PointerEvent) => {
      const next = (event.target as Element | null)?.closest<HTMLElement>(".related-group li a") ?? null;
      if (next !== target) {
        target?.style.removeProperty("--px");
        target?.style.removeProperty("--py");
        target = next;
      }
      if (!target) return;
      clientX = event.clientX;
      clientY = event.clientY;
      if (!frame) frame = requestAnimationFrame(paint);
    };
    const onLeave = () => {
      if (frame) { cancelAnimationFrame(frame); frame = 0; }
      target?.style.removeProperty("--px");
      target?.style.removeProperty("--py");
      target = null;
    };

    host.addEventListener("pointermove", onMove);
    host.addEventListener("pointerleave", onLeave);
    return () => {
      if (frame) cancelAnimationFrame(frame);
      host.removeEventListener("pointermove", onMove);
      host.removeEventListener("pointerleave", onLeave);
      onLeave();
    };
  }, []);

  return <div ref={ref} className="spotlight-scope">{children}</div>;
}
