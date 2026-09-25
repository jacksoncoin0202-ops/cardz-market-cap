import { PUBLIC_SITE_URL } from "@/lib/public-site";
import { loadMarketSnapshot } from "@/lib/server-snapshot";

/*
 * GET /api/v1 —— API 目錄。
 *
 * 點解要有：一個裸 JSON blob 冇引擎會 cite；佢要見到「呢個 endpoint 係咩、欄位係咩、
 * 幾耐更新一次、點署名」先肯用。呢條 route 就係嗰份講明書嘅機讀版，人讀版喺 /data。
 *
 * CORS header 喺呢度再寫一次（next.config.ts 亦有一份）：next.config 嘅 headers 只喺
 * Next 自己 serve 嗰層生效，中間隔住 CDN／standalone adapter 就唔一定跟得到。
 * route 自己出嘅 header 一定跟住個 response 走。
 */
export const revalidate = 300;

const site = PUBLIC_SITE_URL;

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, OPTIONS",
  "Cache-Control": "public, s-maxage=300, stale-while-revalidate=300",
  "X-Robots-Tag": "all",
};

export async function GET(): Promise<Response> {
  const snapshot = await loadMarketSnapshot();
  const cards = [...snapshot.top100, ...snapshot.watchlist];

  return Response.json(
    {
      name: "CardZ Marketcap public API",
      version: "v1",
      description:
        "Daily PSA 10 market capitalisation for graded Pokémon TCG and One Piece Card Game cards. Market cap is the verified PSA 10 population multiplied by a PSA 10 reference price.",
      documentation: `${site}/data`,
      llmsTxt: `${site}/llms.txt`,
      llmsFullTxt: `${site}/llms-full.txt`,
      methodology: `${site}/methodology`,
      dataset: {
        generation: snapshot.generation,
        generatedAt: snapshot.generatedAt,
        effectiveAt: snapshot.effectiveAt,
        cardCount: cards.length,
        top100Count: snapshot.top100.length,
        watchlistCount: snapshot.watchlist.length,
        sealedProductCount: snapshot.sealed?.products.length ?? 0,
      },
      updateCadence: {
        frequency: "daily",
        description:
          "Every reference price, population and market cap is recalculated on each daily build. Quote the effectiveAt date with any figure.",
        cacheControl: "public, s-maxage=300, stale-while-revalidate=300",
      },
      endpoints: [
        {
          method: "GET",
          path: "/api/v1",
          url: `${site}/api/v1`,
          description: "This index: endpoint list, field dictionary, update cadence and attribution requirement.",
        },
        {
          method: "GET",
          path: "/api/v1/search",
          url: `${site}/api/v1/search`,
          description:
            "Look up a card or sealed box by name, nickname or collector number. Matches English, Traditional Chinese, Simplified Chinese, Japanese and Korean. Folded substring, not typo-fuzzy.",
          query: {
            q: { type: "string", required: true, description: "Name, nickname or collector number. Empty returns 400." },
            lang: { values: ["en", "zh-TW", "zh-CN", "ja", "ko"], default: "en", description: "Ranks display names; matching still uses every language." },
            kind: { values: ["card", "box"], description: "Omit to search both." },
            tcg: { values: ["pokemon", "one-piece"], description: "Omit to search both games." },
            limit: { type: "positive integer", default: 8, max: 50 },
          },
          examples: [
            `${site}/api/v1/search?q=${encodeURIComponent("梵高皮卡丘")}&lang=zh-TW`,
            `${site}/api/v1/search?q=moonbreon`,
            `${site}/api/v1/search?q=217/187`,
            `${site}/api/v1/search?q=OP05-119`,
          ],
        },
        {
          method: "GET",
          path: "/api/v1/resolve",
          url: `${site}/api/v1/resolve`,
          description:
            "Same search, but returns one card when the top hit is uniquely strong. Ambiguous names return resolved:null and a short candidate list.",
          query: {
            q: { type: "string", required: true },
            lang: { values: ["en", "zh-TW", "zh-CN", "ja", "ko"], default: "en" },
            kind: { values: ["card", "box"] },
            tcg: { values: ["pokemon", "one-piece"] },
          },
          examples: [
            `${site}/api/v1/resolve?q=217/187`,
            `${site}/api/v1/resolve?q=${encodeURIComponent("月亮伊布")}&lang=zh-TW`,
          ],
        },
        {
          method: "GET",
          path: "/api/v1/market",
          url: `${site}/api/v1/market`,
          description: "Ranked cards with market cap, PSA 10 reference price, verified PSA 10 population and window changes.",
          query: {
            scope: {
              values: ["all", "pokemon", "one-piece", "watchlist"],
              default: "all",
              description: "all and the two game scopes return the top 100 of that scope; watchlist returns rank 101 and beyond.",
            },
            page: { type: "positive integer", default: 1, description: "watchlist scope only; other scopes return one page." },
            pageSize: { type: "positive integer", default: 200, max: 500 },
          },
          examples: [
            `${site}/api/v1/market`,
            `${site}/api/v1/market?scope=pokemon`,
            `${site}/api/v1/market?scope=one-piece`,
            `${site}/api/v1/market?scope=watchlist&page=2`,
          ],
        },
        {
          method: "GET",
          path: "/api/v1/cards/{id}",
          url: `${site}/api/v1/cards/{id}`,
          description: "One card, including its full daily price and tracked-sales history.",
          examples: cards.slice(0, 1).map((card) => `${site}/api/v1/cards/${card.id}`),
        },
      ],
      fields: {
        "generation.id": "Identifier of the dataset build that produced these figures.",
        generatedAt: "ISO timestamp of the build.",
        effectiveAt: "ISO date the figures are effective for. Cite this date with any number.",
        "coverage.claim": "verified-top-100 or verified-top-n for the requested scope.",
        "cards[].id": "Stable public card id; also the /card/{id} URL segment.",
        "cards[].marketRank": "Rank by market cap across the whole index.",
        "cards[].viewRank": "Rank inside the requested scope.",
        "cards[].tcg": "Pokémon or One Piece.",
        "cards[].cardLanguage": "Printing language (en, ja, ko, zhCN, zhTW) — not the UI language.",
        "cards[].officialName": "Canonical PSA/graded full name of the printing.",
        "cards[].setName": "Localised set name keyed by locale; non-English values are null when no translation exists.",
        "cards[].collectorNumber": "Collector number as printed.",
        "cards[].pricePsa10": "{ value: USD | null, status, asOf } — PSA 10 reference price.",
        "cards[].populationPsa10": "{ value: count | null, status, asOf } — verified PSA 10 population.",
        "cards[].marketCap": "{ value: USD | null, status, asOf } — population x reference price.",
        "cards[].windows": "Keyed 1d, 7d, 30d, 90d, 180d, 365d. changePct is the price change; marketCapChangePct is the market-cap change; trackedSales covers completed sales inside tracked coverage.",
        "cards[].historyDaily": "Single-card endpoint only: daily points { at, priceUsd, priceStatus, trackedSalesValueUsd, trackedSalesCount }.",
        "hits[].match": "Why the hit ranked: number-exact, number-prefix, name-exact, name-prefix, official-prefix, or contains.",
        "hits[].url": "Canonical public page for the hit. Cite this URL with the effectiveAt date.",
        citation: "Ready-to-paste attribution line. A figure without its date is not attributable to us.",
        "resolve.ambiguous": "true when more than one printing is a strong match. Do not guess — show hits or ask.",
        "null semantics": "A null value means the figure is not published for that window. It never means zero.",
      },
      license: {
        name: "CC BY 4.0",
        url: "https://creativecommons.org/licenses/by/4.0/",
        attribution: `CardZ Marketcap, ${site}, data as of ${snapshot.effectiveAt.slice(0, 10)}`,
        requirement:
          "Name CardZ Marketcap, link the page or endpoint used, and carry the effectiveAt date of the figure. A figure without its date is not attributable to us.",
      },
      disambiguation:
        "CardZ Marketcap is a data index for physical graded trading cards. It is not a cryptocurrency, not a token and not the CARDS token; there is no CardZ ticker and nothing here is tradable.",
    },
    { headers: CORS_HEADERS },
  );
}

export async function OPTIONS(): Promise<Response> {
  return new Response(null, { status: 204, headers: CORS_HEADERS });
}
