import { describe, expect, it } from "vitest";
import { formatDeltaMoney, formatInteger, formatMetricInteger, formatMetricMoney, formatMoney, formatPercent, formatTrackedSales, metricTone, normaliseCurrency, normaliseLocale } from "./format";
import type { Currency } from "./types";

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

/*
 * FX 迴歸鎖 —— 記錄 2026-07-27 實測行為，唔係理想行為。
 *
 * `format.ts` 係全站金額嘅單點。一個壞咗嘅 FX rate 會同時打沉每一個金額欄位，
 * 爆炸半徑遠超改動本身。呢個 block 逐個 non-finite 輸入釘死現狀，
 * 令任何人改 `formatMoney()` 嘅 guard 都要喺呢度見紅先過得到。
 *
 * ⚠ 2026-07-27 PM 拍板修咗兩條：rate ≤ 0 一律 blank（假零價／負價源頭斷），
 *   `formatPercent()` 加 non-finite guard（NaN% 唔准出街）。
 *   仍然鎖住現狀嘅係 delta 拼接 fallback 字串（sign + 文案）同
 *   non-finite changePct 照計 —— 呢兩條爆得夠大聲，改唔改由 PM 決定。
 */
const withRate = (value: unknown): Record<Currency, number> =>
  ({ ...rates, HKD: value }) as unknown as Record<Currency, number>;

const readyAt = (value: number | null) => ({ value, status: "ready" as const, asOf: "2026-07-22" });

// `Number.isFinite` 攔得住嘅四種輸入：呢啲會退返 `copy[locale].status.unavailable`。
const blankingRates: ReadonlyArray<readonly [string, unknown]> = [
  ["NaN", NaN],
  ["Infinity", Infinity],
  ["-Infinity", -Infinity],
  ["undefined", undefined],
];

describe("non-finite FX rate", () => {
  it.each(blankingRates)("blanks every money surface when the rate is %s", (_label, badRate) => {
    const broken = withRate(badRate);
    expect(formatMoney(100, "HKD", broken, "en")).toBe("Not available");
    expect(formatMoney(1234567, "HKD", broken, "en", true)).toBe("Not available");
    expect(formatMetricMoney(readyAt(100), "HKD", broken, "en")).toBe("Not available");
    expect(formatTrackedSales(
      { valueUsd: readyAt(100), count: readyAt(3), coverage: "partial", asOf: "2026-07-22" },
      "HKD", broken, "en",
    )).toBe("Not available");
  });

  it("blanks a currency that is missing from the rate table entirely", () => {
    const partial = { USD: 1 } as unknown as Record<Currency, number>;
    expect(formatMoney(100, "HKD", partial, "en")).toBe("Not available");
  });

  it("uses the locale's own unavailable copy, not an English leak", () => {
    expect(formatMoney(100, "HKD", withRate(NaN), "ja")).toBe("データなし");
    expect(formatMoney(100, "HKD", withRate(NaN), "zh-TW")).toBe("暫無資料");
  });

  // 2026-07-27 修：rate 為 0 只可能係壞 FX feed，fail-closed blank，
  // 唔准出一個睇落好肯定嘅 HK$0.00 假零價。
  it("blanks a zero rate instead of rendering a fake zero price", () => {
    const zeroed = withRate(0);
    expect(formatMoney(100, "HKD", zeroed, "en")).toBe("Not available");
    expect(formatMoney(1234567, "HKD", zeroed, "en", true)).toBe("Not available");
    expect(formatMetricMoney(readyAt(100), "HKD", zeroed, "en")).toBe("Not available");
  });

  // 2026-07-27 修：負 rate 同 0 一齊擋，負價冇可能係真數。
  it("blanks a negative rate instead of rendering a negative price", () => {
    expect(formatMoney(100, "HKD", withRate(-7.8), "en")).toBe("Not available");
  });

  it("keeps non-money formatters independent of the rate table", () => {
    const broken = withRate(NaN);
    expect(formatPercent(readyAt(4.207), "en")).toBe("+4.21%");
    expect(formatInteger(1234, "en")).toBe("1,234");
    expect(metricTone(readyAt(4.2))).toBe("positive");
    // rate 壞咗都唔應該影響非金額欄位 —— USD 走同一張表但係 finite。
    expect(formatMoney(100, "USD", broken, "en")).toBe("$100");
  });
});

