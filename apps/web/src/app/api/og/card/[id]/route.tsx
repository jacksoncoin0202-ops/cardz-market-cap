import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { ImageResponse } from "next/og";
import { shortSubject } from "@/lib/related-cards";
import { buildShareChart, type ShareChart } from "@/lib/share-chart";
import { loadNodeMarketAsset, loadMarketSnapshot } from "@/lib/server-snapshot";
import { readShareFormat, type ShareFormat } from "@/lib/share-destinations";
import { defaultMarketWindow, marketWindowDays, type MarketCardView, type MarketWindow } from "@/lib/types";

export const size = { width: 1200, height: 630 };
export const contentType = "image/jpeg";

/*
 * 兩款分享圖，一條 route（fe06(share-card)，2026-08-19）：
 *   `?format=wide`（預設，1200×630）  —— 社交 unfurl。`twitter:card=summary_large_image`
 *      同 `og:image` 指住嘅就係佢，尺寸由 `lib/route-metadata.ts` 宣告，唔准亂改。
 *   `?format=post`（1080×1350，4:5）—— 真係一張圖噉貼出去嗰張：存落相簿再貼
 *      Threads／X／IG feed、send 落 LINE／WhatsApp 對話。owner 2026-08-19：
 *      「手機一打開就見到係全屏幕，噉嘅樣先至似樣」。
 *
 * `?format=` 亦收**目的地名**（`whatsapp` / `x` / `threads` / `line` …）。噉樣叫方
 * 寫佢送去邊，唔使記尺寸 —— 張表同埋點解今日三個貼圖目的地全部係 4:5，見
 * `lib/share-destinations.ts`。
 *
 * ⚠️ **4:5 唔准改返 9:16。** 呢個 format 第一版出 1080×1920，owner 實測貼上 Threads / X：
 * 三家對直度圖都有高度上限，超過就**唔裁、改為按高度縮細**，於是隔離人哋啲相滿版、
 * 我哋嗰張永遠得七八成闊。IG feed 4:5 最嚴，鎖到闊÷高 ≥ 0.8 三家都唔會再縮。
 * 同一條線寫死喺熱力圖分享圖（`lib/share-image.ts` `SHARE_MIN_ASPECT`）—— 兩張分享圖
 * 同一個理由、同一個數，`scripts/test-fe-brand-logo-svg.mjs` 兩邊一齊驗。
 * 順帶：高度收窄之後卡圖唔使再放大到 1.267×，而家係 0.933×（**縮細**），
 * 三個 format 入面卡面最銳就係呢張。
 *
 * **點解唔開多條 route**：兩款圖嘅資料來源、卡圖解碼、字體、wordmark、fail-open 行為
 * 完全一樣，開兩條就係同一條問題兩份 copy（AGENTS.md 規矩 13）。分別淨係 layout。
 *
 * `?theme=light|dark`：兩個 format 而家都預設 dark。
 *
 * ⚠️ wide 由 light 揭做 dark 係 owner 2026-08-19 嘅決定，**推翻**咗呢度本來寫嘅
 * 「unfurl 卡片多數坐喺淺色 feed 所以用 light」。理由：坐喺淺色 feed 正正就係要**唔似**
 * 隔籬啲白卡先撳得落手；而且 post 本身係 dark，兩張圖終於得返一個視覺身份，人哋撳入
 * 網站（網站亦係 dark skin 做主）唔會覺得三個地方三個樣。
 * 呢個係一行 flip、一行 revert —— 想睇返 light 就改返呢粒字，或者拉 `?theme=light`。
 */
type ShareTheme = "light" | "dark";

const FORMATS: Record<ShareFormat, { width: number; height: number; defaultTheme: ShareTheme }> = {
  wide: { width: 1200, height: 630, defaultTheme: "dark" },
  post: { width: 1080, height: 1350, defaultTheme: "dark" },
};

/*
 * 兩個 format 都出 JPEG 唔出 PNG（wide 2026-08-19、post 2026-08-20，兩次都係實測驅動）。
 *
 * wide：量到出街嗰三張 PNG 係 594KB / 623KB / 648KB —— WhatsApp 文件寫明 og:image
 * 上限 600KB，即係**當時已經有卡爆咗閘**，而失敗係靜默嘅（人哋條 link 出唔到圖，
 * 我哋呢邊乜 log 都冇）。同一張圖 JPEG q90 4:4:4 得 ~200KB（31% of PNG），
 * 文字邊冇肉眼分別（4:4:4 唔做色度抽樣，就係為咗保住細字同橙色 accent）。
 *
 * post 本來寫住「唔轉：嗰張係人手 save 落相簿再上傳，冇 byte 閘，質素行先」。
 * 個**前提冧咗**：owner 2026-08-20 講明有條 cron 鏈會 download 呢張圖再 upload 去
 * WhatsApp／X／Threads。即係話呢張圖而家一日行幾轉機器嘅 download + upload，而收貨
 * 嗰三家**全部都會自己再壓一次做 JPEG** —— 我哋條 PNG 邊（654,912 bytes 實測）
 * 一個 pixel 都保唔到落最終讀者度，淨係令條鏈慢同食頻寬。所以照轉，質素唔會蝕。
 * post 用 q92（高過 wide）：佢係俾人揿大睇嗰張，而佢冇 600KB 閘要夾。
 *
 * 卡圖坐喺不透明地台上，冇透明角要保，所以轉 JPEG 冇美學代價 —— 同一個理由亦寫喺
 * `components/heatmap.tsx` 個 `SHARE_JPEG_QUALITY`。
 * ⚠️ `og:image:type` 喺 `card/[id]/page.tsx` 明寫 `image/jpeg`，同呢度一定要一致 ——
 *    `scripts/test-fe-og-unfurl.mjs` 會攞真 bytes 對返個宣告。
 */
const JPEG_QUALITY: Record<ShareFormat, number> = { wide: 90, post: 92 };

/*
 * satori 冇 CSS var，所以要寫死 hex。每一粒都係 globals.css 嗰份 token 嘅字面值
 * （`:root` = light、`[data-theme="dark"]` = dark），改 token 記住改埋呢度 —— 呢個
 * 檔同 `lib/share-image.ts` 係全 repo 僅有兩處要手抄 token 嘅地方。
 *
 * `artGround` 直接抄 `--detail-art-bg`：卡頁個卡圖就係坐喺呢塊漸變上面，分享圖用返
 * 同一塊，人哋撳入去先唔會覺得「同頭先張圖唔同個網站」。以前 wide 版寫死一格
 * `#1a1a1a` 純色 —— 個底冇錯（深色襯得返透明角），但同網站冇關係。
 */
const THEMES: Record<ShareTheme, {
  paper: string;
  surface: string;
  ink: string;
  muted: string;
  line: string;
  accent: string;
  positive: string;
  negative: string;
  artGround: string;
  /** 卡圖背後嗰團暖光（accent 溶入地台）—— 網頁靠 hover spotlight 出，張圖冇得 hover，畫死一團。 */
  artGlow: string;
  salesBar: string;
  /** rank 藥丸 = 網頁 `.detail-rank`（`--detail-rank-bg` / `--detail-rank-text`） */
  rankBg: string;
  rankInk: string;
  /*
   * 變動藥丸個底色。satori 冇 CSS var 亦計唔到 alpha 疊底，所以要**預先撈埋**寫成
   * 實色 hex：positive/negative 前景色以 12% 溶入 paper 嗰個結果。
   * 藥丸唔用純 accent 底 —— 88px hero 隔籬要一舊唔搶戲但認得出正負嘅色塊。
   */
  positiveSoft: string;
  negativeSoft: string;
}> = {
  light: {
    paper: "#f7f7f5", /* --paper */
    surface: "#ffffff", /* --surface */
    ink: "#171717", /* --ink */
    muted: "#6f6f6b", /* --muted */
    line: "#e6e6e2", /* --line */
    accent: "#b85416", /* --accent */
    positive: "#23775b", /* --positive */
    negative: "#a63b52", /* --negative */
    artGround: "linear-gradient(155deg, #e8f0f1, #f5f4f1 55%, #eeeaf4)", /* --detail-art-bg */
    artGlow: "radial-gradient(closest-side, rgba(184, 84, 22, 0.16), rgba(184, 84, 22, 0))",
    salesBar: "#a8c5ca", /* .sales-bar */
    rankBg: "rgba(23, 23, 23, 0.8)", /* --detail-rank-bg */
    rankInk: "#ffffff", /* --detail-rank-text */
    positiveSoft: "#e4efeb", /* #23775b @12% on #f7f7f5 */
    negativeSoft: "#f4e4e8", /* #a63b52 @12% on #f7f7f5 */
  },
  dark: {
    paper: "#101010",
    surface: "#1a1a1a",
    ink: "#ececec",
    muted: "#9b9b96",
    line: "#2c2c2a",
    accent: "#e8823f",
    positive: "#4cb893",
    negative: "#d4748c",
    artGround: "linear-gradient(155deg, #1c262a, #1c1c19 55%, #241f2e)",
    artGlow: "radial-gradient(closest-side, rgba(232, 130, 63, 0.22), rgba(232, 130, 63, 0))",
    salesBar: "#7a9aa2",
    rankBg: "rgba(236, 236, 236, 0.85)",
    rankInk: "#101010",
    positiveSoft: "#1a2b26", /* #4cb893 @12% on #101010 */
    negativeSoft: "#26181c", /* #d4748c @12% on #101010 */
  },
};

