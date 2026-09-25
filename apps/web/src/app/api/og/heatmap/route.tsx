import { createHash } from "node:crypto";
import { existsSync } from "node:fs";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { ImageResponse } from "next/og";
import {
  HEATMAP_OG_DEFAULT_PERIOD,
  HEATMAP_OG_DEFAULT_SHOW,
  heatmapOgFilename,
  type HeatmapOgScope,
  type HeatmapOgTheme,
  type HeatmapOgUpDown,
} from "@/lib/heatmap-og";
import { heatmapTreemapLayout } from "@/lib/ranked-strip-layout";
import { loadMarketSnapshot, loadNodeMarketAsset, scopeSnapshot } from "@/lib/server-snapshot";
import { FORMAT_SIZES, readShareFormat, type ShareFormat } from "@/lib/share-destinations";
import { RESOLUTION_SCALE, readShareResolution } from "@/lib/share-resolution";
import { nowStamp, readStampAt, readStampMode, readTimeZone, stampCacheKey } from "@/lib/share-stamp";
import {
  readShareLang,
  SHARE_FONT_FAMILY,
  SHARE_LANG_FONTS,
  shareCopy,
  type ShareLang,
} from "@/lib/share-copy";
import { DEFAULT_TILE, tileStyle, windowTileParams } from "@/lib/tile-style";
import { marketWindows, type MarketCardView, type MarketWindow } from "@/lib/types";

/**
 * Heatmap download API. Auto-update / promo GET this URL after live generation
 * matches. Do not screenshot :3900.
 *
 * Query: period, show, scope (all|pokemon|one-piece), format (post|square|status|
 * wide|portrait 3:4|widescreen 16:9, plus the aliases in lib/share-destinations.ts:
 * 1x1, 3x4, ig-portrait, grid, 16x9, hd, youtube, landscape, tall, ...), theme,
 * updown (green-up|red-up), lang (en|zh-TW|zh-CN), res (1080p|4k), stamp
 * (data|now), tz (IANA, e.g. Asia/Tokyo).
 *
 * ⚠️ `?format=portrait` is still the 4:5 `post` alias, NOT the 3:4 board — the
 * out-of-repo auto-update chain writes it. Ask for 3:4 by name: `?format=3x4`.
 *
 * res=4k is a real 2x render (post -> 2160x2700), not an upscale. It costs
 * ~11x the wall clock of 1080p (measured 2026-08-21: 4.3-5.3s vs 53.7-56.8s on
 * 40 tiles), so it is meant for scripts/heatmap-download.mjs, not for a browser
 * sitting behind a gateway timeout. See lib/share-resolution.ts for the numbers.
 *
 * stamp=now prints the moment the image is rendered, in tz, instead of the
 * snapshot date. Default stays `data` so og:image unfurls and the HERMES cron
 * chain keep getting exactly what they got before.
 */
export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const maxDuration = 60;
export const contentType = "image/jpeg";

const CARD_ASPECT = 0.714;
const CARD_PCT = 0.62;
const TILE_GAP = 3;
const JPEG_QUALITY = 90;
const GREEN = "#17b576";
const RED = "#dc567c";
const NEUTRAL = "rgba(138, 133, 120, 0.3)";
const LABEL = { inset: 4, padX: 3, padY: 1, radius: 4, minFont: 8, maxFont: 14 };

/*
 * 「外框」（logo／標題／個戳／legend）相對畫布嘅大細。
 *
 * 成塊板由頭到尾**冇按 format 分過支** —— treemap 自己食晒 boardW×boardH，所以加
 * 一個新比例本身唔使改 layout：3:4 同 4:5 一樣闊（1080），欄數一樣，格仔高啲；
 * 16:9 闊咗就自然多幾欄。真正會出事嘅淨係外框：上面全部數字（pad 24、header 32、
 * 標題 26、legend 12）係喺一個 **1080 闊**嘅畫布度度出嚟嘅，而 `widescreen` 1920 闊
 * —— 照原數畫出嚟，個 logo 同標題只佔畫面 56%（1080/1920）嘅相對高度，望落就係
 * 「一塊大板上面貼咗行細字」。粒字冇縮過，但相對嚟講細咗，同 2026-08-21 owner 報
 * 「4K 啲 percentage 細到睇唔到」係一模一樣嘅病（見 `labelFontSize`）。
 *
 * ⚠️ 其餘五個**釘死 1**，唔准改成一條 `width / 1080` 通式：`wide` 1200 闊會變成
 * ×1.11，即係一粒「加新比例」嘅改動會靜靜咁改晒已經出咗街嘅 og:image。要改就要
 * 有人明寫、明驗。
 *
 * ⚠️ 呢個**唔係** `scale`（清晰度）。`scale` 係像素密度（4K = 2×），呢個係版面
 * 比例。兩個要相乘，唔可以二揀一。
 */
