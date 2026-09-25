#!/usr/bin/env node
/*
 * 價格圖 Y 軸刻度（2026-09-23 A8）。live 出過「US$2.37萬 / US$1.58萬 / US$7,901」：刻度唔係整數，
 * 單位又一時萬一時冇。moneyAxis（format.ts）而家喺顯示貨幣揀 1／2／2.5／5 × 10^k 嘅刻度，成條軸
 * 一個單位、一個小數位。
 * 鎖死：幾個 live 形狀嘅實際字；31 種貨幣 × 5 種語言 × 9 個價位嘅不變式（等距、整數倍、包住數據、
 * 3–7 條、數據唔好壓到少過 40% 高、字唔重複、除咗 0 單位同小數位一致）；匯率壞 fail-closed；
 * history-chart.tsx 真係行 moneyAxis。
 * run_all_tests.py glob `scripts/test-*.mjs`。
 */
import { readFileSync } from "node:fs";
import { registerHooks } from "node:module";
import { dirname, join, resolve } from "node:path";
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
const { moneyAxis } = await import(pathToFileURL(join(ROOT, "apps/web/src/lib/format.ts")).href);
const { copy } = await import(pathToFileURL(join(ROOT, "apps/web/src/lib/i18n.ts")).href);
const { currencies, locales } = await import(pathToFileURL(join(ROOT, "apps/web/src/lib/types.ts")).href);

/* 匯率只係要個量級啱（IDR／VND 幾萬、GBP < 1），唔使準 */
const RATES = {
  USD: 1, HKD: 7.8, TWD: 32, JPY: 150, KRW: 1350, CNY: 7.2, SGD: 1.35, MYR: 4.7, THB: 36, PHP: 56,
  IDR: 16000, VND: 25000, INR: 83, AUD: 1.5, NZD: 1.65, EUR: 0.92, GBP: 0.79, CHF: 0.88, SEK: 10.5, NOK: 10.7,
  DKK: 6.9, PLN: 4, CZK: 23, CAD: 1.36, MXN: 17, BRL: 5, AED: 3.67, SAR: 3.75, ILS: 3.7, TRY: 32, ZAR: 18.5,
};

const PINNED = [
  // live 嗰張卡嘅形狀（7,901–23,700）：原本出 US$2.37萬 / US$1.58萬 / US$7,901
  ["en", "USD", 7901, 23700, ["$5,000", "$10,000", "$15,000", "$20,000", "$25,000"]],
  ["zh-TW", "USD", 7901, 23700, ["US$5,000", "US$10,000", "US$15,000", "US$20,000", "US$25,000"]],
  // ≥ 100,000 先 compact，單位由頂刻度定，0 唔帶單位
  ["zh-TW", "USD", 150000, 850000, ["US$0", "US$20萬", "US$40萬", "US$60萬", "US$80萬", "US$100萬"]],
  ["en", "USD", 150000, 850000, ["$0", "$0.2M", "$0.4M", "$0.6M", "$0.8M", "$1.0M"]],
  // 平價：幅度最少當 2%，唔會壓成一條罅；小數位一致
  ["en", "USD", 1000000, 1000000, ["$0.98M", "$0.99M", "$1.00M", "$1.01M", "$1.02M"]],
  ["en", "USD", 100, 100, ["$98", "$99", "$100", "$101", "$102"]],
  // 刻度喺顯示貨幣揀：日圓／韓圜／英鎊都係佢哋自己嘅整數
  ["ja", "JPY", 5000, 20000, ["￥0", "￥100万", "￥200万", "￥300万", "￥400万"]],
  ["ko", "KRW", 60000, 110000, ["₩0.6억", "₩0.8억", "₩1.0억", "₩1.2억", "₩1.4억", "₩1.6억"]],
  ["en", "GBP", 12, 13, ["£9.25", "£9.50", "£9.75", "£10.00", "£10.25", "£10.50"]],
  ["en", "USD", 3.2, 4.1, ["$3.0", "$3.5", "$4.0", "$4.5"]],
];
const RANGES = [[7901, 23700], [3.2, 4.1], [150000, 850000], [1e6, 1e6], [100, 100], [12, 13], [0.5, 900], [40, 41000], [2e6, 9e6]];
const NICE = [1, 2, 2.5, 5];

const failed = [];
const close = (a, b, scale) => Math.abs(a - b) <= 1e-9 * Math.max(1, Math.abs(scale));

for (const [locale, currency, min, max, want] of PINNED) {
  const got = moneyAxis(min, max, currency, RATES, locale).ticks.map((tick) => tick.label);
  if (JSON.stringify(got) !== JSON.stringify(want)) {
    failed.push(`moneyAxis(${min}, ${max}, ${currency}, ${locale}) = ${JSON.stringify(got)}, want ${JSON.stringify(want)}`);
  }
}

