import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { graders } from "@/lib/types";
import { getSeedSnapshot } from "@/lib/snapshot";
import { aggregateGraderStats, GradingPulse } from "./grading-pulse";

describe("GradingPulse", () => {
  it("renders a missing grader delta as accumulating, never as +0", () => {
    const card = structuredClone(getSeedSnapshot().top100[0]);
    for (const grader of graders) {
      card.graderPopulations[grader].topGradePopulationChangePct["1d"] = {
        value: null,
        status: "accumulating",
        asOf: null,
      };
    }

    const stats = aggregateGraderStats([card], "1d");
    expect(stats.every((stat) => stat.change.value === null && stat.change.status === "accumulating")).toBe(true);

    const markup = renderToStaticMarkup(
      <GradingPulse cards={[card]} locale="en" period="1d" />,
    );
    expect(markup).toContain("Accumulating");
    expect(markup).not.toContain("+0");
  });
});
