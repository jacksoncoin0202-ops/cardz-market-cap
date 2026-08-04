import { describe, expect, it } from "vitest";
import { copy } from "@/lib/i18n";
import { marketHeatmapTitle } from "./market-page";

describe("market page verified coverage title", () => {
  it("labels partial coverage as Verified Top N", () => {
    expect(marketHeatmapTitle(
      "all",
      7,
      copy.en,
    )).toBe("Top 7 market heatmap");
  });

  it("uses Top 100 only when all 100 cards are verified", () => {
    expect(marketHeatmapTitle(
      "all",
      100,
      copy.en,
    )).toBe("Top 100 market heatmap");
  });
});
