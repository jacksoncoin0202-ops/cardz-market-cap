#!/usr/bin/env python3
"""Build the deduplicated complete CARDZ eligible ranking universe.

One canonical printing is stored once and may belong to the combined TCG,
Pokémon, and One Piece indexes at different ranks. Provider collection runs
against the union, so overlapping index membership never duplicates history.
Top 100/300/350 are downstream presentation cuts only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

PIPELINES_DIR = Path(__file__).resolve().parent
if str(PIPELINES_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINES_DIR))

from active_universe import DEFAULT_GEMRATE, DEFAULT_LANDING, enrich_candidates
from ranking_derivation import TRACKED_INDEX_LIMIT, derive_rankings
from source_crosswalk import DEFAULT_OUT as DEFAULT_CROSSWALK, DEFAULT_SOURCE, build_crosswalk


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "data/runtime/private-source-map/tracked-universe.json"
DEFAULT_GEMRATE_IDS = DEFAULT_OUT.with_name("tracked-gemrate-ids.txt")
DEFAULT_SNK_IDS = DEFAULT_OUT.with_name("tracked-snk-ids.txt")
def atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.next")
    temporary.write_bytes(content)
    os.replace(temporary, path)


def select_ranked_union(
    rows: Iterable[Mapping[str, Any]],
    *,
    limit: int = TRACKED_INDEX_LIMIT,
    generated_at: datetime | None = None,
    previous: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    generated_at = generated_at or datetime.now(timezone.utc)
    document = derive_rankings(rows, effective_at=generated_at, limit=limit, previous=previous)
    document["generatedAt"] = document["effectiveAt"]
    document["counts"]["gemrateMapped"] = sum(bool(row.get("gemrateId")) for row in document["cards"])
    document["counts"]["snkMapped"] = sum(isinstance(row.get("snkItemId"), int) for row in document["cards"])
    monitoring = document.get("monitoringCandidates", [])
    document["counts"]["monitoringGemrateMapped"] = sum(
        bool(row.get("gemrateId")) for row in monitoring if isinstance(row, Mapping)
    )
    document["counts"]["monitoringSnkMapped"] = sum(
        isinstance(row.get("snkItemId"), int) for row in monitoring if isinstance(row, Mapping)
    )
    return document


def write_outputs(document: Mapping[str, Any], output: Path, gemrate_ids: Path, snk_ids: Path) -> None:
    cards = document.get("cards")
    if not isinstance(cards, list):
        raise ValueError("tracked universe cards are missing")
    monitoring = document.get("monitoringCandidates", [])
    if not isinstance(monitoring, list):
        raise ValueError("tracked universe monitoring candidates are invalid")
    atomic_write(output, json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n")
    collection_rows = [*cards, *monitoring]
    gemrate = sorted(
        {
            str(row["gemrateId"])
            for row in collection_rows
            if isinstance(row, Mapping) and row.get("gemrateId")
        }
    )
    snk = sorted(
        {
            int(row["snkItemId"])
            for row in collection_rows
            if isinstance(row, Mapping) and isinstance(row.get("snkItemId"), int)
        }
    )
    atomic_write(gemrate_ids, (("\n".join(gemrate) + "\n") if gemrate else "").encode("ascii"))
    atomic_write(snk_ids, (("\n".join(str(value) for value in snk) + "\n") if snk else "").encode("ascii"))


def _load_snk_rows(path: Path) -> dict[int, Mapping[str, Any]]:
    rows: dict[int, Mapping[str, Any]] = {}
    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            try:
                row = json.loads(line)
            except json.JSONDecodeError as error:
                raise RuntimeError(f"invalid SNK JSONL at line {line_number}: {path}") from error
            if not isinstance(row, Mapping) or not isinstance(row.get("item_id"), int):
                raise RuntimeError(f"invalid SNK row at line {line_number}: {path}")
            rows[int(row["item_id"])] = row
    return rows


def _fx_jpy_per_usd(path: Path) -> float:
    document = json.loads(path.read_text(encoding="utf-8-sig"))
    try:
        value = float(document["rates"]["JPY"]["value"])
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeError(f"valid USD/JPY rate is missing: {path}") from error
    if value <= 0:
        raise RuntimeError(f"valid USD/JPY rate is missing: {path}")
    return value


def _latest_snk_price(snk: Mapping[str, Any], jpy_per_usd: float) -> tuple[float, str] | None:
    points: list[tuple[str, float]] = []
    for point in snk.get("kline", []):
        if not isinstance(point, Mapping):
            continue
        try:
            observed = date.fromisoformat(str(point.get("date") or ""))
            value = float(point.get("price_jpy"))
        except (TypeError, ValueError):
            continue
        if value > 0:
            points.append((observed.isoformat(), value))
    if not points:
        return None
    observed, value = max(points)
    return (round(value / jpy_per_usd, 6), observed)


def candidate_manifest_rows(
    manifest: Mapping[str, Any],
    *,
    snk_rows: Mapping[int, Mapping[str, Any]],
    jpy_per_usd: float,
) -> tuple[list[dict[str, Any]], Counter[str]]:
    """Turn exact formal/pre-entry receipts into canonical collection rows.

    Unavailable or review candidates deliberately stay out of this overlay: they
    remain retry evidence in the immutable candidate manifest and must not
    prevent independently verified printings from being ranked or ingested.
    """

    source = manifest.get("candidates")
    if not isinstance(source, list):
        raise RuntimeError("candidate manifest has no candidates")
    rows: list[dict[str, Any]] = []
    rejected: Counter[str] = Counter()
    for candidate in source:
        if not isinstance(candidate, Mapping):
            rejected["invalid_candidate"] += 1
            continue
        tracking = str(candidate.get("trackingStatus") or "")
        if tracking not in {"eligible", "pre_entry_radar"}:
            continue
        if candidate.get("status") not in {"resolved", "below-threshold"}:
            rejected["tracked_candidate_unresolved"] += 1
            continue
        if candidate.get("identityStatus") != "exact_confirmed":
            rejected["tracked_candidate_identity_unconfirmed"] += 1
            continue
        identity = candidate.get("canonicalIdentity")
        if not isinstance(identity, Mapping):
            rejected["tracked_candidate_identity_missing"] += 1
            continue
        source_code = str(candidate.get("canonicalSourceCode") or "").strip()
        external_id = str(candidate.get("canonicalExternalId") or "").strip()
        tcg = str(identity.get("tcg") or candidate.get("tcg") or "").strip()
        language = str(identity.get("language") or "").strip()
        set_name = str(identity.get("setName") or "").strip()
        collector = str(identity.get("collectorNumber") or "").strip()
        name = str(identity.get("name") or candidate.get("name") or collector).strip()
        gemrate_id = str(candidate.get("gemrateId") or "").strip()
        snk_id = candidate.get("snkItemId")
        population = candidate.get("populationPsa10")
        if (
            not all((source_code, external_id, tcg, language, set_name, collector, gemrate_id))
            or tcg not in {"pokemon", "one-piece"}
            or not isinstance(snk_id, int)
            or not isinstance(population, int)
        ):
            rejected["tracked_candidate_identity_incomplete"] += 1
            continue
        if tracking == "eligible" and population < 1000:
            rejected["tracked_candidate_population_mismatch"] += 1
            continue
        if tracking == "pre_entry_radar" and not 971 <= population < 1000:
            rejected["tracked_candidate_population_mismatch"] += 1
            continue
        snk = snk_rows.get(snk_id)
        price = _latest_snk_price(snk, jpy_per_usd) if snk else None
        if price is None:
            rejected["tracked_candidate_snk_price_missing"] += 1
            continue
        price_usd, price_as_of = price
        key = f"{source_code}|{external_id}"
        rows.append(
            {
                "pokedexId": "candidate_" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:20],
                "canonicalPrintingKey": key,
                "pokedexStatus": "confirmed",
                "canonicalSourceCode": source_code,
                "canonicalExternalId": external_id,
                "gemrateId": gemrate_id,
                "snkItemId": snk_id,
                "tcg": tcg,
                "language": language,
                "segment": f"{tcg}:{language}",
                "setName": set_name,
                "collectorNumber": collector,
                "name": name,
                "edition": str(identity.get("edition") or ""),
                "parallel": str(identity.get("parallel") or ""),
                "finish": str(identity.get("finish") or ""),
                "populationPsa10": population,
                "populationAsOf": str(candidate.get("effectiveDate") or ""),
                "populationSourceState": "gemrate_candidate",
                "populationEstimated": False,
                "priceUsd": price_usd,
                "priceAsOf": price_as_of,
                "priceSourceState": "snk_daily_history",
                "marketCapUsd": round(price_usd * population, 2),
            }
        )
    return rows, rejected


def main() -> int:
    parser = argparse.ArgumentParser(description="Build complete combined/Pokémon/One Piece eligible ranking union")
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--crosswalk", type=Path, default=DEFAULT_CROSSWALK)
    parser.add_argument("--landing-root", type=Path, default=DEFAULT_LANDING)
    parser.add_argument("--gemrate-root", type=Path, default=DEFAULT_GEMRATE)
    parser.add_argument(
        "--candidate-manifest",
        type=Path,
        help="private GemRate candidate manifest; exact formal/pre-entry rows are overlaid after SNK history succeeds",
    )
    parser.add_argument(
        "--snk-run",
        type=Path,
        help="completed exact PSA 10 SNK JSONL required together with --candidate-manifest",
    )
    parser.add_argument(
        "--fx-snapshot",
        type=Path,
        default=ROOT / "data/runtime/private-fx/latest.json",
        help="USD/JPY snapshot used to derive candidate PSA 10 USD reference prices",
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--gemrate-ids-out", type=Path, default=DEFAULT_GEMRATE_IDS)
    parser.add_argument("--snk-ids-out", type=Path, default=DEFAULT_SNK_IDS)
    parser.add_argument(
        "--limit",
        type=int,
        default=TRACKED_INDEX_LIMIT,
        help="deprecated presentation compatibility option; canonical ranking storage is never truncated",
    )
    parser.add_argument("--effective-at")
    args = parser.parse_args()

    effective_at = (
        datetime.fromisoformat(args.effective_at.replace("Z", "+00:00"))
        if args.effective_at
        else datetime.now(timezone.utc)
    )
    crosswalk_path = args.crosswalk.resolve()
    crosswalk = (
        json.loads(crosswalk_path.read_text(encoding="utf-8-sig"))
        if crosswalk_path.is_file()
        else build_crosswalk(args.source_root.resolve(), effective_at)
    )
    candidates, rejected = enrich_candidates(
        args.source_root.resolve(),
        crosswalk,
        args.landing_root.resolve(),
        args.gemrate_root.resolve(),
        effective_at,
    )
    if (args.candidate_manifest is None) != (args.snk_run is None):
        raise RuntimeError("--candidate-manifest and --snk-run must be supplied together")
    if args.candidate_manifest is not None and args.snk_run is not None:
        manifest = json.loads(args.candidate_manifest.resolve().read_text(encoding="utf-8-sig"))
        if not isinstance(manifest, Mapping):
            raise RuntimeError("candidate manifest is invalid")
        candidate_rows, candidate_rejected = candidate_manifest_rows(
            manifest,
            snk_rows=_load_snk_rows(args.snk_run.resolve()),
            jpy_per_usd=_fx_jpy_per_usd(args.fx_snapshot.resolve()),
        )
        existing_sources = {
            (str(row.get("canonicalSourceCode") or ""), str(row.get("canonicalExternalId") or ""))
            for row in candidates
        }
        for row in candidate_rows:
            source_ref = (str(row["canonicalSourceCode"]), str(row["canonicalExternalId"]))
            if source_ref not in existing_sources:
                candidates.append(row)
                existing_sources.add(source_ref)
        rejected.update(candidate_rejected)
    previous = (
        json.loads(args.out.read_text(encoding="utf-8-sig"))
        if args.out.is_file()
        else None
    )
    document = select_ranked_union(candidates, limit=args.limit, generated_at=effective_at, previous=previous)
    combined_rejected = Counter(document["rejected"])
    combined_rejected.update(rejected)
    document["rejected"] = dict(sorted(combined_rejected.items()))
    write_outputs(document, args.out.resolve(), args.gemrate_ids_out.resolve(), args.snk_ids_out.resolve())
    print(json.dumps({"output": str(args.out.resolve()), **document["counts"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