function checkAxis(where, min, max, currency, rates, locale) {
  const rate = rates[currency];
  const axis = moneyAxis(min, max, currency, rates, locale);
  const values = axis.ticks.map((tick) => tick.valueUsd * rate);
  const labels = axis.ticks.map((tick) => tick.label);
  const problems = [];
  if (values.length < 3 || values.length > 7) problems.push(`${values.length} 條刻度，要 3–7`);
  const step = values[1] - values[0];
  const mantissa = step / 10 ** Math.floor(Math.log10(step) + 1e-9);
  if (!NICE.some((nice) => close(mantissa, nice, nice))) problems.push(`step ${step} 唔係 1/2/2.5/5 × 10^k`);
  values.forEach((value, index) => {
    if (index && !close(value - values[index - 1], step, step)) problems.push(`刻度唔等距：${values.join(", ")}`);
    if (!close(value / step, Math.round(value / step), value / step)) problems.push(`${value} 唔係 step 整數倍`);
  });
  if (axis.lo !== axis.ticks[0].valueUsd || axis.hi !== axis.ticks.at(-1).valueUsd) problems.push("lo/hi 同頭尾刻度唔一致");
  if (axis.lo < 0 || axis.lo > min * (1 + 1e-12) || axis.hi < max * (1 - 1e-12)) problems.push(`軸 ${axis.lo}–${axis.hi} 包唔住數據`);
  if (max > min * 1.05 && (max - min) / (axis.hi - axis.lo) < 0.4) problems.push(`數據淨係佔 ${((max - min) / (axis.hi - axis.lo) * 100).toFixed(0)}% 高`);
  if (new Set(labels).size !== labels.length) problems.push(`字重複：${JSON.stringify(labels)}`);
  if (labels.includes(copy[locale].status.unavailable)) problems.push("匯率正常都出「暫無資料」");
  const nonZero = labels.filter((_, index) => values[index] !== 0);
  if (new Set(nonZero.map((label) => label.replace(/[\d.,\s]/g, ""))).size !== 1) problems.push(`單位唔一致：${JSON.stringify(labels)}`);
  if (new Set(nonZero.map((label) => (label.match(/\.(\d+)/)?.[1] ?? "").length)).size !== 1) problems.push(`小數位唔一致：${JSON.stringify(labels)}`);
  if (problems.length) failed.push(`${where}: ${[...new Set(problems)].join("；")}`);
}

let axes = 0;
for (const currency of currencies) {
  for (const locale of locales) {
    for (const [min, max] of RANGES) {
      axes += 1;
      checkAxis(`${locale} ${currency} ${min}–${max}`, min, max, currency, RATES, locale);
    }
  }
}
/* 冇小數嘅貨幣（JPY）刻度要小數嗰陣都要一致：Intl 預設 JPY 0 位，唔講 minimumFractionDigits 就出「￥3」同「￥3.5」 */
axes += 1;
checkAxis("ja JPY 刻度帶小數（假匯率 0.01）", 300, 450, "JPY", { ...RATES, JPY: 0.01 }, "ja");

/* 匯率壞（0／缺）：同 formatMoney 一樣 fail-closed，唔准出假數，幾何仲要係有限數 */
for (const bad of [0, Number.NaN]) {
  const axis = moneyAxis(7901, 23700, "JPY", { ...RATES, JPY: bad }, "zh-TW");
  const unavailable = copy["zh-TW"].status.unavailable;
  if (!axis.ticks.every((tick) => tick.label === unavailable && Number.isFinite(tick.valueUsd)) || !Number.isFinite(axis.hi - axis.lo) || axis.hi <= axis.lo) {
    failed.push(`JPY rate ${bad}: 要全部「${unavailable}」兼幾何有限，得到 ${JSON.stringify(axis)}`);
  }
}

const chart = readFileSync(join(ROOT, "apps/web/src/components/history-chart.tsx"), "utf8");
for (const needle of ["moneyAxis(min, max, currency, rates, locale)", "(axis.hi - value) / (axis.hi - axis.lo)", "{tick.label}"]) {
  if (!chart.includes(needle)) failed.push(`history-chart.tsx: 要經 moneyAxis 出刻度（搵唔到 ${needle}）`);
}
if (/index \/ 3|formatMoney\(tick/.test(chart)) failed.push("history-chart.tsx: 仲有舊嘅平均切格刻度");

if (failed.length) {
  console.error("FAIL test-fe-chart-axis\n" + failed.map((item) => ` - ${item}`).join("\n"));
  process.exit(1);
}
console.log(`PASS test-fe-chart-axis (${PINNED.length} pinned axes; ${axes} axes over ${currencies.length} currencies × ${locales.length} locales: nice evenly spaced ticks cover the data, one unit and one decimal count per axis, unique labels; bad FX fail-closed; history-chart wired)`);
