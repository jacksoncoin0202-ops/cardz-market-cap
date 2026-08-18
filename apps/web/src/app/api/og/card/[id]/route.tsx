import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { ImageResponse } from "next/og";
import { buildShareChart, type ShareChart } from "@/lib/share-chart";
import { loadNodeMarketAsset, loadMarketSnapshot } from "@/lib/server-snapshot";
import { defaultMarketWindow, marketWindowDays, type MarketCardView, type MarketWindow } from "@/lib/types";

export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

/*
 * 兩款分享圖，一條 route（fe06(share-card)，2026-08-19）：
 *   `?format=wide`（預設，1200×630）  —— 社交 unfurl。`twitter:card=summary_large_image`
 *      同 `og:image` 指住嘅就係佢，尺寸由 `lib/route-metadata.ts` 宣告，唔准亂改。
 *   `?format=story`（1080×1920，9:16）—— 人手分享嗰張：存落相簿、send 落 LINE／WhatsApp、
 *      落 IG／Threads story。owner 2026-08-19：「手機一打開就見到係全屏幕，噉嘅樣先至似樣」——
 *      9:16 就係手機睇相嗰個滿版比例，1200×630 喺豎屏得中間一條。版位鬆，所以完整走勢圖 +
 *      成交 bar + 四個數 + 日期軸全部出得晒。
 *
 * **點解唔開多條 route**：兩款圖嘅資料來源、卡圖解碼、字體、wordmark、fail-open 行為
 * 完全一樣，開兩條就係同一條問題兩份 copy（AGENTS.md 規矩 13）。分別淨係 layout。
 *
 * `?theme=light|dark`：wide 預設 light（社交爬蟲唔會帶 theme，而 unfurl 卡片多數坐喺
 * 淺色 feed），story 預設 dark（owner 要「氣氛、優雅」——深底先襯得返卡圖嗰個透明
 * RGBA 邊，亦係手機滿版睇相嗰陣最唔刺眼；同網站 `[data-theme="dark"]` 係同一組 token，
 * 唔係另一套色）。
 */
type ShareFormat = "wide" | "story";
type ShareTheme = "light" | "dark";

const FORMATS: Record<ShareFormat, { width: number; height: number; defaultTheme: ShareTheme }> = {
  wide: { width: 1200, height: 630, defaultTheme: "light" },
  story: { width: 1080, height: 1920, defaultTheme: "dark" },
};

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

const ART_PANEL_WIDTH = 468; /* 1200 嘅 39%，大約計劃講嘅 ~40% */
const ART_MAX_WIDTH = 384;
const ART_MAX_HEIGHT = 522;
/*
 * story 卡圖舞台（952 闊 = 1080 − 64×2）同卡圖上限。
 *
 * ⚠️ **卡圖母版得 429×600**：2026-08-19 抽驗 `data/public/market-assets` 60 個全尺寸
 * asset，52 個係 429×600，其餘係 box 類（1000×730 / 1600×1417 / 517×550）。即係卡面
 * 本身冇更高清嘅來源 —— 想「唔蒙查查」，唯一做法係**唔好放太大**，唔係放大咗嗌高清。
 * 760 高 = 1.267× 放大，配 lanczos3 + 輕銳化（見 loadCardArt）喺手機睇實色邊仲食得住；
 * 過 1.5× 就開始見到 upscale 嗰浸糊。文字／走勢圖／wordmark 全部係 vector，喺 1080 闊度
 * 直接畫，同卡圖冇關係 —— 嗰啲係真銳，所以成張圖睇落仍然係「高清」。 */
const STORY_ART_STAGE_HEIGHT = 820;
const STORY_ART_MAX_WIDTH = 580;
const STORY_ART_MAX_HEIGHT = 760;

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
 * `allowUpscale`：
 *   wide（卡圖畫 384×522）—— 唔開，母版 429×600 已經夠大，縮就得。
 *   story（畫到 760 高）—— 要開，否則 `withoutEnlargement` 會靜靜出返 429×600 嘅細圖，
 *     喺 1080 闊嘅版上面得半個位、成張圖散晒，而且零 error（最難捉嗰種）。
 * 放大一定會軟，所以補一記**輕**銳化。唔可以下重手：卡圖係去咗底嘅 RGBA，邊緣一過銳
 * 就沿住 alpha 邊出白光暈，喺深色 story 底特別現眼。
 */
