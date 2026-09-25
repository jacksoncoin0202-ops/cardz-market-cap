"use client";

import { useCallback, useEffect, useRef, useState, type ChangeEvent } from "react";
import { flushSync } from "react-dom";
import { CopyButton } from "./copy-button";
import { copy } from "@/lib/i18n";
import { DEFAULT_TILE, type TileParams } from "@/lib/tile-style";
import type { Locale } from "@/lib/types";

/*
 * 調參 input 嘅 pump（heatmap tune panel 同 /tune lab 共用），同 heatmap 嘅 tile slider 一樣三件事：
 * 1) input 全部 uncontrolled（defaultValue）——controlled + 每 pixel setState 會令 thumb 追唔上手。
 * 2) 每個 input 事件淨係記低 patch，排一個 rAF；同一幀幾多個事件都疊埋一次 flushSync，
 *    thumb 同 100 格 tile 同一幀落地。
 * 3) 邊個 key 最新就用邊個，唔會漏最後一格。
 * localStorage 由 caller 喺 native `change`（放手 / 揀完色）先寫，唔係每 pixel 寫（見 useTuneCommit）。
 */
export function useParamPump(params: TileParams, apply: (next: TileParams) => void) {
  const latestRef = useRef(params);
  useEffect(() => { latestRef.current = params; }, [params]);
  const patchRef = useRef<Partial<TileParams>>({});
  const rafRef = useRef<number | null>(null);
  const applyRef = useRef(apply);
  useEffect(() => { applyRef.current = apply; }, [apply]);
  const flush = useCallback(() => {
    rafRef.current = null;
    const next = { ...latestRef.current, ...patchRef.current };
    patchRef.current = {};
    latestRef.current = next;
    flushSync(() => applyRef.current(next));
  }, []);
  const set = useCallback((key: keyof TileParams, value: number | string) => {
    (patchRef.current as Record<string, number | string>)[key] = value;
    if (rafRef.current === null) rafRef.current = requestAnimationFrame(flush);
  }, [flush]);
  /* 未 flush 嘅 patch 都算埋：native change 通常喺最後一個 input 之後、rAF 之前到 */
  const peek = useCallback((): TileParams => ({ ...latestRef.current, ...patchRef.current }), []);
  useEffect(() => () => { if (rafRef.current !== null) cancelAnimationFrame(rafRef.current); }, []);
  return { set, peek };
}

/* 掛一個 native `change` listener 喺 panel root：range 放手 / color picker 揀完先 fire，
   同 React 嘅 onChange（＝input，每 pixel）分開。
   用 callback ref 唔用 useEffect：panel 係遲啲先 mount（開咗 Sheet 先有），
   effect 喺 hook 所在 component mount 嗰陣 root 仲係 null，之後唔會再行。 */
export function useTuneCommit(onCommit: ((params: TileParams) => void) | undefined, peek: () => TileParams) {
  const onCommitRef = useRef(onCommit);
  const peekRef = useRef(peek);
  useEffect(() => { onCommitRef.current = onCommit; peekRef.current = peek; }, [onCommit, peek]);
  const detachRef = useRef<(() => void) | null>(null);
  return useCallback((root: HTMLDivElement | null) => {
    detachRef.current?.();
    detachRef.current = null;
    if (!root) return;
    const handler = () => onCommitRef.current?.(peekRef.current());
    root.addEventListener("change", handler);
    detachRef.current = () => root.removeEventListener("change", handler);
  }, []);
}

export function TuneRange({ label, value, min, max, step, format, onInput }: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  format: (value: number) => string;
  onInput: (value: number) => void;
}) {
  const outRef = useRef<HTMLOutputElement>(null);
  const handle = (event: ChangeEvent<HTMLInputElement>) => {
    const next = Number(event.currentTarget.value);
    if (outRef.current) outRef.current.textContent = format(next);
    onInput(next);
  };
  return (
    <label className="tune-field">
      <span>{label}</span>
      <input type="range" min={min} max={max} step={step} defaultValue={value} onChange={handle} />
      <output ref={outRef}>{format(value)}</output>
    </label>
  );
}

