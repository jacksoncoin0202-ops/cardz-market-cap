#!/usr/bin/env node
/*
 * 日期字唔准跟 runtime 時區走（2026-09-23 A11）。頁面係 "use client"：SSR 喺 AWS（UTC）出一次，
 * 瀏覽器（用戶多數 UTC+8/+9）hydrate 再出一次，兩串字唔同 = React #418 hydration mismatch。
 * 當日 live /box 量到：SSR「Sep 23, 2026, 4:20 AM」對 hydrate「1:20 PM」，JST 瀏覽器次次中。
 * 合約：
 *   ① 源碼：apps/web/src 每個 `new Intl.DateTimeFormat(…)`、format.ts 每個 `dateFormat(locale, {…})`
 *      都要帶 timeZone（dateFormat 自己個 helper 係透傳 options，豁免）；唔准 toLocaleDateString /
 *      toLocaleTimeString（永遠跟 runtime 時區）。/box 同 /box/[id] 個 asOf 用 formatObservationDate。
 *   ② 行為：4 個時區（TZ env）各開一個 child 跑 formatObservationDate / formatObservationDayMonth，
 *      字要一模一樣。
 * 負控制：同一個 child 用冇 timeZone 嘅 Intl 格式化同一批時間，嗰串一定要跟時區變——
 * 證明 TZ env 真係生效，② 唔係空轉。
 * run_all_tests.py glob `scripts/test-*.mjs`。
 */
import { spawnSync } from "node:child_process";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { registerHooks } from "node:module";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
/* 20:30Z 同 23:59:59Z 喺 UTC+9 已經係第二日；純日期 2026-03-27 係 DB DATE 嘅樣 */
const SAMPLES = ["2026-09-23T04:20:00.000Z", "2026-09-23T20:30:00.000Z", "2026-03-27", "2026-12-31T23:59:59Z"];
/* 原盒 release：DB 係 YYYY-MM（淨係知月份）；YYYY-MM-DD 係將來有日子嘅樣 */
const RELEASES = ["2026-10", "1999-01", "2026-09-16"];
const LOCALES = ["en", "zh-TW", "zh-CN", "ja", "ko"];
const ZONES = ["UTC", "Asia/Tokyo", "America/Los_Angeles", "Pacific/Kiritimati"];

if (process.argv[2] === "--child") {
  registerHooks({
    resolve(specifier, context, next) {
      if (specifier.startsWith(".") && !/\.[a-z]+$/i.test(specifier)) {
        try { return next(`${specifier}.ts`, context); } catch { /* 跌返原本 specifier */ }
      }
      return next(specifier, context);
    },
  });
  const { formatObservationDate, formatObservationDayMonth, formatReleaseDate } = await import(pathToFileURL(join(ROOT, "apps/web/src/lib/format.ts")).href);
  const out = {};
  for (const locale of LOCALES) {
    for (const sample of SAMPLES) out[`${locale} ${sample}`] = [formatObservationDate(sample, locale), formatObservationDayMonth(sample, locale)];
    for (const value of RELEASES) out[`release ${locale} ${value}`] = [formatReleaseDate(value, locale)];
  }
  const control = SAMPLES.map((sample) => new Intl.DateTimeFormat("en-US", { dateStyle: "medium", timeStyle: "short" }).format(new Date(sample)));
  console.log(JSON.stringify({ out, control }));
  process.exit(0);
}

const failed = [];
const check = (label, condition, detail = "") => {
  if (!condition) failed.push(detail ? `${label} —— ${detail}` : label);
};

/* ① 源碼 */
const files = [];
(function walk(dir) {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) walk(path);
    else if (/\.tsx?$/.test(name)) files.push(path);
  }
})(join(ROOT, "apps/web/src"));

/* 由 `(` 之後數到配對嘅 `)`，攞成個參數表 */
function callArgs(text, openParen) {
  let depth = 0;
  for (let i = openParen; i < text.length; i += 1) {
    if (text[i] === "(") depth += 1;
    else if (text[i] === ")" && --depth === 0) return text.slice(openParen + 1, i);
  }
  return "";
}

