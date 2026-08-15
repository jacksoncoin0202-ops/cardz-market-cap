import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { ImageResponse } from "next/og";
import { loadMarketSnapshot } from "@/lib/server-snapshot";

export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

const PAPER = "#f7f7f5";
const INK = "#171717";
const MUTED = "#6f6f6b";
const LINE = "#e6e6e2";
const ACCENT = "#b85416";

/*
 * 冇卡圖：卡圖係 content-addressed `<sha256>.webp`，而 `next/og` 底下嘅 resvg
 * 解唔到 WebP（實測：同一段 markup 餵 PNG 畫得出、餵 WebP 出 0 px，靜靜地留白）。
 * 所以呢張 OG 走純排版，用卡本身嘅數據砌，唔會出空白卡。
 * This route intentionally renders market text without loading card art.
 */

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

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <span style={{ fontSize: 22, color: MUTED, letterSpacing: 1.6 }}>{label}</span>
      <span style={{ fontSize: 52, color: INK, fontWeight: 700 }}>{value}</span>
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

  const { existsSync } = await import("node:fs");
  const logoFile = [
    resolve(process.cwd(), "public/brand/logo-cardz-marketcap.png"),
    resolve(process.cwd(), "apps/web/public/brand/logo-cardz-marketcap.png"),
  ].find((path) => existsSync(path));
  if (!logoFile) return new Response("Brand mark missing", { status: 500 });
  const logoSrc = `data:image/png;base64,${(await readFile(logoFile)).toString("base64")}`;

  return new ImageResponse(
    (
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
          <span style={{ fontSize: 24, color: ACCENT, letterSpacing: 4 }}>
            {card.tcg.toUpperCase()} · #{card.collectorNumber}
          </span>
          <span style={{ fontSize: 78, color: INK, fontWeight: 700, lineHeight: 1.1 }}>{card.officialName}</span>
          <span style={{ fontSize: 30, color: MUTED, lineHeight: 1.3 }}>{card.setName.en}</span>
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
    ),
    size,
  );
}
