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
