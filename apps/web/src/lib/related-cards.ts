import { formatInteger } from "./format";
import type { Locale, LocalizedText, MarketCardView, MarketMetric, MarketViewSnapshot } from "./types";

/*
 * 卡頁／BOX 頁嘅 internal linking + 可引用事實（GEO，owner 2026-08-16）。
 * 全部係 pure function：冇 React、冇 fetch、冇 `window`，所以 server component
 * （metadata）同 client component（card-detail）可以行同一份，唔會兩邊各寫一套。
 */

/*
 * ⚠️ 呢條 slug 規矩要同 set hub（src/lib/seo-routes.ts）**逐個字一樣**。
 * 卡頁個 set 麵包屑指去 /pokemon/set/{slug}，兩邊各自 slugify 就會出「crumb 指去
 * 一個唔存在嘅 hub」——1,918 條卡頁 URL 一齊死鏈。規矩：lowercase → NFKD →
 * 剝 diacritics → 非 a-z0-9 變 '-' → 收縮 → 剪頭尾。
 */
export function slugify(value: string): string {
  return value
    .toLowerCase()
    .normalize("NFKD")
    .replace(/\p{M}/gu, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/-{2,}/g, "-")
    .replace(/^-|-$/g, "");
}

/** Set hub slug. 一律由 `setName.en` 推導：其他語系可以係 null，英文一定有值。 */
export function setSlug(card: Pick<MarketCardView, "setName">): string {
  return slugify(card.setName.en ?? "");
}

/** `/pokemon` | `/one-piece`。`tcg` 喺 view 層已經 normalise 做「Pokémon」/「One Piece」。 */
export function tcgHubPath(tcg: string): string {
  return tcg === "One Piece" ? "/one-piece" : "/pokemon";
}

export function setHubPath(card: Pick<MarketCardView, "setName" | "tcg">): string {
  const slug = setSlug(card);
  return slug ? `${tcgHubPath(card.tcg)}/set/${slug}` : tcgHubPath(card.tcg);
}

/*
 * 內部連結一律帶返 `?lang=`（en = 裸 path，唔上 param —— 同 route-metadata 個
 * canonical 規矩一致）。client 側有 `useMarketSettings().href()` 連 currency/period
 * 一齊帶，呢個係 server 側（冇 hook）嗰個最細版本。
 */
export function withLang(path: string, locale: Locale): string {
  if (locale === "en") return path;
  return appendParam(path, "lang", locale);
}

/** `href()` 只識喺乾淨 path 後面貼 `?`，所以要加 query 一律行呢條，唔好自己夾字串。 */
export function appendParam(path: string, key: string, value: string): string {
  return `${path}${path.includes("?") ? "&" : "?"}${encodeURIComponent(key)}=${encodeURIComponent(value)}`;
}

/*
 * `officialName` 係 PSA/GemRate 成行證書字：「{年份} {set 英文名} {角色＋版本} {編號}」。
 * 剝走頭尾三段就淨返「主體」——實測 1,599 張卡，`setName.en` 100% 係前綴（0 miss），
 * 所以呢個剝法唔係估，係對住 seed-snapshot 數過。
 */
export function cardSubject(
  card: Pick<MarketCardView, "officialName" | "setName" | "collectorNumber">,
): string {
  const raw = (card.officialName ?? "").trim();
  if (!raw) return "";
  let text = raw.replace(/^\d{4}\s+/, "");
  const setEn = card.setName?.en ?? "";
  if (setEn && text.toLowerCase().startsWith(setEn.toLowerCase())) text = text.slice(setEn.length);
  text = text.trim();
  const number = (card.collectorNumber ?? "").trim();
  if (number && text.toLowerCase().endsWith(number.toLowerCase())) {
    text = text.slice(0, text.length - number.length);
  }
  return text.trim();
}

/*
 * 卡面詞彙（版本／稀有度／產品形態），唔係角色名。呢張清單係由 seed-snapshot 嘅
 * token 頻率表挑出嚟嘅，唔係抄一套通用停用詞：跑完之後 1,599 張入面 1,363 張
 * （85%）落到一個 ≥2 張嘅角色組，頭幾個 key 係 pikachu / charizard /
 * monkey(Monkey D. Luffy) / gengar / mewtwo，冇一個係版本字。
 * 揀唔到 key 嘅卡會攞唔到組，個模組就唔出——fail-closed，唔會夾硬砌一組錯嘅。
 */
