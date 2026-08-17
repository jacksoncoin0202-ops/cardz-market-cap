import type { Locale } from "./types";
import type { HubGame, RankingKind } from "./seo-routes";

/*
 * SEO hub 頁（/pokemon/set/*、/one-piece/set/*、/rankings/*、/market-report）嘅五語文案。
 *
 * 點解另開一個檔而唔入 i18n.ts：i18n.ts 係 app UI 嘅 Copy 契約（nav / labels / hero），
 * 每加一條 key 五個 locale 都要改，而 hub 文案係 template（帶 {placeholder}）＋ 逐個
 * ranking kind 一組，形狀唔同。混埋會令 Copy interface 爆炸。共用嘅字（nav 遊戲名、
 * status.unavailable）一律由 i18n.ts 攞，唔喺呢度再抄一份。
 *
 * 硬規矩（GEO，owner 2026-08-16）：
 *  - 每版第一段 answer 必須自成一句可引用嘅答案，講齊「CardZ Marketcap」＋計法
 *    （PSA 10 價 × PSA 10 鑑定數）＋日期（snapshot effectiveAt）。
 *  - 所有數字由 snapshot 填，呢個檔一個硬編碼數字都唔准有。
 *  - 標題 head term 逐個 locale 用返關鍵詞原字（ja「時価総額」、zh-TW「市值」…），
 *    唔准由英文直譯。
 */

/* `{key}` 佔位符填數。缺 key 就原樣留低（睇得出邊條 template 漏咗值，好過靜靜出空白）。 */
export function fill(template: string, values: Record<string, string | number>): string {
  return template.replace(/\{(\w+)\}/g, (match, key: string) =>
    key in values ? String(values[key]) : match);
}

/* 內部連結一律保住 ?lang；en 出裸 path（同 route-metadata.ts 嘅 canonical 規則一致）。 */
export function withLang(path: string, locale: Locale): string {
  if (locale === "en") return path;
  return `${path}${path.includes("?") ? "&" : "?"}lang=${locale}`;
}

/*
 * 月份標題（「August 2026」／「2026年8月」）。format.ts 冇 export month+year formatter，
 * 亦冇 export 佢個 BCP-47 map，所以呢度自己有一份細嘅。UTC pin 同 formatObservationDate
 * 一樣：snapshot.effectiveAt 係 date-like，唔准畀 runtime 時區推前推後一日。
 * 欠單：應該搬去 format.ts 同 intlLocale 合併（見交付 requestsForOthers）。
 */
const monthLocale: Record<Locale, string> = {
  en: "en-US",
  "zh-TW": "zh-Hant-TW",
  "zh-CN": "zh-Hans-CN",
  ja: "ja-JP",
  ko: "ko-KR",
};

export function monthYearLabel(value: string | null, locale: Locale): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return "";
  return new Intl.DateTimeFormat(monthLocale[locale], { year: "numeric", month: "long", timeZone: "UTC" }).format(date);
}

export interface HubCopy {
  /* 標題／H1 用嘅遊戲名：跟關鍵詞原字（ja「ポケモンカード」而唔係 nav 嘅「ポケモン」）。 */
  games: Record<HubGame, string>;
  scopeAll: string;
  windows: Record<"7d" | "30d", string>;
  table: {
    rank: string;
    card: string;
    set: string;
    number: string;
    price: string;
    population: string;
    marketCap: string;
    change7d: string;
    change30d: string;
    cards: string;
    combinedCap: string;
    topCard: string;
    share: string;
  };
  common: {
    home: string;
    breadcrumb: string;
    method: string;
    methodLink: string;
    updated: string;
    disambiguation: string;
    changeNote: string;
    empty: string;
  };
  set: {
    kicker: string;
    h1: string;
    title: string;
    description: string;
    summary: string;
    tableCaption: string;
    siblings: string;
    back: string;
    setsIndex: string;
  };
  rankings: {
    kicker: string;
    indexH1: string;
    indexTitle: string;
    indexDescription: string;
    indexSummary: string;
    listHeading: string;
    siblings: string;
    tableCaption: string;
    titles: Record<RankingKind, string>;
    h1: Record<RankingKind, string>;
    descriptions: Record<RankingKind, string>;
    answers: Record<RankingKind, string>;
  };
  report: {
    kicker: string;
    h1: string;
    title: string;
    description: string;
    summary: string;
    statCards: string;
    statTotal: string;
    statPokemon: string;
    statOnePiece: string;
    statTop10: string;
    totalsHeading: string;
    totalsLine: string;
    concentrationHeading: string;
    concentrationLine: string;
    gainersHeading: string;
    gainersLine: string;
    losersHeading: string;
    losersLine: string;
    populationHeading: string;
    populationLine: string;
    setsHeading: string;
    setsLine: string;
    note: string;
  };
}

