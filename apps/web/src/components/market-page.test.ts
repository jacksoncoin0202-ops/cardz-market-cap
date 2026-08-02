import { describe, expect, it } from "vitest";
import { copy } from "@/lib/i18n";
import { marketHeatmapTitle } from "./market-page";

describe("market page heatmap title", () => {
  it("uses the plain title regardless of coverage count", () => {
    expect(marketHeatmapTitle("all", 7, copy.en)).toBe("Top 7 market heatmap");
    expect(marketHeatmapTitle("all", 100, copy.en)).toBe("Top 100 market heatmap");
  });

  it("uses the market-specific title for scoped views", () => {
    expect(marketHeatmapTitle("pokemon", 100, copy.en)).toBe("Pokémon market heatmap");
    expect(marketHeatmapTitle("one-piece", 64, copy.en)).toBe("One Piece market heatmap");
  });
});
