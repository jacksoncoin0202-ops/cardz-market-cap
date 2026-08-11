#!/usr/bin/env node
// 證明 scripts/public-surface-gate.mjs 真係會炸。
//
// 點解要有呢個 test：呢個 repo 已經有兩件「寫咗但零 call site」嘅檢查
// （apps/web 個 isOpaquePublicId、同埋刪咗嘅 canary-public.mjs 個 PRIVATE_TOKENS），
// 兩件都令下一個人更加信錯。一個淨係喺 bake（要 MySQL 3308 + tsc + 幾分鐘）先行
// 得到嘅閘，冇人證得到佢會 fire，實際上同冇分別。所以呢度兩個方向都行：
// 出街嗰份真 snapshot 要過，而每一種泄漏形狀都要炸。
//
// Run: node scripts/test-public-surface-gate.mjs

import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import {
  assertPublicSurface,
  LEGACY_PUBLIC_IDS,
  STORY_TOKEN_BASELINE,
} from "./public-surface-gate.mjs";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const SNAPSHOT = join(ROOT, "data", "public", "seed-snapshot.json");

const failed = [];
let checks = 0;

const check = (label, got, want) => {
  checks += 1;
  if (JSON.stringify(got) !== JSON.stringify(want)) {
    failed.push(`FAIL ${label}\n  got  ${JSON.stringify(got)}\n  want ${JSON.stringify(want)}`);
  }
};

/** 個閘一定要炸，而且要炸中預期嗰個原因。 */
const throws = (label, snapshot, expectedFragment) => {
  checks += 1;
  let message = null;
  try {
    assertPublicSurface(snapshot, { warn: () => {} });
  } catch (error) {
    message = error.message;
  }
  if (message === null) failed.push(`FAIL ${label}\n  個閘冇炸 —— 呢個形狀出得街`);
  else if (!message.includes(expectedFragment)) {
    failed.push(`FAIL ${label}\n  炸咗但唔係嗰個原因：${message.split("\n")[0]}`);
  }
};

const passes = (label, snapshot) => {
  checks += 1;
  try {
    assertPublicSurface(snapshot, { warn: () => {} });
  } catch (error) {
    failed.push(`FAIL ${label}\n  唔應該炸：${error.message.split("\n")[0]}`);
  }
};

// --- 1. 出街嗰份真 snapshot 要過 -------------------------------------------
// 唔用 fixture：呢個 gate 唯一嘅意義係「今日出街嗰份係咩形狀」。兩個 baseline
// 都係實測數，唔係我定嘅目標數。
const shipped = JSON.parse(readFileSync(SNAPSHOT, "utf8"));
const gate = assertPublicSurface(shipped, { warn: () => {} });
check("出街 snapshot 過閘：公開卡數", gate.publicIds > 0, true);
check("出街 snapshot 過閘：一個舊前綴 id 都冇", gate.legacyIdsStillPublished, 0);
check("免死金牌名單已經收到零（alias 層落咗之後）", LEGACY_PUBLIC_IDS.size, 0);
check("出街 snapshot 過閘：故事提及次數 = baseline", gate.storyTokenHits, STORY_TOKEN_BASELINE);

// --- 2. 每一種泄漏形狀都要炸 ------------------------------------------------
const card = (overrides = {}) => ({
  id: "cmc_f698284d7bc333408782e4c6",
  officialName: "2019 Pokemon Sun & Moon Base Charizard 170",
  stories: { en: null, zhTW: null, zhCN: null, ja: null, ko: null },
  ...overrides,
});
const snap = (cards) => ({ top100: cards, watchlist: [] });

passes("乾淨最小 snapshot", snap([card()]));