/*
 * wordmark 兩個 skin ↔ 兩個檔。**唔准合併做 template string** —— `scripts/test-fe-brand-logo-svg.mjs`
 * 要驗「邊個 skin 配邊個檔」（覆核 2026-08-18 種過 fault：OG 改指 `-dark` skin，白字畫落
 * 淺底 = 隱形，舊版淨驗 `.svg` 後綴照綠）。寫成一張明表，gate 先至捉得到掉轉。
 */
const LOGO_BY_THEME: Record<ShareTheme, string> = {
  light: "brand/logo-cardz-marketcap.svg",
  dark: "brand/logo-cardz-marketcap-dark.svg",
};

/*
 * 卡圖鐵路由 468 收到 430（2026-08-19）：慳返嘅 38px 全部畀右欄，令 88px hero 數字
 * 同兩行卡名有位企。
 *
 * 誰知鐵路收窄咗反而可以將卡面**放大**：舊版 350×490 喺 630 高嘅板裡面上下各剩
 * 70px 空地，owner 講「好核突」嘅其中一塊就係呢等空位。改 396×554 之後上下各剩
 * 38px、左右各 17px，塊板就係卡。
 * ☠️ 上限永遠唔准超 429×600（卡圖 asset 母版）：554/600 = **0.923×**，仍然係縮細。
 *    超過 1.0× = 放大嗌高清，即係 owner 講嘅「蒙查查」。
 */
const ART_PANEL_WIDTH = 430;
const ART_MAX_WIDTH = 396;
const ART_MAX_HEIGHT = 554;
/*
 * post 卡圖舞台（968 闊 = 1080 − 56×2）同卡圖上限。
 *
 * ⚠️ **卡圖母版得 429×600**：2026-08-19 抽驗 `data/public/market-assets` 60 個全尺寸
 * asset，52 個係 429×600，其餘係 box 類（1000×730 / 1600×1417 / 517×550）。即係卡面
 * 本身冇更高清嘅來源 —— 想「唔蒙查查」，唯一做法係**唔好放太大**，唔係放大咗嗌高清。
 * 560 高 = 0.933×，即係**縮細**：呢張係三個 format 入面唯一冇放大過卡面嗰張，
 * 亦即係最銳嗰張（9:16 嗰版要 760 高 = 1.267× 放大，改 4:5 順帶執返呢樣）。
 * 文字／走勢圖／wordmark 全部係 vector，喺 1080 闊度直接畫，同卡圖冇關係。 */
const POST_ART_STAGE_HEIGHT = 620;
const POST_ART_MAX_WIDTH = 452;
const POST_ART_MAX_HEIGHT = 560;

/*
 * post 嘅字級表（fe07(post-type)，2026-08-19）。
 *
 * ⚠️ **PostLayout 唔准再喺 JSX 度撒 fontSize 數字** —— 一律行呢三級。owner 2026-08-19 睇住
 * 出街嗰張講：「入面啲字大大細細、字體不一，感覺好奇怪」。翻查係兩類毛病，兩類都唔係
 * 「有層次」，係**手滑**：
 *
 *   1) 四個數同一行，字級係 46 / 36 / 36 / 36 —— MARKET CAP 大成 1.28 倍。網頁自己嗰四個
 *      KPI 由 `--kpi-fs` 一個變數出，四格永遠同級（globals.css `.detail-grid-rail`），
 *      即係分享圖同網站講緊兩套唔同嘅嘢。同一行、同一個 label 級、同一條分隔線之下，
 *      唔同字級讀落唔似「重要啲」，似排錯版。
 *   2) 三個「大寫 tracked 細標籤」角色（stat label 22 / 走勢 caption 24 / 頁腳 22）加埋
 *      accent kicker 24 —— 22 同 24 差 9%，肉眼分唔出係有意定係唔小心，但排埋一齊就係
 *      「大大細細」嗰種感覺。同一個角色只准有一個數。
 *
 * 所以而家得三級（title 除外，佢係跟卡名長度嘅 ramp，見 postTitleSize）：
 *   micro 22 —— 所有大寫 tracked 細標籤：stat label（Stat 內置 22）、走勢 caption、
 *               日期軸、頁腳兩邊、accent kicker。
 *   meta  26 —— set 名、rank 藥丸。
 *   stat  44 —— 四個數，**一律同級**。
 *
 * 44 唔係執個中位數，係度返出嚟：四個數最闊嗰行（label `PSA 10 PRICE` 185px +
 * value `▲ +41.6%` 4.83em）喺 968px 可用闊食 ~854px，仲有 114px 鬆動；再大到 46 就只剩
 * 97px，而 market cap 一過 $1000M（`$1234.56M`）就會食突。垂直方面 stat 行由 113.6 跌到
 * 111.2px，即係比舊版**寬鬆咗**，PostLayout 個垂直預算註唔使改。
 */
const POST_TYPE = { micro: 22, meta: 26, stat: 44 } as const;

/* 走勢圖釘死 180 日 —— 同網頁 `defaultMarketWindow`（types.ts）同一個窗。
   張圖出咗街係俾第三者睇，唔可以帶當前用戶揀嘅時段；但撳入去見到嘅預設係 180D，
   所以圖同頁面第一眼一定要係同一段。 */
const SHARE_WINDOW: MarketWindow = defaultMarketWindow;
const SHARE_WINDOW_DAYS = marketWindowDays[SHARE_WINDOW];
const SHARE_WINDOW_LABEL = SHARE_WINDOW.toUpperCase();

/*
 * 卡圖點解要 sharp 解碼一次：卡圖係 content-addressed `<sha256>.webp`，而
 * `next/og` 底下嘅 resvg 解唔到 WebP（實測：同一段 markup 餵 PNG 畫得出、
 * 餵 WebP 出 0 px，靜靜地留白）。所以請求時用 sharp WebP → PNG data URI 再餵
 * 落 <img>，透明角照樣保住。
 *
 * `sharp` 已經係 @cardz/web 嘅明確 dependency（唔再靠 next 嘅 transitive
 * optional dep），standalone tracing 亦喺 next.config.ts
 * `outputFileTracingIncludes` 明寫咗 `sharp` + `@img` —— nft 自己對 sharp 有
 * special case，但嗰個係推論，而漏咗嘅表現係靜靜退返純文字版，所以照寫。
 * 就算真係漏咗，下面 try/catch 會退返純文字版，唔會出 500；響應帶
 * `x-og-art: 0`，deploy 之後 `curl -sI` 一句就驗到卡圖路徑係咪真係生勾勾。
 */