async function loadCardArt(
  card: MarketCardView,
  maxWidth: number,
  maxHeight: number,
  allowUpscale = false,
): Promise<CardArt | null> {
  if (card.image.kind !== "raw_front") return null;
  /* 放大嗰陣一定要攞全尺寸母版，唔可以攞 `_600` 派生檔再放大 —— 兩者同高（600），
     但派生檔已經再壓過一次 WebP，放大會連壓縮 artifact 一齊放大。
     縮細嗰路照用 `_600`：細檔解碼快，結果一模一樣。 */
  const source = allowUpscale ? card.image.url : card.image.variants?.["600"] ?? card.image.url;
  const asset = source.split("/").pop();
  if (!asset) return null;

  const node = await loadNodeMarketAsset(asset);
  if (!node) return null;

  const { default: sharp } = await import("sharp");
  let pipeline = sharp(Buffer.from(node.body)).resize({
    width: maxWidth,
    height: maxHeight,
    fit: "inside",
    withoutEnlargement: !allowUpscale,
    kernel: "lanczos3",
  });
  if (allowUpscale) pipeline = pipeline.sharpen({ sigma: 0.6, m1: 0.4, m2: 0.8 });
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

/* 卡名有長到 90+ 字（例：「… Pikachu With Grey Felt Hat Pokemon X Van Gogh 085/SVP」），
   右欄淨得 620px，所以字級跟長度落，超長先切。 */
function titleSize(name: string): number {
  if (name.length <= 34) return 52;
  if (name.length <= 60) return 42;
  return 34;
}

/* story 成幅 952px 可用闊（比 wide 個 620 闊 54%），所以同一長度食得起大兩級。
   44 呢級係下限：3 行 × 1.1 行距 = 145px，配 820px 舞台先啱啱塞得晒（見 StoryLayout 個預算註）。 */
function storyTitleSize(name: string): number {
  if (name.length <= 34) return 68;
  if (name.length <= 60) return 56;
  return 44;
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

/* set 名最長 74 字（實測全 1604 張榜卡）。右欄 24px 得一行位，兩行就會頂到
   wordmark 距底邊剩 10px（量過：panel ink bottom margin 41 → 10）。 */
function clampSetName(name: string): string {
  return name.length <= 46 ? name : `${name.slice(0, 45).trimEnd()}…`;
}

type Palette = (typeof THEMES)[ShareTheme];

function Stat({ label, value, valueSize = 52, palette, tone }: {
  label: string;
  value: string;
  valueSize?: number;
  palette: Palette;
  tone?: "positive" | "negative" | "neutral";
}) {
  const color = tone === "positive" ? palette.positive : tone === "negative" ? palette.negative : palette.ink;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {/* 對齊網頁 §1.3.2 角色：label = --w-label 600（唔係 400），data = --w-data 600（唔係 700）。
          Inter 600 比 bundled font 700 幼但字身闊少少，tracking 由 1.6 → 1.8 補返個呼吸位。 */}
      <span style={{ fontSize: 22, color: palette.muted, letterSpacing: 1.8, fontWeight: 600 }}>{label}</span>
      <span style={{ fontSize: valueSize, color, fontWeight: 600 }}>{value}</span>
    </div>
  );
}

/* 走勢圖上面嗰行字：左邊講「畫緊咩、幾長」，右邊講變動。SVG 入面冇字（見
   lib/share-chart.ts 檔頭），所有座標軸文字都喺呢度用 satori 畫。 */
function ChartCaption({ palette, change, fontSize }: {
  palette: Palette;
  change: ReturnType<typeof changeText>;
  fontSize: number;
}) {
  const tone = change?.tone === "positive" ? palette.positive : change?.tone === "negative" ? palette.negative : palette.muted;
  return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", width: "100%" }}>
      <span style={{ fontSize, color: palette.muted, letterSpacing: 1.8, fontWeight: 600 }}>
        PSA 10 PRICE · {SHARE_WINDOW_LABEL}
      </span>
      {change ? <span style={{ fontSize, color: tone, fontWeight: 600 }}>{change.text}</span> : null}
    </div>
  );
}

