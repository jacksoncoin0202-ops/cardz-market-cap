#!/usr/bin/env node
/*
 * 熱力圖色階 + 持平／冇數分開。owner 2026-09-23 批（DESIGN.md「明確非目標」同步解禁）。
 *
 * 根因三個：
 *   ① 預設 gamma 4 + aMin 0.78：1%→0.780、3%→0.809 —— 3% 以下肉眼同色，1D 成版一隻綠。
 *   ② 分享圖 api/og/heatmap 自己抄一份色階（tileFill），0.04% 印「+0.0%」綠格，網站係灰格。
 *   ③ 灰格一律叫「資料累積中」，印 0.0% 嘅卡都被講成未有數。
 *   ④ label 底板係格同色 34%，疊喺同色格上面等於冇底板：dark 5% 綠格白字對比得 2.66。
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

const { DEFAULT_TILE, LABEL_PLATE, restoreTileParams, tileColors, tileStyle, windowTileParams, WINDOW_CLAMP_SCALE } = await import(
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
check("升跌同一條色階：用倍數量，+25%（×1.25）同 −20%（÷1.25）一樣深", alphaOf(st(-20, windowTileParams(DEFAULT_TILE, "30d")).bg) === alphaOf(st(25, windowTileParams(DEFAULT_TILE, "30d")).bg));
check("細幅度 log ≈ %：−3% 只係深過 +3% 少少（< 0.02）", alphaOf(st(-3).bg) >= alphaOf(st(3).bg) && alphaOf(st(-3).bg) - alphaOf(st(3).bg) < 0.02,
  `alpha −3% ${alphaOf(st(-3).bg)} / +3% ${alphaOf(st(3).bg)}`);

/* ── ④ 每個窗口自己一個飽和點（owner 2026-09-25：「升親都唔係 5% 咁小，熱力圖 % 間隔要隔開 D」）──
   以前全部窗口 5% 頂格，09-25 Top100 30D 80%／180D 91% 嘅格都係最深色。數字係 Top100 嗰個窗口約第 95 百分位；
   改數要連 tile-style.ts 註解一齊改。負控制：唔經 windowTileParams（舊寫法）30D +13%（Top100 中位數）一定頂格。 */
const { marketWindows } = await import(`file://${join(ROOT, "apps/web/src/lib/types.ts").replaceAll("\\", "/")}`);
const EXPECT_CLAMP = { "1d": 5, "7d": 20, "30d": 50, "90d": 70, "180d": 120, "365d": 250 };
check("WINDOW_CLAMP_SCALE 包晒每個 market window",
  marketWindows.every((w) => Number.isFinite(WINDOW_CLAMP_SCALE[w])) && Object.keys(WINDOW_CLAMP_SCALE).length === marketWindows.length,
  `windows ${marketWindows.join(",")} vs ${Object.keys(WINDOW_CLAMP_SCALE).join(",")}`);
for (const w of marketWindows) {
  const wp = windowTileParams(DEFAULT_TILE, w);
  const cap = EXPECT_CLAMP[w];
  const down = Math.floor(1000 * (1 / (1 + cap / 100) - 1)) / 10; /* label 一位小數：−33.33 印 −33.3 就差一條命，向下取 */
  check(`${w} 飽和點 = +${cap}%`, wp.clamp === cap, `got ${wp.clamp}`);
  check(`${w} 到 +${cap}% 同 ${down.toFixed(1)}% 先頂格，一半幅度仲淺`,
    alphaOf(st(cap, wp).bg) === 1 && alphaOf(st(down, wp).bg) === 1 && alphaOf(st(cap / 2, wp).bg) < 0.8,
    `alpha ${alphaOf(st(cap, wp).bg)} / ${alphaOf(st(down, wp).bg)} / ${alphaOf(st(cap / 2, wp).bg)}`);
}
check("windowTileParams 淨係改 clamp，1D 同預設一樣", JSON.stringify(windowTileParams(DEFAULT_TILE, "1d")) === JSON.stringify(DEFAULT_TILE));
check("/tune 調 1D clamp，長窗跟住按比例", windowTileParams({ ...DEFAULT_TILE, clamp: 10 }, "30d").clamp === 100);
const mid30 = alphaOf(st(13, windowTileParams(DEFAULT_TILE, "30d")).bg);
check("30D +13% 唔再頂格（深淺分得出）", mid30 < 0.7, `alpha ${mid30}`);
check("負控制：唔經 windowTileParams 30D +13% 頂格", alphaOf(st(13).bg) === 1);
check("≤ −100% 壞數唔出 NaN（當頂格）", alphaOf(st(-100, windowTileParams(DEFAULT_TILE, "365d")).bg) === 1 && alphaOf(st(-150).bg) === 1);

