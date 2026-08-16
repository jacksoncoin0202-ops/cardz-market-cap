import { canonicalPublicUrl, datasetId, organizationId, siteOrganization } from "@/components/structured-data";
import { displayCardName } from "./card-name";
import {
  formatInteger,
  formatMetricInteger,
  formatMetricMoney,
  formatMoney,
  formatObservationDate,
  formatPercent,
  metricTone,
} from "./format";
import { fill, hubCopy, monthYearLabel, withLang, type HubCopy } from "./hub-copy";
import { copy } from "./i18n";
import { slugify } from "./related-cards";
import type { Currency, Locale, MarketCardView, MarketMetric, MarketViewSnapshot, MarketWindow } from "./types";

/*
 * SEO hub 嘅共用 server library（owner 2026-08-16）：一個檔管晒「邊啲 hub 路徑存在」、
 * 「每條路徑攞邊批卡」同「頁面 view model（含 JSON-LD）」。
 *
 * 點解三樣擺埋一齊：sitemap.ts 要枚舉路徑、四版頁要攞同一批卡、JSON-LD 要同畫面上
 * 嘅字一模一樣。分開三個檔 = 同一條「邊個 set 有 hub」規則三份 copy，改一份就有頁
 * 200 而 sitemap 冇（repo AGENTS 規矩 13：同一個概念唔准多份實現）。
 *
 * 頁面本身只負責砌 JSX，唔准喺 page.tsx 度再排序／再計數。
 */

export type HubGame = "pokemon" | "one-piece";

/* normaliseSnapshot 出嘅 `tcg` 係顯示字串，唔係 route slug。 */
const GAME_TCG: Record<HubGame, string> = { pokemon: "Pokémon", "one-piece": "One Piece" };

/*
 * Set slug：直接借 related-cards.ts 嘅 `slugify`，唔喺度再寫多一份（AGENTS 規矩 13）。
 * 卡頁麵包屑用嗰條 slug 指入呢度嘅 hub —— 兩邊各寫一個 regex，差一個字就係成批
 * 卡頁指去 404。所以全 app 只留一個實現，呢度淨係包一層 string 簽名（sitemap 契約）。
 */
export function setSlug(setNameEn: string): string {
  return slugify(setNameEn);
}

export interface SeoRoute {
  path: string;
  lastModified?: string;
  priority?: number;
}

/* ── 卡片宇宙 ───────────────────────────────────────────────
 * 用 top100 + watchlist 全量，唔用 scopeSnapshot('pokemon')（佢淨係頭 100 名）：
 * set hub 要覆蓋每一張會 link 過嚟嘅卡，唔係就會有 rank 101+ 嘅卡頁指住 404。
 * 同一個 id 出現兩次（live-db 模式有機會）就以第一次為準。
 */
function allCards(snapshot: MarketViewSnapshot): MarketCardView[] {
  const seen = new Map<string, MarketCardView>();
  for (const card of [...snapshot.top100, ...snapshot.watchlist]) {
    if (!seen.has(card.id)) seen.set(card.id, card);
  }
  return [...seen.values()];
}

export function hubGameCards(snapshot: MarketViewSnapshot, game: HubGame): MarketCardView[] {
  return allCards(snapshot).filter((card) => card.tcg === GAME_TCG[game]);
}

/* 「有數可以出街」= 有值而且狀態係 ready / stale。accumulating / unavailable 一律唔入榜。 */
function usable(metric: MarketMetric<number>): boolean {
  return metric.value !== null
    && Number.isFinite(metric.value)
    && (metric.status === "ready" || metric.status === "stale");
}

function metricValue(metric: MarketMetric<number>): number {
  return usable(metric) ? (metric.value as number) : Number.NaN;
}

function sumCap(cards: MarketCardView[]): number {
  return cards.reduce((total, card) => (usable(card.marketCap) ? total + (card.marketCap.value as number) : total), 0);
}

function byCapDesc(a: MarketCardView, b: MarketCardView): number {
  const capA = usable(a.marketCap) ? (a.marketCap.value as number) : -1;
  const capB = usable(b.marketCap) ? (b.marketCap.value as number) : -1;
  return capB - capA || a.id.localeCompare(b.id);
}

/* ── Set 分組 ─────────────────────────────────────────────── */

export interface HubSetGroup {
  slug: string;
  game: HubGame;
  nameEn: string;
  cards: MarketCardView[];
  combinedCapUsd: number;
  topCard: MarketCardView | null;
}

/* slug 做 key（唔係 setName.en）：兩個只差大小寫／標點嘅 set 名會 slug 到同一條路徑，
   要喺呢度就合埋，唔係 sitemap 會出兩條指住同一版嘅 URL。 */
export function hubSetGroups(snapshot: MarketViewSnapshot, game: HubGame): HubSetGroup[] {
  const groups = new Map<string, HubSetGroup>();
  for (const card of hubGameCards(snapshot, game)) {
    const nameEn = (card.setName.en || "").trim();
    const slug = setSlug(nameEn);
    if (!slug) continue;
    const group = groups.get(slug) ?? { slug, game, nameEn, cards: [], combinedCapUsd: 0, topCard: null };
    group.cards.push(card);
    groups.set(slug, group);
  }
  for (const group of groups.values()) {
    group.cards.sort(byCapDesc);
    group.combinedCapUsd = sumCap(group.cards);
    group.topCard = group.cards.find((card) => usable(card.marketCap)) ?? group.cards[0] ?? null;
  }
  return [...groups.values()].sort((a, b) => b.combinedCapUsd - a.combinedCapUsd || a.slug.localeCompare(b.slug));
}