/* 走勢圖下面嗰行：頭尾兩個月份 —— 同網頁 history chart 底下嗰兩個 label 同一個角色。 */
function ChartAxis({ palette, chart, fontSize }: { palette: Palette; chart: ShareChart; fontSize: number }) {
  const from = monthYear(chart.firstAt);
  const to = monthYear(chart.lastAt);
  if (!from || !to) return null;
  return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", width: "100%" }}>
      <span style={{ fontSize, color: palette.muted, fontWeight: 400 }}>{from}</span>
      <span style={{ fontSize, color: palette.muted, fontWeight: 400 }}>{to}</span>
    </div>
  );
}

/* 今日出街嗰版：淨文字、成幅 1200 闊。攞唔到卡圖就原封不動退返呢個。 */
function TextOnlyLayout({ card, logoSrc, palette }: { card: MarketCardView; logoSrc: string; palette: Palette }) {
  const name = clampTitle(card.officialName ?? card.setName.en);
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        width: "100%",
        height: "100%",
        background: palette.paper,
        padding: "64px 72px",
        justifyContent: "space-between",
      }}
    >
      <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
        {/* kicker = --w-label 600 + --track-kicker 級數；Inter 600 大寫比舊 bundled font 400 闊，
            4 → 3.4 先返返舊闊度（最長 kicker「POKEMON · #SV4A-205」唔可以谷長咗撞落標題）。 */}
        <span style={{ fontSize: 24, color: palette.accent, letterSpacing: 3.4, fontWeight: 600 }}>
          {card.tcg.toUpperCase()} · #{card.collectorNumber}
        </span>
        <span style={{ fontSize: textOnlyTitleSize(name), color: palette.ink, fontWeight: 700, lineHeight: 1.1 }}>{name}</span>
        {/* set 名明寫 400：唔好靠 satori 嘅默認 —— 我哋只 register 400/600/700，唔明寫就靠彩數 */}
        <span style={{ fontSize: 30, color: palette.muted, lineHeight: 1.3, fontWeight: 400 }}>{clampSetName(card.setName.en)}</span>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 26 }}>
        <div style={{ display: "flex", gap: 72, borderTop: `2px solid ${palette.line}`, paddingTop: 30 }}>
          <Stat palette={palette} label="MARKET CAP" value={usd(card.marketCap.value)} />
          <Stat palette={palette} label="PSA 10 PRICE" value={usd(card.pricePsa10.value)} />
          <Stat palette={palette} label="PSA 10 POP" value={integer(card.populationPsa10.value)} />
        </div>
        <img src={logoSrc} alt="CardZ Marketcap" width={280} height={121} />
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
           冇呢句就成團暖光淌出去洗晒隔離嘅文字欄／整張 story 個底。
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

/* wide（1200×630）：左卡圖右數據。fe06 起三個數擺同一行（本來 1+2 兩行），
   慳返嗰行位擺走勢圖 —— 量過：三個數 42/34/34 喺 620px 右欄最闊食 529px，仲有 91px 鬆動。 */
