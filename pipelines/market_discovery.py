#!/usr/bin/env python3
"""Build a private broad-roster diff for the bounded CARDZ market universe.

This lane is intentionally cheap: it consumes current constituent indexes and
does not download full history or images. Source-exact rows outside the active
lock are proposals only; they cannot enter a public ranking before canonical
identity, population, price freshness and image gates pass.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from g10_ingest import private_source_ref


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "integrations" / "grade10" / "data"
DEFAULT_ACTIVE = ROOT / "data" / "runtime" / "private-source-map" / "tracked-universe.json"
DEFAULT_OUT = ROOT / "data" / "runtime" / "private-source-map" / "discovery-radar.json"
INDEXES = (("pokemon", "ptcg"), ("one-piece", "opcg"))


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_language(value: object) -> str:
    aliases = {"EN": "en", "JP": "ja", "JA": "ja", "KR": "ko", "KO": "ko", "CN": "zhCN", "TC": "zhTW"}
    return aliases.get(str(value or "").upper(), "unknown")


def source_reference(row: Mapping[str, Any]) -> tuple[str, str] | None:
    return private_source_ref(row)


def build_radar(source_root: Path, active_path: Path, captured_at: datetime) -> dict[str, Any]:
    active = read_json(active_path)
    if not isinstance(active, Mapping) or not isinstance(active.get("cards"), list):
        raise RuntimeError("active-universe lock is invalid")
    active_refs = {
        (str(card.get("canonicalSourceCode") or "").casefold(), str(card.get("canonicalExternalId") or ""))
        for card in active["cards"]
        if isinstance(card, Mapping)
    }
    roster: list[dict[str, Any]] = []
    rejected = 0
    for tcg, index_name in INDEXES:
        path = source_root / "index" / index_name / "constituents.json"
        if not path.is_file():
            raise RuntimeError(f"discovery index is missing: {path}")
        document = read_json(path)
        rows = document.get("rows") if isinstance(document, Mapping) else None
        if not isinstance(rows, list):
            raise RuntimeError(f"discovery index is invalid: {path}")
        for item in rows:
            if not isinstance(item, Mapping):
                rejected += 1
                continue
            reference = source_reference(item)
            if reference is None:
                rejected += 1
                continue
            rank = item.get("rank")
            raw_price = item.get("priceUsd")
            change = item.get("change30dPct")
            if not isinstance(rank, int) or rank <= 0:
                rejected += 1
                continue
            price = float(raw_price) if isinstance(raw_price, (int, float)) and raw_price > 0 else None
            change_value = (
                float(change)
                if price is not None and isinstance(change, (int, float))
                else None
            )
            candidate_key = hashlib.sha256(f"{reference[0]}:{reference[1]}".encode("utf-8")).hexdigest()
            outside = reference not in active_refs
            high_potential = outside and (
                rank <= 130 or (change_value is not None and change_value >= 20)
            )
            roster.append(
                {
                    "candidateKey": candidate_key,
                    "tcg": tcg,
                    "language": normalize_language(item.get("lang")),
                    "sourceCode": reference[0],
                    "externalId": reference[1],
                    "name": str(item.get("name") or ""),
                    "setName": str(item.get("setName") or ""),
                    "observedRank": rank,
                    "priceUsd": price,
                    "priceStatus": "ready" if price is not None else "unavailable",
                    "change30dPct": change_value,
                    "active": not outside,
                    "identityStatus": "active_confirmed" if not outside else "source_exact_pending_canonical",
                    "potentialStatus": "high" if high_potential else ("candidate" if outside else "active"),
                }
            )
    deduped = {(row["sourceCode"], row["externalId"]): row for row in roster}
    ordered = sorted(deduped.values(), key=lambda row: (row["tcg"], row["observedRank"], row["candidateKey"]))
    payload_hash = hashlib.sha256(canonical_json(ordered)).hexdigest()
    outside = [row for row in ordered if not row["active"]]
    high = [row for row in outside if row["potentialStatus"] == "high"]
    unavailable_price = [row for row in ordered if row["priceStatus"] == "unavailable"]
    return {
        "schemaVersion": 1,
        "capturedAt": captured_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "discoverySha256": payload_hash,
        "coverageStatus": "blocked" if high else "observed",
        "rosterCount": len(ordered),
        "activeMatchedCount": len(ordered) - len(outside),
        "outsideLockCount": len(outside),
        "unresolvedHighPotentialCount": len(high),
        "unavailablePriceCount": len(unavailable_price),
        "rejectedCount": rejected,
        "candidates": outside,
    }


def atomic_write(path: Path, document: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.next")
    temporary.write_bytes(canonical_json(document) + b"\n")
    os.replace(temporary, path)


def self_test() -> dict[str, Any]:
    row = {"url": "https://private.invalid/research/card/source/123", "lang": "JP"}
    return {
        "reference": source_reference(row),
        "language": normalize_language(row["lang"]),
        "candidateKeyStable": source_reference(row) == ("source", "123"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the CARDZ private discovery radar")
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--active-universe", type=Path, default=DEFAULT_ACTIVE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--captured-at")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(self_test(), sort_keys=True))
        return 0
    captured = (
        datetime.fromisoformat(args.captured_at.replace("Z", "+00:00"))
        if args.captured_at
        else datetime.now(timezone.utc)
    )
    document = build_radar(args.source_root.resolve(), args.active_universe.resolve(), captured)
    atomic_write(args.out.resolve(), document)
    print(json.dumps({key: value for key, value in document.items() if key != "candidates"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None
