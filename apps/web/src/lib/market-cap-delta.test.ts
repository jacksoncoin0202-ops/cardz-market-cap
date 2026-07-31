import { describe, expect, it } from "vitest";
import { composeChangePct } from "@cardz/market-data";
import { getSeedSnapshot } from "./snapshot";
import { formatDeltaMoney } from "./format";

/*
 * 市值 = 價 × POP。以前 `rankings.tsx` 直接攞 `windows[w].changePct`（純價格變動）
 * 當市值變動用，漏咗 POP 嗰截：
 *   - POP 只升唔跌，所以幅度永遠低估；
 *   - 價格跌而 POP 升到蓋得過嗰陣，(1+Δ價)(1+ΔPOP)−1 由負變正 —— 箭嘴會**指錯方向**。
 * 呢個 test 鎖死正確嗰條式同埋 fail-closed 行為。
 */

const rates = { USD: 1, HKD: 7.8, CNY: 7.2, GBP: 0.78, TWD: 32, JPY: 157, KRW: 1380 };

function money(value: number | null, changePct: { value: number | null; status: string; asOf: string | null }) {
  return formatDeltaMoney(
    { value, status: "ready", asOf: "2026-07-19T00:00:00Z" },
    changePct as never,
    "USD",
    rates,
    "en",
  );
}

describe("market cap delta", () => {
  const snapshot = getSeedSnapshot();

  it("compounds price change with PSA population change instead of reusing the price change", () => {
    const card = snapshot.top100.find((candidate) => {
      const window = candidate.windows["30d"];
      const populationChange = candidate.graderPopulations.PSA.topGradePopulationChangePct["30d"];
      return window.changePct.value !== null && populationChange.value !== null;
    });
    expect(card).toBeDefined();
    const window = card!.windows["30d"];
    const populationChange = card!.graderPopulations.PSA.topGradePopulationChangePct["30d"];

    expect(window.changePct.value).not.toBeNull();
    expect(populationChange.value).not.toBeNull();
    // (1 + Δ價/100)(1 + ΔPOP/100) − 1
    const expected = composeChangePct(window.changePct, populationChange);
    expect(window.marketCapChangePct.value).toBe(expected.value);
    // 唔可以再係價格變動本身
    expect(window.marketCapChangePct.value).not.toBeCloseTo(window.changePct.value as number, 9);
  });

  it("matches the composition formula for every complete seed window", () => {
    let compared = 0;
    for (const card of snapshot.top100) {
      for (const period of ["1d", "7d", "30d"] as const) {
        const window = card.windows[period];
        const populationChange = card.graderPopulations.PSA.topGradePopulationChangePct[period];
        if (window.changePct.value === null || populationChange.value === null) continue;
        const expected = composeChangePct(window.changePct, populationChange);
        expect(window.marketCapChangePct.value).toBeCloseTo(expected.value as number, 9);
        compared += 1;
      }
    }
    expect(compared).toBeGreaterThan(0);
  });

  it("flips the arrow when a falling price is outrun by a rising population", () => {
    // 價跌 0.08%、POP 升 2.66% ⇒ 市值實際升。舊碼會顯示跌箭嘴。
    const flipped = snapshot.top100.filter((card) => {
      const window = card.windows["30d"];
      const price = window.changePct.value;
      const cap = window.marketCapChangePct.value;
      return price !== null && cap !== null && price < 0 && cap > 0;
    });
    expect(flipped.length).toBeGreaterThan(0);
    for (const card of flipped) {
      const window = card.windows["30d"];
      const wrong = money(card.marketCap.value, window.changePct);
      const right = money(card.marketCap.value, window.marketCapChangePct);
      expect(wrong?.startsWith("−")).toBe(true);
      expect(right?.startsWith("+")).toBe(true);
    }
  });

  it("never reports a bigger market cap swing than the price swing when population only rises", () => {
    for (const card of snapshot.top100) {
      for (const window of ["1d", "7d", "30d"] as const) {
        const metrics = card.windows[window];
        const populationChange = card.graderPopulations.PSA.topGradePopulationChangePct[window];
        if (metrics.marketCapChangePct.value === null || metrics.changePct.value === null) continue;
        if (populationChange.value === null) continue;
        // POP 係存量：只會令市值變動比純價格變動更加向上。
        expect(metrics.marketCapChangePct.value).toBeGreaterThanOrEqual(metrics.changePct.value - 1e-9);
      }
    }
  });

  it("fails closed rather than falling back to the price change", () => {
    const price = { value: -5.32, status: "ready" as const, asOf: "2026-07-19T00:00:00Z" };
    for (const missing of [
      { value: null, status: "accumulating" as const, asOf: null },
      { value: null, status: "unavailable" as const, asOf: null },
    ]) {
      const composed = composeChangePct(price, missing);
      expect(composed.value).toBeNull();
      expect(composed.status).toBe(missing.status);
      // 冇 POP 數就唔顯示 —— 唔准借價格變動頂替
      expect(money(148141528, composed)).toBeNull();
    }
  });

  it("keeps the composed metric no fresher than its stalest input", () => {
    const composed = composeChangePct(
      { value: 2, status: "stale", asOf: "2026-07-19T00:00:00Z" },
      { value: 3, status: "ready", asOf: "2026-07-22T00:00:00Z" },
    );
    expect(composed.status).toBe("stale");
    expect(composed.asOf).toBe("2026-07-19T00:00:00Z");
  });

  it("does not derive a sales delta from the price change", () => {
    // 成交金額同價格變動 % 冇任何數學關係。producer 未出真數之前一律空白。
    for (const card of snapshot.top100.slice(0, 20)) {
      const metrics = card.windows["30d"];
      if (metrics.trackedSalesChangePct.value === null) {
        expect(money(metrics.trackedSales.valueUsd.value, metrics.trackedSalesChangePct)).toBeNull();
      }
    }
  });
});
