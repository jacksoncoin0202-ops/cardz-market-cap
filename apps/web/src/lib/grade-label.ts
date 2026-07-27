import type { Grader } from "./types";

// The population feed ships `topGrade` straight from `top_grade_label`. Some rows carry the
// raw enum "top" instead of the grader's actual top-grade designation, so the label has to be
// resolved here in the display layer — the database column feeds identity and must not be
// rewritten. The per-grader designation is taken from the producer that already owns it,
// `pipelines/g10_public_snapshot.py:918`:
//   labels = {"PSA": "10", "BGS": "Black Label", "CGC": "10", "SGC": "10", "TAG": "10P"}
// The published seed agrees exactly: for every card carrying "top" upstream, the seed shows
// BGS "Black Label", TAG "10P" and PSA/CGC/SGC "10".
const TOP_GRADE_LABEL: Record<Grader, string> = {
  PSA: "10",
  BGS: "Black Label",
  CGC: "10",
  SGC: "10",
  TAG: "10P",
};

// Only the "top" sentinel is replaced. A real upstream label is passed through untouched, and
// an empty label stays empty so callers keep their own unavailable fallback.
export function displayTopGrade(grader: Grader, topGrade: string | null | undefined): string {
  const label = (topGrade ?? "").trim();
  return label.toLowerCase() === "top" ? TOP_GRADE_LABEL[grader] : label;
}