function WideLayout({ card, art, logoSrc, palette, chart, change, asOf }: {
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
          /* 上下 44：最壞情況（3 行 34px 標題 + 一行 set 名）量到 ink 由 top 44
             去到 bottom 589，即上下邊距 44 / 41，齊頭。set 名唔 clamp 就會變兩行、
             底邊距跌到 10px（量過）。 */
          padding: "44px 56px",
          justifyContent: "space-between",
        }}
      >
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
            {card.marketRank >= 1 ? (
              <span
                style={{
                  display: "flex",
                  background: palette.rankBg,
                  color: palette.rankInk,
                  fontSize: 24,
                  /* chip = 網頁 `.detail-rank` 嗰個角色，C2 已經由 650 收做 600 */
                  fontWeight: 600,
                  borderRadius: 999,
                  padding: "6px 18px",
                }}
              >
                #{card.marketRank}
              </span>
            ) : null}
            <span style={{ fontSize: 22, color: palette.accent, letterSpacing: 3, fontWeight: 600 }}>
              {card.tcg.toUpperCase()} · #{card.collectorNumber}
            </span>
          </div>
          <span style={{ fontSize: titleSize(name), color: palette.ink, fontWeight: 700, lineHeight: 1.12 }}>{name}</span>
          <span style={{ fontSize: 22, color: palette.muted, lineHeight: 1.3, fontWeight: 400 }}>{clampSetName(card.setName.en)}</span>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
          <div style={{ display: "flex", gap: 44, borderTop: `2px solid ${palette.line}`, paddingTop: 20 }}>
            <Stat palette={palette} label="MARKET CAP" value={usd(card.marketCap.value)} valueSize={44} />
            <Stat palette={palette} label="PSA 10 PRICE" value={usd(card.pricePsa10.value)} valueSize={34} />
            <Stat palette={palette} label="PSA 10 POP" value={integer(card.populationPsa10.value)} valueSize={34} />
          </div>
          {/* 冇歷史（少過兩個價點）就成塊唔出，唔畫一條假線亦唔留空框。 */}
          {chart ? (
            <div style={{ display: "flex", flexDirection: "column", gap: 6, width: "100%" }}>
              <ChartCaption palette={palette} change={change} fontSize={18} />
              <img src={chart.src} alt="" width={chart.width} height={chart.height} />
            </div>
          ) : null}
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", width: "100%" }}>
            {/* 200×86 = 2.326:1，同 SVG viewBox 969.29/419.45 = 2.311:1 差 0.6%（純文字版係 280×121）。
                呢兩個數唔可以照抄新 SVG 嘅 intrinsic size —— 一定要量返出圖：同一張卡同一支 dev
                server A/B，PNG wordmark 出 ink bbox 196×83 @(526,502)、SVG 出 198×84 @(525,501)，
                底邊兩邊都係 y=584（+2/+1 px 純粹係 vector 抗鋸齒比 PNG 自己嗰條邊多留一格淡墨）。
                即係冇縮水、亦冇撞底邊。剪 viewBox 之前量過係 243×103 vs 276×117（細 12%）——
                所以 viewBox 留白同 PNG 唔一樣嗰陣，呢兩個 declared 數就會靜靜出錯圖。 */}
            <img src={logoSrc} alt="CardZ Marketcap" width={200} height={86} />
            {asOf ? (
              <span style={{ fontSize: 18, color: palette.muted, letterSpacing: 1.2, fontWeight: 400 }}>AS OF {asOf.toUpperCase()}</span>
            ) : null}
          </div>
        </div>
      </div>
    </div>
  );
}

/*
 * story（1080×1920，9:16）：由上而下 —— 品牌／卡圖／身份／四個數／走勢／出處。
 * 呢個係「人手分享、手機滿版睇」嗰張，所以四個數同完整走勢圖（連成交 bar、日期軸）
 * 全部出得起。
 *
 * 垂直預算（padding 64 → 內容高 1792，最壞情況 = 3 行 44px 標題）：
 *   品牌行 95 + 舞台 820 + 身份 240 + 四個數 132 + 走勢 348 + 出處 29 = 1664，
 *   剩 128px 由 `justifyContent: space-between` 攤落 5 個罅（每個 ~25）。
 *   短卡名（1 行 68px）身份跌到 169 → 每個罅 ~39，張圖自動鬆返 —— 唔會好似固定 gap
 *   噉將慳返嘅位全部堆喺底部變一大笪空白。
 */
