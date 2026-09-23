#!/usr/bin/env node
/*
 * 熱力圖色階 + 持平／冇數分開。owner 2026-09-23 批（DESIGN.md「明確非目標」同步解禁）。
 *
 * 根因三個：
 *   ① 預設 gamma 4 + aMin 0.78：1%→0.780、3%→0.809 —— 3% 以下肉眼同色，1D 成版一隻綠。
 *   ② 分享圖 api/og/heatmap 自己抄一份色階（tileFill），0.04% 印「+0.0%」綠格，網站係灰格。
 *   ③ 灰格一律叫「資料累積中」，印 0.0% 嘅卡都被講成未有數。
 * 另外 /tune 存落 localStorage 嘅係成套 params，舊預設跟住存埋，新預設蓋唔到。
 *
 * 呢個檔真係 import tileStyle / restoreTileParams / copy（Node strip types）；
 * route / CSS / legend / tile 接線係 source 契約（嗰幾度要 Next 先行得）。
 * 負控制：OLD 參數（gamma 4 / aMin 0.78）要對住色階合約紅。
 * run_all_tests.py glob `scripts/test-*.mjs`。
 */
import { registerHooks } from "node:module";
import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

registerHooks({
  resolve(specifier, context, next) {
    if (specifier.startsWith(".") && !/\.[a-z]+$/i.test(specifier)) {
      try { return next(`${specifier}.ts`, context); } catch { /* 跌返原本 specifier */ }
    }
    return next(specifier, context);
  },
});

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const read = (rel) => readFileSync(join(ROOT, rel), "utf8");
const failed = [];
const check = (label, condition, detail = "") => {
  if (!condition) failed.push(detail ? `${label} —— ${detail}` : label);
};

const { DEFAULT_TILE, restoreTileParams, tileColors, tileStyle } = await import(
  `file://${join(ROOT, "apps/web/src/lib/tile-style.ts").replaceAll("\\", "/")}`
);
const { copy } = await import(`file://${join(ROOT, "apps/web/src/lib/i18n.ts").replaceAll("\\", "/")}`);

const OLD = { ...DEFAULT_TILE, gamma: 4, aMin: 0.78 };
const colors = tileColors(true, DEFAULT_TILE);
const st = (value, p = DEFAULT_TILE) => tileStyle(value, 120, 160, colors, p);
const alphaOf = (bg) => {
  const m = /^rgba\(\d+, \d+, \d+, ([\d.]+)\)$/.exec(bg);
  return m ? Number(m[1]) : Number.NaN;
};

/* ── ① 色階：1–4% 要睇得出分別 ── */
function ladder(p) {
  const a = [1, 2, 3, 4, 5].map((pct) => alphaOf(st(pct, p).bg));
  const rising = a.every((v, i) => i === 0 || v > a[i - 1]);
  return { a, ok: rising && a[0] <= 0.55 && a[2] - a[0] >= 0.15 && a[4] === 1 };
}
const now = ladder(DEFAULT_TILE);
check("預設色階 1%≤0.55、3% 比 1% 深 ≥0.15、逐級加深、5% 頂格", now.ok, `alpha 1–5% = ${now.a.join(" / ")}`);
check("負控制：舊 gamma 4 / aMin 0.78 過唔到色階合約", !ladder(OLD).ok, `old alpha = ${ladder(OLD).a.join(" / ")}`);
check("升跌同一條色階（−3% 同 +3% 一樣深）", alphaOf(st(-3).bg) === alphaOf(st(3).bg));

/* ── ③ 冇數 vs 持平：同樣灰，但 missing 分得開 ── */
const miss = st(null);
check("冇數 → missing、neutral、灰", miss.missing === true && miss.direction === "neutral" && miss.bg === colors.neutral);
check("NaN 當冇數", st(Number.NaN).missing === true);
for (const raw of [0, 0.04, -0.04]) {
  const flat = st(raw);
  check(`${raw}% → 持平（唔係冇數）`, flat.missing === false && flat.direction === "neutral" && flat.bg === colors.neutral);
}
check("有升跌就唔係冇數", st(2).missing === false && st(-2).missing === false);

/* ── localStorage 舊預設：洗走舊預設值，自訂嘢照留 ── */
check("冇存嘢 → 新預設", restoreTileParams(null) === DEFAULT_TILE);
const migrated = restoreTileParams(JSON.stringify({ ...OLD, upDark: "#00ff00", gap: 5 }));
check("存咗舊預設 gamma 4 / aMin 0.78 → 跟新預設",
  migrated.gamma === DEFAULT_TILE.gamma && migrated.aMin === DEFAULT_TILE.aMin,
  `got gamma ${migrated.gamma} aMin ${migrated.aMin}`);
