import { describe, expect, it } from "vitest";
import { formatMetricMoney, formatMoney, formatPercent, formatTrackedSales, metricTone, normaliseCurrency, normaliseLocale } from "./format";

const rates = { USD: 1, HKD: 7.8, CNY: 7.2, GBP: 0.78, TWD: 32.5 };

describe("market formatting", () => {
  it("defaults to English and USD", () => {
    expect(normaliseLocale(null)).toBe("en");
    expect(normaliseCurrency(null)).toBe("USD");
  });

  it("converts values using snapshot rates", () => {
    expect(formatMoney(100, "HKD", rates, "en")).toContain("780");
  });

  it("never turns accumulating change into a fake zero", () => {
    expect(formatPercent({ value: null, status: "accumulating", asOf: null }, "en")).toBe("Accumulating");
  });

  it("marks stale price metrics instead of presenting them as current", () => {
    expect(formatMetricMoney({ value: 100, status: "stale", asOf: "2026-07-21" }, "USD", rates, "en")).toContain("Stale");
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
});
