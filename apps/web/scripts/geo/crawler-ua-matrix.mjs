#!/usr/bin/env node
/*
 * crawler-ua-matrix.mjs —— 逐個 crawler UA 打真實網站，睇佢實際收到咩。
 *
 * 點解要有：robots.txt 係「請求」，Cloudflare 嘅 AI bot 規則先係「執行」。改完 robots.ts
 * 唔代表爬蟲真係入到 —— 邊緣一個 403 / 302 就足以令成件事白做。呢個 script 就係嗰個
 * 收貨閘：任何一個應該 200 嘅 UA 唔係 200，exit 1。
 *
 * 用法：
 *   node apps/web/scripts/geo/crawler-ua-matrix.mjs
 *   node apps/web/scripts/geo/crawler-ua-matrix.mjs --base https://cardzmarketcap.com
 *   node apps/web/scripts/geo/crawler-ua-matrix.mjs --base http://127.0.0.1:3000 --timeout 20000
 *
 * 冇任何依賴（Node 18+ 內建 fetch）。唔跟 redirect：3xx 本身就係要報嘅結果。
 * 讀嘅嘢：status、Location、cf-mitigated、cf-ray、content-type。
 */

const args = process.argv.slice(2);
const flag = (name, fallback) => {
  const index = args.indexOf(`--${name}`);
  return index >= 0 && args[index + 1] ? args[index + 1] : fallback;
};

const BASE = flag("base", "https://cardzmarketcap.com").replace(/\/$/, "");
const TIMEOUT_MS = Number(flag("timeout", "15000"));
const PATHS = ["/", "/llms.txt", "/api/v1/market"];

/*
 * UA 字串盡量貼近真身（token 準過長度）。`expect200` 全部 true：owner 2026-08-16
 * 決定連訓練爬蟲都開放，所以任何一個收唔到 200 都係事故，唔係設計。
 */
const AGENTS = [
  { name: "Googlebot", ua: "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)" },
  { name: "Bingbot", ua: "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)" },
  { name: "Applebot", ua: "Mozilla/5.0 (compatible; Applebot/0.1; +http://www.apple.com/go/applebot)" },
  { name: "OAI-SearchBot", ua: "Mozilla/5.0 (compatible; OAI-SearchBot/1.0; +https://openai.com/searchbot)" },
  { name: "ChatGPT-User", ua: "Mozilla/5.0 (compatible; ChatGPT-User/1.0; +https://openai.com/bot)" },
  { name: "GPTBot", ua: "Mozilla/5.0 (compatible; GPTBot/1.1; +https://openai.com/gptbot)" },
  { name: "PerplexityBot", ua: "Mozilla/5.0 (compatible; PerplexityBot/1.0; +https://perplexity.ai/perplexitybot)" },
  { name: "Perplexity-User", ua: "Mozilla/5.0 (compatible; Perplexity-User/1.0; +https://perplexity.ai/perplexity-user)" },
  { name: "ClaudeBot", ua: "Mozilla/5.0 (compatible; ClaudeBot/1.0; +claudebot@anthropic.com)" },
  { name: "Claude-User", ua: "Mozilla/5.0 (compatible; Claude-User/1.0; +Claude-User@anthropic.com)" },
  { name: "Claude-SearchBot", ua: "Mozilla/5.0 (compatible; Claude-SearchBot/1.0; +Claude-SearchBot@anthropic.com)" },
  { name: "Google-Extended", ua: "Mozilla/5.0 (compatible; Google-Extended/1.0)" },
  { name: "DuckAssistBot", ua: "Mozilla/5.0 (compatible; DuckAssistBot/1.0; +https://duckduckgo.com/duckassistbot)" },
  { name: "meta-externalagent", ua: "meta-externalagent/1.1 (+https://developers.facebook.com/docs/sharing/webmasters/crawler)" },
  { name: "Amazonbot", ua: "Mozilla/5.0 (compatible; Amazonbot/0.1; +https://developer.amazon.com/support/amazonbot)" },
  { name: "CCBot", ua: "CCBot/2.0 (https://commoncrawl.org/faq/)" },
  { name: "Bytespider", ua: "Mozilla/5.0 (compatible; Bytespider; spider-feedback@bytedance.com)" },
].map((agent) => ({ ...agent, expect200: true }));

async function probe(url, ua) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    const response = await fetch(url, {
      redirect: "manual",
      signal: controller.signal,
      headers: { "user-agent": ua, accept: "*/*" },
    });
    return {
      status: response.status,
      location: response.headers.get("location") ?? "",
      mitigated: response.headers.get("cf-mitigated") ?? "",
      ray: response.headers.get("cf-ray") ?? "",
      contentType: (response.headers.get("content-type") ?? "").split(";")[0],
    };
  } catch (error) {
    return { status: 0, location: "", mitigated: "", ray: "", contentType: "", error: String(error?.message ?? error) };
  } finally {
    clearTimeout(timer);
  }
}

const pad = (value, width) => String(value).padEnd(width);

const rows = [];
for (const path of PATHS) {
  const url = `${BASE}${path}`;
  console.log(`\n=== ${url}`);
  console.log(`${pad("UA", 20)}${pad("status", 8)}${pad("cf-mitigated", 14)}${pad("content-type", 20)}location`);
  for (const agent of AGENTS) {
    const result = await probe(url, agent.ua);
    rows.push({ path, agent: agent.name, expect200: agent.expect200, ...result });
    console.log(
      `${pad(agent.name, 20)}${pad(result.status || `ERR`, 8)}${pad(result.mitigated || "-", 14)}${pad(result.contentType || "-", 20)}${result.location || result.error || ""}`,
    );
  }
}

const bad = rows.filter((row) => row.expect200 && row.status !== 200);
console.log("");
if (bad.length) {
  console.log(`FAIL ${bad.length}/${rows.length} probes did not return 200:`);
  for (const row of bad) {
    console.log(`  ${row.agent} ${row.path} → ${row.status || "ERR"} ${row.mitigated ? `(cf-mitigated: ${row.mitigated})` : ""} ${row.location || row.error || ""}`.trimEnd());
  }
  console.log("robots.txt 改咗但呢度仲紅 = Cloudflare 側未放行（Security → Bots → AI Crawl Control）。");
  process.exit(1);
}
console.log(`OK ${rows.length}/${rows.length} probes returned 200 (${BASE})`);