const CHROME_BASE_WIDTH = 1080;
const CHROME_SCALE: Record<ShareFormat, number> = {
  wide: 1,
  square: 1,
  post: 1,
  status: 1,
  portrait: 1,
  widescreen: FORMAT_SIZES.widescreen.width / CHROME_BASE_WIDTH,
};

const THEMES: Record<HeatmapOgTheme, { paper: string; ink: string; muted: string; logo: string }> = {
  dark: { paper: "#0D0D0F", ink: "#F1F1EE", muted: "#A0A09B", logo: "brand/logo-cardz-marketcap-dark.svg" },
  light: { paper: "#FAFAF7", ink: "#191917", muted: "#555550", logo: "brand/logo-cardz-marketcap.svg" },
};

const BOARD_LABEL: Record<ShareLang, Record<HeatmapOgScope, string>> = {
  en: { all: "TCG", pokemon: "Pokémon", "one-piece": "One Piece" },
  "zh-TW": { all: "TCG", pokemon: "寶可夢", "one-piece": "海賊王" },
  "zh-CN": { all: "TCG", pokemon: "宝可梦", "one-piece": "海贼王" },
};

/* 灰格 = 持平（印出嚟係 0.0%）或者冇數，分享圖唔加斜紋（satori 未必食 repeating gradient，
   呢條 route 唔准 500），所以一個色塊講晒兩樣。網站個 legend 就分開兩格。 */
const LEGEND: Record<ShareLang, { up: string; down: string; pending: string; intensity: string }> = {
  en: { up: "Up", down: "Down", pending: "Flat / data pending", intensity: "Deeper shade = bigger move" },
  "zh-TW": { up: "升", down: "跌", pending: "持平／資料累積中", intensity: "顏色愈深＝變幅愈大" },
  "zh-CN": { up: "涨", down: "跌", pending: "持平／数据累积中", intensity: "颜色越深＝变幅越大" },
};

type OgFont = { name: string; data: Buffer; weight: 400 | 600 | 700; style: "normal" };
const ogFontsByLang = new Map<ShareLang, Promise<OgFont[] | undefined>>();

function loadOgFonts(lang: ShareLang) {
  let pending = ogFontsByLang.get(lang);
  if (!pending) {
    pending = (async () => {
      const { existsSync } = await import("node:fs");
      const files: [string, 400 | 600 | 700, string][] = [
        ["Inter-Regular.ttf", 400, "Inter"],
        ["Inter-SemiBold.ttf", 600, "Inter"],
        ["Inter-Bold.ttf", 700, "Inter"],
        ...(SHARE_LANG_FONTS[lang] ?? []).map(
          ([file, weight, family]) => [file, weight, family] as [string, 400 | 700, string],
        ),
      ];
      try {
        return await Promise.all(
          files.map(async ([file, weight, name]) => {
            const path = [
              resolve(process.cwd(), "public/fonts/og", file),
              resolve(process.cwd(), "apps/web/public/fonts/og", file),
            ].find((p) => existsSync(p));
            if (!path) throw new Error(`missing ${file}`);
            return { name, data: await readFile(path), weight, style: "normal" as const };
          }),
        );
      } catch (error) {
        console.warn(
          `[og/heatmap] font load failed (lang=${lang}): ${error instanceof Error ? error.message : "unknown"}`,
        );
        return undefined;
      }
    })();
    ogFontsByLang.set(lang, pending);
  }
  return pending;
}