/*
 * OG 字體（fe05(og-share)，2026-08-17）：唔餵 `fonts` 嘅話 satori 用返 next/og 自己
 * bundle 嗰隻（`node_modules/next/dist/compiled/@vercel/og/Geist-Regular.ttf`）——
 * 即係網頁行 Inter、分享圖行 Geist，字形／字寬／weight ramp 三樣都對唔上。
 *
 * satori **唔食 woff2 亦唔食 variable font**（要 static TTF/OTF/WOFF），所以唔可以直接
 * 用 `src/fonts/InterVariable-latin.woff2` 嗰份，要另外放三隻 static instance。
 *   來源：Google Fonts CSS API v2（legacy UA 會回 truetype），Inter v20，2026-08-17 取
 *     Inter-Regular.ttf   w400  324,820 bytes  sha256 1b08e7fc267a5c7e1d614100f604b83e7e8a0be241f0f288faa2b3ac93a683ba
 *     Inter-SemiBold.ttf  w600  326,048 bytes  sha256 e7a1aaf7eda9f2fad4131725fa556265ec75ca7b2d756260173a040363e8d4f7
 *     Inter-Bold.ttf      w700  326,468 bytes  sha256 b37284b5701b6b168dfc770aa1a4ac492106422fd3ba76bc7641e37434e8019c
 *   授權 SIL OFL 1.1，全文喺同一個資料夾嘅 OFL.txt（binary 派發必須同行）。
 *   只 register 400 / 600 / 700 三個數 —— layout 唔准用其他 weight，satori 唔會合成。
 *
 * ⚠️ 只有 latin：所以**張圖入面一個字都唔准跟介面語言**（owner 2026-08-17 對 heatmap
 * 分享圖落嘅同一條規矩 —— 一張圖出咗街係俾全世界睇）。卡名一律 `officialName`、
 * set 名一律 `setName.en`、其餘標籤全部係下面嘅英文常數。餵 CJK 落嚟只會出豆腐字。
 *
 * 兩路 `existsSync` 同 logo 嗰段一樣：dev 由 repo root 行，standalone build `process.cwd()`
 * 已經係 `apps/web`。載入失敗**唔准**炸 —— 退返 `undefined`（即係 satori 用返 bundled font），
 * 同卡圖一樣 fail-open：OG 端點死咗等於社交分享冇圖，比字形唔啱仲差。
 */
let ogFontsPromise: Promise<{ name: string; data: Buffer; weight: 400 | 600 | 700; style: "normal" }[] | undefined> | null = null;
function loadOgFonts() {
  ogFontsPromise ??= (async () => {
    const { existsSync } = await import("node:fs");
    const files: [string, 400 | 600 | 700][] = [["Inter-Regular.ttf", 400], ["Inter-SemiBold.ttf", 600], ["Inter-Bold.ttf", 700]];
    try {
      const loaded = await Promise.all(files.map(async ([file, weight]) => {
        const path = [resolve(process.cwd(), "public/fonts/og", file), resolve(process.cwd(), "apps/web/public/fonts/og", file)].find((p) => existsSync(p));
        if (!path) throw new Error(`missing ${file}`);
        return { name: "Inter", data: await readFile(path), weight, style: "normal" as const };
      }));
      return loaded;
    } catch (error) {
      console.warn(`[og/card] OG font load failed, satori 退返 bundled font: ${error instanceof Error ? error.message : "unknown"}`);
      return undefined;
    }
  })();
  return ogFontsPromise;
}

let artFailureLogged = false;
function noteArtFailure(id: string, error: unknown): void {
  /* 只嗌一次：OG 係爬蟲面，壞一張通常等於壞成批，逐張 log 會浸死 log。 */
  if (artFailureLogged) return;
  artFailureLogged = true;
  const reason = error instanceof Error ? error.message : "unknown";
  console.warn(`[og/card] card art unavailable, falling back to text-only layout (id=${id}): ${reason}`);
}

interface CardArt {
  src: string;
  width: number;
  height: number;
}

/*
 * `fromMaster`（2026-08-19 由舊名 `allowUpscale` 改過嚟 —— 4:5 之後 post 已經**冇再放大**，
 * 個名再叫 upscale 就係呃下一個睇呢段 code 嘅人）：
 *   wide（卡圖畫 384×522 = 0.64×）—— 唔開，`_600` 派生檔縮落去就得，解碼快。
 *   post（畫到 560 高 = 0.933×）—— 要開。呢個縮放太貼近 1×，行 `_600` 嘅話等於攞一份
 *     已經再壓過一次 WebP 嘅圖幾乎原大咁貼上去，壓縮 artifact 一粒都磨唔走；攞全尺寸
 *     母版由 600 lanczos3 落 560 先真係乾淨。
 * 補嗰記**輕**銳化係補返 lanczos 縮放本身嗰浸軟。唔可以下重手：卡圖係去咗底嘅 RGBA，
 * 邊緣一過銳就沿住 alpha 邊出白光暈，喺深色底特別現眼。
 * `withoutEnlargement: !fromMaster` 留返做保險：行派生檔嗰路永遠唔准放大。
 */
async function loadCardArt(
  card: MarketCardView,
  maxWidth: number,
  maxHeight: number,
  fromMaster = false,
): Promise<CardArt | null> {
  if (card.image.kind !== "raw_front") return null;
  /* 放大嗰陣一定要攞全尺寸母版，唔可以攞 `_600` 派生檔再放大 —— 兩者同高（600），
     但派生檔已經再壓過一次 WebP，放大會連壓縮 artifact 一齊放大。
     縮細嗰路照用 `_600`：細檔解碼快，結果一模一樣。 */
  const source = fromMaster ? card.image.url : card.image.variants?.["600"] ?? card.image.url;
  const asset = source.split("/").pop();
  if (!asset) return null;

  const node = await loadNodeMarketAsset(asset);
  if (!node) return null;

  const { default: sharp } = await import("sharp");
  let pipeline = sharp(Buffer.from(node.body)).resize({
    width: maxWidth,
    height: maxHeight,
    fit: "inside",
    withoutEnlargement: !fromMaster,
    kernel: "lanczos3",
  });
  if (fromMaster) pipeline = pipeline.sharpen({ sigma: 0.6, m1: 0.4, m2: 0.8 });
  const { data, info } = await pipeline.png({ compressionLevel: 9 }).toBuffer({ resolveWithObject: true });

  return {
    src: `data:image/png;base64,${data.toString("base64")}`,
    width: info.width,
    height: info.height,
  };
}

function usd(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return "—";
  if (value >= 1_000_000) return `$${(value / 1_000_000).toFixed(2)}M`;
  if (value >= 1_000) return `$${(value / 1_000).toFixed(1)}K`;
  return `$${Math.round(value)}`;
}

function integer(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return "—";
  return new Intl.NumberFormat("en-US").format(Math.round(value));
}

/*
 * 窗口變動。`changePct.value` 已經係百分點（`lib/format.ts` `formatPercent` 直接
 * `.toFixed(2)` 加 `%`，冇乘 100），所以呢度唔可以再乘。
 * `status` 唔係 ready/stale（累積中／未有數）就當冇數 —— 圖上面唔准出「0.00%」扮平穩。
 */
function changeText(card: MarketCardView): { text: string; tone: "positive" | "negative" | "neutral" } | null {
  const metric = card.windows?.[SHARE_WINDOW]?.changePct;
  if (!metric || metric.value === null || !Number.isFinite(metric.value)) return null;
  if (metric.status !== "ready" && metric.status !== "stale") return null;
  const value = metric.value;
  const tone = value > 0 ? "positive" : value < 0 ? "negative" : "neutral";
  const arrow = value > 0 ? "▲" : value < 0 ? "▼" : "•";
  return { text: `${arrow} ${value > 0 ? "+" : ""}${value.toFixed(1)}%`, tone };
}

/* 圖入面嘅日期一律英文短月（見上面字體註：只有 latin face）。UTC 讀，唔跟 server 時區。 */
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"] as const;
function shortDate(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const time = Date.parse(iso);
  if (!Number.isFinite(time)) return null;
  const date = new Date(time);
  return `${MONTHS[date.getUTCMonth()]} ${date.getUTCDate()}, ${date.getUTCFullYear()}`;
}
function monthYear(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const time = Date.parse(iso);
  if (!Number.isFinite(time)) return null;
  const date = new Date(time);
  return `${MONTHS[date.getUTCMonth()]} ${date.getUTCFullYear()}`;
}

/* post 成幅 968px 可用闊（比 wide 個 620 闊 56%），所以同一長度食得起大一級。
   38 呢級係下限：`clampTitle` 封咗 96 字，38px 喺 968px 闊度兩行剛好裝得晒
   （3 行 × 1.1 = 125px 會逼爆下面個垂直預算，見 PostLayout 個預算註）。 */
function postTitleSize(name: string): number {
  if (name.length <= 34) return 56;
  if (name.length <= 60) return 46;
  return 38;
}

function clampTitle(name: string): string {
  return name.length <= 96 ? name : `${name.slice(0, 95).trimEnd()}…`;
}

