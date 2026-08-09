#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A pop upsert must restate the grade label it means.

uq_market_grader_population is (variant_id, grader_code, source_code,
observed_date) -- top_grade_label is NOT in it. Two lanes land the same GemRate
day: one decomposes the grades and means PSA 10, one stores the undecomposed
highest grade and says 'top'. Whichever inserts first owns the row, and an
upsert that omits top_grade_label from its update list leaves the other lane's
label in place.

On 2026-08-09 that cost 131 product_ready cards their activation: the bridge
wrote each card's real PSA-10 population into a row still labelled 'top', the
population acceptance lane filters on the label, and S12 aborted saying the
cards had no population -- while the number sat in the table.

So: any statement that inserts an explicit '10' into this table must also
restate top_grade_label and estimated. Lanes that insert 'top' are exempt --
they are the less precise shape and must not be able to overwrite a '10' row's
meaning, only its own.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCANNED = [
    ROOT / "pipelines" / "rebuild_036.py",
    ROOT / "pipelines" / "collect_control.py",
    ROOT / "pipelines" / "db_runtime.py",
]
TABLE = "market_grader_population_observation"
FAILED: list[str] = []


def statements(text: str) -> list[str]:
    """每個 INSERT ... 到下一個 INSERT（或檔尾）為止，就係一條 statement 嘅範圍。"""
    marks = [m.start() for m in re.finditer(rf"INSERT (?:IGNORE )?INTO\s+{TABLE}", text)]
    return [text[a:b] for a, b in zip(marks, marks[1:] + [len(text)])]


found = 0
for path in SCANNED:
    text = path.read_text(encoding="utf-8")
    for chunk in statements(text):
        found += 1
        head, _, tail = chunk.partition("ON DUPLICATE KEY UPDATE")
        # 'top' lanes are the imprecise shape: they must NOT restate the label.
        means_psa10 = re.search(r"""['"]PSA['"]\s*,\s*['"]10['"]""", head) is not None
        label = f"{path.name}: insert #{found}"
        if not tail:
            continue  # plain INSERT / INSERT IGNORE: nothing can be clobbered
        restates = "top_grade_label=VALUES(top_grade_label)" in tail
        if means_psa10 and not restates:
            FAILED.append(f"{label} inserts PSA '10' but does not restate top_grade_label")
        elif means_psa10 and "estimated=VALUES(estimated)" not in tail:
            FAILED.append(f"{label} inserts PSA '10' but does not restate estimated")
        elif not means_psa10 and restates:
            FAILED.append(f"{label} is not the PSA-10 shape yet restates top_grade_label")
        else:
            print(f"ok   {label} ({'PSA 10' if means_psa10 else 'other label'})")

# A checker that found nothing to check is a checker that passes forever.
if found < 4:
    FAILED.append(f"expected at least 4 {TABLE} inserts across the lanes, found {found}")

print()
if FAILED:
    for line in FAILED:
        print(f"FAIL {line}")
    raise SystemExit(1)
print(f"all {found} population upserts state the grade label they mean")
