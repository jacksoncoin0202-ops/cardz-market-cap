import { pointsForWindow } from "./history-window";
import type { PricePoint } from "./types";

/*
 * 分享圖入面嗰條價格走勢 —— 出**純 SVG 字串**，唔係 JSX。
 *
 * 點解唔喺 satori 度直接畫 `<svg>`：satori 對 SVG 子元素嘅支援係另一條 code path，
 * 而 `next/og` 底下嗰個 resvg 對「data URI 入面嘅完整 SVG」係全支援（repo 內已經有
 * 先例 —— wordmark 就係咁樣餵落 `<img>`，見 `api/og/card/[id]/route.tsx` 檔頭）。
 * 行返同一條已知得嘅路，出圖唔會靜靜少咗個 gradient 或者 opacity。
 *
 * ⚠️ **入面一個 `<text>` 都冇**。resvg 要自己解字體先畫到字，而呢個 route 註冊嘅
 * Inter 只餵咗 satori，唔會傳落 resvg —— 喺 SVG 入面寫字就會靜靜出唔到 / 出錯字。
 * 所以座標軸文字（日期／價格範圍）一律喺 satori 側用 `<span>` 畫，唔入 SVG。
 *
 * ⚠️ 一定要 `data:image/svg+xml;base64,`（見 route.tsx 檔頭）：`charset=utf-8,` +
 * `encodeURIComponent` 嗰種寫法行到 satori 內部個 `btoa` 就會 `InvalidCharacterError`。
 * 所以呢度連 base64 一齊出，唔留機會俾叫方寫錯。
 *
 * 顏色／幾何全部同網頁 `components/history-chart.tsx` + globals.css 嗰組對齊：
 *   price line  = `--accent`，stroke-linecap/join round（`.price-line`）
 *   price dot   = fill `--surface` + stroke `--accent`（`.price-point`）
 *   sales bar   = `#a8c5ca` / dark `#7a9aa2`，opacity .72，rx 1.5（`.sales-bar`）
 *   gridline    = `--line` 1px（`.chart-gridline`）
 * 只多咗網頁冇嘅一層 area gradient —— 網頁靠 hover tooltip 交代，張圖冇得 hover，
 * 要靠面積俾人一眼睇到升跌形狀。
 */

export interface ShareChartPalette {
  /** `--accent`：線同 area 漸變 */
  accent: string;
  /** `--line`：橫格線 */
  grid: string;
  /** `.sales-bar` fill */
  bar: string;
  /** `--surface`：尾點個心 */
  surface: string;
}

export interface ShareChartOptions {
  width: number;
  height: number;
  palette: ShareChartPalette;
  /** 條線幾粗。wide（1200×630）同 poster（1080×1350）出圖尺寸差好遠，唔可以共用一個數。 */
  lineWidth?: number;
  /** 出唔出成交金額 bar。wide 版位太扁，bar 會同條線打架，所以只喺 poster 出。 */
  bars?: boolean;
  /** 出唔出橫格線。 */
  grid?: boolean;
}

export interface ShareChart {
  /** 已經 base64 好嘅 data URI，直接餵 `<img src>` */
  src: string;
  width: number;
  height: number;
  /** 窗內最低／最高價（USD）—— 叫方用嚟喺 SVG 外面畫價格範圍字 */
  minUsd: number;
  maxUsd: number;
  /** 窗內第一／最後一個有價嘅日子（ISO）—— 叫方用嚟畫日期軸 */
  firstAt: string;
  lastAt: string;
  /** 真係畫咗幾多個價點（debug / gate 用） */
  pointCount: number;
}

function hasSalesBar(point: PricePoint): point is PricePoint & { trackedSalesValueUsd: number; trackedSalesCount: number } {
  return point.salesCoverage !== "unavailable" &&
    point.trackedSalesValueUsd !== null &&
    point.trackedSalesValueUsd > 0 &&
    point.trackedSalesCount !== null &&
    point.trackedSalesCount > 0;
}

/*
 * `windowDays` 用網頁預設嗰個窗（`types.ts` `defaultMarketWindow`，2026-08-24 起 = 30 日）：分享圖唔可以
 * 帶用戶當前揀嘅時段（張圖出咗街係俾第三者睇，佢冇揀過嘢），所以釘死一個窗，
 * 而嗰個窗要同人哋撳入去之後見到嘅預設一樣，唔係另一個數。
 */
