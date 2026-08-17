"use client";

/* Heatmap 分享圖（PNG）渲染器 —— 純 canvas，唔識 React。
   由 components/heatmap.tsx 嘅 exportHeatmap 抽出嚟：嗰邊淨係砌 opts → 呢度出 canvas →
   嗰邊 toBlob + share/download（分享流程一個字都冇改）。

   Owner 硬規矩（2026-08-17）：
   1) **圖入面所有字一律英文**，唔跟介面語言 —— 一張圖出咗街係俾全世界睇，唔係淨係俾當前
      用戶睇。所以呢個檔零 i18n import，文案全部係下面嘅 SHARE_TEXT。
   2) **圖以熱力圖為主**：header 得 logo + 標題 + 日期；tiles 之下淨係一行 legend。
      冇 footer、冇 QR、冇 methodology、冇 tagline、冇統計 chip（owner：「咩都唔使加」）。
   3) legend 一定要有，而且**唔准寫死「綠 = 升」**：red-up 慣例同 /tune 自訂色會令升跌色
      對調。opts.colors 就係實際畫落 tile 嗰兩隻色，legend 直接攞佢做色板，所以永遠講真話。 */

import { tileStyle, type TileColors, type TileParams } from "./tile-style";

/* 圖入面所有字（英文 only，見檔頭規矩 1） */
const SHARE_TEXT = {
  up: "Up",
  down: "Down",
  neutral: "Data pending",
  /* 深淺 = 幅度，色相 = 方向。呢句唔提任何顏色名，反轉慣例都仍然啱。 */
  intensity: "Deeper shade = bigger move",
} as const;

/* 一條 stack 行晒五個語言（圖入面得英文字，唔使再分 locale）。
   系統字為主：分享圖係 click 之後即刻畫，唔等得 web font 落 network。 */
const SHARE_FONT = '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, system-ui, sans-serif';

/* bg/text/sub 沿用舊 export 嘅值；accent 抄 globals.css 嘅 `--accent`
   （:root #b85416 / [data-theme="dark"] #e8823f）—— canvas 讀唔到 CSS var，改 token 記住改埋呢度。
   logo 同 components/header.tsx 嘅 BRAND_LOGO 一樣：淺色底用有黑描邊嗰版，深色底用白版。 */
const PALETTE = {
  dark: { bg: "#0D0D0F", text: "#F1F1EE", sub: "#A0A09B", accent: "#E8823F", logo: "/brand/logo-cardz-marketcap-dark.png" },
  light: { bg: "#FAFAF7", text: "#191917", sub: "#555550", accent: "#B85416", logo: "/brand/logo-cardz-marketcap.png" },
} as const;

/* tile 圓角：同 globals.css `.heatmap-tile { border-radius: 4px }` 綁死（owner 2026-08-17：
   PNG 入面啲色格要同螢幕一樣有細圓角，唔准四四方方）。改 CSS 嗰邊必改呢度。 */
const TILE_RADIUS_CSS = 4;

/* label 幾何抄 globals.css `.tile-move`（top/right 4px、padding 1px 3px、radius 4px、
   line-height 1.2）—— 同 lib/tile-style.ts TILE_LABEL 係同一組數，全部 × scale 落 canvas。 */
const LABEL = { inset: 4, padX: 3, padY: 1, radius: 4, lineHeight: 1.2 } as const;

/* iOS canvas 上限 16.7M px；呢度自己封頂喺 5.2M，留大量餘裕兼令 toBlob 唔會慢到甩 user activation。 */
const MAX_CANVAS_AREA = 5_200_000;

export interface ShareTileInput {
  /* treemap 原始 rect（未扣 gap），同 on-screen 傳俾 <HeatmapTile> 嗰組數一模一樣 */
  x: number;
  y: number;
  width: number;
  height: number;
  /* changeValue(card, period) 嘅結果；null = 未有數 */
  change: number | null;
  imageUrl: string;
}

export interface HeatmapShareOptions {
  /* on-screen heatmap frame 嘅 CSS px 尺寸（tiles 嘅座標系） */
  frameWidth: number;
  frameHeight: number;
  tiles: ShareTileInput[];
  params: TileParams;
  /* 已經按 red-up / 自訂色換好嘅實際 tile 色 —— legend 直接用佢 */
  colors: TileColors;
  dark: boolean;
  /* 標題 = `Top {count} · {periodLabel}`，兩樣都要英文（periodLabel 傳 copy.en.periods[...]） */
  count: number;
  periodLabel: string;
  dateText: string;
  /* 測試 / 非瀏覽器環境先注入；預設行 new Image() */
  loadImage?: (src: string) => Promise<HTMLImageElement | null>;
}

