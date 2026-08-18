"use client";

/* interactionkit CodeBlock copy 反饋移植（https://github.com/armondschneider/interactionkit, MIT）：
   撳掣 copy 成功 → icon Copy/Share morph 做 Check， 1.8s 後回復；
   Clipboard API + textarea fallback。

   可見 label 唔會變（唔會跳闊度），結果由隔籬 <span role="status" class="sr-only"> 讀出；
   busy 期間 disabled + aria-busy + Loader icon，ref 擋重入（連撳兩下唔會開兩個 share sheet）。
   有 onCopy 就由 onCopy 喺 click handler 入面（user activation 內）自己寫 clipboard，getText 可以唔傳。 */
import { AnimatePresence, motion } from "framer-motion";
import { Check, Loader2, Share2, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { tap } from "@/lib/haptic";

async function writeClipboard(text: string): Promise<void> {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const area = document.createElement("textarea");
  area.value = text;
  area.style.position = "fixed";
  area.style.opacity = "0";
  document.body.appendChild(area);
  area.select();
  try {
    document.execCommand("copy");
  } finally {
    area.remove();
  }
}

type CopyState = "idle" | "busy" | "done" | "error";

const iconMotion = {
  initial: { opacity: 0, scale: 0.6 },
  animate: { opacity: 1, scale: 1 },
  exit: { opacity: 0, scale: 0.6 },
  transition: { duration: 0.18, ease: [0.16, 1, 0.3, 1] as const },
};

export function CopyButton({ getText, label, doneLabel, errorLabel, className = "share-button", preferNativeShare = false, onCopy, onWarm }: {
  /* onCopy 自己搞 clipboard 嗰陣可以唔傳 */
  getText?: () => string;
  label: string;
  doneLabel: string;
  errorLabel: string;
  className?: string;
  preferNativeShare?: boolean;
  onCopy?: () => void | Promise<void>;
  /*
   * 「就快撳」嘅信號（hover / focus / 撳落去嗰刻），俾叫方預先攞定重嘢。
   * ⚠️ 存在理由：`navigator.share` 一定要喺 user activation 之內叫（見 lib/share-file.ts），
   * 撳完先 fetch 幾百 KB 圖 iOS Safari 會掟 NotAllowedError，share sheet 唔出、直接落載。
   * 一定要 idempotent —— 呢個 handler 一次互動會 fire 兩三次（enter → focus → down）。
   */
  onWarm?: () => void;
}) {
  const [state, setState] = useState<CopyState>("idle");
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const busyRef = useRef(false);
  useEffect(() => () => { if (timerRef.current) clearTimeout(timerRef.current); }, []);

  const run = useCallback(async () => {
    if (busyRef.current) return;
    busyRef.current = true;
    if (timerRef.current) clearTimeout(timerRef.current);
    setState("busy");
    const finish = (next: "done" | "error" | "idle") => {
      busyRef.current = false;
      setState(next);
      if (next === "done") tap.success();
      if (next === "error") tap.error();
      if (next !== "idle") timerRef.current = setTimeout(() => setState("idle"), 1800);
    };
    try {
      if (onCopy) {
        await onCopy();
        finish("done");
        return;
      }
      const text = getText ? getText() : "";
      if (!text) { finish("error"); return; }
      if (preferNativeShare && navigator.share) {
        try {
          await navigator.share({ url: text });
          finish("done");
        } catch (error) {
          // 用戶自己撳走 share sheet 唔算失敗
          if (error instanceof DOMException && error.name === "AbortError") finish("idle");
          else finish("error");
        }
        return;
      }
      await writeClipboard(text);
      finish("done");
    } catch {
      finish("error");
    }
  }, [getText, preferNativeShare, onCopy]);

  const busy = state === "busy";
  return (
    <>
      <button
        type="button"
        className={className}
        onClick={run}
        onPointerEnter={onWarm}
        onPointerDown={onWarm}
        onFocus={onWarm}
        data-state={state}
        disabled={busy}
        aria-busy={busy}
        title={state === "error" ? errorLabel : state === "done" ? doneLabel : undefined}
      >
        <AnimatePresence mode="wait" initial={false}>
          {state === "done" ? (
            <motion.span key="check" className="copy-icon" {...iconMotion}>
              <Check aria-hidden="true" size={14} strokeWidth={2.2} />
            </motion.span>
          ) : state === "error" ? (
            <motion.span key="error" className="copy-icon" {...iconMotion}>
              <X aria-hidden="true" size={14} strokeWidth={2.2} />
            </motion.span>
          ) : busy ? (
            <motion.span key="busy" className="copy-icon copy-icon-busy" {...iconMotion}>
              <Loader2 aria-hidden="true" size={14} strokeWidth={2} />
            </motion.span>
          ) : (
            <motion.span key="copy" className="copy-icon" {...iconMotion}>
              <Share2 aria-hidden="true" size={14} strokeWidth={1.8} />
            </motion.span>
          )}
        </AnimatePresence>
        <span>{label}</span>
      </button>
      {/* 結果讀屏用：sibling status，label 唔郁 */}
      <span role="status" aria-live="polite" className="sr-only">
        {state === "done" ? doneLabel : state === "error" ? errorLabel : ""}
      </span>
    </>
  );
}
