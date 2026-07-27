#!/usr/bin/env python3
"""磁碟側盤點：G10 scraper 手上有咩逐卡數據，而 DB 可能冇。

唯讀。只數檔案同記錄條數，除咗 stdout 唔寫任何嘢。

    python -X utf8 docs/evidence/2026-07-27-data-inventory/probe_disk.py
"""
from __future__ import annotations

import glob
import json
import os
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
G10 = ROOT.parent / "grade10-scraper" / "data"


def load(path: str):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return None


def report_sales(pattern: str, label: str) -> None:
    files = sorted(glob.glob(str(G10 / "cards" / "*" / "*" / pattern)))
    rows = 0
    nonempty = 0
    iso = 0
    relative = 0
    other = 0
    for path in files:
        doc = load(path)
        if not isinstance(doc, dict):
            continue
        history = doc.get("saleHistory") or []
        history = history + (doc.get("recentHistory") or [])
        if history:
            nonempty += 1
        rows += len(history)
        for entry in history:
            date = str(entry.get("date", ""))
            if date[:4].isdigit() and "-" in date:
                iso += 1
            elif "ago" in date:
                relative += 1
            else:
                other += 1
    print(
        f"{label:<24} files={len(files):>5} with_sales={nonempty:>5} "
        f"records={rows:>7}  iso={iso} relative={relative} other={other}"
    )


def report_populations() -> None:
    files = sorted(glob.glob(str(G10 / "cards" / "*" / "*" / "populations.json")))
    graders = Counter()
    cards_with_any = 0
    pairs = 0
    for path in files:
        doc = load(path)
        if not isinstance(doc, dict):
            continue
        pop = doc.get("population") or []
        if pop:
            cards_with_any += 1
        for entry in pop:
            graders[entry.get("gradeName")] += 1
            pairs += 1
    print(f"populations.json         files={len(files)} with_data={cards_with_any} grader_rows={pairs}")
    print(f"  per-grader: {dict(graders)}")


def report_tree() -> None:
    total = 0
    by_name = Counter()
    for base, _dirs, names in os.walk(G10):
        for name in names:
            total += 1
            by_name[name] += 1
    print(f"grade10-scraper/data total files = {total}")
    for name, count in by_name.most_common(30):
        print(f"  {count:>5}  {name}")


if __name__ == "__main__":
    print(f"# G10 root: {G10}")
    print()
    report_populations()
    print()
    for pattern, label in [
        ("ebay_PSA_10.json", "ebay PSA 10"),
        ("ebay_PSA_9.json", "ebay PSA 9"),
        ("ebay_BGS_10.json", "ebay BGS 10"),
        ("ebay_BGS_BL.json", "ebay BGS BL"),
        ("ebay_CGC_10.json", "ebay CGC 10"),
    ]:
        report_sales(pattern, label)
    print()
    for grade in sorted(
        {
            Path(p).stem
            for p in glob.glob(str(G10 / "cards" / "*" / "*" / "apparel_grade_*.json"))
        }
    ):
        report_sales(grade + ".json", grade)
    print()
    report_tree()
