"use client";

import { AnimatePresence, motion, useDragControls, type PanInfo } from "framer-motion";
import { useCallback, useEffect, useRef, useSyncExternalStore, type PointerEvent as ReactPointerEvent, type ReactNode, type RefObject } from "react";
import { createPortal } from "react-dom";
import { tap } from "@/lib/haptic";
import { lockScroll, unlockScroll } from "./scroll-lock";

/*
 * 共用 modal 層：手機 bottom sheet、desktop card dialog、heatmap tune panel 三個都行呢個。
 * 負責：AnimatePresence 入場／退場、scroll lock（<html>）、focus trap、Escape、
 * 關咗之後 focus 還返去 trigger、backdrop 撳空位關（pointerup + target===currentTarget）、
 * 手機拉落收（drag y）。外觀（class）由 caller 傳，CSS 唔郁。
 *
 * 唔用 <dialog>：iOS Safari 嘅 showModal 會自己 scroll lock + top-layer，
 * 同 framer 嘅 exit 動畫（unmount 先播）夾唔埋。
 */
export interface SheetProps {
  open: boolean;
  onClose: () => void;
  children: ReactNode;
  backdropClassName: string;
  panelClassName: string;
  /* `sheet`：<=900px 由底部 translateY(100%) 滑上嚟兼可拉落收；>900px 係 dialog（scale .98 + fade）。
     `panel`：固定角落嘅小 panel，slide/fade。 */
  variant: "sheet" | "panel";
  ariaLabelledBy?: string;
  ariaLabel?: string;
  /* 開嗰陣 focus 落邊個；冇就 focus panel 本身（讀屏會讀 dialog 標題） */
  initialFocusRef?: RefObject<HTMLElement | null>;
  /* 關咗之後 focus 還返去邊個；冇就還去開之前嘅 activeElement */
  returnFocusRef?: RefObject<HTMLElement | null>;
}

const mobileQuery = "(max-width: 900px)";
function subscribeMobile(onChange: () => void) {
  const media = window.matchMedia(mobileQuery);
  media.addEventListener("change", onChange);
  return () => media.removeEventListener("change", onChange);
}
const readMobile = () => window.matchMedia(mobileQuery).matches;
const serverMobile = () => false;

const subscribeNoop = () => () => {};
const readMounted = () => true;
const readUnmounted = () => false;

const EASE_OUT = [0.22, 1, 0.36, 1] as const;
const EASE_IN = [0.4, 0, 1, 1] as const;
const FOCUSABLE = "button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])";
/* 拉落幾多 / 幾快就當收（B5） */
const DRAG_CLOSE_OFFSET = 90;
const DRAG_CLOSE_VELOCITY = 500;

/* 覆蓋 globals.css 嘅 `animation: sheet-in / fade-in`：CSS animation 會壓過 inline transform，
   framer 嘅 exit 就播唔到。inline `animation:none` 一定贏 stylesheet。CSS 側清走之後可以刪。 */
const NO_CSS_ANIMATION = { animation: "none" } as const;

export function Sheet(props: SheetProps) {
  const mounted = useSyncExternalStore(subscribeNoop, readMounted, readUnmounted);
  if (!mounted) return null;
  return createPortal(
    <AnimatePresence initial={true}>
      {props.open ? <SheetLayer key="layer" {...props} /> : null}
    </AnimatePresence>,
    document.body,
  );
}

