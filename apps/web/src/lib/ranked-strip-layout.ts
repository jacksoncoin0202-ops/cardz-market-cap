export interface RankedStripInput {
  rank: number;
  value: number;
}

export interface RankedStripTile<T extends RankedStripInput> {
  item: T;
  x: number;
  y: number;
  width: number;
  height: number;
}

function worstAspect<T extends RankedStripInput>(row: T[], width: number, scale: number): number {
  const rowArea = row.reduce((sum, item) => sum + item.value * scale, 0);
  if (rowArea <= 0 || width <= 0) return Number.POSITIVE_INFINITY;
  const height = rowArea / width;
  return row.reduce((worst, item) => {
    const itemWidth = item.value * scale / height;
    const aspect = Math.max(itemWidth / height, height / itemWidth);
    return Math.max(worst, aspect);
  }, 1);
}

/**
 * An ordered strip treemap. Tiles retain exact value-proportional area while
 * ranks remain readable left-to-right, then top-to-bottom.
 */
export function rankedStripLayout<T extends RankedStripInput>(items: T[], width: number, height: number): RankedStripTile<T>[] {
  if (width <= 0 || height <= 0) return [];
  const sorted = items.filter((item) => item.value > 0).sort((a, b) => a.rank - b.rank);
  const total = sorted.reduce((sum, item) => sum + item.value, 0);
  if (!total) return [];
  const scale = width * height / total;
  const rows: T[][] = [];
  let cursor = 0;

  while (cursor < sorted.length) {
    const row: T[] = [sorted[cursor++]];
    while (cursor < sorted.length) {
      const current = worstAspect(row, width, scale);
      const candidate = worstAspect([...row, sorted[cursor]], width, scale);
      if (candidate > current) break;
      row.push(sorted[cursor++]);
    }
    rows.push(row);
  }

  const tiles: RankedStripTile<T>[] = [];
  let y = 0;
  rows.forEach((row, rowIndex) => {
    const rowArea = row.reduce((sum, item) => sum + item.value * scale, 0);
    const rowHeight = rowIndex === rows.length - 1 ? height - y : rowArea / width;
    let x = 0;
    row.forEach((item, itemIndex) => {
      const itemWidth = itemIndex === row.length - 1 ? width - x : item.value * scale / rowHeight;
      tiles.push({ item, x, y, width: itemWidth, height: rowHeight });
      x += itemWidth;
    });
    y += rowHeight;
  });
  return tiles;
}

/* 畫廊版 squarified treemap（同 mockup Plan C 對齊）：按市值排序，row 逐個加入直到
   aspect 變差就開新 row；目標 tile 比例 0.714（卡形直向），最大卡喺左上。 */
const TARGET_ASPECT = 0.714;
const IDEAL_ASPECT = 1 / TARGET_ASPECT;

interface TreemapRect { x: number; y: number; w: number; h: number; }

function worstRatio(areas: number[], side: number): number {
  const rowArea = areas.reduce((sum, area) => sum + area, 0);
  const thickness = rowArea / side;
  let worst = 0;
  for (const area of areas) {
    const aspect = (area / thickness) / thickness;
    const ratio = Math.max(aspect, 1 / aspect);
    worst = Math.max(worst, Math.abs(Math.log(ratio / IDEAL_ASPECT)));
  }
  return worst;
}

function layoutTreemapRow<T extends RankedStripInput>(row: T[], rect: TreemapRect, scale: number, tiles: RankedStripTile<T>[]): void {
  const rowArea = row.reduce((sum, item) => sum + item.value * scale, 0);
  if (rect.w >= rect.h) {
    const rowWidth = rowArea / rect.h;
    let cursorY = rect.y;
    row.forEach((item, index) => {
      const tileHeight = index === row.length - 1 ? rect.y + rect.h - cursorY : item.value * scale / rowWidth;
      tiles.push({ item, x: rect.x, y: cursorY, width: rowWidth, height: tileHeight });
      cursorY += tileHeight;
    });
    rect.x += rowWidth;
    rect.w -= rowWidth;
  } else {
    const rowHeight = rowArea / rect.w;
    let cursorX = rect.x;
    row.forEach((item, index) => {
      const tileWidth = index === row.length - 1 ? rect.x + rect.w - cursorX : item.value * scale / rowHeight;
      tiles.push({ item, x: cursorX, y: rect.y, width: tileWidth, height: rowHeight });
      cursorX += tileWidth;
    });
    rect.y += rowHeight;
    rect.h -= rowHeight;
  }
}

