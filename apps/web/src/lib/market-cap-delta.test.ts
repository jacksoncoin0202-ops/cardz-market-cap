import { describe, expect, it } from "vitest";
import { composeChangePct } from "@cardz/market-data";
import { borrowWindowMetric, getSeedSnapshot } from "./snapshot";
import { formatDeltaMoney } from "./format";
import type { MarketMetric, MarketWindow } from "./types";

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
    const card = snapshot.top100[0];
    const window = card.windows["30d"];
    const populationChange = card.graderPopulations.PSA.topGradePopulationChangePct["30d"];

    expect(window.changePct.value).not.toBeNull();
    expect(populationChange.value).not.toBeNull();
    // (1 + Δ價/100)(1 + ΔPOP/100) − 1
    const expected =
      ((1 + (window.changePct.value as number) / 100) * (1 + (populationChange.value as number) / 100) - 1) * 100;
    expect(window.marketCapChangePct.value).toBeCloseTo(expected, 9);
    // 唔可以再係價格變動本身
    expect(window.marketCapChangePct.value).not.toBeCloseTo(window.changePct.value as number, 9);
  });

  it("matches the audited market cap delta for the four sampled homepage ranks", () => {
    // 實測基準（30d 窗口，seed snapshot）：左邊係舊碼攞價格 changePct 推出嚟嘅假數，
    // 右邊係 (1+Δ價)(1+ΔPOP)−1 推出嚟嘅真數。rank 2/4 連正負都掉轉咗。
    const audited: Record<number, { wrong: number; right: number }> = {
      1: { wrong: -8_323_964, right: -2_595_940 },
      2: { wrong: -76_503, right: 2_401_322 },
      4: { wrong: -571_829, right: 461_525 },
      7: { wrong: -4_567_217, right: -1_774_278 },
    };
    for (const [rank, expected] of Object.entries(audited)) {
      const card = snapshot.top100.find((entry) => entry.rank === Number(rank));
      expect(card, `rank ${rank} missing`).toBeDefined();
      const window = card!.windows["30d"];
      const cap = card!.marketCap.value as number;
      const implied = (change: number | null) => cap - cap / (1 + (change as number) / 100);
      expect(implied(window.changePct.value)).toBeCloseTo(expected.wrong, -1);
      expect(implied(window.marketCapChangePct.value)).toBeCloseTo(expected.right, -1);
    }
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
        // 借咗窗口嘅值唔同源，唔可以攞嚟做同窗口不等式比較。
        if (metrics.marketCapChangePct.fallbackWindow || metrics.changePct.fallbackWindow || populationChange.fallbackWindow) continue;
        // POP 係存量：只會令市值變動比純價格變動更加向上。
        expect(metrics.marketCapChangePct.value).toBeGreaterThanOrEqual(metrics.changePct.value - 1e-9);
      }
    }
  });

  it("fails closed rather than falling back to the price change", () => {
    // composeChangePct 本身維持 fail-closed。2026-07-27 嘅上線頂檔係之後喺 view 層
    // 跨窗口借成個結果（見下面 borrow test），唔係喺公式入面借同窗口嘅價格變動。
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

  it("borrows the freshest displayable window (1d→7d→30d) instead of leaving the cell blank", () => {
    // 2026-07-27 用戶決定（同日糾正過方向）：上線畫面優先，空窗口借數
    // donor 順序固定 1d→7d→30d（最新鮮優先，跳過自己）。
    // 借咗嘅值標 stale + fallbackWindow，方便日後接返真數據源時還原 fail-closed。
    const metrics: Record<MarketWindow, MarketMetric<number>> = {
      "1d": { value: 1.2, status: "ready", asOf: "2026-07-21T00:00:00Z" },
      "7d": { value: null, status: "accumulating", asOf: null },
      "30d": { value: null, status: "accumulating", asOf: null },
    };
    const pick = (window: MarketWindow) => metrics[window];
    expect(borrowWindowMetric("30d", pick)).toMatchObject({ value: 1.2, status: "stale", fallbackWindow: "1d" });
    // 7d 都有數，30d 照借 1d —— 最新鮮優先，唔係揀最近長度
    metrics["7d"] = { value: -0.4, status: "ready", asOf: "2026-07-15T00:00:00Z" };
    expect(borrowWindowMetric("30d", pick)).toMatchObject({ value: 1.2, status: "stale", fallbackWindow: "1d" });
    // 1d 冇數先輪到 7d
    metrics["1d"] = { value: null, status: "accumulating", asOf: null };
    expect(borrowWindowMetric("30d", pick)).toMatchObject({ value: -0.4, status: "stale", fallbackWindow: "7d" });
    // 本窗口有數就原封不動，唔准掛 fallbackWindow
    metrics["30d"] = { value: 5, status: "ready", asOf: "2026-07-22T00:00:00Z" };
    expect(borrowWindowMetric("30d", pick)).toEqual(metrics["30d"]);
    // 1d 空都可以問長窗口借：7d 冇就借 30d
    expect(borrowWindowMetric("1d", pick)).toMatchObject({ value: -0.4, status: "stale", fallbackWindow: "7d" });
    metrics["7d"] = { value: null, status: "accumulating", asOf: null };
    expect(borrowWindowMetric("1d", pick)).toMatchObject({ value: 5, status: "stale", fallbackWindow: "30d" });
    // 三個窗口都冇 → 照舊 fail-closed 出 null，唔准作數
    const empty = (): MarketMetric<number> => ({ value: null, status: "unavailable", asOf: null });
    expect(borrowWindowMetric("30d", empty).value).toBeNull();
    expect(borrowWindowMetric("1d", empty).value).toBeNull();
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