function setDisplayName(group: HubSetGroup, locale: Locale): string {
  const source = group.topCard ?? group.cards[0];
  return source?.setName[locale] || source?.setName.en || group.nameEn;
}

/* ── Rankings ─────────────────────────────────────────────── */

export const rankingSlugs = [
  "most-valuable-pokemon-cards",
  "most-expensive-pokemon-cards",
  "most-valuable-one-piece-cards",
  "most-expensive-one-piece-cards",
  "biggest-gainers-7d",
  "biggest-losers-7d",
  "biggest-gainers-30d",
  "biggest-losers-30d",
  "highest-psa-10-population",
  "rarest-psa-10-population",
  "pokemon-sets-by-market-cap",
  "one-piece-sets-by-market-cap",
] as const;

export type RankingSlug = (typeof rankingSlugs)[number];
export type RankingKind = "value" | "price" | "gainers" | "losers" | "population" | "rarity" | "sets";

export interface RankingSpec {
  slug: RankingSlug;
  kind: RankingKind;
  scope: HubGame | "all";
  window?: Extract<MarketWindow, "7d" | "30d">;
}

const RANKING_SPECS: Record<RankingSlug, RankingSpec> = {
  "most-valuable-pokemon-cards": { slug: "most-valuable-pokemon-cards", kind: "value", scope: "pokemon" },
  "most-expensive-pokemon-cards": { slug: "most-expensive-pokemon-cards", kind: "price", scope: "pokemon" },
  "most-valuable-one-piece-cards": { slug: "most-valuable-one-piece-cards", kind: "value", scope: "one-piece" },
  "most-expensive-one-piece-cards": { slug: "most-expensive-one-piece-cards", kind: "price", scope: "one-piece" },
  "biggest-gainers-7d": { slug: "biggest-gainers-7d", kind: "gainers", scope: "all", window: "7d" },
  "biggest-losers-7d": { slug: "biggest-losers-7d", kind: "losers", scope: "all", window: "7d" },
  "biggest-gainers-30d": { slug: "biggest-gainers-30d", kind: "gainers", scope: "all", window: "30d" },
  "biggest-losers-30d": { slug: "biggest-losers-30d", kind: "losers", scope: "all", window: "30d" },
  "highest-psa-10-population": { slug: "highest-psa-10-population", kind: "population", scope: "all" },
  "rarest-psa-10-population": { slug: "rarest-psa-10-population", kind: "rarity", scope: "all" },
  "pokemon-sets-by-market-cap": { slug: "pokemon-sets-by-market-cap", kind: "sets", scope: "pokemon" },
  "one-piece-sets-by-market-cap": { slug: "one-piece-sets-by-market-cap", kind: "sets", scope: "one-piece" },
};

export function rankingSpec(slug: string): RankingSpec | null {
  return (RANKING_SPECS as Record<string, RankingSpec | undefined>)[slug] ?? null;
}

export const RANKING_LIMIT = 50;

function scopeCards(snapshot: MarketViewSnapshot, scope: HubGame | "all"): MarketCardView[] {
  return scope === "all" ? allCards(snapshot) : hubGameCards(snapshot, scope);
}

/* 每個榜嘅揀卡＋排序規則。冇數嘅卡一律唔入榜，唔准用 0 頂上。 */
export function rankingCards(
  snapshot: MarketViewSnapshot,
  spec: RankingSpec,
  limit = RANKING_LIMIT,
): MarketCardView[] {
  const cards = scopeCards(snapshot, spec.scope);
  const window = spec.window ?? "7d";
  switch (spec.kind) {
    case "value":
      return cards.filter((card) => usable(card.marketCap)).sort(byCapDesc).slice(0, limit);
    case "price":
      return cards
        .filter((card) => usable(card.pricePsa10))
        .sort((a, b) => metricValue(b.pricePsa10) - metricValue(a.pricePsa10) || a.id.localeCompare(b.id))
        .slice(0, limit);
    case "gainers":
      return cards
        .filter((card) => usable(card.windows[window].changePct) && metricValue(card.windows[window].changePct) > 0)
        .sort((a, b) => metricValue(b.windows[window].changePct) - metricValue(a.windows[window].changePct))
        .slice(0, limit);
    case "losers":
      return cards
        .filter((card) => usable(card.windows[window].changePct) && metricValue(card.windows[window].changePct) < 0)
        .sort((a, b) => metricValue(a.windows[window].changePct) - metricValue(b.windows[window].changePct))
        .slice(0, limit);
    case "population":
      return cards
        .filter((card) => usable(card.populationPsa10))
        .sort((a, b) => metricValue(b.populationPsa10) - metricValue(a.populationPsa10) || a.id.localeCompare(b.id))
        .slice(0, limit);
    case "rarity":
      /* 「最稀有」= POP 最細，但要有價先叫得市場數據；冇價嗰啲淨係代表未收到成交。 */
      return cards
        .filter((card) => usable(card.populationPsa10) && metricValue(card.populationPsa10) > 0 && usable(card.pricePsa10) && metricValue(card.pricePsa10) > 0)
        .sort((a, b) => metricValue(a.populationPsa10) - metricValue(b.populationPsa10) || a.id.localeCompare(b.id))
        .slice(0, limit);
    default:
      return [];
  }
}