/* 純文字版（fail-open 路徑）成幅 1056px 可用闊，所以同一長度食得起大一級。
   原本呢度寫死 78px：93 字嘅卡名食四行，`MARKET CAP` 俾底邊斬一半、wordmark
   直情出咗界。退化版一樣要見得人，所以照跟長度落級。 */
function textOnlyTitleSize(name: string): number {
  if (name.length <= 34) return 78;
  if (name.length <= 60) return 58;
  return 46;
}

/*
 * wide（1200×630）：左卡圖右數據。fe06 起三個數擺同一行（本來 1+2 兩行），慳返嗰行位擺走勢圖。
 *
 * 三個數**同一個字級**，同 post 一樣（見 POST_TYPE 個註：同一行、同一個 label 級、同一條線
 * 之下，字級唔同讀落唔似分主次，似排錯版）。舊版係 44/34/34。
 * 36 係量返出嚟嘅（榜首 Van Gogh Pikachu = 全榜最闊嗰行 `$141.95M` / `$2.9K` / `49,808`，
 * 620px 右欄）：44/34/34 嗰陣 label 行食 598px（剩 22），36 之後跌到 561px（剩 59）——
 * 三格闊度由 max(label, value) 定，value 收窄咗成行都跟住收。
 *
 * `WIDE_TYPE` 由三級變**四級**（2026-08-19）。呢個係特登破咗「三級，唔准撒數字」嗰條
 * 規矩，理由係量度出嚟嘅，唔係手癢：
 *
 *   WhatsApp 嘅 unfurl 縮圖闊約 330pt，即係 1200px 源縮到 **0.275×**。舊版最大嗰個
 *   數字係 36px → 落到氣泡入面 **9.9pt**，細過 WhatsApp 自己嘅正文。即係話 owner
 *   而家傳出去嘅每一張 link preview，個市值根本冇人讀得到 —— 換文案、換比例、換
 *   theme 一樣救唔到，因為冇一個字級係為 330pt 縮圖而設。
 *   88px × 0.275 = 24.2pt，大過 WhatsApp 自己嘅粗體標題；Discord（~400px fit box，
 *   0.333×）出 29pt。
 *
 *   micro 18 = 細字（走勢 caption、AS OF、rank 尾巴、佐證 label）
 *   meta  22 = kicker / hero label / set 行
 *   stat  30 = 佐證行兩個數（PSA 10 價、POP）—— **特登細過 hero**：rank 係鈎、
 *              cap 係 payload、呢行係收據，一眼要讀得出邊個係主角。
 *   hero  88 = 市值，全圖唯一一個「縮到氣泡入面都仲讀到」嘅數。
 *
 * ⚠️ rank 用 40px 唔用 hero 級：產品叫 Market Cap，hero 一定要係 cap。兩個 100px 級
 *    數字打對台等於冇 hero。40 vs 88 差 2.2×，層級一眼讀得出。
 */
const WIDE_TYPE = { micro: 18, meta: 22, stat: 30, hero: 88 } as const;
const WIDE_RANK_SIZE = 40;

/*
 * 卡名喺 wide 右欄（678px）嘅字級 ramp。
 *
 * ⚠️ 呢度餵入嚟嘅係 `shortSubject(card, "en", WIDE_NAME_MAX)`，唔係預設嗰個 44 字 ——
 * 44 字 @34px 一定食兩行，而 2026-08-19 逐 px 掃出嚟嘅結果係：兩行標題 + 88px hero
 * 一齊擺，右欄 ink 去到 y=624，衝穿底 padding 35px（張圖照 200 出街，冇 error）。
 * 封 36 字之後每級都入到一行：36×0.5em@34px ≈ 612 < 678。
 */
const WIDE_NAME_MAX = 50;
function wideTitleSize(name: string): number {
  if (name.length <= 20) return 46;
  if (name.length <= 30) return 40;
  /* 50 字 @32px 喺 678 闊度 = 2 行（每行 ~42 字），唔會有第三行。 */
  return 32;
}

/*
 * wide 個 kicker（`SET · #NUM · LANG PRINT`）要入一行。
 *
 * 實測：18px / 600 / letterSpacing 2.2 全大寫，678px 欄寬最多裝 ~53 字（≈ 12.6px/字）。
 * 舊版寫死 `clampSetName(name)` 默認 46，於是 Mario Pikachu 嘅
 * `POKEMON JAPANESE XY PROMO · #294/XY-P · JAPANESE PRINT`（54 字）摺二行，
 * 推矮下面整組 22px。所以 set 名嘅預算係**剩余**，唔係定數。
 */
const WIDE_KICKER_MAX = 53;

/* set 名最長 74 字（實測全 1604 張榜卡）。右欄 24px 得一行位，兩行就會頂到
   wordmark 距底邊剩 10px（量過：panel ink bottom margin 41 → 10）。
   wide 個 kicker 一行要塞晒 set + 編號 + 語言，所以嗰邊傳細啲嘅 max。 */
function clampSetName(name: string, max = 46): string {
  return name.length <= max ? name : `${name.slice(0, max - 1).trimEnd()}…`;
}

/*
 * 印刷語言喺圖入面嘅英文寫法。**唔准**行 `localizedCardLanguage()` ——
 * OG 圖只載到 Inter（latin-only），餵「日文版」入去會出一行 tofu 方格。
 * 呢張表就係「圖入面唔准出 CJK」呢條規矩嘅執行點。
 */
const PRINT_LANGUAGE_EN: Record<string, string> = {
  ja: "JP PRINT",
  ko: "KR PRINT",
  zhCN: "CN PRINT",
  zhTW: "TW PRINT",
};
function printLanguageEn(language: string | null): string | null {
  if (!language || language === "en") return null;
  return PRINT_LANGUAGE_EN[language] ?? null;
}

type Palette = (typeof THEMES)[ShareTheme];

function Stat({ label, value, valueSize = 52, labelSize = 22, palette, tone }: {
  label: string;
  value: string;
  valueSize?: number;
  /** wide 個佐證行 label 收到 micro 18：嗰行係收據，唔可以同 hero label 22 一樣重。 */
  labelSize?: number;
  palette: Palette;
  tone?: "positive" | "negative" | "neutral";
}) {
  const color = tone === "positive" ? palette.positive : tone === "negative" ? palette.negative : palette.ink;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {/* 對齊網頁 §1.3.2 角色：label = --w-label 600（唔係 400），data = --w-data 600（唔係 700）。
          Inter 600 比 bundled font 700 幼但字身闊少少，tracking 由 1.6 → 1.8 補返個呼吸位。 */}
      <span style={{ fontSize: labelSize, color: palette.muted, letterSpacing: 1.8, fontWeight: 600 }}>{label}</span>
      <span style={{ fontSize: valueSize, color, fontWeight: 600 }}>{value}</span>
    </div>
  );
}

/*
 * 走勢圖上面嗰行字：左邊永遠講「畫緊咩、幾長」，右邊按 format 講一樣嘢 ——
 *   wide：講變動（嗰邊得三個數，冇 `180D CHANGE` 嗰格）。
 *   post：講**邊 180 日**（變動已經喺四個數嗰行出咗）。
 * SVG 入面冇字（見 lib/share-chart.ts 檔頭），所有座標軸文字都喺呢度用 satori 畫。
 */
function ChartCaption({ palette, change, range, fontSize }: {
  palette: Palette;
  change: ReturnType<typeof changeText>;
  range?: string | null;
  fontSize: number;
}) {
  const tone = change?.tone === "positive" ? palette.positive : change?.tone === "negative" ? palette.negative : palette.muted;
  return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", width: "100%" }}>
      <span style={{ fontSize, color: palette.muted, letterSpacing: 1.8, fontWeight: 600 }}>
        PSA 10 PRICE · {SHARE_WINDOW_LABEL}
      </span>
      {change ? <span style={{ fontSize, color: tone, fontWeight: 600 }}>{change.text}</span> : null}
      {!change && range ? <span style={{ fontSize, color: palette.muted, letterSpacing: 1.8, fontWeight: 600 }}>{range}</span> : null}
    </div>
  );
}