export const hubCopy: Record<Locale, HubCopy> = {
  en: {
    games: { pokemon: "Pokémon", "one-piece": "One Piece" },
    scopeAll: "the CardZ Marketcap index",
    windows: { "7d": "7-day", "30d": "30-day" },
    table: {
      rank: "#",
      card: "Card",
      set: "Set",
      number: "No.",
      price: "PSA 10 price",
      population: "PSA 10 population",
      marketCap: "Market cap",
      change7d: "7d price",
      change30d: "30d price",
      cards: "Cards",
      combinedCap: "Combined market cap",
      topCard: "Largest card",
      share: "Share",
    },
    common: {
      home: "Market",
      breadcrumb: "Breadcrumb",
      method: "How this list is built: market cap is the PSA 10 reference price multiplied by the verified PSA 10 population, recalculated on every daily update. Cards enter the index with a verified population of at least 1,000 PSA 10 examples.",
      methodLink: "Methodology",
      updated: "Data as of {date}. Figures change on every daily update.",
      disambiguation: "CardZ Marketcap is a data index for graded trading cards. It is not a cryptocurrency, a token or a listed company, and there is no CardZ ticker.",
      changeNote: "Percentage columns show the change in the PSA 10 reference price over the window, not a re-based population.",
      empty: "No card in this list carries a published figure in the current snapshot.",
    },
    set: {
      kicker: "SET MARKET CAP",
      h1: "{set} — card market cap rankings (PSA 10)",
      title: "{set} market cap (PSA 10)",
      description: "PSA 10 market cap rankings for {count} {set} cards: PSA 10 price times verified population, {total} combined. CardZ Marketcap, {date}.",
      summary: "CardZ Marketcap tracks {count} PSA 10 cards from {set}, worth {total} of combined market cap as of {date}. Market cap is the verified PSA 10 population multiplied by a PSA 10 reference price. The largest card in the set is {top} at {topValue}.",
      tableCaption: "{set} cards ranked by PSA 10 market cap, as of {date}",
      siblings: "Other sets by market cap",
      back: "All {game} rankings",
      setsIndex: "Sets by market cap",
    },
    rankings: {
      kicker: "RANKINGS",
      indexH1: "Trading card market cap rankings ({year})",
      indexTitle: "Trading card market cap rankings ({year})",
      indexDescription: "Ranked lists of Pokémon and One Piece cards by PSA 10 market cap, PSA 10 price, population and price moves. CardZ Marketcap data, as of {date}.",
      indexSummary: "CardZ Marketcap publishes {lists} ranking lists built from one daily snapshot of {count} graded cards. Market cap is the verified PSA 10 population multiplied by a PSA 10 reference price, and every list on this page is as of {date}.",
      listHeading: "All rankings",
      siblings: "More rankings",
      tableCaption: "Ranked cards, as of {date}",
      titles: {
        value: "Most valuable {game} cards ({year}) — PSA 10 market cap",
        price: "Most expensive {game} cards ({year}) — PSA 10 price",
        gainers: "Biggest {window} gainers — PSA 10 card prices ({year})",
        losers: "Biggest {window} losers — PSA 10 card prices ({year})",
        population: "Highest PSA 10 population — graded card market index",
        rarity: "Rarest PSA 10 population cards ({year})",
        sets: "{game} card sets by market cap ({year})",
      },
      h1: {
        value: "Most valuable {game} cards ({year}) — ranked by PSA 10 market cap",
        price: "Most expensive {game} cards ({year}) — ranked by PSA 10 price",
        gainers: "Biggest {window} gainers ({year}) — PSA 10 card price moves",
        losers: "Biggest {window} losers ({year}) — PSA 10 card price moves",
        population: "Highest PSA 10 population cards ({year}) — graded card market index",
        rarity: "Rarest PSA 10 populations ({year}) — lowest graded populations with a price",
        sets: "{game} card sets by market cap ({year}) — PSA 10 index",
      },
      descriptions: {
        value: "The most valuable {game} cards by PSA 10 market cap: PSA 10 price times verified population, with population and price moves, as of {date}.",
        price: "The most expensive {game} cards by PSA 10 reference price, each with its verified PSA 10 population and market cap. CardZ Marketcap, {date}.",
        gainers: "The biggest {window} gainers in PSA 10 card prices, Pokémon and One Piece, with population and market cap for each card. CardZ Marketcap, {date}.",
        losers: "The biggest {window} losers in PSA 10 card prices, Pokémon and One Piece, with population and market cap for each card. CardZ Marketcap, {date}.",
        population: "Cards with the highest verified PSA 10 populations in the CardZ Marketcap graded card index, each with PSA 10 price and market cap, as of {date}.",
        rarity: "Cards with the lowest verified PSA 10 populations that still carry a PSA 10 reference price on CardZ Marketcap, with market cap for each, as of {date}.",
        sets: "Every {game} card set ranked by combined PSA 10 market cap on CardZ Marketcap, with card count and largest card per set, as of {date}.",
      },
      answers: {
        value: "CardZ Marketcap ranks {scope} cards by PSA 10 market cap — the verified PSA 10 population multiplied by a PSA 10 reference price. As of {date}, {top} leads at {topValue}, and the {count} cards listed here hold {total} of market cap combined.",
        price: "This CardZ Marketcap list ranks {scope} cards by PSA 10 reference price, the price side of market cap (price times PSA 10 population). As of {date}, the highest price on the board is {top} at {topValue}, across the {count} cards listed here.",
        gainers: "CardZ Marketcap tracks the {window} change in each card's PSA 10 reference price. As of {date}, the biggest {window} gainer is {top} at {topValue}, out of the {count} cards listed on this page. Market cap is that price multiplied by PSA 10 population.",
        losers: "CardZ Marketcap tracks the {window} change in each card's PSA 10 reference price. As of {date}, the biggest {window} decline is {top} at {topValue}, out of the {count} cards listed on this page. Market cap is that price multiplied by PSA 10 population.",
        population: "Population is how many copies PSA has graded 10. As of {date}, the largest verified PSA 10 population in the CardZ Marketcap index is {top} with {topValue} copies, and this page lists the top {count}. Market cap is that population multiplied by a PSA 10 reference price.",
        rarity: "These are the smallest verified PSA 10 populations among priced cards in the CardZ Marketcap index. As of {date}, the lowest is {top} with {topValue} PSA 10 copies. A small population means scarcity, not a large market cap: cap is population times PSA 10 price.",
        sets: "CardZ Marketcap groups {scope} cards by set and adds up their PSA 10 market caps. As of {date}, the largest set is {top} at {topValue}, across the {count} sets listed here, which total {total} combined.",
      },
    },
    report: {
      kicker: "MARKET REPORT",
      h1: "Trading card market report — {month} (as of {date})",
      title: "Trading card market report — {month}",
      description: "Monthly state of the graded card market index: PSA 10 market cap totals for Pokémon and One Piece, concentration and biggest price moves, as of {date}.",
      summary: "CardZ Marketcap tracks {count} graded Pokémon and One Piece cards by PSA 10 market cap — verified PSA 10 population multiplied by a PSA 10 reference price. As of {date} the index totals {total}: {pokemon} in Pokémon and {onePiece} in One Piece.",
      statCards: "Cards tracked",
      statTotal: "Total PSA 10 market cap",
      statPokemon: "Pokémon market cap",
      statOnePiece: "One Piece market cap",
      statTop10: "Top 10 share of index",
      totalsHeading: "Market cap totals",
      totalsLine: "According to CardZ Marketcap, the {count} graded cards in the index carried a combined PSA 10 market cap of {total} as of {date} — {pokemon} in Pokémon and {onePiece} in One Piece.",
      concentrationHeading: "Concentration",
      concentrationLine: "The ten largest cards hold {share} of the index as of {date}, worth {topTen} of the {total} total. The single largest is {top} at {topValue}.",
      gainersHeading: "Biggest gainers",
      gainersLine: "The biggest {window} gain in PSA 10 reference price as of {date} is {top} at {topValue}.",
      losersHeading: "Biggest losers",
      losersLine: "The biggest {window} decline in PSA 10 reference price as of {date} is {top} at {topValue}.",
      populationHeading: "Largest populations",
      populationLine: "The largest verified PSA 10 population as of {date} is {top} with {topValue} graded copies.",
      setsHeading: "Largest sets",
      setsLine: "The largest set by combined PSA 10 market cap as of {date} is {top} at {topValue}.",
      note: "Auto-generated from the daily CardZ Marketcap snapshot. Every number on this page is recalculated with the next daily update and will change.",
    },
  },
  "zh-TW": {
    games: { pokemon: "寶可夢卡牌", "one-piece": "海賊王卡牌" },
    scopeAll: "CardZ Marketcap 指數",
    windows: { "7d": "7 日", "30d": "30 日" },
    table: {
      rank: "#",
      card: "卡牌",
      set: "系列",
      number: "編號",
      price: "PSA 10 價格",
      population: "PSA 10 鑑定數量",
      marketCap: "市值",
      change7d: "7 日價格",
      change30d: "30 日價格",
      cards: "卡牌數",
      combinedCap: "合計市值",
      topCard: "市值最高卡",
      share: "佔比",
    },
    common: {
      home: "市場",
      breadcrumb: "導覽路徑",
      method: "此排行的計算方式：市值 = PSA 10 參考價 × 已核實 PSA 10 鑑定數量，每日更新後重算。入榜門檻為已核實 PSA 10 鑑定數量至少 1,000 張。",
      methodLink: "計算方法",
      updated: "資料截至 {date}，每日更新後數字會變動。",
      disambiguation: "CardZ Marketcap 是鑑定卡牌的數據指數，不是加密貨幣、代幣或上市公司，也沒有 CardZ 代幣代號。",
      changeNote: "百分比欄位為該期間內 PSA 10 參考價的變動，並非鑑定數量重算。",
      empty: "目前的快照中，此排行沒有任何卡牌具備已公布數字。",
    },
    set: {
      kicker: "系列市值",
      h1: "{set} — 卡牌市值排行（PSA 10）",
      title: "{set} 市值排行（PSA 10）",
      description: "CardZ Marketcap 收錄 {set} 共 {count} 張 PSA 10 卡牌市值排行：PSA 10 價格 × 鑑定數量，合計 {total}，資料截至 {date}。",
      summary: "CardZ Marketcap 收錄 {set} 共 {count} 張 PSA 10 卡牌，截至 {date} 合計市值 {total}。市值 = 已核實 PSA 10 鑑定數量 × PSA 10 參考價。系列中市值最高為 {top}，達 {topValue}。",
      tableCaption: "{set} 按 PSA 10 市值排行，資料截至 {date}",
      siblings: "其他系列市值排行",
      back: "{game} 全部排行",
      setsIndex: "各系列市值排行",
    },
    rankings: {
      kicker: "排行榜",
      indexH1: "集換式卡牌 市值排行（{year}）",
      indexTitle: "集換式卡牌 市值排行（{year}）",
      indexDescription: "寶可夢卡牌與海賊王卡牌 PSA 10 市值、價格、鑑定數量與漲跌排行榜。CardZ Marketcap 資料，截至 {date}。",
      indexSummary: "CardZ Marketcap 以同一份每日快照，收錄 {count} 張鑑定卡牌，產生 {lists} 個排行榜。市值 = 已核實 PSA 10 鑑定數量 × PSA 10 參考價，本頁全部榜單資料截至 {date}。",
      listHeading: "全部排行榜",
      siblings: "更多排行榜",
      tableCaption: "排行榜，資料截至 {date}",
      titles: {
        value: "最有價值的{game} 市值排行（{year}）",
        price: "最貴{game} PSA 10 價格排行（{year}）",
        gainers: "{window}漲幅排行 — PSA 10 卡牌價格（{year}）",
        losers: "{window}跌幅排行 — PSA 10 卡牌價格（{year}）",
        population: "PSA 10 鑑定數量最多的卡牌排行",
        rarity: "PSA 10 鑑定數量最少的卡牌排行（{year}）",
        sets: "{game} 各系列市值排行（{year}）",
      },
      h1: {
        value: "最有價值的{game} 市值排行（{year}）— 按 PSA 10 市值",
        price: "最貴{game}（{year}）— 按 PSA 10 參考價排行",
        gainers: "{window}漲幅排行（{year}）— PSA 10 卡牌價格變動",
        losers: "{window}跌幅排行（{year}）— PSA 10 卡牌價格變動",
        population: "PSA 10 鑑定數量最多的卡牌（{year}）— 鑑定卡牌市值指數",
        rarity: "PSA 10 鑑定數量最少的卡牌（{year}）— 有參考價的最低鑑定數量",
        sets: "{game} 各系列市值排行（{year}）— PSA 10 指數",
      },
      descriptions: {
        value: "CardZ Marketcap 按 PSA 10 市值排列最有價值的{game}：PSA 10 價格 × 已核實鑑定數量，同時顯示鑑定數量與價格變動，資料截至 {date}。",
        price: "按 PSA 10 參考價排列最貴的{game}，每張卡同時顯示已核實 PSA 10 鑑定數量與市值。CardZ Marketcap 資料，截至 {date}。",
        gainers: "寶可夢與海賊王卡牌 PSA 10 價格{window}漲幅排行，每張卡附鑑定數量與市值。CardZ Marketcap 資料，截至 {date}。",
        losers: "寶可夢與海賊王卡牌 PSA 10 價格{window}跌幅排行，每張卡附鑑定數量與市值。CardZ Marketcap 資料，截至 {date}。",
        population: "CardZ Marketcap 鑑定卡牌市值指數中，已核實 PSA 10 鑑定數量最多的卡牌，附 PSA 10 價格與市值，資料截至 {date}。",
        rarity: "CardZ Marketcap 中具備 PSA 10 參考價、且已核實鑑定數量最少的卡牌，附市值，資料截至 {date}。",
        sets: "CardZ Marketcap 按合計 PSA 10 市值排列全部{game}系列，附卡牌數與系列中市值最高的卡牌，資料截至 {date}。",
      },
      answers: {
        value: "CardZ Marketcap 按 PSA 10 市值排列{scope}卡牌 —— 市值 = 已核實 PSA 10 鑑定數量 × PSA 10 參考價。截至 {date}，第一位為 {top}，達 {topValue}；本頁 {count} 張卡合計市值 {total}。",
        price: "此 CardZ Marketcap 排行按 PSA 10 參考價排列{scope}卡牌，即市值公式（價格 × PSA 10 鑑定數量）中的價格一端。截至 {date}，價格最高為 {top}，達 {topValue}，本頁共 {count} 張卡。",
        gainers: "CardZ Marketcap 追蹤每張卡 PSA 10 參考價的{window}變動。截至 {date}，{window}漲幅最大的是 {top}，達 {topValue}，本頁共 {count} 張卡。市值 = 該價格 × PSA 10 鑑定數量。",
        losers: "CardZ Marketcap 追蹤每張卡 PSA 10 參考價的{window}變動。截至 {date}，{window}跌幅最大的是 {top}，達 {topValue}，本頁共 {count} 張卡。市值 = 該價格 × PSA 10 鑑定數量。",
        population: "鑑定數量指 PSA 評為 10 分的張數。截至 {date}，CardZ Marketcap 指數中已核實 PSA 10 鑑定數量最多的是 {top}，共 {topValue} 張，本頁列出前 {count} 名。市值 = 該鑑定數量 × PSA 10 參考價。",
        rarity: "以下是 CardZ Marketcap 指數中具備參考價、且已核實 PSA 10 鑑定數量最少的卡牌。截至 {date}，最少的是 {top}，僅 {topValue} 張。數量少代表稀有，並不代表市值高：市值 = 鑑定數量 × PSA 10 價格。",
        sets: "CardZ Marketcap 將{scope}卡牌按系列分組並加總 PSA 10 市值。截至 {date}，最大系列為 {top}，達 {topValue}；本頁 {count} 個系列合計 {total}。",
      },
    },
    report: {
      kicker: "市場報告",
      h1: "集換式卡牌市場報告 — {month}（資料截至 {date}）",
      title: "集換式卡牌市場報告 — {month}",
      description: "鑑定卡牌市值指數月度概況：寶可夢與海賊王 PSA 10 市值總額、集中度與最大價格變動，資料截至 {date}。",
      summary: "CardZ Marketcap 按 PSA 10 市值追蹤 {count} 張寶可夢與海賊王鑑定卡牌 —— 市值 = 已核實 PSA 10 鑑定數量 × PSA 10 參考價。截至 {date}，指數合計 {total}：寶可夢 {pokemon}，海賊王 {onePiece}。",
      statCards: "追蹤卡牌數",
      statTotal: "PSA 10 市值總額",
      statPokemon: "寶可夢市值",
      statOnePiece: "海賊王市值",
      statTop10: "頭 10 名佔比",
      totalsHeading: "市值總額",
      totalsLine: "根據 CardZ Marketcap，截至 {date}，指數中 {count} 張鑑定卡牌合計 PSA 10 市值為 {total} —— 寶可夢 {pokemon}，海賊王 {onePiece}。",
      concentrationHeading: "集中度",
      concentrationLine: "截至 {date}，市值最大的十張卡佔指數 {share}，合計 {topTen}（總額 {total}）。單張最大為 {top}，達 {topValue}。",
      gainersHeading: "最大升幅",
      gainersLine: "截至 {date}，PSA 10 參考價{window}漲幅最大的是 {top}，達 {topValue}。",
      losersHeading: "最大跌幅",
      losersLine: "截至 {date}，PSA 10 參考價{window}跌幅最大的是 {top}，達 {topValue}。",
      populationHeading: "鑑定數量最多",
      populationLine: "截至 {date}，已核實 PSA 10 鑑定數量最多的是 {top}，共 {topValue} 張。",
      setsHeading: "最大系列",
      setsLine: "截至 {date}，合計 PSA 10 市值最大的系列為 {top}，達 {topValue}。",
      note: "本頁由每日 CardZ Marketcap 快照自動生成，所有數字會在下一次每日更新後重算並變動。",
    },
  },
  "zh-CN": {
    games: { pokemon: "宝可梦卡牌", "one-piece": "海贼王卡牌" },
    scopeAll: "CardZ Marketcap 指数",
    windows: { "7d": "7 日", "30d": "30 日" },
    table: {
      rank: "#",
      card: "卡牌",
      set: "系列",
      number: "编号",
      price: "PSA 10 价格",
      population: "PSA 10 评级数量",
      marketCap: "市值",
      change7d: "7 日价格",
      change30d: "30 日价格",
      cards: "卡牌数",
      combinedCap: "合计市值",
      topCard: "市值最高卡",
      share: "占比",
    },
    common: {
      home: "市场",
      breadcrumb: "导航路径",
      method: "本榜算法：市值 = PSA 10 参考价 × 已核实 PSA 10 评级数量，每日更新重算一次。入榜门槛为已核实 PSA 10 评级数量至少 1,000 张。",
      methodLink: "计算方法",
      updated: "数据截至 {date}，每日更新后数字会变化。",
      disambiguation: "CardZ Marketcap 是评级卡牌的数据指数，不是加密货币、代币或上市公司，也没有 CardZ 代币代号。",
      changeNote: "百分比列为该窗口内 PSA 10 参考价的变动，不是评级数量重算。",
      empty: "当前快照中，本榜没有卡牌带已公布数字。",
    },
    set: {
      kicker: "系列市值",
      h1: "{set} — 卡牌市值排行（PSA 10）",
      title: "{set} 市值排行（PSA 10）",
      description: "CardZ Marketcap 收录 {set} 共 {count} 张 PSA 10 卡牌市值排行：PSA 10 价格 × 评级数量，合计 {total}，数据截至 {date}。",
      summary: "CardZ Marketcap 收录 {set} 共 {count} 张 PSA 10 卡牌，截至 {date} 合计市值 {total}。市值 = 已核实 PSA 10 评级数量 × PSA 10 参考价。系列内市值最高的是 {top}，达 {topValue}。",
      tableCaption: "{set} 按 PSA 10 市值排行，数据截至 {date}",
      siblings: "其他系列市值排行",
      back: "{game} 全部排行",
      setsIndex: "各系列市值排行",
    },
    rankings: {
      kicker: "排行榜",
      indexH1: "集换式卡牌 市值指数排行（{year}）",
      indexTitle: "集换式卡牌 市值指数排行（{year}）",
      indexDescription: "宝可梦卡牌与海贼王卡牌 PSA 10 市值、价格、评级数量与涨跌排行榜。CardZ Marketcap 数据，截至 {date}。",
      indexSummary: "CardZ Marketcap 用同一份每日快照，收录 {count} 张评级卡牌，输出 {lists} 个排行榜。市值 = 已核实 PSA 10 评级数量 × PSA 10 参考价，本页全部榜单数据截至 {date}。",
      listHeading: "全部排行榜",
      siblings: "更多排行榜",
      tableCaption: "排行榜，数据截至 {date}",
      titles: {
        value: "最有价值的{game} 市值排行（{year}）",
        price: "最贵{game} PSA 10 价格排行（{year}）",
        gainers: "{window}涨幅排行 — PSA 10 卡牌价格（{year}）",
        losers: "{window}跌幅排行 — PSA 10 卡牌价格（{year}）",
        population: "PSA 10 评级数量最多的卡牌排行",
        rarity: "PSA 10 评级数量最少的卡牌排行（{year}）",
        sets: "{game} 各系列市值排行（{year}）",
      },
      h1: {
        value: "最有价值的{game} 市值排行（{year}）— 按 PSA 10 市值",
        price: "最贵{game}（{year}）— 按 PSA 10 参考价排行",
        gainers: "{window}涨幅排行（{year}）— PSA 10 卡牌价格变动",
        losers: "{window}跌幅排行（{year}）— PSA 10 卡牌价格变动",
        population: "PSA 10 评级数量最多的卡牌（{year}）— 评级卡牌市值指数",
        rarity: "PSA 10 评级数量最少的卡牌（{year}）— 有参考价的最低评级数量",
        sets: "{game} 各系列市值排行（{year}）— PSA 10 指数",
      },
      descriptions: {
        value: "CardZ Marketcap 按 PSA 10 市值排列最有价值的{game}：PSA 10 价格 × 已核实评级数量，同时显示评级数量与价格变动，数据截至 {date}。",
        price: "按 PSA 10 参考价排列最贵的{game}，每张卡同时显示已核实 PSA 10 评级数量与市值。CardZ Marketcap 数据，截至 {date}。",
        gainers: "宝可梦与海贼王卡牌 PSA 10 价格{window}涨幅排行，每张卡附评级数量与市值。CardZ Marketcap 数据，截至 {date}。",
        losers: "宝可梦与海贼王卡牌 PSA 10 价格{window}跌幅排行，每张卡附评级数量与市值。CardZ Marketcap 数据，截至 {date}。",
        population: "CardZ Marketcap 评级卡牌市值指数中，已核实 PSA 10 评级数量最多的卡牌，附 PSA 10 价格与市值，数据截至 {date}。",
        rarity: "CardZ Marketcap 中带 PSA 10 参考价、且已核实评级数量最少的卡牌，附市值，数据截至 {date}。",
        sets: "CardZ Marketcap 按合计 PSA 10 市值排列全部{game}系列，附卡牌数与系列内市值最高卡，数据截至 {date}。",
      },
      answers: {
        value: "CardZ Marketcap 按 PSA 10 市值排列{scope}卡牌 —— 市值 = 已核实 PSA 10 评级数量 × PSA 10 参考价。截至 {date}，第一位是 {top}，达 {topValue}；本页 {count} 张卡合计市值 {total}。",
        price: "本 CardZ Marketcap 榜按 PSA 10 参考价排列{scope}卡牌，即市值公式（价格 × PSA 10 评级数量）的价格一侧。截至 {date}，价格最高的是 {top}，达 {topValue}，本页共 {count} 张卡。",
        gainers: "CardZ Marketcap 追踪每张卡 PSA 10 参考价的{window}变动。截至 {date}，{window}涨幅最大的是 {top}，达 {topValue}，本页共 {count} 张卡。市值 = 该价格 × PSA 10 评级数量。",
        losers: "CardZ Marketcap 追踪每张卡 PSA 10 参考价的{window}变动。截至 {date}，{window}跌幅最大的是 {top}，达 {topValue}，本页共 {count} 张卡。市值 = 该价格 × PSA 10 评级数量。",
        population: "评级数量即 PSA 评为 10 分的张数。截至 {date}，CardZ Marketcap 指数中已核实 PSA 10 评级数量最多的是 {top}，有 {topValue} 张，本页列出前 {count} 名。市值 = 该评级数量 × PSA 10 参考价。",
        rarity: "这些是 CardZ Marketcap 指数中带价格、且已核实 PSA 10 评级数量最少的卡。截至 {date}，最少的是 {top}，仅 {topValue} 张。数量少代表稀有，不代表市值高：市值 = 评级数量 × PSA 10 价格。",
        sets: "CardZ Marketcap 将{scope}卡牌按系列分组并加总 PSA 10 市值。截至 {date}，最大系列是 {top}，达 {topValue}；本页 {count} 个系列合计 {total}。",
      },
    },
    report: {
      kicker: "市场报告",
      h1: "集换式卡牌市场报告 — {month}（数据截至 {date}）",
      title: "集换式卡牌市场报告 — {month}",
      description: "评级卡牌市值指数月度概况：宝可梦与海贼王 PSA 10 市值总额、集中度与最大价格变动，数据截至 {date}。",
      summary: "CardZ Marketcap 按 PSA 10 市值追踪 {count} 张宝可梦与海贼王评级卡牌 —— 市值 = 已核实 PSA 10 评级数量 × PSA 10 参考价。截至 {date} 指数合计 {total}：宝可梦 {pokemon}，海贼王 {onePiece}。",
      statCards: "追踪卡牌数",
      statTotal: "PSA 10 市值总额",
      statPokemon: "宝可梦市值",
      statOnePiece: "海贼王市值",
      statTop10: "前 10 名占比",
      totalsHeading: "市值总额",
      totalsLine: "根据 CardZ Marketcap，截至 {date}，指数中 {count} 张评级卡牌合计 PSA 10 市值 {total} —— 宝可梦 {pokemon}，海贼王 {onePiece}。",
      concentrationHeading: "集中度",
      concentrationLine: "截至 {date}，市值最大的十张卡占指数 {share}，合计 {topTen}（总额 {total}）。单张最大的是 {top}，达 {topValue}。",
      gainersHeading: "最大涨幅",
      gainersLine: "截至 {date}，PSA 10 参考价{window}涨幅最大的是 {top}，达 {topValue}。",
      losersHeading: "最大跌幅",
      losersLine: "截至 {date}，PSA 10 参考价{window}跌幅最大的是 {top}，达 {topValue}。",
      populationHeading: "评级数量最多",
      populationLine: "截至 {date}，已核实 PSA 10 评级数量最多的是 {top}，有 {topValue} 张。",
      setsHeading: "最大系列",
      setsLine: "截至 {date}，合计 PSA 10 市值最大的系列是 {top}，达 {topValue}。",
      note: "本页由每日 CardZ Marketcap 快照自动生成，所有数字会在下一次每日更新后重算并变化。",
    },
  },
  ja: {
    games: { pokemon: "ポケモンカード", "one-piece": "ワンピースカード" },
    scopeAll: "CardZ Marketcap 指数",
    windows: { "7d": "7日間", "30d": "30日間" },
    table: {
      rank: "#",
      card: "カード",
      set: "弾（セット）",
      number: "番号",
      price: "PSA 10 価格",
      population: "PSA 10 鑑定枚数",
      marketCap: "時価総額",
      change7d: "7日 価格",
      change30d: "30日 価格",
      cards: "カード数",
      combinedCap: "合計時価総額",
      topCard: "最大カード",
      share: "構成比",
    },
    common: {
      home: "マーケット",
      breadcrumb: "パンくずリスト",
      method: "算出方法：時価総額 = PSA 10 参考価格 × 検証済み PSA 10 鑑定枚数。毎日の更新ごとに再計算します。掲載条件は検証済み PSA 10 鑑定枚数 1,000 枚以上。",
      methodLink: "算出方法",
      updated: "データ基準日 {date}。毎日の更新で数値は変わります。",
      disambiguation: "CardZ Marketcap は鑑定済みトレーディングカードのデータ指数です。暗号資産・トークン・上場企業ではなく、CardZ という銘柄コードも存在しません。",
      changeNote: "パーセント列は当該期間の PSA 10 参考価格の変動で、鑑定枚数の再計算ではありません。",
      empty: "現在のスナップショットでは、このランキングに公表値を持つカードがありません。",
    },
    set: {
      kicker: "弾別 時価総額",
      h1: "{set} — カード時価総額ランキング（PSA 10）",
      title: "{set} 時価総額ランキング（PSA 10）",
      description: "CardZ Marketcap が収録する {set} の PSA 10 カード {count} 枚の時価総額ランキング。PSA 10 価格 × 鑑定枚数、合計 {total}、基準日 {date}。",
      summary: "CardZ Marketcap は {set} の PSA 10 カードを {count} 枚収録しており、{date} 時点の合計時価総額は {total} です。時価総額は検証済み PSA 10 鑑定枚数 × PSA 10 参考価格。弾内の最大は {top} で {topValue} です。",
      tableCaption: "{set} の PSA 10 時価総額ランキング（基準日 {date}）",
      siblings: "他の弾の時価総額ランキング",
      back: "{game} のランキング一覧",
      setsIndex: "弾別 時価総額ランキング",
    },
    rankings: {
      kicker: "ランキング",
      indexH1: "トレカ 時価総額ランキング（{year}年）",
      indexTitle: "トレカ 時価総額ランキング（{year}年）",
      indexDescription: "ポケモンカードとワンピースカードの PSA 10 時価総額・価格・鑑定枚数・値動きランキング。CardZ Marketcap のデータ、基準日 {date}。",
      indexSummary: "CardZ Marketcap は同一の日次スナップショットから、鑑定済みカード {count} 枚をもとに {lists} 本のランキングを公開しています。時価総額は検証済み PSA 10 鑑定枚数 × PSA 10 参考価格で、本ページの全ランキングは {date} 時点です。",
      listHeading: "ランキング一覧",
      siblings: "他のランキング",
      tableCaption: "ランキング（基準日 {date}）",
      titles: {
        value: "{game} 時価総額ランキング（{year}年）",
        price: "{game} 高額ランキング PSA 10価格（{year}年）",
        gainers: "{window} 値上がりランキング PSA 10価格（{year}年）",
        losers: "{window} 値下がりランキング PSA 10価格（{year}年）",
        population: "PSA 10 鑑定枚数ランキング — トレカ時価総額指数",
        rarity: "PSA 10 鑑定枚数が少ないカード ランキング（{year}年）",
        sets: "{game} 弾別 時価総額ランキング（{year}年）",
      },
      h1: {
        value: "{game} 時価総額ランキング（{year}年）— PSA 10 時価総額順",
        price: "{game} 高額ランキング（{year}年）— PSA 10 参考価格順",
        gainers: "{window} 値上がりランキング（{year}年）— PSA 10 価格の変動",
        losers: "{window} 値下がりランキング（{year}年）— PSA 10 価格の変動",
        population: "PSA 10 鑑定枚数が多いカード（{year}年）— トレカ時価総額指数",
        rarity: "PSA 10 鑑定枚数が少ないカード（{year}年）— 価格のある最少鑑定枚数",
        sets: "{game} 弾別 時価総額ランキング（{year}年）— PSA 10 指数",
      },
      descriptions: {
        value: "CardZ Marketcap が PSA 10 時価総額（PSA 10 価格 × 検証済み鑑定枚数）で並べた{game}ランキング。鑑定枚数と価格変動も表示、基準日 {date}。",
        price: "PSA 10 参考価格で並べた{game}の高額ランキング。各カードに検証済み PSA 10 鑑定枚数と時価総額を併記。CardZ Marketcap、基準日 {date}。",
        gainers: "ポケモンカードとワンピースカードの PSA 10 価格{window}値上がりランキング。各カードに鑑定枚数と時価総額を併記。CardZ Marketcap、基準日 {date}。",
        losers: "ポケモンカードとワンピースカードの PSA 10 価格{window}値下がりランキング。各カードに鑑定枚数と時価総額を併記。CardZ Marketcap、基準日 {date}。",
        population: "CardZ Marketcap の時価総額指数で、検証済み PSA 10 鑑定枚数が最も多いカード。PSA 10 価格と時価総額を併記、基準日 {date}。",
        rarity: "CardZ Marketcap で PSA 10 参考価格があり、検証済み鑑定枚数が最も少ないカード。時価総額も併記、基準日 {date}。",
        sets: "CardZ Marketcap が合計 PSA 10 時価総額で並べた{game}の全弾。カード数と弾内最大カードを併記、基準日 {date}。",
      },
      answers: {
        value: "CardZ Marketcap は{scope}のカードを PSA 10 時価総額 —— 検証済み PSA 10 鑑定枚数 × PSA 10 参考価格 —— で並べています。{date} 時点の首位は {top}（{topValue}）で、本ページの {count} 枚の合計時価総額は {total} です。",
        price: "この CardZ Marketcap のランキングは、{scope}のカードを PSA 10 参考価格（時価総額の価格側、価格 × PSA 10 鑑定枚数）で並べたものです。{date} 時点の最高価格は {top} の {topValue}、本ページは {count} 枚を掲載しています。",
        gainers: "CardZ Marketcap は各カードの PSA 10 参考価格の{window}変動を追跡しています。{date} 時点で{window}の上昇が最も大きいのは {top}（{topValue}）、本ページは {count} 枚を掲載。時価総額はこの価格 × PSA 10 鑑定枚数です。",
        losers: "CardZ Marketcap は各カードの PSA 10 参考価格の{window}変動を追跡しています。{date} 時点で{window}の下落が最も大きいのは {top}（{topValue}）、本ページは {count} 枚を掲載。時価総額はこの価格 × PSA 10 鑑定枚数です。",
        population: "鑑定枚数とは PSA が 10 と判定した枚数です。{date} 時点で CardZ Marketcap 指数の検証済み PSA 10 鑑定枚数が最も多いのは {top} の {topValue} 枚で、本ページは上位 {count} 件を掲載しています。時価総額はこの枚数 × PSA 10 参考価格です。",
        rarity: "これは CardZ Marketcap 指数のうち、価格があり検証済み PSA 10 鑑定枚数が最も少ないカードです。{date} 時点の最少は {top} の {topValue} 枚。枚数が少ないことは希少性であって時価総額の大きさではありません（時価総額 = 鑑定枚数 × PSA 10 価格）。",
        sets: "CardZ Marketcap は{scope}のカードを弾ごとにまとめ、PSA 10 時価総額を合計しています。{date} 時点の最大は {top}（{topValue}）で、本ページの {count} 弾の合計は {total} です。",
      },
    },
    report: {
      kicker: "マーケットレポート",
      h1: "トレカ市場レポート — {month}（基準日 {date}）",
      title: "トレカ市場レポート — {month}",
      description: "鑑定カード時価総額指数の月次サマリー。ポケモンとワンピースの PSA 10 時価総額、集中度、値動きの大きいカードを掲載。基準日 {date}。",
      summary: "CardZ Marketcap はポケモンとワンピースの鑑定済みカード {count} 枚を PSA 10 時価総額（検証済み PSA 10 鑑定枚数 × PSA 10 参考価格）で追跡しています。{date} 時点の指数合計は {total}：ポケモン {pokemon}、ワンピース {onePiece}。",
      statCards: "追跡カード数",
      statTotal: "PSA 10 時価総額 合計",
      statPokemon: "ポケモン時価総額",
      statOnePiece: "ワンピース時価総額",
      statTop10: "上位 10 枚の構成比",
      totalsHeading: "時価総額の合計",
      totalsLine: "CardZ Marketcap によると、{date} 時点で指数に含まれる鑑定済みカード {count} 枚の PSA 10 時価総額は合計 {total}（ポケモン {pokemon}、ワンピース {onePiece}）です。",
      concentrationHeading: "集中度",
      concentrationLine: "{date} 時点で上位 10 枚が指数の {share} を占め、金額は {topTen}（合計 {total}）です。単体最大は {top} の {topValue}。",
      gainersHeading: "値上がり首位",
      gainersLine: "{date} 時点で PSA 10 参考価格の{window}上昇が最も大きいのは {top}（{topValue}）です。",
      losersHeading: "値下がり首位",
      losersLine: "{date} 時点で PSA 10 参考価格の{window}下落が最も大きいのは {top}（{topValue}）です。",
      populationHeading: "鑑定枚数 最多",
      populationLine: "{date} 時点で検証済み PSA 10 鑑定枚数が最も多いのは {top} の {topValue} 枚です。",
      setsHeading: "最大の弾",
      setsLine: "{date} 時点で合計 PSA 10 時価総額が最も大きい弾は {top}（{topValue}）です。",
      note: "本ページは日次の CardZ Marketcap スナップショットから自動生成しています。すべての数値は次回の日次更新で再計算され、変動します。",
    },
  },
  ko: {
    games: { pokemon: "포켓몬 카드", "one-piece": "원피스 카드" },
    scopeAll: "CardZ Marketcap 지수",
    windows: { "7d": "7일", "30d": "30일" },
    table: {
      rank: "#",
      card: "카드",
      set: "세트",
      number: "번호",
      price: "PSA 10 가격",
      population: "PSA 10 개체수",
      marketCap: "시가총액",
      change7d: "7일 가격",
      change30d: "30일 가격",
      cards: "카드 수",
      combinedCap: "합계 시가총액",
      topCard: "최대 카드",
      share: "비중",
    },
    common: {
      home: "마켓",
      breadcrumb: "탐색 경로",
      method: "산출 방식: 시가총액 = PSA 10 기준가 × 검증된 PSA 10 개체수, 매일 업데이트마다 재계산합니다. 편입 조건은 검증된 PSA 10 개체수 1,000장 이상입니다.",
      methodLink: "산출 방식",
      updated: "데이터 기준 {date}. 매일 업데이트마다 수치가 바뀝니다.",
      disambiguation: "CardZ Marketcap은 등급 카드 데이터 지수입니다. 암호화폐나 토큰, 상장사가 아니며 CardZ 티커도 없습니다.",
      changeNote: "퍼센트 열은 해당 기간 PSA 10 기준가의 변동이며, 개체수를 다시 계산한 값이 아닙니다.",
      empty: "현재 스냅샷에는 이 목록에 공개 수치를 가진 카드가 없습니다.",
    },
    set: {
      kicker: "세트 시가총액",
      h1: "{set} — 카드 시가총액 랭킹 (PSA 10)",
      title: "{set} 시가총액 랭킹 (PSA 10)",
      description: "CardZ Marketcap이 수록한 {set} PSA 10 카드 {count}장의 시가총액 랭킹. PSA 10 가격 × 개체수, 합계 {total}, 기준 {date}.",
      summary: "CardZ Marketcap은 {set}의 PSA 10 카드 {count}장을 수록하며, {date} 기준 합계 시가총액은 {total}입니다. 시가총액은 검증된 PSA 10 개체수 × PSA 10 기준가입니다. 세트 내 최대는 {top}으로 {topValue}입니다.",
      tableCaption: "{set} PSA 10 시가총액 랭킹 ({date} 기준)",
      siblings: "다른 세트 시가총액 랭킹",
      back: "{game} 전체 랭킹",
      setsIndex: "세트별 시가총액 랭킹",
    },
    rankings: {
      kicker: "랭킹",
      indexH1: "트레이딩 카드 시가총액 랭킹 ({year})",
      indexTitle: "트레이딩 카드 시가총액 랭킹 ({year})",
      indexDescription: "포켓몬 카드와 원피스 카드의 PSA 10 시가총액·가격·개체수·등락 랭킹. CardZ Marketcap 데이터, {date} 기준.",
      indexSummary: "CardZ Marketcap은 동일한 일간 스냅샷에서 등급 카드 {count}장을 바탕으로 {lists}개의 랭킹을 공개합니다. 시가총액은 검증된 PSA 10 개체수 × PSA 10 기준가이며, 이 페이지의 모든 랭킹은 {date} 기준입니다.",
      listHeading: "전체 랭킹",
      siblings: "다른 랭킹",
      tableCaption: "랭킹 ({date} 기준)",
      titles: {
        value: "{game} 시가총액 랭킹 ({year})",
        price: "가장 비싼 {game} PSA 10 가격 랭킹 ({year})",
        gainers: "{window} 상승률 랭킹 — PSA 10 카드 가격 ({year})",
        losers: "{window} 하락률 랭킹 — PSA 10 카드 가격 ({year})",
        population: "PSA 10 개체수 최다 카드 랭킹",
        rarity: "PSA 10 개체수 최소 카드 랭킹 ({year})",
        sets: "{game} 세트별 시가총액 랭킹 ({year})",
      },
      h1: {
        value: "{game} 시가총액 랭킹 ({year}) — PSA 10 시가총액 순",
        price: "가장 비싼 {game} ({year}) — PSA 10 기준가 순",
        gainers: "{window} 상승률 랭킹 ({year}) — PSA 10 카드 가격 변동",
        losers: "{window} 하락률 랭킹 ({year}) — PSA 10 카드 가격 변동",
        population: "PSA 10 개체수가 가장 많은 카드 ({year}) — 등급 카드 시가총액 지수",
        rarity: "PSA 10 개체수가 가장 적은 카드 ({year}) — 가격이 있는 최소 개체수",
        sets: "{game} 세트별 시가총액 랭킹 ({year}) — PSA 10 지수",
      },
      descriptions: {
        value: "CardZ Marketcap이 PSA 10 시가총액(PSA 10 가격 × 검증된 개체수)으로 정렬한 {game} 랭킹. 개체수와 가격 변동도 함께 표시, {date} 기준.",
        price: "PSA 10 기준가로 정렬한 가장 비싼 {game} 랭킹. 각 카드에 검증된 PSA 10 개체수와 시가총액을 함께 표시. CardZ Marketcap, {date} 기준.",
        gainers: "포켓몬 카드와 원피스 카드의 PSA 10 가격 {window} 상승률 랭킹. 카드마다 개체수와 시가총액을 함께 표시. CardZ Marketcap, {date} 기준.",
        losers: "포켓몬 카드와 원피스 카드의 PSA 10 가격 {window} 하락률 랭킹. 카드마다 개체수와 시가총액을 함께 표시. CardZ Marketcap, {date} 기준.",
        population: "CardZ Marketcap 등급 카드 시가총액 지수에서 검증된 PSA 10 개체수가 가장 많은 카드. PSA 10 가격과 시가총액 포함, {date} 기준.",
        rarity: "CardZ Marketcap에서 PSA 10 기준가가 있고 검증된 개체수가 가장 적은 카드. 시가총액 포함, {date} 기준.",
        sets: "CardZ Marketcap이 합계 PSA 10 시가총액으로 정렬한 {game}의 모든 세트. 카드 수와 세트 내 최대 카드 포함, {date} 기준.",
      },
      answers: {
        value: "CardZ Marketcap은 {scope}의 카드를 PSA 10 시가총액 — 검증된 PSA 10 개체수 × PSA 10 기준가 — 으로 정렬합니다. {date} 기준 1위는 {top}({topValue})이며, 이 페이지의 {count}장 합계 시가총액은 {total}입니다.",
        price: "이 CardZ Marketcap 목록은 {scope}의 카드를 PSA 10 기준가, 즉 시가총액 공식(가격 × PSA 10 개체수)의 가격 쪽으로 정렬합니다. {date} 기준 최고가는 {top}({topValue})이고 이 페이지에는 {count}장이 실려 있습니다.",
        gainers: "CardZ Marketcap은 카드별 PSA 10 기준가의 {window} 변동을 추적합니다. {date} 기준 {window} 상승폭이 가장 큰 카드는 {top}({topValue})이며 이 페이지에는 {count}장이 실려 있습니다. 시가총액은 이 가격 × PSA 10 개체수입니다.",
        losers: "CardZ Marketcap은 카드별 PSA 10 기준가의 {window} 변동을 추적합니다. {date} 기준 {window} 하락폭이 가장 큰 카드는 {top}({topValue})이며 이 페이지에는 {count}장이 실려 있습니다. 시가총액은 이 가격 × PSA 10 개체수입니다.",
        population: "개체수는 PSA가 10등급을 준 장수입니다. {date} 기준 CardZ Marketcap 지수에서 검증된 PSA 10 개체수가 가장 많은 카드는 {top}으로 {topValue}장이며, 이 페이지는 상위 {count}장을 보여줍니다. 시가총액은 이 개체수 × PSA 10 기준가입니다.",
        rarity: "CardZ Marketcap 지수에서 가격이 있고 검증된 PSA 10 개체수가 가장 적은 카드들입니다. {date} 기준 최소는 {top}으로 {topValue}장입니다. 개체수가 적다는 것은 희소성이지 큰 시가총액이 아닙니다: 시가총액 = 개체수 × PSA 10 가격.",
        sets: "CardZ Marketcap은 {scope}의 카드를 세트별로 묶어 PSA 10 시가총액을 합산합니다. {date} 기준 최대 세트는 {top}({topValue})이며, 이 페이지의 {count}개 세트 합계는 {total}입니다.",
      },
    },
    report: {
      kicker: "마켓 리포트",
      h1: "트레이딩 카드 마켓 리포트 — {month} ({date} 기준)",
      title: "트레이딩 카드 마켓 리포트 — {month}",
      description: "등급 카드 시가총액 지수 월간 요약: 포켓몬과 원피스의 PSA 10 시가총액 합계, 집중도, 가격 변동 상위 카드. {date} 기준.",
      summary: "CardZ Marketcap은 포켓몬과 원피스 등급 카드 {count}장을 PSA 10 시가총액(검증된 PSA 10 개체수 × PSA 10 기준가)으로 추적합니다. {date} 기준 지수 합계는 {total}로, 포켓몬 {pokemon}, 원피스 {onePiece}입니다.",
      statCards: "추적 카드 수",
      statTotal: "PSA 10 시가총액 합계",
      statPokemon: "포켓몬 시가총액",
      statOnePiece: "원피스 시가총액",
      statTop10: "상위 10장 비중",
      totalsHeading: "시가총액 합계",
      totalsLine: "CardZ Marketcap에 따르면 {date} 기준 지수에 포함된 등급 카드 {count}장의 PSA 10 시가총액 합계는 {total}이며, 포켓몬 {pokemon}, 원피스 {onePiece}입니다.",
      concentrationHeading: "집중도",
      concentrationLine: "{date} 기준 상위 10장이 지수의 {share}를 차지하며 금액은 {topTen}입니다(합계 {total}). 단일 최대는 {top}({topValue})입니다.",
      gainersHeading: "최대 상승",
      gainersLine: "{date} 기준 PSA 10 기준가 {window} 상승폭이 가장 큰 카드는 {top}({topValue})입니다.",
      losersHeading: "최대 하락",
      losersLine: "{date} 기준 PSA 10 기준가 {window} 하락폭이 가장 큰 카드는 {top}({topValue})입니다.",
      populationHeading: "개체수 최다",
      populationLine: "{date} 기준 검증된 PSA 10 개체수가 가장 많은 카드는 {top}으로 {topValue}장입니다.",
      setsHeading: "최대 세트",
      setsLine: "{date} 기준 합계 PSA 10 시가총액이 가장 큰 세트는 {top}({topValue})입니다.",
      note: "이 페이지는 일간 CardZ Marketcap 스냅샷에서 자동 생성됩니다. 모든 수치는 다음 일간 업데이트에서 재계산되어 바뀝니다.",
    },
  },
};

/*
 * i18n.ts 嘅 `pageTitles` / `pageDescriptions` 由另一位 owner 平行加緊（2026-08-16）。
 * 呢度用 runtime 讀 + fallback，唔用 type 直讀：佢未落地嗰陣照樣行得，
 * 落地之後 hub index 自動跟佢嘅字，唔使再改呢個檔。
 */
export function i18nPageString(
  bag: unknown,
  group: "pageTitles" | "pageDescriptions",
  key: string,
): string | null {
  const source = (bag as Record<string, unknown> | null | undefined)?.[group];
  if (!source || typeof source !== "object") return null;
  const value = (source as Record<string, unknown>)[key];
  return typeof value === "string" && value.trim() ? value : null;
}
