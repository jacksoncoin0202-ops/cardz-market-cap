#!/usr/bin/env python3
"""Immutable full/incremental intake for the private G10 landing zone.

This module never writes the public snapshot and never mutates source files. It
creates a content-addressed private run manifest plus canonical observation
rows whose deterministic keys line up with the MySQL unique constraints in
``migrations/002_market_observations.mysql.sql``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


GRADERS = ("PSA", "BGS", "CGC", "SGC", "TAG")
WINDOW_DAYS = {"1d": 1, "7d": 7, "30d": 30}
_SOURCE_REF = re.compile(r"/card/([^/]+)/([^/?#]+)", re.IGNORECASE)


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_effective_at(value: str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc).replace(microsecond=0)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def private_source_ref(row: Mapping[str, Any]) -> tuple[str, str] | None:
    match = _SOURCE_REF.search(str(row.get("url") or ""))
    if match is None:
        return None
    return match.group(1).lower(), match.group(2)


def storage_source(source_code: str) -> str:
    """Map a private source scope to its on-disk landing directory."""

    return "altxyz" if source_code == "ebay" else source_code


def iter_constituents(source_root: Path) -> Iterable[tuple[str, Mapping[str, Any]]]:
    for market in ("ptcg", "ptcg100", "opcg"):
        path = source_root / "index" / market / "constituents.json"
        if not path.is_file():
            continue
        document = read_json(path)
        rows = document.get("rows") if isinstance(document, Mapping) else None
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, Mapping):
                yield market, row


_COMPLETE_COLLECTOR_NUMBER = re.compile(
    r"^(?:[A-Z0-9]+/\d{2,4}|(?:OP|ST|EB)\d{2}-\d{3}|P-\d{3}|(?:SM|SWSH)\d+)$",
    re.IGNORECASE,
)
_COLLECTOR_IN_TEXT = re.compile(
    r"#\s*((?:[A-Z0-9]+/\d{2,4}|(?:OP|ST|EB)\d{2}-\d{3}|P-\d{3}|(?:SM|SWSH)\d+))\b",
    re.IGNORECASE,
)
_LANGUAGE_CODES = {
    "en": "en",
    "english": "en",
    "ja": "ja",
    "jp": "ja",
    "japanese": "ja",
    "ko": "ko",
    "korean": "ko",
    "zh-cn": "zhCN",
    "zh-hans": "zhCN",
    "zhcn": "zhCN",
    "zh-tw": "zhTW",
    "zh-hant": "zhTW",
    "zhtw": "zhTW",
}


def normalize_language(value: Any) -> str | None:
    """Return a declared card language; never infer one from a card name."""

    normalized = str(value or "").strip().casefold().replace("_", "-")
    return _LANGUAGE_CODES.get(normalized)


def complete_collector_number(value: Any) -> str | None:
    """Accept only an upstream-provided complete collector number.

    Short display fragments such as ``085`` are deliberately rejected.  The
    crosswalk owns any later exact set suffix resolution.
    """

    candidate = re.sub(r"\s+", "", str(value or "").upper())
    return candidate if _COMPLETE_COLLECTOR_NUMBER.fullmatch(candidate) else None


def collector_number_from_asset(asset: Mapping[str, Any], constituent: Mapping[str, Any]) -> str | None:
    explicit = complete_collector_number(asset.get("cardId"))
    if explicit:
        return explicit
    for value in (asset.get("displayTitle"), asset.get("fullTitle"), constituent.get("name")):
        match = _COLLECTOR_IN_TEXT.search(str(value or ""))
        if match:
            return complete_collector_number(match.group(1))
    return None


def market_tcg(market: str) -> str:
    if market == "opcg":
        return "one-piece"
    return "pokemon"


def detail_observation(
    *,
    source_code: str,
    external_id: str,
    market: str,
    observation_kind: str,
    effective_at: datetime,
    payload: Mapping[str, Any],
    observed_date: date | None = None,
) -> dict[str, Any]:
    payload_value = dict(payload)
    payload_hash = sha256_bytes(canonical_json(payload_value))
    observed = observed_date or effective_at.date()
    return {
        "observationKey": sha256_bytes(
            f"{source_code}|{external_id}|{observation_kind}|{observed.isoformat()}|{payload_hash}".encode()
        ),
        "sourceCode": source_code,
        "externalEntityId": external_id,
        "observationKind": observation_kind,
        "market": market,
        "effectiveAt": iso_utc(effective_at),
        "observedDate": observed.isoformat(),
        "payloadHash": payload_hash,
        "payload": payload_value,
    }


def _summary_locale(path: Path) -> str | None:
    match = re.fullmatch(r"summary_([a-z-]+)\.json", path.name.casefold())
    return normalize_language(match.group(1)) if match else None


def _exact_sale_observations(
    card_root: Path,
    *,
    source_code: str,
    external_id: str,
    market: str,
    effective_at: datetime,
    quarantine: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[SaleObservation]]:
    observations: list[dict[str, Any]] = []
    normalized: list[SaleObservation] = []
    for filename in ("ebay_PSA_10.json", "apparel_grade_22.json"):
        path = card_root / filename
        if not path.is_file():
            continue
        document = read_json(path)
        if not isinstance(document, Mapping):
            quarantine.append(
                {
                    "sourceCode": source_code,
                    "externalEntityId": external_id,
                    "reason": "invalid_sales_document",
                    "payloadHash": sha256_file(path),
                }
            )
            continue
        rows = document.get("saleHistory")
        if not isinstance(rows, list):
            continue
        row_quarantine: list[dict[str, Any]] = []
        sales = normalize_psa10_sales(
            [row for row in rows if isinstance(row, Mapping)],
            effective_at,
            f"{source_code}:{external_id}:{filename}",
            row_quarantine,
            allow_relative_dates=False,
        )
        for item in row_quarantine:
            quarantine.append(
                {
                    "sourceCode": source_code,
                    "externalEntityId": external_id,
                    "reason": item["reason"],
                    "payloadHash": item["payloadHash"],
                    "sourcePath": path.relative_to(card_root.parents[2]).as_posix(),
                }
            )
        for sale in sales:
            normalized.append(sale)
            sold_at = parse_effective_at(sale.sold_at)
            payload = {
                "grader": "PSA",
                "gradeLabel": "PSA 10",
                "soldAt": sale.sold_at,
                "sourceDateText": sale.source_date_text,
                "timestampQuality": sale.timestamp_quality,
                "unitPriceUsd": sale.unit_price_usd,
                "quantity": sale.quantity,
                "transactionValueUsd": sale.transaction_value_usd,
                "transactionFingerprint": sale.fingerprint,
                "sourcePath": path.relative_to(card_root.parents[2]).as_posix(),
                "sourcePayloadSha256": sha256_file(path),
                "coverage": "partial",
            }
            observations.append(
                detail_observation(
                    source_code=source_code,
                    external_id=external_id,
                    market=market,
                    observation_kind="sale_observation_psa10",
                    effective_at=effective_at,
                    observed_date=sold_at.date(),
                    payload=payload,
                )
            )
    return observations, normalized


def build_detail_observations(
    source_root: Path,
    effective_at: datetime,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Extract private Grade10 card detail into deterministic candidate rows.

    This is an intake boundary, not a crosswalk.  It emits only evidence that
    is explicit in an asset payload; incomplete identities and undated sales
    stay quarantined for the exact resolver instead of being guessed.
    """

    accepted: list[dict[str, Any]] = []
    quarantined: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for market, constituent in iter_constituents(source_root):
        source_ref = private_source_ref(constituent)
        if source_ref is None or source_ref in seen:
            continue
        seen.add(source_ref)
        source_code, external_id = source_ref
        card_root = source_root / "cards" / storage_source(source_code) / external_id
        asset_path = card_root / "asset_info.json"
        if not asset_path.is_file():
            quarantined.append(
                {
                    "sourceCode": source_code,
                    "externalEntityId": external_id,
                    "reason": "missing_asset_info",
                    "payloadHash": sha256_bytes(canonical_json(dict(constituent))),
                }
            )
            continue
        asset = read_json(asset_path)
        if not isinstance(asset, Mapping):
            quarantined.append(
                {
                    "sourceCode": source_code,
                    "externalEntityId": external_id,
                    "reason": "invalid_asset_info",
                    "payloadHash": sha256_file(asset_path),
                }
            )
            continue
        collector_number = collector_number_from_asset(asset, constituent)
        language = normalize_language(asset.get("language")) or normalize_language(constituent.get("lang"))
        set_name = str(asset.get("setName") or constituent.get("setName") or "").strip() or None
        identity_status = "candidate" if collector_number and language and set_name else "review"
        identity_payload = {
            "tcg": market_tcg(market),
            "set": set_name,
            "language": language,
            "collectorNumber": collector_number,
            "edition": None,
            "parallel": None,
            "finish": None,
            "name": str(asset.get("cardName") or constituent.get("name") or "").strip() or None,
            "year": str(asset.get("year") or "").strip() or None,
            "identityStatus": identity_status,
            "sourcePath": asset_path.relative_to(source_root).as_posix(),
            "sourceVersionSha256": sha256_file(asset_path),
        }
        accepted.append(
            detail_observation(
                source_code=source_code,
                external_id=external_id,
                market=market,
                observation_kind="identity_candidate",
                effective_at=effective_at,
                payload=identity_payload,
            )
        )
        if identity_status != "candidate":
            quarantined.append(
                {
                    "sourceCode": source_code,
                    "externalEntityId": external_id,
                    "reason": "incomplete_identity_candidate",
                    "payloadHash": sha256_bytes(canonical_json(identity_payload)),
                }
            )
        image_value = str(asset.get("image") or "").strip()
        if image_value:
            accepted.append(
                detail_observation(
                    source_code=source_code,
                    external_id=external_id,
                    market=market,
                    observation_kind="image_metadata",
                    effective_at=effective_at,
                    payload={
                        "imageKind": "unverified_remote",
                        "remoteUrlSha256": sha256_bytes(image_value.encode("utf-8")),
                        "sourcePath": asset_path.relative_to(source_root).as_posix(),
                        "sourceVersionSha256": sha256_file(asset_path),
                        "publicAllowed": False,
                    },
                )
            )
        for summary_path in sorted(card_root.glob("summary_*.json"), key=lambda item: item.name.casefold()):
            locale = _summary_locale(summary_path)
            document = read_json(summary_path)
            if locale is None or not isinstance(document, Mapping) or not isinstance(document.get("summary"), str):
                quarantined.append(
                    {
                        "sourceCode": source_code,
                        "externalEntityId": external_id,
                        "reason": "invalid_story_pointer",
                        "payloadHash": sha256_file(summary_path),
                    }
                )
                continue
            accepted.append(
                detail_observation(
                    source_code=source_code,
                    external_id=external_id,
                    market=market,
                    observation_kind="story_pointer",
                    effective_at=effective_at,
                    payload={
                        "locale": locale,
                        "sourcePath": summary_path.relative_to(source_root).as_posix(),
                        "sourceVersionSha256": sha256_file(summary_path),
                    },
                )
            )
        sales, normalized_sales = _exact_sale_observations(
            card_root,
            source_code=source_code,
            external_id=external_id,
            market=market,
            effective_at=effective_at,
            quarantine=quarantined,
        )
        accepted.extend(sales)
        for window, days in WINDOW_DAYS.items():
            aggregate = aggregate_sales(normalized_sales, effective_at, days)
            accepted.append(
                detail_observation(
                    source_code=source_code,
                    external_id=external_id,
                    market=market,
                    observation_kind=f"tracked_sales_{window}",
                    effective_at=effective_at,
                    payload=aggregate,
                )
            )
        accepted.append(
            detail_observation(
                source_code=source_code,
                external_id=external_id,
                market=market,
                observation_kind="daily_price_history_status",
                effective_at=effective_at,
                payload={
                    "status": "unavailable",
                    "reason": "no_explicit_daily_reference_history_in_grade10_detail",
                },
            )
        )
    accepted.sort(key=lambda row: (row["sourceCode"], row["externalEntityId"], row["observationKind"], row["observationKey"]))
    quarantined.sort(key=lambda row: (str(row.get("sourceCode") or ""), str(row.get("externalEntityId") or ""), str(row.get("reason") or ""), str(row.get("payloadHash") or "")))
    return accepted, quarantined