function SheetLayer({ onClose, children, backdropClassName, panelClassName, variant, ariaLabelledBy, ariaLabel, initialFocusRef, returnFocusRef }: SheetProps) {
  const isMobile = useSyncExternalStore(subscribeMobile, readMobile, serverMobile);
  const panelRef = useRef<HTMLElement>(null);
  const downOnBackdropRef = useRef(false);
  const dragControls = useDragControls();
  const onCloseRef = useRef(onClose);
  useEffect(() => { onCloseRef.current = onClose; }, [onClose]);

  /* scroll lock + focus trap + Escape + focus return。cleanup 喺 exit 動畫播完（真正 unmount）先行。
     dev StrictMode 會 mount→cleanup→mount 行多次：還 focus 嘅 rAF 要俾第二次 mount 取消，
     previouslyFocused 只記第一次（唔係第二次 mount 見到嘅 close button）。 */
  const previouslyFocusedRef = useRef<HTMLElement | null>(null);
  const returnRafRef = useRef<number | null>(null);
  useEffect(() => {
    if (returnRafRef.current !== null) { cancelAnimationFrame(returnRafRef.current); returnRafRef.current = null; }
    if (!previouslyFocusedRef.current) previouslyFocusedRef.current = document.activeElement as HTMLElement | null;
    const previouslyFocused = previouslyFocusedRef.current;
    lockScroll();
    tap.open();
    const panel = panelRef.current;
    const initial = initialFocusRef?.current ?? panel;
    initial?.focus({ preventScroll: true });
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); onCloseRef.current(); return; }
      if (event.key !== "Tab" || !panel) return;
      const focusable = Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE));
      if (!focusable.length) { event.preventDefault(); panel.focus(); return; }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement;
      const inside = active instanceof Node && panel.contains(active) && active !== panel;
      if (event.shiftKey && (active === first || !inside)) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && (active === last || !inside)) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      unlockScroll();
      const target = returnFocusRef?.current ?? previouslyFocused;
      returnRafRef.current = requestAnimationFrame(() => {
        returnRafRef.current = null;
        target?.focus({ preventScroll: true });
      });
    };
    // 開一次 lock 一次；ref 唔入 deps
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /* B6：backdrop 用 pointerup 收（唔係 mousedown/click），而且 pointerdown 都要係落喺 backdrop 空位——
     slider / drag 喺 panel 入面開始、喺 backdrop 上面放手嗰下唔准當撳空位。 */
  const onBackdropPointerDown = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    downOnBackdropRef.current = event.target === event.currentTarget;
  }, []);
  const onBackdropPointerUp = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    const hit = downOnBackdropRef.current && event.target === event.currentTarget;
    downOnBackdropRef.current = false;
    if (hit) onCloseRef.current();
  }, []);

  /* 手機拉落收：drag listener 唔開（開咗會食晒 sheet 入面嘅垂直 scroll），
     由 pointerdown 決定——handle / 頂部區一定可以拉；內容冇得碌（scrollHeight ≤ clientHeight）就成塊都可以拉。 */
  const draggable = variant === "sheet" && isMobile;
  const onPanelPointerDown = useCallback((event: ReactPointerEvent<HTMLElement>) => {
    if (!draggable) return;
    const panel = event.currentTarget;
    const target = event.target as HTMLElement;
    const fromRegion = Boolean(target.closest(".sheet-drag-region"));
    const scrollable = panel.scrollHeight - panel.clientHeight > 1;
    if (fromRegion || !scrollable) dragControls.start(event);
  }, [draggable, dragControls]);
  const onDragEnd = useCallback((_: unknown, info: PanInfo) => {
    if (info.offset.y > DRAG_CLOSE_OFFSET || info.velocity.y > DRAG_CLOSE_VELOCITY) {
      tap.close();
      onCloseRef.current();
    }
  }, []);

  /* 手機 sheet 用 px 唔用 "100%"：拖動中 y 係 px，exit 目標同單位先唔使 framer 換算 */
  const offscreen = typeof window === "undefined" ? 900 : window.innerHeight;
  const panelMotion = variant === "sheet" && isMobile
    ? {
      initial: { y: offscreen, opacity: 0.6 },
      animate: { y: 0, opacity: 1, transition: { duration: 0.28, ease: EASE_OUT } },
      exit: { y: offscreen, opacity: 0.6, transition: { duration: 0.2, ease: EASE_IN } },
    }
    : variant === "sheet"
      ? {
        initial: { opacity: 0, scale: 0.98 },
        animate: { opacity: 1, scale: 1, transition: { duration: 0.17, ease: EASE_OUT } },
        exit: { opacity: 0, scale: 0.98, transition: { duration: 0.12, ease: EASE_IN } },
      }
      : {
        initial: { opacity: 0, y: 12, scale: 0.98 },
        animate: { opacity: 1, y: 0, scale: 1, transition: { duration: 0.18, ease: EASE_OUT } },
        exit: { opacity: 0, y: 8, scale: 0.98, transition: { duration: 0.14, ease: EASE_IN } },
      };
  const backdropMotion = {
    initial: { opacity: 0 },
    animate: { opacity: 1, transition: { duration: 0.17 } },
    exit: { opacity: 0, transition: { duration: draggable ? 0.2 : 0.12 } },
  };
  return (
    <motion.div
      className={`${backdropClassName} sheet-motion`}
      role="presentation"
      style={NO_CSS_ANIMATION}
      onPointerDown={onBackdropPointerDown}
      onPointerUp={onBackdropPointerUp}
      {...backdropMotion}
    >
      <motion.section
        ref={panelRef}
        className={`${panelClassName} sheet-motion`}
        role="dialog"
        aria-modal="true"
        aria-labelledby={ariaLabelledBy}
        aria-label={ariaLabel}
        tabIndex={-1}
        style={NO_CSS_ANIMATION}
        drag={draggable ? "y" : false}
        dragListener={false}
        dragControls={dragControls}
        dragConstraints={{ top: 0 }}
        dragElastic={{ top: 0.08 }}
        dragSnapToOrigin
        dragTransition={{ bounceStiffness: 600, bounceDamping: 45 }}
        dragMomentum={false}
        onDragEnd={onDragEnd}
        onPointerDown={onPanelPointerDown}
        {...panelMotion}
      >
        {children}
      </motion.section>
    </motion.div>
  );
}
