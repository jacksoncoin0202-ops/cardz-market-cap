/* type-only import：編譯時抹走，唔會將 site-copy 嗰幾版長文拖入首頁 bundle。 */
import type { ContentPageKey } from "./site-copy";
import type { Locale, MarketWindow } from "./types";

// The card's printing language, not the reader's locale. These are the codes emitted by the
// canonical ingest language contract. An unrecognised code falls back to the supplied display
// value in the same way `names` and `sets` keep their English text when no translation exists.
export const cardLanguages = ["en", "ja", "ko", "zhCN", "zhTW"] as const;
export type CardLanguage = (typeof cardLanguages)[number];

// Short codes shared by every locale: keeps filter chips to a single line on mobile.
export const cardLanguageShort: Record<CardLanguage, string> = {
  en: "EN",
  ja: "JP",
  ko: "KR",
  zhCN: "SC",
  zhTW: "TC",
};

export interface Copy {
  nav: {
    all: string;
    pokemon: string;
    onePiece: string;
    watchlist: string;
    box: string;
    /* watchlist pager 嘅 aria-label（‹ ›）*/
    previousPage: string;
    nextPage: string;
  };
  /*
   * 五版內容頁嘅 <title> / meta description（GEO 批，owner 2026-08-16；verify pass 補齊）。
   *
   * 點解唔跟 site-copy 嘅 h1／答案：H1 寫得長冇問題（畫面有位），但 SERP 會截。
   * 未有呢兩組字之前 content-page.tsx 退返 H1，實測 /methodology 出咗 101 字嘅 <title>，
   * 連 head term 都畀截走。規矩：加埋 root layout 嘅 " | CardZ Marketcap" 之前 ≤60 字，
   * 描述 120–155 字（英文為準，CJK 字數自然短啲），head term 行頭、品牌行尾。
   *
   * 只覆蓋五版內容頁。/rankings 同 /market-report 唔喺度：嗰兩版嘅標題係由 snapshot
   * 生（帶年份／月份），寫死喺呢度就變咗同一句嘢兩個來源，一定有日對唔上。
   */
  pageTitles: Record<ContentPageKey, string>;
  pageDescriptions: Record<ContentPageKey, string>;
  /*
   * Footer 導覽（verify pass 2026-08-16 加）。GEO 批出咗七版新頁，但站內零入口 ——
   * 淨係喺 sitemap 同 llms.txt 見到，人同爬蟲都行唔到過去（孤兒頁）。
   *
   * 點解擺喺 i18n.ts 而唔係 site-copy.ts：Footer 係 client component，site-copy.ts
   * 係五版長文，import 落 client 會將幾十 KB 內容推入每一版嘅 bundle。呢度得七個短 label。
   * 亦唔入 header：手機 header 得四條主 nav 就已經迫爆。
   */
  footerNav: {
    heading: string;
    rankings: string;
    marketReport: string;
    methodology: string;
    about: string;
    faq: string;
    glossary: string;
    data: string;
  };
  boxHero: { eyebrow: string; title: string; body: string };
  box: {
    groupAll: string;
    /* 只分 TCG；語言（EN/JP）由榜嘅語言篩負責，唔再喺呢排掣度分 */
    groups: Record<"optcg" | "ptcg", string>;
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
    showMore: string;
    /* print_wave 代號 → 顯示字；DB 詞彙 std/1st/wave1/wave2/unlimited/reprint，std 唔出行 */
    printWaves: Record<"1st" | "wave1" | "wave2" | "unlimited" | "reprint", string>;
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
  /*
   * 首屏可引用段（GEO，owner 2026-08-16）。H1 淨係一句品牌聲線，答唔到「呢個網站係咩、
   * 個數點計、幾時嘅數」——所以 hero 下面補呢四句：定義、as-of、當前市況、消歧義。
   * `summary` 嘅數全部由 snapshot 填（{total} {topName} {topCap} {moverName} {moverPct}），
   * 一個都唔准寫死；填唔齊就成句唔出（同 cardFactSentence 一樣 fail-closed）。
   */
  intro: {
    definition: string;
    /** {date} */
    asOf: string;
    /** {total} {topName} {topCap} {moverName} {moverPct} */
    summary: string;
    disambiguation: string;
    /** <details> 嘅 summary 標籤（默認摺埋，手機唔准推走熱力圖）；{date} */
    about: string;
  };
  /*
   * hero.* 係畀人睇嘅（owner 定嘅品牌聲線；2026-08-16 GEO 批將 title 改做 keyword-first，
   * body 保留聲線但要講得出 PSA 10 同市值）；呢組淨係畀搜尋／AI 引用睇。
   * 之前三個 ranking hub 直接攞 hero.title/body 當 <title>/description 用，一條 string 做兩份工：
   *   - 首頁 <title> 冇任何 head term，亦冇 `| CardZ Marketcap`（Next 嘅 title.template 唔會套用喺
   *     定義佢嗰個 segment，而 app/page.tsx 同 app/layout.tsx 同一個 segment）；
   *   - /pokemon 同 /one-piece 五個語言全部共用同一句 body，即係兩版重複 meta description。
   * 分開兩組之後版面零改動，只係 <head> 換咗。—— verify pass 2026-08-16
   */
  seo: {
    home: { title: string; description: string };
    pokemon: { title: string; description: string };
    onePiece: { title: string; description: string };
    /*
     * JSON-LD Dataset（@id = datasetId()）嘅唯一名稱來源。之前 market-page、seo-routes、
     * /data 三個 owner 各自寫一份，同一個 @id 喺三版有三個唔同名 —— schema 入面即係
     * 同一個實體自相矛盾，引擎唔知信邊個。放喺 i18n（唔係 site-copy）係因為 market-page
     * 係 "use client"，唔可以將成個 site-copy 拖落 client bundle。—— verify pass 2026-08-16
     */
    dataset: { name: string; description: string };
  };
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
    tilesLabel: string;
    /* kiosk 全屏（店主展示模式）：同一粒掣兩個 aria-label，唔准得一個字串靠 icon 講狀態 */
    fullscreen: string;
    exitFullscreen: string;
    customize: string;
    customizeTitle: string;
    resetDefault: string;
    upColor: string;
    downColor: string;
    intensity: string;
    neutralZone: string;
    gap: string;
    cardSize: string;
    /* /tune lab 專用（clamp / alpha / aspect + 複製 JSON），heatmap tune panel 唔出 */
    clamp: string;
    alphaMin: string;
    alphaMax: string;
    cardAspect: string;
    copyParams: string;
    paramsCopied: string;
    copyFailed: string;
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
    saleDate: string;
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
    /* 手機榜表頭嗰個 48px 成交圖欄專用（`salesTrendShort` 英文係 "Sales trend"，
       48px 塞唔落就摺兩行，成個 header 由 27px 變 40px，第一張卡跌出 320px 外）。 */
    salesTrendColumn: string;
    imageAlt: string;
    noHistory: string;
    noCards: string;
    noSales: string;
    watchStatus: string;
    share: string;
    shareDone: string;
    shareError: string;
    /* 「匯出／分享一張圖」嘅掣文字。熱力圖同卡片內頁共用同一個 key ——
       同一個動作唔好兩個字串，翻譯到第三次就會有一個語言講另一件事。 */
    shareImage: string;
    /*
     * 目的地選單（components/share-menu.tsx）。owner 2026-08-20：撳「分享圖片」要先
     * 問去邊，因為每個平台嘅最佳比例唔同。
     *
     * ⚠️ 冇 `shareToInstagram` / `shareToX` 呢啲 key —— 四個平台名係專有名詞，五個語言
     * 一模一樣，入咗 i18n 就係四條永遠唔會譯、但每次加語言都要抄多四次嘅字串。名寫死
     * 喺 share-menu.tsx `BRAND_NAME`。呢度剩返真係要譯嗰啲。
     */
    shareTo: string;
    shareToStatus: string;
    shareToOther: string;
    shareToDesktop: string;
    shareToPortrait: string;
    shareToWidescreen: string;
    /* 熱力圖闊版唔係固定比例（跟用戶當下畫面），比例位出呢句代替「16:9」 */
    shareRatioFrame: string;
    /*
     * 清晰度（熱力圖先有；owner 2026-08-21「我想 1080p 同埋 4K 兩隻分別嘅啫」）。
     *
     * ⚠️ 冇 `share1080p` / `share4K` —— 「1080p」「4K」係型號名，五個語言一樣，同上面
     * 四個平台名同一個道理，寫死喺 `lib/share-resolution.ts` `RESOLUTION_LABEL`。
     * `shareQualitySlow` 係 4K 旁邊嗰粒提示（未 cache 要成分鐘，唔好扮即刻有）。
     */
    shareQuality: string;
    shareQualitySlow: string;
    /*
     * 印刷版本相關。`printLanguage` 係 template：`languages` 只出裸字（「日文」），
     * 但 badge 要出「日文版」，所以用 {language} 佔位符夾 localizedCardLanguage() 嘅輸出。
     * 其餘幾條係 printing identity 各欄嘅標籤。
     */
    printLanguage: string;
    setCode: string;
    finish: string;
    languageFilterAll: string;
    languageFilterAllShort: string;
    searchPlaceholder: string;
    searchPlaceholderPokemon: string;
    searchPlaceholderOnePiece: string;
    searchPlaceholderBox: string;
    searchLabel: string;
    searchLabelPokemon: string;
    searchLabelOnePiece: string;
    searchScope: string;
    searchScopeAll: string;
    searchClear: string;
    sortHighToLow: string;
    sortLowToHigh: string;
    resultCount: string;
    noSearchResults: string;
    noSearchResultsBox: string;
    /* 入圍門檻，一句過寫喺搜尋框下面 —— 唔係等人搵唔到先解釋（owner 2026-08-19）。
       數字同 methodology.body、llms-full.txt、snapshot universe.populationMin 綁死，
       改門檻要五種語言一齊改，靠 scripts/test-fe-pop-threshold.mjs 守。 */
    searchPopRule: string;
    searchUnqualified: string;
    searchUnqualifiedScoped: string;
    catalogElsewhere: string;
    searchModeTitle: string;
    clearSearch: string;
    rankingRange: string;
    showMore: string;
    /* 搜尋結果分批出：{count} = 今次再顯示幾多張，{total} = 命中總數 */
    showMoreResults: string;
    /* 榜單接落去嗰陣：個掣嘅字 + 讀屏 aria-live 公告（owner 2026-08-18「我想繼續碌落去」）。
       同 `showMoreResults` 分開——嗰個係搜尋結果分批，呢個係榜單本身接落一版。 */
    loadingMore: string;
    /* 全站索引載唔到時嘅退化提示（rankings.tsx 只剩當頁過濾，唔係「冇結果」） */
    catalogUnavailable: string;
    pageSizeLabel: string;
    /* 榜上接夠 {count} 行就唔再接（owner 2026-08-18：800 行會 lag 到要 F5）。
       個「展示更多」掣會消失，所以要出一句解釋去邊度睇下一批。 */
    rowCapReached: string;
    /* 榜頂「而家一共 render 緊幾多行」。owner 2026-08-18：「碌到五百嗰時，睇返頂頂
       嗰個頁面都冇話係五百」—— 每頁數量掣仍然 highlight 住 100（佢真係 100/版），
       同畫面上 500 行望落打對台，所以要喺同一組掣隔離講返實數。 */
    rowsShown: string;
    /* 榜尾 pager 嗰粒「返回頂部」（返榜頂，唔係文件頂） */
    backToTop: string;
    /*
     * Phase B 手機瘦身（設計稿 UX_MOBILE_SEARCH_DESIGN_20260816 §設計（手機））：
     * 計數搬入搜尋框內（`resultCountShort`），讀屏另一份獨立 debounce 播
     * （`resultCountAnnounce`）；排序／方向／印刷語言收埋落 bottom sheet。
     */
    resultCountShort: string;
    resultCountAnnounce: string;
    sortSheetTitle: string;
    sortSheetTrigger: string;
    /* 非預設排序時個掣出「排序 · {label}」 */
    sortSheetTriggerActive: string;
    sortDirection: string;
    applyFilters: string;
    resetFilters: string;
    removeFilter: string;
    /* Phase C（設計稿 §設計（電腦））：分榜搜唔到嘅空狀態掣，真 navigate 去 `/?q=…`。
       常駐嘅搜尋範圍 dropdown 已拆走 —— 範圍 = route。 */
    searchAllSite: string;
    sortBy: string;
    currency: string;
    /* 貨幣選單分組 heading（31 隻貨幣，按地區分四組；次序見 lib/currency-meta.ts） */
    currencyRegionAsia: string;
    currencyRegionAmericas: string;
    currencyRegionEurope: string;
    currencyRegionMea: string;
    /* 升跌顏色慣例切換：按鈕 aria-label / title 出「而家係邊個慣例」 */
    upDownGreen: string;
    upDownRed: string;
  };
  theme: { dark: string; light: string };
  /*
   * Heatmap 分享圖嘅 toast（CopyButton doneLabel/errorLabel）。以前借用 labels.shareDone /
   * shareError（「已複製連結」／「複製失敗」）—— 但個掣係匯出 PNG，唔係複製連結，講錯咗件事。
   * ⚠️ 圖**入面**嘅字全部係英文硬編碼喺 lib/share-image.ts，唔喺呢度（owner 2026-08-17：
   * 一張圖出咗街係俾全世界睇）。呢兩條淨係介面 toast，所以要跟介面語言。
   */
  share: { done: string; error: string };
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
  skipToContent: string;
  notFound: { title: string; body: string; back: string };
  errorPage: { title: string; body: string; retry: string };
}