/* ── label 底板：白字對比 ≥ 4.5（WCAG AA 細字）。frame 底 ⊕ 格色 ⊕ 底板，兩個 theme × 紅綠對調 × 0.1–10%。
   淺色 theme 格色配淺 frame、深色配深 frame（按 --heatmap-frame-bg 光暗配對，唔靠 CSS 次序）。
   負控制：舊「同色 34%」底板要紅（dark 5% 綠格得 2.66）。 ── */
const rgbaOf = (s) => {
  if (s.startsWith("#")) return [1, 3, 5].map((i) => parseInt(s.slice(i, i + 2), 16)).concat(1);
  const v = /rgba?\(([^)]+)\)/.exec(s)[1].split(",").map(Number);
  return [v[0], v[1], v[2], v[3] ?? 1];
};
const over = (top, under) => { const [r, g, b, a] = rgbaOf(top); return [r, g, b].map((c, i) => c * a + under[i] * (1 - a)); };
const lin = (c) => { const s = c / 255; return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4; };
const lum = ([r, g, b]) => 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
const frames = [...read("apps/web/src/app/globals.css").matchAll(/--heatmap-frame-bg: (#[0-9a-fA-F]{6});/g)]
  .map((m) => rgbaOf(m[1]).slice(0, 3)).sort((a, b) => lum(b) - lum(a));
check("--heatmap-frame-bg light + dark 兩套", frames.length === 2);
function worstLabelContrast(plateOf) {
  let worst = { c: Infinity, at: "" };
  for (const [frame, dark] of [[frames[0], false], [frames[1], true]]) {
    const base = tileColors(dark, DEFAULT_TILE);
    for (const cs of [base, { ...base, up: base.down, down: base.up }]) {
      for (const pct of [0.1, 0.5, 1, 2, 3, 4, 5, 10, -0.1, -1, -3, -5, -10]) {
        const tile = tileStyle(pct, 120, 160, cs, DEFAULT_TILE);
        const c = 1.05 / (lum(over(plateOf(tile, pct, cs), over(tile.bg, frame))) + 0.05);
        if (c < worst.c) worst = { c, at: `${dark ? "dark" : "light"} ${cs === base ? "green-up" : "red-up"} ${pct}%` };
      }
    }
  }
  return worst;
}
let plateNow = { c: Number.NaN, at: "" };
if (frames.length === 2) {
  plateNow = worstLabelContrast((tile) => tile.plate);
  check("升跌 label 白字對比 ≥ 4.5", plateNow.c >= 4.5, `最差 ${plateNow.c.toFixed(2)} @ ${plateNow.at}`);
  const plateOld = worstLabelContrast((tile, pct, cs) => {
    const [r, g, b] = rgbaOf(pct > 0 ? cs.up : cs.down);
    return `rgba(${r}, ${g}, ${b}, 0.34)`;
  });
  check("負控制：舊同色 34% 底板過唔到 4.5", plateOld.c < 4.5, `舊最差 ${plateOld.c.toFixed(2)} @ ${plateOld.at}`);
}
check("升跌格底板 = LABEL_PLATE", st(2).plate === LABEL_PLATE && st(-2).plate === LABEL_PLATE);

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
check("OG route import tileStyle + DEFAULT_TILE + windowTileParams", /import \{ DEFAULT_TILE, tileStyle, windowTileParams \} from "@\/lib\/tile-style";/.test(route));
check("OG route 格色 / 底板由 tileStyle 出",
  /tileStyle\(pct, [^;]*windowTileParams\(DEFAULT_TILE, period\)\)/.test(route) && /background: st\.bg,/.test(route) && /const plate = st\.plate;/.test(route));
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
/* 三個 call site 全部經 windowTileParams：漏一個就係網站同分享圖兩條色階 */
for (const [rel, re] of [
  ["apps/web/src/components/heatmap.tsx", /tileStyle\(changeValue\(card, activePeriod\), box\.w, box\.h, colors, windowTileParams\(params, activePeriod\)\)/],
  ["apps/web/src/components/heatmap-tiles-board.tsx", /tileStyle\(changeValue\(card, period\), box\.w, box\.h, colors, windowTileParams\(params, period\)\)/],
]) {
  const src = read(rel);
  check(`${rel} tileStyle 經 windowTileParams`, re.test(src) && (src.match(/tileStyle\(/g) ?? []).length === 1);
}
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
console.log(`PASS test-fe-heatmap-tile-color — 色階 1–5% = ${now.a.join(" / ")}；飽和點 ${marketWindows.map((w) => `${w} ${windowTileParams(DEFAULT_TILE, w).clamp}%`).join(" ")}；label 白字對比最差 ${plateNow.c.toFixed(2)}（${plateNow.at}）；持平／冇數分開；分享圖同網站同一條`);
process.exit(0);