/*
 * 「邊 180 日」= 頭尾兩個月份，砌成一句 `FEB 2026 — AUG 2026`。
 *
 * ⚠️ 本來呢兩個月份係走勢圖**下面**自己一行（`ChartAxis`，左右對齊住條線兩端）。2026-08-19
 * 量過出街嗰張：3 行卡名嗰啲（96 字，例如 Van Gogh Pikachu）ink 去到 y=1313，即係**衝穿咗**
 * 底 padding（1350−56=1294）20px；同時尾段變成四個 22px muted label 砌成一個 2×2
 * （Feb/Aug 一行、網址/AS OF 一行，中間得 5px），owner 睇落就係「大大細細、字體不一」嗰種亂。
 * 收埋落 caption 右邊一次過解兩樣：慳返 26.4(行) + 8(gap) = 34.4px（3 行嗰個 case 由
 * −20 變 +14 鬆動），尾段亦由「2×2 四舊嘢」變返兩行清楚角色（幾時嘅數 / 邊個出）。
 * 絕對日期照樣留喺圖入面 —— 張圖出咗街冇得撳入去問，呢個係當初加 axis 嘅理由，冇丟。
 */
function chartRange(chart: ShareChart): string | null {
  const from = monthYear(chart.firstAt);
  const to = monthYear(chart.lastAt);
  if (!from || !to) return null;
  return `${from} — ${to}`.toUpperCase();
}

/*
 * 攞唔到卡圖（asset 唔喺度／sharp 炸／解碼失敗）就退返呢版。
 *
 * ⚠️ 呢個 layout **兩個 format 共用**，所以幾何一定要跟住 format 行 ——
 * 舊版寫死一套（padding 64/72、logo 280×121、字級 24/30/52），喺 1080×1350 鬆到浪費，
 * 喺 1200×630 就係啱啱好；而家市值升咗做 88px hero，同一套數落 630 高度**一定爆**
 * （手算 bottom group 已經 424px，成幅得 502px 淨）。所以兩套幾何明寫落表。
 *
 * 卡圖炸咗唔係「將就」嘅理由：縮圖睇唔到數字呢個病同卡圖有冇載到完全無關
 * （52px × 0.275 = 14.3pt 一樣讀唔到）。冇咗最搶眼嗰嚿，個數就更加唔可以再細。
 */
const TEXT_ONLY_GEO = {
  wide: { padding: "40px 48px", gapTop: 8, gapBottom: 14, kicker: WIDE_TYPE.micro, set: WIDE_TYPE.micro, stat: WIDE_TYPE.stat, logoW: 144, logoH: 62, rule: 14, cols: 60 },
  post: { padding: "56px 72px", gapTop: 20, gapBottom: 26, kicker: POST_TYPE.micro, set: POST_TYPE.meta, stat: POST_TYPE.stat, logoW: 280, logoH: 121, rule: 30, cols: 72 },
} as const;
function TextOnlyLayout({ card, logoSrc, palette, format }: { card: MarketCardView; logoSrc: string; palette: Palette; format: ShareFormat }) {
  const geo = format === "post" ? TEXT_ONLY_GEO.post : TEXT_ONLY_GEO.wide;
  /* wide 只得 630 高，卡名唔可以食三行 —— 同 WideLayout 行同一個封頂同同一條 ramp。 */
  const name = format === "post"
    ? clampTitle(card.officialName ?? card.setName.en)
    : shortSubject(card, "en", WIDE_NAME_MAX) || clampTitle(card.officialName ?? card.setName.en);
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        width: "100%",
        height: "100%",
        background: palette.paper,
        padding: geo.padding,
        justifyContent: "space-between",
      }}
    >
      <div style={{ display: "flex", flexDirection: "column", gap: geo.gapTop }}>
        {/* kicker = --w-label 600 + --track-kicker 級數；Inter 600 大寫比舊 bundled font 400 闊，
            4 → 3.4 先返返舊闊度（最長 kicker「POKEMON · #SV4A-205」唔可以谷長咗撞落標題）。 */}
        <span style={{ fontSize: geo.kicker, color: palette.accent, letterSpacing: 3.4, fontWeight: 600 }}>
          {card.tcg.toUpperCase()} · #{card.collectorNumber}
        </span>
        <span
          style={{
            fontSize: format === "post" ? textOnlyTitleSize(name) : wideTitleSize(name),
            color: palette.ink,
            fontWeight: 700,
            lineHeight: 1.1,
          }}
        >
          {name}
        </span>
        {/* set 名明寫 400：唔好靠 satori 嘅默認 —— 我哋只 register 400/600/700，唔明寫就靠彩數 */}
        <span style={{ fontSize: geo.set, color: palette.muted, lineHeight: 1.3, fontWeight: 400 }}>
          {clampSetName(card.setName.en, geo.cols)}
        </span>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: geo.gapBottom }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 6, borderTop: `2px solid ${palette.line}`, paddingTop: geo.rule }}>
          <span style={{ fontSize: WIDE_TYPE.meta, color: palette.muted, letterSpacing: 1.8, fontWeight: 600 }}>PSA 10 MARKET CAP</span>
          <span style={{ fontSize: WIDE_TYPE.hero, color: palette.ink, fontWeight: 700, lineHeight: 1 }}>{usd(card.marketCap.value)}</span>
        </div>
        <div style={{ display: "flex", gap: geo.cols }}>
          <Stat palette={palette} label="PSA 10 PRICE" value={usd(card.pricePsa10.value)} valueSize={geo.stat} labelSize={WIDE_TYPE.micro} />
          <Stat palette={palette} label="PSA 10 POP" value={integer(card.populationPsa10.value)} valueSize={geo.stat} labelSize={WIDE_TYPE.micro} />
        </div>
        <img src={logoSrc} alt="CardZ Marketcap" width={geo.logoW} height={geo.logoH} />
      </div>
    </div>
  );
}

/*
 * 卡圖舞台：漸變地台（= 網頁 `--detail-art-bg`）+ 一團 accent 暖光 + 卡圖。
 * 三層要疊，所以外層 relative、光同圖各自 absolute —— satori 支援 position:absolute，
 * 但**每個 absolute 子元素都要自己寫 left/top/width/height**（冇 `inset` shorthand）。
 */
function ArtStage({ art, alt, width, height, radius, palette }: {
  art: CardArt;
  alt: string;
  width: number;
  height: number;
  radius: number;
  palette: Palette;
}) {
  /* 光團開到卡圖 1.6 倍闊，置中喺卡圖後面：太細會變一個圓餅，太大就攤平咗睇唔出。 */
  const glow = Math.round(Math.max(art.width, art.height) * 1.6);
  return (
    <div
      style={{
        position: "relative",
        display: "flex",
        width,
        height,
        alignItems: "center",
        justifyContent: "center",
        borderRadius: radius,
        background: palette.artGround,
        /* ⚠️ 唔准漏：個 glow 係 absolute 而且**特登大過個舞台**（卡圖 1.6 倍），
           冇呢句就成團暖光淌出去洗晒隔離嘅文字欄／整張 post 個底。
           第一版量過就係噉：wide 右欄變咗一片粉橙，舞台邊界完全消失。 */
        overflow: "hidden",
      }}
    >
      <div
        style={{
          position: "absolute",
          left: Math.round((width - glow) / 2),
          top: Math.round((height - glow) / 2),
          width: glow,
          height: glow,
          background: palette.artGlow,
        }}
      />
      {/* 透明角唔補底色：漸變地台襯住原生 RGBA 邊就係想要嘅效果。 */}
      <img src={art.src} alt={alt} width={art.width} height={art.height} />
    </div>
  );
}

