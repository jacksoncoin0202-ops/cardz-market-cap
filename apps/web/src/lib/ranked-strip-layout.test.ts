import { describe, expect, it } from "vitest";
import { rankedStripLayout } from "./ranked-strip-layout";

describe("ranked strip treemap", () => {
  const values = [50, 22, 12, 8, 5, 3].map((value, index) => ({ rank: index + 1, value }));

  it("keeps rank one at the top left and preserves reading order", () => {
    const tiles = rankedStripLayout(values, 1000, 500);
    expect(tiles[0]).toMatchObject({ x: 0, y: 0, item: { rank: 1 } });
    expect(tiles.map((tile) => tile.item.rank)).toEqual([1, 2, 3, 4, 5, 6]);
    for (let index = 1; index < tiles.length; index += 1) {
      const previous = tiles[index - 1];
      const current = tiles[index];
      expect(current.y > previous.y || current.x >= previous.x).toBe(true);
    }
  });

  it("keeps tile area proportional to market value", () => {
    const tiles = rankedStripLayout(values, 1000, 500);
    const scale = 1000 * 500 / 100;
    tiles.forEach((tile) => {
      expect(tile.width * tile.height).toBeCloseTo(tile.item.value * scale, 4);
    });
  });

  it("lays out exactly one tile for each eligible top-100 card", () => {
    const top100 = Array.from({ length: 100 }, (_, index) => ({ rank: index + 1, value: 101 - index }));
    expect(rankedStripLayout(top100, 1200, 600)).toHaveLength(100);
  });
});