/* ── View model（頁面照住畫，唔再自己計） ────────────────── */

export interface HubCrumb {
  label: string;
  href?: string;
}

export interface HubLink {
  label: string;
  href: string;
  note?: string;
}

export interface HubLinkGroup {
  heading: string;
  links: HubLink[];
}

export interface SeoTableColumn {
  key: string;
  label: string;
  numeric?: boolean;
}

export type SeoTone = "positive" | "negative" | "neutral";

export interface SeoTableCell {
  text: string;
  href?: string;
  tone?: SeoTone;
  sub?: string;
}

export interface SeoTableRow {
  key: string;
  cells: SeoTableCell[];
}

export interface HubTableBlock {
  id: string;
  heading?: string;
  intro?: string;
  note?: string;
  caption: string;
  columns: SeoTableColumn[];
  rows: SeoTableRow[];
}

/*
 * FE05 WS3：hub 頁係 server component，傳唔到 format function 落 <CapTicker>，
 * 所以 count-up 要嘅嘢用**可序列化**嘅描述帶落去（數 + 種類 + locale + rates），
 * client 側再砌返同一條 format。`value` 依然係權威文字：冇 `tick` 就照出佢，
 * 有 `tick` 都要同 `value` 逐個字一樣（見 statTick），否則 hydrate 會對唔到數。
 */
export interface HubStatTick {
  value: number;
  kind: "money" | "integer";
  locale: Locale;
  rates: Record<Currency, number>;
}

export interface HubStat {
  label: string;
  value: string;
  tick?: HubStatTick;
}

export interface HubView {
  path: string;
  title: string;
  description: string;
  kicker: string;
  h1: string;
  answer: string;
  breadcrumbLabel: string;
  notes: string[];
  methodNote: string;
  methodLabel: string;
  methodHref: string;
  crumbs: HubCrumb[];
  stats: HubStat[];
  tables: HubTableBlock[];
  linkGroups: HubLinkGroup[];
  emptyNote?: string;
  jsonLd: object;
}

function isoDate(snapshot: MarketViewSnapshot): string {
  return (snapshot.effectiveAt || "").slice(0, 10);
}

function money(value: number, snapshot: MarketViewSnapshot, locale: Locale): string {
  return formatMoney(value, "USD", snapshot.rates, locale, true);
}

/* count-up 描述（FE05 WS3）。`money()` 永遠係 USD compact，所以 client 側個
   formatter 都要係 USD compact —— 兩邊一個 flag 唔同就即刻 hydration mismatch。 */
function statTick(value: number, kind: HubStatTick["kind"], snapshot: MarketViewSnapshot, locale: Locale): HubStatTick {
  return { value, kind, locale, rates: snapshot.rates };
}

function metricMoney(metric: MarketMetric<number>, snapshot: MarketViewSnapshot, locale: Locale): string {
  return formatMetricMoney(metric, "USD", snapshot.rates, locale, true);
}

function cardName(card: MarketCardView, locale: Locale): string {
  return displayCardName(card, locale, copy[locale].status.unavailable);
}

function cardHref(card: MarketCardView, locale: Locale): string {
  return withLang(`/card/${card.id}`, locale);
}

function setHref(game: HubGame, slug: string, locale: Locale): string {
  return withLang(`/${game}/set/${slug}`, locale);
}

function changeCell(card: MarketCardView, window: MarketWindow, locale: Locale): SeoTableCell {
  const metric = card.windows[window].changePct;
  return { text: formatPercent(metric, locale), tone: metricTone(metric) };
}

function cardColumns(t: HubCopy, options: { set?: boolean; number?: boolean }): SeoTableColumn[] {
  return [
    { key: "rank", label: t.table.rank },
    { key: "card", label: t.table.card },
    ...(options.set ? [{ key: "set", label: t.table.set }] : []),
    ...(options.number ? [{ key: "number", label: t.table.number }] : []),
    { key: "price", label: t.table.price, numeric: true },
    { key: "pop", label: t.table.population, numeric: true },
    { key: "cap", label: t.table.marketCap, numeric: true },
    { key: "d7", label: t.table.change7d, numeric: true },
    { key: "d30", label: t.table.change30d, numeric: true },
  ];
}

/* 混合榜（gainers / losers / population）一行一行卡可以係唔同遊戲，所以 set link 嘅
   遊戲要逐張卡由 `tcg` 推，唔准跟成個榜嘅 scope —— 跟 scope 會令 One Piece 卡指去
   /pokemon/set/... 出 404。 */
function cardGame(card: MarketCardView): HubGame | null {
  if (card.tcg === GAME_TCG.pokemon) return "pokemon";
  if (card.tcg === GAME_TCG["one-piece"]) return "one-piece";
  return null;
}

