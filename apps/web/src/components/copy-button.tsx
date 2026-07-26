"use client";

/* interactionkit CodeBlock copy 反饋移植（https://github.com/armondschneider/interactionkit, MIT）：
   撳掣 copy 成功 → icon Copy/Share morph 做 Check， 1.8s 後回復；
   Clipboard API + textarea fallback。 */
import { AnimatePresence, motion } from "framer-motion";
import { Check, Share2 } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

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

export function CopyButton({ getText, label, doneLabel, errorLabel, className = "share-button", preferNativeShare = false, onCopy }: {
  getText: () => string;
  label: string;
  doneLabel: string;
  errorLabel: string;
  className?: string;
  preferNativeShare?: boolean;
  onCopy?: () => void | Promise<void>;
}) {
  const [state, setState] = useState<"idle" | "done" | "error">("idle");
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => { if (timerRef.current) clearTimeout(timerRef.current); }, []);

  const run = useCallback(async () => {
    const finish = (next: "done" | "error") => {
      setState(next);
      if (timerRef.current) clearTimeout(timerRef.current);
      timerRef.current = setTimeout(() => setState("idle"), 1800);
    };
    const text = getText();
    if (onCopy) {
      try { await onCopy(); finish("done"); } catch { finish("error"); }
      return;
    }
    if (preferNativeShare && navigator.share) {
      try { await navigator.share({ url: text }); finish("done"); } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") return;
        finish("error");
      }
      return;
    }
    try { await writeClipboard(text); finish("done"); } catch { finish("error"); }
  }, [getText, preferNativeShare, onCopy]);

  return (
    <button type="button" className={className} onClick={run} data-state={state}>
      <AnimatePresence mode="wait" initial={false}>
        {state === "done" ? (
          <motion.span
            key="check"
            className="copy-icon"
            initial={{ opacity: 0, scale: 0.6 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.6 }}
            transition={{ duration: 0.18, ease: [0.16, 1, 0.3, 1] }}
          >
            <Check aria-hidden="true" size={14} strokeWidth={2.2} />
          </motion.span>
        ) : (
          <motion.span
            key="copy"
            className="copy-icon"
            initial={{ opacity: 0, scale: 0.6 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.6 }}
            transition={{ duration: 0.18, ease: [0.16, 1, 0.3, 1] }}
          >
            <Share2 aria-hidden="true" size={14} strokeWidth={1.8} />
          </motion.span>
        )}
      </AnimatePresence>
      <span>{state === "done" ? doneLabel : state === "error" ? errorLabel : label}</span>
    </button>
  );
}