export function TuneColor({ label, value, inactive = false, onInput }: {
  label: string;
  value: string;
  inactive?: boolean;
  onInput: (value: string) => void;
}) {
  const outRef = useRef<HTMLOutputElement>(null);
  const handle = (event: ChangeEvent<HTMLInputElement>) => {
    const next = event.currentTarget.value;
    if (outRef.current) outRef.current.textContent = next;
    onInput(next);
  };
  return (
    <label className={`tune-field tune-field-color${inactive ? " tune-field-inactive" : ""}`}>
      <span>{label}</span>
      <input type="color" defaultValue={value} onChange={handle} />
      <output ref={outRef}>{value}</output>
    </label>
  );
}

/* /tune lab 嘅調參 panel：俾 testers 逐個參數校（包埋色碼），複製 JSON 貼返俾我哋寫死 */
export function TunePanel({ params, onChange, dark, locale }: { params: TileParams; onChange: (p: TileParams) => void; dark: boolean; locale: Locale }) {
  const t = copy[locale];
  const { set } = useParamPump(params, onChange);
  /* Reset：uncontrolled input 要 remount 先會跟返 DEFAULT */
  const [resetKey, setResetKey] = useState(0);
  const reset = () => { onChange(DEFAULT_TILE); setResetKey((k) => k + 1); };
  const sliders: { key: keyof TileParams; label: string; min: number; max: number; step: number; format: (v: number) => string }[] = [
    { key: "clamp", label: t.heatmap.clamp, min: 1, max: 20, step: 0.5, format: (v) => `${v.toFixed(1)}%` },
    { key: "gamma", label: t.heatmap.intensity, min: 0.5, max: 4, step: 0.1, format: (v) => v.toFixed(1) },
    { key: "deadzone", label: t.heatmap.neutralZone, min: 0, max: 5, step: 0.5, format: (v) => `±${v}%` },
    { key: "aMin", label: t.heatmap.alphaMin, min: 0, max: 1, step: 0.01, format: (v) => v.toFixed(2) },
    { key: "aMax", label: t.heatmap.alphaMax, min: 0, max: 1, step: 0.01, format: (v) => v.toFixed(2) },
    { key: "gap", label: t.heatmap.gap, min: 0, max: 12, step: 1, format: (v) => `${v}px` },
    { key: "cardPct", label: t.heatmap.cardSize, min: 0, max: 1, step: 0.01, format: (v) => `${Math.round(v * 100)}%` },
    { key: "cardAspect", label: t.heatmap.cardAspect, min: 0.4, max: 1, step: 0.001, format: (v) => v.toFixed(3) },
  ];
  const colors: { key: keyof TileParams; label: string; active: boolean }[] = [
    { key: "upDark", label: `${t.heatmap.upColor} · Dark`, active: dark },
    { key: "upLight", label: `${t.heatmap.upColor} · Light`, active: !dark },
    { key: "downDark", label: `${t.heatmap.downColor} · Dark`, active: dark },
    { key: "downLight", label: `${t.heatmap.downColor} · Light`, active: !dark },
  ];
  const json = JSON.stringify(params, null, 2);
  return (
    <aside className="tune-panel" aria-label={t.heatmap.customizeTitle}>
      <div className="tune-panel-head">
        <strong>{t.heatmap.customizeTitle}</strong>
        <div className="tune-panel-actions">
          <button type="button" onClick={reset}>{t.heatmap.resetDefault}</button>
          <CopyButton className="tune-copy" getText={() => json} label={t.heatmap.copyParams} doneLabel={t.heatmap.paramsCopied} errorLabel={t.heatmap.copyFailed} />
        </div>
      </div>
      <div key={`${resetKey}-${dark ? "d" : "l"}`}>
        {colors.map((f) => (
          <TuneColor key={f.key} label={f.label} value={params[f.key] as string} inactive={!f.active} onInput={(v) => set(f.key, v)} />
        ))}
        {sliders.map((f) => (
          <TuneRange key={f.key} label={f.label} value={params[f.key] as number} min={f.min} max={f.max} step={f.step} format={f.format} onInput={(v) => set(f.key, v)} />
        ))}
      </div>
      <pre className="tune-json">{json}</pre>
    </aside>
  );
}
