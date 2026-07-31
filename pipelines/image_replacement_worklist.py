#!/usr/bin/env python3
"""Build a deterministic local replacement-image worklist from QC rejects."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

try:
    from .canonical_db_qc import ROOT
except ImportError:
    from canonical_db_qc import ROOT


DEFAULT_OUTPUT_ROOT = (
    ROOT / "data/runtime/private-reports/image-replacement"
)
SOURCE_PRIORITY = [
    "local_g10_snkrdunk_exact_language_printing",
    "purchased_google_drive_exact_language_printing",
    "snkrdunk_live_exact_language_printing",
    "tcgplayer_or_limitless_exact_clean_language_printing",
]


def normalized_language(value: object) -> str | None:
    language = str(value or "").strip().lower()
    return {"jp": "ja", "jpn": "ja", "eng": "en"}.get(
        language, language or None
    )


def load_local_g10_candidates() -> list[dict[str, Any]]:
    root = ROOT / "integrations/grade10/data/cards"
    image_root = ROOT / "integrations/grade10/data/images"
    rows: list[dict[str, Any]] = []
    for path in root.glob("*/*/asset_info.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        query = payload.get("assetQueryId") or {}
        source = str(query.get("source") or path.parent.parent.name)
        source_id = str(query.get("id") or path.parent.name)
        local_image = image_root / f"{source}_{source_id}.webp"
        rows.append(
            {
                "source": source,
                "sourceId": source_id,
                "collectorNumber": str(payload.get("cardId") or "").strip(),
                "language": normalized_language(payload.get("language")),
                "title": payload.get("fullTitle")
                or payload.get("displayTitle"),
                "setName": payload.get("setName"),
                "imageUrl": payload.get("image"),
                "assetInfo": str(path.relative_to(ROOT)),
                "localImage": (
                    str(local_image.relative_to(ROOT))
                    if local_image.is_file()
                    else None
                ),
            }
        )
    return rows


def build_worklist(
    canonical_report: Mapping[str, Any],
    prefilter_report: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    canonical = {
        int(card["variantId"]): card
        for card in canonical_report.get("cards") or []
    }
    items: list[dict[str, Any]] = []
    for row in prefilter_report.get("cards") or []:
        if str(row.get("decision") or "").startswith("pass_"):
            continue
        card = canonical.get(int(row["variantId"])) or {}
        identity = (card.get("facts") or {}).get("identity") or {}
        collector = str(identity.get("collectorNumber") or "").strip()
        language = normalized_language(identity.get("cardLanguage"))
        exact_local = [
            dict(candidate)
            for candidate in candidates
            if str(candidate.get("collectorNumber") or "").casefold()
            == collector.casefold()
            and normalized_language(candidate.get("language")) == language
        ]
        items.append(
            {
                "cardId": card.get("id") or row.get("cardId"),
                "variantId": row.get("variantId"),
                "assetId": row.get("assetId"),
                "tcg": row.get("tcg"),
                "collectorNumber": collector,
                "language": language,
                "setName": identity.get("set"),
                "edition": identity.get("edition"),
                "parallel": identity.get("parallel"),
                "finish": identity.get("finish"),
                "rejectDecision": row.get("decision"),
                "rejectReason": row.get("reason"),
                "sampleEvidence": row.get("sampleEvidence"),
                "sourcePriority": SOURCE_PRIORITY,
                "localExactCollectorLanguageCandidates": exact_local,
                "nextAction": (
                    "normalize_then_exact_printing_qc"
                    if row.get("decision") == "reject_geometry"
                    else "replace_with_exact_language_printing"
                ),
            }
        )
    return {
        "schemaVersion": 1,
        "sourceCanonicalRunId": canonical_report.get("runId"),
        "sourcePrefilterRunId": prefilter_report.get("runId"),
        "count": len(items),
        "items": items,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--canonical-report", type=Path, required=True)
    parser.add_argument("--prefilter-report", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    args = parser.parse_args(argv)
    document = build_worklist(
        json.loads(args.canonical_report.read_text(encoding="utf-8")),
        json.loads(args.prefilter_report.read_text(encoding="utf-8")),
        load_local_g10_candidates(),
    )
    destination = args.output_root / args.run_id / "worklist.json"
    destination.parent.mkdir(parents=True, exist_ok=False)
    destination.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    with_local = sum(
        bool(row["localExactCollectorLanguageCandidates"])
        for row in document["items"]
    )
    print(
        json.dumps(
            {
                "count": document["count"],
                "withLocalExactCollectorLanguageCandidate": with_local,
                "worklist": str(destination.resolve()),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
