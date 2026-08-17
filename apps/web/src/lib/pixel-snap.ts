/* heatmap tile 幾何釘落 device pixel 格（owner 2026-08-17：「色塊間距唔對等、挨左挨右」）。
   根因：treemap 出嚟嘅 x/y/w/h 係浮點，再加 gap/2 = 1.5px，每格四邊都落喺半粒 pixel 度；
   Chrome 逐個 box 各自 snap 邊界，鄰格之間 3px 嘅 gap 畫出嚟就 2 / 3 / 4px 亂跳
   （1440 量到 18% 格唔係 3px，1920 32%），frame 四邊 margin 亦唔對稱。
   做法：唔改 treemap（面積 ∝ 市值不變），只喺 render 前將「邊界線」round 到 device px，
   gap 拆做 p（左／上）+ q（右／下）兩個整數，p + q = gap —— 相鄰兩格共用同一條邊界線，
   所以每條 gap 都係 exactly gap 粒 device px；frame 四邊 margin 一律 q（= 以前 gap/2 嘅意思）。
   share PNG（lib/share-image.ts）唔行呢度：佢係 2–3.5× 畫 canvas，gap 6–10px，浮點誤差睇唔出。 */

export interface TileBox { x: number; y: number; w: number; h: number; }

/** treemap rect（CSS px、未扣 gap）→ tile 實際 box（CSS px，但每條邊都落喺 device px 整數格）。
 *  邊界線係 0（frame 左／上邊）就用 q 做 margin，同右／下邊對稱；只會令邊格窄 ≤1 device px。 */
export function snapTileBox(x: number, y: number, width: number, height: number, gap: number, dpr: number): TileBox {
  const d = dpr > 0 && Number.isFinite(dpr) ? dpr : 1;
  const g = Math.max(0, Math.round(gap * d));
  const p = Math.floor(g / 2);
  const q = g - p;
  const L = Math.round(x * d), R = Math.round((x + width) * d);
  const T = Math.round(y * d), B = Math.round((y + height) * d);
  const x0 = L + (L === 0 ? q : p), y0 = T + (T === 0 ? q : p);
  const x1 = R - q, y1 = B - q;
  return { x: x0 / d, y: y0 / d, w: Math.max(0, x1 - x0) / d, h: Math.max(0, y1 - y0) / d };
}

/** 卡圖 box：闊高 round 到 device px，再確保 (tile − card) 喺兩個方向都係雙數 device px ——
 *  .tile-card 用 left/top 50% + translate(-50%,-50%) 置中，雙數餘量先會落喺整數格，卡邊唔會半粒 pixel 糊。
 *  tileW/tileH 係 snapTileBox 出嚟嘅（已經係 device px 整數 ÷ dpr）。 */
export function snapCardBox(tileW: number, tileH: number, cardW: number, cardH: number, dpr: number): { cardW: number; cardH: number } {
  const d = dpr > 0 && Number.isFinite(dpr) ? dpr : 1;
  const tw = Math.round(tileW * d), th = Math.round(tileH * d);
  let cw = Math.max(0, Math.round(cardW * d)), ch = Math.max(0, Math.round(cardH * d));
  if ((tw - cw) % 2 !== 0) cw = Math.max(0, cw - 1);
  if ((th - ch) % 2 !== 0) ch = Math.max(0, ch - 1);
  return { cardW: cw / d, cardH: ch / d };
}

/** frame 量度：contentRect 浮點闊高 → 向下取整到 device px（唔好 round：round 大咗會令最右／最底
 *  一行 tile 出界被 overflow 裁走，右／下 margin 就細過左／上）。 */
export function snapFrameSize(width: number, height: number, dpr: number): { width: number; height: number; dpr: number } {
  const d = dpr > 0 && Number.isFinite(dpr) ? dpr : 1;
  return { width: Math.floor(width * d) / d, height: Math.floor(height * d) / d, dpr: d };
}