export function buildShareChart(
  history: PricePoint[],
  windowDays: number,
  { width, height, palette, lineWidth = 3, bars = true, grid = true }: ShareChartOptions,
): ShareChart | null {
  const selected = pointsForWindow(history ?? [], windowDays);
  const priced = selected.filter((point) => point.priceUsd !== null && Number.isFinite(point.priceUsd));
  /* 少過兩點畫唔到一條線 —— 寧願成塊圖表唔出（叫方會收窄版面），都好過畫一條假線。 */
  if (priced.length < 2) return null;

  /* 左右各留條線嘅半闊 + 少少，否則首尾兩點嘅圓頭會俾 viewBox 切走。 */
  const padX = Math.max(4, lineWidth * 2);
  const padTop = Math.max(6, lineWidth * 2);
  const padBottom = Math.max(6, lineWidth * 2);
  const plotW = width - padX * 2;
  const plotH = height - padTop - padBottom;

  const values = priced.map((point) => point.priceUsd as number);
  const min = Math.min(...values);
  const max = Math.max(...values);
  /* 同網頁一樣：完全平坦嘅序列都要有個可見高度，唔可以除 0。 */
  const spread = Math.max(max - min, Math.max(1, max * 0.02));
  const yMin = Math.max(0, min - spread * 0.12);
  const yMax = max + spread * 0.12;

  const startTime = Date.parse(priced[0].at);
  const endTime = Date.parse(priced[priced.length - 1].at);
  const x = (at: string) => endTime <= startTime
    ? padX + plotW / 2
    : padX + ((Date.parse(at) - startTime) / (endTime - startTime)) * plotW;
  const y = (value: number) => padTop + ((yMax - value) / Math.max(1e-9, yMax - yMin)) * plotH;

  const linePoints = priced.map((point) => `${x(point.at).toFixed(2)},${y(point.priceUsd as number).toFixed(2)}`);
  const linePath = `M${linePoints.join(" L")}`;
  /* area 收返落 baseline（plot 底），首尾垂直落去再閂口。 */
  const baseline = (padTop + plotH).toFixed(2);
  const areaPath = `${linePath} L${x(priced[priced.length - 1].at).toFixed(2)},${baseline} L${x(priced[0].at).toFixed(2)},${baseline} Z`;

  const gridLines = grid
    ? Array.from({ length: 3 }, (_, index) => {
      const gy = (padTop + (plotH * (index + 1)) / 4).toFixed(2);
      return `<line x1="${padX}" x2="${(width - padX).toFixed(2)}" y1="${gy}" y2="${gy}" stroke="${palette.grid}" stroke-width="1" />`;
    }).join("")
    : "";

  const salesRects = bars
    ? (() => {
      const withSales = selected.filter(hasSalesBar);
      if (!withSales.length) return "";
      const maxSales = Math.max(...withSales.map((point) => point.trackedSalesValueUsd));
      /* bar 最高只食 plot 嘅 26%（網頁係 28%，呢度扁少少，唔好篤穿條價線）。 */
      const band = Math.max(2, Math.min(14, (plotW / Math.max(1, selected.length)) * 0.6));
      return withSales.map((point) => {
        const barH = Math.max(1.5, (point.trackedSalesValueUsd / maxSales) * plotH * 0.26);
        const bx = (x(point.at) - band / 2).toFixed(2);
        const by = (padTop + plotH - barH).toFixed(2);
        return `<rect x="${bx}" y="${by}" width="${band.toFixed(2)}" height="${barH.toFixed(2)}" rx="1.5" fill="${palette.bar}" opacity="0.72" />`;
      }).join("");
    })()
    : "";

  const lastX = x(priced[priced.length - 1].at).toFixed(2);
  const lastY = y(priced[priced.length - 1].priceUsd as number).toFixed(2);
  const dotR = Math.max(3.5, lineWidth * 1.6);

  const svg = [
    `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}">`,
    `<defs><linearGradient id="cardzArea" x1="0" y1="0" x2="0" y2="1">`,
    `<stop offset="0" stop-color="${palette.accent}" stop-opacity="0.26" />`,
    `<stop offset="1" stop-color="${palette.accent}" stop-opacity="0" />`,
    `</linearGradient></defs>`,
    gridLines,
    salesRects,
    `<path d="${areaPath}" fill="url(#cardzArea)" />`,
    `<path d="${linePath}" fill="none" stroke="${palette.accent}" stroke-width="${lineWidth}" stroke-linecap="round" stroke-linejoin="round" />`,
    /* 尾點：同 `.price-point` 一樣係 surface 心 + accent 邊，講「最新一日就係呢度」。 */
    `<circle cx="${lastX}" cy="${lastY}" r="${dotR.toFixed(2)}" fill="${palette.surface}" stroke="${palette.accent}" stroke-width="${Math.max(2, lineWidth * 0.8).toFixed(2)}" />`,
    `</svg>`,
  ].join("");

  return {
    src: `data:image/svg+xml;base64,${Buffer.from(svg, "utf-8").toString("base64")}`,
    width,
    height,
    minUsd: min,
    maxUsd: max,
    firstAt: priced[0].at,
    lastAt: priced[priced.length - 1].at,
    pointCount: priced.length,
  };
}