function WideLayout({ card, art, logoSrc, palette, chart, change, asOf, name, rankTotal }: {
  card: MarketCardView;
  art: CardArt;
  logoSrc: string;
  palette: Palette;
  chart: ShareChart | null;
  change: ReturnType<typeof changeText>;
  asOf: string | null;
  name: string;
  rankTotal: number | null;
}) {
  const changeTone = change?.tone === "positive" ? palette.positive : change?.tone === "negative" ? palette.negative : palette.muted;
  const changeBg = change?.tone === "positive" ? palette.positiveSoft : change?.tone === "negative" ? palette.negativeSoft : palette.surface;
  return (
    <div style={{ display: "flex", width: "100%", height: "100%", background: palette.paper }}>
      <ArtStage
        art={art}
        alt={card.image.alt ?? name}
        width={ART_PANEL_WIDTH}
        height={630}
        radius={0}
        palette={palette}
      />
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          flex: 1,
          /* 右欄內容闊 = 1200 − 430(卡圖) − 48 − 44 = 678。 */
          padding: "40px 44px 40px 48px",
          justifyContent: "space-between",
        }}
      >
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {/*
            rank 由「22px 藥丸裝飾」升做 40px 區塊（2026-08-19）。
            佢係成張圖入面唯一會自我繁殖嘅元素：睇到「#4 of 1,307」，下一個問題梗係
            「噉 #1 係邊張？」—— 呢個問題答案喺榜頁，唔喺呢張卡度，即係一個鈎唔止一次撳。
            ⚠️ `RANKED` 呢個字**永遠唔准為咗慳位剝走**：1,307 係 CardZ 收錄兼排名嘅卡，
               唔係全世界嘅寶可夢卡（snapshot.coverage.claim = verified-top-n）。
          */}
          {card.marketRank >= 1 ? (
            <div style={{ display: "flex", alignItems: "baseline", gap: 12 }}>
              <span style={{ fontSize: WIDE_RANK_SIZE, color: palette.ink, fontWeight: 700 }}>#{card.marketRank}</span>
              <span style={{ fontSize: WIDE_TYPE.micro, color: palette.muted, letterSpacing: 1.6, fontWeight: 600 }}>
                {rankTotal ? `OF ${integer(rankTotal)} RANKED ${card.tcg.toUpperCase()}` : `RANKED ${card.tcg.toUpperCase()}`}
              </span>
            </div>
          ) : null}
          <span style={{ fontSize: wideTitleSize(name), color: palette.ink, fontWeight: 700, lineHeight: 1.12 }}>{name}</span>
          {/* set · 編號 · 印刷語言 一行過，擺喺卡名**之下**做出處收據（唔係上面做 kicker）：
              unfurl 入面卡名喺氣泡個 title 行已經出咗一次，圖入面呢行嘅角色係「邊個版本」，
              所以行 micro 唔行 meta —— 順帶慳返成組高度畀 88px hero。
              ⚠️ Inter 得 latin face，語言段一定要行英文常數表，唔准餵 CJK（會出 tofu）——
              `PRINT_LANGUAGE_EN` 就係為咗呢個而存在。 */}
          <span style={{ fontSize: WIDE_TYPE.micro, color: palette.accent, letterSpacing: 2.2, fontWeight: 600 }}>
            {(() => {
              const tail = [`#${card.collectorNumber}`, printLanguageEn(card.cardLanguage)].filter(Boolean) as string[];
              const budget = WIDE_KICKER_MAX - tail.join(" · ").length - tail.length * 3;
              return [clampSetName(card.setName.en, Math.max(14, budget)).toUpperCase(), ...tail].join(" · ");
            })()}
          </span>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          {/*
            ★ HERO：全圖唯一一個為「縮到 0.275× 都仲要讀得到」而設嘅數。
            右邊貼變動藥丸；冇變動（累積中／未有數）就**整粒消失**，唔准出 0.00% 扮平穩。
          */}
          <div style={{ display: "flex", flexDirection: "column", gap: 4, borderTop: `2px solid ${palette.line}`, paddingTop: 12 }}>
            {/* ⚠️ 窗口標籤（180D）**唔准**接喙這條 label 後面：市值係即時數，
                唔係 180 日數。寫成「PSA 10 MARKET CAP · 180D」係講假話。 */}
            <span style={{ fontSize: WIDE_TYPE.meta, color: palette.muted, letterSpacing: 1.8, fontWeight: 600 }}>PSA 10 MARKET CAP</span>
            <div style={{ display: "flex", alignItems: "center", gap: 20 }}>
              <span style={{ fontSize: WIDE_TYPE.hero, color: palette.ink, fontWeight: 700, lineHeight: 1 }}>{usd(card.marketCap.value)}</span>
              {change ? (
                <span
                  style={{
                    display: "flex",
                    background: changeBg,
                    color: changeTone,
                    /* ☠️ 唔准升返 stat：實測最闊嘅 hero（$141.95M @88px ≈ 400px）加
                       30px 藥丸 = 681px > 欄寬 678，nowrap 之後藥丸就貼死粒 M。
                       22px 量返係 622px，留返真嘅 20px gap。 */
                    fontSize: WIDE_TYPE.meta,
                    fontWeight: 600,
                    borderRadius: 999,
                    padding: "6px 18px",
                    /* ☠️ 兩粒都唔可以删：舊版嘅藥丸會被 hero 擠窄，「+43.3% 180D」
                       摺成兩行同 hero 撞埋一块。現在窗口標籤搬啦上面條 label，
                       藥丸只剩變動，再加 nowrap + 唔准縮。 */
                    whiteSpace: "nowrap",
                    flexShrink: 0,
                  }}
                >
                  {change.text} {SHARE_WINDOW_LABEL}
                </span>
              ) : null}
            </div>
          </div>
          {/* 佐證行：cap 係點計出嚟嘅（價 × 數量）。特登細過 hero —— 呢行係收據唔係主角，
              但一定要喺度，因為「你堆數作嘅」呢個反對要當場答死。 */}
          <div style={{ display: "flex", gap: 48 }}>
            <Stat palette={palette} label="PSA 10 PRICE" value={usd(card.pricePsa10.value)} valueSize={WIDE_TYPE.stat} labelSize={WIDE_TYPE.micro} />
            <Stat palette={palette} label="PSA 10 POP" value={integer(card.populationPsa10.value)} valueSize={WIDE_TYPE.stat} labelSize={WIDE_TYPE.micro} />
          </div>
          {/* 冇歷史（少過兩個價點）就成塊唔出，唔畫一條假線亦唔留空框。
              變動已經升咗上 hero，所以右槽改出日期範圍（同 post 一樣邏輯）。 */}
          {chart ? (
            <div style={{ display: "flex", flexDirection: "column", gap: 4, width: "100%" }}>
              <ChartCaption palette={palette} change={null} range={chartRange(chart)} fontSize={WIDE_TYPE.micro} />
              <img src={chart.src} alt="" width={chart.width} height={chart.height} />
            </div>
          ) : null}
          {/*
            犧牲帶（y ≳ 546）：X 裁到 2:1、Discord 縮到最細嗰陣，最底呢條最易冇。
            所以**永遠唔准放數字**——淨係 wordmark 同資料日期。
            144×62 = 2.323:1，同 SVG viewBox 969.29/419.45 = 2.311:1 差 0.7%（純文字版係 280×121）。
            **比例先係要守嗰樣，絕對尺寸唔係。** 呢個比例係量返出圖度出嚟嘅，唔係照抄 SVG
            intrinsic size：同一張卡同一支 dev server A/B（當時 declared 200×86），PNG
            wordmark 出 ink bbox 196×83 @(526,502)、SVG 出 198×84 @(525,501)，底邊兩邊
            都係 y=584；剪 viewBox 之前量過係 243×103 vs 276×117（細 12%）—— 即係 viewBox
            留白同 PNG 唔一樣嗰陣，declared 數就會靜靜出錯圖。
            2026-08-19 為咗畀返高度落 88px hero，declared 由 200×86 收做 144×62（同一比例）。
          */}
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", width: "100%" }}>
            <img src={logoSrc} alt="CardZ Marketcap" width={144} height={62} />
            {asOf ? (
              <span style={{ fontSize: WIDE_TYPE.micro, color: palette.muted, letterSpacing: 1.2, fontWeight: 400 }}>AS OF {asOf.toUpperCase()}</span>
            ) : null}
          </div>
        </div>
      </div>
    </div>
  );
}

