import type { Grader, Locale, MarketWindow } from "./types";

export interface Copy {
  nav: {
    all: string;
    pokemon: string;
    onePiece: string;
    graders: string;
    watchlist: string;
  };
  hero: { eyebrow: string; title: string; body: string };
  pokemonHero: { eyebrow: string; title: string; body: string };
  onePieceHero: { eyebrow: string; title: string; body: string };
  watchlistHero: { eyebrow: string; title: string; body: string };
  heatmap: {
    title: string;
    rankingTitle: string;
    pokemonTitle: string;
    onePieceTitle: string;
    body: string;
    negative: string;
    neutral: string;
    positive: string;
    count: string;
    viewRanking: string;
  };
  periods: Record<MarketWindow, string>;
  labels: {
    rank: string;
    card: string;
    number: string;
    language: string;
    price: string;
    population: string;
    marketCap: string;
    trackedSales: string;
    salesHelp: string;
    change: string;
    asOf: string;
    viewCard: string;
    close: string;
    story: string;
    history: string;
    dailyPrice: string;
    trackedSalesBars: string;
    imageAlt: string;
    noHistory: string;
    noCards: string;
    watchStatus: string;
  };
  grader: {
    eyebrow: string;
    title: string;
    body: string;
    topGrade: string;
    topGradePopulation: string;
    totalPopulation: string;
    populationChange: string;
    liquidity: string;
    marketCapAvailable: string;
    marketCapUnavailable: string;
    names: Record<Grader, string>;
  };
  status: Record<"accumulating" | "stale" | "unavailable", string>;
  previewNotice: string;
  footer: string;
}

