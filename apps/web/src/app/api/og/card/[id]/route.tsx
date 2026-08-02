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
 * 要真係放到卡圖，見 docs/DATA_GAPS.md「OG image card art」。
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

/*
 * 呢張圖淨係出英文（用 card.name.en / card.setName.en），所以語言標籤要自己一份英文表，
 * 唔可以用 i18n 嘅 locale copy。JA / EN 孖卡就係靠呢條同下面 set code 分身 ——
 * 冇佢哋兩張卡出嘅 OG PNG 係 byte-identical。
 */
const PRINT_LANGUAGE_LABEL: Record<string, string> = {
  en: "ENGLISH PRINT",
  ja: "JAPANESE PRINT",
  ko: "KOREAN PRINT",
  zhCN: "SIMPLIFIED CHINESE PRINT",
  zhTW: "TRADITIONAL CHINESE PRINT",
};

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <span style={{ fontSize: 22, color: MUTED, letterSpacing: 1.6 }}>{label}</span>
      <span style={{ fontSize: 52, color: INK, fontWeight: 700 }}>{value}</span>
    </div>
  );
}

export async function GET(request: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const origin = new URL(request.url).origin;
  const snapshot = await loadMarketSnapshot().catch(() => null);
  const card = snapshot
    ? [...snapshot.top100, ...snapshot.watchlist].find((candidate) => candidate.id === id) ?? null
    : null;

  // 揾唔到卡都唔准留白 —— 出返品牌預設圖。
  if (!card) return fetch(new URL("/brand/og-light.png", origin));

  const printLanguage = card.cardLanguage ? PRINT_LANGUAGE_LABEL[card.cardLanguage] ?? null : null;
  // set code 淨係喺同 set 名唔同嗰陣先補上，唔好出「OP-09 · OP09」咁嘅重複。
  const setCode = card.printingIdentity?.setCode ?? null;
  const setLine = setCode && setCode !== card.setName.en ? `${card.setName.en} · ${setCode}` : card.setName.en;

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
            {card.tcg.toUpperCase()} · #{card.collectorNumber}{printLanguage ? ` · ${printLanguage}` : ""}
          </span>
          <span style={{ fontSize: 78, color: INK, fontWeight: 700, lineHeight: 1.1 }}>{card.name.en}</span>
          <span style={{ fontSize: 30, color: MUTED, lineHeight: 1.3 }}>{setLine}</span>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 26 }}>
          <div style={{ display: "flex", gap: 72, borderTop: `2px solid ${LINE}`, paddingTop: 30 }}>
            <Stat label="MARKET CAP" value={usd(card.marketCap.value)} />
            <Stat label="PSA 10 PRICE" value={usd(card.pricePsa10.value)} />
            <Stat label="PSA 10 POP" value={integer(card.populationPsa10.value)} />
          </div>
          <span style={{ fontSize: 25, color: MUTED, letterSpacing: 3 }}>CARDZ MARKETCAP</span>
        </div>
      </div>
    ),
    size,
  );
}
