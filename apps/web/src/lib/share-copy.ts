/*
 * 分享圖入面嘅字，逐個語言一份。
 *
 * owner 2026-08-20：「X.com 我哋有 CARDZGame 中文簡體，所以你中文都要出多一次。」
 * 即係同一張卡要出兩次圖：英文一張貼英文帳號、簡體一張貼中文帳號。
 *
 * ⚠️ **加語言之前先睇字體。** `api/og/card/[id]` 本來寫住「一個字都唔准跟介面語言」，
 * 唔係品味問題 —— 佢淨係載住 Inter（latin only），餵 CJK 落去 satori 出嘅係空位，
 * 而且**唔會報錯、唔會 log、照出 200**。所以呢度每加一個語言，就要喺
 * `SHARE_LANG_FONTS` 度講明佢靠邊隻字體，而嗰隻字體要真係喺 `public/fonts/og/`。
 * 下面個 guard 對唔上就炸喺 import 嗰刻 = `next build` 即刻紅。
 *
 * 網站 UI 嗰份 copy（`site-copy.ts`）**唔喺呢度 import**：嗰份係成個網站幾千行、
 * 逐頁逐段嘅文案，而張圖得十零個標籤。夾硬共用會將成份 SiteCopy 拖入 OG bundle，
 * 而且兩邊改嘅節奏唔同（網站文案日日執，圖入面啲標籤半年冇郁過）。用詞照跟返
 * site-copy 嗰套（市值 / PSA 10 参考价 / PSA 10 评级数量 / 数据截至），唔准另撚一套。
 */
export const SHARE_LANGS = ["en", "zh-CN"] as const;
export type ShareLang = (typeof SHARE_LANGS)[number];

/*
 * 邊個語言要邊隻字體檔（相對 `public/fonts/og/`）。
 *
 * `null` = 淨係靠 Inter 就夠（latin）。有值 = 除咗 Inter 仲要疊埋呢隻，satori 會逐個
 * glyph 揀邊隻畫得到。Noto Sans SC 兩隻加埋 16MB，所以**淨係要嗰陣先載**（見 route
 * 個 `loadOgFonts(lang)`）—— 英文 request 唔應該為咗一個用唔著嘅字體食多 16MB memory。
 *
 * 來源：notofonts/noto-cjk release Sans2.004 嘅 `18_NotoSansSC.zip`，2026-08-20 取
 *   NotoSansSC-Regular.otf  w400  8,331,336 bytes  sha256 faa6c9df652116dde789d351359f3d7e5d2285a2b2a1f04a2d7244df706d5ea9
 *   NotoSansSC-Bold.otf     w700  8,543,168 bytes  sha256 c6cb5a93abaa9edc8ee7463b7ebb7f42d618d40e6ed2f7a5371c97b0b64767c0
 *   授權 SIL OFL 1.1，全文喺同一個資料夾嘅 OFL.txt（binary 派發必須同行）。
 *   **冇 subset**：subset 錯一個字就係一格空位，而空位唔會報錯（滿足唔到就唔好慳嗰 15MB）。
 *   satori 唔食 woff2 亦唔食 variable font —— Google Fonts CSS API 派 Noto Sans SC
 *   淨係得 woff2（切成 ~100 個 unicode-range subset），所以一定要行呢個 static OTF release。
 *   ⚠️ 呢兩行 sha256 由 `scripts/test-fe-font-contract.mjs` ⑥ 對返落磁碟。
 */
export const SHARE_LANG_FONTS: Record<ShareLang, readonly [file: string, weight: 400 | 700][] | null> = {
  en: null,
  "zh-CN": [["NotoSansSC-Regular.otf", 400], ["NotoSansSC-Bold.otf", 700]],
};

/** satori 用嘅 family 串。CJK 排喺 Inter 後面：數字／$ / % 行 Inter，中文字先跌落 Noto。 */
export const SHARE_FONT_FAMILY: Record<ShareLang, string> = {
  en: "Inter",
  "zh-CN": "Inter, Noto Sans SC",
};

const MONTHS_EN = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];

