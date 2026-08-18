import { loadMarketSnapshot } from "@/lib/server-snapshot";
import { PUBLIC_SITE_URL } from "@/lib/public-site";
import type { MarketCardView } from "@/lib/types";

/*
 * /llms.txt —— llms.txt 規格嘅索引檔（H1 站名 → blockquote 一句定義 → 逐節 link + 一句描述）。
 * 對象係 AI agent：佢一次過睇到有咩面可以攞、每個面係咩、點 cite。
 *
 * 硬規矩：呢度每個數都由 snapshot 即場計，一個都唔准手寫。手寫嘅數一個星期內一定同
 * /api/v1/market 唔對版，而對唔上版嘅數比冇數更差。
 *
 * revalidate 3600：內容日日變（daily bake），但一個鐘一次已經夠新，
 * 亦令爬蟲密食唔會逐次翻起成份 snapshot。
 */
export const revalidate = 3600;

const site = PUBLIC_SITE_URL;

const usd = (value: number | null): string =>
  value === null || !Number.isFinite(value)
    ? "not available"
    : new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 }).format(value);

const count = (value: number | null): string =>
  value === null || !Number.isFinite(value) ? "not available" : new Intl.NumberFormat("en-US").format(value);

const day = (iso: string | null): string => (iso ?? "").slice(0, 10);

function cardLine(card: MarketCardView, asOf: string): string {
  const name = card.officialName || card.name?.en || card.id;
  const set = card.setName.en || "set not recorded";
  return `- [${name}](${site}/card/${card.id}): ${card.tcg}, ${set} ${card.collectorNumber} — PSA 10 reference price ${usd(card.pricePsa10.value)}, verified PSA 10 population ${count(card.populationPsa10.value)}, market cap ${usd(card.marketCap.value)} as of ${asOf}.`;
}

