#!/usr/bin/env node
/*
 * 每頁數量嘅契約（owner 2026-08-19：「永遠得 500 呀，冇 1000 嘅。最多都係 500，
 * 唔可以有一千，剷曬 1000 啲掣啦。」）。
 *
 * 兩個方向都要守，缺一唔可：
 *  · UI 一粒大過 500 嘅掣都唔准有 —— 而且要靠 `lib/pagination.ts` 個 array 生效，
 *    唔可以三個 component 各自寫死一條 list（咁樣剷一次就漏兩處）。
 *  · 舊 `?size=1000` link（bookmark／爬蟲手上嗰啲）唔准變 404，但亦唔准照出 1000 行 ——
 *    要對返落 500。
 *
 * P6 唔係「睇下有冇個 guard」，係**真係整一份加返 1000 嘅 pagination.ts 落 temp 度
 * import 佢**，逼個 guard 喺每次 run test 嗰陣 fire 一次。有檢查但冇 call site
 * 就當冇檢查（AGENTS.md）。
 */
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { pathToFileURL } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const read = (path) => readFileSync(join(ROOT, path), "utf8");
const failed = [];
const check = (label, condition, detail = "") => {
  if (!condition) failed.push(detail ? `${label} —— ${detail}` : label);
};

const PAGINATION = "apps/web/src/lib/pagination.ts";
const { RANKING_PAGE_SIZES, DEFAULT_RANKING_PAGE_SIZE, parseRequestedPageSize, rankingRowCap } =
  await import(pathToFileURL(join(ROOT, PAGINATION)).href);

/* ── P1：UI 名單本身 ── */
{
  const max = Math.max(...RANKING_PAGE_SIZES);
  check("P1: 冇任何數量大過 500", max <= 500, `最大 = ${max}`);
  check("P1: 1000 唔喺選項入面", !RANKING_PAGE_SIZES.includes(1000), `list = ${RANKING_PAGE_SIZES.join(",")}`);
  check("P1: 預設仍然係名單入面嘅值", RANKING_PAGE_SIZES.includes(DEFAULT_RANKING_PAGE_SIZE), `default = ${DEFAULT_RANKING_PAGE_SIZE}`);
}

/* ── P2：三排掣全部由同一個 array 出 ──
   剷一個數量係改一個 array 就三處一齊冇，唔係去三個 component 度捉。 */
{
  const surfaces = [
    ["apps/web/src/components/ranking-surface.tsx", /RANKING_PAGE_SIZES\.map\(/],
    ["apps/web/src/components/rankings.tsx", /\{RANKING_PAGE_SIZES\.map\(/],
    ["apps/web/src/components/sort-filter-sheet.tsx", /\{RANKING_PAGE_SIZES\.map\(/],
  ];
  for (const [file, pattern] of surfaces) {
    const src = read(file);
    check(`P2: ${file} 排數量掣行 RANKING_PAGE_SIZES`, pattern.test(src));
    check(`P2: ${file} 冇自己寫死一條數量 list`, !/\[\s*100\s*,\s*200\s*,/.test(src));
  }
}

/* ── P3：舊 link 唔准 404，亦唔准出返 1000 ── */
{
  check("P3: ?size=1000 對落 500", parseRequestedPageSize("1000") === 500, `= ${parseRequestedPageSize("1000")}`);
  check("P3: ?size=1000 唔係 null（唔准 404）", parseRequestedPageSize("1000") !== null);
  for (const size of RANKING_PAGE_SIZES) {
    check(`P3: ?size=${size} 照收`, parseRequestedPageSize(String(size)) === size);
  }
  check("P3: 冇傳 = 預設", parseRequestedPageSize(undefined) === DEFAULT_RANKING_PAGE_SIZE);
}

/* ── P4：唔喺名單就係錯，唔准 clamp 去最近嘅合法值 ── */
{
  for (const bad of ["750", "0", "-1", "01", "2zzz", "abc", "1.5", ""]) {
    check(`P4: ?size=${JSON.stringify(bad)} = null`, parseRequestedPageSize(bad) === null, `= ${parseRequestedPageSize(bad)}`);
  }
}

/* ── P5：render 上限 —— 所有選得嘅數量都停喺 500 ──
   owner 投訴嗰個 800 行 lag 機唔准由「數量掣」呢條路返嚟。 */
{
  for (const size of RANKING_PAGE_SIZES) {
    check(`P5: size ${size} 嘅 rowCap = 500`, rankingRowCap(size) === 500, `= ${rankingRowCap(size)}`);
  }
  check("P5: 舊 1000 link 對落 500 之後 rowCap 都係 500", rankingRowCap(parseRequestedPageSize("1000")) === 500);
}

/* ── P6：個 guard 真係會 fire ──
   整一份「加返 1000」嘅 pagination.ts 落 temp，import 佢要即刻掟 error。
   佢炸唔起 = 呢個 module 冇咗防線，之後有人加返 1000 落 array 就靜靜出街。 */
{
  const dir = mkdtempSync(join(tmpdir(), "cardz-pagesize-guard-"));
  try {
    const mutated = read(PAGINATION).replace(
      "export const RANKING_PAGE_SIZES = [100, 200, 300, 500] as const;",
      "export const RANKING_PAGE_SIZES = [100, 200, 300, 500, 1000] as const;",
    );
    check("P6: 改得到嗰行（改咗個 array 寫法就要順手更新呢個 test）", mutated.includes("500, 1000]"));
    const probe = join(dir, "pagination.probe.ts");
    writeFileSync(probe, mutated, "utf8");
    let message = "";
    try {
      await import(pathToFileURL(probe).href);
    } catch (error) {
      message = String(error?.message ?? error);
    }
    check("P6: 加返 1000 落 array 會即刻炸", message.includes("唔准大過 500"), `掟嘅係：${message || "(乜都冇掟)"}`);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

if (failed.length) {
  console.error(`FAIL test-fe-pagination (${failed.length})`);
  for (const line of failed) console.error(`  - ${line}`);
  process.exit(1);
}
console.log("PASS test-fe-pagination — UI 最多 500、三排掣同一個來源、舊 ?size=1000 對落 500 唔 404、加返 1000 即刻炸");