export function heatmapTreemapLayout<T extends RankedStripInput>(items: T[], width: number, height: number): RankedStripTile<T>[] {
  if (width <= 0 || height <= 0) return [];
  const sorted = items.filter((item) => item.value > 0).sort((a, b) => b.value - a.value);
  const total = sorted.reduce((sum, item) => sum + item.value, 0);
  if (!total) return [];
  const scale = width * height / total;
  const rect: TreemapRect = { x: 0, y: 0, w: width, h: height };
  const tiles: RankedStripTile<T>[] = [];
  let row: T[] = [];
  const queue = [...sorted];
  while (queue.length) {
    const side = Math.min(rect.w, rect.h);
    const areas = row.map((item) => item.value * scale);
    const next = queue[0].value * scale;
    if (row.length && worstRatio([...areas, next], side) > worstRatio(areas, side)) {
      layoutTreemapRow(row, rect, scale, tiles);
      row = [];
    } else {
      row.push(queue.shift()!);
    }
  }
  if (row.length) layoutTreemapRow(row, rect, scale, tiles);
  return tiles;
}

/* ── 拖動專用固定格網 ─────────────────────────────────────────
   拖 slider 嗰陣唔好每吓都跑 treemap：tile 數一變全部位置就洗版，
   一來重排要幾百 ms（手機窒），二來冇「逐張浮出」嘅連續感。
   做法：預先一次過砌好 N 檔嘅格網座標，拖動時淨係換 col×row 組合，
   tile 永遠唔郁位，新 tile 喺下一格原地長出嚟。 */
export interface GridGeometry {
  cols: number;
  rows: number;
  count: number; // 呢個格網裝到幾多格
}

/* 目標 tile 闊高比 1.4（近方形、同默認 23 磚 8×3 構圖一致），
   由細到大掃 col×row，揀第一個裝得晒 count 嘅組合 */
function pickGrid(count: number, w: number, h: number): GridGeometry {
  for (let n = count; ; n++) {
    for (let cols = 1; cols <= n; cols++) {
      const rows = Math.ceil(n / cols);
      if (rows * cols < n) continue;
      const ratio = w / cols / (h / rows);
      if (ratio >= 0.72 && ratio <= 1.9) {
        const cellRatio = w / cols / (h / rows);
        if (Math.abs(cellRatio - 1.4) < 0.62) return { cols, rows, count: n };
      }
    }
    if (n > count + 40) {
      /* 後備：正方形最近根，唔會行到嚟呢度（100 磚內一定有解） */
      const cols = Math.ceil(Math.sqrt(count * (w / Math.max(1, h))));
      return { cols, rows: Math.ceil(count / cols), count };
    }
  }
}

/* 由細到大逐檔產生，確保每檔都裝到 total 張（放手時直接停喺目標檔） */
export function buildGridSteps(total: number, min: number, w: number, h: number): GridGeometry[] {
  if (w <= 0 || h <= 0 || total <= 0) return [];
  const steps: GridGeometry[] = [];
  let lastKey = "";
  for (let n = min; n <= total; n++) {
    const g = pickGrid(n, w, h);
    const key = `${g.cols}x${g.rows}`;
    if (key !== lastKey && g.count >= total) {
      steps.push(g);
      lastKey = key;
    }
  }
  if (!steps.length || steps[steps.length - 1].count < total) {
    const cols = Math.ceil(Math.sqrt(total * (w / Math.max(1, h))));
    steps.push({ cols, rows: Math.ceil(total / cols), count: total });
  }
  return steps;
}

/* 某檔格網入面，第 index 張（0-based）嘅座標；行優先填滿 */
export function gridCellRect(step: GridGeometry, index: number, w: number, h: number): { x: number; y: number; width: number; height: number } {
  const col = index % step.cols;
  const row = Math.floor(index / step.cols);
  const cellW = w / step.cols;
  const cellH = h / step.rows;
  return { x: col * cellW, y: row * cellH, width: cellW, height: cellH };
}