function cardRows(
  cards: MarketCardView[],
  snapshot: MarketViewSnapshot,
  locale: Locale,
  options: { set?: boolean; number?: boolean },
): SeoTableRow[] {
  return cards.map((card, index) => {
    const cells: SeoTableCell[] = [
      { text: String(index + 1) },
      { text: cardName(card, locale), href: cardHref(card, locale) },
    ];
    if (options.set) {
      const game = cardGame(card);
      const slug = setSlug(card.setName.en || "");
      cells.push({
        text: card.setName[locale] || card.setName.en || copy[locale].status.unavailable,
        href: game && slug ? setHref(game, slug, locale) : undefined,
      });
    }
    if (options.number) cells.push({ text: card.collectorNumber || "—" });
    cells.push(
      { text: metricMoney(card.pricePsa10, snapshot, locale) },
      { text: formatMetricInteger(card.populationPsa10, locale) },
      { text: metricMoney(card.marketCap, snapshot, locale) },
      changeCell(card, "7d", locale),
      changeCell(card, "30d", locale),
    );
    return { key: card.id, cells };
  });
}

function setRows(
  groups: HubSetGroup[],
  snapshot: MarketViewSnapshot,
  locale: Locale,
  totalCap: number,
): SeoTableRow[] {
  return groups.map((group, index) => {
    const share = totalCap > 0 ? `${((group.combinedCapUsd / totalCap) * 100).toFixed(1)}%` : "—";
    return {
      key: group.slug,
      cells: [
        { text: String(index + 1) },
        { text: setDisplayName(group, locale), href: setHref(group.game, group.slug, locale) },
        { text: formatInteger(group.cards.length, locale) },
        { text: money(group.combinedCapUsd, snapshot, locale) },
        { text: share },
        {
          text: group.topCard ? cardName(group.topCard, locale) : copy[locale].status.unavailable,
          href: group.topCard ? cardHref(group.topCard, locale) : undefined,
        },
      ],
    };
  });
}

function setColumns(locale: Locale): SeoTableColumn[] {
  const t = hubCopy[locale];
  return [
    { key: "rank", label: t.table.rank },
    { key: "set", label: t.table.set },
    { key: "cards", label: t.table.cards, numeric: true },
    { key: "cap", label: t.table.combinedCap, numeric: true },
    { key: "share", label: t.table.share, numeric: true },
    { key: "top", label: t.table.topCard },
  ];
}

/* ── JSON-LD ───────────────────────────────────────────────
 * 全部節點用 @id 互相指，唔重複描述同一個 entity。Organization / Dataset 兩個
 * @id 全站共用，其他 owner 出同一個 @id 就係同一個節點（JSON-LD 會 merge）。
 */
function siteRootUrl(): string {
  return canonicalPublicUrl("/");
}

/* @id 由 components/structured-data.ts 一個地方出（verify pass 2026-08-16）：
   呢度本來自己寫 `#psa10-index`，同卡頁／首頁嗰個 Dataset 變咗兩個 entity。 */

function organizationNode(): object {
  return { ...siteOrganization(), "@id": organizationId() };
}

/*
 * name/description 一律行 copy[locale].seo.dataset：同一個 @id 全站得一個名。
 * 之前呢度寫死英文，而首頁同 /data 各寫另一個名，三版互相矛盾。—— verify pass 2026-08-16
 */
function datasetNode(snapshot: MarketViewSnapshot, cardCount: number, locale: Locale): object {
  return {
    "@type": "Dataset",
    "@id": datasetId(),
    name: copy[locale].seo.dataset.name,
    description: copy[locale].seo.dataset.description,
    url: siteRootUrl(),
    creator: { "@id": organizationId() },
    publisher: { "@id": organizationId() },
    dateModified: snapshot.effectiveAt,
    isAccessibleForFree: true,
    license: "https://creativecommons.org/licenses/by/4.0/",
    measurementTechnique: "PSA 10 reference price multiplied by verified PSA 10 population",
    variableMeasured: ["PSA 10 reference price", "Verified PSA 10 population", "PSA 10 market cap"],
    size: `${cardCount} cards`,
    distribution: [
      {
        "@type": "DataDownload",
        encodingFormat: "application/json",
        contentUrl: canonicalPublicUrl("/api/v1/market"),
      },
    ],
  };
}

function breadcrumbNode(crumbs: HubCrumb[]): object {
  return {
    "@type": "BreadcrumbList",
    itemListElement: crumbs.map((crumb, index) => ({
      "@type": "ListItem",
      position: index + 1,
      name: crumb.label,
      ...(crumb.href ? { item: canonicalPublicUrl(crumb.href) } : {}),
    })),
  };
}

interface ListEntry {
  name: string;
  url: string;
}

function itemListNode(id: string, name: string, entries: ListEntry[]): object {
  return {
    "@type": "ItemList",
    "@id": id,
    name,
    itemListOrder: "https://schema.org/ItemListOrderDescending",
    numberOfItems: entries.length,
    itemListElement: entries.map((entry, index) => ({
      "@type": "ListItem",
      position: index + 1,
      name: entry.name,
      url: entry.url,
    })),
  };
}

