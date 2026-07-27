"""Re-measure the 28 English long-form stories written on 2026-07-27.

Validates every summary_en.json under data/editorial/long-form-stories/ against
the G10 standard (loader-required `summary` key, the four section headings, the
1,938-char G10 floor, contentSha256 integrity) and emits manifest.csv.

The tree is laid out the way pipelines/g10_research_ingest.py expects:
    long-form-stories/altxyz/<ebay external_entity_id>/summary_en.json
so the existing ingest loads it with --g10-root and no code change.

Run from anywhere:
    python -X utf8 docs/evidence/2026-07-27-stories/verify_stories.py

Exit 0 = all pass, 1 = at least one file failed validation.
"""

from __future__ import annotations

import csv
import hashlib
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[3]
STORY_DIR = ROOT / "data" / "editorial" / "long-form-stories"
MANIFEST = pathlib.Path(__file__).resolve().parent / "manifest.csv"

REQUIRED_SECTIONS = (
    "### Basic Info",
    "### Community Pulse",
    "### Card Fun Facts",
    "### Summary (TLDR)",
)
REQUIRED_KEYS = (
    "summary",
    "variantId",
    "opaqueId",
    "locale",
    "tcg",
    "cardLanguage",
    "canonicalName",
    "setNameCatalog",
    "collectorNumber",
    "contentSha256",
)
G10_FLOOR = 1938


def main() -> int:
    paths = sorted(
        STORY_DIR.glob("*/*/summary_en.json"),
        key=lambda p: json.loads(p.read_text(encoding="utf-8"))["variantId"],
    )
    if not paths:
        print(f"no story files under {STORY_DIR}")
        return 1

    rows: list[dict[str, object]] = []
    failures: list[str] = []

    for path in paths:
        label = f"{path.parent.parent.name}/{path.parent.name}"
        doc = json.loads(path.read_text(encoding="utf-8"))
        missing_keys = [k for k in REQUIRED_KEYS if k not in doc]
        if missing_keys:
            failures.append(f"{label}: missing keys {missing_keys}")
            continue

        text = doc["summary"]
        if not isinstance(text, str) or not text.strip():
            failures.append(f"{label}: summary is not a non-empty string")
            continue

        missing_sections = [s for s in REQUIRED_SECTIONS if s not in text]
        if missing_sections:
            failures.append(f"{label}: missing sections {missing_sections}")
        if len(text) < G10_FLOOR:
            failures.append(f"{label}: {len(text)} chars is under the {G10_FLOOR} G10 floor")
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if digest != doc["contentSha256"]:
            failures.append(f"{label}: contentSha256 does not match summary")

        rows.append(
            {
                "variantId": doc["variantId"],
                "tcg": doc["tcg"],
                "cardLanguage": doc["cardLanguage"],
                "collectorNumber": doc["collectorNumber"],
                "canonicalName": doc["canonicalName"],
                "setNameCatalog": doc["setNameCatalog"],
                "chars": len(text),
                "words": len(text.split()),
                "opaqueId": doc["opaqueId"],
                "ebayExternalEntityId": path.parent.name,
                "contentSha256": digest,
                "file": path.relative_to(ROOT).as_posix(),
            }
        )

    with MANIFEST.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    chars = sorted(int(r["chars"]) for r in rows)
    words = sorted(int(r["words"]) for r in rows)
    mid = len(chars) // 2
    median = chars[mid] if len(chars) % 2 else (chars[mid - 1] + chars[mid]) / 2
    op = sum(1 for r in rows if r["tcg"] == "one-piece")

    print(f"files          {len(rows)}")
    print(f"one-piece      {op}")
    print(f"pokemon        {len(rows) - op}")
    print(f"chars          min={chars[0]} median={median} max={chars[-1]}")
    print(f"words          min={words[0]} max={words[-1]}")
    print(f"manifest       {MANIFEST}")

    if failures:
        print("\nFAILURES")
        for line in failures:
            print(f"  {line}")
        return 1
    print("\nall files pass structure + floor + sha256 checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