/*
 * post（1080×1350，4:5）：由上而下 —— 品牌／卡圖／身份／四個數／走勢／出處。
 * 呢個係「人手分享」嗰張：save 落相簿再貼 Threads / X / IG feed。
 *
 * ⚠️ **點解係 4:5 唔係 9:16**（owner 2026-08-19 實測後改）：第一版出 1080×1920，
 * 貼上 Threads / X 永遠淨係佔到 post 闊度七八成。三家對直度圖都有高度上限，超過就
 * **唔裁、改為按高度縮細**，於是隔離人哋啲相滿版、我哋嗰張細一截。IG 4:5 最嚴，
 * 鎖 0.8 就三家都唔會再縮。同一條線亦寫死喺熱力圖分享圖（lib/share-image.ts
 * `SHARE_MIN_ASPECT`）—— 兩張分享圖同一個理由、同一個數。
 *
 * 順帶好處：高度收窄之後卡圖唔使再放大到 1.267×。而家 560 高 = 0.933×（**縮細**），
 * 429×600 母版一 px 都唔使靠估 —— 呢張係三個 format 入面卡面最銳嗰張。
 *
 * 垂直預算（padding 56 → 內容高 1238）。⚠️ **呢度啲數要量，唔可以估** —— 舊版呢段寫住
 * 「最壞情況 = 2 行 38px 標題… 剩 92px」，但 clampTitle 封嘅係 96 **字**唔係行數，而 38px
 * 喺 968 闊度一行得 ~38 字，所以 96 字係 **3 行**。2026-08-19 逐 px 掃出街嗰張：3 行嗰啲
 * （Van Gogh Pikachu / Charizard VSTAR UPC）ink 去到 y=1313，衝穿底 padding 20px。
 * 收走走勢圖下面嗰行日期軸（見 chartRange）之後量返：
 *   3 行卡名 —— ink 58 → 1279，上下邊距 58 / 70（padding 56）✅
 *   2 行卡名 —— ink 58 → 1257，上下邊距 58 / 92 ✅，多出嘅位由 `justifyContent:
 *     space-between` 攤落 4 個罅，張圖自動鬆返 —— 唔會好似固定 gap 噉將慳返嘅位全部
 *     堆喺底部變一大笪空白。
 * 改任何一個 block 嘅高度／字級，行返 `scripts/test-fe-og-post-layout.mjs` 重新量過。
 */
function PostLayout({ card, art, logoSrc, palette, chart, change, asOf }: {
  card: MarketCardView;
  art: CardArt;
  logoSrc: string;
  palette: Palette;
  chart: ShareChart | null;
  change: ReturnType<typeof changeText>;
  asOf: string | null;
}) {
  const name = clampTitle(card.officialName ?? card.setName.en);
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        width: "100%",
        height: "100%",
        background: palette.paper,
        padding: 56,
        justifyContent: "space-between",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", width: "100%" }}>
        {/* 190×82 = 2.316:1，同 wide 個 144×62 一樣係量返出圖嘅數（見 WideLayout 嗰個註）。 */}
        <img src={logoSrc} alt="CardZ Marketcap" width={190} height={82} />
        {card.marketRank >= 1 ? (
          <span
            style={{
              display: "flex",
              background: palette.rankBg,
              color: palette.rankInk,
              fontSize: POST_TYPE.meta,
              fontWeight: 600,
              borderRadius: 999,
              padding: "8px 22px",
            }}
          >
            #{card.marketRank}
          </span>
        ) : null}
      </div>

      <ArtStage
        art={art}
        alt={card.image.alt ?? name}
        width={968}
        height={POST_ART_STAGE_HEIGHT}
        /* 28 = 網頁 `--section-radius` 24 按 post 放大比例調高少少；1080 闊度度 24 會細到似方角。 */
        radius={28}
        palette={palette}
      />

      <div style={{ display: "flex", flexDirection: "column", gap: 10, width: "100%" }}>
        <span style={{ fontSize: POST_TYPE.micro, color: palette.accent, letterSpacing: 3.2, fontWeight: 600 }}>
          {card.tcg.toUpperCase()} · #{card.collectorNumber}
        </span>
        <span style={{ fontSize: postTitleSize(name), color: palette.ink, fontWeight: 700, lineHeight: 1.1 }}>{name}</span>
        <span style={{ fontSize: POST_TYPE.meta, color: palette.muted, lineHeight: 1.3, fontWeight: 400 }}>{clampSetName(card.setName.en)}</span>
      </div>

      {/* 四個數一行，**四格同一個字級**（見 POST_TYPE 個註）—— 同網頁四個 KPI 由 `--kpi-fs`
          一個變數出係同一個道理：同一行、同一個 label 級、同一條線之下，字級唔同讀落唔似
          分主次，似排錯版。要分主次就靠位置（market cap 坐第一格）。 */}
      <div style={{ display: "flex", gap: 40, borderTop: `2px solid ${palette.line}`, paddingTop: 22, width: "100%" }}>
        <Stat palette={palette} label="MARKET CAP" value={usd(card.marketCap.value)} valueSize={POST_TYPE.stat} />
        <Stat palette={palette} label="PSA 10 PRICE" value={usd(card.pricePsa10.value)} valueSize={POST_TYPE.stat} />
        <Stat palette={palette} label="PSA 10 POP" value={integer(card.populationPsa10.value)} valueSize={POST_TYPE.stat} />
        {change ? (
          <Stat palette={palette} label={`${SHARE_WINDOW_LABEL} CHANGE`} value={change.text} valueSize={POST_TYPE.stat} tone={change.tone} />
        ) : null}
      </div>

      {chart ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 8, width: "100%", borderTop: `2px solid ${palette.line}`, paddingTop: 22 }}>
          {/* post 嘅變動已經喺上面四個數嗰行出咗一次（`180D CHANGE`），caption 唔好再出多次
              —— 同一個數喺同一張圖出兩次係雜訊。右邊個位讓返俾日期範圍（見 chartRange 個註）。
              wide 冇嗰個 stat（得三個數），所以嗰邊照傳 change。 */}
          <ChartCaption palette={palette} change={null} range={chartRange(chart)} fontSize={POST_TYPE.micro} />
          <img src={chart.src} alt="" width={chart.width} height={chart.height} />
        </div>
      ) : null}

      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", width: "100%" }}>
        <span style={{ fontSize: POST_TYPE.micro, color: palette.muted, letterSpacing: 1.8, fontWeight: 600 }}>CARDZMARKETCAP.COM</span>
        {asOf ? (
          <span style={{ fontSize: POST_TYPE.micro, color: palette.muted, letterSpacing: 1.2, fontWeight: 400 }}>AS OF {asOf.toUpperCase()}</span>
        ) : null}
      </div>
    </div>
  );
}

function readTheme(value: string | null, fallback: ShareTheme): ShareTheme {
  return value === "dark" ? "dark" : value === "light" ? "light" : fallback;
}