export interface ShareCopy {
  lang: ShareLang;
  /** 攞卡名／set 名嗰陣用邊個 locale key（`LocalizedText` 個 key）。 */
  nameLocale: "en" | "zh-CN";
  fontFamily: string;
  marketCap: string;
  /** wide／純文字版嗰個 hero label：`PSA 10 MARKET CAP`（post 個四格用短嘅 `marketCap`） */
  psa10MarketCap: string;
  psa10Price: string;
  psa10Pop: string;
  /** `180D CHANGE` / `180天变化` */
  change: (window: string) => string;
  /** 走勢圖 caption 左邊：`PSA 10 PRICE · 180D` */
  priceWindow: (window: string) => string;
  /** 底部右邊：`AS OF AUG 18, 2026` / `数据截至 2026年8月18日` */
  asOf: (date: string) => string;
  /** wide 版排名副題：`OF 1,604 RANKED POKÉMON` */
  ranked: (total: string | null, tcg: string) => string;
  /** `Pokémon` → `POKÉMON` / `宝可梦` */
  tcg: (raw: string) => string;
  /** 印刷語言徽章：`JP PRINT` / `日文版` */
  printLanguage: (language: string) => string | null;
  /** `AUG 18, 2026` / `2026年8月18日`（UTC） */
  shortDate: (date: Date) => string;
  /** `FEB 2026` / `2026年2月`（UTC） */
  monthYear: (date: Date) => string;
  /** 走勢圖日期範圍：`FEB 2026 — AUG 2026` */
  range: (from: string, to: string) => string;
  /** 資料字（set 名、卡名以外嗰啲）要唔要 uppercase。中文冇大細楷，一律 identity。 */
  upper: (text: string) => string;
}

const PRINT_LANGUAGE: Record<ShareLang, Record<string, string>> = {
  en: { ja: "JP PRINT", ko: "KR PRINT", zhCN: "CN PRINT", zhTW: "TW PRINT" },
  "zh-CN": { ja: "日文版", ko: "韩文版", zhCN: "简体中文版", zhTW: "繁体中文版" },
};

/* `card.tcg` 係自由字串（DB 出），所以認 substring 唔認 enum —— 認唔到就照出原字，
   唔准 fallback 去某一個 TCG（出錯 TCG 名比出英文名衰十倍）。 */
function tcgZhCN(raw: string): string {
  const key = raw.toLowerCase();
  if (key.includes("pok")) return "宝可梦";
  if (key.includes("one piece") || key.includes("optcg")) return "海贼王";
  return raw;
}

const EN: ShareCopy = {
  lang: "en",
  nameLocale: "en",
  fontFamily: SHARE_FONT_FAMILY.en,
  marketCap: "MARKET CAP",
  psa10MarketCap: "PSA 10 MARKET CAP",
  psa10Price: "PSA 10 PRICE",
  psa10Pop: "PSA 10 POP",
  change: (window) => `${window} CHANGE`,
  priceWindow: (window) => `PSA 10 PRICE · ${window}`,
  asOf: (date) => `AS OF ${date}`,
  ranked: (total, tcg) => (total ? `OF ${total} RANKED ${tcg}` : `RANKED ${tcg}`),
  tcg: (raw) => raw.toUpperCase(),
  printLanguage: (language) => PRINT_LANGUAGE.en[language] ?? null,
  shortDate: (date) => `${MONTHS_EN[date.getUTCMonth()]} ${date.getUTCDate()}, ${date.getUTCFullYear()}`,
  monthYear: (date) => `${MONTHS_EN[date.getUTCMonth()]} ${date.getUTCFullYear()}`,
  range: (from, to) => `${from} — ${to}`,
  upper: (text) => text.toUpperCase(),
};

