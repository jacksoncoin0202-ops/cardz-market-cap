import { graders, type Grader, type MarketViewSnapshot } from "./types";

/*
 * 市佔圓環語意上係跨評級機構嘅比較，所以分母必須係同一個全市場數字，
 * 五個 grader 版面睇到嘅應該一模一樣。
 *
 * 原本 `GraderPage` 將 `graderSnapshot()` 篩完之後嘅 snapshot 直接餵俾圓環，
 * 而 `graderSnapshot()` 只保留該 grader 有 top-grade population 嘅卡，
 * 於是分母跟住當前版面郁 —— 實測同一個 widget 喺五版報五個唔同市佔
 * （PSA 87.5% / BGS 87.4% / CGC 85.1% / SGC 87.7%），而 TAG 因為零張卡入到榜，
 * 分母變 0，五條全部 0.0%、中心總數變「—」。
 *
 * 修法：喺 server 端用未篩過嘅市場 snapshot 計好總數先傳落去。
 */
export function graderShareTotals(snapshot: MarketViewSnapshot): Record<Grader, number> {
  const totals = Object.fromEntries(graders.map((grader) => [grader, 0])) as Record<Grader, number>;
  for (const card of snapshot.top100) {
    for (const grader of graders) {
      const metric = card.graderPopulations[grader].total;
      if (metric.status === "ready" && metric.value !== null && Number.isFinite(metric.value)) {
        totals[grader] += metric.value;
      }
    }
  }
  return totals;
}

/*
 * 邊個 grader 真係有版面可以睇。條件同 `graderSnapshot()` 嘅入榜條件逐隻字一樣，
 * 所以「有 tab」同「有內容」唔會講唔同嘢：pipeline 一日冇 TAG population，
 * TAG tab 就自動唔出；數據一入到 snapshot，tab 同版面同一刻返嚟，唔使改代碼。
 */
export function gradersWithCards(snapshot: MarketViewSnapshot): Grader[] {
  const cards = [...snapshot.top100, ...snapshot.watchlist];
  return graders.filter((grader) =>
    cards.some((card) => card.graderPopulations[grader].topGradePopulation.value !== null),
  );
}