function collectionPageNode(path: string, name: string, description: string, locale: Locale, snapshot: MarketViewSnapshot): object {
  const url = canonicalPublicUrl(path);
  return {
    "@type": "CollectionPage",
    "@id": `${url}#page`,
    url,
    name,
    description,
    inLanguage: locale,
    dateModified: snapshot.effectiveAt,
    isBasedOn: { "@id": datasetId() },
    publisher: { "@id": organizationId() },
    mainEntity: { "@id": `${url}#list` },
  };
}

/*
 * <title> / meta description 長度閘（owner 2026-08-16）。
 * 實測（seed-snapshot 2026-08-16）：209 個 set 名有 79 個長到令標題爆 60 字，
 * 最長 74 字（PriceCharting 原字，例如「Pokemon Japanese Clk-Trading Card Game
 * Classic Blastoise & Suicune EX Deck」）。SERP 一律截走尾段，連 head term
 * 「market cap」都會冇埋，所以 meta 側斬字；H1／麵包屑／可引用答案保留全名。
 */
function clampMeta(value: string, budget: number): string {
  if (budget <= 1 || value.length <= budget) return value;
  const cut = value.slice(0, budget - 1);
  const space = cut.lastIndexOf(" ");
  const trimmed = space > budget * 0.5 ? cut.slice(0, space) : cut;
  return `${trimmed.trimEnd()}…`;
}

function cardEntries(cards: MarketCardView[], locale: Locale): ListEntry[] {
  return cards.map((card) => ({ name: cardName(card, locale), url: canonicalPublicUrl(`/card/${card.id}`) }));
}

/* ── Set hub view ─────────────────────────────────────────── */

export function setHubView(
  snapshot: MarketViewSnapshot,
  game: HubGame,
  slug: string,
  locale: Locale,
): HubView | null {
  const groups = hubSetGroups(snapshot, game);
  const group = groups.find((candidate) => candidate.slug === slug);
  if (!group || group.cards.length < 1) return null;

  const t = hubCopy[locale];
  const nav = copy[locale].nav;
  const date = isoDate(snapshot);
  const setName = setDisplayName(group, locale);
  const gameLabel = game === "pokemon" ? nav.pokemon : nav.onePiece;
  const path = `/${game}/set/${group.slug}`;
  const values = {
    set: setName,
    count: formatInteger(group.cards.length, locale),
    total: money(group.combinedCapUsd, snapshot, locale),
    date,
    top: group.topCard ? cardName(group.topCard, locale) : copy[locale].status.unavailable,
    topValue: group.topCard ? metricMoney(group.topCard.marketCap, snapshot, locale) : copy[locale].status.unavailable,
    game: gameLabel,
  };

  const crumbs: HubCrumb[] = [
    { label: t.common.home, href: "/" },
    { label: gameLabel, href: `/${game}` },
    { label: setName },
  ];
  const siblings = groups.filter((candidate) => candidate.slug !== group.slug).slice(0, 10);
  const url = canonicalPublicUrl(path);

  /* meta 側嘅 set 名預算 = 上限減走 template 本身佔嘅字（逐個 locale 自己計）。 */
  const titleSet = clampMeta(setName, 60 - fill(t.set.title, { ...values, set: "" }).length);
  const descriptionSet = clampMeta(setName, 155 - fill(t.set.description, { ...values, set: "" }).length);

  return {
    path,
    title: fill(t.set.title, { ...values, set: titleSet }),
    description: fill(t.set.description, { ...values, set: descriptionSet }),
    kicker: t.set.kicker,
    h1: fill(t.set.h1, values),
    answer: fill(t.set.summary, values),
    breadcrumbLabel: t.common.breadcrumb,
    notes: [fill(t.common.updated, { date: formatObservationDate(snapshot.effectiveAt, locale) }), t.common.disambiguation],
    methodNote: t.common.method,
    methodLabel: t.common.methodLink,
    methodHref: withLang("/methodology", locale),
    crumbs,
    stats: [],
    tables: [
      {
        id: "cards",
        caption: fill(t.set.tableCaption, values),
        note: t.common.changeNote,
        columns: cardColumns(t, { number: true }),
        rows: cardRows(group.cards, snapshot, locale, { number: true }),
      },
    ],
    linkGroups: [
      {
        heading: t.set.siblings,
        links: [
          ...siblings.map((candidate) => ({
            label: setDisplayName(candidate, locale),
            href: setHref(game, candidate.slug, locale),
          })),
          {
            label: t.set.setsIndex,
            href: withLang(`/rankings/${game === "pokemon" ? "pokemon-sets-by-market-cap" : "one-piece-sets-by-market-cap"}`, locale),
          },
          { label: fill(t.set.back, values), href: withLang(`/${game}`, locale) },
        ],
      },
    ],
    jsonLd: {
      "@context": "https://schema.org",
      "@graph": [
        collectionPageNode(path, fill(t.set.h1, values), fill(t.set.summary, values), locale, snapshot),
        itemListNode(`${url}#list`, fill(t.set.tableCaption, values), cardEntries(group.cards, locale)),
        breadcrumbNode(crumbs),
        datasetNode(snapshot, allCards(snapshot).length, locale),
        organizationNode(),
      ],
    },
  };
}

/* ── Rankings index view ──────────────────────────────────── */

