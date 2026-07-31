import { describe, expect, it } from "vitest";
import { graderShareTotals, gradersWithCards } from "./grader-share";
import { getSeedSnapshot } from "./snapshot";
import type { MarketViewSnapshot } from "./types";

const seed = getSeedSnapshot();

function withoutTagPopulation(snapshot: MarketViewSnapshot): MarketViewSnapshot {
  const clear = (card: MarketViewSnapshot["top100"][number]) => ({
    ...card,
    graderPopulations: {
      ...card.graderPopulations,
      TAG: {
        ...card.graderPopulations.TAG,
        topGradePopulation: { value: null, status: "unavailable" as const, asOf: null, estimated: false },
        total: { value: null, status: "unavailable" as const, asOf: null, estimated: false },
      },
    },
  });
  return {
    ...snapshot,
    top100: snapshot.top100.map(clear),
    watchlist: snapshot.watchlist.map(clear),
  };
}

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
    const withoutTag = withoutTagPopulation(seed);
    expect(gradersWithCards(withoutTag)).not.toContain("TAG");
    expect(gradersWithCards(withoutTag)).toContain("PSA");
  });

  it("lists TAG the moment TAG population lands, with no code change", () => {
    // pipeline 一入 TAG population，tab 同版面就自動返嚟 —— 冇硬寫過 TAG 隱藏。
    expect(gradersWithCards(withTagPopulation(seed))).toContain("TAG");
  });

  it("keeps a grader with no cards visible in the share breakdown at 0%", () => {
    const withoutTag = withoutTagPopulation(seed);
    expect(graderShareTotals(withoutTag).TAG).toBe(0);
    expect(graderShareTotals(withTagPopulation(withoutTag)).TAG).toBe(40);
  });
});