function StoryLayout({ card, art, logoSrc, palette, chart, change, asOf }: {
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
        {/* 220×95 = 2.316:1，同 wide 個 200×86 一樣係量返出圖嘅數（見 WideLayout 嗰個註）。 */}
        <img src={logoSrc} alt="CardZ Marketcap" width={220} height={95} />
        {card.marketRank >= 1 ? (
          <span
            style={{
              display: "flex",
              background: palette.rankBg,
              color: palette.rankInk,
              fontSize: 30,
              fontWeight: 600,
              borderRadius: 999,
              padding: "10px 26px",
            }}
          >
            #{card.marketRank}
          </span>
        ) : null}
      </div>

      <ArtStage
        art={art}
        alt={card.image.alt ?? name}
        width={952}
        height={STORY_ART_STAGE_HEIGHT}
        /* 32 = 網頁 `--section-radius` 24 按 story 放大比例調高少少；手機滿版睇，24 喺
           1080 闊度會細到似方角。 */
        radius={32}
        palette={palette}
      />

      <div style={{ display: "flex", flexDirection: "column", gap: 12, width: "100%" }}>
        <span style={{ fontSize: 28, color: palette.accent, letterSpacing: 3.6, fontWeight: 600 }}>
          {card.tcg.toUpperCase()} · #{card.collectorNumber}
        </span>
        <span style={{ fontSize: storyTitleSize(name), color: palette.ink, fontWeight: 700, lineHeight: 1.1 }}>{name}</span>
        <span style={{ fontSize: 30, color: palette.muted, lineHeight: 1.3, fontWeight: 400 }}>{clampSetName(card.setName.en)}</span>
      </div>

      {/* 四個數一行：量過最闊嗰行由 label 主導（PSA 10 PRICE 最長），約 810px < 952px 可用闊。 */}
      <div style={{ display: "flex", gap: 44, borderTop: `2px solid ${palette.line}`, paddingTop: 26, width: "100%" }}>
        <Stat palette={palette} label="MARKET CAP" value={usd(card.marketCap.value)} valueSize={52} />
        <Stat palette={palette} label="PSA 10 PRICE" value={usd(card.pricePsa10.value)} valueSize={40} />
        <Stat palette={palette} label="PSA 10 POP" value={integer(card.populationPsa10.value)} valueSize={40} />
        {change ? (
          <Stat palette={palette} label={`${SHARE_WINDOW_LABEL} CHANGE`} value={change.text} valueSize={40} tone={change.tone} />
        ) : null}
      </div>

      {chart ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 10, width: "100%", borderTop: `2px solid ${palette.line}`, paddingTop: 26 }}>
          {/* story 嘅變動已經喺上面四個數嗰行出咗一次（`180D CHANGE`），
              caption 唔好再出多次 —— 同一個數喺同一張圖出兩次係雜訊。
              wide 冇嗰個 stat（得三個數），所以嗰邊照傳 change。 */}
          <ChartCaption palette={palette} change={null} fontSize={26} />
          <img src={chart.src} alt="" width={chart.width} height={chart.height} />
          <ChartAxis palette={palette} chart={chart} fontSize={24} />
        </div>
      ) : null}

      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", width: "100%" }}>
        <span style={{ fontSize: 24, color: palette.muted, letterSpacing: 1.8, fontWeight: 600 }}>CARDZMARKETCAP.COM</span>
        {asOf ? (
          <span style={{ fontSize: 24, color: palette.muted, letterSpacing: 1.2, fontWeight: 400 }}>AS OF {asOf.toUpperCase()}</span>
        ) : null}
      </div>
    </div>
  );
}

function readFormat(value: string | null): ShareFormat {
  return value === "story" ? "story" : "wide";
}
function readTheme(value: string | null, fallback: ShareTheme): ShareTheme {
  return value === "dark" ? "dark" : value === "light" ? "light" : fallback;
}

export async function GET(request: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const query = new URL(request.url).searchParams;
  const format = readFormat(query.get("format"));
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
    art = format === "story"
      ? await loadCardArt(card, STORY_ART_MAX_WIDTH, STORY_ART_MAX_HEIGHT, true)
      : await loadCardArt(card, ART_MAX_WIDTH, ART_MAX_HEIGHT);
  } catch (error) {
    noteArtFailure(id, error);
    art = null;
  }

  /* 走勢圖同卡圖一樣 fail-open：`buildShareChart` 少過兩個價點就回 null，layout 見到
     null 就成塊唔出。**唔准**因為冇歷史而令張圖 500 或者畫一條假線。 */
  const chart = buildShareChart(card.historyDaily ?? [], SHARE_WINDOW_DAYS, {
    width: format === "story" ? 952 : 620,
    height: format === "story" ? 240 : 64,
    lineWidth: format === "story" ? 4 : 2.5,
    /* wide 版扁到得 64px，成交 bar 會同條價線打架，所以只喺 story 出（同網頁一樣兩層都有）。 */
    bars: format === "story",
    grid: format === "story",
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

  const element = !art
    ? <TextOnlyLayout card={card} logoSrc={logoSrc} palette={palette} />
    : format === "story"
      ? <StoryLayout card={card} art={art} logoSrc={logoSrc} palette={palette} chart={chart} change={change} asOf={asOf} />
      : <WideLayout card={card} art={art} logoSrc={logoSrc} palette={palette} chart={chart} change={change} asOf={asOf} />;

  return new ImageResponse(element, {
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
       * fail-open 冇 status code 分別（兩邊都係 200 image/png），純文字版本身又有
       * 91KB，size floor 都分唔到。所以每個 response 自己講返行咗邊條路：
       * `curl -sI '.../api/og/card/<id>?format=story' | grep x-og` →
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
}