export function rankingsIndexView(snapshot: MarketViewSnapshot, locale: Locale): HubView {
  const t = hubCopy[locale];
  const date = isoDate(snapshot);
  const year = date.slice(0, 4);
  const cards = allCards(snapshot);
  const path = "/rankings";
  const url = canonicalPublicUrl(path);
  const crumbs: HubCrumb[] = [
    { label: t.common.home, href: "/" },
    { label: t.rankings.kicker, href: "/rankings" },
  ];
  const links = rankingSlugs.map((slug) => ({
    label: rankingHeading(RANKING_SPECS[slug], snapshot, locale, "h1"),
    href: withLang(`/rankings/${slug}`, locale),
  }));

  return {
    path,
    title: fill(t.rankings.indexTitle, { year }),
    description: fill(t.rankings.indexDescription, { date }),
    kicker: t.rankings.kicker,
    h1: fill(t.rankings.indexH1, { year }),
    answer: fill(t.rankings.indexSummary, {
      lists: formatInteger(rankingSlugs.length, locale),
      count: formatInteger(cards.length, locale),
      date,
    }),
    breadcrumbLabel: t.common.breadcrumb,
    notes: [fill(t.common.updated, { date: formatObservationDate(snapshot.effectiveAt, locale) }), t.common.disambiguation],
    methodNote: t.common.method,
    methodLabel: t.common.methodLink,
    methodHref: withLang("/methodology", locale),
    crumbs,
    stats: [],
    tables: [],
    linkGroups: [{ heading: t.rankings.listHeading, links }],
    jsonLd: {
      "@context": "https://schema.org",
      "@graph": [
        collectionPageNode(path, fill(t.rankings.indexH1, { year }), fill(t.rankings.indexSummary, {
          lists: formatInteger(rankingSlugs.length, locale),
          count: formatInteger(cards.length, locale),
          date,
        }), locale, snapshot),
        itemListNode(
          `${url}#list`,
          fill(t.rankings.indexH1, { year }),
          rankingSlugs.map((slug) => ({
            name: rankingHeading(RANKING_SPECS[slug], snapshot, locale, "h1"),
            url: canonicalPublicUrl(`/rankings/${slug}`),
          })),
        ),
        breadcrumbNode(crumbs),
        datasetNode(snapshot, cards.length, locale),
        organizationNode(),
      ],
    },
  };
}

/* ── Ranking detail view ──────────────────────────────────── */

function rankingValues(spec: RankingSpec, snapshot: MarketViewSnapshot, locale: Locale) {
  const t = hubCopy[locale];
  const date = isoDate(snapshot);
  return {
    game: spec.scope === "all" ? t.scopeAll : t.games[spec.scope],
    scope: spec.scope === "all" ? t.scopeAll : t.games[spec.scope],
    window: t.windows[spec.window ?? "7d"],
    year: date.slice(0, 4),
    date,
  };
}

function rankingHeading(
  spec: RankingSpec,
  snapshot: MarketViewSnapshot,
  locale: Locale,
  field: "h1" | "titles" | "descriptions",
): string {
  const t = hubCopy[locale];
  return fill(t.rankings[field][spec.kind], rankingValues(spec, snapshot, locale));
}

