import type { Locale, MarketWindow } from "./types";

// The card's printing language, not the reader's locale. These are the codes emitted by the
// canonical ingest language contract. An unrecognised code falls back to the supplied display
// value in the same way `names` and `sets` keep their English text when no translation exists.
export const cardLanguages = ["en", "ja", "ko", "zhCN", "zhTW"] as const;
export type CardLanguage = (typeof cardLanguages)[number];

export interface Copy {
  nav: {
    all: string;
    pokemon: string;
    onePiece: string;
    watchlist: string;
    box: string;
  };
  boxHero: { eyebrow: string; title: string; body: string };
  box: {
    groupAll: string;
    groups: Record<"optcg-en" | "optcg-jp" | "ptcg-en" | "ptcg-jp", string>;
    boardTitle: string;
    box: string;
    release: string;
    packs: string;
    packsShort: string;
    soldCountShort: string;
    priceKind: Record<"sold" | "market" | "ask", string>;
    askFloor: string;
    unreleased: string;
    setCode: string;
    fullName: string;
    print: string;
    coverage: string;
    empty: string;
  };
  boxProvenance: {
    kicker: string;
    title: string;
    body: string;
    steps: { term: string; detail: string }[];
    updated: string;
    byline: string;
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
    tilesLabel: string;
    viewRanking: string;
    shareImage: string;
    customize: string;
    customizeTitle: string;
    resetDefault: string;
    upColor: string;
    downColor: string;
    intensity: string;
    neutralZone: string;
    gap: string;
    cardSize: string;
    saved: string;
  };
  periods: Record<MarketWindow, string>;
  languages: Record<CardLanguage, string>;
  labels: {
    rank: string;
    card: string;
    number: string;
    language: string;
    price: string;
    ungradedReference: string;
    priceShort: string;
    population: string;
    populationShort: string;
    marketCap: string;
    marketCapShort: string;
    trackedSales: string;
    trackedSalesShort: string;
    salesHelp: string;
    change: string;
    changeShort: string;
    asOf: string;
    pricePeriod: string;
    checkedAt: string;
    awaitingFreshPrice: string;
    viewCard: string;
    close: string;
    story: string;
    history: string;
    dailyPrice: string;
    trackedSalesBars: string;
    salesTrend: string;
    salesTrendShort: string;
    imageAlt: string;
    noHistory: string;
    noCards: string;
    noSales: string;
    watchStatus: string;
    share: string;
    shareDone: string;
    shareError: string;
    expandImage: string;
    marketCapHelp: string;
    populationHelp: string;
    priceHelp: string;
    /*
     * 印刷版本相關。`printLanguage` 係 template：`languages` 只出裸字（「日文」），
     * 但 badge 要出「日文版」，所以用 {language} 佔位符夾 localizedCardLanguage() 嘅輸出。
     * 其餘幾條係 printing identity 各欄嘅標籤。
     */
    printLanguage: string;
    setCode: string;
    rarity: string;
    parallel: string;
    finish: string;
    packSource: string;
    languageFilterAll: string;
  };
  theme: { dark: string; light: string };
  methodology: { title: string; body: string };
  /*
   * 出街頁面淨係講「點計」，唔講由邊度攞數 —— 供應商代號唔准曝光。
   * ⚠️ 呢條線目前冇自動 gate（canary-public.mjs 已刪），全靠人手守。
   */
  provenance: {
    kicker: string;
    title: string;
    body: string;
    steps: { term: string; detail: string }[];
    updated: string;
    byline: string;
    anchorSwitched: string;
  };
  status: Record<"accumulating" | "stale" | "unavailable", string>;
  footer: string;
}

