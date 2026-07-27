import { describe, expect, it } from "vitest";
import { displayTopGrade } from "./grade-label";
import { graders } from "./types";

describe("displayTopGrade", () => {
  it("resolves the raw 'top' enum to each grader's designation", () => {
    expect(graders.map((grader) => displayTopGrade(grader, "top"))).toEqual([
      "10", "Black Label", "10", "10", "10P",
    ]);
  });

  it("never leaks the raw enum for any grader or casing", () => {
    for (const grader of graders) {
      for (const raw of ["top", "TOP", " Top "]) {
        expect(displayTopGrade(grader, raw).toLowerCase()).not.toBe("top");
      }
    }
  });

  it("passes a real upstream label through untouched", () => {
    expect(displayTopGrade("BGS", "Black Label")).toBe("Black Label");
    expect(displayTopGrade("PSA", "10")).toBe("10");
    expect(displayTopGrade("TAG", "10P")).toBe("10P");
  });

  it("keeps an absent label empty so callers apply their own fallback", () => {
    expect(displayTopGrade("PSA", "")).toBe("");
    expect(displayTopGrade("PSA", null)).toBe("");
    expect(displayTopGrade("PSA", undefined)).toBe("");
  });
});