const SUBJECT_STOPWORDS = new Set([
  "full", "art", "alternate", "alt", "manga", "special", "illustration", "illust", "rare", "secret",
  "ultra", "hyper", "reverse", "holo", "holofoil", "foil", "base", "set", "edition", "1st", "first",
  "unlimited", "shadowless", "promo", "box", "collection", "campaign", "center", "master", "ball",
  "gold", "silver", "rainbow", "textured", "character", "super", "sar", "ssr", "sr", "ar", "ur",
  "csr", "chr", "trainer", "supporter", "leader", "parallel", "mega", "poncho", "wearing",
  "v", "vmax", "vstar", "gx", "ex", "m", "x", "y", "s", "p", "e", "r", "n", "a",
  "the", "of", "and", "with", "signature", "anniversary", "limited", "winner", "staff",
  "prerelease", "championship", "championships", "world", "worlds", "stamp", "jumbo", "oversized",
  "deck", "starter", "theme", "pokemon", "tcg", "game", "card", "cards", "japanese", "english",
]);

function isNameToken(token: string): boolean {
  return !SUBJECT_STOPWORDS.has(token.toLowerCase()) && !/^\d+$/.test(token);
}

/** 角色 key（分組用，全細楷）＋ label（出街用，保留原文大細楷同標點，最多 3 個 token）。 */
export function cardCharacter(
  card: Pick<MarketCardView, "officialName" | "setName" | "collectorNumber">,
): { key: string; label: string } | null {
  const subject = cardSubject(card);
  if (!subject) return null;
  const tokens = [...subject.matchAll(/[A-Za-z0-9']+/g)];
  const start = tokens.findIndex((token) => isNameToken(token[0]));
  if (start < 0) return null;
  let end = start;
  while (end + 1 < tokens.length && end - start < 2 && isNameToken(tokens[end + 1][0])) end += 1;
  const head = tokens[start];
  const tail = tokens[end];
  const from = head.index ?? 0;
  const to = (tail.index ?? 0) + tail[0].length;
  return { key: head[0].toLowerCase(), label: subject.slice(from, to) };
}

/** 相關卡連結所需嘅最細投影。刻意唔傳 historyDaily / story：呢啲 byte 唔會有人睇。 */
export interface RelatedCardLink {
  id: string;
  officialName: string | null;
  name?: LocalizedText;
  setName: LocalizedText;
  collectorNumber: string;
  marketRank: number;
  marketCap: MarketMetric<number>;
  change7d: MarketMetric<number>;
}

export interface RelatedCardsPayload {
  /** 同一個 set（`setName.en` 完全一樣），按市值高至低，最多 8 張。 */
  sameSet: RelatedCardLink[];
  /** 市值排名 ±3。 */
  nearbyRanks: RelatedCardLink[];
  /** 同一個角色嘅其他印刷版本，按市值高至低，最多 8 張。 */
  otherPrintings: RelatedCardLink[];
  characterLabel: string | null;
  setPath: string;
  tcgPath: string;
  /** 同一個 TCG 有排名嘅卡數——卡頁「第 N 名／共 M 張」嗰個 M。 */
  tcgRankedCount: number;
  indexRankedCount: number;
}

const RELATED_LIMIT = 8;
const NEARBY_RANK_SPAN = 3;

function relatedLink(card: MarketCardView): RelatedCardLink {
  return {
    id: card.id,
    officialName: card.officialName ?? null,
    name: card.name,
    setName: card.setName,
    collectorNumber: card.collectorNumber,
    marketRank: card.marketRank,
    marketCap: card.marketCap,
    change7d: card.windows["7d"].marketCapChangePct,
  };
}

function byMarketCapDesc(a: MarketCardView, b: MarketCardView): number {
  return (b.marketCap.value ?? -1) - (a.marketCap.value ?? -1);
}

/*
 * ⚠️ 要**完整** snapshot（top100 + watchlist）先計得到，`singleCardSnapshot` 淨返一張卡。
 * 所以呢個 function 喺 route（server）行，計完傳個 plain payload 落 card-detail。
 */
export function relatedCards(snapshot: MarketViewSnapshot, id: string): RelatedCardsPayload | null {
  const all = [...snapshot.top100, ...snapshot.watchlist];
  const card = all.find((candidate) => candidate.id === id);
  if (!card) return null;

  const ranked = all.filter((candidate) => candidate.marketRank >= 1);
  const setKey = (card.setName.en ?? "").trim().toLowerCase();
  const sameSet = setKey
    ? all
      .filter((candidate) => candidate.id !== card.id && (candidate.setName.en ?? "").trim().toLowerCase() === setKey)
      .sort(byMarketCapDesc)
      .slice(0, RELATED_LIMIT)
    : [];

  const nearbyRanks = card.marketRank >= 1
    ? ranked
      .filter((candidate) => candidate.id !== card.id
        && Math.abs(candidate.marketRank - card.marketRank) <= NEARBY_RANK_SPAN)
      .sort((a, b) => a.marketRank - b.marketRank)
    : [];

  const character = cardCharacter(card);
  const otherPrintings = character
    ? all
      .filter((candidate) => candidate.id !== card.id && cardCharacter(candidate)?.key === character.key)
      .sort(byMarketCapDesc)
      .slice(0, RELATED_LIMIT)
    : [];

  return {
    sameSet: sameSet.map(relatedLink),
    nearbyRanks: nearbyRanks.map(relatedLink),
    otherPrintings: otherPrintings.map(relatedLink),
    characterLabel: character?.label ?? null,
    setPath: setHubPath(card),
    tcgPath: tcgHubPath(card.tcg),
    tcgRankedCount: ranked.filter((candidate) => candidate.tcg === card.tcg).length,
    indexRankedCount: ranked.length,
  };
}

/*
 * GEO 文案（owner 2026-08-16）。特登唔入 `src/lib/i18n.ts`：嗰個檔今日有幾個
 * owner 同時改緊，加多五個 locale 落去必撞。呢度嘅字全部係卡頁／BOX 頁專用，
 * 有需要嘅時候再由 i18n owner 收編。
 *
 * 寫法規矩（GEO）：每段可引用文字都要自己站得住——講得出實體名（CardZ Marketcap）、
 * 計法（PSA 10 價 × PSA 10 鑑定數量）同日期。數字全部由 snapshot 填，一個都唔准作。
 */
export interface GeoCopy {
  breadcrumbLabel: string;
  relatedRegion: string;
  sameSet: string;
  nearbyRanks: string;
  otherPrintings: string;
  viewSet: string;
  cardFact: string;
  cardFactRank: string;
  cardFactRankNoTotal: string;
  boxTitle: string;
  boxDescription: string;
  boxFact: string;
  boxDisambiguation: string;
  boxDetailTitle: string;
  boxDetailDescription: string;
  boxDetailDescriptionNoPrice: string;
  boxDetailFact: string;
  boxDetailFactNoPrice: string;
}

export const geoCopy: Record<Locale, GeoCopy> = {
  en: {
    breadcrumbLabel: "Breadcrumb",
    relatedRegion: "Related cards",
    sameSet: "More from this set",
    nearbyRanks: "Nearby market cap ranks",
    otherPrintings: "Other printings of {name}",
    viewSet: "View set",
    cardFact: "{name} ({set} #{num}) has a PSA 10 market cap of {cap} on CardZ Marketcap as of {date}: PSA 10 price {price} × PSA 10 population {pop}.",
    /* 前一句已經講明係 PSA 10 市值，排名句唔使再重覆一次「PSA 10」——慳兩個字，
       大部分卡先至守得住 50 字上限（1,599 張實測：中位數 45 字）。 */
    cardFactRank: "Ranked #{rank} of {total} {tcg} cards by market cap.",
    cardFactRankNoTotal: "Ranked #{rank} by market cap.",
    boxTitle: "Sealed Booster Box Prices — Sold-First Market Tracker",
    boxDescription: "Sealed booster box prices for the Pokémon TCG and One Piece Card Game, set by completed sales first. {priced} of {total} boxes carry a price as of {date}.",
    boxFact: "CardZ Marketcap tracks sealed booster box prices for the Pokémon TCG and the One Piece Card Game. A completed sale sets the reference price; an ask floor stands in only when no sale exists. {priced} of {total} boxes carry a price as of {date}.",
    boxDisambiguation: "CardZ Marketcap is a data index for graded trading cards and sealed boxes — not a cryptocurrency, a token or a listed company. There is no CardZ ticker.",
    /*
     * Title 收到「{name} Booster Box Price」（除名 18 字），最長嘅 box 名 40 字都仲
     * 喺 60 字以內 —— fitTitle 係斬個名，斬到「Offense and Defense of the Furthest E…」
     * 就唔似關鍵詞，寧願 title 短啲。「sealed」呢個詞留返落 description。
     */
    boxDetailTitle: "{name} Booster Box Price",
    boxDetailDescription: "{name} sealed booster box price: {price} on CardZ Marketcap as of {date}, set by completed sales, not ask floors.",
    boxDetailDescriptionNoPrice: "{name} sealed booster box price on CardZ Marketcap is still pending. Prices come from completed sales, not ask floors.",
    boxDetailFact: "The {name} sealed booster box has a reference price of {price} on CardZ Marketcap as of {date} ({priceKind}). A completed sale sets this figure; an ask floor stands in only when no sale exists.",
    boxDetailFactNoPrice: "The {name} sealed booster box is tracked by CardZ Marketcap but carries no reference price as of {date}: no completed sale is inside the tracked window. A missing price is never filled with zero.",
  },
  "zh-TW": {
    breadcrumbLabel: "麵包屑導覽",
    relatedRegion: "相關卡牌",
    sameSet: "同一系列的其他卡牌",
    nearbyRanks: "市值排名相鄰的卡牌",
    otherPrintings: "{name} 的其他版本",
    viewSet: "查看系列",
    cardFact: "{name}（{set} #{num}）在 CardZ Marketcap 的 PSA 10 市值為 {cap}（{date}）：PSA 10 價格 {price} × PSA 10 鑑定數量 {pop}。",
    cardFactRank: "在 {total} 張{tcg}卡牌之中，市值排名第 {rank}。",
    cardFactRankNoTotal: "市值排名第 {rank}。",
    boxTitle: "寶可夢／海賊王原盒價格 — 未拆盒行情追蹤",
    boxDescription: "寶可夢與海賊王集換式卡牌原盒價格，以成交價優先計算。截至 {date}，{total} 個原盒之中有 {priced} 個有價格。",
    boxFact: "CardZ Marketcap 追蹤寶可夢與海賊王集換式卡牌的未拆原盒價格。參考價一律以完成成交為準；只有在完全沒有成交時，才以最低要價暫代。截至 {date}，{total} 個原盒之中有 {priced} 個有價格。",
    boxDisambiguation: "CardZ Marketcap 是鑑定卡與未拆原盒的資料指數，並非加密貨幣、代幣或上市公司，也沒有 CardZ 代號。",
    boxDetailTitle: "{name} 原盒價格・未拆盒行情",
    boxDetailDescription: "{name} 未拆原盒：CardZ Marketcap 參考價 {price}（{date}），以完成成交優先計算，要價不會頂替成交價。",
    boxDetailDescriptionNoPrice: "{name} 未拆原盒：CardZ Marketcap 尚未有參考價。此處價格一律以完成成交為準，要價不會頂替成交價。",
    boxDetailFact: "{name} 未拆原盒在 CardZ Marketcap 的參考價為 {price}（{priceKind}，{date}）。此數字以完成成交為準；只有在完全沒有成交時，才以最低要價暫代。",
    boxDetailFactNoPrice: "{name} 未拆原盒已被 CardZ Marketcap 追蹤，但截至 {date} 沒有參考價：追蹤窗口內沒有完成成交。缺少的價格不會用零填補。",
  },
  "zh-CN": {
    breadcrumbLabel: "面包屑导航",
    relatedRegion: "相关卡牌",
    sameSet: "同一系列的其他卡牌",
    nearbyRanks: "市值排名相邻的卡牌",
    otherPrintings: "{name} 的其他版本",
    viewSet: "查看系列",
    cardFact: "{name}（{set} #{num}）在 CardZ Marketcap 的 PSA 10 市值为 {cap}（{date}）：PSA 10 价格 {price} × PSA 10 评级数量 {pop}。",
    cardFactRank: "在 {total} 张{tcg}卡牌之中，市值排名第 {rank}。",
    cardFactRankNoTotal: "市值排名第 {rank}。",
    boxTitle: "宝可梦／海贼王原盒价格 — 未拆盒行情追踪",
    boxDescription: "宝可梦与海贼王集换式卡牌原盒价格，以成交价优先计算。截至 {date}，{total} 个原盒之中有 {priced} 个有价格。",
    boxFact: "CardZ Marketcap 追踪宝可梦与海贼王集换式卡牌的未拆原盒价格。参考价一律以完成成交为准；只有在完全没有成交时，才以最低要价暂代。截至 {date}，{total} 个原盒之中有 {priced} 个有价格。",
    boxDisambiguation: "CardZ Marketcap 是评级卡与未拆原盒的数据指数，并非加密货币、代币或上市公司，也没有 CardZ 代号。",
    boxDetailTitle: "{name} 原盒价格・未拆盒行情",
    boxDetailDescription: "{name} 未拆原盒：CardZ Marketcap 参考价 {price}（{date}），以完成成交优先计算，要价不会顶替成交价。",
    boxDetailDescriptionNoPrice: "{name} 未拆原盒：CardZ Marketcap 尚未有参考价。此处价格一律以完成成交为准，要价不会顶替成交价。",
    boxDetailFact: "{name} 未拆原盒在 CardZ Marketcap 的参考价为 {price}（{priceKind}，{date}）。此数字以完成成交为准；只有在完全没有成交时，才以最低要价暂代。",
    boxDetailFactNoPrice: "{name} 未拆原盒已被 CardZ Marketcap 追踪，但截至 {date} 没有参考价：追踪窗口内没有完成成交。缺少的价格不会用零填补。",
  },
  ja: {
    breadcrumbLabel: "パンくずリスト",
    relatedRegion: "関連カード",
    sameSet: "同じセットの他のカード",
    nearbyRanks: "時価総額ランキングの前後",
    otherPrintings: "{name} の他のバージョン",
    viewSet: "セットを見る",
    cardFact: "{name}（{set} #{num}）の PSA 10 時価総額は CardZ Marketcap で {date} 時点 {cap}。PSA 10 価格 {price} × PSA 10 鑑定枚数 {pop} で算出。",
    cardFactRank: "{tcg}カード {total} 枚中、時価総額 {rank} 位。",
    cardFactRankNoTotal: "時価総額 {rank} 位。",
    boxTitle: "ポケカ・ワンピBOX相場 — 未開封 BOX価格トラッカー",
    boxDescription: "ポケカとワンピースカードの未開封 BOX相場。参考価格は成約優先で算出。{date} 時点で {total} BOX 中 {priced} BOX に価格。",
    boxFact: "CardZ Marketcap はポケモンカードとワンピースカードゲームの未開封 BOX相場を追跡します。参考価格は成約価格が基準で、成約がない場合のみ最安提示価格を代用します。{date} 時点で {total} BOX 中 {priced} BOX に価格があります。",
    boxDisambiguation: "CardZ Marketcap は鑑定済みトレカと未開封 BOXのデータ指数です。暗号資産・トークン・上場企業ではなく、CardZ のティッカーは存在しません。",
    boxDetailTitle: "{name} BOX相場・未開封 BOX価格",
    boxDetailDescription: "{name} 未開封 BOX：CardZ Marketcap の参考価格は {date} 時点で {price}。成約価格が基準で、提示価格が成約価格を代替することはありません。",
    boxDetailDescriptionNoPrice: "{name} 未開封 BOX：CardZ Marketcap にまだ参考価格がありません。価格は成約優先で算出し、提示価格が成約価格を代替することはありません。",
    boxDetailFact: "{name} の未開封 BOXの参考価格は CardZ Marketcap で {date} 時点 {price}（{priceKind}）。この数字は成約価格が基準で、成約がない場合のみ最安提示価格を代用します。",
    boxDetailFactNoPrice: "{name} の未開封 BOXは CardZ Marketcap が追跡していますが、{date} 時点で参考価格はありません。追跡期間内に成約がないためで、欠測をゼロで埋めることはしません。",
  },
  ko: {
    breadcrumbLabel: "탐색 경로",
    relatedRegion: "관련 카드",
    sameSet: "같은 세트의 다른 카드",
    nearbyRanks: "시가총액 순위가 인접한 카드",
    otherPrintings: "{name}의 다른 버전",
    viewSet: "세트 보기",
    cardFact: "{name}({set} #{num})의 PSA 10 시가총액은 CardZ Marketcap 기준 {date} 현재 {cap}입니다: PSA 10 가격 {price} × PSA 10 개체수 {pop}.",
    cardFactRank: "{tcg} 카드 {total}장 중 시가총액 {rank}위.",
    cardFactRankNoTotal: "시가총액 {rank}위.",
    boxTitle: "포켓몬·원피스 부스터 박스 시세 — 미개봉 박스 가격",
    boxDescription: "포켓몬·원피스 카드게임 미개봉 부스터 박스 시세. 기준가는 체결가 우선입니다. {date} 기준 {total}개 박스 중 {priced}개에 가격이 있습니다.",
    boxFact: "CardZ Marketcap은 포켓몬 카드와 원피스 카드게임의 미개봉 부스터 박스 시세를 추적합니다. 기준가는 체결된 판매가로 정하며, 체결가가 없을 때만 최저 호가로 대체합니다. {date} 기준 {total}개 박스 중 {priced}개에 가격이 있습니다.",
    boxDisambiguation: "CardZ Marketcap은 등급 카드와 미개봉 박스의 데이터 지수입니다. 암호화폐·토큰·상장사가 아니며 CardZ 티커는 없습니다.",
    boxDetailTitle: "{name} 부스터 박스 시세·미개봉 가격",
    boxDetailDescription: "{name} 미개봉 부스터 박스: CardZ Marketcap 기준가는 {date} 현재 {price}입니다. 체결가 우선이며 호가가 체결가를 대신하지 않습니다.",
    boxDetailDescriptionNoPrice: "{name} 미개봉 부스터 박스: CardZ Marketcap에 아직 기준가가 없습니다. 모든 가격은 체결가 우선이며 호가가 체결가를 대신하지 않습니다.",
    boxDetailFact: "{name} 미개봉 부스터 박스의 CardZ Marketcap 기준가는 {date} 현재 {price}입니다({priceKind}). 이 수치는 체결가로 정하며, 체결가가 없을 때만 최저 호가로 대체합니다.",
    boxDetailFactNoPrice: "{name} 미개봉 부스터 박스는 CardZ Marketcap이 추적 중이지만 {date} 기준 기준가가 없습니다: 추적 구간 안에 체결된 판매가 없습니다. 빠진 값을 0으로 채우지 않습니다.",
  },
};

/*
 * `<title>` 上限 60 字（root layout 之後再貼「 | CardZ Marketcap」）。長名要剪嘅係
 * 名，唔係關鍵詞：關鍵詞剪走咗個 title 就冇晒用。剪到最短都要留 12 個字畀個名。
 */
export function fitTitle(template: string, name: string, max = 60): string {
  const overhead = template.split("{name}").join("").length;
  const room = Math.max(max - overhead, 12);
  const trimmed = name.length > room ? `${name.slice(0, room - 1).trimEnd()}…` : name;
  return fillTemplate(template, { name: trimmed });
}

export function fillTemplate(template: string, values: Record<string, string | number>): string {
  return Object.entries(values).reduce(
    (text, [key, value]) => text.split(`{${key}}`).join(String(value)),
    template,
  );
}

export interface CardFactInput {
  name: string;
  set: string;
  num: string;
  cap: string;
  price: string;
  pop: string;
  date: string;
  rank: number;
  /** 同 TCG 有排名嘅卡總數；冇完整 snapshot 就係 null，嗰陣唔准講「共 N 張」。 */
  total: number | null;
  /** 已本地化嘅 TCG 名（t.nav.pokemon / t.nav.onePiece）。 */
  tcg: string;
}

/*
 * 卡頁嗰句可引用事實。三段拼埋：實體＋計法＋日期，跟住先至排名。
 * 排名 ≤0（awaiting fresh price）就唔出排名句，`total` 唔知就唔講總數——
 * 兩樣都係「唔知就唔講」，唔准填個似層層嘅數。
 */
export function cardFactSentence(locale: Locale, input: CardFactInput): string {
  const t = geoCopy[locale];
  const base = fillTemplate(t.cardFact, {
    name: input.name,
    set: input.set,
    num: input.num,
    cap: input.cap,
    price: input.price,
    pop: input.pop,
    date: input.date,
  });
  if (input.rank < 1) return base;
  const rank = input.total && input.total > 0
    ? fillTemplate(t.cardFactRank, {
      rank: input.rank,
      total: formatInteger(input.total, locale),
      tcg: input.tcg,
    })
    : fillTemplate(t.cardFactRankNoTotal, { rank: input.rank });
  /*
   * 兩句之間嘅空格由呢度決定，唔好寫死喺 template 度：ko 用半形句號但要空格，
   * ja/zh 用全形句號本身已經有留白。（ko 曾經黐埋一齊出「…49,787.포켓몬 카드…」）
   */
  const separator = /[。！？]$/.test(base) ? "" : " ";
  return `${base}${separator}${rank}`;
}