export function rankingView(snapshot: MarketViewSnapshot, slug: string, locale: Locale): HubView | null {
  const spec = rankingSpec(slug);
  if (!spec) return null;

  const t = hubCopy[locale];
  const base = rankingValues(spec, snapshot, locale);
  const path = `/rankings/${spec.slug}`;
  const url = canonicalPublicUrl(path);
  const crumbs: HubCrumb[] = [
    { label: t.common.home, href: "/" },
    { label: t.rankings.kicker, href: "/rankings" },
    { label: rankingHeading(spec, snapshot, locale, "h1") },
  ];

  const isSets = spec.kind === "sets";
  const groups = isSets && spec.scope !== "all" ? hubSetGroups(snapshot, spec.scope) : [];
  const cards = isSets ? [] : rankingCards(snapshot, spec);
  const gameTotalCap = isSets ? groups.reduce((total, group) => total + group.combinedCapUsd, 0) : 0;

  const topGroup = groups[0] ?? null;
  const topCard = cards[0] ?? null;
  const topValue = (() => {
    if (isSets) return topGroup ? money(topGroup.combinedCapUsd, snapshot, locale) : copy[locale].status.unavailable;
    if (!topCard) return copy[locale].status.unavailable;
    switch (spec.kind) {
      case "price":
        return metricMoney(topCard.pricePsa10, snapshot, locale);
      case "gainers":
      case "losers":
        return formatPercent(topCard.windows[spec.window ?? "7d"].changePct, locale);
      case "population":
      case "rarity":
        return formatMetricInteger(topCard.populationPsa10, locale);
      default:
        return metricMoney(topCard.marketCap, snapshot, locale);
    }
  })();

  const values = {
    ...base,
    count: formatInteger(isSets ? groups.length : cards.length, locale),
    total: money(isSets ? gameTotalCap : sumCap(cards), snapshot, locale),
    top: isSets
      ? (topGroup ? setDisplayName(topGroup, locale) : copy[locale].status.unavailable)
      : (topCard ? cardName(topCard, locale) : copy[locale].status.unavailable),
    topValue,
  };

  const h1 = fill(t.rankings.h1[spec.kind], values);
  const answer = fill(t.rankings.answers[spec.kind], values);
  const table: HubTableBlock = isSets
    ? {
      id: "sets",
      caption: fill(t.rankings.tableCaption, values),
      columns: setColumns(locale),
      rows: setRows(groups, snapshot, locale, gameTotalCap),
    }
    : {
      id: "cards",
      caption: fill(t.rankings.tableCaption, values),
      note: t.common.changeNote,
      columns: cardColumns(t, { set: true }),
      rows: cardRows(cards, snapshot, locale, { set: true }),
    };

  const siblings = rankingSlugs
    .filter((candidate) => candidate !== spec.slug)
    .map((candidate) => ({
      label: rankingHeading(RANKING_SPECS[candidate], snapshot, locale, "h1"),
      href: withLang(`/rankings/${candidate}`, locale),
    }));

  const entries: ListEntry[] = isSets
    ? groups.map((group) => ({ name: setDisplayName(group, locale), url: canonicalPublicUrl(`/${group.game}/set/${group.slug}`) }))
    : cardEntries(cards, locale);

  return {
    path,
    title: fill(t.rankings.titles[spec.kind], values),
    description: fill(t.rankings.descriptions[spec.kind], values),
    kicker: t.rankings.kicker,
    h1,
    answer,
    breadcrumbLabel: t.common.breadcrumb,
    notes: [fill(t.common.updated, { date: formatObservationDate(snapshot.effectiveAt, locale) }), t.common.disambiguation],
    methodNote: t.common.method,
    methodLabel: t.common.methodLink,
    methodHref: withLang("/methodology", locale),
    crumbs,
    stats: [],
    tables: [table],
    linkGroups: [{ heading: t.rankings.siblings, links: siblings }],
    emptyNote: table.rows.length === 0 ? t.common.empty : undefined,
    jsonLd: {
      "@context": "https://schema.org",
      "@graph": [
        collectionPageNode(path, h1, answer, locale, snapshot),
        itemListNode(`${url}#list`, h1, entries),
        breadcrumbNode(crumbs),
        datasetNode(snapshot, allCards(snapshot).length, locale),
        organizationNode(),
      ],
    },
  };
}

/* ── Market report view ───────────────────────────────────── */