const ZH_CN: ShareCopy = {
  lang: "zh-CN",
  nameLocale: "zh-CN",
  fontFamily: SHARE_FONT_FAMILY["zh-CN"],
  marketCap: "市值",
  psa10MarketCap: "PSA 10 市值",
  psa10Price: "PSA 10 参考价",
  psa10Pop: "PSA 10 评级数量",
  /* `180D` → `180天`：四個數同一行，label 位有限，所以窗口字要短。 */
  change: (window) => `${window.replace(/D$/i, "天")}变化`,
  priceWindow: (window) => `PSA 10 参考价 · ${window.replace(/D$/i, "天")}`,
  asOf: (date) => `数据截至 ${date}`,
  ranked: (total, tcg) => (total ? `${tcg}排名 · 共 ${total} 张` : `${tcg}排名`),
  tcg: tcgZhCN,
  printLanguage: (language) => PRINT_LANGUAGE["zh-CN"][language] ?? null,
  shortDate: (date) => `${date.getUTCFullYear()}年${date.getUTCMonth() + 1}月${date.getUTCDate()}日`,
  monthYear: (date) => `${date.getUTCFullYear()}年${date.getUTCMonth() + 1}月`,
  range: (from, to) => `${from} — ${to}`,
  /* 中文冇大細楷，`toUpperCase()` 對中文係 no-op，但對混住嘅 set 名（`ex 超級電擊`）
     會將 latin 嗰截扯成全大楷，同旁邊啲中文對唔上重心。所以直接唔 upper。 */
  upper: (text) => text,
};

const COPY: Record<ShareLang, ShareCopy> = { en: EN, "zh-CN": ZH_CN };

/*
 * ⚠️ 呢個 guard 唔係擺設：加一個語言落 `SHARE_LANGS` 而唔喺 `SHARE_LANG_FONTS` /
 * `SHARE_FONT_FAMILY` / `COPY` 補齊，TypeScript 會嗌（`Record<ShareLang, …>` 缺 key）。
 * 但**加咗 key 但指去一隻冇 ship 嘅字體檔**就唔會 —— 而嗰個 case 正正就係「圖出咗街
 * 但入面係一格格空白」。呢度炸喺 import 嗰刻 = `next build` 即刻紅。
 * 檔案存唔存在由 `scripts/test-fe-og-lang.mjs` 落磁碟驗（呢度唔准掂 fs：呢個 module
 * 亦會俾 test 喺 Node 度直接 import，加 fs 就變咗要 mock）。
 */
{
  const missing = SHARE_LANGS.filter((lang) => !COPY[lang] || !SHARE_FONT_FAMILY[lang]);
  if (missing.length > 0) {
    throw new Error(`share-copy: 語言冇齊 copy／font family（${missing.join("、")}）`);
  }
  const unfonted = SHARE_LANGS.filter((lang) => lang !== "en" && (SHARE_LANG_FONTS[lang]?.length ?? 0) === 0);
  if (unfonted.length > 0) {
    throw new Error(`share-copy: ${unfonted.join("、")} 冇指定 CJK 字體 —— satori 會出空格，唔會報錯`);
  }
}

/*
 * key 一律細楷（`readShareLang` 會 lowercase）。條 HERMES 鏈同各家 API 寫 locale
 * 嘅方式唔一（`zh-CN` / `zh_CN` / `zhcn` / `cn`），所以認寬啲 —— 但**只認得兩個
 * 語言**：多打一個 `zh-TW` 落嚟會跌返 en 出英文圖，唔會出一堆空格。
 */
const LANG_ALIASES: Record<string, ShareLang> = {
  en: "en",
  "zh-cn": "zh-CN",
  zhcn: "zh-CN",
  cn: "zh-CN",
  zh: "zh-CN",
  "zh-hans": "zh-CN",
};

/*
 * 認唔到就跌返 `en`，唔准 500。實際行咗邊個由 response 個 `x-og-lang` 講返。
 *
 * 收 `zh-CN` 亦收 `zh-cn` / `zh_CN`（條 HERMES 鏈同各家 API 對 locale 大細楷嘅寫法唔一）。
 */
export function readShareLang(value: string | null | undefined): ShareLang {
  if (!value) return "en";
  return LANG_ALIASES[value.trim().toLowerCase().replace("_", "-")] ?? "en";
}

export function shareCopy(lang: ShareLang): ShareCopy {
  return COPY[lang];
}