function readPeriod(raw: string | null): MarketWindow {
  return marketWindows.includes(raw as MarketWindow) ? (raw as MarketWindow) : HEATMAP_OG_DEFAULT_PERIOD;
}

function readShow(raw: string | null): number {
  const n = Number.parseInt(raw ?? "", 10);
  if (!Number.isFinite(n)) return HEATMAP_OG_DEFAULT_SHOW;
  return Math.min(100, Math.max(10, n));
}

function readScope(raw: string | null): HeatmapOgScope {
  return raw === "pokemon" || raw === "one-piece" ? raw : "all";
}

function readTheme(raw: string | null): HeatmapOgTheme {
  return raw === "light" ? "light" : "dark";
}

function readUpDown(raw: string | null): HeatmapOgUpDown {
  return raw === "red-up" ? "red-up" : "green-up";
}

function changePct(card: MarketCardView, period: MarketWindow): number | null {
  const metric = card.windows?.[period]?.changePct;
  if (!metric || metric.value === null || !Number.isFinite(metric.value)) return null;
  if (metric.status !== "ready" && metric.status !== "stale") return null;
  return metric.value;
}

function cardBox(w: number, h: number): { cardW: number; cardH: number } {
  const longSide = Math.max(w, h);
  let cardH = longSide * CARD_PCT;
  let cardW = cardH * CARD_ASPECT;
  if (cardW > w * 0.88) {
    cardW = w * 0.88;
    cardH = cardW / CARD_ASPECT;
  }
  if (cardH > h * 0.86) {
    cardH = h * 0.86;
    cardW = cardH * CARD_ASPECT;
  }
  return { cardW, cardH };
}

function formatMove(pct: number | null): string | null {
  if (pct === null || pct === 0) return null;
  const sign = pct > 0 ? "+" : "";
  return `${sign}${pct.toFixed(1)}%`;
}

/*
 * 出圖倍數 = 用戶揀嘅清晰度，唔再寫死。
 *
 * 舊註釋（2026-08 之前）寫住「2× 本機 ~40s，live gateway 504／timeout → CLI 自動化斷，
 * 所以一律 1×」。件事今日仲係啱 —— 唔同嘅係而家個代價**擺咗上枱**：預設仍然係 1×
 * （`DEFAULT_SHARE_RESOLUTION`），想要 2× 就要明寫 `?res=4k`，而網站個掣同 CLI 都會
 * 講返要等幾耐。慢係慢，但唔再係「想要都冇得要」。
 */
function outputScale(res: ReturnType<typeof readShareResolution>): number {
  return RESOLUTION_SCALE[res];
}

function cachePath(generation: string, parts: string[]): string {
  const id = createHash("sha256").update(parts.join("|")).digest("hex").slice(0, 24);
  return join(tmpdir(), "cardz-og-heatmap", generation, `${id}.jpg`);
}

/*
 * 升跌 % 個字。`w`/`h` 入嚟已經係**放大咗**嘅 tile 尺寸，所以 `shortSide * 0.12`
 * 自己已經係 2×；但 `minFont`/`maxFont` 係 1× 嘅數，唔乘返 `scale` 就會喺 4K
 * 度夾死喺 14px —— 畫布大咗一倍、粒字冇變，睇落就係「4K 啲 % 細咗一半」。
 * （2026-08-21 owner 報：4K 啲 percentage 細到睇唔到。）
 *
 * `scale` **冇 default 值**，係要逼將來新嘅 call site 明寫，唔寫 tsc 就紅。
 */
function labelFontSize(w: number, h: number, scale: number): number {
  const shortSide = Math.min(w, h);
  return Math.max(LABEL.minFont * scale, Math.min(LABEL.maxFont * scale, Math.round(shortSide * 0.12)));
}