export function marketReportView(snapshot: MarketViewSnapshot, locale: Locale): HubView {
  const t = hubCopy[locale];
  const date = isoDate(snapshot);
  const month = monthYearLabel(snapshot.effectiveAt, locale);
  const path = "/market-report";
  const url = canonicalPublicUrl(path);

  const cards = allCards(snapshot);
  const pokemon = hubGameCards(snapshot, "pokemon");
  const onePiece = hubGameCards(snapshot, "one-piece");
  const totalCap = sumCap(cards);
  const pokemonCap = sumCap(pokemon);
  const onePieceCap = sumCap(onePiece);
  const ranked = cards.filter((card) => usable(card.marketCap)).sort(byCapDesc);
  const topTen = ranked.slice(0, 10);
  const topTenCap = sumCap(topTen);
  const sharePct = totalCap > 0 ? `${((topTenCap / totalCap) * 100).toFixed(1)}%` : copy[locale].status.unavailable;
  const topCard = ranked[0] ?? null;

  const gainers7 = rankingCards(snapshot, RANKING_SPECS["biggest-gainers-7d"], 5);
  const losers7 = rankingCards(snapshot, RANKING_SPECS["biggest-losers-7d"], 5);
  const gainers30 = rankingCards(snapshot, RANKING_SPECS["biggest-gainers-30d"], 5);
  const losers30 = rankingCards(snapshot, RANKING_SPECS["biggest-losers-30d"], 5);
  const population = rankingCards(snapshot, RANKING_SPECS["highest-psa-10-population"], 5);
  const pokemonSets = hubSetGroups(snapshot, "pokemon").slice(0, 5);
  const topSet = pokemonSets[0] ?? null;

  const values = {
    date,
    month,
    count: formatInteger(cards.length, locale),
    total: money(totalCap, snapshot, locale),
    pokemon: money(pokemonCap, snapshot, locale),
    onePiece: money(onePieceCap, snapshot, locale),
    share: sharePct,
    topTen: money(topTenCap, snapshot, locale),
    top: topCard ? cardName(topCard, locale) : copy[locale].status.unavailable,
    topValue: topCard ? metricMoney(topCard.marketCap, snapshot, locale) : copy[locale].status.unavailable,
  };

  const crumbs: HubCrumb[] = [
    { label: t.common.home, href: "/" },
    { label: t.report.kicker, href: "/market-report" },
  ];
  const h1 = fill(t.report.h1, values);
  const answer = fill(t.report.summary, values);

  const moverBlock = (
    id: string,
    heading: string,
    line: string,
    window: "7d" | "30d",
    movers: MarketCardView[],
  ): HubTableBlock => {
    const lead = movers[0];
    return {
      id,
      heading: `${heading} · ${t.windows[window]}`,
      intro: fill(line, {
        date,
        window: t.windows[window],
        top: lead ? cardName(lead, locale) : copy[locale].status.unavailable,
        topValue: lead ? formatPercent(lead.windows[window].changePct, locale) : copy[locale].status.unavailable,
      }),
      caption: `${heading} · ${t.windows[window]} (${date})`,
      columns: cardColumns(t, { set: true }),
      rows: cardRows(movers, snapshot, locale, { set: true }),
    };
  };

  const tables: HubTableBlock[] = [
    {
      id: "top-cards",
      heading: t.report.concentrationHeading,
      intro: fill(t.report.concentrationLine, values),
      caption: `${t.report.concentrationHeading} (${date})`,
      columns: cardColumns(t, { set: true }),
      rows: cardRows(topTen, snapshot, locale, { set: true }),
    },
    moverBlock("gainers-7d", t.report.gainersHeading, t.report.gainersLine, "7d", gainers7),
    moverBlock("losers-7d", t.report.losersHeading, t.report.losersLine, "7d", losers7),
    moverBlock("gainers-30d", t.report.gainersHeading, t.report.gainersLine, "30d", gainers30),
    moverBlock("losers-30d", t.report.losersHeading, t.report.losersLine, "30d", losers30),
    {
      id: "population",
      heading: t.report.populationHeading,
      intro: fill(t.report.populationLine, {
        date,
        top: population[0] ? cardName(population[0], locale) : copy[locale].status.unavailable,
        topValue: population[0] ? formatMetricInteger(population[0].populationPsa10, locale) : copy[locale].status.unavailable,
      }),
      caption: `${t.report.populationHeading} (${date})`,
      columns: cardColumns(t, { set: true }),
      rows: cardRows(population, snapshot, locale, { set: true }),
    },
    {
      id: "sets",
      heading: t.report.setsHeading,
      intro: fill(t.report.setsLine, {
        date,
        top: topSet ? setDisplayName(topSet, locale) : copy[locale].status.unavailable,
        topValue: topSet ? money(topSet.combinedCapUsd, snapshot, locale) : copy[locale].status.unavailable,
      }),
      caption: `${t.report.setsHeading} (${date})`,
      columns: setColumns(locale),
      rows: setRows(pokemonSets, snapshot, locale, pokemonCap),
    },
  ];

  return {
    path,
    title: fill(t.report.title, values),
    description: fill(t.report.description, values),
    kicker: t.report.kicker,
    h1,
    answer,
    breadcrumbLabel: t.common.breadcrumb,
    notes: [
      fill(t.report.totalsLine, values),
      t.report.note,
      t.common.disambiguation,
    ],
    methodNote: t.common.method,
    methodLabel: t.common.methodLink,
    methodHref: withLang("/methodology", locale),
    crumbs,
    /* 四個數字型 stat 行 count-up；`statTop10` 係一句百分比字串，冇 count-up。 */
    stats: [
      { label: t.report.statCards, value: formatInteger(cards.length, locale), tick: statTick(cards.length, "integer", snapshot, locale) },
      { label: t.report.statTotal, value: money(totalCap, snapshot, locale), tick: statTick(totalCap, "money", snapshot, locale) },
      { label: t.report.statPokemon, value: money(pokemonCap, snapshot, locale), tick: statTick(pokemonCap, "money", snapshot, locale) },
      { label: t.report.statOnePiece, value: money(onePieceCap, snapshot, locale), tick: statTick(onePieceCap, "money", snapshot, locale) },
      { label: t.report.statTop10, value: sharePct },
    ],
    tables,
    linkGroups: [
      {
        heading: t.rankings.siblings,
        links: rankingSlugs.map((slug) => ({
          label: rankingHeading(RANKING_SPECS[slug], snapshot, locale, "h1"),
          href: withLang(`/rankings/${slug}`, locale),
        })),
      },
    ],
    jsonLd: {
      "@context": "https://schema.org",
      "@graph": [
        {
          "@type": "Article",
          "@id": `${url}#article`,
          headline: h1,
          description: answer,
          inLanguage: locale,
          datePublished: snapshot.effectiveAt,
          dateModified: snapshot.generatedAt,
          author: { "@id": organizationId() },
          publisher: { "@id": organizationId() },
          isBasedOn: { "@id": datasetId() },
          mainEntityOfPage: url,
        },
        itemListNode(`${url}#list`, t.report.concentrationHeading, cardEntries(topTen, locale)),
        breadcrumbNode(crumbs),
        datasetNode(snapshot, cards.length, locale),
        organizationNode(),
      ],
    },
  };
}

/* ── Sitemap 契約 ─────────────────────────────────────────── */

export function seoRoutes(snapshot: MarketViewSnapshot): SeoRoute[] {
  const lastModified = snapshot.effectiveAt;
  return [
    { path: "/rankings", lastModified, priority: 0.8 },
    ...rankingSlugs.map((slug) => ({ path: `/rankings/${slug}`, lastModified, priority: 0.7 })),
    { path: "/market-report", lastModified, priority: 0.7 },
    ...(["pokemon", "one-piece"] as HubGame[]).flatMap((game) =>
      hubSetGroups(snapshot, game).map((group) => ({
        path: `/${game}/set/${group.slug}`,
        lastModified,
        priority: 0.6,
      }))),
  ];
}
