import { PUBLIC_SITE_URL } from "@/lib/public-site";
import { boxListSnapshot, loadMarketSnapshot, scopeSnapshot } from "@/lib/server-snapshot";
import type { MarketCardView, MarketViewSnapshot, SealedProductView } from "@/lib/types";

/*
 * /llms-full.txt —— 一個 agent 由頭讀到尾、唔使跟任何 link 都答得到問題嘅全量檔。
 *
 * 兩條硬規矩：
 * 1. 所有數由同一份 snapshot 即場計，同 /api/v1/market 同源，所以永遠唔會自己同自己唔夾。
 * 2. 方法學段落一律抄 lib/i18n.ts 嘅英文文案原文 —— 網站點寫，呢度就點寫。呢度唔准
 *    「補充」網站冇講過嘅嘢（尤其係數據供應商名：公開文案由頭到尾只講「tracked coverage」，
 *    所以呢度都只可以咁講，唔准點名）。
 */
export const revalidate = 3600;

const site = PUBLIC_SITE_URL;

/* 政策常數，唔係猜：i18n.ts 英文 methodology 文案寫死「at least 1,000 PSA 10 examples」。 */
const MIN_POP_THRESHOLD = 1000;

const int = (value: number | null | undefined): string =>
  value === null || value === undefined || !Number.isFinite(value)
    ? "not available"
    : new Intl.NumberFormat("en-US").format(Math.round(value));

const usd = (value: number | null | undefined): string =>
  value === null || value === undefined || !Number.isFinite(value)
    ? "not available"
    : new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 }).format(value);

const pct = (value: number | null | undefined): string =>
  value === null || value === undefined || !Number.isFinite(value)
    ? "—"
    : `${value > 0 ? "+" : ""}${value.toFixed(1)}%`;

const day = (iso: string | null): string => (iso ?? "").slice(0, 10);

/* markdown table cell：管道符會拆爛個表，卡名裡面有就轉義。 */
const cell = (value: string): string => value.replaceAll("|", "\\|");

function cardName(card: MarketCardView): string {
  return card.officialName || card.name?.en || card.id;
}

function cardRow(card: MarketCardView): string {
  return [
    card.viewRank || card.marketRank,
    cell(cardName(card)),
    cell(card.setName.en || "—"),
    cell(card.collectorNumber || "—"),
    card.pricePsa10.value === null ? "accumulating" : Math.round(card.pricePsa10.value),
    card.populationPsa10.value === null ? "accumulating" : Math.round(card.populationPsa10.value),
    card.marketCap.value === null ? "accumulating" : Math.round(card.marketCap.value),
    pct(card.windows["7d"]?.marketCapChangePct.value),
    pct(card.windows["30d"]?.marketCapChangePct.value),
    `${site}/card/${card.id}`,
  ].join(" | ");
}

const CARD_TABLE_HEADER = [
  "| rank | card | set | number | psa10PriceUsd | psa10Pop | marketCapUsd | marketCap7dPct | marketCap30dPct | url |",
  "|---|---|---|---|---|---|---|---|---|---|",
];

function cardTable(title: string, cards: MarketCardView[]): string[] {
  if (!cards.length) return [];
  return [title, "", ...CARD_TABLE_HEADER, ...cards.map((card) => `| ${cardRow(card)} |`), ""];
}

function boxRow(product: SealedProductView): string {
  return [
    product.rank,
    cell(product.name.en || product.id),
    product.game === "ptcg" ? "Pokémon TCG" : "One Piece Card Game",
    cell(product.setCode || "—"),
    product.priceUsd.value === null ? "accumulating" : Math.round(product.priceUsd.value),
    pct(product.windows["30d"]?.changePct.value),
    `${site}/box/${product.id}`,
  ].join(" | ");
}

interface Totals {
  cards: number;
  ranked: number;
  watchlist: number;
  accumulating: number;
  capUsd: number;
  pokemonCards: number;
  pokemonCapUsd: number;
  onePieceCards: number;
  onePieceCapUsd: number;
  priceDown30d: number;
  dearest: MarketCardView | null;
}