describe("delta formatting", () => {
  it("signs a positive and a negative move", () => {
    expect(formatDeltaMoney(readyAt(105), readyAt(5), "USD", rates, "en")).toBe("+$5");
    // baseline = 105 / (1 − 0.04762) = 110.25，所以跌幅係 5.25 唔係 5。
    expect(formatDeltaMoney(readyAt(105), readyAt(-4.762), "USD", rates, "en")).toBe("−$5.25");
  });

  it("returns null for a zero move and for a total wipeout", () => {
    expect(formatDeltaMoney(readyAt(100), readyAt(0), "USD", rates, "en")).toBeNull();
    // changePct <= -100 會令 baseline 除零，producer 唔應該出到，但 guard 喺度。
    expect(formatDeltaMoney(readyAt(105), readyAt(-100), "USD", rates, "en")).toBeNull();
  });

  it("returns null when either input is not ready or stale", () => {
    expect(formatDeltaMoney(readyAt(100), { value: 5, status: "accumulating", asOf: null }, "USD", rates, "en")).toBeNull();
    expect(formatDeltaMoney(readyAt(100), { value: 5, status: "unavailable", asOf: null }, "USD", rates, "en")).toBeNull();
    expect(formatDeltaMoney({ value: null, status: "ready", asOf: null }, readyAt(5), "USD", rates, "en")).toBeNull();
  });

  // 已知缺陷：delta 冇自己嘅 finite guard，佢淨係拼 sign + `formatMoney()` 個結果。
  // 所以 FX 一壞，個欄位唔係空白，係「+Not available」呢種符號撞文案。
  it("concatenates the sign onto the unavailable copy when the rate is non-finite (known gap)", () => {
    for (const [, badRate] of blankingRates) {
      expect(formatDeltaMoney(readyAt(105), readyAt(5), "HKD", withRate(badRate), "en")).toBe("+Not available");
    }
    expect(formatDeltaMoney(readyAt(105), readyAt(5), "HKD", withRate(NaN), "ja")).toBe("+データなし");
  });

  // 2026-07-27 起負 rate 喺 `formatMoney()` 度已被擋，雙符號 `+-HK$39` 冇咗源頭；
  // 剩返嘅係上面嗰個 sign + fallback 文案拼接模式（known gap）。
  it("falls into the sign-plus-copy concatenation when the rate is negative", () => {
    expect(formatDeltaMoney(readyAt(105), readyAt(5), "HKD", withRate(-7.8), "en")).toBe("+Not available");
  });

  // 已知缺陷：changePct 係 non-finite 都會計落去。
  // NaN 令 `delta >= 0` 做假 → 出「−」；Infinity 令 baseline 變 0 → 報成升足全額。
  it("does not guard a non-finite change percentage (known gap)", () => {
    expect(formatDeltaMoney(readyAt(105), readyAt(NaN), "USD", rates, "en")).toBe("−Not available");
    expect(formatDeltaMoney(readyAt(105), readyAt(Infinity), "USD", rates, "en")).toBe("+$105");
    expect(formatDeltaMoney(readyAt(NaN), readyAt(5), "USD", rates, "en")).toBe("−Not available");
  });
});

describe("non-finite metric values", () => {
  it("blanks non-finite money and integers", () => {
    expect(formatMoney(NaN, "USD", rates, "en")).toBe("Not available");
    expect(formatMoney(Infinity, "USD", rates, "en")).toBe("Not available");
    expect(formatMoney(null, "USD", rates, "en")).toBe("Not available");
    expect(formatInteger(NaN, "en")).toBe("Not available");
    expect(formatMetricInteger(readyAt(NaN), "en")).toBe("Not available");
    expect(formatMetricInteger(readyAt(Infinity), "en")).toBe("Not available");
  });

  it("keeps a non-finite metric tone neutral", () => {
    expect(metricTone(readyAt(NaN))).toBe("neutral");
    expect(metricTone(readyAt(Infinity))).toBe("neutral");
    expect(metricTone(readyAt(0))).toBe("neutral");
  });

  // 2026-07-27 修：`formatPercent()` 加咗 non-finite guard，
  // NaN%／Infinity% 唔會再原原本本印出街；合法嘅 0 照出 0.00%。
  it("blanks non-finite percentages instead of printing NaN%", () => {
    expect(formatPercent(readyAt(NaN), "en")).toBe("Not available");
    expect(formatPercent(readyAt(Infinity), "en")).toBe("Not available");
    expect(formatPercent(readyAt(-Infinity), "en")).toBe("Not available");
    expect(formatPercent(readyAt(0), "en")).toBe("0.00%");
    expect(formatPercent(readyAt(null), "en")).toBe("Not available");
  });

  it("switches money precision at the 100 boundary", () => {
    expect(formatMoney(99, "USD", rates, "en")).toBe("$99.00");
    expect(formatMoney(100, "USD", rates, "en")).toBe("$100");
    expect(formatMoney(1234567, "USD", rates, "en", true)).toBe("$1.23M");
  });
});
