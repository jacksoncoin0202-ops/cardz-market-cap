import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
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
import { FORMAT_SIZES, readShareFormat } from "@/lib/share-destinations";
import {
  readShareLang,
  SHARE_FONT_FAMILY,
  SHARE_LANG_FONTS,
  shareCopy,
  type ShareLang,
} from "@/lib/share-copy";
import { marketWindows, type MarketCardView, type MarketWindow } from "@/lib/types";

/**
 * Heatmap download API. Auto-update / promo GET this URL after live generation
 * matches. Do not screenshot :3900.
 *
 * Query: period, show, scope (all|pokemon|one-piece), format (post|status|wide|
 * landscape|portrait), theme, updown (green-up|red-up), lang (en|zh-TW|zh-CN).
 */
export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const maxDuration = 60;
export const contentType = "image/jpeg";

const CARD_ASPECT = 0.714;
const CARD_PCT = 0.62;
const JPEG_QUALITY = 92;
const GREEN = "#17b576";
const RED = "#dc567c";
const NEUTRAL = "rgba(138, 133, 120, 0.3)";

const THEMES: Record<HeatmapOgTheme, { paper: string; ink: string; muted: string; logo: string }> = {
  dark: { paper: "#0D0D0F", ink: "#F1F1EE", muted: "#A0A09B", logo: "brand/logo-cardz-marketcap-dark.svg" },
  light: { paper: "#FAFAF7", ink: "#191917", muted: "#555550", logo: "brand/logo-cardz-marketcap.svg" },
};

const BOARD_LABEL: Record<ShareLang, Record<HeatmapOgScope, string>> = {
  en: { all: "TCG", pokemon: "Pokémon", "one-piece": "One Piece" },
  "zh-TW": { all: "TCG", pokemon: "寶可夢", "one-piece": "海賊王" },
  "zh-CN": { all: "TCG", pokemon: "宝可梦", "one-piece": "海贼王" },
};

