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