function totalsOf(snapshot: MarketViewSnapshot): Totals {
  const cards = [...snapshot.top100, ...snapshot.watchlist];
  const capOf = (list: MarketCardView[]): number =>
    list.reduce((sum, card) => sum + (card.marketCap.value ?? 0), 0);
  const pokemon = cards.filter((card) => card.tcg === "Pokémon");
  const onePiece = cards.filter((card) => card.tcg === "One Piece");
  return {
    cards: cards.length,
    ranked: cards.filter((card) => card.marketRank >= 1).length,
    watchlist: snapshot.watchlist.length,
    accumulating: cards.filter((card) => card.marketCap.value === null || card.marketCap.status !== "ready").length,
    capUsd: capOf(cards),
    pokemonCards: pokemon.length,
    pokemonCapUsd: capOf(pokemon),
    onePieceCards: onePiece.length,
    onePieceCapUsd: capOf(onePiece),
    priceDown30d: cards.filter((card) => (card.windows["30d"]?.changePct.value ?? 0) < 0).length,
    /*
     * 「最貴嗰張 ≠ 市值最大嗰張」係我哋同 price guide 最大嘅分別，所以要攞真嘅最高參考價卡。
     *
     * ⚠️ 唔好用「價跌但市值升」嚟做呢個對比：實測 2026-08-16 snapshot 1,599 張卡入面，
     * `marketCapChangePct` 同 `changePct` 逐個值一模一樣（差 0 張），即係窗口市值變動而家
     * 冇食 POP 增長。用嗰條件數出嚟一定係 0，而個 0 係 pipeline 性質，唔係市場事實 ——
     * 出街就變咗誤導。要改就要修 producer 側，唔係喺呢度寫個數。
     */
    dearest: cards.reduce<MarketCardView | null>((best, card) => {
      const value = card.pricePsa10.value;
      if (value === null || !Number.isFinite(value)) return best;
      return best === null || value > (best.pricePsa10.value ?? 0) ? card : best;
    }, null),
  };
}