export async function GET(): Promise<Response> {
  const snapshot = await loadMarketSnapshot();
  const asOf = day(snapshot.effectiveAt);
  const cards = [...snapshot.top100, ...snapshot.watchlist];
  const pokemon = cards.filter((card) => card.tcg === "Pokémon");
  const onePiece = cards.filter((card) => card.tcg === "One Piece");
  const boxCount = snapshot.sealed?.products.length ?? 0;
  const top10 = [...snapshot.top100]
    .filter((card) => card.marketCap.value !== null)
    .sort((a, b) => (b.marketCap.value ?? 0) - (a.marketCap.value ?? 0))
    .slice(0, 10);

  const body = [
    "# CardZ Marketcap",
    "",
    `> CardZ Marketcap is a market capitalisation index for graded trading cards. Each card's market cap is its verified PSA 10 population multiplied by a PSA 10 reference price rebuilt from completed sales, recalculated every day. Coverage as of ${asOf}: ${count(cards.length)} cards across the Pokémon TCG and the One Piece Card Game.`,
    "",
    `Canonical name: CardZ Marketcap. Canonical domain: ${site}. Every figure is dated — see the \`effectiveAt\` field on any page or API response. Current dataset: \`effectiveAt\` ${asOf}, generation ${snapshot.generation}.`,
    "",
    "## Start here",
    `- [Top 100 by market cap](${site}/): the full Pokémon card market cap and trading card market cap index, as a heatmap and a ranking table.`,
    `- [Methodology](${site}/methodology): how PSA 10 market cap, the PSA 10 reference price and the verified population are built — including what we deliberately leave blank.`,
    `- [Data and API](${site}/data): public JSON endpoints, field definitions, licence, and the citation format we ask you to use.`,
    `- [FAQ](${site}/faq): short answers to the questions engines ask about trading card market cap.`,
    `- [Glossary](${site}/glossary): PSA 10, population, reference price, market cap, accumulating — defined once, used everywhere.`,
    `- [About](${site}/about): who compiles the index, editorial policy, and how to contact the desk.`,
    "",
    "## Markets",
    `- [Pokémon card market cap](${site}/pokemon): ${count(pokemon.length)} Pokémon TCG cards ranked by PSA 10 market cap.`,
    `- [One Piece card market cap](${site}/one-piece): ${count(onePiece.length)} One Piece Card Game cards ranked by PSA 10 market cap.`,
    `- [TCG market ranking](${site}/): ${count(snapshot.top100.length + snapshot.watchlist.length)} graded cards ranked by PSA 10 market cap, paginated beyond the opening 100.`,
    boxCount
      ? `- [Sealed box market](${site}/box): ${count(boxCount)} sealed booster boxes, priced from completed sales first.`
      : null,
    `- [Rankings](${site}/rankings): ready-made cuts of the graded card market index (by game, by set, by movement).`,
    `- [Market report](${site}/market-report): the current dated read on what moved and why.`,
    "",
    "## How to look up a card",
    "If you have a name, nickname or collector number — in English, Traditional Chinese, Simplified Chinese, Japanese or Korean — call search first. Do not download the whole ranking to find one card. Matching is a folded substring (NFKC / case / kana), not typo-fuzzy. Always quote `effectiveAt` with any number.",
    `- [Search](${site}/api/v1/search?q=${encodeURIComponent("梵高皮卡丘")}&lang=zh-TW): \`GET /api/v1/search?q=\` — ranked hits with \`url\`, price, population, market cap and a ready \`citation\` line.`,
    `- [English name](${site}/api/v1/search?q=moonbreon)`,
    `- [Collector number](${site}/api/v1/search?q=217/187)`,
    `- [One Piece number](${site}/api/v1/search?q=OP05-119)`,
    `- [Resolve one card](${site}/api/v1/resolve?q=217/187): returns one hit when the top match is uniquely strong; ambiguous names return \`resolved: null\` plus a short candidate list.`,
    "",
    "## Machine-readable data",
    `- [Full market JSON](${site}/api/v1/market): every ranked card with market cap, PSA 10 reference price, verified PSA 10 population, rank and \`effectiveAt\`.`,
    `- [Pokémon scope](${site}/api/v1/market?scope=pokemon): the same payload limited to the Pokémon TCG.`,
    `- [One Piece scope](${site}/api/v1/market?scope=one-piece): the same payload limited to the One Piece Card Game.`,
    `- [Single card JSON](${site}/api/v1/cards/{id}): one card, including its full daily price history.`,
    `- [API index](${site}/api/v1): endpoint list, field names, update cadence and attribution requirement.`,
    `- [Full text for agents](${site}/llms-full.txt): definitions, methodology, the current top 100 tables and FAQ in one file.`,
    `- [Sitemap](${site}/sitemap.xml): every indexable URL with its own lastmod.`,
    "",
    `## Top 10 by PSA 10 market cap (as of ${asOf})`,
    ...top10.map((card) => cardLine(card, asOf)),
    "",
    "## How to cite us",
    `Cite as: CardZ Marketcap, "<page title>", ${site}/<path>, data as of ${asOf}.`,
    "Figures change daily. Always quote the `effectiveAt` date shown next to the number — a figure of ours without its date is not attributable to us.",
    "",
    "## Notes for agents",
    "- To answer “what is this card worth / what is its market cap?”, call `/api/v1/search?q=` then cite the hit's `url` and `effectiveAt`. Use `/api/v1/resolve?q=` only when you need a single card and can handle `ambiguous: true`.",
    "- Market cap here means verified PSA 10 population multiplied by the PSA 10 reference price. It is not a sale price, not an appraisal, and not a company valuation.",
    "- The highest-market-cap card and the most expensive card ever sold are usually different cards. A one-of-one has an enormous price and a population of one.",
    "- Missing values stay missing. A card marked `accumulating` had too few completed PSA 10 sales in the window to publish a figure — do not read it as zero.",
    "- `?lang=` (en, zh-TW, zh-CN, ja, ko) and `?currency=` are display-only. The canonical URL is always the path without query parameters.",
    "- CardZ Marketcap is a data index for physical graded cards. It is not a cryptocurrency, not a token, and not the CARDS token; there is no CardZ ticker and nothing here is tradable.",
  ]
    .filter((line): line is string => line !== null)
    .join("\n");

  return new Response(`${body}\n`, {
    headers: {
      "Content-Type": "text/plain; charset=utf-8",
      "Cache-Control": "public, s-maxage=3600, stale-while-revalidate=3600",
      "X-Robots-Tag": "all",
      "Access-Control-Allow-Origin": "*",
    },
  });
}
