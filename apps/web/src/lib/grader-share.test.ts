import { describe, expect, it } from "vitest";
import { graderShareTotals, gradersWithCards } from "./grader-share";
import { getSeedSnapshot } from "./snapshot";
import type { MarketViewSnapshot } from "./types";

const seed = getSeedSnapshot();

function withTagPopulation(snapshot: MarketViewSnapshot): MarketViewSnapshot {
  const [first, ...rest] = snapshot.top100;
  return {
    ...snapshot,
    top100: [
      {
        ...first,
        graderPopulations: {
          ...first.graderPopulations,
          TAG: {
            ...first.graderPopulations.TAG,
            topGrade: "10",
            topGradePopulation: { value: 12, status: "ready", asOf: "2026-07-26", estimated: false },
            total: { value: 40, status: "ready", asOf: "2026-07-26", estimated: false },
          },
        },
      },
      ...rest,
    ],
  };
}

describe("grader share", () => {
  it("computes one whole-market denominator, not a per-grader one", () => {
    const totals = graderShareTotals(seed);
    const sum = Object.values(totals).reduce((a, b) => a + b, 0);
    expect(sum).toBeGreaterThan(0);
    expect(totals.PSA).toBeGreaterThan(0);
  });

  it("does not list a grader that has no eligible cards", () => {
    // 呢個係 /graders/tag 死版嘅根源：TAG 一張卡都入唔到榜。
    expect(gradersWithCards(seed)).not.toContain("TAG");
    expect(gradersWithCards(seed)).toContain("PSA");
  });

  it("lists TAG the moment TAG population lands, with no code change", () => {
    // pipeline 一入 TAG population，tab 同版面就自動返嚟 —— 冇硬寫過 TAG 隱藏。
    expect(gradersWithCards(withTagPopulation(seed))).toContain("TAG");
  });

  it("keeps a grader with no cards visible in the share breakdown at 0%", () => {
    // 分母係全市場，所以 TAG 冇數據只會係 0%，唔會拖冧成個 widget。
    expect(graderShareTotals(seed).TAG).toBe(0);
    expect(graderShareTotals(withTagPopulation(seed)).TAG).toBe(40);
  });
});
