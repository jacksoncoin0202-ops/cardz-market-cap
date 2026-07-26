#!/usr/bin/env node
// 對**任意**一份 snapshot 檔跑 production 驗證。
//
// 點解要有：`scripts/verify-public.mjs` 嘅 SNAPSHOT_PATH 寫死咗
// `data/public/seed-snapshot.json`，即係只驗得到「已發佈」嗰份。出街前想驗
// 一份 candidate（`canonical_public_snapshot.py --output temp/x.json`）就冇工具，
// 每次都要臨時寫個 mjs —— 呢個檔就係嚟取代嗰堆一次性腳本。
//
// 用法：
//   node scripts/validate-snapshot.mjs temp/prod-candidate9.json
//   node scripts/validate-snapshot.mjs data/public/seed-snapshot.json --dev
//
// exit 0 = 過，1 = 有 error，2 = 讀唔到檔／validator 載入唔到（**唔等於過**）。
//
// ⚠ 兩個踩過嘅坑，睇住：
//   1. option 係 `{ production: true }`，唔係 `{ mode: "production" }`。傳錯個 key
//      唔會報錯，只會靜靜地淨係行 base 驗證。
//   2. `validatePublicSnapshot()` 直接 return `string[]`，唔係 `{ errors }`。
//      寫 `result.errors ?? []` 會永遠攞到空陣列 → 假 0 error。
//   兩個一齊中就會出「0 errors」而其實乜都冇驗過。實測中過。

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const LOCALES = ["en", "zhTW", "zhCN", "ja"];
const MIN_STORY_CHARS = 80;

const args = process.argv.slice(2);
const target = args.find((arg) => !arg.startsWith("--"));
const production = !args.includes("--dev");

if (!target) {
  console.error("用法: node scripts/validate-snapshot.mjs <snapshot.json> [--dev]");
  process.exit(2);
}

let validatePublicSnapshot;
try {
  // 一定要 pathToFileURL —— Windows 上面 `C:\...` 會被 ESM loader 當成
  // 「protocol 'c:'」而拒收。直接傳 path.join() 落去喺 Linux 行得，喺 Windows 死。
  ({ validatePublicSnapshot } = await import(
    pathToFileURL(path.join(ROOT, "packages", "market-data", "dist", "index.js")).href
  ));
} catch (error) {
  console.error(`載入唔到 validator（要先 npm run build --workspace @cardz/market-data）: ${error.message}`);
  process.exit(2);
}

let snapshot;
try {
  snapshot = JSON.parse(fs.readFileSync(path.resolve(ROOT, target), "utf-8"));
} catch (error) {
  console.error(`讀唔到 ${target}: ${error.message}`);
  process.exit(2);
}

const storyComplete = (card) =>
  LOCALES.every((locale) => {
    const value = card.stories?.[locale];
    return typeof value === "string" && value.trim().length >= MIN_STORY_CHARS;
  });

const top100 = snapshot.top100 ?? [];
const watchlist = snapshot.watchlist ?? [];
const distinct = top100.filter(
  (card) => new Set(LOCALES.map((locale) => card.stories?.[locale]?.trim())).size === LOCALES.length,
).length;

console.log(`檔案      ${target}`);
console.log(`模式      ${production ? "production" : "dev"} | generation.mode=${snapshot.generation?.mode}`);
console.log(`發佈      top100 ${top100.length} + watchlist ${watchlist.length}`);
// 故事閘只查 top100（`validate.ts:283-287`），所以兩層要分開報 —— 撈埋一齊講
// 「151 條缺故事」會令人以為上線卡住，其實 watchlist 缺故事根本唔入閘。
console.log(`故事      top100 ${top100.filter(storyComplete).length}/${top100.length} 四語齊、${distinct}/${top100.length} 互不相同（入閘）`);
console.log(`          watchlist ${watchlist.filter(storyComplete).length}/${watchlist.length} 四語齊（唔入閘，只影響詳情頁完成度）`);

const errors = validatePublicSnapshot(snapshot, { production });
if (errors.length === 0) {
  console.log(`\n✅ 0 error`);
  process.exit(0);
}

console.log(`\n❌ ${errors.length} error\n`);
const buckets = new Map();
for (const message of errors) {
  const key = message.replace(/\[\d+\]/g, "[N]");
  buckets.set(key, (buckets.get(key) ?? 0) + 1);
}
for (const [key, count] of [...buckets].sort((a, b) => b[1] - a[1])) {
  console.log(`${String(count).padStart(4)}  ${key}`);
}
process.exit(1);
