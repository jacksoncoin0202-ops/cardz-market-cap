#!/usr/bin/env node
/*
 * indexnow-ping.mjs —— 每日 bake 之後同 Bing／Yandex 講「呢幾條 URL 更新咗」。
 *
 * 點解要有：我哋啲數日日變，等爬蟲自己嚟可以拖幾日；IndexNow 係 Microsoft 官方
 * 嗰條 freshness 通道，Copilot 亦係我哋而家最健康嗰個引擎。一個 POST，冇成本。
 *
 * 前置（一次過）：
 *   1. apps/web/public/<key>.txt 內容 = key 本身（呢個 repo 已經有，同 indexnow-key.txt 同一個 key）。
 *   2. 部署之後 https://cardzmarketcap.com/<key>.txt 要回 200 —— 冇呢步 IndexNow 會 403。
 *
 * 用法：
 *   node apps/web/scripts/geo/indexnow-ping.mjs                      # 預設核心 URL
 *   node apps/web/scripts/geo/indexnow-ping.mjs /card/cmc_x /pokemon # 指定路徑或完整 URL
 *   node apps/web/scripts/geo/indexnow-ping.mjs --base https://cardzmarketcap.com --dry-run
 *
 * 冇任何依賴（Node 18+ 內建 fetch）。IndexNow 成功係 200 或者 202。
 */

import { readdir, readFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const WEB_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
const PUBLIC_DIR = join(WEB_ROOT, "public");
const ENDPOINT = "https://api.indexnow.org/indexnow";

const args = process.argv.slice(2);
const flag = (name, fallback) => {
  const index = args.indexOf(`--${name}`);
  return index >= 0 && args[index + 1] ? args[index + 1] : fallback;
};
const DRY_RUN = args.includes("--dry-run");
const BASE = flag("base", process.env.NEXT_PUBLIC_SITE_URL ?? "https://cardzmarketcap.com").replace(/\/$/, "");
/* 只有 --base 食值；--dry-run 唔食。逐個 flag 分清楚，否則 `--dry-run /pokemon` 會靜靜吞咗條 path。 */
const VALUE_FLAGS = new Set(["--base"]);
const positional = args.filter((value, index) => !value.startsWith("--") && !VALUE_FLAGS.has(args[index - 1] ?? ""));

/* 每日一定要 ping 嘅面：榜頁同機讀面。卡頁太多，逐張 ping 由呼叫方傳入。 */
const DEFAULT_PATHS = [
  "/",
  "/pokemon",
  "/one-piece",
  "/watchlist",
  "/box",
  "/rankings",
  "/market-report",
  "/methodology",
  "/data",
  "/faq",
  "/llms.txt",
  "/llms-full.txt",
  "/sitemap.xml",
];

async function readKey() {
  /* 權威係 public/<key>.txt（IndexNow 驗證檔本身）。indexnow-key.txt 只係人手方便睇。 */
  const files = await readdir(PUBLIC_DIR);
  const keyFile = files.find((name) => /^[a-f0-9]{32}\.txt$/i.test(name));
  if (!keyFile) {
    throw new Error(`public/ 入面搵唔到 <32-hex>.txt key 檔（睇 ${PUBLIC_DIR}）`);
  }
  const key = (await readFile(join(PUBLIC_DIR, keyFile), "utf8")).trim();
  if (key !== keyFile.replace(/\.txt$/i, "")) {
    throw new Error(`key 檔內容同檔名唔一致：${keyFile} 內容 "${key}" —— IndexNow 會 403`);
  }
  return key;
}

const key = await readKey();
const host = new URL(BASE).host;
const urlList = (positional.length ? positional : DEFAULT_PATHS).map((value) =>
  value.startsWith("http") ? value : `${BASE}${value.startsWith("/") ? "" : "/"}${value}`,
);

const payload = {
  host,
  key,
  keyLocation: `${BASE}/${key}.txt`,
  urlList,
};

console.log(`IndexNow → ${ENDPOINT}`);
console.log(`  host        ${payload.host}`);
console.log(`  keyLocation ${payload.keyLocation}`);
console.log(`  urlList     ${urlList.length} URL`);
for (const url of urlList) console.log(`    ${url}`);

if (DRY_RUN) {
  console.log("\n--dry-run：冇真係 POST。");
  process.exit(0);
}

const response = await fetch(ENDPOINT, {
  method: "POST",
  headers: { "content-type": "application/json; charset=utf-8" },
  body: JSON.stringify(payload),
});
const text = await response.text();
console.log(`\nHTTP ${response.status} ${response.statusText}`);
if (text.trim()) console.log(text.trim());

/* 200 = 收到；202 = 收到但 key 仲驗緊。其他一律當 fail，唔好靜靜當 ping 咗。 */
if (response.status !== 200 && response.status !== 202) {
  console.log("403 多數係 keyLocation 攞唔到（部署未出／public 檔冇跟住去）。");
  process.exit(1);
}
