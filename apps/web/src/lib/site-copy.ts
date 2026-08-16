import type { Locale } from "./types";

/*
 * 內容頁（/methodology /about /faq /glossary /data）嘅文案。
 *
 * 點解唔擺入 i18n.ts：i18n.ts 係產品 UI 字串（label、狀態、單位），每個 render 都要；
 * 呢五版係長文，只有五條 route 用到，撈埋一齊會令首頁 bundle 白白食多幾十 KB。
 *
 * 硬規矩（owner 2026-08-16）：呢個檔一個數字都唔准寫死。所有數（市值、POP、價、日期、
 * generation）一律留 `{token}`，由 page 讀 loadMarketSnapshot() 之後用 fill() 填。
 * 填唔到 = 成句唔出，唔准出 raw token，亦唔准當佢係零。
 */

export type ContentPageKey = "methodology" | "about" | "faq" | "glossary" | "data";

export interface QaItem {
  q: string;
  a: string;
}

export interface Prose {
  heading: string;
  body: string[];
}

export interface TermItem {
  id: string;
  term: string;
  def: string;
}

export interface TableCopy {
  caption: string;
  head: string[];
  rows: string[][];
}

export interface ShellCopy {
  home: string;
  asOfLabel: string;
  onThisPage: string;
  moreHeading: string;
  nav: Record<ContentPageKey, string>;
  indexLink: string;
  notCrypto: string;
}

export interface MethodologyCopy {
  eyebrow: string;
  h1: string;
  answer: string;
  scope: string;
  formula: Prose;
  worked: {
    heading: string;
    intro: string;
    head: string[];
    rows: { label: string; value: string }[];
    note: string;
  };
  sources: Prose;
  eligibility: Prose;
  gradedVsRaw: Prose;
  limits: Prose;
  compare: {
    heading: string;
    intro: string;
    table: TableCopy;
    note: string;
  };
  versioning: Prose;
  cite: {
    heading: string;
    intro: string;
    template: string;
    note: string;
  };
  faqHeading: string;
  faq: QaItem[];
}

export interface FaqPageCopy {
  eyebrow: string;
  h1: string;
  answer: string;
  groups: { heading: string; items: QaItem[] }[];
}

export interface GlossaryCopy {
  eyebrow: string;
  h1: string;
  answer: string;
  setName: string;
  setDescription: string;
  terms: TermItem[];
}

export interface AboutCopy {
  eyebrow: string;
  h1: string;
  answer: string;
  descriptor: string;
  sections: Prose[];
}

export interface DataCopy {
  eyebrow: string;
  h1: string;
  answer: string;
  endpoints: { heading: string; intro: string; table: TableCopy };
  params: { heading: string; intro: string; table: TableCopy };
  fields: { heading: string; intro: string; table: TableCopy };
  example: { heading: string; intro: string; note: string };
  cadence: Prose;
  fairUse: Prose;
  license: { heading: string; body: string[]; attributionLabel: string; attributionTemplate: string };
  files: { heading: string; intro: string; items: { href: string; label: string; note: string }[] };
}

export interface SiteCopy {
  shell: ShellCopy;
  methodology: MethodologyCopy;
  faq: FaqPageCopy;
  glossary: GlossaryCopy;
  about: AboutCopy;
  data: DataCopy;
}

/* en 唔加 query，其餘跟 route-metadata.ts 嘅 localizedPath() 一樣加 `?lang=`。
   唔 import 人哋個 private helper，兩邊行為要一致靠呢行同 test。 */
export function withLang(path: string, locale: Locale): string {
  if (locale === "en") return path;
  return `${path}${path.includes("?") ? "&" : "?"}lang=${locale}`;
}

/* 解唔到 placeholder 就返 null，caller 見到 null 就成句唔 render。
   寧願少一句，都好過出「$undefined」或者 raw `{topCap}` 畀人／畀 LLM 引用。 */
export function fill(template: string, vars: Record<string, string | null | undefined>): string | null {
  let missing = false;
  const out = template.replace(/\{(\w+)\}/g, (token, key: string) => {
    const value = vars[key];
    if (!value) {
      missing = true;
      return token;
    }
    return value;
  });
  return missing ? null : out;
}

/*
 * 段落用：逐句填，淨係丟走解唔到 placeholder 嗰一句，其餘照出。
 * 整段掉會令一段得返半個意思，所以句子先係最細嘅「出／唔出」單位。
 * 句號同時認 . 。 ！ ？，五個 locale 都行得。
 */