/* measureText 得呢個 shape 就夠 —— fitText 因此可以喺 test 度用假 ctx 跑（唔使真 canvas）。 */
export interface TextMeasurer {
  measureText(text: string): { width: number };
}

/* 單行截字：入唔到就砍到入為止再加「…」。ctx 要事先 set 好 font。 */
export function fitText(ctx: TextMeasurer, text: string, maxWidth: number): string {
  if (maxWidth <= 0) return "";
  if (ctx.measureText(text).width <= maxWidth) return text;
  let cut = text;
  while (cut.length > 1 && ctx.measureText(`${cut}…`).width > maxWidth) cut = cut.slice(0, -1);
  return `${cut}…`;
}

/* 2400 / frameWidth，夾喺 2–3.5：390 闊手機出 3.5×，1280 桌面出 2×。
   再用面積閘收返（估算高度 = frame + 頭尾約 150 CSS px），爆咗就按 √ 比例縮。 */
function planScale(frameWidth: number, frameHeight: number): number {
  let scale = Math.min(3.5, Math.max(2, 2400 / Math.max(1, frameWidth)));
  for (let i = 0; i < 4; i++) {
    const area = (frameWidth + 48) * scale * (frameHeight + 150) * scale;
    if (area <= MAX_CANVAS_AREA) break;
    scale = Math.max(1.25, scale * Math.sqrt(MAX_CANVAS_AREA / area));
  }
  return scale;
}

function defaultLoadImage(src: string): Promise<HTMLImageElement | null> {
  return new Promise((resolve) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => resolve(null);
    img.src = src;
  });
}

/* 圓角 path：唔靠 ctx.roundRect（Safari 16 之前冇），arcTo 自己畫，行為完全可控。 */
function roundedPath(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number): void {
  const radius = Math.max(0, Math.min(r, w / 2, h / 2));
  ctx.beginPath();
  ctx.moveTo(x + radius, y);
  ctx.arcTo(x + w, y, x + w, y + h, radius);
  ctx.arcTo(x + w, y + h, x, y + h, radius);
  ctx.arcTo(x, y + h, x, y, radius);
  ctx.arcTo(x, y, x + w, y, radius);
  ctx.closePath();
}

/* #rrggbb → rgba(...)；唔係 hex（例如 params.neutralTile 本身就係 rgba）就原樣退返，
   由 caller 決定用唔用。 */