export const copy: Record<Locale, Copy> = {
  en: {
    nav: { all: "TCG Market", pokemon: "Pokémon", onePiece: "One Piece", watchlist: "Watchlist", box: "BOX" },
    boxHero: {
      eyebrow: "BOX MARKET",
      title: "Sold-first prices for sealed booster boxes",
      body: "Completed sales set the reference price; asking prices never stand in for them.",
    },
    box: {
      groupAll: "All",
      groups: { "optcg-en": "One Piece EN", "optcg-jp": "One Piece JP", "ptcg-en": "Pokémon EN", "ptcg-jp": "Pokémon JP" },
      boardTitle: "BOX ({count})",
      box: "Box",
      release: "Release",
      packs: "Packs per box",
      packsShort: "Packs",
      soldCountShort: "Sold",
      priceKind: { sold: "Last sold", market: "Market price", ask: "Ask floor" },
      askFloor: "Ask floor",
      unreleased: "Unreleased",
      setCode: "Set code",
      fullName: "Full name",
      print: "Print",
      coverage: "{priced} of {total} priced",
      empty: "BOX data is being prepared.",
    },
    boxProvenance: {
      kicker: "METHOD & DATA",
      title: "How the BOX reference price is built",
      body: "Each BOX rank uses a reference price. The method prefers completed sales. A market reference is used when sold evidence is thin. An ask floor is only a fallback, never treated as a completed sale.",
      steps: [
        { term: "Sold", detail: "Completed box sales captured inside CardZ Marketcap tracked coverage. Lots are unitised to one box, extreme outliers are dropped, and what remains is reduced to a median." },
        { term: "Market reference", detail: "A published market reference for that exact set, language and print wave when sold coverage is insufficient." },
        { term: "Ask floor", detail: "The current ask floor is shown only as a secondary reference. It does not replace a sold or market figure. A missing value stays missing, never zero." },
      ],
      updated: "Updated",
      byline: "Compiled and reviewed by the CardZ Marketcap Editorial desk.",
    },
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
      title: "Ranks 101 and beyond, under watch",
      body: "Cards just outside the top 100, tracked for price freshness, supply and demand.",
    },
    heatmap: {
      title: "Top {count} market heatmap",
      rankingTitle: "Top {count} by market cap",
      pokemonTitle: "Pokémon market heatmap",
      onePieceTitle: "One Piece market heatmap",
      body: "Area represents current PSA 10 market cap. Colour follows the selected price window.",
      negative: "Down",
      neutral: "Data pending",
      positive: "Up",
      count: "eligible cards",
      tilesLabel: "Tiles",
      viewRanking: "View Top {count}",
      shareImage: "Share image",
      customize: "Customize colours",
      customizeTitle: "Heatmap colours",
      resetDefault: "Reset to default",
      upColor: "Up colour",
      downColor: "Down colour",
      intensity: "Colour intensity",
      neutralZone: "Neutral zone",
      gap: "Tile spacing",
      cardSize: "Card size",
      saved: "Saved",
    },
    periods: { "1d": "1d", "7d": "7d", "30d": "30d" },
    languages: { en: "English", ja: "Japanese", ko: "Korean", zhCN: "Simplified Chinese", zhTW: "Traditional Chinese" },
    labels: {
      rank: "Rank", card: "Card", number: "Full number", language: "Language", price: "PSA 10 price", ungradedReference: "Ungraded / RAW reference",
      priceShort: "Price",
      population: "PSA 10 population", populationShort: "Pop",
      marketCap: "Market cap", marketCapShort: "Mkt Cap", trackedSales: "Tracked sales",
      trackedSalesShort: "Sales",
      salesHelp: "Only completed PSA 10 sales captured within CardZ Marketcap tracked coverage.", change: "Change", changeShort: "Chg", asOf: "Data time",
      pricePeriod: "Price period", checkedAt: "Last checked",
      awaitingFreshPrice: "Awaiting fresh price",
      viewCard: "Open card profile", close: "Close", story: "Why the market cares", history: "Daily market history",
      dailyPrice: "Reference price", trackedSalesBars: "Tracked sales", salesTrend: "Tracked sales trend", salesTrendShort: "Sales trend", imageAlt: "Card artwork",
      noHistory: "Daily price history is still accumulating.", noCards: "No eligible cards are available in this view.", noSales: "No sales recorded", watchStatus: "Watchlist status",
      share: "Share card", shareDone: "Link copied", shareError: "Copy failed — select the address bar",
      expandImage: "View full-size card",
      marketCapHelp: "PSA 10 price × PSA 10 population — the tradable value of the top-grade supply.",
      populationHelp: "Verified PSA 10 graded copies counted in the registry.",
      priceHelp: "Latest verified PSA 10 sale price in the tracked window.",
      printLanguage: "{language} print", setCode: "Set code", rarity: "Rarity", parallel: "Parallel", finish: "Surface", packSource: "Pack source",
      languageFilterAll: "All languages",
    },
    theme: { dark: "Dark mode", light: "Light mode" },
    methodology: {
      title: "How CardZ Marketcap ranks the market",
      /*
       * 2026-08-11：呢段本身寫住「每個 rolling 30 日窗口不少於五宗經核實成交」。
       * 對住當日出街嗰份 snapshot 實測：1,322 張入面有 115 張喺 30 日窗口錄得少過
       * 五宗（其中 33 張根本冇成交數）而價格狀態仍然係 ready，Top 100 入面就有 8
       * 張。即係話嗰個門檻唔存在，而且狀態亦冇標示出嚟。
       *
       * Owner 決定（同日）：唔講死數字，講機制 —— 數字一改，文案就會再一次變假；
       * 講機制就唔會。所以呢度亦唔准寫「不足就會標示」，因為實測就係唔會標示。
       */
      body: "A place in this index is earned, never assumed. Every card carries a verified population of at least 1,000 PSA 10 examples, and its market cap is that population multiplied by a PSA 10 reference price. The reference price is rebuilt from verified PSA 10 sales captured inside our tracked coverage; where a window records too few of them, the figure stands as a reference level rather than a traded average. Real supply, real demand, and nothing invented.",
    },
    provenance: {
      kicker: "METHOD & DATA",
      title: "How the market cap number is built",
      body: "Market cap is the current PSA 10 reference price multiplied by the verified PSA 10 population, recalculated on every daily update.",
      steps: [
        { term: "Reference price", detail: "Completed PSA 10 sales captured inside CardZ Marketcap tracked coverage. Lots are unitised down to a single card, extreme outliers are dropped, and what remains is reduced to a median." },
        { term: "Population", detail: "The verified PSA 10 population for that exact printing — language, set, collector number and parallel are never merged across printings." },
        { term: "Gaps", detail: "A card with insufficient data coverage in the window is marked as accumulating rather than being given a filled-in number. A missing value stays missing, never zero." },
      ],
      updated: "Updated",
      byline: "Compiled and reviewed by the CardZ Marketcap Editorial desk.",
      anchorSwitched: "Historical anchor: earlier reference series",
    },
    status: { accumulating: "Accumulating", stale: "Stale", unavailable: "Not available" },
    footer: "CardZ Marketcap. Art market intelligence for collectible cards.",
  },
  "zh-TW": {
    nav: { all: "TCG 市場", pokemon: "寶可夢", onePiece: "海賊王", watchlist: "觀察名單", box: "原盒" },
    boxHero: {
      eyebrow: "BOX MARKET",
      title: "未開封原盒 · 成交價優先",
      body: "原盒參考價以實際成交為準，掛牌價只作參考。",
    },
    box: {
      groupAll: "全部",
      groups: { "optcg-en": "海賊王 英文", "optcg-jp": "海賊王 日文", "ptcg-en": "寶可夢 英文", "ptcg-jp": "寶可夢 日文" },
      boardTitle: "原盒排行（{count}）",
      box: "原盒",
      release: "發售",
      packs: "每盒包數",
      packsShort: "包數",
      soldCountShort: "成交",
      priceKind: { sold: "最近成交", market: "市場價", ask: "最低掛牌" },
      askFloor: "最低掛牌",
      unreleased: "未發售",
      setCode: "系列編號",
      fullName: "全名",
      print: "印刷版",
      coverage: "{total} 盒中 {priced} 盒有價",
      empty: "原盒市場數據準備中。",
    },
    boxProvenance: {
      kicker: "METHOD & DATA",
      title: "原盒參考價的計算方法",
      body: "每個原盒排名用參考價。方法以已完成成交為先；成交證據不足時才用市場參考；掛牌底價只作後備，永不當作成交。",
      steps: [
        { term: "成交", detail: "取自 CardZ Marketcap 追蹤範圍內已完成的原盒成交：先還原單盒單價，剔除極端值，再取中位數。" },
        { term: "市場參考", detail: "成交覆蓋不足時，用該系列、語言與印刷版的市場參考價。" },
        { term: "掛牌底價", detail: "現時最低掛牌只作次要參考，不會取代成交或市場參考。缺失的數值永遠保持缺失，不會當作零。" },
      ],
      updated: "更新",
      byline: "Compiled and reviewed by the CardZ Marketcap Editorial desk.",
    },
    hero: {
      eyebrow: "CARDZ MARKET INDEX",
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
  title: "第 101 位起 · 持續觀察",
      body: "緊貼前百名之外嘅卡牌，追蹤價格時效、供應同需求。",
    },
    heatmap: {
      title: "市值前 {count} 熱力圖", rankingTitle: "市值前 {count} 排行", pokemonTitle: "寶可夢市場熱力圖", onePieceTitle: "海賊王市場熱力圖",
      body: "面積代表現時 PSA 10 市值，色彩反映所選期間的價格變化。",
      negative: "下跌", neutral: "資料累積中", positive: "上升", count: "張合資格卡牌", tilesLabel: "顯示格數", viewRanking: "查看前 {count}", shareImage: "分享圖片",
      customize: "自訂色彩", customizeTitle: "熱力圖色彩", resetDefault: "恢復預設",
      upColor: "上升顏色", downColor: "下跌顏色", intensity: "色彩強度", neutralZone: "中立區", gap: "格子間距", cardSize: "卡牌大小", saved: "已儲存",
    },
    periods: { "1d": "1 日", "7d": "7 日", "30d": "30 日" },
    languages: { en: "英文", ja: "日文", ko: "韓文", zhCN: "簡體中文", zhTW: "繁體中文" },
    labels: {
      rank: "排名", card: "卡牌", number: "完整編號", language: "語言", price: "PSA 10 價格", ungradedReference: "未評級／RAW 參考價",
      priceShort: "價格",
      population: "PSA 10 數量", populationShort: "數量",
      marketCap: "市值", marketCapShort: "市值", trackedSales: "已追蹤成交額",
      trackedSalesShort: "成交",
      salesHelp: "只包括 CardZ Marketcap 追蹤範圍內捕捉到的 PSA 10 完成成交。", change: "升跌", changeShort: "升跌", asOf: "資料時間",
      pricePeriod: "價格期數", checkedAt: "最近檢查",
      awaitingFreshPrice: "等待新鮮價格",
      viewCard: "查看卡牌詳情", close: "關閉", story: "市場為何追捧", history: "每日市場走勢",
      dailyPrice: "參考價格", trackedSalesBars: "已追蹤成交額", salesTrend: "已追蹤成交額走勢", salesTrendShort: "成交走勢", imageAlt: "卡牌圖像",
      noHistory: "每日價格歷史仍在累積。", noCards: "此分類暫時沒有合資格卡牌。", noSales: "無成交紀錄", watchStatus: "觀察狀態",
      share: "分享卡牌", shareDone: "已複製連結", shareError: "複製失敗，請手動複製網址",
      expandImage: "放大檢視卡牌",
      marketCapHelp: "PSA 10 價格乘以已核實的 PSA 10 數量——頂級評分存量的可流通價值。",
      populationHelp: "登記在冊、經核實的 PSA 10 存世數量。",
      priceHelp: "追蹤期內最近一筆經核實的 PSA 10 成交價。",
      printLanguage: "{language}版", setCode: "系列代碼", rarity: "稀有度", parallel: "平行卡", finish: "卡面", packSource: "卡包來源",
      languageFilterAll: "全部語言",
    },
    theme: { dark: "深色模式", light: "淺色模式" },
    methodology: {
      title: "CardZ Marketcap 如何排列市場",
      // 改動理由見上面英文版嗰段註解（實測數字 + owner 決定）。
      body: "入選，從來不是理所當然。本指數收錄的每一張卡，均至少有 1,000 張經核實的 PSA 10；市值即為該存量乘以 PSA 10 參考價。參考價取自追蹤範圍內經核實的 PSA 10 成交；若窗口內的成交紀錄不足，該數字即為參考水平，而非成交均價。真實供應、真實需求，絕不虛構。",
    },
    provenance: {
      kicker: "METHOD & DATA",
      title: "市值數字的計算方法",
      body: "市值＝現時 PSA 10 參考價 × 經核實的 PSA 10 存世數量，每日更新時重新計算。",
      steps: [
        { term: "參考價", detail: "取自 CardZ Marketcap 追蹤範圍內已完成的 PSA 10 成交：先按張數還原單價，剔除極端值，再取中位數。" },
        { term: "存世數量", detail: "該一個印刷版本經核實的 PSA 10 數量。語言、系列、卡號與平行版本不會混為一談。" },
        { term: "缺口", detail: "期間內資料覆蓋不足的卡會標示為資料累積中，而不是填一個數上去；缺失的數值永遠保持缺失，不會當作零。" },
      ],
      updated: "更新",
      byline: "由 CardZ Marketcap Editorial 編算及覆核。",
      anchorSwitched: "歷史錨點：另一組參考序列",
    },
    status: { accumulating: "資料累積中", stale: "資料已逾時", unavailable: "暫無資料" },
    footer: "CardZ Marketcap，收藏卡牌藝術市場情報。",
  },
  "zh-CN": {
    nav: { all: "TCG 市场", pokemon: "宝可梦", onePiece: "海贼王", watchlist: "观察名单", box: "原盒" },
    boxHero: {
      eyebrow: "BOX MARKET",
      title: "未开封原盒 · 成交价优先",
      body: "原盒参考价以实际成交为准，挂牌价仅作参考。",
    },
    box: {
      groupAll: "全部",
      groups: { "optcg-en": "海贼王 英文", "optcg-jp": "海贼王 日文", "ptcg-en": "宝可梦 英文", "ptcg-jp": "宝可梦 日文" },
      boardTitle: "原盒排行（{count}）",
      box: "原盒",
      release: "发售",
      packs: "每盒包数",
      packsShort: "包数",
      soldCountShort: "成交",
      priceKind: { sold: "最近成交", market: "市场价", ask: "最低挂牌" },
      askFloor: "最低挂牌",
      unreleased: "未发售",
      setCode: "系列编号",
      fullName: "全名",
      print: "印刷版",
      coverage: "{total} 盒中 {priced} 盒有价",
      empty: "原盒市场数据准备中。",
    },
    boxProvenance: {
      kicker: "METHOD & DATA",
      title: "原盒参考价的计算方法",
      body: "每个原盒排名用参考价。方法以已完成成交为先；成交证据不足时才用市场参考；挂牌底价只作后备，永不当作成交。",
      steps: [
        { term: "成交", detail: "取自 CardZ Marketcap 追踪范围内已完成的原盒成交：先还原单盒单价，剔除极端值，再取中位数。" },
        { term: "市场参考", detail: "成交覆盖不足时，用该系列、语言与印刷版的市场参考价。" },
        { term: "挂牌底价", detail: "现时最低挂牌只作次要参考，不会取代成交或市场参考。缺失的数值永远保持缺失，不会当作零。" },
      ],
      updated: "更新",
      byline: "Compiled and reviewed by the CardZ Marketcap Editorial desk.",
    },
    hero: {
      eyebrow: "CARDZ MARKET INDEX", title: "收藏卡牌的市场全景",
      body: "以艺术价值为起点，通过经核实的身份、可流通供应及当前价格理解市场。",
    },
    pokemonHero: {
      eyebrow: "宝可梦市场", title: "以流动市场视角理解宝可梦卡牌", body: "按已核实印刷版本、PSA 10 供应及当前价格排列。",
    },
    onePieceHero: {
      eyebrow: "海贼王市场", title: "以流动市场视角理解海贼王卡牌", body: "按已核实印刷版本、PSA 10 供应及当前价格排列。",
    },
    watchlistHero: {
      eyebrow: "市场观察", title: "第 101 位起 · 持续观察", body: "紧随前百名之外的卡牌，追踪价格时效、供应与需求。",
    },
    heatmap: {
      title: "市值前 {count} 热力图", rankingTitle: "市值前 {count} 排行", pokemonTitle: "宝可梦市场热力图", onePieceTitle: "海贼王市场热力图",
      body: "面积代表当前 PSA 10 市值，色彩反映所选期间的价格变化。",
      negative: "下跌", neutral: "数据累积中", positive: "上涨", count: "张合资格卡牌", tilesLabel: "显示格数", viewRanking: "查看前 {count}", shareImage: "分享图片",
      customize: "自定义色彩", customizeTitle: "热力图色彩", resetDefault: "恢复默认",
      upColor: "上涨颜色", downColor: "下跌颜色", intensity: "色彩强度", neutralZone: "中立区", gap: "格子间距", cardSize: "卡牌大小", saved: "已保存",
    },
    periods: { "1d": "1 日", "7d": "7 日", "30d": "30 日" },
    languages: { en: "英文", ja: "日文", ko: "韩文", zhCN: "简体中文", zhTW: "繁体中文" },
    labels: {
      rank: "排名", card: "卡牌", number: "完整编号", language: "语言", price: "PSA 10 价格", ungradedReference: "未评级／RAW 参考价",
      priceShort: "价格",
      population: "PSA 10 数量", populationShort: "数量",
      marketCap: "市值", marketCapShort: "市值", trackedSales: "已追踪成交额",
      trackedSalesShort: "成交",
      salesHelp: "只包括 CardZ Marketcap 追踪范围内捕捉到的 PSA 10 完成成交。", change: "涨跌", changeShort: "涨跌", asOf: "数据时间",
      pricePeriod: "价格期数", checkedAt: "最近检查",
      awaitingFreshPrice: "等待新鲜价格",
      viewCard: "查看卡牌详情", close: "关闭", story: "市场为何追捧", history: "每日市场走势",
      dailyPrice: "参考价格", trackedSalesBars: "已追踪成交额", salesTrend: "已追踪成交额走势", salesTrendShort: "成交走势", imageAlt: "卡牌图像",
      noHistory: "每日价格历史仍在累积。", noCards: "此分类暂时没有合资格卡牌。", noSales: "无成交纪录", watchStatus: "观察状态",
      share: "分享卡牌", shareDone: "已复制链接", shareError: "复制失败，请手动复制网址",
      expandImage: "放大查看卡牌",
      marketCapHelp: "PSA 10 价格乘以已核实的 PSA 10 数量——顶级评级存量的可流通价值。",
      populationHelp: "登记在册、经核实的 PSA 10 存世数量。",
      priceHelp: "追踪期内最近一笔经核实的 PSA 10 成交价。",
      printLanguage: "{language}版", setCode: "系列代码", rarity: "稀有度", parallel: "平行卡", finish: "卡面", packSource: "卡包来源",
      languageFilterAll: "全部语言",
    },
    theme: { dark: "深色模式", light: "浅色模式" },
    methodology: {
      title: "CardZ Marketcap 如何排列市场",
      body: "入选，从来不是理所当然。本指数收录的每一张卡都至少有 1,000 张经核实的 PSA 10；市值即为该存量乘以 PSA 10 参考价。参考价取自追踪范围内经核实的 PSA 10 成交；若窗口内的成交纪录不足，该数字即为参考水平，而非成交均价。真实供应、真实需求，绝不虚构。",
    },
    provenance: {
      kicker: "METHOD & DATA",
      title: "市值数字的计算方法",
      body: "市值＝当前 PSA 10 参考价 × 经核实的 PSA 10 存世数量，每日更新时重新计算。",
      steps: [
        { term: "参考价", detail: "取自 CardZ Marketcap 追踪范围内已完成的 PSA 10 成交：先按张数还原单价，剔除极端值，再取中位数。" },
        { term: "存世数量", detail: "该一个印刷版本经核实的 PSA 10 数量。语言、系列、卡号与平行版本不会混为一谈。" },
        { term: "缺口", detail: "期间内数据覆盖不足的卡会标示为数据累积中，而不是填一个数上去；缺失的数值永远保持缺失，不会当作零。" },
      ],
      updated: "更新",
      byline: "由 CardZ Marketcap Editorial 编算及复核。",
      anchorSwitched: "历史锚点：另一组参考序列",
    },
    status: { accumulating: "数据累积中", stale: "数据已过期", unavailable: "暂无数据" },
    footer: "CardZ Marketcap，收藏卡牌艺术市场情报。",
  },
  ja: {
    nav: { all: "TCG 市場", pokemon: "ポケモン", onePiece: "ワンピース", watchlist: "ウォッチリスト", box: "BOX" },
    boxHero: {
      eyebrow: "BOX市場",
      title: "未開封BOX · 成約価格を優先",
      body: "BOX参考価格は実際の成約が基準。出品価格は参考値です。",
    },
    box: {
      groupAll: "すべて",
      groups: { "optcg-en": "ワンピース 英語", "optcg-jp": "ワンピース 日本語", "ptcg-en": "ポケモン 英語", "ptcg-jp": "ポケモン 日本語" },
      boardTitle: "BOXランキング（{count}）",
      box: "BOX",
      release: "発売",
      packs: "1BOXのパック数",
      packsShort: "パック",
      soldCountShort: "成約",
      priceKind: { sold: "直近成約", market: "市場価格", ask: "最安出品" },
      askFloor: "最安出品",
      unreleased: "未発売",
      setCode: "セットコード",
      fullName: "正式名称",
      print: "版",
      coverage: "{total} 中 {priced} BOXに価格",
      empty: "BOX市場データを準備中です。",
    },
    boxProvenance: {
      kicker: "METHOD & DATA",
      title: "BOX参考価格の算出方法",
      body: "各BOX順位は参考価格を使います。方法は確定成約を優先し、成約証拠が薄いときだけ市場参考を使い、出品下限は予備であり成約としては扱いません。",
      steps: [
        { term: "成約", detail: "CardZ Marketcap の追跡範囲で確認できたBOX成約から取得します。まとめ売りは1BOXあたりに換算し、極端な外れ値を除いたうえで中央値を用います。" },
        { term: "市場参考", detail: "成約カバレッジが不足している場合、そのセット・言語・印刷版の市場参考価格を用います。" },
        { term: "出品下限", detail: "現在の最安出品は二次的な参考であり、成約や市場参考の代わりにはなりません。欠損値は常に欠損のままで、ゼロとしては扱いません。" },
      ],
      updated: "更新",
      byline: "Compiled and reviewed by the CardZ Marketcap Editorial desk.",
    },
    hero: {
      eyebrow: "CARDZ MARKET INDEX", title: "コレクティブルカード市場を一望する",
      body: "アートの価値を起点に、確認済みのカード情報、流通供給、現在価格から市場を読み解きます。",
    },
    pokemonHero: {
      eyebrow: "ポケモン市場", title: "動く市場として見るポケモンカード", body: "確認済みの印刷版、PSA 10 供給、現在価格で順位付けします。",
    },
    onePieceHero: {
      eyebrow: "ワンピース市場", title: "動く市場として見るワンピースカード", body: "確認済みの印刷版、PSA 10 供給、現在価格で順位付けします。",
    },
    watchlistHero: {
      eyebrow: "マーケットウォッチ", title: "101位以降 · 継続ウォッチ", body: "トップ100圏外のカードの価格鮮度・供給・需要を追跡。",
    },
    heatmap: {
      title: "時価総額トップ {count} ヒートマップ", rankingTitle: "時価総額トップ {count}", pokemonTitle: "ポケモン市場ヒートマップ", onePieceTitle: "ワンピース市場ヒートマップ",
      body: "面積は現在の PSA 10 時価総額、色は選択期間の価格変化を表します。",
      negative: "下落", neutral: "集計中", positive: "上昇", count: "枚の適格カード", tilesLabel: "表示数", viewRanking: "トップ {count} を見る", shareImage: "画像をシェア",
      customize: "色をカスタマイズ", customizeTitle: "ヒートマップの色", resetDefault: "デフォルトに戻す",
      upColor: "上昇カラー", downColor: "下落カラー", intensity: "色の強度", neutralZone: "ニュートラルゾーン", gap: "タイル間隔", cardSize: "カードサイズ", saved: "保存済み",
    },
    periods: { "1d": "1 日", "7d": "7 日", "30d": "30 日" },
    languages: { en: "英語", ja: "日本語", ko: "韓国語", zhCN: "簡体中国語", zhTW: "繁体中国語" },
    labels: {
      rank: "順位", card: "カード", number: "完全な番号", language: "言語", price: "PSA 10 価格", ungradedReference: "未鑑定／RAW 参考価格",
      priceShort: "価格",
      population: "PSA 10 枚数", populationShort: "枚数",
      marketCap: "時価総額", marketCapShort: "時価総額", trackedSales: "追跡成約額",
      trackedSalesShort: "成約",
      salesHelp: "CardZ Marketcap の追跡範囲で確認できた PSA 10 の成約のみを含みます。", change: "変動", changeShort: "変動", asOf: "データ時刻",
      pricePeriod: "価格期", checkedAt: "最終確認",
      awaitingFreshPrice: "新しい価格を待機中",
      viewCard: "カード詳細を見る", close: "閉じる", story: "市場で支持される理由", history: "日次市場推移",
      dailyPrice: "参考価格", trackedSalesBars: "追跡成約額", salesTrend: "追跡成約額の推移", salesTrendShort: "成約推移", imageAlt: "カード画像",
      noHistory: "日次価格履歴を蓄積しています。", noCards: "この表示には適格カードがありません。", noSales: "成約記録なし", watchStatus: "観察ステータス",
      share: "カードを共有", shareDone: "リンクをコピーしました", shareError: "コピーに失敗しました。URL を手動でコピーしてください",
      expandImage: "カードを拡大表示",
      marketCapHelp: "PSA 10 価格 × 確認済み PSA 10 枚数——最高評価の流通可能な価値。",
      populationHelp: "レジストリに記録された、確認済みの PSA 10 現存枚数。",
      priceHelp: "追跡期間内で確認できた直近の PSA 10 成約価格。",
      printLanguage: "{language}版", setCode: "セットコード", rarity: "レアリティ", parallel: "パラレル", finish: "表面", packSource: "収録パック",
      languageFilterAll: "すべての言語",
    },
    theme: { dark: "ダークモード", light: "ライトモード" },
    methodology: {
      title: "CardZ Marketcap の市場ランキング方法",
      body: "掲載は、与えられるものではなく獲得するもの。この指数のカードはすべて確認済み PSA 10 が 1,000 枚以上あり、時価総額はその流通量に PSA 10 参考価格を掛けた値です。参考価格は、当社の追跡範囲内で確認された PSA 10 取引から算出します。対象期間の取引記録が十分でない場合、その数値は取引平均ではなく参考水準として示されます。実在する供給と需要、それ以外は作りません。",
    },
    provenance: {
      kicker: "METHOD & DATA",
      title: "マーケットキャップの算出方法",
      body: "マーケットキャップは、現在の PSA 10 参考価格に確認済み PSA 10 の現存枚数を掛けた値で、毎日の更新ごとに再計算されます。",
      steps: [
        { term: "参考価格", detail: "CardZ Marketcap の追跡範囲で確認できた PSA 10 の成約から取得します。まとめ売りは1枚あたりに換算し、極端な外れ値を除いたうえで中央値を用います。" },
        { term: "現存枚数", detail: "その印刷版に対する確認済み PSA 10 の枚数です。言語・セット・カード番号・パラレルを混在させることはありません。" },
        { term: "欠損", detail: "対象期間のデータカバレッジが不足しているカードは、数値を埋めずに集計中と表示します。欠損値は常に欠損のままで、ゼロとしては扱いません。" },
      ],
      updated: "更新",
      byline: "CardZ Marketcap Editorial が集計・確認しています。",
      anchorSwitched: "履歴の基準：別の参照系列",
    },
    status: { accumulating: "集計中", stale: "更新待ち", unavailable: "データなし" },
    footer: "CardZ Marketcap。コレクティブルカードのアート市場情報。",
  },
  ko: {
    nav: { all: "TCG 마켓", pokemon: "포켓몬", onePiece: "원피스", watchlist: "관심 목록", box: "BOX" },
    boxHero: {
      eyebrow: "BOX MARKET",
      title: "미개봉 박스 · 체결가 우선",
      body: "박스 기준 가격은 실제 체결이 기준. 호가는 참고값입니다.",
    },
    box: {
      groupAll: "전체",
      groups: { "optcg-en": "원피스 영문", "optcg-jp": "원피스 일문", "ptcg-en": "포켓몬 영문", "ptcg-jp": "포켓몬 일문" },
      boardTitle: "BOX 랭킹 ({count})",
      box: "BOX",
      release: "발매",
      packs: "박스당 팩 수",
      packsShort: "팩",
      soldCountShort: "체결",
      priceKind: { sold: "최근 체결", market: "시장 가격", ask: "최저 호가" },
      askFloor: "최저 호가",
      unreleased: "미발매",
      setCode: "세트 코드",
      fullName: "정식 명칭",
      print: "판",
      coverage: "{total}개 중 {priced}개 가격 확보",
      empty: "BOX 시장 데이터를 준비 중입니다.",
    },
    boxProvenance: {
      kicker: "METHOD & DATA",
      title: "BOX 기준가 산출 방법",
      body: "각 BOX 순위는 기준가를 씁니다. 방법은 완료 체결을 우선하고, 체결 증거가 부족할 때만 시장 참고를 쓰며, 호가 하한은 예비일 뿐 체결로 취급하지 않습니다.",
      steps: [
        { term: "체결", detail: "CardZ Marketcap 추적 범위에서 확인된 BOX 완료 거래에서 가져옵니다. 묶음은 박스 하나로 환산하고 극단값을 제거한 뒤 중앙값을 사용합니다." },
        { term: "시장 참고", detail: "체결 커버리지가 부족할 때는 해당 세트, 언어, 인쇄판의 시장 참고가를 사용합니다." },
        { term: "호가 하한", detail: "현재 최저 호가는 이차 참고일 뿐이며 체결이나 시장 참고를 대체하지 않습니다. 결측값은 언제나 결측으로 남으며 0으로 처리하지 않습니다." },
      ],
      updated: "업데이트",
      byline: "Compiled and reviewed by the CardZ Marketcap Editorial desk.",
    },
    hero: {
      eyebrow: "CARDZ MARKET INDEX",
      title: "컬렉터블 카드 시장을 한눈에",
      body: "아트의 가치를 출발점으로, 검증된 카드 정보와 유통 공급, 현재 가격으로 시장을 읽습니다.",
    },
    pokemonHero: {
      eyebrow: "포켓몬 마켓",
      title: "살아있는 시장으로 보는 포켓몬 카드",
      body: "검증된 인쇄판을 현재 PSA 10 공급과 가격으로 순위화합니다.",
    },
    onePieceHero: {
      eyebrow: "원피스 마켓",
      title: "살아있는 시장으로 보는 원피스 카드",
      body: "검증된 인쇄판을 현재 PSA 10 공급과 가격으로 순위화합니다.",
    },
    watchlistHero: {
      eyebrow: "마켓 워치",
      title: "101위 이후 · 지속 관찰",
      body: "상위 100 밖 카드의 가격 신선도·공급·수요를 추적합니다.",
    },
    heatmap: {
      title: "시가총액 상위 {count} 히트맵", rankingTitle: "시가총액 상위 {count}", pokemonTitle: "포켓몬 시장 히트맵", onePieceTitle: "원피스 시장 히트맵",
      body: "면적은 현재 PSA 10 시가총액, 색상은 선택 기간의 가격 변동을 나타냅니다.",
      negative: "하락", neutral: "집계 중", positive: "상승", count: "장의 적격 카드", tilesLabel: "표시 수", viewRanking: "상위 {count} 보기", shareImage: "이미지 공유",
      customize: "색상 사용자 정의", customizeTitle: "히트맵 색상", resetDefault: "기본값으로 재설정",
      upColor: "상승 색상", downColor: "하락 색상", intensity: "색상 강도", neutralZone: "중립 구간", gap: "타일 간격", cardSize: "카드 크기", saved: "저장됨",
    },
    periods: { "1d": "1일", "7d": "7일", "30d": "30일" },
    languages: { en: "영어", ja: "일본어", ko: "한국어", zhCN: "중국어 간체", zhTW: "중국어 번체" },
    labels: {
      rank: "순위", card: "카드", number: "전체 번호", language: "언어", price: "PSA 10 가격", ungradedReference: "미감정／RAW 참고가",
      priceShort: "가격",
      population: "PSA 10 매수", populationShort: "매수",
      marketCap: "시가총액", marketCapShort: "시총", trackedSales: "추적 거래액",
      trackedSalesShort: "거래",
      salesHelp: "CardZ Marketcap 추적 범위에서 확인된 PSA 10 완료 거래만 포함합니다.", change: "등락", changeShort: "등락", asOf: "데이터 시각",
      pricePeriod: "가격 기간", checkedAt: "최근 확인",
      awaitingFreshPrice: "신선한 가격 대기",
      viewCard: "카드 상세 보기", close: "닫기", story: "시장이 주목하는 이유", history: "일별 시장 추이",
      dailyPrice: "기준 가격", trackedSalesBars: "추적 거래액", salesTrend: "추적 거래액 추이", salesTrendShort: "거래 추이", imageAlt: "카드 이미지",
      noHistory: "일별 가격 이력을 축적하고 있습니다.", noCards: "이 보기에 적격 카드가 없습니다.", noSales: "거래 기록 없음", watchStatus: "관찰 상태",
      share: "카드 공유", shareDone: "링크 복사됨", shareError: "복사 실패 — 주소창에서 직접 복사하세요",
      expandImage: "카드 크게 보기",
      marketCapHelp: "PSA 10 가격 × 검증된 PSA 10 매수 — 최고 등급 공급의 거래 가능 가치.",
      populationHelp: "레지스트리에 기록된 검증된 PSA 10 현존 매수.",
      priceHelp: "추적 기간 내 가장 최근에 검증된 PSA 10 거래 가격.",
      printLanguage: "{language}판", setCode: "세트 코드", rarity: "레어도", parallel: "패러렐", finish: "표면", packSource: "수록 팩",
      languageFilterAll: "모든 언어",
    },
    theme: { dark: "다크 모드", light: "라이트 모드" },
    methodology: {
      title: "CardZ Marketcap의 시장 순위 방식",
      body: "수록은 주어지는 것이 아니라 얻어내는 것입니다. 이 지수의 모든 카드는 검증된 PSA 10이 1,000장 이상이며, 시가총액은 그 물량에 PSA 10 기준가를 곱한 값입니다. 기준가는 저희 추적 범위 안에서 검증된 PSA 10 거래로 산출합니다. 해당 기간의 거래 기록이 충분하지 않을 경우 그 수치는 거래 평균이 아니라 참고 수준으로 제시됩니다. 실제 공급과 실제 수요, 그 밖의 것은 만들지 않습니다.",
    },
    provenance: {
      kicker: "METHOD & DATA",
      title: "시가총액 산출 방식",
      body: "시가총액은 현재 PSA 10 기준가에 검증된 PSA 10 현존 수량을 곱한 값이며, 매일 갱신할 때마다 다시 계산합니다.",
      steps: [
        { term: "기준가", detail: "CardZ Marketcap 추적 범위에서 확인된 PSA 10 완료 거래에서 가져옵니다. 묶음 거래는 카드 한 장 기준으로 환산하고 극단값을 제거한 뒤 중앙값을 사용합니다." },
        { term: "현존 수량", detail: "해당 인쇄본에 대해 검증된 PSA 10 수량입니다. 언어, 세트, 카드 번호, 패러렐을 섞지 않습니다." },
        { term: "결측", detail: "해당 기간의 데이터 커버리지가 부족한 카드는 숫자를 채우지 않고 집계 중으로 표시합니다. 결측값은 언제나 결측으로 남으며 0으로 처리하지 않습니다." },
      ],
      updated: "업데이트",
      byline: "CardZ Marketcap Editorial이 집계하고 검토합니다.",
      anchorSwitched: "과거 기준: 다른 참조 계열",
    },
    status: { accumulating: "집계 중", stale: "오래된 데이터", unavailable: "데이터 없음" },
    footer: "CardZ Marketcap. 컬렉터블 카드 아트 마켓 인텔리전스.",
  },
};

// Mirrors the canonical ingest alias table so a printing language written in any accepted
// spelling resolves to the same canonical code.
const CARD_LANGUAGE_ALIASES: Record<string, CardLanguage> = {
  en: "en", english: "en",
  ja: "ja", jp: "ja", japanese: "ja",
  ko: "ko", korean: "ko",
  zhcn: "zhCN", zhhans: "zhCN",
  zhtw: "zhTW", zhhant: "zhTW",
};

export function localizedCardLanguage(language: string, locale: Locale): string {
  const raw = (language ?? "").trim();
  const canonical = CARD_LANGUAGE_ALIASES[raw.toLowerCase().replace(/[-_\s]/g, "")];
  return canonical ? copy[locale].languages[canonical] : raw;
}
