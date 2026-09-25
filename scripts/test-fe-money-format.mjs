#!/usr/bin/env node
/*
 * 金額格式（review 2026-09-23 A5）。中日韓 compact 用萬／億，Intl 預設出「US$3483.29萬」：
 * 冇千位分隔（compact 預設 useGrouping "min2"），兼 6 位有效數字。
 * 合約：
 *   ① 中日韓 compact：千位分隔，最多 4 位有效數字（同 2 位小數比，揀精度低嗰個）。
 *   ② en compact 同非 compact（卡價）一個字都唔准變：逐個值對住舊 Intl 參數。
 * 真係 import lib/format.ts（Node strip types；extensionless import 靠 registerHooks 補 .ts）。
 * 負控制：舊參數對住 ① 一定要紅，證明呢個 test 捉得到舊格式。
 * run_all_tests.py glob `scripts/test-*.mjs`。
 */
import { registerHooks } from "node:module";
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
const failed = [];
const check = (label, condition, detail = "") => {
  if (!condition) failed.push(detail ? `${label} —— ${detail}` : label);
};

const { formatDeltaMoney, formatMoney } = await import(
  `file://${join(ROOT, "apps/web/src/lib/format.ts").replaceAll("\\", "/")}`
);

const intlLocale = { en: "en-US", "zh-TW": "zh-Hant-TW", "zh-CN": "zh-Hans-CN", ja: "ja-JP", ko: "ko-KR" };
const CJK = ["zh-TW", "zh-CN", "ja", "ko"];
const rates = { USD: 1, JPY: 150, TWD: 32, KRW: 1350, HKD: 7.8, EUR: 0.92 };
const currencies = Object.keys(rates);
const money = (usd, locale, currency = "USD", compact = true) => formatMoney(usd, currency, rates, locale, compact);

/* 改之前 format.ts 嘅參數：鎖 en 同非 compact 唔變，兼做負控制 */
function oldFormat(usd, locale, currency, compact) {
  const converted = usd * rates[currency];
  return new Intl.NumberFormat(intlLocale[locale], {
    style: "currency",
    currency,
    maximumFractionDigits: compact ? 2 : converted < 100 ? 2 : 0,
    notation: compact ? "compact" : "standard",
  }).format(converted);
}

const ungrouped = (text) => /\d{4}/.test(text);
const sigDigits = (text) => text.replace(/\D/g, "").replace(/^0+/, "").length;
/* US$1 – US$1T，log 掃 193 點；乘埋匯率，最大 ₩1,350조，冇超出最大單位 */
const sweep = Array.from({ length: 193 }, (_, k) => 10 ** (k / 16));

/* ── 負控制：舊參數喺中日韓一定踩中 ①，唔係嘅話呢個 test 捉唔到舊格式 ── */
const oldLive = oldFormat(34_832_900, "zh-TW", "USD", true);
check("負控制：舊格式 zh-TW 34,832,900 係 US$3483.29萬", oldLive === "US$3483.29萬", `got ${oldLive}`);
const oldBad = CJK.flatMap((locale) => currencies.flatMap((currency) => sweep.map((usd) => oldFormat(usd, locale, currency, true))))
  .filter((text) => ungrouped(text) || sigDigits(text) > 4).length;
check("負控制：舊格式喺掃描入面有違規", oldBad > 0, `oldBad=${oldBad}`);

/* ── ① 例子（USD）：live 榜上嘅 US$8886.75萬／US$3483.29萬，加埋未夠萬同億 ── */
const EXPECT = {
  "zh-TW": [
    [88_867_500, "US$8,887萬"], [34_832_900, "US$3,483萬"], [2_432_500, "US$243.3萬"], [380_440, "US$38.04萬"],
    [12_346, "US$1.23萬"], [2_134.87, "US$2,135"], [145.1, "US$145.1"], [1_131_000_000, "US$11.31億"], [27_120_000_000, "US$271.2億"],
  ],
  "zh-CN": [[34_832_900, "US$3,483万"], [1_131_000_000, "US$11.31亿"]],
  ja: [[34_832_900, "$3,483万"], [1_131_000_000, "$11.31億"]],
  ko: [[34_832_900, "US$3,483만"], [1_131_000_000, "US$11.31억"]],
  en: [[88_867_500, "$88.87M"], [34_832_900, "$34.83M"], [380_440, "$380.44K"], [2_134.87, "$2.13K"], [145.1, "$145.1"], [1_131_000_000, "$1.13B"]],
};
for (const [locale, rows] of Object.entries(EXPECT)) {
  for (const [usd, want] of rows) {
    const got = money(usd, locale);
    check(`${locale} compact ${usd} = ${want}`, got === want, `got ${got}`);
  }
}

/* ── ① 掃：中日韓 × 6 隻貨幣，唔准有 4 個數字連住（即係冇分組），有效數字 ≤ 4 ── */
let swept = 0;
for (const locale of CJK) {
  for (const currency of currencies) {
    for (const usd of sweep) {
      const got = money(usd, locale, currency);
      check(`${locale}/${currency} ${usd} 有千位分隔`, !ungrouped(got), got);
      check(`${locale}/${currency} ${usd} ≤ 4 位有效數字`, sigDigits(got) <= 4, got);
      swept++;
    }
  }
}

/* ── ② en compact 同非 compact：逐個值同舊格式一模一樣 ── */
for (const locale of Object.keys(intlLocale)) {
  for (const currency of currencies) {
    for (const usd of sweep) {
      if (locale === "en") {
        const got = money(usd, "en", currency);
        const want = oldFormat(usd, "en", currency, true);
        check(`en/${currency} compact ${usd} 同舊格式一樣`, got === want, `got ${got} want ${want}`);
      }
      const got = money(usd, locale, currency, false);
      const want = oldFormat(usd, locale, currency, false);
      check(`${locale}/${currency} 非 compact ${usd} 同舊格式一樣`, got === want, `got ${got} want ${want}`);
    }
  }
}

/* ── 升跌金額（formatDeltaMoney）行同一條路 ── */
const ready = (value) => ({ value, status: "ready", asOf: "2026-09-23T00:00:00Z" });
const delta = formatDeltaMoney(ready(34_832_900), ready(-6.5), "USD", rates, "zh-TW");
check("formatDeltaMoney 中文：−號 + 千位分隔 + ≤ 4 位", typeof delta === "string" && delta.startsWith("−") && !ungrouped(delta) && sigDigits(delta) <= 4, `got ${delta}`);

if (failed.length) {
  console.error(`FAIL test-fe-money-format (${failed.length})`);
  for (const line of failed.slice(0, 40)) console.error(`  - ${line}`);
  if (failed.length > 40) console.error(`  - … +${failed.length - 40}`);
  process.exit(1);
}
console.log(`PASS test-fe-money-format — 中日韓 compact 千位分隔 + ≤ 4 位有效數字（${swept} 點），en compact／非 compact 一個字都冇變（負控制舊格式 ${oldBad} 點違規）`);
process.exit(0);
