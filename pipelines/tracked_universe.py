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


G10_RELAXED_OVERLAY_SCHEMA = "4.0.0"
G10_RELAXED_OVERLAY_POLICY = {
    "indexes": ["tcg", "pokemon", "one-piece"],
    "canonicalMembership": "complete_eligible",
    "languagePartitioning": False,
    "bootstrapSource": "grade10",
    "releaseProfile": "relaxed-launch-v1",
    "rankBasis": "deterministic_crosswalk_order",
    "identityStatus": "provisional",
}


def _g10_overlay_hash(source_code: str, external_id: str) -> str:
    return hashlib.sha256(f"{source_code.casefold()}|{external_id}".encode("utf-8")).hexdigest()


def _g10_provisional_collector(source_code: str, external_id: str) -> str:
    """Keep a DB-required identifier without pretending it is a printing number."""

    return f"G10-{_g10_overlay_hash(source_code, external_id)[:20]}"


def build_g10_relaxed_overlay(
    crosswalk: Mapping[str, Any],
    *,
    effective_at: datetime,
) -> dict[str, Any]:
    """Build the isolated 600-card Grade10 bootstrap overlay.

    This is deliberately separate from the current strict universe lock.  It
    preserves each crosswalk source reference and its available identity fields,
    while an explicit ``G10-<hash>`` marker makes a missing collector number
    provisional instead of inventing a real printing number.
    """

    source = crosswalk.get("cards")
    if not isinstance(source, list):
        raise RuntimeError("Grade10 crosswalk has no cards")

    cards: list[dict[str, Any]] = []
    seen_sources: set[tuple[str, str]] = set()
    seen_opaque_ids: set[str] = set()
    provisional_collectors = 0
    for index, raw in enumerate(source, start=1):
        if not isinstance(raw, Mapping):
            raise RuntimeError(f"Grade10 crosswalk card {index} is invalid")
        source_code = str(raw.get("canonicalSourceCode") or "").strip().casefold()
        external_id = str(raw.get("canonicalExternalId") or "").strip()
        tcg = str(raw.get("market") or "").strip()
        language = str(raw.get("language") or "").strip()
        name = str(raw.get("name") or "").strip()
        set_name = str(raw.get("setName") or "").strip()
        if (
            not source_code
            or not external_id
            or tcg not in {"pokemon", "one-piece"}
            or language not in {"en", "ja", "ko", "zhCN", "zhTW"}
            or not name
            or not set_name
        ):
            raise RuntimeError(f"Grade10 crosswalk card {index} lacks bootstrap identity")
        source_ref = (source_code, external_id)
        if source_ref in seen_sources:
            raise RuntimeError(f"Grade10 crosswalk repeats source identity: {source_code}:{external_id}")
        digest = _g10_overlay_hash(source_code, external_id)
        opaque_id = f"g10_{digest[:24]}"
        if opaque_id in seen_opaque_ids:
            raise RuntimeError("Grade10 bootstrap opaque ID collision")
        seen_sources.add(source_ref)
        seen_opaque_ids.add(opaque_id)

        raw_collector = str(raw.get("collectorNumberRaw") or "").strip()
        provisional_collector = not raw_collector
        collector = raw_collector or _g10_provisional_collector(source_code, external_id)
        provisional_collectors += int(provisional_collector)
        snk_item_id = raw.get("snkItemId")
        cards.append(
            {
                "pokedexId": opaque_id,
                "pokedexStatus": "provisional",
                "identityStatus": "provisional",
                "canonicalSourceCode": source_code,
                "canonicalExternalId": external_id,
                "gemrateId": str(raw.get("gemrateId") or "").strip() or None,
                "snkItemId": (
                    int(snk_item_id)
                    if isinstance(snk_item_id, int) and not isinstance(snk_item_id, bool) and snk_item_id > 0
                    else None
                ),
                "tcg": tcg,
                "language": language,
                "name": name,
                "setName": set_name,
                "collectorNumber": collector,
                "rankMemberships": {},
                "g10Bootstrap": {
                    "sourceCrosswalkIdentityStatus": str(raw.get("identityStatus") or "review"),
                    "collectorNumberRaw": raw_collector or None,
                    "collectorNumberStatus": "provisional_marker" if provisional_collector else "source_value",
                    "canonicalPrintingKey": raw.get("canonicalPrintingKey"),
                    "edition": str(raw.get("edition") or "").strip(),
                    "parallel": str(raw.get("parallel") or "").strip(),
                    "finish": str(raw.get("finish") or "").strip(),
                },
            }
        )

    cards.sort(
        key=lambda row: (
            str(row["tcg"]),
            str(row["canonicalSourceCode"]),
            str(row["canonicalExternalId"]),
        )
    )
    market_ranks: Counter[str] = Counter()
    for combined_rank, card in enumerate(cards, start=1):
        tcg = str(card["tcg"])
        market_ranks[tcg] += 1
        card["rankMemberships"] = {"tcg": combined_rank, tcg: market_ranks[tcg]}

    effective = effective_at.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return {
        "schemaVersion": G10_RELAXED_OVERLAY_SCHEMA,
        "authority": {
            "source": "grade10_crosswalk",
            "releaseProfile": "relaxed-launch-v1",
            "crosswalkPayloadSha256": crosswalk.get("payloadSha256"),
        },
        "effectiveAt": effective,
        "generatedAt": effective,
        "policy": dict(G10_RELAXED_OVERLAY_POLICY),
        "counts": {
            "cards": len(cards),
            "pokemon": market_ranks["pokemon"],
            "onePiece": market_ranks["one-piece"],
            "gemrateMapped": sum(bool(card.get("gemrateId")) for card in cards),
            "snkMapped": sum(isinstance(card.get("snkItemId"), int) for card in cards),
            "provisionalCollectorMarkers": provisional_collectors,
        },
        "payloadSha256": hashlib.sha256(
            json.dumps(cards, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "cards": cards,
    }


def write_g10_relaxed_overlay(
    document: Mapping[str, Any],
    output: Path,
    gemrate_ids: Path,
    snk_ids: Path,
) -> None:
    """Write a private relaxed overlay plus its paired provider worklists."""

    write_outputs(document, output, gemrate_ids, snk_ids)


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


def _canonical_source_ref(row: Mapping[str, Any]) -> tuple[str, str]:
    nested = row.get("canonicalSource")
    source = nested if isinstance(nested, Mapping) else row
    return (
        str(source.get("sourceCode") or row.get("canonicalSourceCode") or "").strip(),
        str(source.get("externalId") or row.get("canonicalExternalId") or "").strip(),
    )


def _canonical_identity_signature(row: Mapping[str, Any]) -> tuple[str, ...] | None:
    identity = row.get("canonicalIdentity")
    if not isinstance(identity, Mapping):
        return None
    fields = ("tcg", "setName", "collectorNumber", "edition", "parallel", "finish")
    values = tuple(str(identity.get(field) or "").strip().casefold() for field in fields)
    return values if all(values) else None


def merge_snk_refill_worklist(
    manifest: Mapping[str, Any],
    worklist: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, int]]:
    """Carry exact resolved SNK IDs back to their immutable candidate reference."""

    raw_candidates = manifest.get("candidates")
    raw_worklist = worklist.get("cards")
    if not isinstance(raw_candidates, list):
        raise RuntimeError("candidate manifest has no candidates")
    if not isinstance(raw_worklist, list):
        raise RuntimeError("SNK exact refill worklist has no candidate rows")

    candidates = [dict(row) for row in raw_candidates if isinstance(row, Mapping)]
    by_source: dict[tuple[str, str], dict[str, Any]] = {}
    for candidate in candidates:
        source_ref = _canonical_source_ref(candidate)
        if not all(source_ref):
            continue
        if source_ref in by_source:
            raise RuntimeError(f"candidate manifest repeats canonical source reference: {source_ref[0]}:{source_ref[1]}")
        by_source[source_ref] = candidate

    counts = {
        "worklistRows": len(raw_worklist),
        "resolvedRows": 0,
        "attached": 0,
        "replayed": 0,
        "unresolvedRows": 0,
    }
    resolved_by_source: dict[tuple[str, str], int] = {}
    for raw in raw_worklist:
        if not isinstance(raw, Mapping) or raw.get("status") != "resolved":
            counts["unresolvedRows"] += 1
            continue
        counts["resolvedRows"] += 1
        source_ref = _canonical_source_ref(raw)
        snk_item_id = raw.get("snkItemId")
        if (
            not all(source_ref)
            or raw.get("identityStatus") != "exact_confirmed"
            or not isinstance(snk_item_id, int)
            or isinstance(snk_item_id, bool)
            or snk_item_id <= 0
        ):
            raise RuntimeError("resolved SNK worklist row is missing an exact candidate association")
        prior = resolved_by_source.get(source_ref)
        if prior is not None and prior != snk_item_id:
            raise RuntimeError(f"SNK worklist rebind blocked: {source_ref[0]}:{source_ref[1]}")
        resolved_by_source[source_ref] = snk_item_id
        candidate = by_source.get(source_ref)
        if candidate is None:
            raise RuntimeError(f"SNK worklist candidate is absent from manifest: {source_ref[0]}:{source_ref[1]}")
        candidate_identity = _canonical_identity_signature(candidate)
        worklist_identity = _canonical_identity_signature(raw)
        if (
            candidate_identity is None
            or worklist_identity is None
            or candidate_identity != worklist_identity
        ):
            raise RuntimeError(f"SNK worklist identity mismatch: {source_ref[0]}:{source_ref[1]}")
        existing = candidate.get("snkItemId")
        if isinstance(existing, int) and not isinstance(existing, bool):
            if existing != snk_item_id:
                raise RuntimeError(f"SNK identity rebind blocked: {source_ref[0]}:{source_ref[1]}")
            counts["replayed"] += 1
            continue
        candidate["snkItemId"] = snk_item_id
        nested_source = candidate.get("canonicalSource")
        if isinstance(nested_source, Mapping):
            candidate["canonicalSource"] = {**dict(nested_source), "snkItemId": snk_item_id}
        counts["attached"] += 1

    return {**dict(manifest), "candidates": candidates}, counts


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
    parser.add_argument(
        "--g10-relaxed-overlay",
        action="store_true",
        help="build a separate Grade10-backed relaxed-launch-v1 bootstrap universe; never overwrite the strict lock",
    )
    parser.add_argument(
        "--expect-crosswalk-cards",
        type=int,
        default=600,
        help="required Grade10 crosswalk cardinality for --g10-relaxed-overlay",
    )
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
        "--snk-worklist",
        type=Path,
        help="exact refill association worklist merged into the candidate manifest before overlay construction",
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
    if args.g10_relaxed_overlay:
        output = args.out.resolve()
        gemrate_ids = args.gemrate_ids_out.resolve()
        snk_ids = args.snk_ids_out.resolve()
        if output == DEFAULT_OUT.resolve():
            raise RuntimeError("--g10-relaxed-overlay requires a separate --out; tracked-universe.json is protected")
        if gemrate_ids == DEFAULT_GEMRATE_IDS.resolve() or snk_ids == DEFAULT_SNK_IDS.resolve():
            raise RuntimeError("--g10-relaxed-overlay requires separate --gemrate-ids-out and --snk-ids-out")
        if not crosswalk_path.is_file():
            raise RuntimeError(f"Grade10 crosswalk does not exist: {crosswalk_path}")
        loaded_crosswalk = json.loads(crosswalk_path.read_text(encoding="utf-8-sig"))
        if not isinstance(loaded_crosswalk, Mapping):
            raise RuntimeError("Grade10 crosswalk is invalid")
        document = build_g10_relaxed_overlay(loaded_crosswalk, effective_at=effective_at)
        if len(document["cards"]) != args.expect_crosswalk_cards:
            raise RuntimeError(
                f"Grade10 relaxed overlay completeness failed: {len(document['cards'])} != {args.expect_crosswalk_cards}"
            )
        write_g10_relaxed_overlay(document, output, gemrate_ids, snk_ids)
        print(
            json.dumps(
                {
                    "status": "ready",
                    "mode": "g10_relaxed_overlay",
                    "output": str(output),
                    "gemrateIds": str(gemrate_ids),
                    "snkIds": str(snk_ids),
                    **document["counts"],
                },
                sort_keys=True,
            )
        )
        return 0
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
    if args.snk_worklist is not None and args.candidate_manifest is None:
        raise RuntimeError("--snk-worklist requires --candidate-manifest and --snk-run")
    snk_worklist_merge: dict[str, int] | None = None
    if args.candidate_manifest is not None and args.snk_run is not None:
        manifest = json.loads(args.candidate_manifest.resolve().read_text(encoding="utf-8-sig"))
        if not isinstance(manifest, Mapping):
            raise RuntimeError("candidate manifest is invalid")
        if args.snk_worklist is not None:
            worklist = json.loads(args.snk_worklist.resolve().read_text(encoding="utf-8-sig"))
            if not isinstance(worklist, Mapping):
                raise RuntimeError("SNK exact refill worklist is invalid")
            manifest, snk_worklist_merge = merge_snk_refill_worklist(manifest, worklist)
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
    if snk_worklist_merge is not None:
        document["candidateSnkWorklistMerge"] = snk_worklist_merge
    write_outputs(document, args.out.resolve(), args.gemrate_ids_out.resolve(), args.snk_ids_out.resolve())
    print(json.dumps({"output": str(args.out.resolve()), **document["counts"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