let calls = 0;
for (const file of files) {
  const rel = relative(ROOT, file).replaceAll("\\", "/");
  const text = readFileSync(file, "utf8").replace(/\/\*[\s\S]*?\*\//g, " ").replace(/^\s*\/\/.*$/gm, " ");
  for (const m of text.matchAll(/new Intl\.DateTimeFormat\(|\bdateFormat\(\s*locale\s*,/g)) {
    const args = callArgs(text, m.index + m[0].indexOf("("));
    /* format.ts 個 helper：options 係透傳，timeZone 由每個 dateFormat(locale, {…}) call 負責 */
    if (rel === "apps/web/src/lib/format.ts" && /^intlLocale\[locale\],\s*options$/.test(args.trim())) continue;
    calls += 1;
    const callee = m[0].slice(0, m[0].indexOf("("));
    check(`${rel}: 日期格式冇 timeZone`, /\btimeZone\s*:/.test(args), `${callee}(${args.replace(/\s+/g, " ").slice(0, 120)})`);
  }
  check(`${rel}: 唔准 toLocaleDateString / toLocaleTimeString（跟 runtime 時區）`, !/\.toLocale(Date|Time)String\(/.test(text));
}
check("掃描搵到日期格式 call", calls >= 6, `只得 ${calls} 個 —— pattern 壞咗，① 會空轉`);

const boxPages = ["apps/web/src/components/box-market-page.tsx", "apps/web/src/components/box-detail.tsx"];
for (const page of boxPages) {
  check(`${page}: asOf 用 formatObservationDate`, /\{t\.labels\.asOf\}: \{formatObservationDate\(/.test(readFileSync(join(ROOT, page), "utf8")));
}
check("box-detail.tsx: 發售日用 formatReleaseDate（YYYY-MM 唔准補 1 號）",
  /<dt>\{t\.box\.release\}<\/dt><dd>\{formatReleaseDate\(product\.release, locale\)\}/.test(readFileSync(join(ROOT, boxPages[1]), "utf8")));

/* ② 行為：每個時區一個 child process（TZ 要喺 process 開頭定，同一個 process 入面改唔穩陣） */
const runs = ZONES.map((zone) => {
  const child = spawnSync(process.execPath, [fileURLToPath(import.meta.url), "--child"], {
    env: { ...process.env, TZ: zone },
    encoding: "utf8",
  });
  check(`child TZ=${zone} 行得`, child.status === 0, (child.stderr || "").trim().split("\n").slice(-3).join(" | "));
  return { zone, data: child.status === 0 ? JSON.parse(child.stdout) : null };
});
const base = runs[0].data;
if (base) {
  check("UTC 基準：20:30Z 仲係 9月23日", base.out["zh-TW 2026-09-23T20:30:00.000Z"][0] === "2026年9月23日", base.out["zh-TW 2026-09-23T20:30:00.000Z"][0]);
  /* 發售月：唔准出「Oct 1, 2026」；有日子嘅就照出日子 */
  for (const [key, want] of [["release en 2026-10", "Oct 2026"], ["release zh-TW 2026-10", "2026年10月"], ["release en 2026-09-16", "Sep 16, 2026"]]) {
    check(`${key} → ${want}`, base.out[key][0] === want, base.out[key][0]);
  }
  for (const run of runs.slice(1)) {
    if (!run.data) continue;
    const diff = Object.keys(base.out).filter((key) => JSON.stringify(base.out[key]) !== JSON.stringify(run.data.out[key]));
    check(`TZ=${run.zone} 同 UTC 出同一串字`, diff.length === 0, diff.slice(0, 3).map((key) => `${key}: ${base.out[key]} ≠ ${run.data.out[key]}`).join("; "));
    /* 負控制：冇 timeZone 嘅 Intl 一定要跟住 TZ 變，唔係即係 TZ env 冇生效 */
    check(`負控制：TZ=${run.zone} 冇 timeZone 嘅字會變`, JSON.stringify(run.data.control) !== JSON.stringify(base.control), run.data.control.join(" / "));
  }
}

if (failed.length) {
  console.error("FAIL test-fe-date-timezone\n" + failed.map((item) => ` - ${item}`).join("\n"));
  process.exit(1);
}
console.log(`PASS test-fe-date-timezone (${calls} 個日期格式 call 帶 timeZone；${ZONES.length} 個時區 × ${LOCALES.length} 語言 × ${SAMPLES.length} 個日期 + ${RELEASES.length} 個發售月字一樣；發售月唔補 1 號；負控制會變)`);