async function loadCardArt(card: MarketCardView, maxEdge: number): Promise<string | null> {
  if (card.image.kind !== "raw_front") return null;
  const source = card.image.variants?.["600"] ?? card.image.variants?.["200"] ?? card.image.url;
  const asset = source.split("/").pop();
  if (!asset) return null;
  const node = await loadNodeMarketAsset(asset);
  if (!node) return null;
  const { default: sharp } = await import("sharp");
  const edge = Math.max(48, Math.min(600, Math.ceil(maxEdge)));
  const { data } = await sharp(Buffer.from(node.body))
    .resize({ width: edge, height: edge, fit: "inside", withoutEnlargement: true, kernel: "lanczos3" })
    .png({ compressionLevel: 6 })
    .toBuffer({ resolveWithObject: true });
  return `data:image/png;base64,${data.toString("base64")}`;
}

export async function GET(request: Request): Promise<Response> {
  const query = new URL(request.url).searchParams;
  const period = readPeriod(query.get("period"));
  const show = readShow(query.get("show"));
  const scope = readScope(query.get("scope"));
  const format = readShareFormat(query.get("format") ?? "post");
  const theme = readTheme(query.get("theme"));
  const updown = readUpDown(query.get("updown"));
  const lang = readShareLang(query.get("lang"));
  const res = readShareResolution(query.get("res"));
  const stampMode = readStampMode(query.get("stamp"));
  const tz = readTimeZone(query.get("tz"));
  const stampAt = readStampAt(query.get("at"));
  const copy = shareCopy(lang);
  const spec = FORMAT_SIZES[format];
  const scale = outputScale(res);
  const width = Math.round(spec.width * scale);
  const height = Math.round(spec.height * scale);
  const skin = THEMES[theme];
  const up = updown === "red-up" ? RED : GREEN;
  const down = updown === "red-up" ? GREEN : RED;
  const legend = LEGEND[lang];

  const snapshot = await loadMarketSnapshot();
  /*
   * 右上角個戳。`now` 要喺 cache key 之前算好 —— 個戳一入圖，就係圖嘅一部分，
   * 唔入 key 就係「第二個人攞到第一個人嗰一刻嘅鐘」。精度同顯示文字同源（分鐘），
   * 所以同一分鐘之內（hover warm + 撳掣）仍然共用一張圖。
   */
  const dateText = stampMode === "now"
    ? nowStamp(stampAt.date, tz, lang)
    : copy.shortDate(new Date(snapshot.effectiveAt || snapshot.generatedAt));
  const cacheFile = cachePath(snapshot.generation, [
    snapshot.generation, period, String(show), scope, format, theme, updown, lang,
    res, stampCacheKey(stampMode, dateText),
  ]);
  const filename = heatmapOgFilename({ period, show, scope, format, theme, updown, lang, res });
  const ogHeaders = () => {
    const headers = new Headers();
    headers.set("Cache-Control", "public, max-age=300, s-maxage=300, stale-while-revalidate=86400");
    headers.set("Content-Disposition", `inline; filename="${filename}.jpg"`);
    headers.set("x-og-generation", snapshot.generation);
    headers.set("x-og-period", period);
    headers.set("x-og-show", String(show));
    headers.set("x-og-scope", scope);
    headers.set("x-og-format", format);
    headers.set("x-og-theme", theme);
    headers.set("x-og-updown", updown);
    headers.set("x-og-lang", lang);
    headers.set("x-og-width", String(width));
    headers.set("x-og-height", String(height));
    headers.set("x-og-res", res);
    headers.set("x-og-stamp-mode", stampMode);
    /* ⚠️ 個戳中文版有「年月日」，HTTP header 只食 latin-1 —— 直接 set 會喺 undici
       度掟 `Invalid character in header content`，即係一 set 中文就成個 request 500。
       percent-encode 之後 CLI 自己 `decodeURIComponent` 返。 */
    headers.set("x-og-stamp", encodeURIComponent(dateText));
    if (stampMode === "now") {
      headers.set("x-og-tz", tz);
      /* 叫方要知自己個 `at` 收咗未 —— 收唔到就代表佢下一次 retry 會係另一條 cache
         key（永遠 miss）。`pinned` = 用咗你俾嗰一刻，`now` = 用咗 server 而家。 */
      headers.set("x-og-at", stampAt.pinned ? "pinned" : "now");
    }
    return headers;
  };
  if (existsSync(cacheFile)) {
    const jpeg = await readFile(cacheFile);
    if (jpeg.length >= 8000 && jpeg[0] === 0xff && jpeg[1] === 0xd8) {
      const headers = ogHeaders();
      headers.set("Content-Type", "image/jpeg");
      headers.set("Content-Length", String(jpeg.length));
      headers.set("x-og-bytes", String(jpeg.length));
      headers.set("x-og-cache", "hit");
      return new Response(new Uint8Array(jpeg), { headers });
    }
  }
  const scoped = scopeSnapshot(snapshot, scope, { pageSize: 100 });
  // The seated board (30d seat rule, #47), not page 1 of the list: on a short
  // per-game board page 1 continues into rank 101+. scripts/test-top100-30d-sales.mjs checks this.
  const cards = (scoped.lead100 ?? []).slice(0, show);
  if (cards.length === 0) return new Response("No cards", { status: 404 });

  /*
   * 外框 = 清晰度 × 版面比例（見 `CHROME_SCALE`）。**唔准**用返 `scale` ——
   * 1920 闊嘅畫布配 1080 闊嘅外框數字，個標題同 legend 相對嚟講細一半。
   * 格仔嗰邊（`gap` / `labelFontSize` / 圓角 / 陰影）繼續行 `scale`：嗰啲跟格仔
   * 本身大細走，treemap 已經幫佢哋按畫布分配好。
   */
  const chrome = scale * CHROME_SCALE[format];
  const pad = Math.round(24 * chrome);
  const headerH = Math.round(32 * chrome);
  const legendH = Math.round(16 * chrome);
  const boardGap = Math.round(20 * chrome);
  const boardW = width - pad * 2;
  const boardH = height - pad * 2 - headerH - boardGap * 2 - legendH;
  const gap = TILE_GAP * scale;
  const items = cards.map((card) => ({
    card,
    rank: card.viewRank,
    value: Math.max(1, card.marketCap.value ?? 1),
  }));
  const tiles = heatmapTreemapLayout(items, boardW, boardH);
  const arts = await Promise.all(
    tiles.map(async (tile) => {
      const tw = Math.max(1, tile.width - gap);
      const th = Math.max(1, tile.height - gap);
      const { cardW } = cardBox(tw, th);
      return loadCardArt(tile.item.card, Math.max(48, Math.ceil(cardW)));
    }),
  );

  const logoFile = [
    resolve(process.cwd(), "public", skin.logo),
    resolve(process.cwd(), "apps/web/public", skin.logo),
  ].find((path) => existsSync(path));
  if (!logoFile) return new Response("Brand mark missing", { status: 500 });
  const logoSrc = `data:image/svg+xml;base64,${(await readFile(logoFile)).toString("base64")}`;
  const title = `${BOARD_LABEL[lang][scope]} Top ${cards.length} · ${period.toUpperCase()}`;
  const fonts = await loadOgFonts(lang);

  const logoH = Math.round(32 * chrome);
  const logoW = Math.round(logoH * (969 / 419));
  const titleSize = Math.round(26 * chrome);
  const stampSize = Math.round(12 * chrome);
  const legendSize = Math.round(12 * chrome);
  const swatch = Math.round(12 * chrome);

  const image = new ImageResponse(
    (
      <div
        style={{
          width,
          height,
          display: "flex",
          flexDirection: "column",
          background: skin.paper,
          color: skin.ink,
          padding: pad,
          fontFamily: SHARE_FONT_FAMILY[lang],
        }}
      >
        <div style={{ display: "flex", alignItems: "center", height: headerH, justifyContent: "space-between" }}>
          <div style={{ display: "flex", alignItems: "center", gap: Math.round(16 * chrome) }}>
            <img src={logoSrc} alt="CardZ Marketcap" width={logoW} height={logoH} />
            <div style={{ fontSize: titleSize, fontWeight: 700 }}>{title}</div>
          </div>
          <div style={{ fontSize: stampSize, color: skin.muted }}>{dateText}</div>
        </div>
        <div
          style={{
            display: "flex",
            position: "relative",
            width: boardW,
            height: boardH,
            marginTop: boardGap,
          }}
        >
          {tiles.map((tile, i) => {
            const pct = changePct(tile.item.card, period);
            const tw = Math.max(1, tile.width - gap);
            const th = Math.max(1, tile.height - gap);
            const { cardW, cardH } = cardBox(tw, th);
            /* 色階、「印出嚟 0.0% 就當持平」、label 底板，全部同網站行同一個 tileStyle ——
               以前呢度自己抄一份，0.04% 會印「+0.0%」綠格，網站就係灰格。 */
            const st = tileStyle(pct, tw, th, { up, down, neutral: NEUTRAL }, windowTileParams(DEFAULT_TILE, period));
            const move = st.direction === "neutral" ? null : formatMove(pct);
            const art = arts[i];
            const fontPx = Math.round(labelFontSize(tw, th, scale));
            const plate = st.plate;
            return (
              <div
                key={tile.item.card.id}
                style={{
                  position: "absolute",
                  left: tile.x + gap / 2,
                  top: tile.y + gap / 2,
                  width: tw,
                  height: th,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  overflow: "hidden",
                  background: st.bg,
                  borderRadius: 4 * scale,
                }}
              >
                {art ? (
                  <img
                    src={art}
                    alt=""
                    width={Math.round(cardW)}
                    height={Math.round(cardH)}
                    style={{ boxShadow: `0 ${2 * scale}px ${6 * scale}px rgba(0,0,0,0.35)` }}
                  />
                ) : null}
                {move && plate ? (
                  <div
                    style={{
                      position: "absolute",
                      top: LABEL.inset * scale,
                      right: LABEL.inset * scale,
                      display: "flex",
                      background: plate,
                      borderRadius: LABEL.radius * scale,
                      padding: `${LABEL.padY * scale}px ${LABEL.padX * scale}px`,
                      fontSize: fontPx,
                      fontWeight: 800,
                      color: "#fff",
                    }}
                  >
                    {move}
                  </div>
                ) : null}
              </div>
            );
          })}
        </div>
        <div
          style={{
            display: "flex",
            marginTop: boardGap,
            fontSize: legendSize,
            fontWeight: 600,
            color: skin.muted,
            gap: Math.round(20 * chrome),
            height: legendH,
            alignItems: "center",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: Math.round(8 * chrome) }}>
            <div style={{ width: swatch, height: swatch, background: up, borderRadius: 2 * chrome }} />
            {legend.up}
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: Math.round(8 * chrome) }}>
            <div style={{ width: swatch, height: swatch, background: down, borderRadius: 2 * chrome }} />
            {legend.down}
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: Math.round(8 * chrome) }}>
            <div style={{ width: swatch, height: swatch, background: NEUTRAL, borderRadius: 2 * chrome }} />
            {legend.pending}
          </div>
          <div style={{ marginLeft: "auto" }}>{legend.intensity}</div>
        </div>
      </div>
    ),
    { width, height, ...(fonts ? { fonts } : {}) },
  );

  const png = Buffer.from(await image.arrayBuffer());
  const headers = ogHeaders();
  headers.set("x-og-show", String(cards.length));
  headers.set("x-og-cache", "miss");
  const respond = (body: Buffer, mime: string) => {
    headers.set("Content-Type", mime);
    headers.set("Content-Length", String(body.length));
    headers.set("x-og-bytes", String(body.length));
    return new Response(new Uint8Array(body), { headers });
  };
  try {
    const { default: sharp } = await import("sharp");
    const jpeg = await sharp(png).jpeg({ quality: JPEG_QUALITY, chromaSubsampling: "4:4:4", mozjpeg: true }).toBuffer();
    try {
      await mkdir(dirname(cacheFile), { recursive: true });
      await writeFile(cacheFile, jpeg);
    } catch {
      /* cache 寫唔入唔擋出圖 */
    }
    return respond(jpeg, "image/jpeg");
  } catch {
    headers.set("Content-Disposition", `inline; filename="${filename}.png"`);
    return respond(png, "image/png");
  }
}
