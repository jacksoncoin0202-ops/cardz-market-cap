#!/usr/bin/env python3
"""Turn private GemRate search evidence into an API population worklist.

Search-level ``gems`` and ``total_population`` are discovery signals only.
They are never written as canonical PSA 10 population. Ranking remains blocked
until ``gemrate_source.py api-dump`` returns the card population payload.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SEARCH = ROOT / "data/runtime/private-source-map/one-piece-gemrate-discovery.json"
DEFAULT_OUT = ROOT / "data/runtime/private-source-map/one-piece-gemrate-candidates.json"
DEFAULT_IDS = ROOT / "data/runtime/private-source-map/one-piece-gemrate-population-worklist.txt"


def is_one_piece(result: Mapping[str, Any]) -> bool:
    haystack = " ".join(
        str(result.get(key) or "") for key in ("description", "set_name", "matched_query")
    ).casefold()
    return "one piece" in haystack or any(code in haystack for code in ("op01", "op02", "op03", "op04", "op05", "op06", "op07", "op08", "op09", "op10", "op11", "op12", "op13", "op14", "eb01", "eb02", "eb03"))


def build_candidates(document: Mapping[str, Any]) -> dict[str, Any]:
    results = document.get("results")
    if not isinstance(results, list):
        raise ValueError("GemRate discovery results are missing")
    chosen: dict[str, dict[str, Any]] = {}
    rejected = 0
    for result in results:
        if not isinstance(result, Mapping) or not is_one_piece(result):
            rejected += 1
            continue
        gemrate_id = str(result.get("gemrate_id") or "")
        if len(gemrate_id) != 40 or any(char not in "0123456789abcdef" for char in gemrate_id.casefold()):
            rejected += 1
            continue
        chosen[gemrate_id] = {
            "gemrateId": gemrate_id,
            "nameEvidence": result.get("name"),
            "descriptionEvidence": result.get("description"),
            "setEvidence": result.get("set_name"),
            "collectorNumberEvidence": result.get("card_number"),
            "parallelEvidence": result.get("parallel"),
            # These are copied only when the discovery transport explicitly
            # supplies them.  Missing values are intentionally not converted
            # into base-edition/foil guesses by the identity crosswalk.
            **({"languageEvidence": result.get("language")} if "language" in result else {}),
            **({"editionEvidence": result.get("edition")} if "edition" in result else {}),
            **({"finishEvidence": result.get("finish")} if "finish" in result else {}),
            "searchPopulationType": result.get("population_type"),
            "searchTotalPopulationSignal": result.get("total_population"),
            "searchGemSignal": result.get("gems"),
            "populationStatus": "api_required",
            "rankingStatus": "blocked_until_gemrate_population",
        }
    ordered = sorted(
        chosen.values(),
        key=lambda row: (
            -float(row["searchTotalPopulationSignal"] or 0),
            -float(row["searchGemSignal"] or 0),
            row["gemrateId"],
        ),
    )
    payload = {
        "schemaVersion": 1,
        "tcg": "one-piece",
        "populationAuthority": "gemrate",
        "candidateCount": len(ordered),
        "rejectedCount": rejected,
        "candidates": ordered,
    }
    payload["payloadSha256"] = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    payload["generatedAt"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Build One Piece GemRate population worklist")
    parser.add_argument("--search-results", type=Path, default=DEFAULT_SEARCH)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--ids-out", type=Path, default=DEFAULT_IDS)
    args = parser.parse_args()
    source = json.loads(args.search_results.read_text(encoding="utf-8"))
    document = build_candidates(source)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    args.ids_out.write_text(
        "".join(f"{row['gemrateId']}\n" for row in document["candidates"]),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "candidateCount": document["candidateCount"],
                "populationAuthority": document["populationAuthority"],
                "rankingStatus": "blocked_until_gemrate_population",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
