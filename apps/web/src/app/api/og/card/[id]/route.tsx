import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { ImageResponse } from "next/og";
import { loadNodeMarketAsset, loadMarketSnapshot } from "@/lib/server-snapshot";
import type { MarketCardView } from "@/lib/types";

export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

/*
 * satori 冇 CSS var，所以要寫死 hex。每一粒都係 globals.css `:root` 嗰份
 * light token 嘅字面值（--paper / --ink / --muted / --line / --accent）。
 * OG 圖冇 theme（social scraper 唔會帶 data-theme），所以固定行 brand light 面。
 */
const PAPER = "#f7f7f5"; /* --paper */
const INK = "#171717"; /* --ink */
const MUTED = "#6f6f6b"; /* --muted */
const LINE = "#e6e6e2"; /* --line */
const ACCENT = "#b85416"; /* --accent */
/* 卡圖係透明底（原生 RGBA 圓角，globals.css --card-img-radius 註解），要塊深色
   中性地台先襯得返個邊。值 = [data-theme="dark"] 嘅 --surface。 */
const ART_GROUND = "#1a1a1a";

const ART_PANEL_WIDTH = 468; /* 1200 嘅 39%，大約計劃講嘅 ~40% */
const ART_MAX_WIDTH = 384;
const ART_MAX_HEIGHT = 522;

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

async function loadCardArt(card: MarketCardView): Promise<CardArt | null> {
  if (card.image.kind !== "raw_front") return null;
  /* 攞 _600 派生檔（原圖 429×600 級數，OG 只用到 384×522），冇就退返全尺寸。 */
  const source = card.image.variants?.["600"] ?? card.image.url;
  const asset = source.split("/").pop();
  if (!asset) return null;

  const node = await loadNodeMarketAsset(asset);
  if (!node) return null;

  const { default: sharp } = await import("sharp");
  const { data, info } = await sharp(Buffer.from(node.body))
    .resize({
      width: ART_MAX_WIDTH,
      height: ART_MAX_HEIGHT,
      fit: "inside",
      withoutEnlargement: true,
    })
    .png({ compressionLevel: 9 })
    .toBuffer({ resolveWithObject: true });

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

/* 卡名有長到 90+ 字（例：「… Pikachu With Grey Felt Hat Pokemon X Van Gogh 085/SVP」），
   右欄淨得 592px，所以字級跟長度落，超長先切。 */
function titleSize(name: string): number {
  if (name.length <= 34) return 52;
  if (name.length <= 60) return 42;
  return 34;
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

function Stat({ label, value, valueSize = 52 }: { label: string; value: string; valueSize?: number }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {/* 對齊網頁 §1.3.2 角色：label = --w-label 600（唔係 400），data = --w-data 600（唔係 700）。
          Inter 600 比 bundled font 700 幼但字身闊少少，tracking 由 1.6 → 1.8 補返個呼吸位。 */}
      <span style={{ fontSize: 22, color: MUTED, letterSpacing: 1.8, fontWeight: 600 }}>{label}</span>
      <span style={{ fontSize: valueSize, color: INK, fontWeight: 600 }}>{value}</span>
    </div>
  );
}

/* 今日出街嗰版：淨文字、成幅 1200 闊。攞唔到卡圖就原封不動退返呢個。 */
function TextOnlyLayout({ card, logoSrc }: { card: MarketCardView; logoSrc: string }) {
  const name = clampTitle(card.officialName ?? card.setName.en);
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        width: "100%",
        height: "100%",
        background: PAPER,
        padding: "64px 72px",
        justifyContent: "space-between",
      }}
    >
      <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
        {/* kicker = --w-label 600 + --track-kicker 級數；Inter 600 大寫比舊 bundled font 400 闊，
            4 → 3.4 先返返舊闊度（最長 kicker「POKEMON · #SV4A-205」唔可以谷長咗撞落標題）。 */}
        <span style={{ fontSize: 24, color: ACCENT, letterSpacing: 3.4, fontWeight: 600 }}>
          {card.tcg.toUpperCase()} · #{card.collectorNumber}
        </span>
        <span style={{ fontSize: textOnlyTitleSize(name), color: INK, fontWeight: 700, lineHeight: 1.1 }}>{name}</span>
        {/* set 名明寫 400：唔好靠 satori 嘅默認 —— 我哋只 register 400/600/700，唔明寫就靠彩數 */}
        <span style={{ fontSize: 30, color: MUTED, lineHeight: 1.3, fontWeight: 400 }}>{clampSetName(card.setName.en)}</span>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 26 }}>
        <div style={{ display: "flex", gap: 72, borderTop: `2px solid ${LINE}`, paddingTop: 30 }}>
          <Stat label="MARKET CAP" value={usd(card.marketCap.value)} />
          <Stat label="PSA 10 PRICE" value={usd(card.pricePsa10.value)} />
          <Stat label="PSA 10 POP" value={integer(card.populationPsa10.value)} />
        </div>
        <img src={logoSrc} alt="CardZ Marketcap" width={280} height={121} />
      </div>
    </div>
  );
}