def build_constituent_observations(source_root: Path, effective_at: datetime) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for market, row in iter_constituents(source_root):
        source_ref = private_source_ref(row)
        price = row.get("priceUsd")
        if source_ref is None or not isinstance(price, (int, float)) or price <= 0:
            rejected.append({"market": market, "reason": "invalid_source_or_price", "payloadHash": sha256_bytes(canonical_json(row))})
            continue
        if source_ref in seen:
            continue
        seen.add(source_ref)
        source_code, external_id = source_ref
        payload_hash = sha256_bytes(canonical_json(row))
        observed_date = effective_at.date().isoformat()
        # One close per source-scoped identity per UTC date. Full timestamps
        # and payload hashes remain evidence, but a same-day retry cannot
        # manufacture a second price/population observation.
        key_material = f"{source_code}|{external_id}|index_constituent|{observed_date}".encode()
        accepted.append(
            {
                "observationKey": sha256_bytes(key_material),
                "sourceCode": source_code,
                "externalEntityId": external_id,
                "observationKind": "index_constituent",
                "market": market,
                "effectiveAt": iso_utc(effective_at),
                "observedDate": observed_date,
                "payloadHash": payload_hash,
                "payload": dict(row),
            }
        )
    return accepted, rejected


def build_grader_population_observations(
    source_root: Path,
    effective_at: datetime,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build one top-grade population close per source identity and grader.

    Population is observed when the private runner fetches the card payload,
    independently of the upstream index price's effective date.
    """

    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    observed_date = effective_at.date().isoformat()
    for market, row in iter_constituents(source_root):
        source_ref = private_source_ref(row)
        if source_ref is None or source_ref in seen:
            continue
        seen.add(source_ref)
        source_code, external_id = source_ref
        path = source_root / "cards" / storage_source(source_code) / external_id / "populations.json"
        if not path.is_file():
            continue
        document = read_json(path)
        rows = document.get("population") if isinstance(document, Mapping) else None
        if not isinstance(rows, list):
            rejected.append(
                {
                    "market": market,
                    "sourceCode": source_code,
                    "externalEntityId": external_id,
                    "reason": "invalid_grader_population_document",
                    "payloadHash": sha256_file(path),
                }
            )
            continue
        for population in rows:
            if not isinstance(population, Mapping):
                continue
            grader = str(population.get("gradeName") or "").upper()
            total = population.get("total")
            top_grade_population = population.get("topGrade")
            if grader not in GRADERS or not isinstance(total, int) or total < 0 or not isinstance(top_grade_population, int) or top_grade_population < 0:
                rejected.append(
                    {
                        "market": market,
                        "sourceCode": source_code,
                        "externalEntityId": external_id,
                        "grader": grader or None,
                        "reason": "invalid_grader_population_row",
                        "payloadHash": sha256_bytes(canonical_json(population)),
                    }
                )
                continue
            payload = {
                "grader": grader,
                "totalPopulation": total,
                "topGradePopulation": top_grade_population,
                "estimated": False,
                "authority": "gemrate",
                "transport": "grade10_gemrate_mirror",
            }
            observation_kind = f"grader_population_{grader.casefold()}"
            key_material = f"{source_code}|{external_id}|{observation_kind}|{observed_date}".encode()
            accepted.append(
                {
                    "observationKey": sha256_bytes(key_material),
                    "sourceCode": source_code,
                    "providerCode": "gemrate",
                    "transportCode": "grade10_gemrate_mirror",
                    "externalEntityId": external_id,
                    "observationKind": observation_kind,
                    "market": market,
                    "effectiveAt": iso_utc(effective_at),
                    "observedDate": observed_date,
                    "payloadHash": sha256_bytes(canonical_json(payload)),
                    "payload": payload,
                }
            )
    return accepted, rejected


def build_daily_observations(
    source_root: Path,
    price_effective_at: datetime,
    population_effective_at: datetime,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    prices, price_rejected = build_constituent_observations(source_root, price_effective_at)
    populations, population_rejected = build_grader_population_observations(source_root, population_effective_at)
    details, detail_quarantined = build_detail_observations(source_root, population_effective_at)
    return [*prices, *populations, *details], [*price_rejected, *population_rejected, *detail_quarantined]


def create_landing_manifest(
    source_root: Path,
    mode: str,
    effective_at: datetime,
    previous_manifest: Mapping[str, Any] | None = None,
    observation_effective_at: datetime | None = None,
) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    aggregate = hashlib.sha256()
    for path in sorted((item for item in source_root.rglob("*") if item.is_file()), key=lambda item: item.as_posix().lower()):
        relative = path.relative_to(source_root).as_posix()
        digest = sha256_file(path)
        record = {"path": relative, "bytes": path.stat().st_size, "sha256": digest}
        records.append(record)
        aggregate.update(canonical_json(record))
    price_effective_at = observation_effective_at or effective_at
    accepted, rejected = build_constituent_observations(source_root, price_effective_at)
    payload_hash = aggregate.hexdigest()
    run_key = sha256_bytes(f"g10|{mode}|{iso_utc(effective_at)}|{payload_hash}".encode())
    previous_files = {
        str(record.get("path")): str(record.get("sha256"))
        for record in (previous_manifest or {}).get("files", [])
        if isinstance(record, Mapping)
    }
    current_files = {record["path"]: record["sha256"] for record in records}
    changed_files = [record for record in records if previous_files.get(record["path"]) != record["sha256"]]
    deleted_files = sorted(path for path in previous_files if path not in current_files)
    return {
        "schemaVersion": "2.0.0",
        "runId": run_key,
        "mode": mode,
        "effectiveAt": iso_utc(effective_at),
        "observationEffectiveAt": iso_utc(price_effective_at),
        "parentRunId": (previous_manifest or {}).get("runId"),
        "payloadSha256": payload_hash,
        "fileCount": len(records),
        "changedFileCount": len(records) if mode == "full" else len(changed_files),
        "deletedFileCount": 0 if mode == "full" else len(deleted_files),
        "cardCount": len(accepted) + len(rejected),
        "accepted": len(accepted),
        "quarantined": 0,
        "rejected": len(rejected),
        "files": records,
        "changedFiles": records if mode == "full" else changed_files,
        "deletedFiles": [] if mode == "full" else deleted_files,
    }


def latest_landing_manifest(landing_root: Path) -> Mapping[str, Any] | None:
    manifests = sorted(
        (path for path in (landing_root / "g10").rglob("manifest.json") if path.is_file()),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )
    return read_json(manifests[0]) if manifests else None


def freeze_landing(
    source_root: Path,
    landing_root: Path,
    mode: str,
    effective_at: datetime,
    archive_payload: bool,
    observation_effective_at: datetime | None = None,
) -> tuple[Path, dict[str, Any], bool]:
    previous_manifest = latest_landing_manifest(landing_root) if mode == "incremental" else None
    manifest = create_landing_manifest(
        source_root,
        mode,
        effective_at,
        previous_manifest,
        observation_effective_at=observation_effective_at,
    )
    if mode == "full":
        run_dir = landing_root / "g10" / "full" / manifest["runId"]
    else:
        run_dir = landing_root / "g10" / "incremental" / effective_at.date().isoformat() / manifest["runId"]
    manifest_path = run_dir / "manifest.json"
    if manifest_path.is_file():
        existing = read_json(manifest_path)
        if existing != manifest:
            raise RuntimeError(f"immutable landing manifest mismatch: {manifest_path}")
        return manifest_path, manifest, True
    run_dir.mkdir(parents=True, exist_ok=False)
    if archive_payload:
        payload_root = run_dir / "payload"
        for record in manifest["changedFiles"]:
            source = source_root / record["path"]
            destination = payload_root / record["path"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            if sha256_file(destination) != record["sha256"]:
                raise RuntimeError(f"archived payload hash mismatch: {record['path']}")
    manifest_path.write_bytes(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n")
    return manifest_path, manifest, False


class ObservationLedger:
    """Small deterministic ledger used by replay tests and batch preparation."""

    def __init__(self) -> None:
        self._keys: set[str] = set()

    def append(self, observations: Sequence[Mapping[str, Any]]) -> dict[str, int | bool]:
        inserted = 0
        for observation in observations:
            key = str(observation["observationKey"])
            if key not in self._keys:
                self._keys.add(key)
                inserted += 1
        return {"inserted": inserted, "replayed": inserted == 0 and bool(observations)}


@dataclass
class LandingReplay:
    prices: dict[tuple[str, str], dict[date, float]]
    grader_populations: dict[tuple[str, str, str], dict[date, int]]
    grader_totals: dict[tuple[str, str, str], dict[date, int]]
    batch_count: int
    accepted_price_anchors: int
    accepted_population_anchors: int
    ignored_same_date_retries: int


def load_landing_replay(landing_root: Path) -> LandingReplay:
    """Replay immutable canonical batches into deterministic daily anchors.

    Only observations carrying an explicit ``observedDate`` are accepted. This
    deliberately excludes the legacy batch format that conflated fetch time
    with the upstream price date. Batches are applied by fetch time. An exact
    same-priority payload is a retry, while a later changed payload replaces
    the daily anchor, matching the normalized database upsert contract.
    """

    batches: list[tuple[datetime, str, Path, Mapping[str, Any]]] = []
    # G10, GemRate and SNK all land as immutable canonical batches below the
    # same private root.  Public code only sees the deterministic replay.
    for path in landing_root.rglob("canonical-batch.json"):
        if not path.is_file():
            continue
        document = read_json(path)
        if not isinstance(document, Mapping):
            continue
        fetched_raw = document.get("fetchedAt")
        fetched_at = parse_effective_at(str(fetched_raw)) if fetched_raw else datetime.fromtimestamp(0, tz=timezone.utc)
        batches.append((fetched_at, str(document.get("runId") or ""), path, document))
    batches.sort(key=lambda value: (value[0], value[1], value[2].as_posix()))

    prices: dict[tuple[str, str], dict[date, float]] = defaultdict(dict)
    populations: dict[tuple[str, str, str], dict[date, int]] = defaultdict(dict)
    totals: dict[tuple[str, str, str], dict[date, int]] = defaultdict(dict)
    price_anchors = 0
    population_anchors = 0
    retries = 0
    price_priorities: dict[tuple[str, str, date], int] = {}
    population_priorities: dict[tuple[str, str, str, date], int] = {}
    price_payloads: dict[tuple[str, str, date], bytes] = {}
    population_payloads: dict[tuple[str, str, str, date], bytes] = {}
    for _, _, _, batch in batches:
        observations = batch.get("observations")
        if not isinstance(observations, list):
            continue
        for observation in observations:
            if not isinstance(observation, Mapping):
                continue
            observed_raw = observation.get("observedDate")
            if not isinstance(observed_raw, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", observed_raw):
                continue
            try:
                observed_date = date.fromisoformat(observed_raw)
            except ValueError:
                continue
            source_code = str(observation.get("sourceCode") or "").casefold()
            external_id = str(observation.get("externalEntityId") or "")
            payload = observation.get("payload")
            kind = str(observation.get("observationKind") or "")
            if not source_code or not external_id or not isinstance(payload, Mapping):
                continue
            priority_raw = observation.get("sourcePriority", 100)
            priority = int(priority_raw) if isinstance(priority_raw, (int, float)) else 100
            if kind == "index_constituent":
                price = payload.get("priceUsd")
                if not isinstance(price, (int, float)) or price <= 0:
                    continue
                daily = prices[(source_code, external_id)]
                priority_key = (source_code, external_id, observed_date)
                existing_priority = price_priorities.get(priority_key)
                payload_signature = canonical_json(payload)
                if existing_priority is not None:
                    if priority < existing_priority:
                        continue
                    if priority == existing_priority and price_payloads[priority_key] == payload_signature:
                        retries += 1
                        continue
                daily[observed_date] = float(price)
                price_priorities[priority_key] = priority
                price_payloads[priority_key] = payload_signature
                if existing_priority is None:
                    price_anchors += 1
                continue
            if kind.startswith("grader_population_"):
                grader = str(payload.get("grader") or kind.rsplit("_", 1)[-1]).upper()
                value = payload.get("topGradePopulation")
                total = payload.get("total")
                if grader not in GRADERS or not isinstance(value, int) or value < 0:
                    continue
                if total is not None and (not isinstance(total, int) or total < value):
                    continue
                daily = populations[(source_code, external_id, grader)]
                priority_key = (source_code, external_id, grader, observed_date)
                existing_priority = population_priorities.get(priority_key)
                payload_signature = canonical_json(payload)
                if existing_priority is not None:
                    if priority < existing_priority:
                        continue
                    if priority == existing_priority and population_payloads[priority_key] == payload_signature:
                        retries += 1
                        continue
                daily[observed_date] = value
                if isinstance(total, int):
                    totals[(source_code, external_id, grader)][observed_date] = total
                else:
                    totals[(source_code, external_id, grader)].pop(observed_date, None)
                population_priorities[priority_key] = priority
                population_payloads[priority_key] = payload_signature
                if existing_priority is None:
                    population_anchors += 1

    return LandingReplay(
        prices=dict(prices),
        grader_populations=dict(populations),
        grader_totals=dict(totals),
        batch_count=len(batches),
        accepted_price_anchors=price_anchors,
        accepted_population_anchors=population_anchors,
        ignored_same_date_retries=retries,
    )


def parse_sale_at(raw: Any, discovered_at: datetime) -> tuple[datetime, str] | None:
    text = str(raw or "").strip().lower()
    if not text:
        return None
    relative = re.fullmatch(r"(\d+)\s+(minute|hour|day|week)s?\s+ago", text)
    if relative:
        count = int(relative.group(1))
        unit = relative.group(2)
        delta = {"minute": timedelta(minutes=count), "hour": timedelta(hours=count), "day": timedelta(days=count), "week": timedelta(weeks=count)}[unit]
        estimated_date = (discovered_at - delta).date()
        return datetime.combine(estimated_date, time.min, tzinfo=timezone.utc), "relative_date_bucket"
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%b %d, %Y"):
        try:
            parsed_date = datetime.strptime(text.title(), fmt).date()
            return datetime.combine(parsed_date, time.min, tzinfo=timezone.utc), "date"
        except ValueError:
            continue
    return None


@dataclass(frozen=True)
class SaleObservation:
    fingerprint: str
    sold_at: str
    discovered_at: str
    unit_price_usd: float
    quantity: int
    transaction_value_usd: float
    timestamp_quality: str
    source_date_text: str


def normalize_psa10_sales(
    rows: Sequence[Mapping[str, Any]],
    discovered_at: datetime,
    identity: str,
    quarantine: list[dict[str, Any]] | None = None,
    *,
    allow_relative_dates: bool = False,
) -> list[SaleObservation]:
    staged: list[dict[str, Any]] = []
    occurrences: Counter[str] = Counter()
    for row in rows:
        grade = re.sub(r"[^A-Z0-9]", "", str(row.get("grade") or "").upper())
        if grade != "PSA10" or str(row.get("currency") or "usd").lower() != "usd":
            continue
        parsed = parse_sale_at(row.get("date"), discovered_at)
        if parsed is None:
            continue
        if parsed[1] == "relative_date_bucket" and not allow_relative_dates:
            if quarantine is not None:
                quarantine.append({"reason": "relative_sale_date", "payloadHash": sha256_bytes(canonical_json(row))})
            continue
        price = row.get("price")
        quantity_raw = row.get("bundleSize", 1)
        transaction_raw = row.get("txAmount")
        if not isinstance(price, (int, float)) or price <= 0:
            continue
        if not isinstance(quantity_raw, (int, float)) or int(quantity_raw) <= 0:
            if quarantine is not None:
                quarantine.append({"reason": "invalid_bundle_quantity", "payloadHash": sha256_bytes(canonical_json(row))})
            continue
        quantity = int(quantity_raw)
        stated_price = float(price)
        if transaction_raw is None:
            if quantity > 1:
                if quarantine is not None:
                    quarantine.append({"reason": "ambiguous_bundle_amount", "payloadHash": sha256_bytes(canonical_json(row))})
                continue
            transaction_value = stated_price
        elif isinstance(transaction_raw, (int, float)) and transaction_raw > 0:
            transaction_value = float(transaction_raw)
        else:
            if quarantine is not None:
                quarantine.append({"reason": "invalid_transaction_value", "payloadHash": sha256_bytes(canonical_json(row))})
            continue
        expected_transaction = stated_price * quantity
        if abs(expected_transaction - transaction_value) > max(0.01, transaction_value * 0.01):
            # With no explicit upstream price-semantics flag, choosing either
            # ``price`` or ``txAmount / bundleSize`` would fabricate a unit
            # price. Keep the raw row private for review and omit it from the
            # public rolling aggregate.
            if quarantine is not None:
                quarantine.append({"reason": "ambiguous_bundle_amount", "payloadHash": sha256_bytes(canonical_json(row))})
            continue
        unit_price = stated_price
        sold_at, quality = parsed
        base = canonical_json(
            {
                "identity": identity,
                "soldAt": iso_utc(sold_at),
                "grade": "PSA10",
                "unitPrice": round(unit_price, 6),
                "quantity": quantity,
                "transactionValue": round(transaction_value, 6),
                "timestampQuality": quality,
            }
        )
        base_hash = sha256_bytes(base)
        occurrence = occurrences[base_hash]
        occurrences[base_hash] += 1
        staged.append(
            {
                "fingerprint": sha256_bytes(f"{base_hash}|{occurrence}".encode()),
                "sold_at": iso_utc(sold_at),
                "discovered_at": iso_utc(discovered_at),
                "unit_price_usd": round(unit_price, 6),
                "quantity": quantity,
                "transaction_value_usd": round(transaction_value, 6),
                "timestamp_quality": quality,
                "source_date_text": str(row.get("date") or ""),
            }
        )
    return [SaleObservation(**item) for item in staged]


def aggregate_sales(sales: Sequence[SaleObservation], as_of: datetime, days: int) -> dict[str, Any]:
    start = as_of - timedelta(days=days)
    selected = [sale for sale in sales if start <= parse_effective_at(sale.sold_at) <= as_of]
    if not selected:
        return {"count": None, "valueUsd": None, "coverage": "unavailable", "asOf": iso_utc(as_of)}
    return {
        "count": len(selected),
        "valueUsd": round(sum(sale.transaction_value_usd for sale in selected), 6),
        "coverage": "partial",
        "asOf": iso_utc(as_of),
    }


def derive_price_windows(daily_prices: Mapping[date, float], as_of: date) -> dict[str, dict[str, Any]]:
    positive = {key: float(value) for key, value in daily_prices.items() if value > 0 and key <= as_of}
    if not positive:
        return {window: {"value": None, "status": "unavailable", "asOf": None} for window in WINDOW_DAYS}
    latest_date = max(positive)
    current = positive[latest_date]
    earliest = min(positive)
    result: dict[str, dict[str, Any]] = {}
    for window, days in WINDOW_DAYS.items():
        target = latest_date - timedelta(days=days)
        tolerance = 1 if days == 1 else 2 if days == 7 else 3
        # The latest close can be within the 1d tolerance of its own target.
        # It is never a valid anchor: doing so manufactures a ready 0% change
        # from a single observation.
        candidates = [
            (abs((key - target).days), key, value)
            for key, value in positive.items()
            if key < latest_date and abs((key - target).days) <= tolerance
        ]
        if candidates:
            _, anchor_date, anchor = min(candidates, key=lambda item: (item[0], item[1] > target, item[1]))
            result[window] = {
                "value": round(((current / anchor) - 1) * 100, 6),
                "status": "ready",
                "asOf": latest_date.isoformat(),
                "anchorAt": anchor_date.isoformat(),
            }
        elif earliest > target:
            result[window] = {"value": None, "status": "accumulating", "asOf": None}
        else:
            result[window] = {"value": None, "status": "unavailable", "asOf": None}
    return result


def normalize_grader_populations(document: Mapping[str, Any], effective_at: datetime) -> dict[str, dict[str, Any]]:
    rows = document.get("population")
    indexed: dict[str, Mapping[str, Any]] = {}
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, Mapping):
                grader = str(row.get("gradeName") or "").upper()
                if grader in GRADERS:
                    indexed[grader] = row
    result: dict[str, dict[str, Any]] = {}
    for grader in GRADERS:
        row = indexed.get(grader)
        total = row.get("total") if row else None
        top = row.get("topGrade") if row else None
        status = "ready" if isinstance(total, int) and total >= 0 and isinstance(top, int) and top >= 0 else "unavailable"
        result[grader] = {
            "topGrade": "10",
            "total": {"value": total if status == "ready" else None, "status": status, "asOf": iso_utc(effective_at) if status == "ready" else None, "estimated": False},
            "topGradePopulation": {"value": top if status == "ready" else None, "status": status, "asOf": iso_utc(effective_at) if status == "ready" else None, "estimated": False},
        }
    return result


def self_test() -> dict[str, Any]:
    ledger = ObservationLedger()
    base_at = datetime(2026, 7, 22, 6, 30, tzinfo=timezone.utc)

    def observation(name: str, accepted: bool = True) -> dict[str, Any]:
        payload = {"name": name, "accepted": accepted}
        return {"observationKey": sha256_bytes(canonical_json(payload)), **payload}

    full_rows = [observation("a"), observation("b")]
    full = ledger.append(full_rows)
    replay = ledger.append(full_rows)
    incremental = ledger.append([observation("c")])
    raw_sales = [
        {"date": "2026-07-22", "grade": "PSA10", "price": 100, "txAmount": 200, "bundleSize": 2, "currency": "usd"},
        {"date": "2026-07-22", "grade": "PSA10", "price": 100, "bundleSize": 2, "currency": "usd"},
        {"date": "2026-07-21", "grade": "PSA 10", "price": 100, "currency": "usd"},
        {"date": "2026-07-21", "grade": "PSA 10", "price": 100, "currency": "usd"},
        {"date": "2026-07-21", "grade": "PSA 9", "price": 50, "currency": "usd"},
    ]
    sale_quarantine: list[dict[str, Any]] = []
    sales = normalize_psa10_sales(raw_sales, base_at, "fixture-card", sale_quarantine)
    repeated_day_one = normalize_psa10_sales(
        [
            {"date": "2026-07-21", "grade": "PSA 10", "price": 100, "currency": "usd"},
            {"date": "2026-07-21", "grade": "PSA 10", "price": 100, "currency": "usd"},
        ],
        base_at,
        "fixture-card",
    )
    repeated_day_two = normalize_psa10_sales(
        [
            {"date": "2026-07-21", "grade": "PSA 10", "price": 100, "currency": "usd"},
            {"date": "2026-07-21", "grade": "PSA 10", "price": 100, "currency": "usd"},
        ],
        base_at + timedelta(days=1),
        "fixture-card",
    )
    aggregate = aggregate_sales(sales, base_at, 7)
    windows = derive_price_windows({date(2026, 7, 21): 100, date(2026, 7, 22): 110}, date(2026, 7, 22))
    single_close = derive_price_windows({date(2026, 7, 22): 110}, date(2026, 7, 22))
    graders = normalize_grader_populations(
        {"population": [{"gradeName": grader, "total": 10, "topGrade": 5} for grader in GRADERS]},
        base_at,
    )
    with tempfile.TemporaryDirectory(prefix="cardz-g10-observation-") as directory:
        source_root = Path(directory)
        constituents = source_root / "index" / "ptcg" / "constituents.json"
        constituents.parent.mkdir(parents=True)
        constituents.write_text(
            json.dumps({"rows": [{"name": "Pikachu #001", "priceUsd": 10, "url": "https://private.invalid/card/source/card-1"}]}),
            encoding="utf-8",
        )
        populations_path = source_root / "cards" / "source" / "card-1" / "populations.json"
        populations_path.parent.mkdir(parents=True)
        populations_path.write_text(
            json.dumps({"population": [{"gradeName": "PSA", "total": 1500, "topGrade": 1200}]}),
            encoding="utf-8",
        )
        morning = build_constituent_observations(source_root, base_at)[0][0]
        afternoon = build_constituent_observations(source_root, base_at + timedelta(hours=8))[0][0]
        next_day = build_constituent_observations(source_root, base_at + timedelta(days=1))[0][0]
        fixed_price_at = base_at - timedelta(days=1)
        first_fetch = create_landing_manifest(
            source_root,
            "full",
            base_at,
            observation_effective_at=fixed_price_at,
        )
        next_fetch = create_landing_manifest(
            source_root,
            "incremental",
            base_at + timedelta(days=1),
            first_fetch,
            observation_effective_at=fixed_price_at,
        )
        daily_observations, _ = build_daily_observations(source_root, fixed_price_at, base_at)
        grader_observation = next(
            item for item in daily_observations if item["observationKind"] == "grader_population_psa"
        )
    with tempfile.TemporaryDirectory(prefix="cardz-landing-replay-") as directory:
        landing_root = Path(directory)

        def replay_batch(run: str, observed_date: str, fetched_at: str, price: float, population: int) -> None:
            run_root = landing_root / "g10" / "incremental" / observed_date / run
            run_root.mkdir(parents=True)
            observations = [
                {
                    "sourceCode": "scope-a",
                    "externalEntityId": "card-1",
                    "observationKind": "index_constituent",
                    "observedDate": observed_date,
                    "sourcePriority": 100,
                    "payload": {"priceUsd": price},
                },
                {
                    "sourceCode": "scope-a",
                    "externalEntityId": "card-1",
                    "observationKind": "grader_population_psa",
                    "observedDate": observed_date,
                    "sourcePriority": 100,
                    "payload": {"grader": "PSA", "topGradePopulation": population, "total": population * 2},
                },
            ]
            (run_root / "canonical-batch.json").write_text(
                json.dumps({"runId": run, "fetchedAt": fetched_at, "observations": observations}),
                encoding="utf-8",
            )

        replay_batch("day-0", "2026-07-14", "2026-07-14T06:30:00Z", 999, 9999)
        replay_batch("day-0-retry", "2026-07-14", "2026-07-14T07:00:00Z", 999, 9999)
        replay_batch("day-0-correction", "2026-07-14", "2026-07-14T07:30:00Z", 100, 1000)
        replay_batch("day-1", "2026-07-15", "2026-07-15T06:30:00Z", 110, 1100)
        replay_batch("day-7", "2026-07-21", "2026-07-21T06:30:00Z", 121, 1200)
        landing = load_landing_replay(landing_root)
        price_key = ("scope-a", "card-1")
        population_key = ("scope-a", "card-1", "PSA")
        two_day_prices = {
            day: value for day, value in landing.prices[price_key].items() if day <= date(2026, 7, 15)
        }
        landing_replay = {
            "priceAnchorCount": len(landing.prices[price_key]),
            "populationAnchorCount": len(landing.grader_populations[population_key]),
            "populationTotalRetained": landing.grader_totals[population_key][date(2026, 7, 21)] == 2400,
            "identicalSameDateRetryIgnored": landing.ignored_same_date_retries == 2,
            "laterPriceCorrectionWins": landing.prices[price_key][date(2026, 7, 14)] == 100,
            "laterPopulationCorrectionWins": landing.grader_populations[population_key][date(2026, 7, 14)] == 1000,
            "twoDay1d": derive_price_windows(two_day_prices, date(2026, 7, 15))["1d"],
            "twoDay7d": derive_price_windows(two_day_prices, date(2026, 7, 15))["7d"],
            "eightDay7d": derive_price_windows(landing.prices[price_key], date(2026, 7, 21))["7d"],
            "populationEightDay7d": derive_price_windows(
                landing.grader_populations[population_key], date(2026, 7, 21)
            )["7d"],
        }
    return {
        "full": {"accepted": int(full["inserted"]), "rejected": 1},
        "replay": replay,
        "incremental": incremental,
        "sales": {
            "count": aggregate["count"],
            "valueUsd": aggregate["valueUsd"],
            "bundleUnitPrice": sales[0].unit_price_usd,
            "samePriceTransactions": sum(sale.unit_price_usd == 100 and sale.quantity == 1 for sale in sales),
            "sameSaleStableAcrossFetches": [sale.fingerprint for sale in repeated_day_one]
            == [sale.fingerprint for sale in repeated_day_two],
            "sameDayDuplicatesRemainDistinct": len({sale.fingerprint for sale in repeated_day_one}) == 2,
            "ambiguousBundleQuarantined": len(sale_quarantine) == 1
            and sale_quarantine[0]["reason"] == "ambiguous_bundle_amount",
        },
        "observationIdentity": {
            "sameDayKeyStable": morning["observationKey"] == afternoon["observationKey"],
            "nextDayKeyChanged": morning["observationKey"] != next_day["observationKey"],
            "fullTimestampRetained": morning["effectiveAt"] != afternoon["effectiveAt"],
            "fixedPriceDateAcrossFetches": first_fetch["observationEffectiveAt"]
            == next_fetch["observationEffectiveAt"],
            "separateFetchTimeRetained": first_fetch["effectiveAt"] != next_fetch["effectiveAt"],
            "populationUsesFetchDate": grader_observation["observedDate"] == base_at.date().isoformat(),
            "populationValueRetained": grader_observation["payload"]["topGradePopulation"] == 1200,
        },
        "windows": windows,
        "singleCloseWindows": single_close,
        "graders": list(graders),
        "landingReplay": landing_replay,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument(
        "--source-root",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "integrations" / "grade10" / "data",
    )
    parser.add_argument("--landing-root", type=Path, default=Path(__file__).resolve().parents[1] / "data" / "runtime" / "private-landing")
    parser.add_argument("--mode", choices=("full", "incremental"), default="incremental")
    parser.add_argument("--effective-at")
    parser.add_argument("--observation-effective-at")
    parser.add_argument("--archive-payload", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(self_test(), sort_keys=True))
        return
    effective_at = parse_effective_at(args.effective_at)
    observation_effective_at = parse_effective_at(args.observation_effective_at) if args.observation_effective_at else effective_at
    source_root = args.source_root.resolve()
    if not source_root.is_dir():
        raise SystemExit(f"source root does not exist: {source_root}")
    manifest_path, manifest, replayed = freeze_landing(
        source_root,
        args.landing_root.resolve(),
        args.mode,
        effective_at,
        args.archive_payload,
        observation_effective_at=observation_effective_at,
    )
    observations, rejected = build_daily_observations(
        source_root,
        observation_effective_at,
        effective_at,
    )
    batch = {
        "runId": manifest["runId"],
        "mode": args.mode,
        "effectiveAt": iso_utc(observation_effective_at),
        "fetchedAt": manifest["effectiveAt"],
        "payloadSha256": manifest["payloadSha256"],
        "observations": observations,
        "rejected": rejected,
    }
    batch_path = manifest_path.parent / "canonical-batch.json"
    if not batch_path.exists():
        batch_path.write_bytes(json.dumps(batch, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n")
    print(
        json.dumps(
            {
                "runId": manifest["runId"],
                "mode": args.mode,
                "manifest": str(manifest_path),
                "batch": str(batch_path),
                "fileCount": manifest["fileCount"],
                "changedFileCount": manifest["changedFileCount"],
                "deletedFileCount": manifest["deletedFileCount"],
                "cardCount": manifest["cardCount"],
                "accepted": len(observations),
                "rejected": len(rejected),
                "replayed": replayed,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