const LEGEND: Record<ShareLang, { up: string; down: string; pending: string; intensity: string }> = {
  en: { up: "Up", down: "Down", pending: "Data pending", intensity: "Deeper shade = bigger move" },
  "zh-TW": { up: "升", down: "跌", pending: "資料累積中", intensity: "顏色愈深＝變幅愈大" },
  "zh-CN": { up: "涨", down: "跌", pending: "数据累积中", intensity: "颜色越深＝变幅越大" },
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

function hexToRgb(hex: string): [number, number, number] {
  const h = hex.replace("#", "");
  return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
}

function tileFill(pct: number | null, up: string, down: string): string {
  if (pct === null || pct === 0) return NEUTRAL;
  const mag = Math.min(Math.abs(pct) / 5, 1);
  const t = mag ** 4;
  const alpha = 0.78 + t * 0.22;
  const [r, g, b] = hexToRgb(pct > 0 ? up : down);
  return `rgba(${r}, ${g}, ${b}, ${alpha.toFixed(3)})`;
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

async function loadCardArt(card: MarketCardView, maxEdge: number): Promise<string | null> {
  if (card.image.kind !== "raw_front") return null;
  const source = card.image.variants?.["200"] ?? card.image.variants?.["600"] ?? card.image.url;
  const asset = source.split("/").pop();
  if (!asset) return null;
  const node = await loadNodeMarketAsset(asset);
  if (!node) return null;
  const { default: sharp } = await import("sharp");
  const { data } = await sharp(Buffer.from(node.body))
    .resize({ width: maxEdge, height: maxEdge, fit: "inside", withoutEnlargement: true, kernel: "lanczos3" })
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
  const copy = shareCopy(lang);
  const spec = FORMAT_SIZES[format];
  const skin = THEMES[theme];
  const up = updown === "red-up" ? RED : GREEN;
  const down = updown === "red-up" ? GREEN : RED;
  const legend = LEGEND[lang];

  const snapshot = await loadMarketSnapshot();
  const scoped = scopeSnapshot(snapshot, scope, { pageSize: 100 });
  const cards = scoped.top100.slice(0, show);
  if (cards.length === 0) return new Response("No cards", { status: 404 });

  const pad = 36;
  const headerH = 56;
  const legendH = 28;
  const boardW = spec.width - pad * 2;
  const boardH = spec.height - pad * 2 - headerH - 24 - legendH;
  const items = cards.map((card) => ({
    card,
    rank: card.viewRank,
    value: Math.max(1, card.marketCap.value ?? 1),
  }));
  const tiles = heatmapTreemapLayout(items, boardW, boardH);
  const arts = await Promise.all(
    tiles.map(async (tile) => {
      const { cardW } = cardBox(Math.max(1, tile.width - 3), Math.max(1, tile.height - 3));
      return loadCardArt(tile.item.card, Math.max(48, Math.ceil(cardW)));
    }),
  );

  const { existsSync } = await import("node:fs");
  const logoFile = [
    resolve(process.cwd(), "public", skin.logo),
    resolve(process.cwd(), "apps/web/public", skin.logo),
  ].find((path) => existsSync(path));
  if (!logoFile) return new Response("Brand mark missing", { status: 500 });
  const logoSrc = `data:image/svg+xml;base64,${(await readFile(logoFile)).toString("base64")}`;
  const title = `${BOARD_LABEL[lang][scope]} Top ${cards.length} · ${period.toUpperCase()}`;
  const dateText = copy.shortDate(new Date(snapshot.effectiveAt || snapshot.generatedAt));
  const fonts = await loadOgFonts(lang);
  const filename = heatmapOgFilename({ period, show: cards.length, scope, format, theme, updown, lang });

  const image = new ImageResponse(
    (
      <div
        style={{
          width: spec.width,
          height: spec.height,
          display: "flex",
          flexDirection: "column",
          background: skin.paper,
          color: skin.ink,
          padding: pad,
          fontFamily: SHARE_FONT_FAMILY[lang],
        }}
      >
        <div style={{ display: "flex", alignItems: "center", height: headerH, justifyContent: "space-between" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
            <img src={logoSrc} alt="CardZ Marketcap" width={148} height={64} />
            <div style={{ fontSize: 26, fontWeight: 700 }}>{title}</div>
          </div>
          <div style={{ fontSize: 16, color: skin.muted }}>{dateText}</div>
        </div>
        <div style={{ display: "flex", position: "relative", width: boardW, height: boardH, marginTop: 12 }}>
          {tiles.map((tile, i) => {
            const pct = changePct(tile.item.card, period);
            const { cardW, cardH } = cardBox(Math.max(1, tile.width - 3), Math.max(1, tile.height - 3));
            const move = formatMove(pct);
            const art = arts[i];
            return (
              <div
                key={tile.item.card.id}
                style={{
                  position: "absolute",
                  left: tile.x,
                  top: tile.y,
                  width: tile.width,
                  height: tile.height,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  background: tileFill(pct, up, down),
                  borderRadius: 4,
                }}
              >
                {art ? <img src={art} alt="" width={Math.round(cardW)} height={Math.round(cardH)} /> : null}
                {move ? (
                  <div
                    style={{
                      position: "absolute",
                      top: 4,
                      right: 4,
                      fontSize: 14,
                      fontWeight: 700,
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
        <div style={{ display: "flex", marginTop: 14, fontSize: 14, fontWeight: 600, color: skin.muted, gap: 20 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <div style={{ width: 12, height: 12, background: up, borderRadius: 2 }} />
            {legend.up}
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <div style={{ width: 12, height: 12, background: down, borderRadius: 2 }} />
            {legend.down}
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <div style={{ width: 12, height: 12, background: NEUTRAL, borderRadius: 2 }} />
            {legend.pending}
          </div>
          <div style={{ marginLeft: "auto" }}>{legend.intensity}</div>
        </div>
      </div>
    ),
    { width: spec.width, height: spec.height, ...(fonts ? { fonts } : {}) },
  );

  const png = Buffer.from(await image.arrayBuffer());
  const headers = new Headers(image.headers);
  headers.set("Cache-Control", "public, max-age=300, s-maxage=300, stale-while-revalidate=86400");
  headers.set("Content-Disposition", `inline; filename="${filename}.jpg"`);
  headers.set("x-og-generation", snapshot.generation);
  headers.set("x-og-period", period);
  headers.set("x-og-show", String(cards.length));
  headers.set("x-og-scope", scope);
  headers.set("x-og-format", format);
  headers.set("x-og-theme", theme);
  headers.set("x-og-updown", updown);
  headers.set("x-og-lang", lang);
  const respond = (body: Buffer, mime: string) => {
    headers.set("Content-Type", mime);
    headers.set("Content-Length", String(body.length));
    headers.set("x-og-bytes", String(body.length));
    return new Response(new Uint8Array(body), { headers });
  };
  try {
    const { default: sharp } = await import("sharp");
    const jpeg = await sharp(png).jpeg({ quality: JPEG_QUALITY, chromaSubsampling: "4:4:4", mozjpeg: true }).toBuffer();
    return respond(jpeg, "image/jpeg");
  } catch {
    headers.set("Content-Disposition", `inline; filename="${filename}.png"`);
    return respond(png, "image/png");
  }
}
