/* heatmap tile 幾何釘落 device pixel 格（owner 2026-08-17：「色塊間距唔對等、挨左挨右」）。

   根因一（tile 之間）：treemap 出嚟嘅 x/y/w/h 係浮點，再加 gap/2 = 1.5px，每格四邊都落喺半粒
   pixel 度；Chrome 逐個 box 各自 snap 邊界，鄰格之間 3px 嘅 gap 畫出嚟就 2 / 3 / 4px 亂跳
   （1440 量到 18% 格唔係 3px，1920 32%）。做法：唔改 treemap（面積 ∝ 市值不變），只喺 render 前將
   「邊界線」round 到 device px，gap 拆做 p（左／上）+ q（右／下）兩個整數，p + q = gap ——
   相鄰兩格共用同一條邊界線，所以每條 gap 都係 exactly gap 粒 device px。

   根因二（frame 四邊）：Chrome 係將**絕對**座標 snap 落 device 格，而 frame 自己個 origin 係浮點
   （1440 實測 left 43.1875、width 1338.625）。淨係喺 frame 本地座標 round，右／下嗰條邊 round 落
   絕對格就會多一粒 —— live 量到 1440 render 出嚟係 左2 上2 右3 下3、1440@2x 左3 上3 右4 下3。
   所以 snapFrameGrid() 要收 frame 嘅 absLeft/absTop，喺**絕對** device 格度計：
     originX = round(absLeft × dpr) / dpr − absLeft   （由 frame 邊界移到最近嗰條 device 線嘅偏移）
     width   = (round((absLeft + w) × dpr) − round(absLeft × dpr)) / dpr   （frame 真正 render 出嚟嘅闊度）
   之後每條 tile 邊都係「整數 device px + originX」，round 返落絕對格係 exact，四邊 margin 一律 q。

   ⚠️ absTop 係 viewport 座標，會跟 scroll 變；ResizeObserver 唔會因為 scroll 而 fire，所以 scroll
   完 originY 有機會過時。**tile 之間嘅 gap 唔受影響**（gap = 兩個整數之差，加同一個偏移 round 完
   差值不變），最多只係最外圈上／下 margin 飄 ±1 device px —— 呢個同修之前一樣，唔值得為咗佢
   喺 scroll handler 度 setState 令成版 100 格重排。

   share PNG（lib/share-image.ts）唔行呢度：佢係 2–3.5× 畫 canvas，gap 6–10px，浮點誤差睇唔出。 */

export interface TileBox { x: number; y: number; w: number; h: number; }

/** frame 喺絕對 device 格入面嘅量度結果：width/height 係 frame 真正 render 嘅尺寸（CSS px），
 *  originX/originY 係「本地 0 → frame 已 snap 嘅邊界」嘅偏移（|值| < 1 device px）。 */
export interface FrameGrid { width: number; height: number; dpr: number; originX: number; originY: number; }

const safeDpr = (dpr: number) => (dpr > 0 && Number.isFinite(dpr) ? dpr : 1);
const safeCoord = (v: number) => (Number.isFinite(v) ? v : 0);

/** frame 量度：contentRect 浮點闊高 + frame 喺 viewport 嘅絕對位置 → 絕對 device 格上嘅 layout 盒。
 *  absLeft/absTop 唔傳（或者 0）就退化返「本地格」，右／下 margin 會有 ≤1 device px 誤差。 */
export function snapFrameGrid(width: number, height: number, dpr: number, absLeft = 0, absTop = 0): FrameGrid {
  const d = safeDpr(dpr);
  const ax = safeCoord(absLeft), ay = safeCoord(absTop);
  const gx = Math.round(ax * d), gy = Math.round(ay * d);
  return {
    width: Math.max(0, Math.round((ax + width) * d) - gx) / d,
    height: Math.max(0, Math.round((ay + height) * d) - gy) / d,
    dpr: d,
    originX: gx / d - ax,
    originY: gy / d - ay,
  };
}

/** treemap rect（CSS px、未扣 gap，座標系 = [0, grid.width] × [0, grid.height]）→ tile 實際 box：
 *  每條邊 round 落絕對 device 格，再加返 grid origin。邊界線係 0（frame 左／上邊）就用 q 做 margin，
 *  同右／下邊對稱；只會令邊格窄 ≤1 device px。 */
export function snapTileBox(x: number, y: number, width: number, height: number, gap: number, grid: FrameGrid): TileBox {
  const d = grid.dpr;
  const g = Math.max(0, Math.round(gap * d));
  const p = Math.floor(g / 2);
  const q = g - p;
  const L = Math.round(x * d), R = Math.round((x + width) * d);
  const T = Math.round(y * d), B = Math.round((y + height) * d);
  const x0 = L + (L === 0 ? q : p), y0 = T + (T === 0 ? q : p);
  const x1 = R - q, y1 = B - q;
  return {
    x: grid.originX + x0 / d,
    y: grid.originY + y0 / d,
    w: Math.max(0, x1 - x0) / d,
    h: Math.max(0, y1 - y0) / d,
  };
}

/** 卡圖 box：闊高 round 到 device px，再確保 (tile − card) 喺兩個方向都係雙數 device px ——
 *  .tile-card 用 left/top 50% + translate(-50%,-50%) 置中，雙數餘量先會落喺整數格，卡邊唔會半粒 pixel 糊。
 *  tileW/tileH 係 snapTileBox 出嚟嘅（已經係 device px 整數 ÷ dpr）。 */
export function snapCardBox(tileW: number, tileH: number, cardW: number, cardH: number, dpr: number): { cardW: number; cardH: number } {
  const d = safeDpr(dpr);
  const tw = Math.round(tileW * d), th = Math.round(tileH * d);
  let cw = Math.max(0, Math.round(cardW * d)), ch = Math.max(0, Math.round(cardH * d));
  if ((tw - cw) % 2 !== 0) cw = Math.max(0, cw - 1);
  if ((th - ch) % 2 !== 0) ch = Math.max(0, ch - 1);
  return { cardW: cw / d, cardH: ch / d };
}