export const copy: Record<Locale, Copy> = {
  en: {
    nav: { all: "TCG Market", pokemon: "Pokémon", onePiece: "One Piece", watchlist: "Watchlist", box: "BOX", previousPage: "Previous page", nextPage: "Next page" },
    pageTitles: {
      methodology: "Trading card market cap: PSA 10 price × population",
      about: "About CardZ Marketcap, the graded card market index",
      faq: "Pokémon card market cap FAQ: PSA 10 questions answered",
      glossary: "PSA 10 market cap glossary: graded card index terms",
      data: "PSA 10 market cap data and API: rankings in JSON",
    },
    pageDescriptions: {
      methodology: "How CardZ Marketcap computes trading card market cap: PSA 10 reference price multiplied by verified PSA 10 population, and the gaps we leave blank.",
      about: "CardZ Marketcap is a daily graded card market index. What it tracks, how Pokémon and One Piece cards are ranked by PSA 10 market cap, and who builds it.",
      faq: "Answers on Pokémon card market cap and PSA 10 market cap: what the number means, where the price and population come from, and what it is not.",
      glossary: "Definitions for the graded card market index: PSA 10 market cap, population report, gem rate, reference price and the other terms used in the rankings.",
      data: "PSA 10 market cap rankings as JSON over a public read-only API. Endpoints, field definitions, update cadence, licence and the citation format.",
    },
    footerNav: {
      heading: "Explore",
      rankings: "Rankings",
      marketReport: "Market report",
      methodology: "Methodology",
      about: "About",
      faq: "FAQ",
      glossary: "Glossary",
      data: "Data & API",
    },
    boxHero: {
      eyebrow: "BOX MARKET",
      title: "Sold-first prices for sealed booster boxes",
      body: "Completed sales set the reference price; asking prices never stand in for them.",
    },
    box: {
      groupAll: "All",
      groups: { optcg: "One Piece", ptcg: "Pokémon" },
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
      showMore: "Show more ({count} remaining)",
      printWaves: { "1st": "1st Edition", wave1: "1st Edition", wave2: "Reprint", unlimited: "Unlimited", reprint: "Reprint" },
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
      title: "Pokémon & Trading Card Market Cap — PSA 10 Index",
      body: "Art comes first. Verified identity, tradable PSA 10 supply and current pricing turn each card into a readable market cap.",
    },
    pokemonHero: {
      eyebrow: "POKÉMON MARKET",
      title: "Pokémon Card Market Cap Rankings (PSA 10)",
      body: "Verified printings ranked by PSA 10 market cap: current PSA 10 supply and pricing, nothing invented.",
    },
    onePieceHero: {
      eyebrow: "ONE PIECE MARKET",
      title: "One Piece Card Market Cap Rankings (PSA 10)",
      body: "Verified printings ranked by PSA 10 market cap: current PSA 10 supply and pricing, nothing invented.",
    },
    intro: {
      definition: "CardZ Marketcap is a daily market-cap index for graded collectible cards. Each card's market cap is its PSA 10 reference price multiplied by its verified PSA 10 population, covering Pokémon and One Piece printings ranked from the top down.",
      asOf: "Figures on this page are as of {date}.",
      summary: "The cards listed here carry {total} in combined PSA 10 market cap. {topName} leads at {topCap}; the largest 7-day price move is {moverName} at {moverPct}.",
      disambiguation: "CardZ Marketcap is a data index for graded cards — not a cryptocurrency, not the CARDS token, and there is no CardZ ticker.",
      about: "About this index · as of {date}",
    },
    seo: {
      home: {
        title: "Trading card market cap: live PSA 10 index",
        description: "Trading card market cap rankings for Pokémon and One Piece, calculated as PSA 10 price × verified PSA 10 population. Updated daily by CardZ Marketcap.",
      },
      pokemon: {
        title: "Pokémon card market cap: PSA 10 rankings",
        description: "Pokémon card market cap rankings by PSA 10 price × verified PSA 10 population, covering the most valuable graded cards. Updated daily by CardZ Marketcap.",
      },
      onePiece: {
        title: "One Piece card market cap: PSA 10 rankings",
        description: "One Piece card market cap rankings by PSA 10 price × verified PSA 10 population, covering the top graded cards. Updated daily by CardZ Marketcap.",
      },
      dataset: {
        name: "CardZ Marketcap graded trading card market cap index",
        description: "Daily market capitalisation index for graded Pokémon and One Piece trading cards, computed as PSA 10 reference price multiplied by verified PSA 10 population.",
      },
    },
    watchlistHero: {
      eyebrow: "MARKET WATCH",
      title: "Ranks 101 and beyond, under watch",
      body: "Cards just outside the top 100, tracked for price freshness, supply and demand.",
    },
    heatmap: {
      /* heatmap H1 一定要 ≤ 9em 闊（globals.css .heatmap-heading h1 用容器闊度 ÷ 9.5 定字級，永遠一行）；
         owner 2026-08-17：「就咁 top 100 市值咪算囉」—— 圖自己會講嘢。改文案先量闊度。 */
      title: "Top {count} heatmap",
      rankingTitle: "Top {count} by market cap",
      pokemonTitle: "Pokémon heatmap",
      onePieceTitle: "One Piece heatmap",
      body: "Area represents current PSA 10 market cap. Colour follows the selected price window.",
      negative: "Down",
      neutral: "Data pending",
      positive: "Up",
      tilesLabel: "Tiles",
      fullscreen: "Full screen display",
      exitFullscreen: "Exit full screen",
      customize: "Customize heatmap",
      customizeTitle: "Heatmap settings",
      resetDefault: "Reset to default",
      upColor: "Up colour",
      downColor: "Down colour",
      intensity: "Colour intensity",
      neutralZone: "Neutral zone",
      gap: "Tile spacing",
      cardSize: "Card size",
      clamp: "Saturation point (% change)", alphaMin: "Lightest opacity", alphaMax: "Deepest opacity", cardAspect: "Card aspect ratio",
      copyParams: "Copy parameters", paramsCopied: "Parameters copied", copyFailed: "Copy failed",
    },
    periods: { "1d": "1D", "7d": "7D", "30d": "30D", "90d": "3M", "180d": "6M", "365d": "1Y" },
    languages: { en: "English", ja: "Japanese", ko: "Korean", zhCN: "Simplified Chinese", zhTW: "Traditional Chinese" },
    labels: {
      rank: "Rank", card: "Card", number: "Full number", language: "Language", price: "PSA 10 price", ungradedReference: "Ungraded / RAW reference",
      priceShort: "Price",
      population: "PSA 10 population", populationShort: "Pop",
      marketCap: "Market cap", marketCapShort: "Mkt Cap", trackedSales: "Tracked sales",
      trackedSalesShort: "Sales",
      salesHelp: "Only completed PSA 10 sales captured within CardZ Marketcap tracked coverage.", change: "Change", changeShort: "Chg", asOf: "Data time",
      pricePeriod: "Price period", saleDate: "Last sale", checkedAt: "Last checked",
      awaitingFreshPrice: "Awaiting fresh price",
      viewCard: "Open card profile", close: "Close", story: "Why the market cares", history: "Daily market history",
      dailyPrice: "Reference price", trackedSalesBars: "Tracked sales", salesTrend: "Tracked sales trend", salesTrendShort: "Sales trend", salesTrendColumn: "Trend", imageAlt: "Card artwork",
      noHistory: "Daily price history is still accumulating.", noCards: "No eligible cards are available in this view.", noSales: "No sales recorded", watchStatus: "Watchlist status",
      share: "Share card", shareDone: "Link copied", shareError: "Copy failed — select the address bar", shareImage: "Share image", shareTo: "Share to", shareToStatus: "Story / Status", shareToOther: "Other app", shareToDesktop: "Desktop / blog", shareToPortrait: "Instagram portrait 3:4", shareToWidescreen: "Widescreen 16:9", shareRatioFrame: "As shown", shareQuality: "Quality", shareQualitySlow: "slow",
      printLanguage: "{language} print", setCode: "Set code", finish: "Surface",
      languageFilterAll: "All languages",
      languageFilterAllShort: "All",
      searchPlaceholder: "Search every card on CARDZ",
      searchPlaceholderPokemon: "Search Pokémon",
      searchPlaceholderOnePiece: "Search One Piece",
      searchPlaceholderBox: "Search box name or set code",
      searchLabel: "Search all cards on CARDZ",
      searchLabelPokemon: "Search Pokémon cards",
      searchLabelOnePiece: "Search One Piece cards",
      searchScope: "Search in",
      searchScopeAll: "All",
      searchClear: "Clear",
      sortHighToLow: "High to low",
      sortLowToHigh: "Low to high",
      resultCount: "{shown} / {total}",
      noSearchResults: "Nothing on CARDZ matches that.",
      noSearchResultsBox: "No boxes match this search.",
      searchPopRule: "Listed only at 1,000+ verified PSA 10 copies.",
      searchUnqualified: "A card missing here is below 1,000 verified PSA 10 copies — not a bug.",
      searchUnqualifiedScoped: "Nothing in {scope} matches. Switch the scope, or search all of CARDZ.",
      catalogElsewhere: "On CARDZ, outside this ranking",
      searchModeTitle: "Search results",
      clearSearch: "Clear search",
      rankingRange: "#{from}–#{to}",
      showMore: "Show more",
      showMoreResults: "Show {count} more ({total} total)",
      loadingMore: "Loading…",
      catalogUnavailable: "Site-wide index unavailable right now — showing matches from this page only.",
      pageSizeLabel: "Per page",
      rowCapReached: "Showing at most {count} rows — use › for the next page.",
      rowsShown: "{count} loaded",
      backToTop: "Back to top",
      resultCountShort: "{count} cards",
      resultCountAnnounce: "{count} cards found",
      sortSheetTitle: "Sort & filter",
      sortSheetTrigger: "Sort",
      sortSheetTriggerActive: "Sort · {label}",
      sortDirection: "Direction",
      applyFilters: "Apply",
      resetFilters: "Reset",
      removeFilter: "Remove {filter}",
      searchAllSite: "Search all of CARDZ for “{query}” →",
      sortBy: "Sort by",
      currency: "Currency",
      currencyRegionAsia: "Asia-Pacific",
      currencyRegionAmericas: "Americas",
      currencyRegionEurope: "Europe",
      currencyRegionMea: "Middle East & Africa",
      upDownGreen: "Gains shown in green",
      upDownRed: "Gains shown in red",
    },
    theme: { dark: "Dark mode", light: "Light mode" },
    share: { done: "Image ready", error: "Export failed — try again" },
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
        { term: "Reference price", detail: "The most recent completed PSA 10 sale captured inside CardZ Marketcap tracked coverage. Lots are unitised down to a single card, and a sale priced far outside its own recent range is rejected in favour of the next most recent one." },
        { term: "Population", detail: "The verified PSA 10 population for that exact printing — language, set, collector number and parallel are never merged across printings." },
        { term: "Gaps", detail: "A card with insufficient data coverage in the window is marked as accumulating rather than being given a filled-in number. A missing value stays missing, never zero." },
      ],
      updated: "Updated",
      byline: "Compiled and reviewed by the CardZ Marketcap Editorial desk.",
      anchorSwitched: "Historical anchor: earlier reference series",
    },
    status: { accumulating: "Accumulating", stale: "Stale", unavailable: "Not available" },
    footer: "CardZ Marketcap. Art market intelligence for collectible cards.",
    skipToContent: "Skip to content",
    notFound: { title: "Page not found", body: "This card, box or page is not on the board.", back: "Back to the market" },
    errorPage: { title: "Something went wrong", body: "The board could not be drawn. Try again or head back to the market.", retry: "Try again" },
  },
  "zh-TW": {
    nav: { all: "TCG 市場", pokemon: "寶可夢", onePiece: "海賊王", watchlist: "觀察名單", box: "原盒", previousPage: "上一頁", nextPage: "下一頁" },
    pageTitles: {
      methodology: "集換式卡牌 市值計算方法：PSA 10 價格 × 鑑定數量",
      about: "關於 CardZ Marketcap：鑑定卡市場指數",
      faq: "寶可夢卡牌 市值常見問題：PSA 10 市值解答",
      glossary: "PSA 10 市值名詞解釋：鑑定卡指數用語",
      data: "PSA 10 市值資料與 API：以 JSON 取得排行",
    },
    pageDescriptions: {
      methodology: "CardZ Marketcap 如何計算集換式卡牌市值：以 PSA 10 參考價格乘上已確認的 PSA 10 鑑定數量，並說明哪些資料我們刻意留空。",
      about: "CardZ Marketcap 是每日更新的鑑定卡市場指數，依 PSA 10 市值為寶可夢與海賊王卡牌排名。這裡說明收錄範圍、計算方式與製作團隊。",
      faq: "關於寶可夢卡牌市值與 PSA 10 市值的問答：這個數字代表什麼、價格與鑑定數量從何而來，以及它不代表什麼。",
      glossary: "鑑定卡市場指數的名詞解釋：PSA 10 市值、鑑定數量報告、Gem Rate、參考價格，以及排行中使用的其他用語。",
      data: "以公開唯讀 API 提供 JSON 格式的 PSA 10 市值排行。內含端點、欄位定義、更新頻率、授權條款與引用格式。",
    },
    footerNav: {
      heading: "探索",
      rankings: "排行榜",
      marketReport: "市場報告",
      methodology: "計算方法",
      about: "關於我們",
      faq: "常見問題",
      glossary: "名詞解釋",
      data: "資料與 API",
    },
    boxHero: {
      eyebrow: "BOX MARKET",
      title: "未開封原盒 · 成交價優先",
      body: "原盒參考價以實際成交為準，掛牌價只作參考。",
    },
    box: {
      groupAll: "全部",
      groups: { optcg: "海賊王", ptcg: "寶可夢" },
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
      showMore: "顯示更多（仲有 {count} 個）",
      printWaves: { "1st": "初版", wave1: "初版", wave2: "再版", unlimited: "無限版", reprint: "再版" },
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
      byline: "由 CardZ Marketcap Editorial 編算及覆核。",
    },
    hero: {
      eyebrow: "CARDZ MARKET INDEX",
      title: "寶可夢卡牌 · 集換式卡牌市值 — PSA 10 指數",
      body: "以藝術價值為起點，透過經核實的身份、PSA 10 可流通供應及現時價格，將每張卡讀成一個市值。",
    },
    pokemonHero: {
      eyebrow: "寶可夢市場",
      title: "寶可夢卡牌市值排行（PSA 10）",
      body: "按已核實印刷版本的 PSA 10 市值排列：現時 PSA 10 供應與價格，沒有虛構數字。",
    },
    onePieceHero: {
      eyebrow: "海賊王市場",
      title: "海賊王卡牌市值排行（PSA 10）",
      body: "按已核實印刷版本的 PSA 10 市值排列：現時 PSA 10 供應與價格，沒有虛構數字。",
    },
    intro: {
      definition: "CardZ Marketcap 是鑑定收藏卡的每日市值指數：每張卡的市值 = PSA 10 參考價 × 已核實 PSA 10 鑑定數量，涵蓋寶可夢與海賊王卡牌，由高至低排列。",
      asOf: "本頁數據截至 {date}。",
      summary: "本頁列出的卡牌合計 PSA 10 市值為 {total}，由 {topName}（{topCap}）領先；7 日價格升幅最大的是 {moverName}（{moverPct}）。",
      disambiguation: "CardZ Marketcap 是鑑定卡的資料指數，並非加密貨幣，亦不是 CARDS 代幣，沒有 CardZ 代號。",
      about: "關於這個指數 · 截至 {date}",
    },
    seo: {
      home: {
        title: "集換式卡牌市值：PSA 10 即時指數",
        description: "集換式卡牌市值排行，涵蓋寶可夢與海賊王，以 PSA 10 參考價 × 已核實 PSA 10 鑑定數量計算，每日更新。資料來自 CardZ Marketcap。",
      },
      pokemon: {
        title: "寶可夢卡牌市值：PSA 10 排行",
        description: "寶可夢卡牌市值排行，以 PSA 10 參考價 × 已核實 PSA 10 鑑定數量計算，收錄最具價值的鑑定卡，每日更新。資料來自 CardZ Marketcap。",
      },
      onePiece: {
        title: "海賊王卡牌市值：PSA 10 排行",
        description: "海賊王卡牌市值排行，以 PSA 10 參考價 × 已核實 PSA 10 鑑定數量計算，收錄最具價值的鑑定卡，每日更新。資料來自 CardZ Marketcap。",
      },
      dataset: {
        name: "CardZ Marketcap 鑑定集換式卡牌市值指數",
        description: "鑑定寶可夢與海賊王集換式卡牌的每日市值指數，以 PSA 10 參考價乘以已核實 PSA 10 鑑定數量計算。",
      },
    },
    watchlistHero: {
      eyebrow: "市場觀察",
  title: "第 101 位起 · 持續觀察",
      body: "緊貼前百名之外嘅卡牌，追蹤價格時效、供應同需求。",
    },
    heatmap: {
      title: "市值前 {count} 熱力圖", rankingTitle: "市值前 {count} 排行", pokemonTitle: "寶可夢市場熱力圖", onePieceTitle: "海賊王市場熱力圖",
      body: "面積代表現時 PSA 10 市值，色彩反映所選期間的價格變化。",
      negative: "下跌", neutral: "資料累積中", positive: "上升", tilesLabel: "顯示格數",
      fullscreen: "全螢幕展示", exitFullscreen: "離開全螢幕",
      customize: "自訂熱力圖", customizeTitle: "熱力圖設定", resetDefault: "恢復預設",
      upColor: "上升顏色", downColor: "下跌顏色", intensity: "色彩強度", neutralZone: "中立區", gap: "格子間距", cardSize: "卡牌大小",
      clamp: "飽和點（漲跌 %）", alphaMin: "最淺透明度", alphaMax: "最深透明度", cardAspect: "卡牌長寬比",
      copyParams: "複製參數", paramsCopied: "已複製參數", copyFailed: "複製失敗",
    },
    periods: { "1d": "1D", "7d": "7D", "30d": "30D", "90d": "3M", "180d": "6M", "365d": "1Y" },
    languages: { en: "英文", ja: "日文", ko: "韓文", zhCN: "簡體中文", zhTW: "繁體中文" },
    labels: {
      rank: "排名", card: "卡牌", number: "完整編號", language: "語言", price: "PSA 10 價格", ungradedReference: "未評級／RAW 參考價",
      priceShort: "價格",
      population: "PSA 10 數量", populationShort: "數量",
      marketCap: "市值", marketCapShort: "市值", trackedSales: "已追蹤成交額",
      trackedSalesShort: "成交",
      salesHelp: "只包括 CardZ Marketcap 追蹤範圍內捕捉到的 PSA 10 完成成交。", change: "升跌", changeShort: "升跌", asOf: "資料時間",
      pricePeriod: "價格期數", saleDate: "成交日", checkedAt: "最近檢查",
      awaitingFreshPrice: "等待新鮮價格",
      viewCard: "查看卡牌詳情", close: "關閉", story: "市場為何追捧", history: "每日市場走勢",
      dailyPrice: "參考價格", trackedSalesBars: "已追蹤成交額", salesTrend: "已追蹤成交額走勢", salesTrendShort: "成交走勢", salesTrendColumn: "走勢", imageAlt: "卡牌圖像",
      noHistory: "每日價格歷史仍在累積。", noCards: "此分類暫時沒有合資格卡牌。", noSales: "無成交紀錄", watchStatus: "觀察狀態",
      share: "分享卡牌", shareDone: "已複製連結", shareError: "複製失敗，請手動複製網址", shareImage: "分享圖片", shareTo: "分享去邊", shareToStatus: "限時動態／狀態", shareToOther: "其他 App", shareToDesktop: "電腦／網誌", shareToPortrait: "IG 直向 3:4", shareToWidescreen: "橫向 16:9", shareRatioFrame: "跟畫面", shareQuality: "清晰度", shareQualitySlow: "較慢",
      printLanguage: "{language}版", setCode: "系列代碼", finish: "卡面",
      languageFilterAll: "全部語言",
      languageFilterAllShort: "全部",
      searchPlaceholder: "搜尋站內全部卡牌",
      searchPlaceholderPokemon: "搜尋寶可夢",
      searchPlaceholderOnePiece: "搜尋海賊王",
      searchPlaceholderBox: "搜尋盒名或系列代碼",
      searchLabel: "搜尋站內全部卡牌",
      searchLabelPokemon: "搜尋寶可夢卡牌",
      searchLabelOnePiece: "搜尋海賊王卡牌",
      searchScope: "搜尋範圍",
      searchScopeAll: "全部",
      searchClear: "清除",
      sortHighToLow: "由高到低",
      sortLowToHigh: "由低到高",
      resultCount: "{shown} / {total}",
      noSearchResults: "站內沒有符合此搜尋的卡牌。",
      noSearchResultsBox: "沒有符合此搜尋的原盒。",
      searchPopRule: "只收錄經核實 PSA 10 達 1,000 張或以上的卡牌。",
      searchUnqualified: "找不到並非故障：該卡經核實的 PSA 10 未夠 1,000 張，尚未收錄。",
      searchUnqualifiedScoped: "在{scope}找不到這張卡。可改搜尋範圍，或返大榜搜全部。",
      catalogElsewhere: "已收錄，但不在此榜",
      searchModeTitle: "搜尋結果",
      clearSearch: "清除搜尋",
      rankingRange: "#{from}–#{to}",
      showMore: "展示更多",
      showMoreResults: "再顯示 {count} 張（共 {total} 張）",
      loadingMore: "載入中…",
      catalogUnavailable: "全站索引暫時載不到，只顯示本頁結果。",
      pageSizeLabel: "每頁",
      rowCapReached: "一次最多顯示 {count} 行，按 › 看下一頁。",
      rowsShown: "已載入 {count}",
      backToTop: "回到頂部",
      resultCountShort: "{count} 張",
      resultCountAnnounce: "{count} 張卡牌",
      sortSheetTitle: "排序與篩選",
      sortSheetTrigger: "排序",
      sortSheetTriggerActive: "排序 · {label}",
      sortDirection: "方向",
      applyFilters: "套用",
      resetFilters: "重設",
      removeFilter: "移除 {filter}",
      searchAllSite: "在全站搜尋「{query}」→",
      sortBy: "排序方式",
      currency: "貨幣",
      currencyRegionAsia: "亞太",
      currencyRegionAmericas: "美洲",
      currencyRegionEurope: "歐洲",
      currencyRegionMea: "中東・非洲",
      upDownGreen: "紅跌綠升",
      upDownRed: "紅升綠跌",
    },
    theme: { dark: "深色模式", light: "淺色模式" },
    share: { done: "圖片已匯出", error: "匯出失敗，請再試" },
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
        { term: "參考價", detail: "取自 CardZ Marketcap 追蹤範圍內已完成的 PSA 10 成交：先按張數還原單價，價格離自己近期區間太遠嗰單會被剔走，然後採用最近一單成交價。" },
        { term: "存世數量", detail: "該一個印刷版本經核實的 PSA 10 數量。語言、系列、卡號與平行版本不會混為一談。" },
        { term: "缺口", detail: "期間內資料覆蓋不足的卡會標示為資料累積中，而不是填一個數上去；缺失的數值永遠保持缺失，不會當作零。" },
      ],
      updated: "更新",
      byline: "由 CardZ Marketcap Editorial 編算及覆核。",
      anchorSwitched: "歷史錨點：另一組參考序列",
    },
    status: { accumulating: "資料累積中", stale: "資料已逾時", unavailable: "暫無資料" },
    footer: "CardZ Marketcap，收藏卡牌藝術市場情報。",
    skipToContent: "跳至主要內容",
    notFound: { title: "找不到頁面", body: "這張卡牌、原盒或頁面不在榜上。", back: "返回市場" },
    errorPage: { title: "發生錯誤", body: "榜單暫時無法載入。請再試一次，或返回市場。", retry: "再試一次" },
  },
  "zh-CN": {
    nav: { all: "TCG 市场", pokemon: "宝可梦", onePiece: "海贼王", watchlist: "观察名单", box: "原盒", previousPage: "上一页", nextPage: "下一页" },
    pageTitles: {
      methodology: "集换式卡牌 市值计算方法：PSA 10 价格 × 鉴定数量",
      about: "关于 CardZ Marketcap：鉴定卡市值指数",
      faq: "宝可梦卡牌 市值常见问题：PSA 10 市值解答",
      glossary: "PSA 10 市值名词解释：鉴定卡指数用语",
      data: "PSA 10 市值数据与 API：以 JSON 获取排行",
    },
    pageDescriptions: {
      methodology: "CardZ Marketcap 如何计算集换式卡牌市值：以 PSA 10 参考价格乘以已核实的 PSA 10 鉴定数量，并说明哪些数据我们刻意留空。",
      about: "CardZ Marketcap 是每日更新的集换式卡牌市值指数，按 PSA 10 市值为宝可梦与海贼王卡牌排名。这里说明收录范围、计算方式与制作团队。",
      faq: "关于宝可梦卡牌市值与 PSA 10 市值的问答：这个数字代表什么、价格与鉴定数量从何而来，以及它不代表什么。",
      glossary: "鉴定卡市值指数的名词解释：PSA 10 市值、鉴定数量报告、Gem Rate、参考价格，以及排行中使用的其他用语。",
      data: "以公开只读 API 提供 JSON 格式的 PSA 10 市值排行。内含端点、字段定义、更新频率、授权条款与引用格式。",
    },
    footerNav: {
      heading: "探索",
      rankings: "排行榜",
      marketReport: "市场报告",
      methodology: "计算方法",
      about: "关于我们",
      faq: "常见问题",
      glossary: "名词解释",
      data: "数据与 API",
    },
    boxHero: {
      eyebrow: "BOX MARKET",
      title: "未开封原盒 · 成交价优先",
      body: "原盒参考价以实际成交为准，挂牌价仅作参考。",
    },
    box: {
      groupAll: "全部",
      groups: { optcg: "海贼王", ptcg: "宝可梦" },
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
      showMore: "显示更多（还有 {count} 个）",
      printWaves: { "1st": "初版", wave1: "初版", wave2: "再版", unlimited: "无限版", reprint: "再版" },
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
      byline: "由 CardZ Marketcap Editorial 编算及复核。",
    },
    hero: {
      eyebrow: "CARDZ MARKET INDEX", title: "宝可梦卡牌 · 集换式卡牌市值 — PSA 10 指数",
      body: "以艺术价值为起点，通过经核实的身份、PSA 10 可流通供应及当前价格，把每张卡读成一个市值。",
    },
    pokemonHero: {
      eyebrow: "宝可梦市场", title: "宝可梦卡牌市值排行（PSA 10）", body: "按已核实印刷版本的 PSA 10 市值排列：当前 PSA 10 供应与价格，没有虚构数字。",
    },
    onePieceHero: {
      eyebrow: "海贼王市场", title: "海贼王卡牌市值排行（PSA 10）", body: "按已核实印刷版本的 PSA 10 市值排列：当前 PSA 10 供应与价格，没有虚构数字。",
    },
    intro: {
      definition: "CardZ Marketcap 是评级收藏卡的每日市值指数：每张卡的市值 = PSA 10 参考价 × 已核实 PSA 10 评级数量，涵盖宝可梦与海贼王卡牌，由高至低排列。",
      asOf: "本页数据截至 {date}。",
      summary: "本页列出的卡牌合计 PSA 10 市值为 {total}，由 {topName}（{topCap}）领先；7 日价格涨幅最大的是 {moverName}（{moverPct}）。",
      disambiguation: "CardZ Marketcap 是评级卡的数据指数，并非加密货币，也不是 CARDS 代币，没有 CardZ 代号。",
      about: "关于这个指数 · 截至 {date}",
    },
    seo: {
      home: {
        title: "集换式卡牌市值：PSA 10 实时指数",
        description: "集换式卡牌市值排行，涵盖宝可梦与海贼王，以 PSA 10 参考价 × 已核实 PSA 10 鉴定数量计算，每日更新。数据来自 CardZ Marketcap。",
      },
      pokemon: {
        title: "宝可梦卡牌市值：PSA 10 排行",
        description: "宝可梦卡牌市值排行，以 PSA 10 参考价 × 已核实 PSA 10 鉴定数量计算，收录最具价值的鉴定卡，每日更新。数据来自 CardZ Marketcap。",
      },
      onePiece: {
        title: "海贼王卡牌市值：PSA 10 排行",
        description: "海贼王卡牌市值排行，以 PSA 10 参考价 × 已核实 PSA 10 鉴定数量计算，收录最具价值的鉴定卡，每日更新。数据来自 CardZ Marketcap。",
      },
      dataset: {
        name: "CardZ Marketcap 评级集换式卡牌市值指数",
        description: "评级宝可梦与海贼王集换式卡牌的每日市值指数，以 PSA 10 参考价乘以已核实 PSA 10 评级数量计算。",
      },
    },
    watchlistHero: {
      eyebrow: "市场观察", title: "第 101 位起 · 持续观察", body: "紧随前百名之外的卡牌，追踪价格时效、供应与需求。",
    },
    heatmap: {
      title: "市值前 {count} 热力图", rankingTitle: "市值前 {count} 排行", pokemonTitle: "宝可梦市场热力图", onePieceTitle: "海贼王市场热力图",
      body: "面积代表当前 PSA 10 市值，色彩反映所选期间的价格变化。",
      negative: "下跌", neutral: "数据累积中", positive: "上涨", tilesLabel: "显示格数",
      fullscreen: "全屏展示", exitFullscreen: "退出全屏",
      customize: "自定义热力图", customizeTitle: "热力图设置", resetDefault: "恢复默认",
      upColor: "上涨颜色", downColor: "下跌颜色", intensity: "色彩强度", neutralZone: "中立区", gap: "格子间距", cardSize: "卡牌大小",
      clamp: "饱和点（涨跌 %）", alphaMin: "最浅透明度", alphaMax: "最深透明度", cardAspect: "卡牌长宽比",
      copyParams: "复制参数", paramsCopied: "已复制参数", copyFailed: "复制失败",
    },
    periods: { "1d": "1D", "7d": "7D", "30d": "30D", "90d": "3M", "180d": "6M", "365d": "1Y" },
    languages: { en: "英文", ja: "日文", ko: "韩文", zhCN: "简体中文", zhTW: "繁体中文" },
    labels: {
      rank: "排名", card: "卡牌", number: "完整编号", language: "语言", price: "PSA 10 价格", ungradedReference: "未评级／RAW 参考价",
      priceShort: "价格",
      population: "PSA 10 数量", populationShort: "数量",
      marketCap: "市值", marketCapShort: "市值", trackedSales: "已追踪成交额",
      trackedSalesShort: "成交",
      salesHelp: "只包括 CardZ Marketcap 追踪范围内捕捉到的 PSA 10 完成成交。", change: "涨跌", changeShort: "涨跌", asOf: "数据时间",
      pricePeriod: "价格期数", saleDate: "成交日", checkedAt: "最近检查",
      awaitingFreshPrice: "等待新鲜价格",
      viewCard: "查看卡牌详情", close: "关闭", story: "市场为何追捧", history: "每日市场走势",
      dailyPrice: "参考价格", trackedSalesBars: "已追踪成交额", salesTrend: "已追踪成交额走势", salesTrendShort: "成交走势", salesTrendColumn: "走势", imageAlt: "卡牌图像",
      noHistory: "每日价格历史仍在累积。", noCards: "此分类暂时没有合资格卡牌。", noSales: "无成交纪录", watchStatus: "观察状态",
      share: "分享卡牌", shareDone: "已复制链接", shareError: "复制失败，请手动复制网址", shareImage: "分享图片", shareTo: "分享到哪里", shareToStatus: "限时动态／状态", shareToOther: "其他 App", shareToDesktop: "电脑／博客", shareToPortrait: "IG 竖版 3:4", shareToWidescreen: "横向 16:9", shareRatioFrame: "跟画面", shareQuality: "清晰度", shareQualitySlow: "较慢",
      printLanguage: "{language}版", setCode: "系列代码", finish: "卡面",
      languageFilterAll: "全部语言",
      languageFilterAllShort: "全部",
      searchPlaceholder: "搜索站内全部卡牌",
      searchPlaceholderPokemon: "搜索宝可梦",
      searchPlaceholderOnePiece: "搜索海贼王",
      searchPlaceholderBox: "搜索盒名或系列代码",
      searchLabel: "搜索站内全部卡牌",
      searchLabelPokemon: "搜索宝可梦卡牌",
      searchLabelOnePiece: "搜索海贼王卡牌",
      searchScope: "搜索范围",
      searchScopeAll: "全部",
      searchClear: "清除",
      sortHighToLow: "由高到低",
      sortLowToHigh: "由低到高",
      resultCount: "{shown} / {total}",
      noSearchResults: "站内没有符合此搜索的卡牌。",
      noSearchResultsBox: "没有符合此搜索的原盒。",
      searchPopRule: "只收录经核实 PSA 10 达 1,000 张或以上的卡牌。",
      searchUnqualified: "找不到并非故障：该卡经核实的 PSA 10 未够 1,000 张，尚未收录。",
      searchUnqualifiedScoped: "在{scope}找不到这张卡。可改搜索范围，或回大榜搜全部。",
      catalogElsewhere: "已收录，但不在此榜",
      searchModeTitle: "搜索结果",
      clearSearch: "清除搜索",
      rankingRange: "#{from}–#{to}",
      showMore: "展示更多",
      showMoreResults: "再显示 {count} 张（共 {total} 张）",
      loadingMore: "加载中…",
      catalogUnavailable: "全站索引暂时加载不到，只显示本页结果。",
      pageSizeLabel: "每页",
      rowCapReached: "一次最多显示 {count} 行，点 › 看下一页。",
      rowsShown: "已加载 {count}",
      backToTop: "回到顶部",
      resultCountShort: "{count} 张",
      resultCountAnnounce: "{count} 张卡牌",
      sortSheetTitle: "排序与筛选",
      sortSheetTrigger: "排序",
      sortSheetTriggerActive: "排序 · {label}",
      sortDirection: "方向",
      applyFilters: "应用",
      resetFilters: "重置",
      removeFilter: "移除 {filter}",
      searchAllSite: "在全站搜索「{query}」→",
      sortBy: "排序方式",
      currency: "货币",
      currencyRegionAsia: "亚太",
      currencyRegionAmericas: "美洲",
      currencyRegionEurope: "欧洲",
      currencyRegionMea: "中东・非洲",
      upDownGreen: "红跌绿升",
      upDownRed: "红升绿跌",
    },
    theme: { dark: "深色模式", light: "浅色模式" },
    share: { done: "图片已导出", error: "导出失败，请重试" },
    methodology: {
      title: "CardZ Marketcap 如何排列市场",
      body: "入选，从来不是理所当然。本指数收录的每一张卡都至少有 1,000 张经核实的 PSA 10；市值即为该存量乘以 PSA 10 参考价。参考价取自追踪范围内经核实的 PSA 10 成交；若窗口内的成交纪录不足，该数字即为参考水平，而非成交均价。真实供应、真实需求，绝不虚构。",
    },
    provenance: {
      kicker: "METHOD & DATA",
      title: "市值数字的计算方法",
      body: "市值＝当前 PSA 10 参考价 × 经核实的 PSA 10 存世数量，每日更新时重新计算。",
      steps: [
        { term: "参考价", detail: "取自 CardZ Marketcap 追踪范围内已完成的 PSA 10 成交：先按张数还原单价，价格离自身近期区间太远的会被剔除，然后采用最近一单成交价。" },
        { term: "存世数量", detail: "该一个印刷版本经核实的 PSA 10 数量。语言、系列、卡号与平行版本不会混为一谈。" },
        { term: "缺口", detail: "期间内数据覆盖不足的卡会标示为数据累积中，而不是填一个数上去；缺失的数值永远保持缺失，不会当作零。" },
      ],
      updated: "更新",
      byline: "由 CardZ Marketcap Editorial 编算及复核。",
      anchorSwitched: "历史锚点：另一组参考序列",
    },
    status: { accumulating: "数据累积中", stale: "数据已过期", unavailable: "暂无数据" },
    footer: "CardZ Marketcap，收藏卡牌艺术市场情报。",
    skipToContent: "跳至主要内容",
    notFound: { title: "找不到页面", body: "这张卡牌、原盒或页面不在榜上。", back: "返回市场" },
    errorPage: { title: "发生错误", body: "榜单暂时无法加载。请再试一次，或返回市场。", retry: "再试一次" },
  },
  ja: {
    nav: { all: "TCG 市場", pokemon: "ポケモン", onePiece: "ワンピース", watchlist: "ウォッチリスト", box: "BOX", previousPage: "前のページ", nextPage: "次のページ" },
    pageTitles: {
      methodology: "トレカ 時価総額の算出方法：PSA 10 価格 × 鑑定枚数",
      about: "CardZ Marketcap とは：鑑定カード時価総額指数",
      faq: "ポケモンカード 時価総額 FAQ：PSA 10 の疑問に回答",
      glossary: "PSA 10 時価総額の用語集：鑑定カード指数の用語",
      data: "PSA 10 時価総額のデータと API：JSON で取得",
    },
    pageDescriptions: {
      methodology: "CardZ Marketcap がトレカ時価総額を算出する方法：PSA 10 参考価格に確認済みの PSA 10 鑑定枚数を掛けます。あえて空欄のままにする箇所も説明します。",
      about: "CardZ Marketcap は毎日更新される鑑定カード時価総額指数です。ポケモンカードとワンピースカードを PSA 10 時価総額で順位付けしています。対象範囲と運営者を説明します。",
      faq: "ポケモンカード時価総額と PSA 10 時価総額についての質問と回答：この数値が何を示すのか、価格と鑑定枚数の出どころ、そして何ではないのか。",
      glossary: "鑑定カード指数の用語集：PSA 10 時価総額、ポピュレーションレポート、ジェムレート、参考価格など、ランキングで使う用語を定義します。",
      data: "公開の読み取り専用 API で PSA 10 時価総額ランキングを JSON 提供。エンドポイント、フィールド定義、更新頻度、ライセンス、引用形式を掲載。",
    },
    footerNav: {
      heading: "サイト内リンク",
      rankings: "ランキング",
      marketReport: "市場レポート",
      methodology: "算出方法",
      about: "運営について",
      faq: "よくある質問",
      glossary: "用語集",
      data: "データと API",
    },
    boxHero: {
      eyebrow: "BOX 市場",
      title: "未開封 BOX · 成約価格を優先",
      body: "BOX参考価格は実際の成約が基準。出品価格は参考値です。",
    },
    box: {
      groupAll: "すべて",
      groups: { optcg: "ワンピース", ptcg: "ポケモン" },
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
      empty: "BOX 市場データを準備中です。",
      showMore: "もっと見る（残り {count} 件）",
      printWaves: { "1st": "初版", wave1: "初版", wave2: "再販", unlimited: "アンリミテッド", reprint: "再販" },
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
      byline: "CardZ Marketcap Editorial が集計・確認しています。",
    },
    hero: {
      eyebrow: "CARDZ MARKET INDEX", title: "ポケモンカード・トレカ時価総額 — PSA 10 指数",
      body: "アートの価値を起点に、確認済みのカード情報と PSA 10 の流通供給、現在価格から一枚ごとの時価総額を読み解きます。",
    },
    pokemonHero: {
      eyebrow: "ポケモン市場", title: "ポケモンカード 時価総額ランキング（PSA 10）", body: "確認済みの印刷版を PSA 10 時価総額で順位付け。現在の PSA 10 供給と価格だけを使い、数字は作りません。",
    },
    onePieceHero: {
      eyebrow: "ワンピース市場", title: "ワンピースカード 時価総額ランキング（PSA 10）", body: "確認済みの印刷版を PSA 10 時価総額で順位付け。現在の PSA 10 供給と価格だけを使い、数字は作りません。",
    },
    intro: {
      definition: "CardZ Marketcap は鑑定済みコレクションカードの日次時価総額指数です。各カードの時価総額は PSA 10 参考価格 × 確認済み PSA 10 鑑定枚数で算出し、ポケモンカードとワンピースカードを上位から並べます。",
      asOf: "本ページの数値は {date} 時点です。",
      summary: "掲載カードの PSA 10 時価総額は合計 {total}。首位は {topName}（{topCap}）、7日間の価格変動が最大なのは {moverName}（{moverPct}）です。",
      disambiguation: "CardZ Marketcap は鑑定カードのデータ指数です。暗号資産でも CARDS トークンでもなく、CardZ のティッカーは存在しません。",
      about: "この指数について · {date} 時点",
    },
    seo: {
      home: {
        title: "トレカ時価総額：PSA 10 ライブ指数",
        description: "ポケモンとワンピースを網羅したトレカ時価総額ランキング。PSA 10 参考価格 × 確認済み PSA 10 鑑定枚数で算出し、毎日更新しています。提供：CardZ Marketcap。",
      },
      pokemon: {
        title: "ポケモンカード時価総額：PSA 10 ランキング",
        description: "ポケモンカード時価総額ランキング。PSA 10 参考価格 × 確認済み PSA 10 鑑定枚数で算出し、価値の高い鑑定済みカードを毎日更新。提供：CardZ Marketcap。",
      },
      onePiece: {
        title: "ワンピースカード時価総額：PSA 10 ランキング",
        description: "ワンピースカード時価総額ランキング。PSA 10 参考価格 × 確認済み PSA 10 鑑定枚数で算出し、価値の高い鑑定済みカードを毎日更新。提供：CardZ Marketcap。",
      },
      dataset: {
        name: "CardZ Marketcap 鑑定トレーディングカード時価総額指数",
        description: "鑑定済みのポケモンおよびワンピースのトレーディングカードを対象とした日次時価総額指数。PSA 10 参考価格に確認済み PSA 10 鑑定枚数を掛けて算出。",
      },
    },
    watchlistHero: {
      eyebrow: "マーケットウォッチ", title: "101位以降 · 継続ウォッチ", body: "トップ 100圏外のカードの価格鮮度・供給・需要を追跡。",
    },
    heatmap: {
      title: "時価総額 TOP {count}", rankingTitle: "時価総額トップ {count}", pokemonTitle: "ポケモン TOP {count}", onePieceTitle: "ワンピース TOP {count}",
      body: "面積は現在の PSA 10 時価総額、色は選択期間の価格変化を表します。",
      negative: "下落", neutral: "集計中", positive: "上昇", tilesLabel: "表示数",
      fullscreen: "フルスクリーン表示", exitFullscreen: "フルスクリーンを終了",
      customize: "ヒートマップをカスタマイズ", customizeTitle: "ヒートマップ設定", resetDefault: "デフォルトに戻す",
      upColor: "上昇カラー", downColor: "下落カラー", intensity: "色の強度", neutralZone: "ニュートラルゾーン", gap: "タイル間隔", cardSize: "カードサイズ",
      clamp: "飽和点（変動率 %）", alphaMin: "最も薄い不透明度", alphaMax: "最も濃い不透明度", cardAspect: "カードの縦横比",
      copyParams: "パラメータをコピー", paramsCopied: "パラメータをコピーしました", copyFailed: "コピーに失敗しました",
    },
    periods: { "1d": "1D", "7d": "7D", "30d": "30D", "90d": "3M", "180d": "6M", "365d": "1Y" },
    languages: { en: "英語", ja: "日本語", ko: "韓国語", zhCN: "簡体中国語", zhTW: "繁体中国語" },
    labels: {
      rank: "順位", card: "カード", number: "完全な番号", language: "言語", price: "PSA 10 価格", ungradedReference: "未鑑定／RAW 参考価格",
      priceShort: "価格",
      population: "PSA 10 枚数", populationShort: "枚数",
      marketCap: "時価総額", marketCapShort: "時価総額", trackedSales: "追跡成約額",
      trackedSalesShort: "成約",
      salesHelp: "CardZ Marketcap の追跡範囲で確認できた PSA 10 の成約のみを含みます。", change: "変動", changeShort: "変動", asOf: "データ時刻",
      pricePeriod: "価格期", saleDate: "成約日", checkedAt: "最終確認",
      awaitingFreshPrice: "新しい価格を待機中",
      viewCard: "カード詳細を見る", close: "閉じる", story: "市場で支持される理由", history: "日次市場推移",
      dailyPrice: "参考価格", trackedSalesBars: "追跡成約額", salesTrend: "追跡成約額の推移", salesTrendShort: "成約推移", salesTrendColumn: "推移", imageAlt: "カード画像",
      noHistory: "日次価格履歴を蓄積しています。", noCards: "この表示には適格カードがありません。", noSales: "成約記録なし", watchStatus: "観察ステータス",
      share: "カードを共有", shareDone: "リンクをコピーしました", shareError: "コピーに失敗しました。URL を手動でコピーしてください", shareImage: "画像をシェア", shareTo: "シェア先", shareToStatus: "ストーリー／ステータス", shareToOther: "その他のアプリ", shareToDesktop: "PC・ブログ", shareToPortrait: "Instagram 縦 3:4", shareToWidescreen: "ワイド 16:9", shareRatioFrame: "画面どおり", shareQuality: "画質", shareQualitySlow: "低速",
      printLanguage: "{language}版", setCode: "セットコード", finish: "表面",
      languageFilterAll: "すべての言語",
      languageFilterAllShort: "すべて",
      searchPlaceholder: "掲載中の全カードを検索",
      searchPlaceholderPokemon: "ポケモンを検索",
      searchPlaceholderOnePiece: "ワンピースを検索",
      searchPlaceholderBox: "ボックス名またはセットコードで検索",
      searchLabel: "掲載中の全カードを検索",
      searchLabelPokemon: "ポケモンカードを検索",
      searchLabelOnePiece: "ワンピースカードを検索",
      searchScope: "検索対象",
      searchScopeAll: "すべて",
      searchClear: "クリア",
      sortHighToLow: "高い順",
      sortLowToHigh: "低い順",
      resultCount: "{shown} / {total}",
      noSearchResults: "一致するカードはありません。",
      noSearchResultsBox: "この検索に一致するボックスはありません。",
      searchPopRule: "確認済み PSA 10 が 1,000 枚以上のカードのみ掲載。",
      searchUnqualified: "見つからないのは不具合ではなく、確認済み PSA 10 が 1,000 枚に届いていないためです。",
      searchUnqualifiedScoped: "{scope}には一致するカードがありません。対象を切り替えるか、全体から検索してください。",
      catalogElsewhere: "掲載中・このランキング外",
      searchModeTitle: "検索結果",
      clearSearch: "検索をクリア",
      rankingRange: "#{from}–#{to}",
      showMore: "さらに表示",
      showMoreResults: "さらに {count} 件表示（全 {total} 件）",
      loadingMore: "読み込み中…",
      catalogUnavailable: "サイト全体の索引を読み込めません。このページ内の該当分のみ表示しています。",
      pageSizeLabel: "表示件数",
      rowCapReached: "一度に表示できるのは最大 {count} 件です。続きは › で次のページへ。",
      rowsShown: "{count} 件読込済",
      backToTop: "先頭に戻る",
      resultCountShort: "{count} 件",
      resultCountAnnounce: "カード {count} 件",
      sortSheetTitle: "並べ替えと絞り込み",
      sortSheetTrigger: "並べ替え",
      sortSheetTriggerActive: "並べ替え · {label}",
      sortDirection: "並び順",
      applyFilters: "適用",
      resetFilters: "リセット",
      removeFilter: "{filter} を解除",
      searchAllSite: "サイト全体で「{query}」を検索 →",
      sortBy: "並べ替え",
      currency: "通貨",
      currencyRegionAsia: "アジア太平洋",
      currencyRegionAmericas: "米州",
      currencyRegionEurope: "欧州",
      currencyRegionMea: "中東・アフリカ",
      upDownGreen: "上昇＝緑",
      upDownRed: "上昇＝赤",
    },
    theme: { dark: "ダークモード", light: "ライトモード" },
    share: { done: "画像を書き出しました", error: "書き出しに失敗しました" },
    methodology: {
      title: "CardZ Marketcap の市場ランキング方法",
      body: "掲載は、与えられるものではなく獲得するもの。この指数のカードはすべて確認済み PSA 10 が 1,000 枚以上あり、時価総額はその流通量に PSA 10 参考価格を掛けた値です。参考価格は、当社の追跡範囲内で確認された PSA 10 取引から算出します。対象期間の取引記録が十分でない場合、その数値は取引平均ではなく参考水準として示されます。実在する供給と需要、それ以外は作りません。",
    },
    provenance: {
      kicker: "METHOD & DATA",
      title: "マーケットキャップの算出方法",
      body: "マーケットキャップは、現在の PSA 10 参考価格に確認済み PSA 10 の現存枚数を掛けた値で、毎日の更新ごとに再計算されます。",
      steps: [
        { term: "参考価格", detail: "CardZ Marketcap の追跡範囲で確認できた PSA 10 の成約から取得します。まとめ売りは1枚あたりに換算し、直近の水準から大きく外れた成約を除いたうえで、最新の成約価格を用います。" },
        { term: "現存枚数", detail: "その印刷版に対する確認済み PSA 10 の枚数です。言語・セット・カード番号・パラレルを混在させることはありません。" },
        { term: "欠損", detail: "対象期間のデータカバレッジが不足しているカードは、数値を埋めずに集計中と表示します。欠損値は常に欠損のままで、ゼロとしては扱いません。" },
      ],
      updated: "更新",
      byline: "CardZ Marketcap Editorial が集計・確認しています。",
      anchorSwitched: "履歴の基準：別の参照系列",
    },
    status: { accumulating: "集計中", stale: "更新待ち", unavailable: "データなし" },
    footer: "CardZ Marketcap。コレクティブルカードのアート市場情報。",
    skipToContent: "本文へスキップ",
    notFound: { title: "ページが見つかりません", body: "このカード、BOX、またはページはボードにありません。", back: "マーケットに戻る" },
    errorPage: { title: "エラーが発生しました", body: "ボードを表示できませんでした。もう一度お試しいただくか、マーケットに戻ってください。", retry: "もう一度試す" },
  },
  ko: {
    nav: { all: "TCG 마켓", pokemon: "포켓몬", onePiece: "원피스", watchlist: "관심 목록", box: "BOX", previousPage: "이전 페이지", nextPage: "다음 페이지" },
    pageTitles: {
      methodology: "트레이딩 카드 시가총액 산출 방법: PSA 10 가격 × 개체수",
      about: "CardZ Marketcap 소개: 등급 카드 시가총액 지수",
      faq: "포켓몬 카드 시가총액 FAQ: PSA 10 질문 정리",
      glossary: "PSA 10 시가총액 용어집: 등급 카드 지수 용어",
      data: "PSA 10 시가총액 데이터와 API: JSON으로 제공",
    },
    pageDescriptions: {
      methodology: "CardZ Marketcap이 트레이딩 카드 시가총액을 산출하는 방법: PSA 10 기준가에 검증된 PSA 10 개체수를 곱합니다. 비워 두는 항목도 함께 설명합니다.",
      about: "CardZ Marketcap은 매일 갱신되는 등급 카드 시가총액 지수입니다. 포켓몬 카드와 원피스 카드를 PSA 10 시가총액으로 순위를 매기며, 수록 범위와 운영 주체를 설명합니다.",
      faq: "포켓몬 카드 시가총액과 PSA 10 시가총액에 대한 질문과 답변: 이 수치가 무엇을 뜻하는지, 가격과 개체수의 출처는 어디인지, 그리고 무엇이 아닌지.",
      glossary: "등급 카드 지수 용어집: PSA 10 시가총액, 개체수 리포트, 젬 레이트, 기준가 등 순위에 사용하는 용어를 정의합니다.",
      data: "공개 읽기 전용 API로 PSA 10 시가총액 순위를 JSON으로 제공합니다. 엔드포인트, 필드 정의, 갱신 주기, 라이선스, 인용 형식을 안내합니다.",
    },
    footerNav: {
      heading: "둘러보기",
      rankings: "랭킹",
      marketReport: "시장 리포트",
      methodology: "산출 방법",
      about: "소개",
      faq: "자주 묻는 질문",
      glossary: "용어집",
      data: "데이터와 API",
    },
    boxHero: {
      eyebrow: "BOX MARKET",
      title: "미개봉 박스 · 체결가 우선",
      body: "박스 기준 가격은 실제 체결이 기준. 호가는 참고값입니다.",
    },
    box: {
      groupAll: "전체",
      groups: { optcg: "원피스", ptcg: "포켓몬" },
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
      showMore: "더 보기 ({count}개 남음)",
      printWaves: { "1st": "초판", wave1: "초판", wave2: "재판", unlimited: "언리미티드", reprint: "재판" },
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
      byline: "CardZ Marketcap Editorial이 집계하고 검토합니다.",
    },
    hero: {
      eyebrow: "CARDZ MARKET INDEX",
      title: "포켓몬 카드·트레이딩 카드 시가총액 — PSA 10 지수",
      body: "아트의 가치를 출발점으로, 검증된 카드 정보와 PSA 10 유통 공급, 현재 가격으로 카드마다 시가총액을 읽습니다.",
    },
    pokemonHero: {
      eyebrow: "포켓몬 마켓",
      title: "포켓몬 카드 시가총액 순위 (PSA 10)",
      body: "검증된 인쇄판을 PSA 10 시가총액으로 순위화합니다. 현재 PSA 10 공급과 가격만 사용하며 수치를 지어내지 않습니다.",
    },
    onePieceHero: {
      eyebrow: "원피스 마켓",
      title: "원피스 카드 시가총액 순위 (PSA 10)",
      body: "검증된 인쇄판을 PSA 10 시가총액으로 순위화합니다. 현재 PSA 10 공급과 가격만 사용하며 수치를 지어내지 않습니다.",
    },
    intro: {
      definition: "CardZ Marketcap은 등급 수집 카드의 일간 시가총액 지수입니다. 카드별 시가총액은 PSA 10 기준가 × 검증된 PSA 10 개체수로 산출하며, 포켓몬 카드와 원피스 카드를 상위부터 나열합니다.",
      asOf: "이 페이지의 수치는 {date} 기준입니다.",
      summary: "이 페이지에 표시된 카드의 PSA 10 시가총액 합계는 {total}입니다. 1위는 {topName}({topCap}), 7일 가격 변동이 가장 큰 카드는 {moverName}({moverPct})입니다.",
      disambiguation: "CardZ Marketcap은 등급 카드의 데이터 지수입니다. 암호화폐도 CARDS 토큰도 아니며 CardZ 티커는 존재하지 않습니다.",
      about: "이 지수에 대하여 · {date} 기준",
    },
    seo: {
      home: {
        title: "트레이딩 카드 시가총액: PSA 10 지수",
        description: "포켓몬과 원피스를 아우르는 트레이딩 카드 시가총액 순위. PSA 10 기준가 × 검증된 PSA 10 등급 수량으로 산출하며 매일 갱신합니다. 제공: CardZ Marketcap.",
      },
      pokemon: {
        title: "포켓몬 카드 시가총액: PSA 10 순위",
        description: "포켓몬 카드 시가총액 순위. PSA 10 기준가 × 검증된 PSA 10 등급 수량으로 산출한 최고가 등급 카드 목록을 매일 갱신합니다. 제공: CardZ Marketcap.",
      },
      onePiece: {
        title: "원피스 카드 시가총액: PSA 10 순위",
        description: "원피스 카드 시가총액 순위. PSA 10 기준가 × 검증된 PSA 10 등급 수량으로 산출한 최고가 등급 카드 목록을 매일 갱신합니다. 제공: CardZ Marketcap.",
      },
      dataset: {
        name: "CardZ Marketcap 등급 트레이딩 카드 시가총액 지수",
        description: "등급을 받은 포켓몬·원피스 트레이딩 카드의 일간 시가총액 지수. PSA 10 기준가에 검증된 PSA 10 개체수를 곱해 산출한다.",
      },
    },
    watchlistHero: {
      eyebrow: "마켓 워치",
      title: "101위 이후 · 지속 관찰",
      body: "상위 100 밖 카드의 가격 신선도·공급·수요를 추적합니다.",
    },
    heatmap: {
      title: "시가총액 TOP {count}", rankingTitle: "시가총액 상위 {count}", pokemonTitle: "포켓몬 TOP {count}", onePieceTitle: "원피스 TOP {count}",
      body: "면적은 현재 PSA 10 시가총액, 색상은 선택 기간의 가격 변동을 나타냅니다.",
      negative: "하락", neutral: "집계 중", positive: "상승", tilesLabel: "표시 수",
      fullscreen: "전체 화면 표시", exitFullscreen: "전체 화면 종료",
      customize: "히트맵 사용자 정의", customizeTitle: "히트맵 설정", resetDefault: "기본값으로 재설정",
      upColor: "상승 색상", downColor: "하락 색상", intensity: "색상 강도", neutralZone: "중립 구간", gap: "타일 간격", cardSize: "카드 크기",
      clamp: "포화 지점(변동률 %)", alphaMin: "가장 옅은 불투명도", alphaMax: "가장 짙은 불투명도", cardAspect: "카드 가로세로 비율",
      copyParams: "파라미터 복사", paramsCopied: "파라미터를 복사했습니다", copyFailed: "복사 실패",
    },
    periods: { "1d": "1D", "7d": "7D", "30d": "30D", "90d": "3M", "180d": "6M", "365d": "1Y" },
    languages: { en: "영어", ja: "일본어", ko: "한국어", zhCN: "중국어 간체", zhTW: "중국어 번체" },
    labels: {
      rank: "순위", card: "카드", number: "전체 번호", language: "언어", price: "PSA 10 가격", ungradedReference: "미감정／RAW 참고가",
      priceShort: "가격",
      population: "PSA 10 매수", populationShort: "매수",
      marketCap: "시가총액", marketCapShort: "시총", trackedSales: "추적 거래액",
      trackedSalesShort: "거래",
      salesHelp: "CardZ Marketcap 추적 범위에서 확인된 PSA 10 완료 거래만 포함합니다.", change: "등락", changeShort: "등락", asOf: "데이터 시각",
      pricePeriod: "가격 기간", saleDate: "체결일", checkedAt: "최근 확인",
      awaitingFreshPrice: "신선한 가격 대기",
      viewCard: "카드 상세 보기", close: "닫기", story: "시장이 주목하는 이유", history: "일별 시장 추이",
      dailyPrice: "기준 가격", trackedSalesBars: "추적 거래액", salesTrend: "추적 거래액 추이", salesTrendShort: "거래 추이", salesTrendColumn: "추이", imageAlt: "카드 이미지",
      noHistory: "일별 가격 이력을 축적하고 있습니다.", noCards: "이 보기에 적격 카드가 없습니다.", noSales: "거래 기록 없음", watchStatus: "관찰 상태",
      share: "카드 공유", shareDone: "링크 복사됨", shareError: "복사 실패 — 주소창에서 직접 복사하세요", shareImage: "이미지 공유", shareTo: "공유할 곳", shareToStatus: "스토리 / 상태", shareToOther: "다른 앱", shareToDesktop: "PC / 블로그", shareToPortrait: "Instagram 세로 3:4", shareToWidescreen: "와이드 16:9", shareRatioFrame: "화면대로", shareQuality: "화질", shareQualitySlow: "느림",
      printLanguage: "{language}판", setCode: "세트 코드", finish: "표면",
      languageFilterAll: "모든 언어",
      languageFilterAllShort: "전체",
      searchPlaceholder: "사이트 전체 카드 검색",
      searchPlaceholderPokemon: "포켓몬 검색",
      searchPlaceholderOnePiece: "원피스 검색",
      searchPlaceholderBox: "박스 이름 또는 세트 코드로 검색",
      searchLabel: "사이트 전체 카드 검색",
      searchLabelPokemon: "포켓몬 카드 검색",
      searchLabelOnePiece: "원피스 카드 검색",
      searchScope: "검색 범위",
      searchScopeAll: "전체",
      searchClear: "지우기",
      sortHighToLow: "높은 순",
      sortLowToHigh: "낮은 순",
      resultCount: "{shown} / {total}",
      noSearchResults: "일치하는 카드가 없습니다.",
      noSearchResultsBox: "이 검색과 일치하는 박스가 없습니다.",
      searchPopRule: "검증된 PSA 10 1,000장 이상 카드만 수록합니다.",
      searchUnqualified: "찾을 수 없다면 오류가 아니라, 검증된 PSA 10이 1,000장에 미치지 못한 것입니다.",
      searchUnqualifiedScoped: "{scope}에서 찾을 수 없습니다. 범위를 바꾸거나 전체에서 검색하세요.",
      catalogElsewhere: "수록됨 · 이 순위 밖",
      searchModeTitle: "검색 결과",
      clearSearch: "검색 지우기",
      rankingRange: "#{from}–#{to}",
      showMore: "더 보기",
      showMoreResults: "{count}개 더 보기 (총 {total}개)",
      loadingMore: "불러오는 중…",
      catalogUnavailable: "전체 색인을 지금 불러올 수 없어 이 페이지의 결과만 표시합니다.",
      pageSizeLabel: "페이지당",
      rowCapReached: "한 번에 최대 {count}개까지 표시됩니다. 다음 페이지는 › 를 누르세요.",
      rowsShown: "{count}개 로드됨",
      backToTop: "맨 위로",
      resultCountShort: "{count}장",
      resultCountAnnounce: "카드 {count}장",
      sortSheetTitle: "정렬 및 필터",
      sortSheetTrigger: "정렬",
      sortSheetTriggerActive: "정렬 · {label}",
      sortDirection: "정렬 방향",
      applyFilters: "적용",
      resetFilters: "초기화",
      removeFilter: "{filter} 해제",
      searchAllSite: "전체에서 “{query}” 검색 →",
      sortBy: "정렬 기준",
      currency: "통화",
      currencyRegionAsia: "아시아·태평양",
      currencyRegionAmericas: "미주",
      currencyRegionEurope: "유럽",
      currencyRegionMea: "중동·아프리카",
      upDownGreen: "상승=녹색",
      upDownRed: "상승=빨강",
    },
    theme: { dark: "다크 모드", light: "라이트 모드" },
    share: { done: "이미지를 내보냈습니다", error: "내보내기에 실패했습니다" },
    methodology: {
      title: "CardZ Marketcap의 시장 순위 방식",
      body: "수록은 주어지는 것이 아니라 얻어내는 것입니다. 이 지수의 모든 카드는 검증된 PSA 10이 1,000장 이상이며, 시가총액은 그 물량에 PSA 10 기준가를 곱한 값입니다. 기준가는 저희 추적 범위 안에서 검증된 PSA 10 거래로 산출합니다. 해당 기간의 거래 기록이 충분하지 않을 경우 그 수치는 거래 평균이 아니라 참고 수준으로 제시됩니다. 실제 공급과 실제 수요, 그 밖의 것은 만들지 않습니다.",
    },
    provenance: {
      kicker: "METHOD & DATA",
      title: "시가총액 산출 방식",
      body: "시가총액은 현재 PSA 10 기준가에 검증된 PSA 10 현존 수량을 곱한 값이며, 매일 갱신할 때마다 다시 계산합니다.",
      steps: [
        { term: "기준가", detail: "CardZ Marketcap 추적 범위에서 확인된 PSA 10 완료 거래에서 가져옵니다. 묶음 거래는 카드 한 장 기준으로 환산하고, 최근 구간에서 크게 벗어난 거래는 제외한 뒤 가장 최근 체결가를 사용합니다." },
        { term: "현존 수량", detail: "해당 인쇄본에 대해 검증된 PSA 10 수량입니다. 언어, 세트, 카드 번호, 패러렐을 섞지 않습니다." },
        { term: "결측", detail: "해당 기간의 데이터 커버리지가 부족한 카드는 숫자를 채우지 않고 집계 중으로 표시합니다. 결측값은 언제나 결측으로 남으며 0으로 처리하지 않습니다." },
      ],
      updated: "업데이트",
      byline: "CardZ Marketcap Editorial이 집계하고 검토합니다.",
      anchorSwitched: "과거 기준: 다른 참조 계열",
    },
    status: { accumulating: "집계 중", stale: "오래된 데이터", unavailable: "데이터 없음" },
    footer: "CardZ Marketcap. 컬렉터블 카드 아트 마켓 인텔리전스.",
    skipToContent: "본문으로 건너뛰기",
    notFound: { title: "페이지를 찾을 수 없습니다", body: "이 카드, 박스 또는 페이지는 보드에 없습니다.", back: "마켓으로 돌아가기" },
    errorPage: { title: "문제가 발생했습니다", body: "보드를 표시할 수 없습니다. 다시 시도하거나 마켓으로 돌아가세요.", retry: "다시 시도" },
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

// Locale-independent short label for filter chips; unknown codes keep the raw value.
export function localizedCardLanguageShort(language: string): string {
  const raw = (language ?? "").trim();
  const canonical = CARD_LANGUAGE_ALIASES[raw.toLowerCase().replace(/[-_\s]/g, "")];
  return canonical ? cardLanguageShort[canonical] : raw;
}
