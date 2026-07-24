"use client";

import { useState } from "react";
import { DEFAULT_TILE, type TileParams } from "@/lib/tile-style";

/* 調參 panel：俾 testers 逐個參數校（包埋色碼），輸出 JSON 貼返俾我哋寫死 */
export function TunePanel({ params, onChange, dark }: { params: TileParams; onChange: (p: TileParams) => void; dark: boolean }) {
  const [copied, setCopied] = useState(false);
  const set = (key: keyof TileParams, value: number | string) => onChange({ ...params, [key]: value });
  const sliders: { key: keyof TileParams; label: string; min: number; max: number; step: number }[] = [
    { key: "clamp", label: "爆色點 clamp %（到幾多 % 當最深）", min: 1, max: 20, step: 0.5 },
    { key: "gamma", label: "強度曲線 gamma（大=誇大差異）", min: 0.5, max: 4, step: 0.1 },
    { key: "aMin", label: "最淺色透明度 aMin", min: 0, max: 1, step: 0.01 },
    { key: "aMax", label: "最深色透明度 aMax", min: 0, max: 1, step: 0.01 },
    { key: "gap", label: "格間距 gap px", min: 0, max: 12, step: 1 },
    { key: "cardPct", label: "卡佔 tile 比例 cardPct（0=唔顯示）", min: 0, max: 1, step: 0.01 },
    { key: "cardAspect", label: "卡闊高比 cardAspect（Wall=0.714）", min: 0.4, max: 1, step: 0.001 },
  ];
  const colors: { key: keyof TileParams; label: string; active: boolean }[] = [
    { key: "upDark", label: "升色（深色模式）", active: dark },
    { key: "upLight", label: "升色（淺色模式）", active: !dark },
    { key: "downDark", label: "跌色（深色模式）", active: dark },
    { key: "downLight", label: "跌色（淺色模式）", active: !dark },
  ];
  const json = JSON.stringify(params, null, 2);
  const copyJson = async () => {
    try { await navigator.clipboard.writeText(json); setCopied(true); setTimeout(() => setCopied(false), 1500); } catch { /* clipboard 無權限就算 */ }
  };
  return (
    <aside className="tune-panel" aria-label="熱力圖調參">
      <div className="tune-panel-head">
        <strong>熱力圖調參</strong>
        <div className="tune-panel-actions">
          <button type="button" onClick={() => onChange(DEFAULT_TILE)}>重設</button>
          <button type="button" onClick={copyJson}>{copied ? "已複製" : "複製參數"}</button>
        </div>
      </div>
      {colors.map((f) => (
        <label className={`tune-field tune-field-color${f.active ? "" : " tune-field-inactive"}`} key={f.key}>
          <span>{f.label}{f.active ? "（而家用緊）" : ""}</span>
          <input
            type="color"
            value={params[f.key] as string}
            onChange={(e) => set(f.key, e.target.value)}
          />
          <output>{params[f.key]}</output>
        </label>
      ))}
      {sliders.map((f) => (
        <label className="tune-field" key={f.key}>
          <span>{f.label}</span>
          <input
            type="range"
            min={f.min}
            max={f.max}
            step={f.step}
            value={params[f.key] as number}
            onChange={(e) => set(f.key, Number(e.target.value))}
          />
          <output>{(params[f.key] as number).toFixed(f.step < 0.1 ? 3 : f.step < 1 ? 2 : 0)}</output>
        </label>
      ))}
      <pre className="tune-json">{json}</pre>
    </aside>
  );
}