export async function GET(request: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const query = new URL(request.url).searchParams;
  const format = readShareFormat(query.get("format"));
  const spec = FORMATS[format];
  const theme = readTheme(query.get("theme"), spec.defaultTheme);
  const palette = THEMES[theme];

  const snapshot = await loadMarketSnapshot().catch(() => null);
  const card = snapshot
    ? [...snapshot.top100, ...snapshot.watchlist].find((candidate) => candidate.id === id) ?? null
    : null;

  /*
   * 揾唔到 id 就同 `api/v1/cards/[id]` 一樣回 404 —— 個 id 唔存在，張圖亦唔存在。
   * 原本呢句寫 `fetch(new URL("/brand/og-light.png", origin))`：容器 fetch 返自己
   * 個 public origin 攞一個坐喺本機硬碟嘅檔（server → Cloudflare → server）。
   * 實測 `/api/og/card/does-not-exist` 回 500 空 body，而 `/brand/og-light.png`
   * 自己 200 —— 即係 fallback 由第一日就冇成功過，只係冇人拉過條 URL。
   */
  if (!card) return new Response("Card not found", { status: 404 });

  /*
   * Wordmark 餵 SVG 唔餵 PNG（fe05(logo-svg)，2026-08-18）：satori 內置 resvg 識直接
   * 行 vector，唔使解一張 879×380 嘅 PNG 再喺 200px 闊度縮 —— 出嚟嘅邊係真銳邊。
   * ⚠️ **一定要 `;base64,`**：`svg+xml;charset=utf-8,` + `encodeURIComponent` 嗰種寫法
   *    行到 satori 內部個 `btoa` 就會 `InvalidCharacterError`（實測），OG 端點直接 500。
   * ⚠️ 呢條路 **冇** letterbox 呢個 failure mode —— 舊註寫錯咗，覆核 2026-08-18 用真嘅
   *    @vercel/og 餵四個變體重現唔到：`width="900"` 而 viewBox 唔郁、甚至 width/height
   *    兩個屬性完全剝走，ink 都係 198×84 @(957,501)、inkPx 6435，**逐個數一樣**。
   *    原因係下面個 `<img width={200} height={86}>` 已經俾咗 explicit box，resvg 唔會理
   *    SVG 自己嗰兩個屬性。真正會令 logo 縮細嘅係 **share-image 嗰條 Chrome canvas 路**
   *    （見 lib/share-image.ts 個註）—— width/viewBox 唔一致喺嗰邊會細 ~7%。
   * ⚠️ skin 要跟 theme：白 wordmark 畫落 #f7f7f5 = 隱形，黑描邊嗰版畫落 #101010 一樣冇。
   *    對應表喺上面 `LOGO_BY_THEME`，`scripts/test-fe-brand-logo-svg.mjs` 驗住佢。
   * 兩路 `existsSync` 同上面 OG font 嗰段一樣：dev 由 repo root 行、standalone build
   * `process.cwd()` 已經係 `apps/web`。搵唔到就照舊回 500 —— 冇 wordmark 嘅 OG 圖唔算出到街。
   */
  const { existsSync } = await import("node:fs");
  const logoRelative = LOGO_BY_THEME[theme];
  const logoFile = [
    resolve(process.cwd(), "public", logoRelative),
    resolve(process.cwd(), "apps/web/public", logoRelative),
  ].find((path) => existsSync(path));
  if (!logoFile) return new Response("Brand mark missing", { status: 500 });
  const logoSrc = `data:image/svg+xml;base64,${(await readFile(logoFile)).toString("base64")}`;

  /* fail-open：卡圖任何一步炸（asset 唔喺度、sharp 載唔到、解碼失敗）都退返
     純文字版，唔准變 500 —— OG 端點死咗等於社交分享冇圖，比冇卡圖仲差。 */
  let art: CardArt | null = null;
  try {
    art = format === "post"
      ? await loadCardArt(card, POST_ART_MAX_WIDTH, POST_ART_MAX_HEIGHT, true)
      : await loadCardArt(card, ART_MAX_WIDTH, ART_MAX_HEIGHT);
  } catch (error) {
    noteArtFailure(id, error);
    art = null;
  }

  /* 走勢圖同卡圖一樣 fail-open：`buildShareChart` 少過兩個價點就回 null，layout 見到
     null 就成塊唔出。**唔准**因為冇歷史而令張圖 500 或者畫一條假線。 */
  const chart = buildShareChart(card.historyDaily ?? [], SHARE_WINDOW_DAYS, {
    /* wide 右欄由 620 闊做 678（卡圖鐵路 468 → 430）；高度由 64 收到 44 讓位畀 88px hero。 */
    width: format === "post" ? 968 : 678,
    height: format === "post" ? 120 : 34,
    lineWidth: format === "post" ? 3 : 2.5,
    /* wide 版扁到得 64px，成交 bar 會同條價線打架，所以只喺 post 出（同網頁一樣兩層都有）。 */
    bars: format === "post",
    grid: format === "post",
    palette: { accent: palette.accent, grid: palette.line, bar: palette.salesBar, surface: palette.surface },
  });

  const change = changeText(card);
  /*
   * 「資料時間」用卡自己嗰個價格觀察日，冇先跌 snapshot 時間 —— 同卡頁 `.data-time`
   * 同一條式（card-detail.tsx）。唔可以用 generation 時間：實測 1286 張出街卡入面
   * 949 張（73.8%）真實價格日比 generation 早 8 日以上。
   */
  const asOf = shortDate(card.pricePsa10.checkedAt || card.pricePsa10.asOf || snapshot?.effectiveAt);

  const fonts = await loadOgFonts();

  /*
   * wide 用短卡名（同 `<title>` / `og:title` 同一個 helper），唔再用 96 字嘅 PSA 全串。
   * 全串喺 630px 高嘅畫布度食三行，逼到 88px hero 冇位企；而縮到 WhatsApp 氣泡入面
   * 嗰三行字本身一個字都讀唔到。圖入面永遠行 en（Inter latin-only）。
   */
  const wideName = shortSubject(card, "en", WIDE_NAME_MAX);
  /*
   * 同 TCG 有排名嘅卡總數。`snapshot.top100` 只係排頭 100 張，其餘坐喺 `watchlist` ——
   * 淨數 top100 會出「OF 93 RANKED POKÉMON」呢個 13 倍細嘅假分母。攞唔到 snapshot
   * （fail-open）就係 null，嗰陣淨寫「RANKED POKÉMON」，唔准填個似層層嘅數。
   */
  const rankTotal = snapshot
    ? [...snapshot.top100, ...snapshot.watchlist]
      .filter((candidate) => candidate.tcg === card.tcg && candidate.marketRank >= 1).length || null
    : null;

  const element = !art
    ? <TextOnlyLayout card={card} logoSrc={logoSrc} palette={palette} format={format} />
    : format === "post"
      ? <PostLayout card={card} art={art} logoSrc={logoSrc} palette={palette} chart={chart} change={change} asOf={asOf} />
      : <WideLayout card={card} art={art} logoSrc={logoSrc} palette={palette} chart={chart} change={change} asOf={asOf} name={wideName} rankTotal={rankTotal} />;

  const image = new ImageResponse(element, {
    width: spec.width,
    height: spec.height,
    /* undefined = 載唔到字體（上面已經 warn 咗），交返俾 satori 用 bundled font，唔好因為字體炸咗張圖 */
    ...(fonts ? { fonts } : {}),
    /*
     * 卡圖本身係 content-addressed（immutable），但張 OG 仲印住市值／PSA10 價／
     * pop，呢啲跟 snapshot 每日郁，所以唔可以行 market-media.ts 嗰條一年
     * immutable。對齊 next.config.ts 嘅 HTML `s-maxage=300`，再俾長少少嘅
     * stale-while-revalidate 令社交爬蟲永遠有嘢即刻攞。
     */
    headers: {
      "Cache-Control": "public, max-age=300, s-maxage=300, stale-while-revalidate=86400",
      /*
       * fail-open 冇 status code 分別（兩邊都係 200，成功 image/jpeg），純文字版本身又有
       * 91KB，size floor 都分唔到。所以每個 response 自己講返行咗邊條路：
       * `curl -sI '.../api/og/card/<id>?format=post' | grep x-og` →
       *   x-og-art 1/0    = 有冇卡圖（0 多數即係 standalone 冇 ship sharp）
       *   x-og-chart 1/0  = 有冇走勢圖（0 = 呢張卡窗內少過兩個價點）
       *   x-og-format/theme = 實際行咗邊個 layout（query 打錯字會靜靜跌返 wide + 該 format 預設 theme）
       * 上面 noteArtFailure 個 log 一個 process 只嗌一次，靠佢驗證唔到。
       */
      "x-og-art": art ? "1" : "0",
      "x-og-chart": chart ? "1" : "0",
      "x-og-format": format,
      "x-og-theme": theme,
    },
  });

  /*
   * 轉 JPEG（見上面 `JPEG_QUALITY` 個註）。
   * ⚠️ fail-open：sharp 載唔到／轉唔到就照出返 PNG —— 一張大過閘嘅圖，好過冇圖。
   *    嗰陣 `x-og-bytes` 仍然講真數，CI（test-fe-og-unfurl）就會紅，唔會靜靜過骨。
   *    落到用戶手上嗰個檔名亦跟返真 MIME（`lib/share-file.ts` `filenameFor`），
   *    fail-open 出 PNG 都唔會變成一個叫 `.jpg` 嘅 PNG。
   */
  /*
   * ☠️ 用 `Headers` 實例、行 `.set()`——**唔可以** spread
   *    `Object.fromEntries(image.headers)` 再插一条 `"Content-Type"`。
   *    `Headers` iterator 吐出嘅 key 係小寫（`content-type`），喙 plain object
   *    裏面同 `"Content-Type"` 係**兩把鍵**，兩條都會落到個 response，
   *    出街嘅值係 `image/png, image/jpeg`。實測過：唔會喁、唔會警告，
   *    sharp 一樣 decode 到（看 bytes），但 unfurler 係看個 header 字串嘅。
   */
  const png = Buffer.from(await image.arrayBuffer());
  const respond = (body: Buffer, mime: string) => {
    const headers = new Headers(image.headers);
    headers.set("Content-Type", mime);
    headers.set("Content-Length", String(body.length));
    headers.set("x-og-bytes", String(body.length));
    return new Response(new Uint8Array(body), { headers });
  };
  try {
    const { default: sharp } = await import("sharp");
    const jpeg = await sharp(png)
      /* 4:4:4 = 唔做色度抽樣。細字同 #e8823f 橙色 accent 靠佢先唔會糊邊。 */
      .jpeg({ quality: JPEG_QUALITY[format], chromaSubsampling: "4:4:4", mozjpeg: true })
      .toBuffer();
    return respond(jpeg, "image/jpeg");
  } catch (error) {
    noteArtFailure(`${id}:jpeg`, error);
    return respond(png, "image/png");
  }
}
