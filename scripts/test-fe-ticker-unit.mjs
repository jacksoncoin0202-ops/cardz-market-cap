#!/usr/bin/env node
/*
 * FE05 CapTicker 單位契約（fe05(cjk)，2026-08-17）：數字由起點滾去目標值途中，compact 單位（$…M/B、万/億、만/억/조）
 * 唔准中途換字 —— 換字 = 字串長度跳、`.heatmap-total-cap` 闊度跳、視覺上似 glitch。
 * 直接 import apps/web/src/lib/ticker-start.ts 同 lib/format.ts（Node ≥ 22.6 剝 type 就跑到，同 test-fe-pixel-snap.mjs 一樣；
 * format.ts 嘅 extensionless import 靠下面 registerHooks 補 .ts），用真嘅 formatMoney(compact)。
 * 以前呢度自己抄一份 Intl 參數，format.ts 一改（中日韓千位分隔＋4 位有效數字）就同真身分家。
 * 掃 5 locale × 6 貨幣 × 一批目標值，逐條 path 採樣 101 點，睇 unitSignature 有冇變。
 * 由 run_all_tests.py 自動 glob 入 npm test。
 */
import { registerHooks } from "node:module";
import { dirname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

registerHooks({
  resolve(specifier, context, next) {
    if (specifier.startsWith(".") && !/\.[a-z]+$/i.test(specifier)) {
      try { return next(`${specifier}.ts`, context); } catch { /* 跌返原本 specifier */ }
    }
    return next(specifier, context);
  },
});

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const { tickerStart, tickerEase, unitSignature } = await import(pathToFileURL(resolve(ROOT, "apps/web/src/lib/ticker-start.ts")).href);
const { formatMoney } = await import(pathToFileURL(resolve(ROOT, "apps/web/src/lib/format.ts")).href);

const failed = [];
const check = (label, condition, detail) => { if (!condition) failed.push(detail ? `${label}: ${detail}` : label); };

const intlLocale = { en: "en-US", "zh-TW": "zh-Hant-TW", "zh-CN": "zh-Hans-CN", ja: "ja-JP", ko: "ko-KR" };
const currencies = ["USD", "JPY", "TWD", "KRW", "HKD", "EUR"];
// 目標值（USD 基準 × 粗略匯率，令每隻貨幣都跨到唔同單位）：$850K … $27B
const targetsUsd = [850_000, 999_000, 1_000_000, 2_700_000_000, 12_310_000, 999_990_000, 1_000_000_000, 27_120_000_000, 100, 12.5];
const rate = { USD: 1, JPY: 150, TWD: 32, KRW: 1350, HKD: 7.8, EUR: 0.92 };
// 匯率 1：formatMoney 收 USD 再乘匯率，呢度直接當 n 已經係嗰隻貨幣
const compact = (locale, currency) => (n) => formatMoney(n, currency, { [currency]: 1 }, locale, true);
const standard = (locale, currency) => (n) => formatMoney(n, currency, { [currency]: 1 }, locale, false);

// 由 from 滾去 target 沿途（線性採樣 101 點；ease-out 只係改時間分佈，經過嘅值集合一樣）單位唔准變
function pathUnits(from, target, format, steps = 100) {
  const units = new Set();
  for (let i = 0; i <= steps; i++) units.add(unitSignature(format(from + (target - from) * (i / steps))));
  return units;
}

let paths = 0;
for (const locale of Object.keys(intlLocale)) {
  for (const currency of currencies) {
    const format = compact(locale, currency);
    const targets = targetsUsd.map((usd) => usd * rate[currency]);
    for (const target of targets) {
      const label = `${locale}/${currency} target=${format(target)}`;
      // ① 第一次滾（from = null）：起點 > 0、≤ target、同單位，沿途單位唔變
      const start = tickerStart(null, target, format);
      check(`${label} first-run start in (0, target]`, start > 0 && start <= target, `start=${start}`);
      const units = pathUnits(start, target, format);
      check(`${label} first-run path keeps one unit`, units.size === 1, [...units].join(" | "));
      paths++;
      // ② 同單位 retarget：由顯示緊嘅值直接滾
      const near = target * 1.15;
      if (unitSignature(format(near)) === unitSignature(format(target))) {
        check(`${label} same-unit retarget starts from shown value`, tickerStart(near, target, format) === near);
      }
      // ③ 跨單位 retarget（上／落）：起點同目標同單位，沿途唔變；方向要保持
      //    （由上面跌落嚟就要由本單位範圍頂接落去，唔准突然變咗由下面升上去 —— 除非 target 已經係範圍頂，冇位）
      for (const from of [target * 12, target / 12]) {
        const s = tickerStart(from, target, format);
        check(`${label} retarget from ${format(from)} start has target unit`, unitSignature(format(s)) === unitSignature(format(target)), `start=${format(s)}`);
        const u = pathUnits(s, target, format);
        check(`${label} retarget from ${format(from)} path keeps one unit`, u.size === 1, [...u].join(" | "));
        const sig = unitSignature(format(target));
        const roomAbove = unitSignature(format(target * 1.001)) === sig;
        const roomBelow = unitSignature(format(target * 0.999)) === sig;
        /* ⚠️ 方向係**無條件**斷言，唔准由 roomAbove/roomBelow 守住：target 貼住範圍頂（$999.99M）嗰陣
           roomAbove 啱啱係 false，舊寫法就係咁 skip 咗，而 tickerStart 嗰時回範圍底 $1M ——
           $12B 跌價竟然由 $1M 向上滾。冇位滾嘅正確行為係 start === target（唔滾），唔係跳去另一端。 */
        if (from > target) check(`${label} retarget down from ${format(from)} 方向唔倒轉 (start ≥ target)`, s >= target, `start=${format(s)}(${s}) target=${format(target)}(${target})`);
        if (from < target) check(`${label} retarget up from ${format(from)} 方向唔倒轉 (start ≤ target)`, s <= target, `start=${format(s)}(${s}) target=${format(target)}(${target})`);
        // 有位滾就一定要真係滾（唔准 degenerate 成冇動畫）；冇位滾（$1M 整 / $999.99M）先准 start === target
        if (from > target && roomAbove) check(`${label} retarget down from ${format(from)} keeps falling (start > target)`, s > target, `start=${format(s)}`);
        if (from < target && roomBelow) check(`${label} retarget up from ${format(from)} keeps rising (start < target)`, s < target, `start=${format(s)}`);
        paths++;
      }
    }
  }
}

// ④ 非 compact 格式（卡頁價格 / 人口）：簽名永遠一樣，起點 ≤ target 就得，沿途自然唔變
for (const locale of Object.keys(intlLocale)) {
  const format = standard(locale, "USD");
  const s = tickerStart(null, 1234, format);
  check(`${locale} standard format first-run start in (0, target]`, s > 0 && s <= 1234, `start=${s}`);
  check(`${locale} standard format same-unit retarget keeps shown value`, tickerStart(5000, 1234, format) === 5000);
}

// ⑤ 守門：target ≤ 0 / NaN → 唔滾（回 from ?? 0）
const usd = compact("en", "USD");
check("target 0 returns from ?? 0", tickerStart(null, 0, usd) === 0 && tickerStart(42, 0, usd) === 42);
check("target NaN returns from ?? 0", tickerStart(null, Number.NaN, usd) === 0);

// ⑥ 具體例（投訴嘅場景）：$2.7B 第一次由 $1B 起（唔係 $0）；万→億（ja JPY）由 1億 起
check("$2.7B first run starts at $1B", usd(tickerStart(null, 2_700_000_000, usd)) === "$1B", usd(tickerStart(null, 2_700_000_000, usd)));
const jpy = compact("ja", "JPY");
const jaStart = jpy(tickerStart(null, 12_310_000_000, jpy));
check("￥123.1億 first run starts inside 億 range", unitSignature(jaStart) === unitSignature(jpy(12_310_000_000)), jaStart);

/* ⑦ 真動畫路徑（唔止線性採樣）：CapTicker 用 `tickerEase(now - start, duration)`，而 `now` 係 rAF frame
   時間戳，可以早過 layout effect 入面攞嘅 `start` → elapsed 負數。冇夾低位嘅 ease-out cubic 喺 p<0
   會回負值，顯示值跌到起點以下 —— dev :3901 實測 en/USD 起點 $1B 第一 frame 出咗 $993.75M（單位跳返 M）。
   呢度由 -2 frame 掃到 duration + 2 frame，逐點行返同一條公式，單位簽名唔准變。 */
const FRAME = 1000 / 60;
for (const elapsed of [-1000, -100, -FRAME, -0.001, 0, 1, 450, 899, 900, 1200]) {
  const e = tickerEase(elapsed, 900);
  check(`tickerEase(${elapsed}) in [0,1]`, e >= 0 && e <= 1, `got ${e}`);
}
check("tickerEase(negative) === 0 (唔准 undershoot)", tickerEase(-FRAME, 900) === 0, `got ${tickerEase(-FRAME, 900)}`);
check("tickerEase(duration) === 1", tickerEase(900, 900) === 1);
check("tickerEase 單調不減", (() => {
  let prev = -1;
  for (let t = -60; t <= 960; t += 4) { const e = tickerEase(t, 900); if (e < prev - 1e-12) return false; prev = e; }
  return true;
})());
check("tickerEase duration 0 → 1（唔准出 NaN）", tickerEase(0, 0) === 1 && tickerEase(5, 0) === 1);

let animPaths = 0;
for (const locale of Object.keys(intlLocale)) {
  for (const currency of currencies) {
    const format = compact(locale, currency);
    for (const target of targetsUsd.map((usd) => usd * rate[currency])) {
      const from = tickerStart(null, target, format);
      if (from === target) continue;
      const units = new Set();
      for (let elapsed = -2 * FRAME; elapsed <= 900 + 2 * FRAME; elapsed += FRAME / 2) {
        const eased = tickerEase(elapsed, 900);
        units.add(unitSignature(format(eased >= 1 ? target : from + (target - from) * eased)));
      }
      check(`${locale}/${currency} ${format(target)} rAF path keeps one unit`, units.size === 1, [...units].join(" | "));
      animPaths++;
    }
  }
}

/* ⑧ 單位範圍邊緣密掃。上面 targetsUsd 只係手揀嘅幾個值，跨貨幣乘完匯率就未必再貼住邊緣；
   而「起點揀錯邊」正正只喺邊緣爆（target 喺範圍頂 0.01% 之內 → 搵唔到更高嘅同單位起點）。
   每個 10 冪嘅上下各幾檔一齊掃，上／落兩個方向都要方向唔倒轉、沿途單位唔變。 */
let edgePaths = 0;
for (const locale of Object.keys(intlLocale)) {
  for (const currency of currencies) {
    const format = compact(locale, currency);
    for (let p = 1e4; p <= 1e13; p *= 10) {
      for (const f of [0.999999, 0.99999, 0.9999, 0.999, 0.99, 1.000001, 1.0001, 1.01]) {
        const target = p * f;
        const label = `${locale}/${currency} edge target=${format(target)}`;
        for (const from of [target * 40, target / 40]) {
          const s = tickerStart(from, target, format);
          check(`${label} from ${format(from)} start has target unit`, unitSignature(format(s)) === unitSignature(format(target)), `start=${format(s)}`);
          check(`${label} from ${format(from)} 方向唔倒轉`, from > target ? s >= target : s <= target, `start=${format(s)}(${s}) target=${target}`);
          check(`${label} from ${format(from)} path keeps one unit`, pathUnits(s, target, format, 20).size === 1);
          edgePaths++;
        }
      }
    }
  }
}

if (failed.length) {
  console.error(`FAIL test-fe-ticker-unit (${failed.length}):\n - ` + failed.slice(0, 40).join("\n - ") + (failed.length > 40 ? `\n - … +${failed.length - 40}` : ""));
  process.exit(1);
}
console.log(`PASS test-fe-ticker-unit (${paths} paths × 101 samples + ${animPaths} rAF paths × ${Math.round((900 + 4 * FRAME) / (FRAME / 2))} frames（含負 elapsed）+ ${edgePaths} 單位邊緣 paths, 5 locale × ${currencies.length} currency, unit never changes mid-animation, 方向唔倒轉)`);
