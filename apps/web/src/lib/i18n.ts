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
    tilesLabel: string;
    viewRanking: string;
    shareImage: string;
  };
  periods: Record<MarketWindow, string>;
  labels: {
    rank: string;
    card: string;
    number: string;
    language: string;
    price: string;
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
    watchStatus: string;
    share: string;
    shareDone: string;
    shareError: string;
    expandImage: string;
    marketCapHelp: string;
    populationHelp: string;
    priceHelp: string;
  };
  grader: {
    eyebrow: string;
    title: string;
    body: string;
    topGrade: string;
    topGradeShort: string;
    topGradePopulation: string;
    topGradePopulationShort: string;
    totalPopulation: string;
    totalPopulationShort: string;
    populationChange: string;
    populationChangeShort: string;
    liquidity: string;
    marketCapAvailable: string;
    marketCapUnavailable: string;
    marketShare: string;
    names: Record<Grader, string>;
  };
  theme: { dark: string; light: string };
  methodology: { title: string; body: string };
  gradingPulse: {
    eyebrow: string;
    title: string;
    subtitle: string;
    windowCaption: string;
    submissions: string;
    share: string;
  };
  status: Record<"accumulating" | "stale" | "unavailable", string>;
  footer: string;
}

export const copy: Record<Locale, Copy> = {
  en: {
    nav: { all: "TCG Market", pokemon: "Pokémon", onePiece: "One Piece", graders: "Graders", watchlist: "Watchlist" },
    hero: {
      eyebrow: "CARDS MARKET INDEX",
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
      body: "Ranks 101 to 300, monitored for identity, price freshness, supply and demand.",
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
    },
    periods: { "1d": "1d", "7d": "7d", "30d": "30d" },
    labels: {
      rank: "Rank", card: "Card", number: "Full number", language: "Language", price: "PSA 10 price",
      priceShort: "Price",
      population: "PSA 10 population", populationShort: "Pop",
      marketCap: "Market cap", marketCapShort: "Mkt Cap", trackedSales: "Tracked sales",
      trackedSalesShort: "Sales",
      salesHelp: "Only completed PSA 10 sales captured within CARDS tracked coverage.", change: "Change", changeShort: "Chg", asOf: "Data time",
      viewCard: "Open card profile", close: "Close", story: "Why the market cares", history: "Daily market history",
      dailyPrice: "Reference price", trackedSalesBars: "Tracked sales", salesTrend: "Tracked sales trend", salesTrendShort: "Sales trend", imageAlt: "Card artwork",
      noHistory: "Daily price history is still accumulating.", noCards: "No eligible cards are available in this view.", watchStatus: "Eligibility watch",
      share: "Share card", shareDone: "Link copied", shareError: "Copy failed — select the address bar",
      expandImage: "View full-size card",
      marketCapHelp: "PSA 10 price × PSA 10 population — the tradable value of the top-grade supply.",
      populationHelp: "Verified PSA 10 graded copies counted in the registry.",
      priceHelp: "Latest verified PSA 10 sale price in the tracked window.",
    },
    grader: {
      eyebrow: "GRADING SUPPLY",
      title: "{grader} market supply",
      body: "Top-grade population and tracked liquidity are shown without inventing unavailable prices.",
      topGrade: "Top grade", topGradeShort: "Grade", topGradePopulation: "Top-grade population", topGradePopulationShort: "Pop", totalPopulation: "Total graded population", totalPopulationShort: "Total", populationChange: "population change", populationChangeShort: "change",
      liquidity: "Tracked liquidity", marketCapAvailable: "PSA 10 market cap", marketCapUnavailable: "Market cap unavailable", marketShare: "Market share",
      names: { PSA: "PSA", BGS: "BGS", CGC: "CGC", SGC: "SGC", TAG: "TAG" },
    },
    theme: { dark: "Dark mode", light: "Light mode" },
    methodology: {
      title: "How CARDS ranks the market",
      body: "Every card in this index has at least 1,000 verified PSA 10 examples. That floor keeps the ranking tied to real, tradable supply — not thin populations that a handful of sales could move.",
    },
    gradingPulse: {
      eyebrow: "GRADING PULSE",
      title: "Daily grading flow",
      subtitle: "Across the tracked top 100",
      windowCaption: "Change vs prior {period}",
      submissions: "Top-grade population change",
      share: "Share of tracked top-grade population",
    },
    status: { accumulating: "Accumulating", stale: "Stale", unavailable: "Not available" },
    footer: "CARDS Market Cap. Art market intelligence for collectible cards.",
  },
  "zh-TW": {
    nav: { all: "TCG 市場", pokemon: "寶可夢", onePiece: "海賊王", graders: "評級公司", watchlist: "觀察名單" },
    hero: {
      eyebrow: "CARDS 市場指數",
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
      body: "持續觀察第 101 至 300 位卡牌的身份、價格時效、供應及需求。",
    },
    heatmap: {
      title: "市值前 {count} 熱力圖", rankingTitle: "市值前 {count} 排行", pokemonTitle: "寶可夢市場熱力圖", onePieceTitle: "海賊王市場熱力圖",
      body: "面積代表現時 PSA 10 市值，色彩反映所選期間的價格變化。",
      negative: "下跌", neutral: "資料累積中", positive: "上升", count: "張合資格卡牌", tilesLabel: "顯示格數", viewRanking: "查看前 {count}", shareImage: "分享圖片",
    },
    periods: { "1d": "1 日", "7d": "7 日", "30d": "30 日" },
    labels: {
      rank: "排名", card: "卡牌", number: "完整編號", language: "語言", price: "PSA 10 價格",
      priceShort: "價格",
      population: "PSA 10 數量", populationShort: "數量",
      marketCap: "市值", marketCapShort: "市值", trackedSales: "已追蹤成交額",
      trackedSalesShort: "成交",
      salesHelp: "只包括 CARDS 追蹤範圍內捕捉到的 PSA 10 完成成交。", change: "升跌", changeShort: "升跌", asOf: "資料時間",
      viewCard: "查看卡牌詳情", close: "關閉", story: "市場為何追捧", history: "每日市場走勢",
      dailyPrice: "參考價格", trackedSalesBars: "已追蹤成交額", salesTrend: "已追蹤成交額走勢", salesTrendShort: "成交走勢", imageAlt: "卡牌圖像",
      noHistory: "每日價格歷史仍在累積。", noCards: "此分類暫時沒有合資格卡牌。", watchStatus: "入榜觀察",
      share: "分享卡牌", shareDone: "已複製連結", shareError: "複製失敗，請手動複製網址",
      expandImage: "放大檢視卡牌",
      marketCapHelp: "PSA 10 價格乘以已核實的 PSA 10 數量——頂級評分存量的可流通價值。",
      populationHelp: "登記在冊、經核實的 PSA 10 存世數量。",
      priceHelp: "追蹤期內最近一筆經核實的 PSA 10 成交價。",
    },
    grader: {
      eyebrow: "評級供應",
      title: "{grader} 市場供應",
      body: "呈現最高評級數量及已追蹤流動性，不會為缺失價格製造估算。",
      topGrade: "最高評級", topGradeShort: "評級", topGradePopulation: "最高評級數量", topGradePopulationShort: "數量", totalPopulation: "評級總數量", totalPopulationShort: "總數", populationChange: "數量變化", populationChangeShort: "變化",
      liquidity: "已追蹤流動性", marketCapAvailable: "PSA 10 市值", marketCapUnavailable: "暫無市值", marketShare: "市佔",
      names: { PSA: "PSA", BGS: "BGS", CGC: "CGC", SGC: "SGC", TAG: "TAG" },
    },
    theme: { dark: "深色模式", light: "淺色模式" },
    methodology: {
      title: "CARDS 如何排列市場",
      body: "本指數收錄的每一張卡，均至少有 1,000 張經核實的 PSA 10。此門檻確保排名錨定真實、可交易的供應——而非流通量極少、數筆成交即可推動的品種。",
    },
    gradingPulse: {
      eyebrow: "評級動態",
      title: "每日送評流向",
      subtitle: "涵蓋已追蹤前 100 名",
      windowCaption: "較上一{period}的變化",
      submissions: "最高評級數量變化",
      share: "佔已追蹤最高評級總量",
    },
    status: { accumulating: "資料累積中", stale: "資料已逾時", unavailable: "暫無資料" },
    footer: "CARDS Market Cap，收藏卡牌藝術市場情報。",
  },
  "zh-CN": {
    nav: { all: "TCG 市场", pokemon: "宝可梦", onePiece: "海贼王", graders: "评级公司", watchlist: "观察名单" },
    hero: {
      eyebrow: "CARDS 市场指数", title: "收藏卡牌的市场全景",
      body: "以艺术价值为起点，通过经核实的身份、可流通供应及当前价格理解市场。",
    },
    pokemonHero: {
      eyebrow: "宝可梦市场", title: "以流动市场视角理解宝可梦卡牌", body: "按已核实印刷版本、PSA 10 供应及当前价格排列。",
    },
    onePieceHero: {
      eyebrow: "海贼王市场", title: "以流动市场视角理解海贼王卡牌", body: "按已核实印刷版本、PSA 10 供应及当前价格排列。",
    },
    watchlistHero: {
      eyebrow: "市场观察", title: "正在接近领先市场的卡牌", body: "持续观察第 101 至 300 位卡牌的身份、价格时效、供应及需求。",
    },
    heatmap: {
      title: "市值前 {count} 热力图", rankingTitle: "市值前 {count} 排行", pokemonTitle: "宝可梦市场热力图", onePieceTitle: "海贼王市场热力图",
      body: "面积代表当前 PSA 10 市值，色彩反映所选期间的价格变化。",
      negative: "下跌", neutral: "数据累积中", positive: "上涨", count: "张合资格卡牌", tilesLabel: "显示格数", viewRanking: "查看前 {count}", shareImage: "分享图片",
    },
    periods: { "1d": "1 日", "7d": "7 日", "30d": "30 日" },
    labels: {
      rank: "排名", card: "卡牌", number: "完整编号", language: "语言", price: "PSA 10 价格",
      priceShort: "价格",
      population: "PSA 10 数量", populationShort: "数量",
      marketCap: "市值", marketCapShort: "市值", trackedSales: "已追踪成交额",
      trackedSalesShort: "成交",
      salesHelp: "只包括 CARDS 追踪范围内捕捉到的 PSA 10 完成成交。", change: "涨跌", changeShort: "涨跌", asOf: "数据时间",
      viewCard: "查看卡牌详情", close: "关闭", story: "市场为何追捧", history: "每日市场走势",
      dailyPrice: "参考价格", trackedSalesBars: "已追踪成交额", salesTrend: "已追踪成交额走势", salesTrendShort: "成交走势", imageAlt: "卡牌图像",
      noHistory: "每日价格历史仍在累积。", noCards: "此分类暂时没有合资格卡牌。", watchStatus: "入榜观察",
      share: "分享卡牌", shareDone: "已复制链接", shareError: "复制失败，请手动复制网址",
      expandImage: "放大查看卡牌",
      marketCapHelp: "PSA 10 价格乘以已核实的 PSA 10 数量——顶级评级存量的可流通价值。",
      populationHelp: "登记在册、经核实的 PSA 10 存世数量。",
      priceHelp: "追踪期内最近一笔经核实的 PSA 10 成交价。",
    },
    grader: {
      eyebrow: "评级供应", title: "{grader} 市场供应", body: "呈现最高评级数量及已追踪流动性，不会为缺失价格制造估算。",
      topGrade: "最高评级", topGradeShort: "评级", topGradePopulation: "最高评级数量", topGradePopulationShort: "数量", totalPopulation: "评级总数量", totalPopulationShort: "总数", populationChange: "数量变化", populationChangeShort: "变化",
      liquidity: "已追踪流动性", marketCapAvailable: "PSA 10 市值", marketCapUnavailable: "暂无市值", marketShare: "市占",
      names: { PSA: "PSA", BGS: "BGS", CGC: "CGC", SGC: "SGC", TAG: "TAG" },
    },
    theme: { dark: "深色模式", light: "浅色模式" },
    methodology: {
      title: "CARDS 如何排列市场",
      body: "本指数收录的每一张卡都至少有 1,000 张经核实的 PSA 10。这一门槛确保排名锚定真实、可交易的供应——而非流通量极少、几笔成交即可推动的品种。",
    },
    gradingPulse: {
      eyebrow: "评级动态",
      title: "每日送评流向",
      subtitle: "覆盖已追踪前 100 名",
      windowCaption: "较上一{period}的变化",
      submissions: "最高评级数量变化",
      share: "占已追踪最高评级总量",
    },
    status: { accumulating: "数据累积中", stale: "数据已过期", unavailable: "暂无数据" },
    footer: "CARDS Market Cap，收藏卡牌艺术市场情报。",
  },
  ja: {
    nav: { all: "TCG 市場", pokemon: "ポケモン", onePiece: "ワンピース", graders: "鑑定会社", watchlist: "ウォッチリスト" },
    hero: {
      eyebrow: "CARDS マーケット指数", title: "コレクティブルカード市場を一望する",
      body: "アートの価値を起点に、確認済みのカード情報、流通供給、現在価格から市場を読み解きます。",
    },
    pokemonHero: {
      eyebrow: "ポケモン市場", title: "動く市場として見るポケモンカード", body: "確認済みの印刷版、PSA 10 供給、現在価格で順位付けします。",
    },
    onePieceHero: {
      eyebrow: "ワンピース市場", title: "動く市場として見るワンピースカード", body: "確認済みの印刷版、PSA 10 供給、現在価格で順位付けします。",
    },
    watchlistHero: {
      eyebrow: "マーケットウォッチ", title: "主要市場に近づくカード", body: "101 位から 300 位までの識別情報、価格鮮度、供給、需要を観察します。",
    },
    heatmap: {
      title: "時価総額トップ {count} ヒートマップ", rankingTitle: "時価総額トップ {count}", pokemonTitle: "ポケモン市場ヒートマップ", onePieceTitle: "ワンピース市場ヒートマップ",
      body: "面積は現在の PSA 10 時価総額、色は選択期間の価格変化を表します。",
      negative: "下落", neutral: "集計中", positive: "上昇", count: "枚の適格カード", tilesLabel: "表示数", viewRanking: "トップ {count} を見る", shareImage: "画像をシェア",
    },
    periods: { "1d": "1 日", "7d": "7 日", "30d": "30 日" },
    labels: {
      rank: "順位", card: "カード", number: "完全な番号", language: "言語", price: "PSA 10 価格",
      priceShort: "価格",
      population: "PSA 10 枚数", populationShort: "枚数",
      marketCap: "時価総額", marketCapShort: "時価総額", trackedSales: "追跡成約額",
      trackedSalesShort: "成約",
      salesHelp: "CARDS の追跡範囲で確認できた PSA 10 の成約のみを含みます。", change: "変動", changeShort: "変動", asOf: "データ時刻",
      viewCard: "カード詳細を見る", close: "閉じる", story: "市場で支持される理由", history: "日次市場推移",
      dailyPrice: "参考価格", trackedSalesBars: "追跡成約額", salesTrend: "追跡成約額の推移", salesTrendShort: "成約推移", imageAlt: "カード画像",
      noHistory: "日次価格履歴を蓄積しています。", noCards: "この表示には適格カードがありません。", watchStatus: "適格性を観察中",
      share: "カードを共有", shareDone: "リンクをコピーしました", shareError: "コピーに失敗しました。URL を手動でコピーしてください",
      expandImage: "カードを拡大表示",
      marketCapHelp: "PSA 10 価格 × 確認済み PSA 10 枚数——最高評価の流通可能な価値。",
      populationHelp: "レジストリに記録された、確認済みの PSA 10 現存枚数。",
      priceHelp: "追跡期間内で確認できた直近の PSA 10 成約価格。",
    },
    grader: {
      eyebrow: "鑑定供給", title: "{grader} の市場供給", body: "最高評価枚数と追跡流動性を表示し、欠損価格は推計しません。",
      topGrade: "最高評価", topGradeShort: "評価", topGradePopulation: "最高評価枚数", topGradePopulationShort: "枚数", totalPopulation: "鑑定総数", totalPopulationShort: "総数", populationChange: "枚数変化", populationChangeShort: "変化",
      liquidity: "追跡流動性", marketCapAvailable: "PSA 10 時価総額", marketCapUnavailable: "時価総額データなし", marketShare: "シェア",
      names: { PSA: "PSA", BGS: "BGS", CGC: "CGC", SGC: "SGC", TAG: "TAG" },
    },
    theme: { dark: "ダークモード", light: "ライトモード" },
    methodology: {
      title: "CARDS の市場ランキング方法",
      body: "この指数に掲載されるカードは、いずれも確認済みの PSA 10 が 1,000 枚以上。実際に取引できる供給量に連動したランキングを保ち、数件の取引で動いてしまう希少品の影響を抑えます。",
    },
    gradingPulse: {
      eyebrow: "鑑定パルス",
      title: "毎日の鑑定フロー",
      subtitle: "追跡対象トップ 100 全体",
      windowCaption: "前回の{period}からの変化",
      submissions: "最高評価枚数の変化",
      share: "追跡対象の最高評価総数に占める割合",
    },
    status: { accumulating: "集計中", stale: "更新待ち", unavailable: "データなし" },
    footer: "CARDS Market Cap。コレクティブルカードのアート市場情報。",
  },
  ko: {
    nav: { all: "TCG 마켓", pokemon: "포켓몬", onePiece: "원피스", graders: "감정사", watchlist: "관심 목록" },
    hero: {
      eyebrow: "CARDS 마켓 인덱스",
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
      title: "주요 시장에 근접한 카드",
      body: "101위부터 300위까지의 카드 식별 정보, 가격 신선도, 공급, 수요를 관찰합니다.",
    },
    heatmap: {
      title: "시가총액 상위 {count} 히트맵", rankingTitle: "시가총액 상위 {count}", pokemonTitle: "포켓몬 시장 히트맵", onePieceTitle: "원피스 시장 히트맵",
      body: "면적은 현재 PSA 10 시가총액, 색상은 선택 기간의 가격 변동을 나타냅니다.",
      negative: "하락", neutral: "집계 중", positive: "상승", count: "장의 적격 카드", tilesLabel: "표시 수", viewRanking: "상위 {count} 보기", shareImage: "이미지 공유",
    },
    periods: { "1d": "1일", "7d": "7일", "30d": "30일" },
    labels: {
      rank: "순위", card: "카드", number: "전체 번호", language: "언어", price: "PSA 10 가격",
      priceShort: "가격",
      population: "PSA 10 매수", populationShort: "매수",
      marketCap: "시가총액", marketCapShort: "시총", trackedSales: "추적 거래액",
      trackedSalesShort: "거래",
      salesHelp: "CARDS 추적 범위에서 확인된 PSA 10 완료 거래만 포함합니다.", change: "등락", changeShort: "등락", asOf: "데이터 시각",
      viewCard: "카드 상세 보기", close: "닫기", story: "시장이 주목하는 이유", history: "일별 시장 추이",
      dailyPrice: "기준 가격", trackedSalesBars: "추적 거래액", salesTrend: "추적 거래액 추이", salesTrendShort: "거래 추이", imageAlt: "카드 이미지",
      noHistory: "일별 가격 이력을 축적하고 있습니다.", noCards: "이 보기에 적격 카드가 없습니다.", watchStatus: "자격 관찰 중",
      share: "카드 공유", shareDone: "링크 복사됨", shareError: "복사 실패 — 주소창에서 직접 복사하세요",
      expandImage: "카드 크게 보기",
      marketCapHelp: "PSA 10 가격 × 검증된 PSA 10 매수 — 최고 등급 공급의 거래 가능 가치.",
      populationHelp: "레지스트리에 기록된 검증된 PSA 10 현존 매수.",
      priceHelp: "추적 기간 내 가장 최근에 검증된 PSA 10 거래 가격.",
    },
    grader: {
      eyebrow: "감정 공급", title: "{grader} 시장 공급", body: "최고 등급 매수와 추적 유동성을 표시하며, 누락된 가격은 추정하지 않습니다.",
      topGrade: "최고 등급", topGradeShort: "등급", topGradePopulation: "최고 등급 매수", topGradePopulationShort: "매수", totalPopulation: "총 감정 수", totalPopulationShort: "총수", populationChange: "매수 변화", populationChangeShort: "변화",
      liquidity: "추적 유동성", marketCapAvailable: "PSA 10 시가총액", marketCapUnavailable: "시가총액 데이터 없음", marketShare: "점유율",
      names: { PSA: "PSA", BGS: "BGS", CGC: "CGC", SGC: "SGC", TAG: "TAG" },
    },
    theme: { dark: "다크 모드", light: "라이트 모드" },
    methodology: {
      title: "CARDS의 시장 순위 방식",
      body: "이 지수에 수록되는 모든 카드는 검증된 PSA 10이 1,000장 이상입니다. 이 기준은 순위가 실제 거래 가능한 공급에 연동되도록 하여, 소수 거래로 움직이는 희소 품목의 영향을 줄입니다.",
    },
    gradingPulse: {
      eyebrow: "감정 펄스",
      title: "일별 감정 흐름",
      subtitle: "추적 대상 상위 100 전체",
      windowCaption: "이전 {period} 대비 변화",
      submissions: "최고 등급 매수 변화",
      share: "추적 대상 최고 등급 총량 대비 비중",
    },
    status: { accumulating: "집계 중", stale: "오래된 데이터", unavailable: "데이터 없음" },
    footer: "CARDS Market Cap. 컬렉터블 카드 아트 마켓 인텔리전스.",
  },
};