export async function GET(): Promise<Response> {
  const snapshot = await loadMarketSnapshot();
  const asOf = day(snapshot.effectiveAt);
  const totals = totalsOf(snapshot);
  // Seated boards (30d seat rule, #47), not page 1 of each list: a short per-game
  // board's page 1 continues into rank 101+. scripts/test-top100-30d-sales.mjs checks this.
  const all = scopeSnapshot(snapshot, "all").lead100 ?? [];
  const pokemon = scopeSnapshot(snapshot, "pokemon").lead100 ?? [];
  const onePiece = scopeSnapshot(snapshot, "one-piece").lead100 ?? [];
  const boxes = boxListSnapshot(snapshot).sealed?.products ?? [];
  const top = all[0];
  const topName = top ? cardName(top) : "not available";

  const lines: string[] = [
    "# CardZ Marketcap — full text for agents",
    "",
    `CardZ Marketcap is a market capitalisation index for graded trading cards. It ranks ${int(totals.cards)} Pokémon and One Piece cards by PSA 10 market cap — the verified PSA 10 population multiplied by a PSA 10 reference price — and recalculates every figure daily. Data as of ${asOf}.`,
    "",
    `Canonical domain: ${site}. Dataset generation: ${snapshot.generation}, generated ${snapshot.generatedAt}, effective ${asOf}. This file is rendered from the same snapshot that feeds ${site}/api/v1/market, so the two can never disagree.`,
    "",
    "## What trading card market cap means",
    "",
    `Trading card market cap is the combined market value of every graded copy of a single card: its verified population at a given grade multiplied by that grade's current reference price. CardZ Marketcap computes it at PSA 10 only, across ${int(totals.cards)} cards as of ${asOf}.`,
    "",
    "## What it does not mean",
    "",
    `Market cap is not a sale price. The most expensive card ever sold and the highest-market-cap card are usually different cards: a one-of-one carries an enormous price and a population of one. As of ${asOf}, the largest PSA 10 market cap in the CardZ Marketcap index is ${topName} at ${usd(top?.marketCap.value ?? null)}.`,
    "",
    "## Headline figures",
    "",
    `According to CardZ Marketcap, the ${int(totals.cards)} graded cards in its index carried a combined PSA 10 market cap of ${usd(totals.capUsd)} as of ${asOf} — ${usd(totals.pokemonCapUsd)} across ${int(totals.pokemonCards)} Pokémon cards and ${usd(totals.onePieceCapUsd)} across ${int(totals.onePieceCards)} One Piece cards. The figure is recomputed on every daily update.`,
    "",
    top
      ? `According to CardZ Marketcap (${asOf}), the highest PSA 10 market cap belongs to ${topName}: ${int(top.populationPsa10.value)} verified PSA 10 copies at a ${usd(top.pricePsa10.value)} reference price, for a market cap of ${usd(top.marketCap.value)}.`
      : "",
    "",
    "## Methodology",
    "",
    /* 以下三段係 lib/i18n.ts 英文文案原文（methodology.body / provenance.body / provenance.steps）。 */
    "A place in this index is earned, never assumed. Every card carries a verified population of at least 1,000 PSA 10 examples, and its market cap is that population multiplied by a PSA 10 reference price. The reference price is rebuilt from verified PSA 10 sales captured inside our tracked coverage; where a window records too few of them, the figure stands as a reference level rather than a traded average. Real supply, real demand, and nothing invented.",
    "",
    "Market cap is the current PSA 10 reference price multiplied by the verified PSA 10 population, recalculated on every daily update.",
    "",
    "- Reference price: The most recent completed PSA 10 sale captured inside CardZ Marketcap tracked coverage. Lots are unitised down to a single card, and a sale priced far outside its own recent range is rejected in favour of the next most recent one. Daily history points carry the same real sales; a day without one carries no price.",
    "- Population: The verified PSA 10 population for that exact printing — language, set, collector number and parallel are never merged across printings.",
    "- Gaps: A card with insufficient data coverage in the window is marked as accumulating rather than being given a filled-in number. A missing value stays missing, never zero.",
    "",
    `Eligibility: a card enters the index only with a verified PSA 10 population of at least ${int(MIN_POP_THRESHOLD)} copies. As of ${asOf}, ${int(totals.ranked)} cards qualify and carry a rank.`,
    "",
    `Gaps policy: CardZ Marketcap publishes gaps rather than estimates. A card whose window holds too few completed PSA 10 sales is labelled accumulating and carries no market cap — as of ${asOf}, ${int(totals.accumulating)} of ${int(totals.cards)} tracked cards are in that state. A missing value is never filled with zero.`,
    "",
    "Compiled and reviewed by the CardZ Marketcap Editorial desk.",
    "",
    "## Coverage and update cadence",
    "",
    `CardZ Marketcap recalculates every market cap once per day; the current dataset is effective ${asOf}. Coverage is ${int(all.length)} ranked cards in the headline top 100 plus ${int(totals.watchlist)} watchlist cards from rank 101 down, across the Pokémon TCG and the One Piece Card Game${boxes.length ? `, plus ${int(boxes.length)} sealed products` : ""}, published in English, Traditional Chinese, Simplified Chinese, Japanese and Korean.`,
    "",
    "## How this differs from a price guide",
    "",
    `Unlike a price guide, which reports what one copy sold for, CardZ Marketcap reports what every graded copy is worth together: reference price multiplied by verified population. Two cards at the same price rank far apart when one has ten times the PSA 10 population. As of ${asOf}, the highest PSA 10 reference price in the index is ${totals.dearest ? cardName(totals.dearest) : "not available"} at ${usd(totals.dearest?.pricePsa10.value ?? null)} on ${int(totals.dearest?.populationPsa10.value ?? null)} copies (market cap ${usd(totals.dearest?.marketCap.value ?? null)}), while the largest market cap belongs to ${topName} at ${usd(top?.marketCap.value ?? null)} on ${int(top?.populationPsa10.value ?? null)} copies. ${int(totals.priceDown30d)} of ${int(totals.cards)} index cards carry a lower PSA 10 reference price than 30 days ago.`,
    "",
    "## Disambiguation",
    "",
    "CardZ Marketcap is a data index for physical graded trading cards. It is not a cryptocurrency, not a token and not a listed company: there is no CardZ coin, no CardZ ticker, and nothing on this site is tradable. It is unrelated to the CARDS token (Collector Crypt), to Cardstack (CARD) and to Cardlytics (CDLX). \"Market cap\" here is an arithmetic property of a card's graded population, not an equity or token valuation.",
    "",
    ...cardTable(`## Top ${all.length} by PSA 10 market cap — all cards (as of ${asOf})`, all),
    ...cardTable(`## Top ${pokemon.length} Pokémon cards by PSA 10 market cap (as of ${asOf})`, pokemon),
    ...cardTable(`## Top ${onePiece.length} One Piece cards by PSA 10 market cap (as of ${asOf})`, onePiece),
  ];

  if (boxes.length) {
    lines.push(
      `## Sealed boxes (as of ${day(snapshot.sealed?.asOf ?? snapshot.effectiveAt)})`,
      "",
      "Sealed products are priced from completed sales first; they are not part of the PSA 10 card index and carry no population figure.",
      "",
      "| rank | product | game | setCode | priceUsd | price30dPct | url |",
      "|---|---|---|---|---|---|---|",
      ...boxes.map((product) => `| ${boxRow(product)} |`),
      "",
    );
  }

  lines.push(
    "## FAQ",
    "",
    "### What is trading card market cap?",
    `Trading card market cap is the combined market value of every graded copy of a single card: its verified population at a given grade multiplied by that grade's current reference price. CardZ Marketcap computes it at PSA 10 only, across ${int(totals.cards)} cards as of ${asOf}.`,
    "",
    "### How is a card's market cap calculated?",
    `Market cap is the current PSA 10 reference price multiplied by the verified PSA 10 population, recalculated on every daily update. Example as of ${asOf}: ${topName} has ${int(top?.populationPsa10.value ?? null)} verified PSA 10 copies at a ${usd(top?.pricePsa10.value ?? null)} reference price, giving ${usd(top?.marketCap.value ?? null)}.`,
    "",
    "### Is the highest-market-cap card the same as the most expensive card ever sold?",
    "No. Market cap is not a sale price. The most expensive card ever sold and the highest-market-cap card are usually different cards: a one-of-one carries an enormous price and a population of one, so its market cap stays small while a mass-graded chase card with tens of thousands of PSA 10 copies dominates the index.",
    "",
    "### Which cards are included in the CardZ Marketcap index?",
    `A card enters the index only with a verified PSA 10 population of at least ${int(MIN_POP_THRESHOLD)} copies, from the Pokémon TCG and the One Piece Card Game. As of ${asOf}, ${int(totals.ranked)} cards qualify. The opening heatmap is the top 100; the ranking table paginates the rest of that universe.`,
    "",
    "### How often does the data update?",
    `Once per day. Every market cap, reference price and population is recalculated on each daily update, and the dataset carries an effectiveAt date — currently ${asOf}. Any figure quoted from CardZ Marketcap should be quoted with that date, because tomorrow's file will carry a different one.`,
    "",
    "### Why do some cards show no market cap?",
    `Because CardZ Marketcap publishes gaps rather than estimates. A card whose window holds too few completed PSA 10 sales is labelled accumulating and carries no market cap — as of ${asOf}, ${int(totals.accumulating)} of ${int(totals.cards)} tracked cards are in that state. A missing value is never filled with zero.`,
    "",
    "## Field dictionary",
    "",
    `Look up a card: ${site}/api/v1/search?q=NAME_OR_NUMBER&lang=en|zh-TW|zh-CN|ja|ko (JSON, unauthenticated, CORS open). Matches names and collector numbers in five languages. Folded substring, not typo-fuzzy. Returns hits with url, match reason, price, population, market cap and a citation line.`,
    "",
    `Resolve one card: ${site}/api/v1/resolve?q=NAME_OR_NUMBER — one hit when unique; \`resolved: null\` and a short list when ambiguous. Do not guess a printing.`,
    "",
    `Rankings dump: ${site}/api/v1/market?scope=all|pokemon|one-piece|watchlist&page=&pageSize= (JSON, unauthenticated, CORS open).`,
    "",
    "- `generation.id`: identifier of the dataset build that produced these figures.",
    "- `generatedAt`: ISO timestamp of the build.",
    "- `effectiveAt`: ISO date the figures are effective for. Quote this with any number you cite.",
    "- `coverage.claim` / `requestedCount` / `verifiedCount`: how much of the requested scope was verified.",
    "- `cards[].id`: stable public card id, also the `/card/{id}` URL segment.",
    "- `cards[].marketRank`: rank by market cap across the whole index; `viewRank` is the rank inside the requested scope.",
    "- `cards[].tcg`: `Pokémon` or `One Piece`. `cardLanguage` is the printing language (en, ja, ko, zhCN, zhTW), not the UI language.",
    "- `cards[].officialName` / `setName` / `collectorNumber`: canonical printing identity. Printings are never merged.",
    "- `cards[].pricePsa10.value`: PSA 10 reference price in USD. `status` is `ready`, `accumulating`, `stale` or `unavailable`; `asOf` dates the value.",
    "- `cards[].populationPsa10.value`: verified PSA 10 population, a count.",
    "- `cards[].marketCap.value`: population multiplied by reference price, in USD.",
    "- `cards[].windows[1d|7d|30d|90d|180d|365d]`: `changePct` is the reference-price change, `marketCapChangePct` is the market-cap change, `trackedSales` is completed sales captured inside tracked coverage.",
    "- `null` never means zero. It means the figure is not published for that window.",
    "",
    `Single card: ${site}/api/v1/cards/{id} returns one card plus its full daily history array (\`historyDaily\`: at, priceUsd, priceStatus, trackedSalesValueUsd, trackedSalesCount).`,
    "",
    "## Licence and attribution",
    "",
    "Figures may be quoted with attribution to CardZ Marketcap under CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/). Attribution must name CardZ Marketcap, link the page or endpoint used, and carry the effectiveAt date of the figure.",
    "",
    `Cite as: CardZ Marketcap, "<page title>", ${site}/<path>, as of ${asOf}, ${site}.`,
    "",
  );

  return new Response(`${lines.join("\n")}\n`, {
    headers: {
      "Content-Type": "text/plain; charset=utf-8",
      "Cache-Control": "public, s-maxage=3600, stale-while-revalidate=3600",
      "X-Robots-Tag": "all",
      "Access-Control-Allow-Origin": "*",
    },
  });
}