throws(
  "新鑄一個帶供應商前綴嘅公開 id",
  snap([card({ id: "g10_0123456789abcdef01234567" })]),
  "outside the cmc_ namespace",
);
throws(
  "id 唔喺 cmc_ 命名空間（隨便一個 prefix）",
  snap([card({ id: "candidate_0123456789abcdef" })]),
  "outside the cmc_ namespace",
);
// 舊前綴 id 而家一個都唔放行。呢三個係 2026-08-11 之前真係出咗街嗰批，佢哋一旦
// 再喺 snapshot 出現，就代表 alias 層甩咗（例如有人 revert 咗投影層嗰個 COALESCE）。
for (const retired of [
  "g10_036812c0a409b0fef6ba5dff",
  "g10_356a7d75453fba4d71586411",
  "g10_c43dd6aa54b7671068938692",
]) {
  throws(`已經改咗嘅舊 id 再出現：${retired}`, snap([card({ id: retired })]), "outside the cmc_ namespace");
}

throws(
  "結構欄位出現供應商代號",
  snap([card({ pricePsa10: { value: 1, provider: "GemRate" } })]),
  "structural field",
);
throws(
  "underscore 接落去嘅 token —— 舊 canary 用 \\b 就係喺呢度漏",
  snap([card({ windows: { "30d": { trackedSalesSource: "ebay_sales" } } })]),
  "structural field",
);
throws(
  "供應商代號收埋喺 array 深處",
  snap([card({ historyDaily: [{ at: "2026-08-10", note: "snkrdunk" }] })]),
  "structural field",
);
throws(
  "免死金牌淨係赦免完全一樣嗰條舊 id，唔赦免夾雜住嘅代號",
  snap([card({ pricePsa10: { anchor: "priced off g10 mid" } })]),
  "structural field",
);
// 呢兩條係上面嗰個真 bug 嘅 regression：canonical 叫 `stories`、view model 叫
// `story`，兩個 bucket 都要認得，唔係就會將市場評論當成結構泄漏。
passes(
  "canonical 複數 `stories` 認得係故事 bucket",
  snap([card({ stories: { en: "listed on eBay", zhTW: null, zhCN: null, ja: null, ko: null } })]),
);
passes(
  "view model 單數 `story` 一樣認得",
  snap([card({ stories: undefined, story: { en: "listed on eBay" } })]),
);
throws(
  "個名夾住 story 唔算故事 bucket（`storyboardSource`）",
  snap([card({ storyboardSource: "gemrate" })]),
  "structural field",
);

throws(
  `編輯故事多咗一次提及（baseline ${STORY_TOKEN_BASELINE}）`,
  snap([
    card({ stories: { en: "listed on eBay", zhTW: null, zhCN: null, ja: null, ko: null } }),
    card({ stories: { en: "and on SNKRDUNK", zhTW: null, zhCN: null, ja: null, ko: null } }),
    card({ stories: { en: "and on PriceCharting", zhTW: null, zhCN: null, ja: null, ko: null } }),
  ]),
  "editorial stories name a provider",
);
passes(
  "故事提及次數 <= baseline → 過（今日出街嗰兩句就係咁）",
  snap([
    card({ stories: { en: "platforms like SNKRDUNK", zhTW: null, zhCN: null, ja: null, ko: null } }),
    card({ stories: { en: "early eBay pre-orders", zhTW: null, zhCN: null, ja: null, ko: null } }),
  ]),
);

// --- 3. 唔准誤殺 ------------------------------------------------------------
// 個 pattern 前後都要求非 [A-Za-z0-9]。呢幾個係真卡名／真字會出現嘅形狀，一炸
// 就代表下一個人要靠加例外去 bake，個閘就開始腐爛。
passes(
  "token 黐住字母唔算（g100 / rebay / gemrated）",
  snap([card({ officialName: "g100 rebay gemrated ebayer", stories: { en: "g10x", zhTW: null, zhCN: null, ja: null, ko: null } })]),
);

for (const line of failed) console.log(line);
console.log(`${checks - failed.length}/${checks} checks passed`);
process.exit(failed.length ? 1 : 0);
