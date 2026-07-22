import { describe, expect, it } from "vitest";
import { pointsForWindow } from "./history-chart";
import type { PricePoint } from "@/lib/types";

const point = (at: string): PricePoint => ({
  at,
  priceUsd: 100,
  priceStatus: "ready",
  trackedSalesValueUsd: null,
  trackedSalesCount: null,
  salesCoverage: "unavailable",
});

describe("history window selection", () => {
  const points = [point("2026-07-01"), point("2026-07-14"), point("2026-07-21"), point("2026-07-22")];

  it("uses exactly the last two distinct daily closes for 1d", () => {
    expect(pointsForWindow(points, 1).map(({ at }) => at)).toEqual(["2026-07-21", "2026-07-22"]);
  });

  it("keeps the nearest earlier anchor for a longer window", () => {
    expect(pointsForWindow(points, 7).map(({ at }) => at)).toEqual(["2026-07-14", "2026-07-21", "2026-07-22"]);
  });
});