/* v2：左卡圖右數據（ogpedia 參考版面）。 */
function ArtLayout({ card, art, logoSrc }: { card: MarketCardView; art: CardArt; logoSrc: string }) {
  const name = clampTitle(card.officialName ?? card.setName.en);
  return (
    <div style={{ display: "flex", width: "100%", height: "100%", background: PAPER }}>
      <div
        style={{
          display: "flex",
          width: ART_PANEL_WIDTH,
          height: "100%",
          background: ART_GROUND,
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        {/* 透明角唔補底色：深地台襯住原生 RGBA 邊就係想要嘅效果。 */}
        <img src={art.src} alt={card.image.alt ?? name} width={art.width} height={art.height} />
      </div>
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          flex: 1,
          /* 上下 44：最壞情況（3 行 34px 標題 + 一行 set 名）量到 ink 由 top 44
             去到 bottom 589，即上下邊距 44 / 41，齊頭。set 名唔 clamp 就會變兩行、
             底邊距跌到 10px（量過）。再厚啲個 wordmark 就會撞穿底邊（52px 爆 39px）。 */
          padding: "44px 56px",
          justifyContent: "space-between",
        }}
      >
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
            {card.marketRank >= 1 ? (
              <span
                style={{
                  display: "flex",
                  background: ACCENT,
                  color: PAPER,
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
            <span style={{ fontSize: 22, color: MUTED, letterSpacing: 3, fontWeight: 600 }}>
              {card.tcg.toUpperCase()} · #{card.collectorNumber}
            </span>
          </div>
          <span style={{ fontSize: titleSize(name), color: INK, fontWeight: 700, lineHeight: 1.12 }}>{name}</span>
          <span style={{ fontSize: 24, color: MUTED, lineHeight: 1.3, fontWeight: 400 }}>{clampSetName(card.setName.en)}</span>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
          <div style={{ display: "flex", flexDirection: "column", gap: 18, borderTop: `2px solid ${LINE}`, paddingTop: 22 }}>
            <Stat label="MARKET CAP" value={usd(card.marketCap.value)} valueSize={48} />
            <div style={{ display: "flex", gap: 56 }}>
              <Stat label="PSA 10 PRICE" value={usd(card.pricePsa10.value)} valueSize={34} />
              <Stat label="PSA 10 POP" value={integer(card.populationPsa10.value)} valueSize={34} />
            </div>
          </div>
          {/* 200×86 = 2.326:1，同 SVG viewBox 969.29/419.45 = 2.311:1 差 0.6%（純文字版係 280×121）。
              呢兩個數唔可以照抄新 SVG 嘅 intrinsic size —— 一定要量返出圖：同一張卡同一支 dev
              server A/B，PNG wordmark 出 ink bbox 196×83 @(526,502)、SVG 出 198×84 @(525,501)，
              底邊兩邊都係 y=584（+2/+1 px 純粹係 vector 抗鋸齒比 PNG 自己嗰條邊多留一格淡墨）。
              即係冇縮水、亦冇撞底邊。剪 viewBox 之前量過係 243×103 vs 276×117（細 12%）——
              所以 viewBox 留白同 PNG 唔一樣嗰陣，呢兩個 declared 數就會靜靜出錯圖。 */}
          <img src={logoSrc} alt="CardZ Marketcap" width={200} height={86} />
        </div>
      </div>
    </div>
  );
}

export async function GET(_request: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
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
   *    所以 scripts/test-fe-brand-logo-svg.mjs 嗰條 `width == viewBox[2]` 係為 share-image
   *    而守，唔係為呢度；OG 呢邊真正要守嘅係上面 `;base64,` 同下面個 skin 檔名。
   * 兩路 `existsSync` 同上面 OG font 嗰段一樣：dev 由 repo root 行、standalone build
   * `process.cwd()` 已經係 `apps/web`。搵唔到就照舊回 500 —— 冇 wordmark 嘅 OG 圖唔算出到街。
   */
  const { existsSync } = await import("node:fs");
  const logoFile = [
    resolve(process.cwd(), "public/brand/logo-cardz-marketcap.svg"),
    resolve(process.cwd(), "apps/web/public/brand/logo-cardz-marketcap.svg"),
  ].find((path) => existsSync(path));
  if (!logoFile) return new Response("Brand mark missing", { status: 500 });
  const logoSrc = `data:image/svg+xml;base64,${(await readFile(logoFile)).toString("base64")}`;

  /* fail-open：卡圖任何一步炸（asset 唔喺度、sharp 載唔到、解碼失敗）都退返
     純文字版，唔准變 500 —— OG 端點死咗等於社交分享冇圖，比冇卡圖仲差。 */
  let art: CardArt | null = null;
  try {
    art = await loadCardArt(card);
  } catch (error) {
    noteArtFailure(id, error);
    art = null;
  }

  const fonts = await loadOgFonts();

  return new ImageResponse(
    art ? <ArtLayout card={card} art={art} logoSrc={logoSrc} /> : <TextOnlyLayout card={card} logoSrc={logoSrc} />,
    {
      ...size,
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
         * fail-open 冇 status code 分別（兩邊都係 200 image/png 1200×630），純
         * 文字版本身又有 91KB，size floor 都分唔到。所以每個 response 自己講返
         * 行咗邊條路：`curl -sI .../api/og/card/<id> | grep x-og-art` → 1 = 有卡
         * 圖，0 = 退咗做純文字版（多數即係 standalone 冇 ship sharp）。
         * 上面 noteArtFailure 個 log 一個 process 只嗌一次，靠佢驗證唔到。
         */
        "x-og-art": art ? "1" : "0",
      },
    },
  );
}