check("自訂色同格距照留", migrated.upDark === "#00ff00" && migrated.gap === 5);
const custom = restoreTileParams(JSON.stringify({ gamma: 2.2, aMin: 0.6 }));
check("自己揀過嘅 gamma / aMin 照留", custom.gamma === 2.2 && custom.aMin === 0.6);
check("存咗 null → 新預設", JSON.stringify(restoreTileParams("null")) === JSON.stringify(DEFAULT_TILE));
let threw = false;
try { restoreTileParams("{bad"); } catch { threw = true; }
check("壞 JSON 掟出去俾 caller 嘅 try 接", threw);
const heatmapSrc = read("apps/web/src/components/heatmap.tsx");
check("heatmap.tsx 用 restoreTileParams 讀 localStorage（喺 try 入面）",
  /try \{[^}]*return restoreTileParams\(localStorage\.getItem\("cardz-heatmap-params"\)\);\s*\} catch/.test(heatmapSrc));

/* ── ② 分享圖同網站同一條色階 ── */
const tileSrc = read("apps/web/src/lib/tile-style.ts");
check("tile-style.ts 冇 \"use client\"（OG route 係 server，要真 call 佢）", !/^\s*["']use client["'];?\s*$/m.test(tileSrc));
const route = read("apps/web/src/app/api/og/heatmap/route.tsx");
check("OG route import tileStyle + DEFAULT_TILE", /import \{ DEFAULT_TILE, tileStyle \} from "@\/lib\/tile-style";/.test(route));
check("OG route 格色 / 底板由 tileStyle 出",
  /tileStyle\(pct, [^;]*DEFAULT_TILE\)/.test(route) && /background: st\.bg,/.test(route) && /const plate = st\.plate;/.test(route));
check("OG route 冇自己計 alpha", !/\balpha\s*=/.test(route) && !/function tileFill/.test(route));
check("OG route 持平唔出 label", /const move = st\.direction === "neutral" \? null : formatMove\(pct\);/.test(route));
check("OG legend 灰格講埋持平（三個語言）",
  route.includes('pending: "Flat / data pending"')
  && route.includes('pending: "持平／資料累積中"')
  && route.includes('pending: "持平／数据累积中"'));

/* ── 網站 tile 接線 + legend ── */
check("HeatmapTile 出 data-missing", /data-missing=\{p\.missing \? "" : undefined\}/.test(read("apps/web/src/components/heatmap-tile.tsx")));
check("heatmap.tsx 傳 missing", heatmapSrc.includes("missing={st.missing}"));
check("/tune board 傳 missing", read("apps/web/src/components/heatmap-tiles-board.tsx").includes("missing={st.missing}"));
check("legend 有持平一格", heatmapSrc.includes('<li><span className="legend-swatch flat" />{t.heatmap.flat}</li>'));
check("legend 有冇數一格", heatmapSrc.includes('<li><span className="legend-swatch pending" />{t.heatmap.neutral}</li>'));
for (const [locale, c] of Object.entries(copy)) {
  check(`${locale} 有 heatmap.flat 而且唔同 neutral`, typeof c.heatmap.flat === "string" && c.heatmap.flat.trim() !== "" && c.heatmap.flat !== c.heatmap.neutral);
}

const css = read("apps/web/src/app/globals.css");
const missingRule = /\.heatmap-tile\[data-missing\]::before \{([^}]*)\}/.exec(css);
check("CSS 冇數格有斜紋", Boolean(missingRule) && /content: "";/.test(missingRule[1]) && /repeating-linear-gradient/.test(missingRule[1]));
check("斜紋唔食 click", Boolean(missingRule) && /pointer-events: none;/.test(missingRule[1]));
check("--tile-hatch light + dark 兩套", (css.match(/--tile-hatch:/g) ?? []).length === 2);
const flatRule = /\.legend-swatch\.flat \{([^}]*)\}/.exec(css);
const pendingRule = /\.legend-swatch\.pending \{([^}]*)\}/.exec(css);
check("legend 持平 = 實色", Boolean(flatRule) && !/repeating-linear-gradient/.test(flatRule[1]));
check("legend 冇數 = 斜紋", Boolean(pendingRule) && /repeating-linear-gradient/.test(pendingRule[1]));

if (failed.length) {
  console.error(`FAIL test-fe-heatmap-tile-color (${failed.length})`);
  for (const line of failed) console.error(`  - ${line}`);
  process.exit(1);
}
console.log(`PASS test-fe-heatmap-tile-color — 色階 1–5% = ${now.a.join(" / ")}；持平／冇數分開；分享圖同網站同一條`);
process.exit(0);