export function fillProse(text: string, vars: Record<string, string | null | undefined>): string {
  // 連住句尾空白一齊切，join 返去先唔會喺中日韓句子中間多咗個空格。
  const sentences = text.match(/[^.。！？!?]*[.。！？!?]["」』）)]?\s*|[^.。！？!?]+$/g) ?? [text];
  const kept = sentences.map((sentence) => fill(sentence, vars)).filter((part): part is string => part !== null);
  return kept.join("").trim();
}

/* 2026-08-16：站內未有 /rankings hub route，所以「排行榜」一律指返首頁 Top 100。
   將來開咗 /rankings，改呢一個 const 就五版一齊轉。 */
export const INDEX_PATH = "/";

export const contentPagePaths: Record<ContentPageKey, string> = {
  methodology: "/methodology",
  about: "/about",
  faq: "/faq",
  glossary: "/glossary",
  data: "/data",
};

const en: SiteCopy = {
  shell: {
    home: "CardZ Marketcap",
    asOfLabel: "Figures as of",
    onThisPage: "On this page",
    moreHeading: "More from CardZ Marketcap",
    nav: {
      methodology: "Methodology",
      about: "About",
      faq: "FAQ",
      glossary: "Glossary",
      data: "Data & API",
    },
    indexLink: "Live rankings",
    notCrypto: "CardZ Marketcap is a trading card price index. It is not a cryptocurrency, not a token, and not a listed company. There is no CardZ ticker, and this site is unrelated to the crypto asset traded as CARDS.",
  },
  methodology: {
    eyebrow: "Methodology",
    h1: "How CardZ Marketcap calculates trading card market cap (PSA 10 price × population)",
    answer:
      "CardZ Marketcap calculates trading card market cap as PSA 10 reference price multiplied by verified PSA 10 population. A card priced at $100 with 10,000 PSA 10 copies has a $1,000,000 market cap. The index covers Pokémon card market cap and One Piece card market cap, as of {asOf}.",
    scope:
      "Market cap here means the total value of every graded copy that exists at the top grade, not the price of one card. It is the same idea as a share price multiplied by shares outstanding: a cheap card printed in enormous numbers can outrank an expensive card that was almost never graded.",
    formula: {
      heading: "The formula",
      body: [
        "PSA 10 market cap = PSA 10 reference price × PSA 10 population.",
        "The PSA 10 reference price is the price we hold for a single PSA 10 copy of that exact printing. The PSA 10 population is the count of copies PSA has graded 10 for that printing, taken from PSA's published population report.",
        "Both sides of the multiplication must resolve for the same printing. If either side is missing or stale, the card shows a status instead of a number. We never substitute zero for a missing value, and we never carry a price across from a different language or a different print run.",
      ],
    },
    worked: {
      heading: "Worked example",
      intro: "Using the card ranked #1 on the live index as of {asOf}:",
      head: ["Input", "Value"],
      rows: [
        { label: "Card", value: "{topName}" },
        { label: "PSA 10 reference price", value: "{topPrice}" },
        { label: "PSA 10 population", value: "{topPop}" },
        { label: "PSA 10 market cap", value: "{topCap}" },
      ],
      note: "Price × population is the whole calculation. There is no smoothing factor, no float adjustment, and no editorial weighting on top of it.",
    },
    sources: {
      heading: "Data sources and refresh cadence",
      body: [
        "Population comes from PSA's published population report for the specific printing, matched by set, card number, language and finish.",
        "Prices come from graded-sale evidence for that same printing, collected daily and carried forward with an explicit observation date so you can always see how fresh a number is.",
        "The published snapshot is rebuilt daily. Every card carries the date its price and population were observed, and pages show the effective date of the snapshot you are reading.",
        "Non-USD figures are converted at the exchange rate recorded with the snapshot, so a currency switch never changes the underlying ranking.",
      ],
    },
    eligibility: {
      heading: "What counts, and what is excluded",
      body: [
        "A printing is ranked once it has a verified PSA 10 population large enough to be statistically meaningful and a price we can stand behind. The lowest PSA 10 population among ranked cards is {minPop} as of {asOf}.",
        "Each printing is ranked separately. The Japanese, English and Chinese releases of the same artwork are different rows, because they have different populations and different prices.",
        "Cards where the price or population cannot be verified are held out of the ranking rather than estimated. Sealed product is tracked separately from single cards and does not enter the single-card market cap.",
      ],
    },
    gradedVsRaw: {
      heading: "Why PSA 10 and not raw prices",
      body: [
        "A raw card has no verified supply. Nobody knows how many copies of a 1999 base-set card still exist, or what condition they are in, so raw price multiplied by print run is a guess dressed as a number.",
        "A PSA 10 population is a counted, published figure that only moves in one direction as more copies are submitted. Pairing it with the price of that exact grade gives a market cap you can audit: both inputs are observable, and both are dated.",
        "The trade-off is that PSA 10 market cap ignores value held in raw and lower-grade copies. It measures the top-grade market, not the entire hobby.",
      ],
    },
    limits: {
      heading: "Limitations we will not hide",
      body: [
        "Population reports lag reality. Cards graded this week appear in the report later, so a fast-rising modern card can be undercounted.",
        "Thinly traded cards have noisy prices. When a printing sells a handful of times a year, one unusual sale moves the market cap more than the market actually moved.",
        "Cracked and resubmitted cards inflate population counts, because a card that is graded, cracked out and regraded can be counted more than once.",
        "Market cap is not liquidity. Nobody could sell every PSA 10 copy of a card at the reference price; the figure measures scale, not cash value.",
      ],
    },
    compare: {
      heading: "How this differs from price-only indexes",
      intro:
        "Most trading card trackers publish the price of one copy. A graded card market index multiplies that price by verified supply, which changes the ranking substantially.",
      table: {
        caption: "CardZ Marketcap compared with other things called a card index",
        head: ["What it is", "What it measures", "Supply verified?"],
        rows: [
          [
            "CardZ Marketcap",
            "PSA 10 reference price × verified PSA 10 population",
            "Yes, from PSA population reports",
          ],
          [
            "Price-only trackers",
            "Last sale or average sale for one copy",
            "No supply term at all",
          ],
          [
            "Crypto tokens named CARDS",
            "Price of an unrelated digital asset",
            "Not a trading card product",
          ],
        ],
      },
      note: "A $30 card with 300,000 PSA 10 copies outranks a $30,000 card with 40. Price-only lists cannot show that, which is the entire reason this index exists.",
    },
    versioning: {
      heading: "Versioning and changes",
      body: [
        "Every published snapshot carries a generation identifier and an effective date. The page you are reading was built from generation {generation}, effective {asOf}.",
        "Methodology changes are applied going forward and noted here. We do not silently restate published history: if a definition changes, the change is described rather than backfilled.",
      ],
    },
    cite: {
      heading: "Cite this page",
      intro: "If you quote these figures in an article, a video or a model answer, please cite the method and the date:",
      template: "CardZ Marketcap, “How CardZ Marketcap calculates trading card market cap”, PSA 10 price × PSA 10 population, as of {asOf}.",
      note: "Figures move daily. Citing the effective date tells readers which snapshot you used.",
    },
    faqHeading: "Methodology questions",
    faq: [
      {
        q: "What is trading card market cap?",
        a: "Trading card market cap is the price of one PSA 10 copy multiplied by the number of PSA 10 copies that exist. It measures the total value of a card's top-grade supply rather than the price of a single copy.",
      },
      {
        q: "How does CardZ Marketcap calculate Pokémon card market cap?",
        a: "CardZ Marketcap multiplies the PSA 10 reference price of a specific Pokémon printing by that printing's verified PSA 10 population from PSA's population report. Prices and populations are refreshed daily and dated.",
      },
      {
        q: "Why use PSA 10 population instead of print run?",
        a: "Print runs are rarely published and surviving copies are unknown. PSA 10 population is counted and published, so it can be verified. Using it keeps both sides of the market cap calculation observable.",
      },
      {
        q: "Does market cap mean the card is worth that much?",
        a: "No. Market cap is the theoretical value of all PSA 10 copies at once. Selling them all at that price is impossible, so treat market cap as a measure of scale and supply, not of cash you could realise.",
      },
      {
        q: "How often is the index updated?",
        a: "The snapshot is rebuilt daily. Each card shows the observation date of its price and population, and each page shows the effective date of the snapshot, so you can tell exactly how fresh a figure is.",
      },
    ],
  },
  faq: {
    eyebrow: "FAQ",
    h1: "Pokémon card market cap FAQ: PSA 10 market cap questions answered",
    answer:
      "CardZ Marketcap ranks Pokémon card market cap and One Piece card market cap by multiplying PSA 10 reference price by verified PSA 10 population. This FAQ answers how the figures are built, what they cover and what they cannot tell you, as of {asOf}.",
    groups: [
      {
        heading: "The basics",
        items: [
          {
            q: "What is CardZ Marketcap?",
            a: "CardZ Marketcap is a daily market capitalisation index for graded trading cards. It ranks Pokémon and One Piece cards by verified PSA 10 population multiplied by PSA 10 reference price, and publishes the effective date of every snapshot.",
          },
          {
            q: "What is a PSA 10 market cap?",
            a: "A PSA 10 market cap is the price of one PSA 10 copy multiplied by the number of PSA 10 copies PSA has graded. It expresses how much value sits in a card's top-grade supply rather than in a single copy.",
          },
          {
            q: "Is CardZ a cryptocurrency or a token?",
            a: "No. CardZ Marketcap is a trading card price index with no token, no coin and no ticker. It is unrelated to any crypto asset trading under a similar name, and nothing on this site can be bought or sold.",
          },
          {
            q: "Is CardZ Marketcap free to use?",
            a: "Yes. The rankings, card pages, methodology and glossary are free to read, and the figures are free to quote with attribution and a link back to the page you took them from.",
          },
        ],
      },
      {
        heading: "How the numbers are built",
        items: [
          {
            q: "How is trading card market cap calculated?",
            a: "Market cap equals PSA 10 reference price multiplied by verified PSA 10 population for the same printing. Both inputs carry an observation date, and a card is held out of the ranking if either input cannot be verified.",
          },
          {
            q: "Where does the population data come from?",
            a: "Population comes from PSA's published population report, matched to the exact set, card number, language and finish. We use the PSA 10 count only; lower grades are not part of the market cap figure.",
          },
          {
            q: "How often do the numbers update?",
            a: "The published snapshot is rebuilt daily. Each card shows when its price and population were observed, so a figure that has not moved recently is visibly dated rather than silently refreshed.",
          },
          {
            q: "Why do some cards show no number?",
            a: "Because the value could not be verified for that printing on that date. CardZ Marketcap leaves unverified values blank instead of substituting zero or estimating, which would quietly corrupt the ranking.",
          },
          {
            q: "Which currency are the figures in?",
            a: "Figures are computed in US dollars and converted for display using the exchange rate stored with the snapshot. Switching currency changes the display only; the underlying ranking never changes with it.",
          },
        ],
      },
      {
        heading: "Coverage",
        items: [
          {
            q: "Which cards are covered?",
            a: "The index covers Pokémon and One Piece single cards that have a verified PSA 10 population and a price we can stand behind. As of {asOf} it ranks {ranked} printings: {pokemon} Pokémon and {onePiece} One Piece.",
          },
          {
            q: "Are Japanese and English cards ranked separately?",
            a: "Yes. Each printing is a separate row because each has its own population and its own price. The same artwork in another language is a different card for ranking purposes, never an average of the two.",
          },
          {
            q: "Do you cover sealed boxes?",
            a: "Sealed product is tracked separately from single cards. It has no PSA 10 population, so it cannot enter the single-card market cap ranking and is never mixed into those totals.",
          },
          {
            q: "What is the total market cap tracked?",
            a: "As of {asOf}, the published index tracks {totalCap} in PSA 10 market cap across {ranked} printings. That total moves daily with prices and with newly graded copies entering the population reports.",
          },
        ],
      },
      {
        heading: "Reading the rankings",
        items: [
          {
            q: "What are the most valuable Pokémon cards by market cap?",
            a: "The ranking is published live and reorders daily. Market cap leaders combine a high PSA 10 price with a large graded population, so the top of the list is rarely the same as a list of the most expensive single cards.",
          },
          {
            q: "Why is a cheap card ranked above an expensive one?",
            a: "Because market cap multiplies price by supply. A card worth $30 with 300,000 PSA 10 copies carries more total value than a card worth $30,000 with 40 copies, even though one copy costs far less.",
          },
          {
            q: "Can I use these figures in an article or a video?",
            a: "Yes, with attribution to CardZ Marketcap and a link to the page you took the figure from. Please include the effective date, because market cap figures change every day.",
          },
        ],
      },
    ],
  },
  glossary: {
    eyebrow: "Glossary",
    h1: "Graded card market index glossary: PSA 10 market cap terms",
    answer:
      "This glossary defines the terms CardZ Marketcap uses to rank graded cards, from PSA 10 market cap and population report to gem rate and pop-weighted supply. Definitions apply to the index as published on {asOf}.",
    setName: "CardZ Marketcap graded card glossary",
    setDescription: "Definitions of the grading, pricing and market cap terms used by the CardZ Marketcap graded card market index.",
    terms: [
      { id: "market-cap", term: "Market cap (trading card)", def: "The PSA 10 reference price of a printing multiplied by its verified PSA 10 population. It measures the total value of top-grade supply, not the price of a single copy." },
      { id: "psa-10-market-cap", term: "PSA 10 market cap", def: "Market cap computed strictly from PSA 10 inputs: the price of a PSA 10 copy and the count of PSA 10 copies. Lower grades are excluded from the figure." },
      { id: "psa", term: "PSA", def: "Professional Sports Authenticator, a third-party grading company. PSA assigns a numeric grade to a submitted card and publishes population counts for each grade." },
      { id: "psa-10", term: "PSA 10", def: "PSA's highest standard grade, described as Gem Mint. It is the grade CardZ Marketcap uses for both the price and the supply side of market cap." },
      { id: "population-report", term: "Population report", def: "PSA's published count of how many copies of a card it has graded at each grade. It is the source of the supply term in market cap." },
      { id: "pop", term: "Pop", def: "Short for population. Pop 10 means the number of copies graded PSA 10 for a specific printing, set, language and finish." },
      { id: "reference-price", term: "Reference price", def: "The price CardZ Marketcap holds for one PSA 10 copy of a printing on a given date, derived from graded-sale evidence for that exact printing." },
      { id: "printing", term: "Printing", def: "A specific release of a card: one set, card number, language and finish. Different printings of the same artwork are ranked as separate rows." },
      { id: "finish", term: "Finish", def: "The surface treatment of a card, such as holo, reverse holo or non-holo. Finish changes both scarcity and price, so it is part of a printing's identity." },
      { id: "parallel", term: "Parallel", def: "An alternate version of a card printed with a different treatment or numbering. Parallels have their own populations and prices and never share a market cap row." },
      { id: "promo", term: "Promo", def: "A card distributed outside standard set packs, such as an event or campaign card. Promos often carry small populations and volatile prices." },
      { id: "set-code", term: "Set code", def: "The short identifier for the set a card belongs to. Combined with the collector number it identifies which printing a price and population belong to." },
      { id: "collector-number", term: "Collector number", def: "The number printed on a card showing its position within a set, such as 085/SVP. It is part of how a printing is matched to its population data." },
      { id: "grading", term: "Grading", def: "The process of submitting a card for third-party assessment of centring, corners, edges and surface, resulting in an encapsulated card with a numeric grade." },
      { id: "gem-rate", term: "Gem rate", def: "The share of submitted copies that receive a PSA 10. A low gem rate means PSA 10 supply grows slowly even when many copies are submitted." },
      { id: "raw", term: "Raw", def: "An ungraded card. Raw copies have no verified supply count, which is why they are not used for market cap." },
      { id: "ungraded-reference", term: "Ungraded reference price", def: "An indicative price for a raw copy, shown for context only. It never enters the market cap calculation." },
      { id: "slab", term: "Slab", def: "The sealed plastic holder a graded card is encapsulated in, carrying the grade and a certification number." },
      { id: "cert-number", term: "Certification number", def: "The unique number assigned to a graded card. It identifies one physical slab rather than a printing." },
      { id: "crack-and-resubmit", term: "Crack and resubmit", def: "Removing a card from its slab and submitting it again for a higher grade. It can cause the same physical card to be counted more than once in population data." },
      { id: "supply", term: "Verified supply", def: "The portion of a card's supply that has been counted by a grading company. CardZ Marketcap uses verified supply rather than estimated print runs." },
      { id: "print-run", term: "Print run", def: "The number of copies originally produced. Print runs are rarely published for trading cards and are not used in this index." },
      { id: "index", term: "Graded card market index", def: "A ranking of cards by a value measure computed from graded data. CardZ Marketcap is a graded card market index built on PSA 10 price and population." },
      { id: "snapshot", term: "Snapshot", def: "A single published build of the index, identified by a generation and an effective date. Every figure on the site belongs to one snapshot." },
      { id: "effective-date", term: "Effective date", def: "The date a snapshot's figures represent. It is shown on every page so a quoted figure can be tied to a specific day." },
      { id: "observation-date", term: "Observation date", def: "The date a specific price or population was observed. It can be earlier than the effective date when a source has not moved." },
      { id: "accumulating", term: "Accumulating", def: "A status shown when there is not yet enough evidence to publish a value. It means the figure is being collected, not that the value is zero." },
      { id: "stale", term: "Stale", def: "A status shown when a value exists but its observation date is old. The number is still displayed, with its date, rather than being hidden." },
      { id: "coverage", term: "Coverage", def: "How complete a snapshot is relative to what it claims to rank. Coverage is published with each snapshot so gaps are visible." },
      { id: "watchlist", term: "Watchlist", def: "Ranked printings outside the top 100 that are tracked with the same method. They use identical inputs and appear in the same totals." },
      { id: "one-piece-card", term: "One Piece Card Game", def: "The trading card game based on the One Piece manga. Its graded printings are ranked alongside Pokémon using the same market cap method." },
    ],
  },
  about: {
    eyebrow: "About",
    h1: "About CardZ Marketcap, the graded card market index",
    answer:
      "CardZ Marketcap is a daily market capitalisation index for graded trading cards, ranking Pokémon and One Piece cards by verified PSA 10 population multiplied by PSA 10 reference price. The published snapshot is effective {asOf}.",
    descriptor:
      "It exists because the hobby had plenty of price trackers and no supply-aware ranking. Knowing a card sold for $2,000 tells you nothing about whether two hundred or two hundred thousand copies exist at that grade.",
    sections: [
      {
        heading: "What we publish",
        body: [
          "A daily ranking of graded Pokémon and One Piece printings by PSA 10 market cap, with the price, population and market cap shown for each row.",
          "A page for every ranked printing, showing its inputs and how they have moved.",
          "A written methodology, a glossary of the terms used, and a machine-readable API so the same figures can be checked rather than taken on trust.",
        ],
      },
      {
        heading: "Editorial standards",
        body: [
          "Every published number is computed from a dated snapshot. We do not hand-adjust rankings, and no card can be promoted by paying for it.",
          "Missing values stay missing. If a price or population cannot be verified, the card shows a status instead of a number; we never substitute zero, and we never borrow a price from a different printing.",
          "Limitations are published alongside the method rather than buried. The methodology page lists the known weaknesses of a population-based index, including reporting lag and resubmission double-counting.",
          "Corrections are made in the next daily snapshot and described rather than backfilled silently.",
        ],
      },
      {
        heading: "Update cadence",
        body: [
          "The index is rebuilt daily. Each snapshot carries a generation identifier and an effective date, and each figure carries the date it was observed.",
          "Pages show the effective date of the snapshot you are reading, so a figure quoted from this site can always be tied to a specific day.",
        ],
      },
      {
        heading: "Independence",
        body: [
          "CardZ Marketcap does not sell cards, does not run auctions and does not take payment for placement in the rankings.",
          "We name PSA as the grading source because its population reports are public and checkable. We do not publish the identities of our pricing suppliers.",
        ],
      },
      {
        heading: "How to cite us",
        body: [
          "Quote the figure, name CardZ Marketcap, link to the page you took it from, and include the effective date. Example: CardZ Marketcap, PSA 10 market cap, as of {asOf}.",
          "Figures are free to quote and cite with attribution. Bulk redistribution of the dataset needs written permission; see the licence note on the data page.",
        ],
      },
    ],
  },
  data: {
    eyebrow: "Data & API",
    h1: "CardZ Marketcap data and API: PSA 10 market cap in JSON",
    answer:
      "CardZ Marketcap publishes its PSA 10 market cap rankings as JSON over a public read-only API. Each response carries the snapshot generation, the effective date and per-card price, population and market cap. Current snapshot effective {asOf}.",
    endpoints: {
      heading: "Endpoints",
      intro: "All endpoints are GET, return JSON, and need no key.",
      table: {
        caption: "Public read-only endpoints",
        head: ["Endpoint", "Returns"],
        rows: [
          ["/api/v1/market", "A page of the ranking for a scope, with snapshot metadata"],
          ["/api/v1/cards/<id>", "One card by id, or a 404 with an error body if it is not ranked"],
        ],
      },
    },
    params: {
      heading: "Query parameters",
      intro: "Invalid values return HTTP 400 rather than silently falling back to a default.",
      table: {
        caption: "Parameters accepted by /api/v1/market",
        head: ["Parameter", "Accepted values", "Notes"],
        rows: [
          ["scope", "all, pokemon, one-piece, watchlist", "Anything else returns 400"],
          ["page", "A positive integer", "Non-numeric or zero returns 400"],
          ["pageSize", "A positive integer", "Clamped to a server maximum"],
        ],
      },
    },
    fields: {
      heading: "Response fields",
      intro: "The market response wraps the card list with the snapshot identity, so a stored response can always be traced back to the build that produced it.",
      table: {
        caption: "Top-level fields in the market response",
        head: ["Field", "Meaning"],
        rows: [
          ["generation", "Identifier of the snapshot build the response came from"],
          ["generatedAt", "When the snapshot was built"],
          ["effectiveAt", "The date the figures represent"],
          ["coverage", "How complete the snapshot is against what it claims to rank"],
          ["count", "Number of cards in this response"],
          ["cards", "The ranked cards, each with price, population and market cap"],
        ],
      },
    },
    example: {
      heading: "Example request",
      intro: "Fetch the first page of the Pokémon scope:",
      note: "Responses are cached briefly at the edge. Read the effectiveAt field rather than the response time to know which day a figure belongs to.",
    },
    cadence: {
      heading: "Update cadence",
      body: [
        "A new snapshot is published daily and the API serves the current one. Older generations are not served from these endpoints.",
        "If you store responses, store the generation and effectiveAt with them. Two figures from different generations are not comparable without their dates.",
      ],
    },
    fairUse: {
      heading: "Fair use",
      body: [
        "The API is public and unauthenticated, so please keep request rates reasonable: read a page, cache it, and do not poll faster than the data changes. The snapshot only moves once a day.",
        "Excessive automated traffic may be rate limited. If you need bulk access, ask rather than scraping.",
      ],
    },
    license: {
      heading: "Attribution and licence",
      /*
       * 三個面（/data、/api/v1、/llms-full.txt）講嘅授權必須逐隻字一樣（verify pass 2026-08-16）。
       * 之前呢度寫「整批轉載需要書面授權」，而另外兩個面寫 CC BY 4.0 —— CC BY 4.0 本身
       * 就准許再散佈，兩句直接相反，任何引擎讀到都唔知信邊句。統一跟 GEO 批嘅
       * CC BY 4.0 + 同一條 attribution 字串。
       */
      body: [
        "Figures may be quoted and republished under CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/), with attribution to CardZ Marketcap and a link to the page or endpoint used.",
        "Attribution must carry the effective date of the figure, because market cap is recomputed on every daily update.",
      ],
      attributionLabel: "Attribution line",
      attributionTemplate: "CardZ Marketcap, {site}, data as of {asOf}",
    },
    files: {
      heading: "Machine-readable files",
      intro: "For crawlers, agents and language models:",
      items: [
        { href: "/llms.txt", label: "/llms.txt", note: "Short description of the site and its key pages" },
        { href: "/llms-full.txt", label: "/llms-full.txt", note: "Expanded version with the methodology summarised" },
        { href: "/sitemap.xml", label: "/sitemap.xml", note: "Every indexable page" },
      ],
    },
  },
};

const zhTW: SiteCopy = {
  shell: {
    home: "CardZ Marketcap",
    asOfLabel: "數據截至",
    onThisPage: "本頁內容",
    moreHeading: "更多 CardZ Marketcap",
    nav: {
      methodology: "計算方法",
      about: "關於我們",
      faq: "常見問題",
      glossary: "名詞解釋",
      data: "資料與 API",
    },
    indexLink: "即時排行榜",
    notCrypto: "CardZ Marketcap 是集換式卡牌的價格指數，不是加密貨幣、不是代幣，也不是上市公司。本站沒有任何代幣代號，與名為 CARDS 的加密資產無關。",
  },
  methodology: {
    eyebrow: "計算方法",
    h1: "CardZ Marketcap 如何計算集換式卡牌市值（PSA 10 價格 × 鑑定數量）",
    answer:
      "CardZ Marketcap 以「PSA 10 參考價 × 已核實 PSA 10 鑑定數量」計算集換式卡牌市值。一張 100 美元、有 10,000 張 PSA 10 的卡，市值就是 1,000,000 美元。指數涵蓋寶可夢卡牌市值與海賊王卡牌市值，數據截至 {asOf}。",
    scope:
      "這裡的市值指的是「該卡最高鑑定等級的全部存世價值」，不是一張卡的成交價。概念等同股價乘以流通股數：一張便宜但鑑定數量龐大的卡，排名可以高過一張昂貴但幾乎沒人送鑑的卡。",
    formula: {
      heading: "公式",
      body: [
        "PSA 10 市值 ＝ PSA 10 參考價 × PSA 10 鑑定數量。",
        "PSA 10 參考價是我們為「同一個版次」單張 PSA 10 所持有的價格；PSA 10 鑑定數量則取自 PSA 公開的鑑定數量報告中，該版次被評為 10 分的張數。",
        "兩個數必須來自同一個版次。任何一邊缺失或過舊，該卡就顯示狀態而不是數字。我們不會用零代替缺值，也不會把其他語言或其他刷次的價格搬過來。",
      ],
    },
    worked: {
      heading: "實例計算",
      intro: "以 {asOf} 即時榜第 1 名的卡為例：",
      head: ["項目", "數值"],
      rows: [
        { label: "卡片", value: "{topName}" },
        { label: "PSA 10 參考價", value: "{topPrice}" },
        { label: "PSA 10 鑑定數量", value: "{topPop}" },
        { label: "PSA 10 市值", value: "{topCap}" },
      ],
      note: "整個計算就只有「價格 × 數量」。沒有平滑係數、沒有流通量調整，也沒有任何人為加權。",
    },
    sources: {
      heading: "資料來源與更新頻率",
      body: [
        "鑑定數量來自 PSA 公開的鑑定數量報告，並以卡集、卡號、語言、特殊處理逐項對上同一版次。",
        "價格來自同一版次的已鑑定成交證據，每日收集，並附上明確的觀測日期，讓你隨時看得出這個數字有多新。",
        "已發佈的快照每日重建。每張卡都帶有價格與鑑定數量的觀測日期，每一頁也標示你正在閱讀的快照生效日期。",
        "非美元金額使用快照內記錄的匯率換算，所以切換幣別不會改變排名。",
      ],
    },
    eligibility: {
      heading: "哪些卡列入、哪些排除",
      body: [
        "一個版次要同時具備「已核實且數量足以有統計意義的 PSA 10 鑑定數量」與「我們敢負責的價格」，才會進入排名。截至 {asOf}，入榜卡片中最低的 PSA 10 鑑定數量是 {minPop}。",
        "每個版次分開排名。同一張圖的日文版、英文版與中文版是不同列，因為它們的鑑定數量與價格都不同。",
        "價格或鑑定數量無法核實的卡，我們寧可不排，也不會用估算填數。未拆封商品與單卡分開追蹤，不會併入單卡市值。",
      ],
    },
    gradedVsRaw: {
      heading: "為什麼用 PSA 10，而不是裸卡價",
      body: [
        "裸卡沒有可核實的供給量。沒有人知道 1999 年基礎系列還有多少張存世、品相如何，所以「裸卡價 × 印量」只是包裝成數字的猜測。",
        "PSA 10 鑑定數量是被清點並公開的數字，而且只會隨著送鑑增加而單向上升。把它配上同一等級的價格，就得到一個可以查核的市值：兩個輸入都看得見，也都有日期。",
        "代價是：PSA 10 市值忽略了裸卡與低分卡所承載的價值。它衡量的是頂級品相市場，不是整個興趣圈。",
      ],
    },
    limits: {
      heading: "我們不會遮掩的限制",
      body: [
        "鑑定數量報告落後現實。本週送鑑的卡要之後才會出現在報告中，所以急升的現代卡可能被低估。",
        "冷門卡的價格雜訊大。一年只成交幾次的版次，一筆異常成交對市值的影響會大過市場真正的變動。",
        "拆殼重送會灌大鑑定數量，因為同一張實體卡被拆出來重評，可能被重複計算。",
        "市值不等於流動性。沒有人能把一張卡所有 PSA 10 都按參考價賣掉；這個數字衡量的是規模，不是可變現金額。",
      ],
    },
    compare: {
      heading: "與只看價格的指數有何不同",
      intro:
        "大多數卡牌追蹤站公佈的是「一張卡的價格」。鑑定卡市場指數把價格乘上已核實供給量，排名會出現明顯差異。",
      table: {
        caption: "CardZ Marketcap 與其他被稱作「卡牌指數」的東西比較",
        head: ["是什麼", "衡量什麼", "供給量有核實嗎"],
        rows: [
          ["CardZ Marketcap", "PSA 10 參考價 × 已核實 PSA 10 鑑定數量", "有，來自 PSA 鑑定數量報告"],
          ["純價格追蹤站", "單張卡的最新或平均成交價", "完全沒有供給量這一項"],
          ["名為 CARDS 的加密代幣", "一個無關數位資產的價格", "並非卡牌產品"],
        ],
      },
      note: "一張 30 美元、有 300,000 張 PSA 10 的卡，市值高過一張 30,000 美元但只有 40 張的卡。純價格榜看不出這件事，而這正是本指數存在的理由。",
    },
    versioning: {
      heading: "版本與變更",
      body: [
        "每個已發佈快照都帶有 generation 識別碼與生效日期。你正在閱讀的這一頁來自 generation {generation}，生效日 {asOf}。",
        "方法變更一律向前套用並在此註明。我們不會偷偷改寫已發佈的歷史：定義有變就寫明變更內容，而不是回填舊資料。",
      ],
    },
    cite: {
      heading: "引用本頁",
      intro: "若你在文章、影片或模型回答中引用這些數字，請一併註明方法與日期：",
      template: "CardZ Marketcap，〈CardZ Marketcap 如何計算集換式卡牌市值〉，PSA 10 價格 × PSA 10 鑑定數量，數據截至 {asOf}。",
      note: "數字每日變動。標明生效日期，讀者才知道你用的是哪一份快照。",
    },
    faqHeading: "計算方法常見問題",
    faq: [
      {
        q: "什麼是集換式卡牌市值？",
        a: "集換式卡牌市值是單張 PSA 10 的價格，乘以存世 PSA 10 的張數。它衡量的是一張卡在最高鑑定等級的全部供給價值，而不是單張卡的成交價。",
      },
      {
        q: "CardZ Marketcap 怎樣計算寶可夢卡牌市值？",
        a: "CardZ Marketcap 把某個寶可夢版次的 PSA 10 參考價，乘以該版次在 PSA 鑑定數量報告中已核實的 PSA 10 張數。價格與數量每日更新，並附觀測日期。",
      },
      {
        q: "為什麼用 PSA 10 鑑定數量而不是印量？",
        a: "印量很少公開，存世張數也無從得知。PSA 10 鑑定數量是被清點並公開的，可以查核。用它可以讓市值算式兩邊都保持可觀測。",
      },
      {
        q: "市值等於這張卡值那麼多錢嗎？",
        a: "不等於。市值是所有 PSA 10 同時按該價格計算的理論總值。實際上不可能全部按價賣出，所以請把市值視為規模與供給的指標，而不是可變現金額。",
      },
      {
        q: "指數多久更新一次？",
        a: "快照每日重建。每張卡都顯示價格與鑑定數量的觀測日期，每一頁都顯示快照的生效日期，你可以清楚判斷一個數字有多新。",
      },
    ],
  },
  faq: {
    eyebrow: "常見問題",
    h1: "寶可夢卡牌市值常見問題：PSA 10 市值一次講清楚",
    answer:
      "CardZ Marketcap 以「PSA 10 參考價 × 已核實 PSA 10 鑑定數量」排出寶可夢卡牌市值與海賊王卡牌市值。本頁說明這些數字怎樣算出來、涵蓋什麼、以及不能說明什麼，數據截至 {asOf}。",
    groups: [
      {
        heading: "基本概念",
        items: [
          {
            q: "CardZ Marketcap 是什麼？",
            a: "CardZ Marketcap 是鑑定卡的每日市值指數，以已核實 PSA 10 鑑定數量乘以 PSA 10 參考價，為寶可夢與海賊王卡片排名，並公佈每份快照的生效日期。",
          },
          {
            q: "什麼是 PSA 10 市值？",
            a: "PSA 10 市值是單張 PSA 10 的價格，乘以 PSA 已評為 10 分的張數。它表達的是一張卡在最高鑑定等級的供給總價值，而不是單張的價格。",
          },
          {
            q: "CardZ 是加密貨幣或代幣嗎？",
            a: "不是。CardZ Marketcap 是集換式卡牌價格指數，沒有代幣、沒有幣、也沒有代號，與任何名稱相近的加密資產無關，本站也沒有任何東西可以買賣。",
          },
          {
            q: "CardZ Marketcap 免費嗎？",
            a: "免費。排行榜、卡片頁、計算方法與名詞解釋都可自由閱讀；數字亦可自由引用，只要註明出處並連回你取數的那一頁。",
          },
        ],
      },
      {
        heading: "數字怎樣算出來",
        items: [
          {
            q: "集換式卡牌市值怎樣計算？",
            a: "市值等於同一版次的 PSA 10 參考價乘以已核實 PSA 10 鑑定數量。兩項輸入都附觀測日期；任何一邊無法核實，該卡就不會進入排名。",
          },
          {
            q: "鑑定數量的資料從哪裡來？",
            a: "來自 PSA 公開的鑑定數量報告，並對應到確切的卡集、卡號、語言與特殊處理。我們只取 PSA 10 的張數，低分等級不計入市值。",
          },
          {
            q: "數字多久更新一次？",
            a: "已發佈的快照每日重建。每張卡都顯示價格與鑑定數量的觀測時間，所以久未變動的數字會清楚地顯示為舊資料，而不是被無聲刷新。",
          },
          {
            q: "為什麼有些卡沒有數字？",
            a: "因為該版次在該日期的數值無法核實。CardZ Marketcap 寧可留空，也不會用零代替或憑估算填數——那會悄悄污染整個排名。",
          },
          {
            q: "數字用什麼幣別？",
            a: "所有數值以美元計算，顯示時按快照內記錄的匯率換算。切換幣別只影響顯示，排名不會因此改變。",
          },
        ],
      },
      {
        heading: "涵蓋範圍",
        items: [
          {
            q: "涵蓋哪些卡？",
            a: "涵蓋具備已核實 PSA 10 鑑定數量、且價格可負責的寶可夢與海賊王單卡。截至 {asOf}，共排名 {ranked} 個版次：寶可夢 {pokemon} 個、海賊王 {onePiece} 個。",
          },
          {
            q: "日文版與英文版會分開排名嗎？",
            a: "會。每個版次各自成列，因為各有各的鑑定數量與價格。同一張圖的另一語言版本在排名上是另一張卡，絕不會取兩者平均。",
          },
          {
            q: "有涵蓋未拆封盒裝嗎？",
            a: "未拆封商品與單卡分開追蹤。它沒有 PSA 10 鑑定數量，所以無法進入單卡市值排名，也不會被混入那些總額。",
          },
          {
            q: "追蹤中的總市值是多少？",
            a: "截至 {asOf}，已發佈指數在 {ranked} 個版次上共追蹤 {totalCap} 的 PSA 10 市值。該總額會隨價格變動與新增鑑定張數每日改變。",
          },
        ],
      },
      {
        heading: "看懂排行榜",
        items: [
          {
            q: "以市值計，最有價值的寶可夢卡是哪些？",
            a: "排名即時公佈並每日重排。市值領先者同時具備高 PSA 10 價格與龐大鑑定數量，所以榜首名單通常與「最貴單卡」名單並不相同。",
          },
          {
            q: "為什麼便宜的卡排在昂貴的卡前面？",
            a: "因為市值是價格乘以供給量。一張 30 美元、有 300,000 張 PSA 10 的卡，總價值高過一張 30,000 美元但只有 40 張的卡，即使單張便宜得多。",
          },
          {
            q: "我可以在文章或影片引用這些數字嗎？",
            a: "可以，請註明出處為 CardZ Marketcap 並連回你取數的那一頁，同時附上生效日期，因為市值數字每日都在變。",
          },
        ],
      },
    ],
  },
  glossary: {
    eyebrow: "名詞解釋",
    h1: "鑑定卡市場指數名詞解釋：PSA 10 市值用語",
    answer:
      "本頁解釋 CardZ Marketcap 為鑑定卡排名時使用的名詞，包括 PSA 10 市值、鑑定數量報告、寶石率與已核實供給量。定義適用於 {asOf} 所發佈的指數。",
    setName: "CardZ Marketcap 鑑定卡名詞解釋",
    setDescription: "CardZ Marketcap 鑑定卡市場指數所使用的鑑定、定價與市值名詞定義。",
    terms: [
      { id: "market-cap", term: "市值（集換式卡牌）", def: "某版次的 PSA 10 參考價乘以已核實 PSA 10 鑑定數量。衡量的是最高等級供給的總價值，而非單張卡的價格。" },
      { id: "psa-10-market-cap", term: "PSA 10 市值", def: "只用 PSA 10 輸入計算的市值：PSA 10 單張價格與 PSA 10 張數。低分等級不計入。" },
      { id: "psa", term: "PSA", def: "Professional Sports Authenticator，第三方鑑定公司。PSA 為送鑑卡片評定分數，並公佈各等級的鑑定數量。" },
      { id: "psa-10", term: "PSA 10", def: "PSA 的最高標準分數，稱為 Gem Mint。CardZ Marketcap 的價格與供給量兩邊都採用這個等級。" },
      { id: "population-report", term: "鑑定數量報告", def: "PSA 公佈的各等級已評張數統計，是市值中供給量那一項的來源。" },
      { id: "pop", term: "Pop", def: "population 的簡稱。Pop 10 指某個卡集、語言與特殊處理下的特定版次被評為 PSA 10 的張數。" },
      { id: "reference-price", term: "參考價", def: "CardZ Marketcap 在某一日為某版次單張 PSA 10 所持有的價格，取自該版次的已鑑定成交證據。" },
      { id: "printing", term: "版次", def: "一張卡的特定發行：卡集、卡號、語言與特殊處理。同一張圖的不同版次分成不同列排名。" },
      { id: "finish", term: "特殊處理", def: "卡面的表面處理，例如亮面、反亮面或普卡。處理方式同時影響稀有度與價格，所以屬於版次身分的一部分。" },
      { id: "parallel", term: "平行卡", def: "以不同處理或編號印製的同卡替代版本。平行卡有自己的鑑定數量與價格，永遠不與本卡共用市值列。" },
      { id: "promo", term: "宣傳卡", def: "在一般卡包以外發放的卡，例如活動卡或聯名卡。宣傳卡通常鑑定數量較少、價格波動較大。" },
      { id: "set-code", term: "卡集代碼", def: "卡片所屬卡集的簡短識別碼。與卡號合併使用，可判定價格與鑑定數量屬於哪個版次。" },
      { id: "collector-number", term: "卡號", def: "印在卡上的編號，代表在卡集中的位置，例如 085/SVP。是版次對應鑑定數量資料的依據之一。" },
      { id: "grading", term: "鑑定", def: "把卡片送交第三方評估置中、邊角、邊緣與表面，最後封裝並給出分數的流程。" },
      { id: "gem-rate", term: "寶石率", def: "送鑑卡片中取得 PSA 10 的比例。寶石率低表示即使大量送鑑，PSA 10 供給量仍增加得很慢。" },
      { id: "raw", term: "裸卡", def: "未鑑定的卡。裸卡沒有可核實的供給量統計，因此不用於市值計算。" },
      { id: "ungraded-reference", term: "裸卡參考價", def: "裸卡的參考性價格，僅供對照，永遠不會進入市值計算。" },
      { id: "slab", term: "鑑定殼", def: "封裝鑑定卡的密封膠殼，上面標示分數與鑑定編號。" },
      { id: "cert-number", term: "鑑定編號", def: "指派給一張鑑定卡的唯一編號，識別的是一個實體殼，而不是一個版次。" },
      { id: "crack-and-resubmit", term: "拆殼重送", def: "把卡從鑑定殼取出再送鑑以爭取更高分數。這會令同一張實體卡在鑑定數量中被重複計算。" },
      { id: "supply", term: "已核實供給量", def: "一張卡中已被鑑定公司清點的供給部分。CardZ Marketcap 採用已核實供給量，而不是估算印量。" },
      { id: "print-run", term: "印量", def: "原始生產的張數。集換式卡牌的印量甚少公開，本指數不使用。" },
      { id: "index", term: "鑑定卡市場指數", def: "以鑑定資料計算價值指標的卡片排名。CardZ Marketcap 就是建基於 PSA 10 價格與鑑定數量的鑑定卡市場指數。" },
      { id: "snapshot", term: "快照", def: "指數的一次已發佈建置，由 generation 與生效日期識別。本站每個數字都屬於某一份快照。" },
      { id: "effective-date", term: "生效日期", def: "快照數字所代表的日期。每頁都會標示，讓引用的數字可以對應到具體某一天。" },
      { id: "observation-date", term: "觀測日期", def: "某個價格或鑑定數量被觀測到的日期。來源未變動時，它可以早於生效日期。" },
      { id: "accumulating", term: "累積中", def: "證據尚不足以發佈數值時顯示的狀態。代表資料仍在收集，不代表數值為零。" },
      { id: "stale", term: "資料偏舊", def: "數值存在但觀測日期較舊時顯示的狀態。數字仍會連同日期顯示，而不是被隱藏。" },
      { id: "coverage", term: "涵蓋度", def: "快照相對其所聲稱排名範圍的完整程度。每份快照都會公佈，讓缺口可見。" },
      { id: "watchlist", term: "觀察清單", def: "百大以外、以相同方法追蹤的版次。輸入完全一致，並計入相同的總額。" },
      { id: "one-piece-card", term: "海賊王卡牌遊戲", def: "以《海賊王》漫畫為基礎的集換式卡牌遊戲。其鑑定版次與寶可夢使用相同的市值方法一同排名。" },
    ],
  },
  about: {
    eyebrow: "關於我們",
    h1: "關於 CardZ Marketcap：鑑定卡市場指數",
    answer:
      "CardZ Marketcap 是鑑定卡的每日市值指數，以已核實 PSA 10 鑑定數量乘以 PSA 10 參考價，為寶可夢與海賊王卡片排名。目前發佈的快照生效日為 {asOf}。",
    descriptor:
      "它之所以存在，是因為這個興趣圈從來不缺價格追蹤，卻沒有考慮供給量的排名。知道一張卡賣了 2,000 美元，並不能告訴你該等級究竟存世兩百張還是二十萬張。",
    sections: [
      {
        heading: "我們發佈什麼",
        body: [
          "以 PSA 10 市值排列的鑑定寶可夢與海賊王版次每日排行榜，每一列都顯示價格、鑑定數量與市值。",
          "每個入榜版次各有專頁，列出其輸入資料與變化。",
          "書面計算方法、名詞解釋，以及可機讀的 API，讓同一組數字可以被查核，而不是靠信任。",
        ],
      },
      {
        heading: "編輯準則",
        body: [
          "每個發佈的數字都由帶日期的快照計算得出。我們不會人手調整排名，也沒有任何卡片可以付費上榜。",
          "缺值就留白。價格或鑑定數量無法核實時，該卡顯示狀態而非數字；我們不會用零代替，也不會借用其他版次的價格。",
          "限制與方法一併公佈，不會埋在角落。計算方法頁列出以鑑定數量為基礎的指數已知弱點，包括報告落後與拆殼重送的重複計算。",
          "更正會在下一份每日快照中處理並寫明，不會靜靜回填。",
        ],
      },
      {
        heading: "更新頻率",
        body: [
          "指數每日重建。每份快照帶有 generation 識別碼與生效日期，每個數字亦帶有其觀測日期。",
          "每頁都標示你正在閱讀的快照生效日期，所以引用自本站的數字永遠可以對應到具體某一天。",
        ],
      },
      {
        heading: "獨立性",
        body: [
          "CardZ Marketcap 不賣卡、不辦拍賣，也不收取任何排名置入費用。",
          "我們具名指出鑑定來源為 PSA，因為其鑑定數量報告公開可查。我們不公開定價供應商的身分。",
        ],
      },
      {
        heading: "如何引用",
        body: [
          "引述數字時請註明 CardZ Marketcap、連回你取數的那一頁，並附上生效日期。例如：CardZ Marketcap，PSA 10 市值，數據截至 {asOf}。",
          "數字可自由引用並註明出處。整批轉載資料集需要書面授權，詳見資料頁的授權說明。",
        ],
      },
    ],
  },
  data: {
    eyebrow: "資料與 API",
    h1: "CardZ Marketcap 資料與 API：以 JSON 取得 PSA 10 市值",
    answer:
      "CardZ Marketcap 透過公開唯讀 API 以 JSON 發佈 PSA 10 市值排名。每個回應都帶有快照 generation、生效日期，以及每張卡的價格、鑑定數量與市值。目前快照生效日為 {asOf}。",
    endpoints: {
      heading: "端點",
      intro: "所有端點皆為 GET、回傳 JSON，且無需金鑰。",
      table: {
        caption: "公開唯讀端點",
        head: ["端點", "回傳內容"],
        rows: [
          ["/api/v1/market", "指定範圍的一頁排名，附快照中繼資料"],
          ["/api/v1/cards/<id>", "以 id 取單張卡；未入榜則回 404 及錯誤內容"],
        ],
      },
    },
    params: {
      heading: "查詢參數",
      intro: "無效值一律回 HTTP 400，不會靜靜退回預設值。",
      table: {
        caption: "/api/v1/market 接受的參數",
        head: ["參數", "可接受值", "備註"],
        rows: [
          ["scope", "all、pokemon、one-piece、watchlist", "其他值回 400"],
          ["page", "正整數", "非數字或 0 回 400"],
          ["pageSize", "正整數", "會被限制在伺服器上限內"],
        ],
      },
    },
    fields: {
      heading: "回應欄位",
      intro: "市場回應會把卡片清單連同快照身分一併包起，讓已儲存的回應永遠追溯得回產生它的那次建置。",
      table: {
        caption: "市場回應的頂層欄位",
        head: ["欄位", "意義"],
        rows: [
          ["generation", "回應所屬快照建置的識別碼"],
          ["generatedAt", "快照建置的時間"],
          ["effectiveAt", "數字所代表的日期"],
          ["coverage", "快照相對其聲稱範圍的完整程度"],
          ["count", "本回應中的卡片數量"],
          ["cards", "已排名卡片，各自帶有價格、鑑定數量與市值"],
        ],
      },
    },
    example: {
      heading: "請求範例",
      intro: "取得寶可夢範圍的第一頁：",
      note: "回應會在邊緣短暫快取。請以 effectiveAt 欄位判斷數字屬於哪一天，而不是看回應時間。",
    },
    cadence: {
      heading: "更新頻率",
      body: [
        "每日發佈新快照，API 提供當前那一份。舊的 generation 不會由這些端點提供。",
        "若你要儲存回應，請連同 generation 與 effectiveAt 一起存。不同 generation 的兩個數字，沒有日期就不可比較。",
      ],
    },
    fairUse: {
      heading: "合理使用",
      body: [
        "API 公開且免驗證，所以請維持合理的請求頻率：讀一頁、快取起來，不要用快過資料變動的速度輪詢。快照一日才動一次。",
        "過量自動化流量可能被限速。若你需要整批存取，請先聯絡我們，不要用爬的。",
      ],
    },
    license: {
      heading: "出處標示與授權",
      body: [
        "數字可依 CC BY 4.0（https://creativecommons.org/licenses/by/4.0/）引用及轉載，惟須註明出處為 CardZ Marketcap，並連回所引用的頁面或端點。",
        "標示時必須附上該數字的生效日期，因為市值每日重新計算。",
      ],
      attributionLabel: "標示格式",
      attributionTemplate: "CardZ Marketcap，{site}，數據截至 {asOf}",
    },
    files: {
      heading: "可機讀檔案",
      intro: "供爬蟲、代理程式與語言模型使用：",
      items: [
        { href: "/llms.txt", label: "/llms.txt", note: "本站與主要頁面的簡短說明" },
        { href: "/llms-full.txt", label: "/llms-full.txt", note: "加長版本，附計算方法摘要" },
        { href: "/sitemap.xml", label: "/sitemap.xml", note: "所有可索引頁面" },
      ],
    },
  },
};

const zhCN: SiteCopy = {
  shell: {
    home: "CardZ Marketcap",
    asOfLabel: "数据截至",
    onThisPage: "本页内容",
    moreHeading: "更多 CardZ Marketcap",
    nav: {
      methodology: "计算方法",
      about: "关于我们",
      faq: "常见问题",
      glossary: "名词解释",
      data: "数据与 API",
    },
    indexLink: "实时排行榜",
    notCrypto: "CardZ Marketcap 是集换式卡牌的价格指数，不是加密货币、不是代币，也不是上市公司。本站没有任何代币代号，与名为 CARDS 的加密资产无关。",
  },
  methodology: {
    eyebrow: "计算方法",
    h1: "CardZ Marketcap 如何计算集换式卡牌市值指数（PSA 10 价格 × 评级数量）",
    answer:
      "CardZ Marketcap 以「PSA 10 参考价 × 已核实 PSA 10 评级数量」计算集换式卡牌市值指数。一张 100 美元、有 10,000 张 PSA 10 的卡，市值就是 1,000,000 美元。指数覆盖宝可梦卡牌市值与海贼王卡牌市值，数据截至 {asOf}。",
    scope:
      "这里的市值指的是「该卡最高评级的全部存世价值」，而不是一张卡的成交价。概念等同股价乘以流通股数：一张便宜但评级数量庞大的卡，排名可以高过一张昂贵但几乎没人送评的卡。",
    formula: {
      heading: "公式",
      body: [
        "PSA 10 市值 ＝ PSA 10 参考价 × PSA 10 评级数量。",
        "PSA 10 参考价是我们为「同一版次」单张 PSA 10 所持有的价格；PSA 10 评级数量则取自 PSA 公开的评级数量报告中，该版次被评为 10 分的张数。",
        "两个数必须来自同一版次。任何一边缺失或过旧，该卡就显示状态而不是数字。我们不会用零代替缺值，也不会把其他语言或其他刷次的价格搬过来。",
      ],
    },
    worked: {
      heading: "实例计算",
      intro: "以 {asOf} 实时榜第 1 名的卡为例：",
      head: ["项目", "数值"],
      rows: [
        { label: "卡片", value: "{topName}" },
        { label: "PSA 10 参考价", value: "{topPrice}" },
        { label: "PSA 10 评级数量", value: "{topPop}" },
        { label: "PSA 10 市值", value: "{topCap}" },
      ],
      note: "整个计算就只有「价格 × 数量」。没有平滑系数、没有流通量调整，也没有任何人为加权。",
    },
    sources: {
      heading: "数据来源与更新频率",
      body: [
        "评级数量来自 PSA 公开的评级数量报告，并以卡包系列、卡号、语言、特殊工艺逐项对应到同一版次。",
        "价格来自同一版次的已评级成交证据，每日采集，并附上明确的观测日期，让你随时看得出这个数字有多新。",
        "已发布的快照每日重建。每张卡都带有价格与评级数量的观测日期，每一页也标注你正在阅读的快照生效日期。",
        "非美元金额使用快照内记录的汇率换算，所以切换币种不会改变排名。",
      ],
    },
    eligibility: {
      heading: "哪些卡纳入、哪些排除",
      body: [
        "一个版次要同时具备「已核实且数量足以有统计意义的 PSA 10 评级数量」与「我们敢负责的价格」，才会进入排名。截至 {asOf}，入榜卡片中最低的 PSA 10 评级数量是 {minPop}。",
        "每个版次分开排名。同一张图的日文版、英文版与中文版是不同行，因为它们的评级数量与价格都不同。",
        "价格或评级数量无法核实的卡，我们宁可不排，也不会用估算填数。未拆封商品与单卡分开追踪，不会并入单卡市值。",
      ],
    },
    gradedVsRaw: {
      heading: "为什么用 PSA 10，而不是裸卡价",
      body: [
        "裸卡没有可核实的供给量。没有人知道 1999 年基础系列还有多少张存世、品相如何，所以「裸卡价 × 印量」只是包装成数字的猜测。",
        "PSA 10 评级数量是被清点并公开的数字，而且只会随着送评增加而单向上升。把它配上同一评级的价格，就得到一个可以核查的市值：两个输入都看得见，也都有日期。",
        "代价是：PSA 10 市值忽略了裸卡与低分卡所承载的价值。它衡量的是顶级品相市场，而不是整个圈子。",
      ],
    },
    limits: {
      heading: "我们不会遮掩的局限",
      body: [
        "评级数量报告落后于现实。本周送评的卡要之后才会出现在报告中，所以急涨的现代卡可能被低估。",
        "冷门卡的价格噪音大。一年只成交几次的版次，一笔异常成交对市值的影响会大过市场真正的变动。",
        "拆壳重送会灌大评级数量，因为同一张实体卡被拆出来重评，可能被重复计算。",
        "市值不等于流动性。没有人能把一张卡所有 PSA 10 都按参考价卖掉；这个数字衡量的是规模，而不是可变现金额。",
      ],
    },
    compare: {
      heading: "与只看价格的指数有何不同",
      intro:
        "大多数卡牌追踪站公布的是「一张卡的价格」。评级卡市场指数把价格乘上已核实供给量，排名会出现明显差异。",
      table: {
        caption: "CardZ Marketcap 与其他被称作「卡牌指数」的东西对比",
        head: ["是什么", "衡量什么", "供给量是否核实"],
        rows: [
          ["CardZ Marketcap", "PSA 10 参考价 × 已核实 PSA 10 评级数量", "是，来自 PSA 评级数量报告"],
          ["纯价格追踪站", "单张卡的最新或平均成交价", "完全没有供给量这一项"],
          ["名为 CARDS 的加密代币", "一个无关数字资产的价格", "并非卡牌产品"],
        ],
      },
      note: "一张 30 美元、有 300,000 张 PSA 10 的卡，市值高过一张 30,000 美元但只有 40 张的卡。纯价格榜看不出这件事，而这正是本指数存在的理由。",
    },
    versioning: {
      heading: "版本与变更",
      body: [
        "每个已发布快照都带有 generation 标识与生效日期。你正在阅读的这一页来自 generation {generation}，生效日 {asOf}。",
        "方法变更一律向前适用并在此注明。我们不会悄悄改写已发布的历史：定义有变就写明变更内容，而不是回填旧数据。",
      ],
    },
    cite: {
      heading: "引用本页",
      intro: "若你在文章、视频或模型回答中引用这些数字，请一并注明方法与日期：",
      template: "CardZ Marketcap，《CardZ Marketcap 如何计算集换式卡牌市值指数》，PSA 10 价格 × PSA 10 评级数量，数据截至 {asOf}。",
      note: "数字每日变动。标明生效日期，读者才知道你用的是哪一份快照。",
    },
    faqHeading: "计算方法常见问题",
    faq: [
      {
        q: "什么是集换式卡牌市值？",
        a: "集换式卡牌市值是单张 PSA 10 的价格，乘以存世 PSA 10 的张数。它衡量的是一张卡在最高评级上的全部供给价值，而不是单张卡的成交价。",
      },
      {
        q: "CardZ Marketcap 怎样计算宝可梦卡牌市值？",
        a: "CardZ Marketcap 把某个宝可梦版次的 PSA 10 参考价，乘以该版次在 PSA 评级数量报告中已核实的 PSA 10 张数。价格与数量每日更新，并附观测日期。",
      },
      {
        q: "为什么用 PSA 10 评级数量而不是印量？",
        a: "印量很少公开，存世张数也无从得知。PSA 10 评级数量是被清点并公开的，可以核查。用它可以让市值算式两边都保持可观测。",
      },
      {
        q: "市值等于这张卡值那么多钱吗？",
        a: "不等于。市值是所有 PSA 10 同时按该价格计算的理论总值。实际上不可能全部按价卖出，所以请把市值视为规模与供给的指标，而不是可变现金额。",
      },
      {
        q: "指数多久更新一次？",
        a: "快照每日重建。每张卡都显示价格与评级数量的观测日期，每一页都显示快照的生效日期，你可以清楚判断一个数字有多新。",
      },
    ],
  },
  faq: {
    eyebrow: "常见问题",
    h1: "宝可梦卡牌市值常见问题：PSA 10 市值一次讲清楚",
    answer:
      "CardZ Marketcap 以「PSA 10 参考价 × 已核实 PSA 10 评级数量」排出宝可梦卡牌市值与海贼王卡牌市值。本页说明这些数字怎样算出来、覆盖什么、以及不能说明什么，数据截至 {asOf}。",
    groups: [
      {
        heading: "基本概念",
        items: [
          {
            q: "CardZ Marketcap 是什么？",
            a: "CardZ Marketcap 是评级卡的每日市值指数，以已核实 PSA 10 评级数量乘以 PSA 10 参考价，为宝可梦与海贼王卡片排名，并公布每份快照的生效日期。",
          },
          {
            q: "什么是 PSA 10 市值？",
            a: "PSA 10 市值是单张 PSA 10 的价格，乘以 PSA 已评为 10 分的张数。它表达的是一张卡在最高评级上的供给总价值，而不是单张的价格。",
          },
          {
            q: "CardZ 是加密货币或代币吗？",
            a: "不是。CardZ Marketcap 是集换式卡牌价格指数，没有代币、没有币、也没有代号，与任何名称相近的加密资产无关，本站也没有任何东西可以买卖。",
          },
          {
            q: "CardZ Marketcap 免费吗？",
            a: "免费。排行榜、卡片页、计算方法与名词解释都可自由阅读；数字也可自由引用，只要注明出处并链接回你取数的那一页。",
          },
        ],
      },
      {
        heading: "数字怎样算出来",
        items: [
          {
            q: "集换式卡牌市值怎样计算？",
            a: "市值等于同一版次的 PSA 10 参考价乘以已核实 PSA 10 评级数量。两项输入都附观测日期；任何一边无法核实，该卡就不会进入排名。",
          },
          {
            q: "评级数量的数据从哪里来？",
            a: "来自 PSA 公开的评级数量报告，并对应到确切的卡包系列、卡号、语言与特殊工艺。我们只取 PSA 10 的张数，低分评级不计入市值。",
          },
          {
            q: "数字多久更新一次？",
            a: "已发布的快照每日重建。每张卡都显示价格与评级数量的观测时间，所以长期未变动的数字会清楚地显示为旧数据，而不是被无声刷新。",
          },
          {
            q: "为什么有些卡没有数字？",
            a: "因为该版次在该日期的数值无法核实。CardZ Marketcap 宁可留空，也不会用零代替或凭估算填数——那会悄悄污染整个排名。",
          },
          {
            q: "数字用什么币种？",
            a: "所有数值以美元计算，显示时按快照内记录的汇率换算。切换币种只影响显示，排名不会因此改变。",
          },
        ],
      },
      {
        heading: "覆盖范围",
        items: [
          {
            q: "覆盖哪些卡？",
            a: "覆盖具备已核实 PSA 10 评级数量、且价格可负责的宝可梦与海贼王单卡。截至 {asOf}，共排名 {ranked} 个版次：宝可梦 {pokemon} 个、海贼王 {onePiece} 个。",
          },
          {
            q: "日文版与英文版会分开排名吗？",
            a: "会。每个版次各自成行，因为各有各的评级数量与价格。同一张图的另一语言版本在排名上是另一张卡，绝不会取两者平均。",
          },
          {
            q: "有覆盖未拆封原盒吗？",
            a: "未拆封商品与单卡分开追踪。它没有 PSA 10 评级数量，所以无法进入单卡市值排名，也不会被混入那些总额。",
          },
          {
            q: "追踪中的总市值是多少？",
            a: "截至 {asOf}，已发布指数在 {ranked} 个版次上共追踪 {totalCap} 的 PSA 10 市值。该总额会随价格变动与新增评级张数每日改变。",
          },
        ],
      },
      {
        heading: "看懂排行榜",
        items: [
          {
            q: "以市值计，最有价值的宝可梦卡是哪些？",
            a: "排名实时公布并每日重排。市值领先者同时具备高 PSA 10 价格与庞大评级数量，所以榜首名单通常与「最贵单卡」名单并不相同。",
          },
          {
            q: "为什么便宜的卡排在昂贵的卡前面？",
            a: "因为市值是价格乘以供给量。一张 30 美元、有 300,000 张 PSA 10 的卡，总价值高过一张 30,000 美元但只有 40 张的卡，即使单张便宜得多。",
          },
          {
            q: "我可以在文章或视频里引用这些数字吗？",
            a: "可以，请注明出处为 CardZ Marketcap 并链接回你取数的那一页，同时附上生效日期，因为市值数字每日都在变。",
          },
        ],
      },
    ],
  },
  glossary: {
    eyebrow: "名词解释",
    h1: "评级卡市场指数名词解释：PSA 10 市值用语",
    answer:
      "本页解释 CardZ Marketcap 为评级卡排名时使用的名词，包括 PSA 10 市值、评级数量报告、宝石率与已核实供给量。定义适用于 {asOf} 所发布的指数。",
    setName: "CardZ Marketcap 评级卡名词解释",
    setDescription: "CardZ Marketcap 评级卡市场指数所使用的评级、定价与市值名词定义。",
    terms: [
      { id: "market-cap", term: "市值（集换式卡牌）", def: "某版次的 PSA 10 参考价乘以已核实 PSA 10 评级数量。衡量的是最高评级供给的总价值，而非单张卡的价格。" },
      { id: "psa-10-market-cap", term: "PSA 10 市值", def: "只用 PSA 10 输入计算的市值：PSA 10 单张价格与 PSA 10 张数。低分评级不计入。" },
      { id: "psa", term: "PSA", def: "Professional Sports Authenticator，第三方评级公司。PSA 为送评卡片评定分数，并公布各评级的数量统计。" },
      { id: "psa-10", term: "PSA 10", def: "PSA 的最高标准分数，称为 Gem Mint。CardZ Marketcap 的价格与供给量两边都采用这个评级。" },
      { id: "population-report", term: "评级数量报告", def: "PSA 公布的各评级已评张数统计，是市值中供给量那一项的来源。" },
      { id: "pop", term: "Pop", def: "population 的简称。Pop 10 指某个系列、语言与特殊工艺下的特定版次被评为 PSA 10 的张数。" },
      { id: "reference-price", term: "参考价", def: "CardZ Marketcap 在某一日为某版次单张 PSA 10 所持有的价格，取自该版次的已评级成交证据。" },
      { id: "printing", term: "版次", def: "一张卡的特定发行：系列、卡号、语言与特殊工艺。同一张图的不同版次分成不同行排名。" },
      { id: "finish", term: "特殊工艺", def: "卡面的表面处理，例如闪面、反闪或普卡。工艺同时影响稀有度与价格，所以属于版次身份的一部分。" },
      { id: "parallel", term: "平行卡", def: "以不同工艺或编号印制的同卡替代版本。平行卡有自己的评级数量与价格，永远不与本卡共用市值行。" },
      { id: "promo", term: "宣传卡", def: "在一般卡包以外发放的卡，例如活动卡或联名卡。宣传卡通常评级数量较少、价格波动较大。" },
      { id: "set-code", term: "系列代码", def: "卡片所属系列的简短标识。与卡号合并使用，可判定价格与评级数量属于哪个版次。" },
      { id: "collector-number", term: "卡号", def: "印在卡上的编号，代表在系列中的位置，例如 085/SVP。是版次对应评级数量数据的依据之一。" },
      { id: "grading", term: "评级", def: "把卡片送交第三方评估居中、边角、边缘与表面，最后封装并给出分数的流程。" },
      { id: "gem-rate", term: "宝石率", def: "送评卡片中取得 PSA 10 的比例。宝石率低表示即使大量送评，PSA 10 供给量仍增加得很慢。" },
      { id: "raw", term: "裸卡", def: "未评级的卡。裸卡没有可核实的供给量统计，因此不用于市值计算。" },
      { id: "ungraded-reference", term: "裸卡参考价", def: "裸卡的参考性价格，仅供对照，永远不会进入市值计算。" },
      { id: "slab", term: "评级壳", def: "封装评级卡的密封胶壳，上面标注分数与鉴定编号。" },
      { id: "cert-number", term: "鉴定编号", def: "指派给一张评级卡的唯一编号，标识的是一个实体壳，而不是一个版次。" },
      { id: "crack-and-resubmit", term: "拆壳重送", def: "把卡从评级壳取出再送评以争取更高分数。这会令同一张实体卡在评级数量中被重复计算。" },
      { id: "supply", term: "已核实供给量", def: "一张卡中已被评级公司清点的供给部分。CardZ Marketcap 采用已核实供给量，而不是估算印量。" },
      { id: "print-run", term: "印量", def: "原始生产的张数。集换式卡牌的印量甚少公开，本指数不使用。" },
      { id: "index", term: "评级卡市场指数", def: "以评级数据计算价值指标的卡片排名。CardZ Marketcap 就是建立在 PSA 10 价格与评级数量之上的评级卡市场指数。" },
      { id: "snapshot", term: "快照", def: "指数的一次已发布构建，由 generation 与生效日期标识。本站每个数字都属于某一份快照。" },
      { id: "effective-date", term: "生效日期", def: "快照数字所代表的日期。每页都会标注，让引用的数字可以对应到具体某一天。" },
      { id: "observation-date", term: "观测日期", def: "某个价格或评级数量被观测到的日期。来源未变动时，它可以早于生效日期。" },
      { id: "accumulating", term: "累积中", def: "证据尚不足以发布数值时显示的状态。代表数据仍在采集，不代表数值为零。" },
      { id: "stale", term: "数据偏旧", def: "数值存在但观测日期较旧时显示的状态。数字仍会连同日期显示，而不是被隐藏。" },
      { id: "coverage", term: "覆盖度", def: "快照相对其所声称排名范围的完整程度。每份快照都会公布，让缺口可见。" },
      { id: "watchlist", term: "观察清单", def: "百强以外、以相同方法追踪的版次。输入完全一致，并计入相同的总额。" },
      { id: "one-piece-card", term: "海贼王卡牌游戏", def: "以《海贼王》漫画为基础的集换式卡牌游戏。其评级版次与宝可梦使用相同的市值方法一同排名。" },
    ],
  },
  about: {
    eyebrow: "关于我们",
    h1: "关于 CardZ Marketcap：评级卡市场指数",
    answer:
      "CardZ Marketcap 是评级卡的每日市值指数，以已核实 PSA 10 评级数量乘以 PSA 10 参考价，为宝可梦与海贼王卡片排名。目前发布的快照生效日为 {asOf}。",
    descriptor:
      "它之所以存在，是因为这个圈子从来不缺价格追踪，却没有考虑供给量的排名。知道一张卡卖了 2,000 美元，并不能告诉你该评级究竟存世两百张还是二十万张。",
    sections: [
      {
        heading: "我们发布什么",
        body: [
          "以 PSA 10 市值排列的评级宝可梦与海贼王版次每日排行榜，每一行都显示价格、评级数量与市值。",
          "每个入榜版次各有专页，列出其输入数据与变化。",
          "书面计算方法、名词解释，以及可机读的 API，让同一组数字可以被核查，而不是靠信任。",
        ],
      },
      {
        heading: "编辑准则",
        body: [
          "每个发布的数字都由带日期的快照计算得出。我们不会人工调整排名，也没有任何卡片可以付费上榜。",
          "缺值就留空。价格或评级数量无法核实时，该卡显示状态而非数字；我们不会用零代替，也不会借用其他版次的价格。",
          "局限与方法一并公布，不会埋在角落。计算方法页列出以评级数量为基础的指数已知弱点，包括报告落后与拆壳重送的重复计算。",
          "更正会在下一份每日快照中处理并写明，不会悄悄回填。",
        ],
      },
      {
        heading: "更新频率",
        body: [
          "指数每日重建。每份快照带有 generation 标识与生效日期，每个数字也带有其观测日期。",
          "每页都标注你正在阅读的快照生效日期，所以引用自本站的数字永远可以对应到具体某一天。",
        ],
      },
      {
        heading: "独立性",
        body: [
          "CardZ Marketcap 不卖卡、不办拍卖，也不收取任何排名植入费用。",
          "我们具名指出评级来源为 PSA，因为其评级数量报告公开可查。我们不公开定价供应商的身份。",
        ],
      },
      {
        heading: "如何引用",
        body: [
          "引述数字时请注明 CardZ Marketcap、链接回你取数的那一页，并附上生效日期。例如：CardZ Marketcap，PSA 10 市值，数据截至 {asOf}。",
          "数字可自由引用并注明出处。整批转载数据集需要书面授权，详见数据页的授权说明。",
        ],
      },
    ],
  },
  data: {
    eyebrow: "数据与 API",
    h1: "CardZ Marketcap 数据与 API：以 JSON 获取 PSA 10 市值",
    answer:
      "CardZ Marketcap 通过公开只读 API 以 JSON 发布 PSA 10 市值排名。每个响应都带有快照 generation、生效日期，以及每张卡的价格、评级数量与市值。当前快照生效日为 {asOf}。",
    endpoints: {
      heading: "端点",
      intro: "所有端点均为 GET、返回 JSON，且无需密钥。",
      table: {
        caption: "公开只读端点",
        head: ["端点", "返回内容"],
        rows: [
          ["/api/v1/market", "指定范围的一页排名，附快照元数据"],
          ["/api/v1/cards/<id>", "以 id 取单张卡；未入榜则返回 404 及错误内容"],
        ],
      },
    },
    params: {
      heading: "查询参数",
      intro: "无效值一律返回 HTTP 400，不会悄悄退回默认值。",
      table: {
        caption: "/api/v1/market 接受的参数",
        head: ["参数", "可接受值", "备注"],
        rows: [
          ["scope", "all、pokemon、one-piece、watchlist", "其他值返回 400"],
          ["page", "正整数", "非数字或 0 返回 400"],
          ["pageSize", "正整数", "会被限制在服务器上限内"],
        ],
      },
    },
    fields: {
      heading: "响应字段",
      intro: "市场响应会把卡片列表连同快照身份一并包起，让已存储的响应永远追溯得回产生它的那次构建。",
      table: {
        caption: "市场响应的顶层字段",
        head: ["字段", "含义"],
        rows: [
          ["generation", "响应所属快照构建的标识"],
          ["generatedAt", "快照构建的时间"],
          ["effectiveAt", "数字所代表的日期"],
          ["coverage", "快照相对其声称范围的完整程度"],
          ["count", "本响应中的卡片数量"],
          ["cards", "已排名卡片，各自带有价格、评级数量与市值"],
        ],
      },
    },
    example: {
      heading: "请求示例",
      intro: "获取宝可梦范围的第一页：",
      note: "响应会在边缘短暂缓存。请以 effectiveAt 字段判断数字属于哪一天，而不是看响应时间。",
    },
    cadence: {
      heading: "更新频率",
      body: [
        "每日发布新快照，API 提供当前那一份。旧的 generation 不会由这些端点提供。",
        "若你要存储响应，请连同 generation 与 effectiveAt 一起存。不同 generation 的两个数字，没有日期就不可比较。",
      ],
    },
    fairUse: {
      heading: "合理使用",
      body: [
        "API 公开且免鉴权，所以请保持合理的请求频率：读一页、缓存起来，不要用快过数据变动的速度轮询。快照一日才动一次。",
        "过量自动化流量可能被限速。若你需要整批访问，请先联系我们，不要用爬的。",
      ],
    },
    license: {
      heading: "出处标注与授权",
      body: [
        "数字可依 CC BY 4.0（https://creativecommons.org/licenses/by/4.0/）引用及转载，但须注明出处为 CardZ Marketcap，并链接回所引用的页面或端点。",
        "标注时必须附上该数字的生效日期，因为市值每日重新计算。",
      ],
      attributionLabel: "标注格式",
      attributionTemplate: "CardZ Marketcap，{site}，数据截至 {asOf}",
    },
    files: {
      heading: "可机读文件",
      intro: "供爬虫、智能体与语言模型使用：",
      items: [
        { href: "/llms.txt", label: "/llms.txt", note: "本站与主要页面的简短说明" },
        { href: "/llms-full.txt", label: "/llms-full.txt", note: "加长版本，附计算方法摘要" },
        { href: "/sitemap.xml", label: "/sitemap.xml", note: "所有可索引页面" },
      ],
    },
  },
};

const ja: SiteCopy = {
  shell: {
    home: "CardZ Marketcap",
    asOfLabel: "データ基準日",
    onThisPage: "このページの内容",
    moreHeading: "CardZ Marketcap の他のページ",
    nav: {
      methodology: "算出方法",
      about: "運営について",
      faq: "よくある質問",
      glossary: "用語集",
      data: "データと API",
    },
    indexLink: "ランキングを見る",
    notCrypto: "CardZ Marketcap はトレカの価格指数です。暗号資産でもトークンでも上場企業でもありません。ティッカーは存在せず、CARDS という名称の暗号資産とは無関係です。",
  },
  methodology: {
    eyebrow: "算出方法",
    h1: "CardZ Marketcap のトレカ時価総額の算出方法（PSA10 価格 × 鑑定枚数）",
    answer:
      "CardZ Marketcap はトレカ時価総額を「PSA10 参考価格 × 確認済み PSA10 鑑定枚数」で算出します。1 枚 100 ドルで PSA10 が 10,000 枚あれば時価総額は 1,000,000 ドルです。対象はポケモンカード時価総額とワンピースカード時価総額、データ基準日は {asOf} です。",
    scope:
      "ここでいう時価総額とは「最高鑑定グレードで現存する全枚数の総価値」であり、1 枚の取引価格ではありません。株価×発行済株式数と同じ考え方で、安くても鑑定枚数が膨大なカードは、高額でもほとんど鑑定に出されていないカードより上位になり得ます。",
    formula: {
      heading: "計算式",
      body: [
        "PSA10 時価総額 ＝ PSA10 参考価格 × PSA10 鑑定枚数。",
        "PSA10 参考価格は、まったく同じ版（プリンティング）の PSA10 1 枚に対して当サイトが保持する価格です。PSA10 鑑定枚数は、PSA が公表するポピュレーションレポートで、その版が 10 と判定された枚数です。",
        "掛け算の両辺は同じ版から取る必要があります。どちらかが欠落または古い場合、そのカードは数値ではなくステータスを表示します。欠損値をゼロで代用することはなく、別言語や別刷りの価格を流用することもありません。",
      ],
    },
    worked: {
      heading: "計算例",
      intro: "{asOf} 時点でランキング 1 位のカードを例にすると：",
      head: ["項目", "値"],
      rows: [
        { label: "カード", value: "{topName}" },
        { label: "PSA10 参考価格", value: "{topPrice}" },
        { label: "PSA10 鑑定枚数", value: "{topPop}" },
        { label: "PSA10 時価総額", value: "{topCap}" },
      ],
      note: "計算は「価格 × 枚数」だけです。平滑化係数も、流通量の調整も、編集部による重み付けも一切加えていません。",
    },
    sources: {
      heading: "データソースと更新頻度",
      body: [
        "鑑定枚数は PSA が公表するポピュレーションレポートから取得し、セット・カード番号・言語・仕様で同一の版に突き合わせています。",
        "価格は同じ版の鑑定済み取引の証跡から日次で収集し、観測日を明示して保持します。その数値がどれだけ新しいかが常に分かるようにするためです。",
        "公開スナップショットは毎日再構築されます。各カードは価格と鑑定枚数の観測日を保持し、各ページには閲覧中のスナップショットの基準日を表示します。",
        "米ドル以外の表示は、スナップショットに記録された為替レートで換算します。通貨を切り替えても順位は変わりません。",
      ],
    },
    eligibility: {
      heading: "対象になるカード、ならないカード",
      body: [
        "統計的に意味のある規模の確認済み PSA10 鑑定枚数と、当サイトが責任を持てる価格の両方が揃った版のみランキングに入ります。{asOf} 時点で、掲載カードの PSA10 鑑定枚数の最小値は {minPop} です。",
        "版ごとに別々に順位付けします。同じイラストでも日本語版・英語版・中国語版は別の行です。鑑定枚数も価格も異なるためです。",
        "価格または鑑定枚数を確認できないカードは、推計で埋めるのではなくランキングから外します。未開封商品はシングルカードとは別に管理し、シングルの時価総額には合算しません。",
      ],
    },
    gradedVsRaw: {
      heading: "なぜ生カードではなく PSA10 なのか",
      body: [
        "生カードには確認可能な供給量がありません。1999 年の基本セットが今何枚残っていて、どんな状態かは誰にも分かりません。つまり「生価格 × 印刷枚数」は数値の形をした推測にすぎません。",
        "PSA10 鑑定枚数は数え上げられ公表された数字で、提出が増えるにつれ一方向にしか動きません。同じグレードの価格と組み合わせれば、両方の入力が観測可能で日付も付いた、検証できる時価総額になります。",
        "代償として、PSA10 時価総額は生カードや低グレードが持つ価値を無視します。測っているのは最高グレード市場であり、趣味全体ではありません。",
      ],
    },
    limits: {
      heading: "隠さずに書く限界",
      body: [
        "ポピュレーションレポートは現実に遅れます。今週提出されたカードが反映されるのは後日なので、急騰中の現行カードは過小評価されることがあります。",
        "取引の薄いカードは価格ノイズが大きくなります。年に数回しか売れない版では、1 件の異常な取引が実際の市場変動より大きく時価総額を動かします。",
        "殻を割っての再提出は鑑定枚数を水増しします。同じ実物カードが割られて再鑑定されると、二重に数えられ得るためです。",
        "時価総額は流動性ではありません。1 枚の参考価格で PSA10 全枚数を売り切ることは誰にもできません。この数値が示すのは規模であり、換金額ではありません。",
      ],
    },
    compare: {
      heading: "価格だけの指数との違い",
      intro:
        "多くのトレカ相場サイトが公開しているのは「1 枚あたりの価格」です。鑑定カード市場指数はその価格に確認済みの供給量を掛けるため、順位は大きく変わります。",
      table: {
        caption: "「カード指数」と呼ばれるものとの比較",
        head: ["対象", "測っているもの", "供給量の確認"],
        rows: [
          ["CardZ Marketcap", "PSA10 参考価格 × 確認済み PSA10 鑑定枚数", "あり（PSA ポピュレーションレポート）"],
          ["価格のみの相場サイト", "1 枚の直近取引価格または平均価格", "供給量の項目自体がない"],
          ["CARDS という名の暗号トークン", "無関係なデジタル資産の価格", "トレカ関連の商品ではない"],
        ],
      },
      note: "30 ドルで PSA10 が 300,000 枚あるカードは、30,000 ドルで 40 枚しかないカードより上位になります。価格のみのランキングではこれが見えません。この指数が存在する理由はまさにそこです。",
    },
    versioning: {
      heading: "バージョンと変更",
      body: [
        "公開される各スナップショットには generation 識別子と基準日が付きます。このページは generation {generation}、基準日 {asOf} のスナップショットから生成されています。",
        "算出方法の変更は将来分から適用し、ここに明記します。公開済みの履歴を黙って書き換えることはしません。定義が変わった場合は、遡って埋め戻すのではなく変更内容を記載します。",
      ],
    },
    cite: {
      heading: "このページの引用",
      intro: "記事・動画・モデルの回答でこの数値を引用する場合は、算出方法と日付も併記してください：",
      template: "CardZ Marketcap「CardZ Marketcap のトレカ時価総額の算出方法」PSA10 価格 × PSA10 鑑定枚数、データ基準日 {asOf}。",
      note: "数値は日々動きます。基準日を書けば、読者はどのスナップショットを使ったか分かります。",
    },
    faqHeading: "算出方法についてのよくある質問",
    faq: [
      {
        q: "トレカの時価総額とは何ですか。",
        a: "トレカの時価総額とは、PSA10 1 枚の価格に、現存する PSA10 の枚数を掛けた値です。1 枚の取引価格ではなく、そのカードの最高グレード供給全体の価値を示します。",
      },
      {
        q: "ポケカの時価総額はどう計算していますか。",
        a: "対象となるポケモンカードの版の PSA10 参考価格に、PSA のポピュレーションレポートで確認した同じ版の PSA10 枚数を掛けます。価格も枚数も日次で更新し、観測日を付けています。",
      },
      {
        q: "なぜ印刷枚数ではなく PSA10 鑑定枚数を使うのですか。",
        a: "印刷枚数はほとんど公表されず、現存枚数も分かりません。PSA10 鑑定枚数は数え上げられ公表されているため検証できます。計算式の両辺を観測可能に保てます。",
      },
      {
        q: "時価総額はそのカードの価値額ということですか。",
        a: "違います。時価総額は PSA10 全枚数を同時にその価格で評価した理論値です。全部を同じ価格で売ることは不可能なので、換金額ではなく規模と供給の指標として見てください。",
      },
      {
        q: "指数はどのくらいの頻度で更新されますか。",
        a: "スナップショットは毎日再構築されます。各カードには価格と鑑定枚数の観測日、各ページにはスナップショットの基準日が表示されるため、数値の鮮度が正確に分かります。",
      },
    ],
  },
  faq: {
    eyebrow: "よくある質問",
    h1: "ポケモンカード時価総額のFAQ：PSA10 時価総額の疑問に回答",
    answer:
      "CardZ Marketcap は「PSA10 参考価格 × 確認済み PSA10 鑑定枚数」でポケモンカード時価総額とワンピースカード時価総額をランキングしています。本ページでは数値の作り方、対象範囲、そして分からないことを説明します。データ基準日は {asOf} です。",
    groups: [
      {
        heading: "基本",
        items: [
          {
            q: "CardZ Marketcap とは何ですか。",
            a: "CardZ Marketcap は鑑定済みトレカの日次時価総額指数です。確認済み PSA10 鑑定枚数に PSA10 参考価格を掛けてポケモンとワンピースのカードを順位付けし、各スナップショットの基準日を公開しています。",
          },
          {
            q: "PSA10 時価総額とは何ですか。",
            a: "PSA10 時価総額とは、PSA10 1 枚の価格に PSA が 10 と判定した枚数を掛けた値です。1 枚の価格ではなく、最高グレード供給に存在する価値の総量を表します。",
          },
          {
            q: "CardZ は暗号資産やトークンですか。",
            a: "いいえ。CardZ Marketcap はトレカの価格指数で、トークンもコインもティッカーもありません。名称の似た暗号資産とは無関係で、当サイトで売買できるものは一切ありません。",
          },
          {
            q: "CardZ Marketcap は無料ですか。",
            a: "無料です。ランキング、カードページ、算出方法、用語集はすべて自由に閲覧できます。数値も、出典明記と参照元ページへのリンクがあれば自由に引用できます。",
          },
        ],
      },
      {
        heading: "数値の作り方",
        items: [
          {
            q: "トレカの時価総額はどう計算しますか。",
            a: "時価総額は、同じ版の PSA10 参考価格に確認済み PSA10 鑑定枚数を掛けた値です。両方の入力に観測日が付き、どちらかが確認できない場合そのカードはランキングに入りません。",
          },
          {
            q: "鑑定枚数のデータはどこから来ますか。",
            a: "PSA が公表するポピュレーションレポートから取得し、セット・カード番号・言語・仕様が一致する版に突き合わせています。使うのは PSA10 の枚数のみで、下位グレードは時価総額に含めません。",
          },
          {
            q: "数値はどのくらいの頻度で更新されますか。",
            a: "公開スナップショットは毎日再構築されます。各カードには価格と鑑定枚数の観測日が表示されるため、しばらく動いていない数値は黙って更新されるのではなく、古いものとして見えます。",
          },
          {
            q: "数値が表示されないカードがあるのはなぜですか。",
            a: "その日その版で値を確認できなかったためです。CardZ Marketcap はゼロで代用したり推計で埋めたりせず空欄のままにします。埋めればランキング全体が静かに壊れるからです。",
          },
          {
            q: "通貨は何ですか。",
            a: "数値は米ドルで計算し、表示時にスナップショットへ記録された為替レートで換算します。通貨の切り替えは表示だけを変え、順位は変わりません。",
          },
        ],
      },
      {
        heading: "対象範囲",
        items: [
          {
            q: "どのカードが対象ですか。",
            a: "確認済み PSA10 鑑定枚数があり、価格に責任を持てるポケモンとワンピースのシングルカードが対象です。{asOf} 時点で {ranked} 版を掲載しており、内訳はポケモン {pokemon} 版、ワンピース {onePiece} 版です。",
          },
          {
            q: "日本語版と英語版は分けて順位付けされますか。",
            a: "はい。鑑定枚数も価格も異なるため、版ごとに別の行になります。同じイラストの別言語版はランキング上は別のカードで、両者を平均することはありません。",
          },
          {
            q: "未開封ボックスも対象ですか。",
            a: "未開封商品はシングルカードとは別に管理しています。PSA10 鑑定枚数が存在しないためシングルの時価総額ランキングには入らず、その合計にも混ざりません。",
          },
          {
            q: "追跡中の時価総額の合計はいくらですか。",
            a: "{asOf} 時点で、公開指数は {ranked} 版について合計 {totalCap} の PSA10 時価総額を追跡しています。この合計は価格の変動と、新たに鑑定された枚数によって日々変わります。",
          },
        ],
      },
      {
        heading: "ランキングの読み方",
        items: [
          {
            q: "時価総額で見た最も価値のあるポケモンカードは何ですか。",
            a: "ランキングは随時公開され、毎日並び替わります。上位は高い PSA10 価格と大きな鑑定枚数を併せ持つため、「最も高額な 1 枚」の一覧とはたいてい一致しません。",
          },
          {
            q: "なぜ安いカードが高いカードより上位なのですか。",
            a: "時価総額は価格に供給量を掛けるからです。30 ドルで PSA10 が 300,000 枚あるカードは、1 枚の価格がはるかに安くても、30,000 ドルで 40 枚のカードより総額が大きくなります。",
          },
          {
            q: "記事や動画でこの数値を使えますか。",
            a: "使えます。出典を CardZ Marketcap と明記し、参照元ページへリンクしてください。時価総額は毎日変わるため、基準日も併記してください。",
          },
        ],
      },
    ],
  },
  glossary: {
    eyebrow: "用語集",
    h1: "鑑定カード市場指数の用語集：PSA10 時価総額の用語",
    answer:
      "この用語集は、CardZ Marketcap が鑑定カードを順位付けする際に使う用語を定義します。PSA10 時価総額、ポピュレーションレポート、ジェムレート、確認済み供給量などが対象です。定義は {asOf} 公開の指数に適用されます。",
    setName: "CardZ Marketcap 鑑定カード用語集",
    setDescription: "CardZ Marketcap の鑑定カード市場指数で使用する、鑑定・価格・時価総額に関する用語の定義。",
    terms: [
      { id: "market-cap", term: "時価総額（トレカ）", def: "ある版の PSA10 参考価格に確認済み PSA10 鑑定枚数を掛けた値。1 枚の価格ではなく、最高グレード供給の総価値を示す。" },
      { id: "psa-10-market-cap", term: "PSA10 時価総額", def: "PSA10 の入力だけで算出した時価総額。PSA10 1 枚の価格と PSA10 の枚数を使い、下位グレードは含めない。" },
      { id: "psa", term: "PSA", def: "Professional Sports Authenticator。第三者鑑定会社で、提出されたカードにグレードを付け、グレードごとの枚数を公表する。" },
      { id: "psa-10", term: "PSA10", def: "PSA の最高標準グレードで Gem Mint と呼ばれる。CardZ Marketcap は価格側と供給側の両方でこのグレードを使う。" },
      { id: "population-report", term: "ポピュレーションレポート", def: "PSA が公表する、各グレードで鑑定した枚数の集計。時価総額の供給項の出典。" },
      { id: "pop", term: "ポップ", def: "population の略。Pop 10 は、特定のセット・言語・仕様の版で PSA10 と判定された枚数を指す。" },
      { id: "reference-price", term: "参考価格", def: "ある日付において、ある版の PSA10 1 枚に対して CardZ Marketcap が保持する価格。同じ版の鑑定済み取引の証跡から導く。" },
      { id: "printing", term: "版（プリンティング）", def: "セット・カード番号・言語・仕様で特定される個々の発売形態。同じイラストでも版が違えば別行として順位付けする。" },
      { id: "finish", term: "仕様（フィニッシュ）", def: "ホロ、リバースホロ、ノンホロなどカード表面の加工。希少性と価格の両方に影響するため、版の一部として扱う。" },
      { id: "parallel", term: "パラレル", def: "加工やナンバリングを変えて印刷された別バージョン。独自の鑑定枚数と価格を持ち、通常版と時価総額の行を共有しない。" },
      { id: "promo", term: "プロモ", def: "通常のパックとは別に配布されるカード。イベントやコラボが典型で、鑑定枚数が少なく価格が動きやすい。" },
      { id: "set-code", term: "セットコード", def: "カードが属するセットの短い識別子。カード番号と組み合わせて、価格と鑑定枚数がどの版のものかを特定する。" },
      { id: "collector-number", term: "カード番号", def: "セット内での位置を示す、カードに印刷された番号（例：085/SVP）。版とポピュレーションデータを突き合わせる手掛かりの一つ。" },
      { id: "grading", term: "鑑定（グレーディング）", def: "センタリング・角・縁・表面を第三者が評価し、グレードを付けて封入するまでの一連の工程。" },
      { id: "gem-rate", term: "ジェムレート", def: "提出されたカードのうち PSA10 が付く割合。低いほど、提出が多くても PSA10 の供給はゆっくりしか増えない。" },
      { id: "raw", term: "生カード", def: "未鑑定のカード。確認可能な供給枚数がないため、時価総額の計算には使わない。" },
      { id: "ungraded-reference", term: "生カード参考価格", def: "生カードの目安価格。参考表示のみで、時価総額の計算には一切入らない。" },
      { id: "slab", term: "スラブ", def: "鑑定済みカードを封入する密閉ケース。グレードと認証番号が記載されている。" },
      { id: "cert-number", term: "認証番号", def: "鑑定済みカード 1 枚ごとに割り当てられる固有番号。版ではなく個々の実物スラブを識別する。" },
      { id: "crack-and-resubmit", term: "クラック再提出", def: "スラブからカードを取り出し、より高いグレードを狙って再提出すること。同じ実物カードが枚数に二重計上され得る。" },
      { id: "supply", term: "確認済み供給量", def: "鑑定会社によって数え上げられた供給の部分。CardZ Marketcap は推定印刷枚数ではなくこちらを使う。" },
      { id: "print-run", term: "印刷枚数", def: "当初生産された枚数。トレカでは公表されることがほとんどなく、この指数では使用しない。" },
      { id: "index", term: "鑑定カード市場指数", def: "鑑定データから算出した価値指標でカードを順位付けしたもの。CardZ Marketcap は PSA10 の価格と鑑定枚数に基づく鑑定カード市場指数。" },
      { id: "snapshot", term: "スナップショット", def: "指数の一回分の公開ビルド。generation と基準日で識別される。本サイトのすべての数値はいずれかのスナップショットに属する。" },
      { id: "effective-date", term: "基準日", def: "スナップショットの数値が表す日付。全ページに表示され、引用した数値を特定の日に紐付けられる。" },
      { id: "observation-date", term: "観測日", def: "個々の価格や鑑定枚数が観測された日付。ソースが動いていなければ基準日より前になり得る。" },
      { id: "accumulating", term: "収集中", def: "値を公開するだけの証跡がまだない場合に表示するステータス。収集中という意味であり、値がゼロという意味ではない。" },
      { id: "stale", term: "情報が古い", def: "値はあるが観測日が古い場合に表示するステータス。数値は隠さず、日付と併せて表示する。" },
      { id: "coverage", term: "カバレッジ", def: "掲げた対象範囲に対して、そのスナップショットがどれだけ揃っているか。各スナップショットで公開し、欠落を見えるようにしている。" },
      { id: "watchlist", term: "ウォッチリスト", def: "上位 100 位の外側で、同じ方法により追跡している版。入力は完全に同一で、同じ合計に含まれる。" },
      { id: "one-piece-card", term: "ワンピースカードゲーム", def: "漫画『ONE PIECE』を題材にしたトレーディングカードゲーム。鑑定済みの版はポケモンと同じ方法で並べて順位付けする。" },
    ],
  },
  about: {
    eyebrow: "運営について",
    h1: "CardZ Marketcap について：鑑定カード市場指数",
    answer:
      "CardZ Marketcap は鑑定済みトレカの日次時価総額指数です。確認済み PSA10 鑑定枚数に PSA10 参考価格を掛けて、ポケモンとワンピースのカードを順位付けしています。公開中のスナップショットの基準日は {asOf} です。",
    descriptor:
      "この指数があるのは、この趣味に価格の記録は山ほどあっても、供給量を踏まえたランキングが無かったからです。あるカードが 2,000 ドルで売れたと知っても、そのグレードが 200 枚あるのか 200,000 枚あるのかは分かりません。",
    sections: [
      {
        heading: "公開しているもの",
        body: [
          "鑑定済みのポケモンとワンピースの版を PSA10 時価総額で並べた日次ランキング。各行に価格・鑑定枚数・時価総額を表示します。",
          "掲載している版ごとの個別ページ。入力値とその推移を掲載します。",
          "文章による算出方法、用語集、そして機械可読な API。同じ数値を信用ではなく検証で確かめられるようにしています。",
        ],
      },
      {
        heading: "編集方針",
        body: [
          "公開する数値はすべて、日付の付いたスナップショットから計算しています。順位を手作業で調整することはなく、支払いによって掲載順位が上がることもありません。",
          "欠損値は欠損のままにします。価格や鑑定枚数を確認できない場合は数値ではなくステータスを表示し、ゼロで代用したり別の版から価格を借りたりしません。",
          "限界は算出方法と並べて公開し、隅に埋めたりしません。算出方法のページには、報告の遅れや再提出による二重計上など、鑑定枚数ベースの指数の既知の弱点を記載しています。",
          "訂正は翌日のスナップショットで反映し、内容を記載します。黙って埋め戻すことはしません。",
        ],
      },
      {
        heading: "更新頻度",
        body: [
          "指数は毎日再構築されます。各スナップショットには generation 識別子と基準日が付き、各数値には観測日が付きます。",
          "各ページには閲覧中のスナップショットの基準日を表示するため、当サイトから引用した数値は常に特定の日に紐付けられます。",
        ],
      },
      {
        heading: "独立性",
        body: [
          "CardZ Marketcap はカードを販売せず、オークションも運営せず、ランキング掲載の対価を受け取ることもありません。",
          "鑑定のソースとして PSA を明記しているのは、そのポピュレーションレポートが公開され検証できるからです。価格データの供給元の名称は公開しません。",
        ],
      },
      {
        heading: "引用方法",
        body: [
          "数値を引用する際は CardZ Marketcap と明記し、参照元ページにリンクし、基準日を併記してください。例：CardZ Marketcap、PSA10 時価総額、データ基準日 {asOf}。",
          "数値は出典明記のうえ自由に引用できます。データセットの一括再配布には書面での許可が必要です。詳細はデータページのライセンス欄をご覧ください。",
        ],
      },
    ],
  },
  data: {
    eyebrow: "データと API",
    h1: "CardZ Marketcap のデータと API：PSA10 時価総額を JSON で取得",
    answer:
      "CardZ Marketcap は PSA10 時価総額ランキングを、公開の読み取り専用 API から JSON で配信しています。各レスポンスにはスナップショットの generation、基準日、カードごとの価格・鑑定枚数・時価総額が含まれます。現在のスナップショットの基準日は {asOf} です。",
    endpoints: {
      heading: "エンドポイント",
      intro: "すべて GET、JSON を返し、キーは不要です。",
      table: {
        caption: "公開読み取り専用エンドポイント",
        head: ["エンドポイント", "返すもの"],
        rows: [
          ["/api/v1/market", "指定スコープのランキング 1 ページ分とスナップショットのメタデータ"],
          ["/api/v1/cards/<id>", "id 指定のカード 1 件。掲載外なら 404 とエラー本文"],
        ],
      },
    },
    params: {
      heading: "クエリパラメータ",
      intro: "不正な値は既定値に黙って戻さず、HTTP 400 を返します。",
      table: {
        caption: "/api/v1/market が受け付けるパラメータ",
        head: ["パラメータ", "受け付ける値", "備考"],
        rows: [
          ["scope", "all、pokemon、one-piece、watchlist", "それ以外は 400"],
          ["page", "正の整数", "数値でない場合や 0 は 400"],
          ["pageSize", "正の整数", "サーバ側の上限で丸められる"],
        ],
      },
    },
    fields: {
      heading: "レスポンスのフィールド",
      intro: "マーケットのレスポンスはカード一覧をスナップショットの識別情報で包んでいるため、保存したレスポンスは常に生成元のビルドまで辿れます。",
      table: {
        caption: "マーケットレスポンスのトップレベルフィールド",
        head: ["フィールド", "意味"],
        rows: [
          ["generation", "レスポンスの元になったスナップショットビルドの識別子"],
          ["generatedAt", "スナップショットが構築された日時"],
          ["effectiveAt", "数値が表す日付"],
          ["coverage", "掲げた対象範囲に対する充足度"],
          ["count", "このレスポンスに含まれるカード数"],
          ["cards", "順位付けされたカード。各々に価格・鑑定枚数・時価総額"],
        ],
      },
    },
    example: {
      heading: "リクエスト例",
      intro: "ポケモンスコープの 1 ページ目を取得する：",
      note: "レスポンスはエッジで短時間キャッシュされます。どの日付の数値かは、レスポンス時刻ではなく effectiveAt フィールドで判断してください。",
    },
    cadence: {
      heading: "更新頻度",
      body: [
        "新しいスナップショットは毎日公開され、API は現在のものを配信します。過去の generation はこれらのエンドポイントからは取得できません。",
        "レスポンスを保存する場合は generation と effectiveAt も一緒に保存してください。generation の異なる 2 つの数値は、日付なしでは比較できません。",
      ],
    },
    fairUse: {
      heading: "フェアユース",
      body: [
        "API は公開かつ認証不要です。リクエスト頻度は常識の範囲に保ってください。1 ページ読んでキャッシュし、データの更新より速くポーリングしないでください。スナップショットは 1 日 1 回しか動きません。",
        "過剰な自動アクセスはレート制限の対象になることがあります。一括取得が必要な場合は、スクレイピングではなくご相談ください。",
      ],
    },
    license: {
      heading: "出典表示とライセンス",
      body: [
        "数値は CC BY 4.0（https://creativecommons.org/licenses/by/4.0/）に基づき引用・再掲載できます。CardZ Marketcap を出典として明記し、引用したページまたはエンドポイントへリンクしてください。",
        "時価総額は毎日の更新で再計算されるため、出典表記にはその数値の基準日を必ず併記してください。",
      ],
      attributionLabel: "出典表記",
      attributionTemplate: "CardZ Marketcap、{site}、データ基準日 {asOf}",
    },
    files: {
      heading: "機械可読ファイル",
      intro: "クローラー、エージェント、言語モデル向け：",
      items: [
        { href: "/llms.txt", label: "/llms.txt", note: "サイトと主要ページの短い説明" },
        { href: "/llms-full.txt", label: "/llms-full.txt", note: "算出方法の要約を含む拡張版" },
        { href: "/sitemap.xml", label: "/sitemap.xml", note: "インデックス対象の全ページ" },
      ],
    },
  },
};

const ko: SiteCopy = {
  shell: {
    home: "CardZ Marketcap",
    asOfLabel: "데이터 기준일",
    onThisPage: "이 페이지의 목차",
    moreHeading: "CardZ Marketcap 더 보기",
    nav: {
      methodology: "산출 방법",
      about: "소개",
      faq: "자주 묻는 질문",
      glossary: "용어 사전",
      data: "데이터와 API",
    },
    indexLink: "실시간 랭킹",
    notCrypto: "CardZ Marketcap은 트레이딩 카드 가격 지수입니다. 암호화폐도, 토큰도, 상장 기업도 아닙니다. 티커가 존재하지 않으며 CARDS라는 이름의 암호자산과도 무관합니다.",
  },
  methodology: {
    eyebrow: "산출 방법",
    h1: "CardZ Marketcap이 트레이딩 카드 시가총액을 계산하는 방법 (PSA 10 가격 × 개체수)",
    answer:
      "CardZ Marketcap은 트레이딩 카드 시가총액을 「PSA 10 기준가 × 검증된 PSA 10 개체수」로 계산합니다. 한 장에 100달러이고 PSA 10이 10,000장이면 시가총액은 1,000,000달러입니다. 포켓몬 카드 시가총액과 원피스 카드 시가총액을 다루며, 데이터 기준일은 {asOf}입니다.",
    scope:
      "여기서 시가총액은 「최고 등급으로 현존하는 전체 물량의 총가치」이며, 카드 한 장의 거래가가 아닙니다. 주가에 발행주식수를 곱하는 것과 같은 개념이라서, 값이 싸도 등급 물량이 많은 카드가 비싸지만 감정 사례가 거의 없는 카드보다 위에 올 수 있습니다.",
    formula: {
      heading: "계산식",
      body: [
        "PSA 10 시가총액 ＝ PSA 10 기준가 × PSA 10 개체수.",
        "PSA 10 기준가는 동일한 인쇄판의 PSA 10 한 장에 대해 저희가 보유한 가격입니다. PSA 10 개체수는 PSA가 공개하는 개체수 리포트에서 해당 인쇄판이 10등급을 받은 장수입니다.",
        "곱셈의 양쪽은 반드시 같은 인쇄판에서 나와야 합니다. 어느 한쪽이 없거나 오래되면 해당 카드는 숫자 대신 상태를 표시합니다. 결측값을 0으로 대체하지 않으며, 다른 언어나 다른 인쇄의 가격을 가져오지도 않습니다.",
      ],
    },
    worked: {
      heading: "계산 예시",
      intro: "{asOf} 기준 랭킹 1위 카드를 예로 들면:",
      head: ["항목", "값"],
      rows: [
        { label: "카드", value: "{topName}" },
        { label: "PSA 10 기준가", value: "{topPrice}" },
        { label: "PSA 10 개체수", value: "{topPop}" },
        { label: "PSA 10 시가총액", value: "{topCap}" },
      ],
      note: "계산은 「가격 × 수량」이 전부입니다. 평활 계수도, 유통량 조정도, 편집상의 가중치도 얹지 않습니다.",
    },
    sources: {
      heading: "데이터 출처와 갱신 주기",
      body: [
        "개체수는 PSA가 공개하는 개체수 리포트에서 가져오며, 세트·카드 번호·언어·마감 사양으로 동일 인쇄판에 맞춰 대조합니다.",
        "가격은 같은 인쇄판의 등급 카드 거래 근거에서 매일 수집하고, 관측일을 명시해 보관합니다. 그 숫자가 얼마나 최신인지 항상 확인할 수 있게 하기 위해서입니다.",
        "공개 스냅샷은 매일 다시 생성됩니다. 카드마다 가격과 개체수의 관측일을 갖고 있으며, 모든 페이지에 지금 보고 있는 스냅샷의 기준일을 표시합니다.",
        "미국 달러 외의 금액은 스냅샷에 기록된 환율로 환산합니다. 통화를 바꿔도 순위는 달라지지 않습니다.",
      ],
    },
    eligibility: {
      heading: "포함되는 카드와 제외되는 카드",
      body: [
        "통계적으로 의미 있는 규모의 검증된 PSA 10 개체수와, 저희가 책임질 수 있는 가격이 모두 갖춰진 인쇄판만 랭킹에 들어갑니다. {asOf} 기준 등재 카드 중 가장 낮은 PSA 10 개체수는 {minPop}입니다.",
        "인쇄판마다 따로 순위를 매깁니다. 같은 일러스트라도 일본어판, 영어판, 중국어판은 개체수와 가격이 다르기 때문에 서로 다른 행입니다.",
        "가격이나 개체수를 검증할 수 없는 카드는 추정치로 채우지 않고 랭킹에서 제외합니다. 미개봉 상품은 싱글 카드와 분리해 추적하며 싱글 시가총액에 합산하지 않습니다.",
      ],
    },
    gradedVsRaw: {
      heading: "왜 로우 카드가 아니라 PSA 10인가",
      body: [
        "로우 카드에는 검증 가능한 공급량이 없습니다. 1999년 베이스 세트가 지금 몇 장 남아 있고 상태가 어떤지는 아무도 모릅니다. 즉 「로우 가격 × 인쇄량」은 숫자의 형태를 한 추측일 뿐입니다.",
        "PSA 10 개체수는 집계되어 공개된 숫자이고, 감정 접수가 늘수록 한 방향으로만 움직입니다. 같은 등급의 가격과 짝지으면 두 입력값이 모두 관측 가능하고 날짜까지 달린, 감사 가능한 시가총액이 됩니다.",
        "대가로 PSA 10 시가총액은 로우 카드와 낮은 등급이 담고 있는 가치를 무시합니다. 측정하는 것은 최고 등급 시장이지 취미 전체가 아닙니다.",
      ],
    },
    limits: {
      heading: "숨기지 않는 한계",
      body: [
        "개체수 리포트는 현실보다 늦습니다. 이번 주에 접수된 카드는 나중에 반영되므로, 빠르게 오르는 최신 카드는 과소 집계될 수 있습니다.",
        "거래가 얇은 카드는 가격 노이즈가 큽니다. 1년에 몇 번만 팔리는 인쇄판에서는 이례적인 거래 한 건이 실제 시장 변동보다 시가총액을 더 크게 움직입니다.",
        "케이스를 깨고 재접수하면 개체수가 부풀려집니다. 같은 실물 카드가 다시 감정되어 두 번 이상 집계될 수 있기 때문입니다.",
        "시가총액은 유동성이 아닙니다. 한 카드의 PSA 10 전량을 기준가로 팔아치울 수 있는 사람은 없습니다. 이 숫자는 규모를 나타내지 현금화 가능액을 나타내지 않습니다.",
      ],
    },
    compare: {
      heading: "가격만 보는 지수와 무엇이 다른가",
      intro:
        "대부분의 카드 시세 사이트가 공개하는 것은 「한 장의 가격」입니다. 등급 카드 시장 지수는 그 가격에 검증된 공급량을 곱하기 때문에 순위가 크게 달라집니다.",
      table: {
        caption: "「카드 지수」라고 불리는 것들과의 비교",
        head: ["대상", "측정하는 것", "공급량 검증"],
        rows: [
          ["CardZ Marketcap", "PSA 10 기준가 × 검증된 PSA 10 개체수", "있음 (PSA 개체수 리포트)"],
          ["가격만 보는 시세 사이트", "한 장의 최근 거래가 또는 평균가", "공급량 항목 자체가 없음"],
          ["CARDS라는 이름의 암호 토큰", "무관한 디지털 자산의 가격", "트레이딩 카드 상품이 아님"],
        ],
      },
      note: "30달러이고 PSA 10이 300,000장인 카드는 30,000달러이고 40장뿐인 카드보다 위에 옵니다. 가격만 보는 목록으로는 이것이 보이지 않으며, 이 지수가 존재하는 이유가 바로 그것입니다.",
    },
    versioning: {
      heading: "버전과 변경",
      body: [
        "공개되는 모든 스냅샷에는 generation 식별자와 기준일이 붙습니다. 지금 보고 계신 이 페이지는 generation {generation}, 기준일 {asOf}의 스냅샷으로 생성되었습니다.",
        "산출 방법 변경은 앞으로의 데이터부터 적용하며 여기에 명시합니다. 이미 공개된 이력을 조용히 고쳐 쓰지 않습니다. 정의가 바뀌면 소급해 채우는 대신 변경 내용을 설명합니다.",
      ],
    },
    cite: {
      heading: "이 페이지 인용",
      intro: "기사, 영상, 모델 답변에서 이 수치를 인용할 때는 산출 방법과 날짜를 함께 밝혀 주세요:",
      template: "CardZ Marketcap, 「CardZ Marketcap이 트레이딩 카드 시가총액을 계산하는 방법」, PSA 10 가격 × PSA 10 개체수, 데이터 기준일 {asOf}.",
      note: "수치는 매일 바뀝니다. 기준일을 함께 적어야 독자가 어느 스냅샷을 쓴 것인지 알 수 있습니다.",
    },
    faqHeading: "산출 방법 관련 질문",
    faq: [
      {
        q: "트레이딩 카드 시가총액이란 무엇인가요?",
        a: "트레이딩 카드 시가총액은 PSA 10 한 장의 가격에 현존하는 PSA 10 장수를 곱한 값입니다. 한 장의 거래가가 아니라 그 카드의 최고 등급 공급 전체의 가치를 나타냅니다.",
      },
      {
        q: "포켓몬 카드 시가총액은 어떻게 계산하나요?",
        a: "해당 포켓몬 인쇄판의 PSA 10 기준가에, PSA 개체수 리포트에서 검증한 같은 인쇄판의 PSA 10 장수를 곱합니다. 가격과 개체수는 매일 갱신되며 관측일이 함께 표시됩니다.",
      },
      {
        q: "왜 인쇄량이 아니라 PSA 10 개체수를 쓰나요?",
        a: "인쇄량은 거의 공개되지 않고 현존 장수도 알 수 없습니다. PSA 10 개체수는 집계되어 공개되므로 검증할 수 있습니다. 계산식 양쪽을 모두 관측 가능하게 유지할 수 있습니다.",
      },
      {
        q: "시가총액이 곧 그 카드의 가치인가요?",
        a: "아닙니다. 시가총액은 모든 PSA 10을 동시에 그 가격으로 평가한 이론값입니다. 전량을 같은 가격에 파는 것은 불가능하므로 현금화액이 아니라 규모와 공급의 지표로 보셔야 합니다.",
      },
      {
        q: "지수는 얼마나 자주 갱신되나요?",
        a: "스냅샷은 매일 다시 만들어집니다. 카드마다 가격과 개체수의 관측일이, 페이지마다 스냅샷 기준일이 표시되므로 숫자가 얼마나 최신인지 정확히 알 수 있습니다.",
      },
    ],
  },
  faq: {
    eyebrow: "자주 묻는 질문",
    h1: "포켓몬 카드 시가총액 FAQ: PSA 10 시가총액 질문 정리",
    answer:
      "CardZ Marketcap은 「PSA 10 기준가 × 검증된 PSA 10 개체수」로 포켓몬 카드 시가총액과 원피스 카드 시가총액을 랭킹합니다. 이 페이지는 수치를 만드는 방법, 다루는 범위, 그리고 알 수 없는 것을 설명합니다. 데이터 기준일은 {asOf}입니다.",
    groups: [
      {
        heading: "기본",
        items: [
          {
            q: "CardZ Marketcap은 무엇인가요?",
            a: "CardZ Marketcap은 등급 카드의 일간 시가총액 지수입니다. 검증된 PSA 10 개체수에 PSA 10 기준가를 곱해 포켓몬과 원피스 카드의 순위를 매기고, 모든 스냅샷의 기준일을 공개합니다.",
          },
          {
            q: "PSA 10 시가총액이란 무엇인가요?",
            a: "PSA 10 시가총액은 PSA 10 한 장의 가격에 PSA가 10등급으로 판정한 장수를 곱한 값입니다. 한 장의 가격이 아니라 최고 등급 공급에 담긴 가치의 총량을 나타냅니다.",
          },
          {
            q: "CardZ는 암호화폐나 토큰인가요?",
            a: "아닙니다. CardZ Marketcap은 트레이딩 카드 가격 지수이며 토큰도 코인도 티커도 없습니다. 이름이 비슷한 암호자산과 무관하고, 이 사이트에서 사고팔 수 있는 것은 아무것도 없습니다.",
          },
          {
            q: "CardZ Marketcap은 무료인가요?",
            a: "무료입니다. 랭킹, 카드 페이지, 산출 방법, 용어 사전은 자유롭게 볼 수 있습니다. 수치도 출처를 밝히고 인용한 페이지로 링크하면 자유롭게 인용할 수 있습니다.",
          },
        ],
      },
      {
        heading: "수치를 만드는 방법",
        items: [
          {
            q: "트레이딩 카드 시가총액은 어떻게 계산하나요?",
            a: "시가총액은 같은 인쇄판의 PSA 10 기준가에 검증된 PSA 10 개체수를 곱한 값입니다. 두 입력값 모두 관측일을 갖고 있으며, 어느 한쪽이라도 검증되지 않으면 그 카드는 랭킹에 들어가지 않습니다.",
          },
          {
            q: "개체수 데이터는 어디서 오나요?",
            a: "PSA가 공개하는 개체수 리포트에서 가져오며, 정확한 세트·카드 번호·언어·마감 사양에 맞춰 대조합니다. PSA 10 장수만 사용하고 낮은 등급은 시가총액에 넣지 않습니다.",
          },
          {
            q: "수치는 얼마나 자주 갱신되나요?",
            a: "공개 스냅샷은 매일 다시 만들어집니다. 카드마다 가격과 개체수를 언제 관측했는지 표시하므로, 오래 움직이지 않은 수치는 조용히 갱신되는 대신 오래된 것으로 드러납니다.",
          },
          {
            q: "어떤 카드는 왜 숫자가 없나요?",
            a: "그 날짜에 해당 인쇄판의 값을 검증할 수 없었기 때문입니다. CardZ Marketcap은 0으로 대체하거나 추정치로 채우지 않고 비워 둡니다. 채우면 랭킹 전체가 조용히 망가지기 때문입니다.",
          },
          {
            q: "수치의 통화는 무엇인가요?",
            a: "수치는 미국 달러로 계산하고, 표시할 때 스냅샷에 저장된 환율로 환산합니다. 통화 전환은 표시만 바꾸며 그 아래의 순위는 바뀌지 않습니다.",
          },
        ],
      },
      {
        heading: "다루는 범위",
        items: [
          {
            q: "어떤 카드를 다루나요?",
            a: "검증된 PSA 10 개체수가 있고 가격에 책임질 수 있는 포켓몬과 원피스 싱글 카드를 다룹니다. {asOf} 기준 {ranked}개 인쇄판을 랭킹하며, 포켓몬 {pokemon}개, 원피스 {onePiece}개입니다.",
          },
          {
            q: "일본어판과 영어판은 따로 순위를 매기나요?",
            a: "네. 개체수와 가격이 각각 다르므로 인쇄판마다 별도의 행입니다. 같은 일러스트의 다른 언어판은 랭킹에서 다른 카드이며, 둘을 평균 내는 일은 없습니다.",
          },
          {
            q: "미개봉 박스도 다루나요?",
            a: "미개봉 상품은 싱글 카드와 분리해 추적합니다. PSA 10 개체수가 없으므로 싱글 시가총액 랭킹에 들어갈 수 없고, 그 합계에 섞이지도 않습니다.",
          },
          {
            q: "추적 중인 총 시가총액은 얼마인가요?",
            a: "{asOf} 기준으로 공개 지수는 {ranked}개 인쇄판에 걸쳐 총 {totalCap}의 PSA 10 시가총액을 추적합니다. 이 합계는 가격 변동과 새로 감정된 장수에 따라 매일 달라집니다.",
          },
        ],
      },
      {
        heading: "랭킹 읽는 법",
        items: [
          {
            q: "시가총액 기준으로 가장 가치 있는 포켓몬 카드는 무엇인가요?",
            a: "랭킹은 실시간으로 공개되며 매일 재정렬됩니다. 시가총액 상위권은 높은 PSA 10 가격과 큰 개체수를 함께 갖추므로, 「가장 비싼 한 장」 목록과는 대개 일치하지 않습니다.",
          },
          {
            q: "왜 싼 카드가 비싼 카드보다 위에 있나요?",
            a: "시가총액은 가격에 공급량을 곱하기 때문입니다. 30달러에 PSA 10이 300,000장인 카드는 한 장 값이 훨씬 싸도, 30,000달러에 40장인 카드보다 총가치가 큽니다.",
          },
          {
            q: "기사나 영상에서 이 수치를 써도 되나요?",
            a: "됩니다. 출처를 CardZ Marketcap으로 밝히고 인용한 페이지로 링크해 주세요. 시가총액은 매일 바뀌므로 기준일도 함께 적어 주세요.",
          },
        ],
      },
    ],
  },
  glossary: {
    eyebrow: "용어 사전",
    h1: "등급 카드 시장 지수 용어 사전: PSA 10 시가총액 용어",
    answer:
      "이 용어 사전은 CardZ Marketcap이 등급 카드를 랭킹할 때 쓰는 용어를 정의합니다. PSA 10 시가총액, 개체수 리포트, 젬 비율, 검증된 공급량 등이 포함됩니다. 정의는 {asOf}에 공개된 지수에 적용됩니다.",
    setName: "CardZ Marketcap 등급 카드 용어 사전",
    setDescription: "CardZ Marketcap 등급 카드 시장 지수에서 사용하는 감정·가격·시가총액 용어의 정의.",
    terms: [
      { id: "market-cap", term: "시가총액 (트레이딩 카드)", def: "어떤 인쇄판의 PSA 10 기준가에 검증된 PSA 10 개체수를 곱한 값. 한 장의 가격이 아니라 최고 등급 공급의 총가치를 나타낸다." },
      { id: "psa-10-market-cap", term: "PSA 10 시가총액", def: "PSA 10 입력값만으로 계산한 시가총액. PSA 10 한 장의 가격과 PSA 10 장수를 쓰며 낮은 등급은 제외한다." },
      { id: "psa", term: "PSA", def: "Professional Sports Authenticator. 제3자 감정 회사로, 접수된 카드에 등급을 부여하고 등급별 개체수를 공개한다." },
      { id: "psa-10", term: "PSA 10", def: "PSA의 최고 표준 등급으로 Gem Mint라고 부른다. CardZ Marketcap은 가격과 공급량 양쪽 모두 이 등급을 사용한다." },
      { id: "population-report", term: "개체수 리포트", def: "PSA가 공개하는 등급별 감정 장수 집계. 시가총액의 공급 항목이 나오는 출처다." },
      { id: "pop", term: "팝 (POP)", def: "population의 줄임말. Pop 10은 특정 세트·언어·마감 사양의 인쇄판이 PSA 10을 받은 장수를 뜻한다." },
      { id: "reference-price", term: "기준가", def: "특정 날짜에 어떤 인쇄판의 PSA 10 한 장에 대해 CardZ Marketcap이 보유한 가격. 같은 인쇄판의 등급 카드 거래 근거에서 도출한다." },
      { id: "printing", term: "인쇄판", def: "세트, 카드 번호, 언어, 마감 사양으로 특정되는 개별 발매 형태. 같은 일러스트라도 인쇄판이 다르면 별도의 행으로 순위를 매긴다." },
      { id: "finish", term: "마감 사양", def: "홀로, 리버스 홀로, 논홀로 등 카드 표면 처리. 희소성과 가격에 모두 영향을 주므로 인쇄판 정체성의 일부다." },
      { id: "parallel", term: "패러렐", def: "다른 처리나 넘버링으로 인쇄된 대체 버전. 자체 개체수와 가격을 가지며 일반판과 시가총액 행을 공유하지 않는다." },
      { id: "promo", term: "프로모", def: "일반 부스터 외 경로로 배포되는 카드. 이벤트나 콜라보가 대표적이며 개체수가 적고 가격 변동이 큰 편이다." },
      { id: "set-code", term: "세트 코드", def: "카드가 속한 세트의 짧은 식별자. 카드 번호와 함께 쓰여 가격과 개체수가 어느 인쇄판의 것인지 특정한다." },
      { id: "collector-number", term: "카드 번호", def: "세트 내 위치를 나타내는, 카드에 인쇄된 번호(예: 085/SVP). 인쇄판과 개체수 데이터를 맞추는 근거 중 하나다." },
      { id: "grading", term: "감정 (그레이딩)", def: "센터링, 모서리, 가장자리, 표면을 제3자가 평가하고 등급을 부여해 밀봉하기까지의 과정." },
      { id: "gem-rate", term: "젬 비율", def: "접수된 카드 중 PSA 10을 받는 비율. 낮을수록 접수가 많아도 PSA 10 공급은 느리게 늘어난다." },
      { id: "raw", term: "로우 카드", def: "감정을 받지 않은 카드. 검증 가능한 공급량 집계가 없어 시가총액 계산에 쓰지 않는다." },
      { id: "ungraded-reference", term: "로우 카드 참고가", def: "로우 카드의 참고용 가격. 대조용으로만 표시하며 시가총액 계산에는 절대 들어가지 않는다." },
      { id: "slab", term: "슬랩", def: "등급 카드를 밀봉하는 플라스틱 케이스. 등급과 인증 번호가 표기되어 있다." },
      { id: "cert-number", term: "인증 번호", def: "등급 카드 한 장마다 부여되는 고유 번호. 인쇄판이 아니라 개별 실물 슬랩을 식별한다." },
      { id: "crack-and-resubmit", term: "크랙 후 재접수", def: "슬랩에서 카드를 꺼내 더 높은 등급을 노려 다시 접수하는 것. 같은 실물 카드가 개체수에 중복 계산될 수 있다." },
      { id: "supply", term: "검증된 공급량", def: "감정 회사가 집계한 공급 부분. CardZ Marketcap은 추정 인쇄량이 아니라 이 값을 사용한다." },
      { id: "print-run", term: "인쇄량", def: "최초로 생산된 장수. 트레이딩 카드에서는 거의 공개되지 않으며 이 지수에서는 사용하지 않는다." },
      { id: "index", term: "등급 카드 시장 지수", def: "감정 데이터로 계산한 가치 지표로 카드 순위를 매긴 것. CardZ Marketcap은 PSA 10 가격과 개체수에 기반한 등급 카드 시장 지수다." },
      { id: "snapshot", term: "스냅샷", def: "지수의 한 번의 공개 빌드. generation과 기준일로 식별된다. 이 사이트의 모든 수치는 어느 한 스냅샷에 속한다." },
      { id: "effective-date", term: "기준일", def: "스냅샷의 수치가 나타내는 날짜. 모든 페이지에 표시되어 인용한 수치를 특정 날짜에 연결할 수 있다." },
      { id: "observation-date", term: "관측일", def: "개별 가격이나 개체수가 관측된 날짜. 출처가 움직이지 않았다면 기준일보다 앞설 수 있다." },
      { id: "accumulating", term: "수집 중", def: "값을 공개할 만큼의 근거가 아직 없을 때 표시하는 상태. 수집 중이라는 뜻이지 값이 0이라는 뜻이 아니다." },
      { id: "stale", term: "오래된 데이터", def: "값은 있지만 관측일이 오래되었을 때 표시하는 상태. 숫자를 숨기지 않고 날짜와 함께 표시한다." },
      { id: "coverage", term: "커버리지", def: "표방한 랭킹 범위에 대해 스냅샷이 얼마나 채워졌는지. 스냅샷마다 공개해 결손이 드러나게 한다." },
      { id: "watchlist", term: "관찰 목록", def: "상위 100위 밖에서 같은 방법으로 추적하는 인쇄판. 입력값이 완전히 동일하며 같은 합계에 포함된다." },
      { id: "one-piece-card", term: "원피스 카드 게임", def: "만화 『원피스』를 소재로 한 트레이딩 카드 게임. 등급 인쇄판은 포켓몬과 같은 시가총액 방법으로 함께 순위를 매긴다." },
    ],
  },
  about: {
    eyebrow: "소개",
    h1: "CardZ Marketcap 소개: 등급 카드 시장 지수",
    answer:
      "CardZ Marketcap은 등급 카드의 일간 시가총액 지수로, 검증된 PSA 10 개체수에 PSA 10 기준가를 곱해 포켓몬과 원피스 카드의 순위를 매깁니다. 공개 중인 스냅샷의 기준일은 {asOf}입니다.",
    descriptor:
      "이 지수가 존재하는 이유는, 이 취미에 가격 기록은 넘쳐나도 공급량을 반영한 랭킹이 없었기 때문입니다. 어떤 카드가 2,000달러에 팔렸다는 사실만으로는 그 등급이 200장 남았는지 200,000장 남았는지 알 수 없습니다.",
    sections: [
      {
        heading: "무엇을 공개하나",
        body: [
          "등급 포켓몬·원피스 인쇄판을 PSA 10 시가총액으로 정렬한 일간 랭킹. 각 행에 가격, 개체수, 시가총액을 표시합니다.",
          "등재된 인쇄판마다 개별 페이지를 두어 입력값과 그 변화를 보여 줍니다.",
          "문서로 정리한 산출 방법, 용어 사전, 그리고 기계 판독 가능한 API. 같은 수치를 신뢰가 아니라 검증으로 확인할 수 있게 합니다.",
        ],
      },
      {
        heading: "편집 원칙",
        body: [
          "공개하는 모든 숫자는 날짜가 붙은 스냅샷에서 계산합니다. 순위를 수동으로 조정하지 않으며, 돈을 내고 랭킹에 오를 수 있는 카드도 없습니다.",
          "결측값은 결측 상태로 둡니다. 가격이나 개체수를 검증할 수 없으면 숫자 대신 상태를 표시하며, 0으로 대체하거나 다른 인쇄판의 가격을 빌려 오지 않습니다.",
          "한계는 산출 방법과 나란히 공개하고 구석에 묻지 않습니다. 산출 방법 페이지에는 보고 지연, 재접수로 인한 중복 계산 등 개체수 기반 지수의 알려진 약점을 적어 두었습니다.",
          "정정은 다음 일간 스냅샷에서 반영하고 내용을 설명합니다. 조용히 소급해 채우지 않습니다.",
        ],
      },
      {
        heading: "갱신 주기",
        body: [
          "지수는 매일 다시 생성됩니다. 스냅샷마다 generation 식별자와 기준일이, 수치마다 관측일이 붙습니다.",
          "모든 페이지에 지금 보고 있는 스냅샷의 기준일을 표시하므로, 이 사이트에서 인용한 수치는 언제나 특정 날짜에 연결할 수 있습니다.",
        ],
      },
      {
        heading: "독립성",
        body: [
          "CardZ Marketcap은 카드를 판매하지 않고, 경매를 운영하지 않으며, 랭킹 노출의 대가를 받지 않습니다.",
          "감정 출처로 PSA를 밝히는 것은 그 개체수 리포트가 공개되어 확인 가능하기 때문입니다. 가격 데이터 공급처의 신원은 공개하지 않습니다.",
        ],
      },
      {
        heading: "인용 방법",
        body: [
          "수치를 인용할 때는 CardZ Marketcap을 밝히고, 인용한 페이지로 링크하고, 기준일을 함께 적어 주세요. 예: CardZ Marketcap, PSA 10 시가총액, 데이터 기준일 {asOf}.",
          "수치는 출처를 밝히면 자유롭게 인용할 수 있습니다. 데이터셋 대량 재배포에는 서면 허가가 필요합니다. 데이터 페이지의 라이선스 안내를 참고하세요.",
        ],
      },
    ],
  },
  data: {
    eyebrow: "데이터와 API",
    h1: "CardZ Marketcap 데이터와 API: PSA 10 시가총액을 JSON으로",
    answer:
      "CardZ Marketcap은 PSA 10 시가총액 랭킹을 공개 읽기 전용 API로 JSON 형태로 제공합니다. 모든 응답에는 스냅샷 generation, 기준일, 카드별 가격·개체수·시가총액이 담깁니다. 현재 스냅샷 기준일은 {asOf}입니다.",
    endpoints: {
      heading: "엔드포인트",
      intro: "모두 GET이며 JSON을 반환하고, 키가 필요 없습니다.",
      table: {
        caption: "공개 읽기 전용 엔드포인트",
        head: ["엔드포인트", "반환 내용"],
        rows: [
          ["/api/v1/market", "지정한 스코프의 랭킹 한 페이지와 스냅샷 메타데이터"],
          ["/api/v1/cards/<id>", "id로 카드 한 장. 등재되지 않았으면 404와 오류 본문"],
        ],
      },
    },
    params: {
      heading: "쿼리 파라미터",
      intro: "잘못된 값은 기본값으로 조용히 되돌리지 않고 HTTP 400을 반환합니다.",
      table: {
        caption: "/api/v1/market이 받는 파라미터",
        head: ["파라미터", "허용 값", "비고"],
        rows: [
          ["scope", "all, pokemon, one-piece, watchlist", "그 외의 값은 400"],
          ["page", "양의 정수", "숫자가 아니거나 0이면 400"],
          ["pageSize", "양의 정수", "서버 상한으로 제한됨"],
        ],
      },
    },
    fields: {
      heading: "응답 필드",
      intro: "마켓 응답은 카드 목록을 스냅샷 정체성과 함께 감싸므로, 저장해 둔 응답도 언제나 그것을 만든 빌드까지 되짚을 수 있습니다.",
      table: {
        caption: "마켓 응답의 최상위 필드",
        head: ["필드", "의미"],
        rows: [
          ["generation", "응답이 나온 스냅샷 빌드의 식별자"],
          ["generatedAt", "스냅샷이 생성된 시각"],
          ["effectiveAt", "수치가 나타내는 날짜"],
          ["coverage", "표방한 범위 대비 스냅샷의 충족도"],
          ["count", "이 응답에 담긴 카드 수"],
          ["cards", "순위가 매겨진 카드. 각각 가격·개체수·시가총액 포함"],
        ],
      },
    },
    example: {
      heading: "요청 예시",
      intro: "포켓몬 스코프의 첫 페이지를 가져오기:",
      note: "응답은 엣지에서 짧게 캐시됩니다. 어느 날짜의 수치인지는 응답 시각이 아니라 effectiveAt 필드로 판단하세요.",
    },
    cadence: {
      heading: "갱신 주기",
      body: [
        "새 스냅샷은 매일 공개되며 API는 현재 스냅샷을 제공합니다. 과거 generation은 이 엔드포인트로 제공되지 않습니다.",
        "응답을 저장한다면 generation과 effectiveAt도 함께 저장하세요. generation이 다른 두 수치는 날짜 없이는 비교할 수 없습니다.",
      ],
    },
    fairUse: {
      heading: "공정 이용",
      body: [
        "API는 공개이고 인증이 없으므로 요청 빈도를 상식적인 수준으로 유지해 주세요. 한 페이지를 읽고 캐시하며, 데이터가 바뀌는 속도보다 빠르게 폴링하지 마세요. 스냅샷은 하루에 한 번만 움직입니다.",
        "과도한 자동 트래픽은 속도 제한 대상이 될 수 있습니다. 대량 접근이 필요하면 스크래핑 대신 문의해 주세요.",
      ],
    },
    license: {
      heading: "출처 표시와 라이선스",
      body: [
        "수치는 CC BY 4.0(https://creativecommons.org/licenses/by/4.0/)에 따라 인용하고 다시 게재할 수 있습니다. CardZ Marketcap을 출처로 밝히고 인용한 페이지 또는 엔드포인트로 링크해 주세요.",
        "시가총액은 매일 갱신될 때마다 다시 계산되므로, 출처 표기에는 해당 수치의 기준일을 반드시 함께 적어야 합니다.",
      ],
      attributionLabel: "출처 표기",
      attributionTemplate: "CardZ Marketcap, {site}, 데이터 기준일 {asOf}",
    },
    files: {
      heading: "기계 판독 가능 파일",
      intro: "크롤러, 에이전트, 언어 모델용:",
      items: [
        { href: "/llms.txt", label: "/llms.txt", note: "사이트와 주요 페이지의 짧은 설명" },
        { href: "/llms-full.txt", label: "/llms-full.txt", note: "산출 방법 요약을 포함한 확장판" },
        { href: "/sitemap.xml", label: "/sitemap.xml", note: "색인 대상 전체 페이지" },
      ],
    },
  },
};

export const siteCopy: Record<Locale, SiteCopy> = {
  en,
  "zh-TW": zhTW,
  "zh-CN": zhCN,
  ja,
  ko,
};
