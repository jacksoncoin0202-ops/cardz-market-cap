// 出街閘：canonical snapshot 入面唔准帶供應商代號，公開 card id 唔准出 cmc_
// 命名空間。呢個 document 會原封不動 ship 落 client payload，所以「snapshot 有」
// 等於「view-source 有」。
//
// 點解獨立一個檔而唔係寫喺 bake 入面：bake 要開 MySQL 3308、要 tsc、要幾分鐘。
// 一個淨係喺嗰個情況下先行得到嘅檢查，冇人證得到佢真係會炸。呢度分開之後
// scripts/test-public-surface-gate.mjs 可以兩個方向都行一次（乾淨 → 過、污糟 →
// 炸），一秒有答案。
//
// 以前有個 scripts/canary-public.mjs 喺 deploy 之後掃 HTML，2026-08-07 連同兩個
// caller 一齊刪咗。就算佢仲喺度都攔唔到今日呢批嘢：個 pattern 用 \b 做邊界，而
// `_` 係 word character，所以 `g10_356a…` 一世都 match 唔到；佢個 path list 亦
// 從來冇 /card/*。所以呢個版本唔係翻譯佢，係搬去 producer 側重寫。
//
// 兩條 ratchet，只准跌唔准升：
//   1. 公開 card id 必須喺 cmc_ 命名空間，一個例外都冇。
//   2. 結構欄位入面唔准出現供應商代號。編輯故事（story）係市場評論，會提到交易
//      平台，另外計數 baseline。
//
// 任何一個 baseline 升 => bake 失敗。跌 => 出提示叫人收緊個數，唔好留住張過期
// 嘅免死金牌。
//
// LEGACY_PUBLIC_IDS 嘅短命史：2026-08-11 早上開呢個閘嗰陣，catalog_variant 有 4
// 行 opaque_id 帶住供應商前綴，其中 3 行已經上榜出咗街，所以要暫時放行。同日
// migration 041 + pipelines/public_card_alias.py 起咗 alias 層，重 bake 之後個閘
// 自己出提示話 0/3 仲喺度，於是收到零。舊 URL 由 apps/web/src/lib/legacy-card-ids.ts
// 出 308。保留呢個空 Set 唔係裝飾：佢係下一次「唯讀放行一個舊 id」嘅唯一入口，
// 而一放行就會喺呢度睇得見。

export const ID_SHAPE = /^cmc_[0-9a-f]{20,24}$/;

export const LEGACY_PUBLIC_IDS = new Set([]);

export const FORBIDDEN_TOKENS = [
  "g10",
  "grade10",
  "gemrate",
  "snkrdunk",
  "sneakerdunk",
  "altxyz",
  "ebay",
  "pricecharting",
];

// 前後都要係非 [A-Za-z0-9]：咁 `ebay_sales`、`g10_356a…` 呢類 underscore 接落去
// 嘅 token 一樣攔得到，正正係舊 canary 個 \b 漏咗嗰種。
export const TOKEN_PATTERN = new RegExp(
  `(?<![A-Za-z0-9])(?:${FORBIDDEN_TOKENS.join("|")})(?![A-Za-z0-9])`,
  "i",
);

// 編輯故事嗰個 bucket 認 path segment。canonical snapshot 叫 `stories`（複數），
// view model 叫 `story`（單數）—— 兩個都要認。用 `/story/i` 掃成條 path 係唔夠嘅：
// "stories" 入面根本冇 "story" 呢六個字母，實測會將市場評論當成結構泄漏，
// 一 bake 就炸。
export const STORY_PATH = /(?:^|\.)stor(?:y|ies)(?:\.|\[|$)/i;

// 2026-08-16：出街 snapshot db3308_b2fa581189fddbeb 故事提及已跌到 0。
// 閘只准收緊；test-public-surface-gate 要求實測數 = baseline，唔降就擋每日鏈。
export const STORY_TOKEN_BASELINE = 0;

/**
 * 掃一份 canonical PublicMarketSnapshot。過 => return 個 gate 統計；唔過 => throw。
 *
 * @param {{top100: any[], watchlist: any[]}} snapshot
 * @param {{warn?: (message: string) => void}} [options]
 */
export function assertPublicSurface(snapshot, options = {}) {
  const warn = options.warn ?? ((message) => process.stderr.write(message));

  const publicIds = [...snapshot.top100, ...snapshot.watchlist].map((card) => card.id);
  const badIds = publicIds.filter((id) => !ID_SHAPE.test(id) && !LEGACY_PUBLIC_IDS.has(id));
  if (badIds.length) {
    throw new Error(
      `bake failed: ${badIds.length} public card id(s) outside the cmc_ namespace: `
      + `${badIds.slice(0, 10).join(", ")}${badIds.length > 10 ? " …" : ""}. `
      + "A public id is a public URL and a sitemap entry -- fix the mint "
      + "(pipelines/db_runtime.py upsert_variant) rather than widening this gate.",
    );
  }

  const structuralHits = [];
  let storyHits = 0;
  const walk = (node, path) => {
    if (typeof node === "string") {
      if (!TOKEN_PATTERN.test(node)) return;
      if (LEGACY_PUBLIC_IDS.has(node)) return;
      if (STORY_PATH.test(path)) storyHits += 1;
      else structuralHits.push(`${path} = ${JSON.stringify(node.slice(0, 120))}`);
      return;
    }
    if (Array.isArray(node)) {
      node.forEach((item, index) => walk(item, `${path}[${index}]`));
      return;
    }
    if (node && typeof node === "object") {
      for (const [key, value] of Object.entries(node)) walk(value, path ? `${path}.${key}` : key);
    }
  };
  walk(snapshot, "");

  if (structuralHits.length) {
    throw new Error(
      `bake failed: provider token in ${structuralHits.length} structural field(s):\n`
      + `${structuralHits.slice(0, 10).map((hit) => `  ${hit}`).join("\n")}`
      + `${structuralHits.length > 10 ? "\n  …" : ""}\n`
      + "This document is shipped to the browser verbatim. Drop the field at the "
      + "projection, do not add it to FORBIDDEN_TOKENS' exceptions.",
    );
  }
  if (storyHits > STORY_TOKEN_BASELINE) {
    throw new Error(
      `bake failed: editorial stories name a provider ${storyHits} time(s), baseline is `
      + `${STORY_TOKEN_BASELINE}. A new one appeared -- either rewrite the story, or raise `
      + "the baseline deliberately with the owner's call on the record.",
    );
  }

  const gate = {
    publicIds: publicIds.length,
    legacyIdsStillPublished: publicIds.filter((id) => LEGACY_PUBLIC_IDS.has(id)).length,
    storyTokenHits: storyHits,
    storyTokenBaseline: STORY_TOKEN_BASELINE,
  };
  if (gate.legacyIdsStillPublished < LEGACY_PUBLIC_IDS.size) {
    warn(
      `note: only ${gate.legacyIdsStillPublished}/${LEGACY_PUBLIC_IDS.size} legacy ids are still `
      + "published -- shrink LEGACY_PUBLIC_IDS so the gate stays honest.\n",
    );
  }
  if (storyHits < STORY_TOKEN_BASELINE) {
    warn(`note: story token hits dropped to ${storyHits} -- lower STORY_TOKEN_BASELINE.\n`);
  }
  return gate;
}