export const copy: Record<Locale, Copy> = {
  en: {
    nav: { all: "TCG Market", pokemon: "Pokémon", onePiece: "One Piece", graders: "Graders", watchlist: "Watchlist" },
    hero: {
      eyebrow: "CARDZ MARKET INDEX",
      title: "The market view for collectible cards",
      body: "Art comes first. Verified identity, tradable supply and current pricing make the market easier to read.",
    },
    pokemonHero: {
      eyebrow: "POKÉMON MARKET",
      title: "Pokémon cards as a living market",
      body: "Verified printings ranked through current PSA 10 supply and pricing.",
    },
    onePieceHero: {
      eyebrow: "ONE PIECE MARKET",
      title: "One Piece cards as a living market",
      body: "Verified printings ranked through current PSA 10 supply and pricing.",
    },
    watchlistHero: {
      eyebrow: "MARKET WATCH",
      title: "Cards approaching the leading market",
      body: "Ranks 101 to 500, monitored for identity, price freshness, supply and demand.",
    },
    heatmap: {
      title: "Top 100 market heatmap",
      rankingTitle: "Top 100 by market cap",
      pokemonTitle: "Pokémon market heatmap",
      onePieceTitle: "One Piece market heatmap",
      body: "Area represents current PSA 10 market cap. Colour follows the selected price window.",
      negative: "Down",
      neutral: "Data pending",
      positive: "Up",
      count: "eligible cards",
      viewRanking: "View Top 100",
    },
    periods: { "1d": "1d", "7d": "7d", "30d": "30d" },
    labels: {
      rank: "Rank", card: "Card", number: "Full number", language: "Language", price: "PSA 10 price",
      population: "PSA 10 population", marketCap: "Market cap", trackedSales: "Tracked sales",
      salesHelp: "Only completed PSA 10 sales captured within CARDZ tracked coverage.", change: "Change", asOf: "Data time",
      viewCard: "Open card profile", close: "Close", story: "Why the market cares", history: "Daily market history",
      dailyPrice: "Reference price", trackedSalesBars: "Tracked sales", imageAlt: "Card artwork",
      noHistory: "Daily price history is still accumulating.", noCards: "No eligible cards are available in this view.", watchStatus: "Eligibility watch",
    },
    grader: {
      eyebrow: "GRADING SUPPLY",
      title: "{grader} market supply",
      body: "Top-grade population and tracked liquidity are shown without inventing unavailable prices.",
      topGrade: "Top grade", topGradePopulation: "Top-grade population", totalPopulation: "Total graded population", populationChange: "population change",
      liquidity: "Tracked liquidity", marketCapAvailable: "PSA 10 market cap", marketCapUnavailable: "Market cap unavailable",
      names: { PSA: "PSA", BGS: "BGS", CGC: "CGC", SGC: "SGC" },
    },
    status: { accumulating: "Accumulating", stale: "Stale", unavailable: "Not available" },
    previewNotice: "Preview data is shown locally. Publication requires a validated market snapshot.",
    footer: "CARDZ Market Cap. Art market intelligence for collectible cards.",
  },
  "zh-TW": {
    nav: { all: "TCG 市場", pokemon: "寶可夢", onePiece: "海賊王", graders: "評級公司", watchlist: "觀察名單" },
    hero: {
      eyebrow: "CARDZ 市場指數",
      title: "收藏卡牌的市場全景",
      body: "以藝術價值為起點，透過經核實的身份、可流通供應及現時價格理解市場。",
    },
    pokemonHero: {
      eyebrow: "寶可夢市場",
      title: "以流動市場視角理解寶可夢卡牌",
      body: "按已核實印刷版本、PSA 10 供應及現時價格排列。",
    },
    onePieceHero: {
      eyebrow: "海賊王市場",
      title: "以流動市場視角理解海賊王卡牌",
      body: "按已核實印刷版本、PSA 10 供應及現時價格排列。",
    },
    watchlistHero: {
      eyebrow: "市場觀察",
      title: "正在接近領先市場的卡牌",
      body: "持續觀察第 101 至 500 位卡牌的身份、價格時效、供應及需求。",
    },
    heatmap: {
      title: "市值前 100 熱力圖", rankingTitle: "市值前 100 排行", pokemonTitle: "寶可夢市場熱力圖", onePieceTitle: "海賊王市場熱力圖",
      body: "面積代表現時 PSA 10 市值，色彩反映所選期間的價格變化。",
      negative: "下跌", neutral: "資料累積中", positive: "上升", count: "張合資格卡牌", viewRanking: "查看前 100",
    },
    periods: { "1d": "1 日", "7d": "7 日", "30d": "30 日" },
    labels: {
      rank: "排名", card: "卡牌", number: "完整編號", language: "語言", price: "PSA 10 價格",
      population: "PSA 10 數量", marketCap: "市值", trackedSales: "已追蹤成交額",
      salesHelp: "只包括 CARDZ 追蹤範圍內捕捉到的 PSA 10 完成成交。", change: "升跌", asOf: "資料時間",
      viewCard: "查看卡牌詳情", close: "關閉", story: "市場為何追捧", history: "每日市場走勢",
      dailyPrice: "參考價格", trackedSalesBars: "已追蹤成交額", imageAlt: "卡牌圖像",
      noHistory: "每日價格歷史仍在累積。", noCards: "此分類暫時沒有合資格卡牌。", watchStatus: "入榜觀察",
    },
    grader: {
      eyebrow: "評級供應",
      title: "{grader} 市場供應",
      body: "呈現最高評級數量及已追蹤流動性，不會為缺失價格製造估算。",
      topGrade: "最高評級", topGradePopulation: "最高評級數量", totalPopulation: "評級總數量", populationChange: "數量變化",
      liquidity: "已追蹤流動性", marketCapAvailable: "PSA 10 市值", marketCapUnavailable: "暫無市值",
      names: { PSA: "PSA", BGS: "BGS", CGC: "CGC", SGC: "SGC" },
    },
    status: { accumulating: "資料累積中", stale: "資料已逾時", unavailable: "暫無資料" },
    previewNotice: "目前顯示本機預覽資料，正式發布前必須通過市場快照驗證。",
    footer: "CARDZ Market Cap，收藏卡牌藝術市場情報。",
  },
  "zh-CN": {
    nav: { all: "TCG 市场", pokemon: "宝可梦", onePiece: "海贼王", graders: "评级公司", watchlist: "观察名单" },
    hero: {
      eyebrow: "CARDZ 市场指数", title: "收藏卡牌的市场全景",
      body: "以艺术价值为起点，通过经核实的身份、可流通供应及当前价格理解市场。",
    },
    pokemonHero: {
      eyebrow: "宝可梦市场", title: "以流动市场视角理解宝可梦卡牌", body: "按已核实印刷版本、PSA 10 供应及当前价格排列。",
    },
    onePieceHero: {
      eyebrow: "海贼王市场", title: "以流动市场视角理解海贼王卡牌", body: "按已核实印刷版本、PSA 10 供应及当前价格排列。",
    },
    watchlistHero: {
      eyebrow: "市场观察", title: "正在接近领先市场的卡牌", body: "持续观察第 101 至 500 位卡牌的身份、价格时效、供应及需求。",
    },
    heatmap: {
      title: "市值前 100 热力图", rankingTitle: "市值前 100 排行", pokemonTitle: "宝可梦市场热力图", onePieceTitle: "海贼王市场热力图",
      body: "面积代表当前 PSA 10 市值，色彩反映所选期间的价格变化。",
      negative: "下跌", neutral: "数据累积中", positive: "上涨", count: "张合资格卡牌", viewRanking: "查看前 100",
    },
    periods: { "1d": "1 日", "7d": "7 日", "30d": "30 日" },
    labels: {
      rank: "排名", card: "卡牌", number: "完整编号", language: "语言", price: "PSA 10 价格",
      population: "PSA 10 数量", marketCap: "市值", trackedSales: "已追踪成交额",
      salesHelp: "只包括 CARDZ 追踪范围内捕捉到的 PSA 10 完成成交。", change: "涨跌", asOf: "数据时间",
      viewCard: "查看卡牌详情", close: "关闭", story: "市场为何追捧", history: "每日市场走势",
      dailyPrice: "参考价格", trackedSalesBars: "已追踪成交额", imageAlt: "卡牌图像",
      noHistory: "每日价格历史仍在累积。", noCards: "此分类暂时没有合资格卡牌。", watchStatus: "入榜观察",
    },
    grader: {
      eyebrow: "评级供应", title: "{grader} 市场供应", body: "呈现最高评级数量及已追踪流动性，不会为缺失价格制造估算。",
      topGrade: "最高评级", topGradePopulation: "最高评级数量", totalPopulation: "评级总数量", populationChange: "数量变化",
      liquidity: "已追踪流动性", marketCapAvailable: "PSA 10 市值", marketCapUnavailable: "暂无市值",
      names: { PSA: "PSA", BGS: "BGS", CGC: "CGC", SGC: "SGC" },
    },
    status: { accumulating: "数据累积中", stale: "数据已过期", unavailable: "暂无数据" },
    previewNotice: "当前显示本地预览数据，正式发布前必须通过市场快照验证。",
    footer: "CARDZ Market Cap，收藏卡牌艺术市场情报。",
  },
  ja: {
    nav: { all: "TCG 市場", pokemon: "ポケモン", onePiece: "ワンピース", graders: "鑑定会社", watchlist: "ウォッチリスト" },
    hero: {
      eyebrow: "CARDZ マーケット指数", title: "コレクティブルカード市場を一望する",
      body: "アートの価値を起点に、確認済みのカード情報、流通供給、現在価格から市場を読み解きます。",
    },
    pokemonHero: {
      eyebrow: "ポケモン市場", title: "動く市場として見るポケモンカード", body: "確認済みの印刷版、PSA 10 供給、現在価格で順位付けします。",
    },
    onePieceHero: {
      eyebrow: "ワンピース市場", title: "動く市場として見るワンピースカード", body: "確認済みの印刷版、PSA 10 供給、現在価格で順位付けします。",
    },
    watchlistHero: {
      eyebrow: "マーケットウォッチ", title: "主要市場に近づくカード", body: "101 位から 500 位までの識別情報、価格鮮度、供給、需要を観察します。",
    },
    heatmap: {
      title: "時価総額トップ 100 ヒートマップ", rankingTitle: "時価総額トップ 100", pokemonTitle: "ポケモン市場ヒートマップ", onePieceTitle: "ワンピース市場ヒートマップ",
      body: "面積は現在の PSA 10 時価総額、色は選択期間の価格変化を表します。",
      negative: "下落", neutral: "集計中", positive: "上昇", count: "枚の適格カード", viewRanking: "トップ 100 を見る",
    },
    periods: { "1d": "1 日", "7d": "7 日", "30d": "30 日" },
    labels: {
      rank: "順位", card: "カード", number: "完全な番号", language: "言語", price: "PSA 10 価格",
      population: "PSA 10 枚数", marketCap: "時価総額", trackedSales: "追跡成約額",
      salesHelp: "CARDZ の追跡範囲で確認できた PSA 10 の成約のみを含みます。", change: "変動", asOf: "データ時刻",
      viewCard: "カード詳細を見る", close: "閉じる", story: "市場で支持される理由", history: "日次市場推移",
      dailyPrice: "参考価格", trackedSalesBars: "追跡成約額", imageAlt: "カード画像",
      noHistory: "日次価格履歴を蓄積しています。", noCards: "この表示には適格カードがありません。", watchStatus: "適格性を観察中",
    },
    grader: {
      eyebrow: "鑑定供給", title: "{grader} の市場供給", body: "最高評価枚数と追跡流動性を表示し、欠損価格は推計しません。",
      topGrade: "最高評価", topGradePopulation: "最高評価枚数", totalPopulation: "鑑定総数", populationChange: "枚数変化",
      liquidity: "追跡流動性", marketCapAvailable: "PSA 10 時価総額", marketCapUnavailable: "時価総額データなし",
      names: { PSA: "PSA", BGS: "BGS", CGC: "CGC", SGC: "SGC" },
    },
    status: { accumulating: "集計中", stale: "更新待ち", unavailable: "データなし" },
    previewNotice: "現在はローカルプレビューデータです。公開には市場スナップショットの検証が必要です。",
    footer: "CARDZ Market Cap。コレクティブルカードのアート市場情報。",
  },
};
