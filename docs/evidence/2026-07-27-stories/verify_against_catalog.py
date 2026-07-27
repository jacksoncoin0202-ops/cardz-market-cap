"""Diff the 28 story files field-by-field against the catalog_variant DB dump.

This is the runnable proof of the truthfulness red line: every card name, set
label, collector number and opaque_id printed inside a story must come from
`catalog_variant`, not from the author.

Reads the frozen dump `catalog-fields.json` (produced by the SELECT recorded in
FINDING.md) and compares it against every summary_en.json under
data/editorial/long-form-stories/.

Run from anywhere:
    python -X utf8 docs/evidence/2026-07-27-stories/verify_against_catalog.py

Exit 0 = zero mismatches, 1 = at least one field disagrees with the DB.
"""

from __future__ import annotations

import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
STORY_DIR = ROOT / "data" / "editorial" / "long-form-stories"
DUMP = HERE / "catalog-fields.json"

# story doc key -> catalog_variant column
FIELD_MAP = {
    "tcg": "tcg_code",
    "cardLanguage": "card_language",
    "canonicalName": "canonical_name",
    "setNameCatalog": "set_name",
    "collectorNumber": "collector_number",
    "opaqueId": "opaque_id",
}


def main() -> int:
    raw = json.loads(DUMP.read_text(encoding="utf-8"))
    catalog = {int(r["variant_id"]): r for r in raw[0]["rows"]}

    paths = sorted(STORY_DIR.glob("*/*/summary_en.json"))
    if not paths:
        print(f"no story files under {STORY_DIR}")
        return 1

    mismatches: list[str] = []
    checked = 0

    for path in paths:
        doc = json.loads(path.read_text(encoding="utf-8"))
        vid = int(doc["variantId"])
        row = catalog.get(vid)
        if row is None:
            mismatches.append(f"variant {vid}: not present in {DUMP.name}")
            continue
        for doc_key, col in FIELD_MAP.items():
            checked += 1
            if doc[doc_key] != row[col]:
                mismatches.append(
                    f"variant {vid} {doc_key}: story={doc[doc_key]!r} db={row[col]!r}"
                )

    print(f"story files    {len(paths)}")
    print(f"catalog rows   {len(catalog)}")
    print(f"fields checked {checked}")
    print(f"mismatches     {len(mismatches)}")

    if mismatches:
        print("\nMISMATCHES")
        for line in mismatches:
            print(f"  {line}")
        return 1
    print("\nevery name / set label / collector number / opaque_id matches catalog_variant")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
