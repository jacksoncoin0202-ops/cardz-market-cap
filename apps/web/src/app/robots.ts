import type { MetadataRoute } from "next";

/* 同 `lib/public-site.ts` 預設同一個 origin。呢個檔俾 node test 直接 import，唔可以 `@/`。 */
const siteUrl = (process.env.NEXT_PUBLIC_SITE_URL ?? "https://cardzmarketcap.com").replace(/\/$/, "");

export const dynamic = "force-dynamic";

/*
 * 唔畀爬嘅路徑。逐條有理由，唔好順手加：
 * - `/api/health`：運維探針，零內容價值（next.config.ts 亦只剩佢仲掛 noindex）。
 * - `/data/private/`：私密資料。
 * - `/tune`：內部 heatmap 調參室。page metadata 有 `noindex, nofollow`，但嗰個要爬咗先見到，
 *   呢行先係真係唔畀爬。
 * - `/_next/`：build asset，爬到都冇內容。
 *
 * ⚠️ 2026-08-16 起唔再擋成個 `/api/`：`/api/v1/` 係公開 JSON、`/api/og/` 係 social card 圖，
 * 兩條都係俾 AI 引擎攞嚟引用嘅面，擋咗等於自己收埋。
 */
const DISALLOWED_PATHS = ["/api/health", "/data/private/", "/tune", "/_next/"];

/*
 * 明寫出嚟嘅開放路徑。`Allow: /` 技術上已經包晒，但：
 * (1) 有啲爬蟲行 first-match 而唔係 longest-match，見到 `/api/v1/` 有獨立 Allow 先肯行；
 * (2) 人手 review robots.txt 嗰陣，「機讀面係開放嘅」要一眼睇得出，唔靠推理。
 */
const ALLOWED_PATHS = ["/", "/api/v1/", "/api/og/", "/llms.txt", "/llms-full.txt"];

/* (a) 傳統搜尋引擎 —— 索引 + 排名，冇佢哋就冇自然流量。 */
const SEARCH_ENGINE_AGENTS = [
  "Googlebot",
  "Bingbot",
  "DuckDuckBot",
  "Applebot",
  "YandexBot",
  "Baiduspider",
  "Yeti", // NaverBot（韓國）嘅實際 UA token
];

/*
 * (b) AI 搜尋 / 用戶觸發嘅即時抓取。呢批同 (c) 訓練爬蟲係兩件事：
 * 佢哋喺用戶問問題嗰刻先去攞頁，攞到咩就 cite 咩。想被 AI 答案引用，就係靠呢批。
 */
const AI_SEARCH_AGENTS = [
  "OAI-SearchBot",
  "ChatGPT-User",
  "PerplexityBot",
  "Perplexity-User",
  "Claude-SearchBot",
  "Claude-User",
  "DuckAssistBot",
  "MistralAI-User",
  "YouBot",
];

/*
 * (c) 模型訓練 / dataset 收集爬蟲。
 *
 * Owner 決定 2026-08-16「做盡」：全部 ALLOW —— 我哋想啲模型本身就知道 CardZ Marketcap
 * 係咩、市值點計，唔止喺搜尋嗰刻先臨時讀。呢個係 owner 政策，唔係技術選擇。
 * 要反口好簡單：下面 `allow: ALLOWED_PATHS` 改做 `disallow: "/"` 一行搞掂，
 * 改嗰行就係一個新嘅 owner 決定，順手更新 scripts/test-robots-policy.mjs 嘅斷言。
 */
const AI_TRAINING_AGENTS = [
  "GPTBot",
  "ClaudeBot",
  "anthropic-ai",
  "Google-Extended",
  "CCBot",
  "Amazonbot",
  "Applebot-Extended",
  "meta-externalagent",
  "Bytespider",
  "cohere-ai",
  "PetalBot",
];

export function createRobotsPolicy(environment?: string): MetadataRoute.Robots {
  /* canary / staging 係未出街嘅副本，一律全擋，亦唔出 sitemap（唔好同生產搶索引）。 */
  if (environment === "canary" || environment === "staging") {
    return { rules: [{ userAgent: "*", disallow: "/" }] };
  }

  const openRule = { allow: ALLOWED_PATHS, disallow: DISALLOWED_PATHS };
  return {
    rules: [
      // (a) 搜尋引擎
      { userAgent: SEARCH_ENGINE_AGENTS, ...openRule },
      // (b) AI 搜尋 / 用戶觸發抓取
      { userAgent: AI_SEARCH_AGENTS, ...openRule },
      // (c) AI 訓練爬蟲（owner 2026-08-16：允許）
      { userAgent: AI_TRAINING_AGENTS, ...openRule },
      // (d) 其他所有
      { userAgent: "*", ...openRule },
    ],
    sitemap: `${siteUrl}/sitemap.xml`,
  };
}

export default async function robots(): Promise<MetadataRoute.Robots> {
  return createRobotsPolicy(process.env.CARDZ_ENVIRONMENT);
}
