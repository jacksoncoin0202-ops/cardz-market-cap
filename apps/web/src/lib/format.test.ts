import { describe, expect, it } from "vitest";
import { formatDeltaMoney, formatMetricMoney, formatMoney, formatPercent, formatTrackedSales, metricTone, normaliseCurrency, normaliseLocale } from "./format";

const rates = { USD: 1, HKD: 7.8, CNY: 7.2, GBP: 0.78, TWD: 32.5, JPY: 162.5, KRW: 1380 };

describe("market formatting", () => {
  it("defaults to English and USD", () => {
    expect(normaliseLocale(null)).toBe("en");
    expect(normaliseCurrency(null)).toBe("USD");
  });

  it("converts values using snapshot rates", () => {
    expect(formatMoney(100, "HKD", rates, "en")).toContain("780");
    expect(formatMoney(100, "JPY", rates, "ja")).toContain("16,250");
  });

  it("never turns accumulating change into a fake zero", () => {
    expect(formatPercent({ value: null, status: "accumulating", asOf: null }, "en")).toBe("Accumulating");
  });

  it("renders stale price metrics as plain values without a label", () => {
    expect(formatMetricMoney({ value: 100, status: "stale", asOf: "2026-07-21" }, "USD", rates, "en")).toBe("$100");
  });

  it("keeps stale directional metrics red or green while non-values stay neutral", () => {
    expect(metricTone({ value: 4.2, status: "stale", asOf: "2026-07-21" })).toBe("positive");
    expect(metricTone({ value: -2.4, status: "stale", asOf: "2026-07-21" })).toBe("negative");
    expect(metricTone({ value: null, status: "stale", asOf: "2026-07-21" })).toBe("neutral");
    expect(metricTone({ value: 3.1, status: "accumulating", asOf: null })).toBe("neutral");
  });

  it("never turns unavailable tracked sales into a fake zero", () => {
    expect(formatTrackedSales({
      valueUsd: { value: null, status: "unavailable", asOf: null },
      count: { value: null, status: "unavailable", asOf: null },
      coverage: "unavailable",
      asOf: null,
    }, "USD", rates, "en")).toBe("—");
  });

  it("does not present an unobserved partial window as zero sales", () => {
    expect(formatTrackedSales({
      valueUsd: { value: 0, status: "ready", asOf: "2026-07-22" },
      count: { value: 0, status: "ready", asOf: "2026-07-22" },
      coverage: "partial",
      asOf: "2026-07-22",
    }, "USD", rates, "en")).toBe("—");
  });

  it("derives the absolute money delta from the percentage change", () => {
    const price = { value: 105, status: "ready" as const, asOf: "2026-07-22" };
    const up = { value: 5, status: "ready" as const, asOf: "2026-07-22" };
    const down = { value: -4.762, status: "ready" as const, asOf: "2026-07-22" };
    expect(formatDeltaMoney(price, up, "USD", rates, "en")).toBe("+$5");
    expect(formatDeltaMoney(price, down, "USD", rates, "en")).toMatch(/^−\$/);
  });

  it("shows no delta arrow when the change did not move the value", () => {
    const price = { value: 100, status: "ready" as const, asOf: "2026-07-22" };
    const flat = { value: 0, status: "ready" as const, asOf: "2026-07-22" };
    expect(formatDeltaMoney(price, flat, "USD", rates, "en")).toBeNull();
  });

  it("never shows a delta for accumulating or unavailable data", () => {
    const price = { value: 100, status: "ready" as const, asOf: "2026-07-22" };
    const accumulating = { value: 5, status: "accumulating" as const, asOf: null };
    expect(formatDeltaMoney(price, accumulating, "USD", rates, "en")).toBeNull();
  });

  it("still derives a delta from a stale price instead of dropping it", () => {
    const price = { value: 105, status: "stale" as const, asOf: "2026-07-21" };
    const up = { value: 5, status: "ready" as const, asOf: "2026-07-22" };
    expect(formatDeltaMoney(price, up, "USD", rates, "en")).toBe("+$5");
  });
});