function withAlpha(color: string, alpha: number): string | null {
  const hex = color.trim();
  if (!/^#[0-9a-f]{6}$/i.test(hex)) return null;
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

interface TextStyle {
  font: string;
  color: string;
  align?: CanvasTextAlign;
  baseline?: CanvasTextBaseline;
}

/* 每次畫字都寫死 align/baseline：舊版 `ctx.textBaseline = "middle"` set 一次就走，
   之後每段字都食住上一段嘅 baseline，加一段新字就會錯位（defect #4）。 */
function drawText(ctx: CanvasRenderingContext2D, text: string, x: number, y: number, style: TextStyle): void {
  ctx.font = style.font;
  ctx.fillStyle = style.color;
  ctx.textAlign = style.align ?? "left";
  ctx.textBaseline = style.baseline ?? "alphabetic";
  ctx.fillText(text, x, y);
  ctx.textAlign = "left";
  ctx.textBaseline = "alphabetic";
}

/* 128² 灰噪點 tile，createPattern repeat 鋪全張 —— 純色底放大睇會見到 banding，
   加咗呢層先似印刷品（alpha 好細，唔會影響 tile 讀色）。 */
function grainPattern(ctx: CanvasRenderingContext2D): CanvasPattern | null {
  const size = 128;
  const noise = document.createElement("canvas");
  noise.width = size;
  noise.height = size;
  const nctx = noise.getContext("2d");
  if (!nctx) return null;
  const image = nctx.createImageData(size, size);
  for (let i = 0; i < image.data.length; i += 4) {
    const v = 128 + Math.round((Math.random() - 0.5) * 210);
    image.data[i] = v;
    image.data[i + 1] = v;
    image.data[i + 2] = v;
    image.data[i + 3] = 255;
  }
  nctx.putImageData(image, 0, 0);
  return ctx.createPattern(noise, "repeat");
}

/**
 * 畫一張 heatmap 分享圖，回傳 canvas（caller 自己 toBlob）。
 * 幾何同 on-screen 完全一致：tileStyle 用**未縮放**嘅 CSS px 尺寸計（同 heatmap.tsx render
 * 嗰個 call 一模一樣），出到嚟嘅 cardW/cardH/fontSize 先統一 × scale —— 咁先係「放大咗嘅同一張圖」。
 */
export async function renderHeatmapShare(opts: HeatmapShareOptions): Promise<HTMLCanvasElement> {
  const { frameWidth, frameHeight, tiles, params, colors, dark, count, periodLabel, dateText } = opts;
  const loadImage = opts.loadImage ?? defaultLoadImage;
  const skin = dark ? PALETTE.dark : PALETTE.light;
  const scale = planScale(frameWidth, frameHeight);

  /* 版面單位：unit = 8 CSS px；所有間距都係佢嘅倍數，唔好再撒硬數落去。 */
  const unit = 8 * scale;
  /* pad 取整：canvas.width setter 會截走小數，唔取整就會右邊 pad 少咗少少 */
  const pad = Math.round(3 * unit);
  const hair = Math.max(1, Math.round(scale)); // 1 CSS px 幼線
  const boardW = Math.round(frameWidth * scale);
  const boardH = Math.round(frameHeight * scale);
  const canvasW = boardW + pad * 2;
  const contentW = canvasW - pad * 2;

  /* 字級（CSS px × scale） */
  const titleFont = (narrow: boolean) => `700 ${Math.round((narrow ? 20 : 26) * scale)}px ${SHARE_FONT}`;
  const stampFont = `500 ${Math.round(12 * scale)}px ${SHARE_FONT}`;
  const legendFont = `600 ${Math.round(12 * scale)}px ${SHARE_FONT}`;
  const noteFont = `500 ${Math.round(11 * scale)}px ${SHARE_FONT}`;

  /* 字體可能仲喺度 load：唔等就會量錯闊度（截字位、legend 排位全部跟住錯）。 */
  if (typeof document !== "undefined" && document.fonts?.ready) {
    try { await document.fonts.ready; } catch { /* 唔支援就照畫 */ }
  }

  const measureCanvas = document.createElement("canvas");
  const measure = measureCanvas.getContext("2d");
  if (!measure) throw new Error("share image: 2d context unavailable");

  /* ── header 量度（唔用死高度）──
     一行擺得落（logo | 標題 | 日期）就一行；擺唔落就兩行（logo + 日期一行、標題自己一行）。 */
  const logo = await loadImage(skin.logo);
  const logoH = Math.round(32 * scale);
  const logoW = logo ? Math.round(logoH * (logo.width / logo.height)) : 0;
  const shareTitle = `Top ${count} · ${periodLabel}`;
  measure.font = stampFont;
  const stampW = measure.measureText(dateText).width;
  measure.font = titleFont(false);
  const titleWideW = measure.measureText(shareTitle).width;
  const oneRow = logoW + unit * 3 + titleWideW + unit * 3 + stampW <= contentW;
  const titleH = Math.round((oneRow ? 26 : 20) * scale);
  const headerH = oneRow
    ? Math.max(logoH, titleH * 1.2)
    : Math.max(logoH, Math.round(12 * scale) * 1.2) + unit * 1.5 + titleH * 1.2;

  /* ── legend 量度 ──
     三格色板 + 標籤，右邊（擺得落先）加一句「深啲 = 郁得多」。 */
  const swatch = Math.round(1.6 * unit);
  const legendGap = unit * 2.5;
  const swatchGap = unit * 0.9;
  measure.font = legendFont;
  const legendItems: { color: string; label: string }[] = [
    { color: colors.up, label: SHARE_TEXT.up },
    { color: colors.down, label: SHARE_TEXT.down },
    { color: colors.neutral, label: SHARE_TEXT.neutral },
  ];
  const legendW = legendItems.reduce((sum, item, i) => sum + (i ? legendGap : 0) + swatch + swatchGap + measure.measureText(item.label).width, 0);
  measure.font = noteFont;
  const noteW = measure.measureText(SHARE_TEXT.intensity).width;
  const showNote = legendW + legendGap + noteW <= contentW;
  const legendH = Math.max(swatch, Math.round(12 * scale) * 1.2);

  const canvasH = Math.round(pad + headerH + unit * 2.5 + boardH + unit * 2.5 + legendH + pad);
  const canvas = document.createElement("canvas");
  canvas.width = canvasW;
  canvas.height = canvasH;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("share image: 2d context unavailable");
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = "high";

  /* ── 底 ── 純色 → 中央微亮、邊角微暗嘅 vignette → 幼噪點 */
  ctx.fillStyle = skin.bg;
  ctx.fillRect(0, 0, canvasW, canvasH);
  const vignette = ctx.createRadialGradient(canvasW / 2, canvasH * 0.34, 0, canvasW / 2, canvasH * 0.34, Math.hypot(canvasW, canvasH) * 0.62);
  if (dark) {
    vignette.addColorStop(0, "rgba(255, 255, 255, 0.055)");
    vignette.addColorStop(0.55, "rgba(255, 255, 255, 0)");
    vignette.addColorStop(1, "rgba(0, 0, 0, 0.30)");
  } else {
    vignette.addColorStop(0, "rgba(255, 255, 255, 0.5)");
    vignette.addColorStop(0.55, "rgba(255, 255, 255, 0)");
    vignette.addColorStop(1, "rgba(0, 0, 0, 0.055)");
  }
  ctx.fillStyle = vignette;
  ctx.fillRect(0, 0, canvasW, canvasH);
  const grain = grainPattern(ctx);
  if (grain) {
    ctx.save();
    ctx.globalAlpha = dark ? 0.035 : 0.025;
    ctx.fillStyle = grain;
    ctx.fillRect(0, 0, canvasW, canvasH);
    ctx.restore();
  }

  /* ── header ── */
  const headerTop = pad;
  if (logo) {
    const logoY = oneRow ? headerTop + (headerH - logoH) / 2 : headerTop;
    ctx.drawImage(logo, pad, logoY, logoW, logoH);
  }
  if (oneRow) {
    const midY = headerTop + headerH / 2;
    ctx.font = titleFont(false);
    const titleMax = contentW - logoW - unit * 3 - stampW - unit * 3;
    drawText(ctx, fitText(ctx, shareTitle, titleMax), pad + logoW + unit * 3, midY, { font: titleFont(false), color: skin.text, baseline: "middle" });
    drawText(ctx, dateText, canvasW - pad, midY, { font: stampFont, color: skin.sub, align: "right", baseline: "middle" });
  } else {
    const rowMid = headerTop + Math.max(logoH, Math.round(12 * scale) * 1.2) / 2;
    drawText(ctx, dateText, canvasW - pad, rowMid, { font: stampFont, color: skin.sub, align: "right", baseline: "middle" });
    ctx.font = titleFont(true);
    /* baseline 用 bottom 唔用 alphabetic：alphabetic 會令 descender（Top 個 p）跌出 header 之外 */
    drawText(ctx, fitText(ctx, shareTitle, contentW), pad, headerTop + headerH, { font: titleFont(true), color: skin.text, baseline: "bottom" });
  }

  /* ── tiles ──
     幾何同 on-screen 一模一樣：x/y/w/h 全部由同一組 treemap rect 扣 gap 得出，再 × scale。 */
  const ox = pad;
  const oy = Math.round(headerTop + headerH + unit * 2.5);
  const images = await Promise.all(tiles.map((tile) => loadImage(tile.imageUrl)));
  const tileRadius = TILE_RADIUS_CSS * scale;
  const labelPlate = { up: withAlpha(colors.up, 0.34), down: withAlpha(colors.down, 0.34) };

  tiles.forEach((tile, index) => {
    const gap = params.gap;
    /* CSS px 尺寸（同 heatmap.tsx render 傳落 tileStyle 嗰兩個數一樣），
       之後所有輸出（cardW/cardH/fontSize）先統一 × scale —— 舊版將已經係 canvas px
       嘅 fontSize 再乘一次 scale，label 大咗成 75%（defect #2）。 */
    const cssW = tile.width - gap;
    const cssH = tile.height - gap;
    if (cssW <= 0 || cssH <= 0) return;
    const st = tileStyle(tile.change, cssW, cssH, colors, params);
    const tx = ox + (tile.x + gap / 2) * scale;
    const ty = oy + (tile.y + gap / 2) * scale;
    const tw = cssW * scale;
    const th = cssH * scale;

    ctx.save();
    roundedPath(ctx, tx, ty, tw, th, tileRadius);
    ctx.clip();
    ctx.fillStyle = st.bg;
    ctx.fillRect(tx, ty, tw, th);

    const img = images[index];
    if (img && st.showCard) {
      const cw = st.cardW * scale;
      const ch = st.cardH * scale;
      const cx = tx + (tw - cw) / 2;
      const cy = ty + (th - ch) / 2;
      // contain：完整卡圖等比縮放入框，唔准 center-crop 食角
      const fit = Math.min(cw / img.width, ch / img.height);
      const dw = img.width * fit;
      const dh = img.height * fit;
      /* 卡圖落陰影（跟 globals.css .tile-card 個 box-shadow 嘅方向）：卡浮起嚟先似實物。
         shadow 只喺呢一 draw 開，畫完即刻收 —— 唔收會漏落之後嘅 label / legend。 */
      ctx.shadowColor = "rgba(0, 0, 0, 0.35)";
      ctx.shadowBlur = 6 * scale;
      ctx.shadowOffsetY = 2 * scale;
      ctx.drawImage(img, cx + (cw - dw) / 2, cy + (ch - dh) / 2, dw, dh);
      ctx.shadowColor = "transparent";
      ctx.shadowBlur = 0;
      ctx.shadowOffsetY = 0;
    }

    if (st.move) {
      /* label 抄 .tile-move：右上角、同色淡底板、白字 800 + 陰影。
         底板色由 opts.colors 嚟（唔係 CSS token）—— red-up / 自訂色之下先至同 tile 對得上。 */
      const fontPx = st.fontSize * scale;
      ctx.font = `800 ${fontPx}px ${SHARE_FONT}`;
      const textW = ctx.measureText(st.move).width;
      const plateW = textW + LABEL.padX * 2 * scale;
      const plateH = fontPx * LABEL.lineHeight + LABEL.padY * 2 * scale;
      const plateX = tx + tw - LABEL.inset * scale - plateW;
      const plateY = ty + LABEL.inset * scale;
      const plate = st.direction === "up" ? labelPlate.up : st.direction === "down" ? labelPlate.down : null;
      if (plate) {
        ctx.fillStyle = plate;
        roundedPath(ctx, plateX, plateY, plateW, plateH, LABEL.radius * scale);
        ctx.fill();
      }
      ctx.shadowColor = "rgba(0, 0, 0, 0.7)";
      ctx.shadowBlur = 4 * scale;
      ctx.shadowOffsetY = scale;
      drawText(ctx, st.move, plateX + LABEL.padX * scale, plateY + plateH / 2, {
        font: `800 ${fontPx}px ${SHARE_FONT}`,
        color: "#fff",
        baseline: "middle",
      });
      ctx.shadowColor = "transparent";
      ctx.shadowBlur = 0;
      ctx.shadowOffsetY = 0;
    }
    ctx.restore();
  });

  /* ── legend ──
     唯一一行附加資訊：色板直接用 opts.colors，所以 red-up 反轉之後個 legend 自己跟住反轉。 */
  const legendTop = oy + boardH + unit * 2.5;
  const legendMid = legendTop + legendH / 2;
  let cursor = pad;
  ctx.font = legendFont;
  for (const item of legendItems) {
    ctx.fillStyle = item.color;
    roundedPath(ctx, cursor, legendMid - swatch / 2, swatch, swatch, TILE_RADIUS_CSS * scale);
    ctx.fill();
    cursor += swatch + swatchGap;
    drawText(ctx, item.label, cursor, legendMid, { font: legendFont, color: skin.text, baseline: "middle" });
    cursor += ctx.measureText(item.label).width + legendGap;
  }
  if (showNote) {
    drawText(ctx, SHARE_TEXT.intensity, canvasW - pad, legendMid, { font: noteFont, color: skin.sub, align: "right", baseline: "middle" });
  }

  /* ── 幼框 ── 最後畫，壓喺所有嘢上面，似一張裱好嘅相 */
  const inset = Math.round(1.25 * unit);
  const frameStroke = withAlpha(skin.accent, 0.22);
  if (frameStroke) {
    ctx.strokeStyle = frameStroke;
    ctx.lineWidth = hair;
    roundedPath(ctx, inset + hair / 2, inset + hair / 2, canvasW - inset * 2 - hair, canvasH - inset * 2 - hair, unit * 1.5);
    ctx.stroke();
  }

  return canvas;
}
