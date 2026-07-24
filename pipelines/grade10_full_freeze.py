#!/usr/bin/env python3
"""Freeze one complete, read-only Grade10 source generation privately.

The Grade10 sibling checkout is an acquisition input only.  This tool copies a
verified complete source tree into CARDZ's ignored private landing area and
writes a small tracked proof manifest with the explicit identity quarantine.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT.parent / "grade10-scraper" / "data"
DEFAULT_LANDING = ROOT / "data" / "runtime" / "private-landing"
DEFAULT_MANIFEST = ROOT / "manifests" / "g10-full-freeze.json"
INDEXES = ("ptcg", "ptcg100", "opcg")
SOURCE_REF = re.compile(r"/card/([^/]+)/([^/?#]+)", re.IGNORECASE)
SECRET_FILE_NAMES = {".env", ".env.local", "id_rsa"}
SECRET_KEY = re.compile(r"(?:^|[_-])(?:api[_-]?key|authorization|bearer|cookie|password|secret|token)(?:$|[_-])", re.IGNORECASE)
SIGNED_URL = re.compile(r"(?:x-amz-(?:credential|signature)|x-goog-signature|[?&](?:access_token|token|api_key)=)", re.IGNORECASE)


class FreezeError(RuntimeError):
    """The private full-source freeze cannot be proven complete or safe."""


def canonical_json(value: Any, *, pretty: bool = False) -> bytes:
    if pretty:
        return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json_value(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        raise FreezeError(f"invalid JSON source file: {path}") from error


def read_json(path: Path) -> Mapping[str, Any]:
    value = read_json_value(path)
    if not isinstance(value, Mapping):
        raise FreezeError(f"JSON source file must be an object: {path}")
    return value


def iso_utc(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise FreezeError("source lastRun must be an ISO timestamp") from error
    if parsed.tzinfo is None:
        raise FreezeError("source lastRun must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def storage_source(source_code: str) -> str:
    return "altxyz" if source_code.casefold() == "ebay" else source_code.casefold()


def validate_no_secrets(value: Any, relative: str) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if SECRET_KEY.search(str(key)):
                raise FreezeError(f"secret-like JSON key is not allowed: {relative}")
            validate_no_secrets(nested, relative)
    elif isinstance(value, list):
        for nested in value:
            validate_no_secrets(nested, relative)
    elif isinstance(value, str) and SIGNED_URL.search(value):
        raise FreezeError(f"signed credential-like URL is not allowed: {relative}")


def source_files(source_root: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in sorted(source_root.rglob("*"), key=lambda item: item.as_posix().casefold()):
        if not path.is_file():
            if path.is_symlink():
                raise FreezeError(f"symlinked source path is not allowed: {path}")
            continue
        relative = path.relative_to(source_root).as_posix()
        name = path.name.casefold()
        if name in SECRET_FILE_NAMES or name.endswith((".pem", ".key", ".p12", ".pfx")):
            raise FreezeError(f"secret-like source file is not allowed: {relative}")
        if path.is_symlink():
            raise FreezeError(f"symlinked source file is not allowed: {relative}")
        if path.suffix.casefold() == ".json":
            validate_no_secrets(read_json_value(path), relative)
        records.append({"path": relative, "bytes": path.stat().st_size, "sha256": sha256_file(path)})
    if not records:
        raise FreezeError("source tree has no files")
    return records


def indexed_cards(source_root: Path) -> dict[tuple[str, str], dict[str, str]]:
    cards: dict[tuple[str, str], dict[str, str]] = {}
    for index_name in INDEXES:
        path = source_root / "index" / index_name / "constituents.json"
        if not path.is_file():
            continue
        rows = read_json(path).get("rows")
        if not isinstance(rows, list):
            raise FreezeError(f"constituents rows are invalid: {path}")
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            match = SOURCE_REF.search(str(row.get("url") or ""))
            if match is None:
                continue
            source_code, external_id = match.group(1).casefold(), match.group(2)
            cards.setdefault(
                (storage_source(source_code), external_id),
                {"sourceCode": storage_source(source_code), "externalId": external_id},
            )
    if not cards:
        raise FreezeError("no source identities found in constituents")
    return cards


def coverage(source_root: Path, cards: Mapping[tuple[str, str], Mapping[str, str]]) -> tuple[dict[str, int], list[dict[str, str]]]:
    population_count = 0
    asset_count = 0
    quarantine: list[dict[str, str]] = []
    for source_code, external_id in sorted(cards):
        card_root = source_root / "cards" / source_code / external_id
        population_path = card_root / "populations.json"
        if not population_path.is_file():
            raise FreezeError(f"population coverage failed: missing {source_code}/{external_id}")
        population = read_json(population_path).get("population")
        if not isinstance(population, list):
            raise FreezeError(f"population coverage failed: invalid {source_code}/{external_id}")
        population_count += 1
        asset_path = card_root / "asset_info.json"
        if not asset_path.is_file():
            quarantine.append({"sourceCode": source_code, "externalId": external_id, "reason": "missing_asset_info"})
            continue
        read_json(asset_path)
        asset_count += 1
    return {
        "cardCount": len(cards),
        "populations": population_count,
        "assetInfo": asset_count,
        "quarantined": len(quarantine),
    }, quarantine


def build_landing_manifest(source_root: Path, effective_at: str, records: list[dict[str, Any]], coverage_counts: Mapping[str, int], quarantine: list[dict[str, str]]) -> dict[str, Any]:
    payload_hash = hashlib.sha256(b"".join(canonical_json(record) for record in records)).hexdigest()
    run_id = hashlib.sha256(canonical_json({"effectiveAt": effective_at, "payloadSha256": payload_hash, "schemaVersion": "1.0.0"})).hexdigest()
    return {
        "schemaVersion": "1.0.0",
        "role": "private_full_freeze",
        "mode": "full",
        "runId": run_id,
        "effectiveAt": effective_at,
        "payloadSha256": payload_hash,
        "fileCount": len(records),
        "coverage": dict(coverage_counts),
        "quarantine": quarantine,
        "files": records,
    }


def copy_payload(source_root: Path, payload_root: Path, records: Iterable[Mapping[str, Any]]) -> None:
    for record in records:
        relative = str(record["path"])
        source = source_root / relative
        destination = payload_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        if destination.stat().st_size != int(record["bytes"]) or sha256_file(destination) != record["sha256"]:
            raise FreezeError(f"payload copy hash mismatch: {relative}")


def private_manifest(landing_manifest: Mapping[str, Any], landing_manifest_sha256: str) -> dict[str, Any]:
    return {
        "schemaVersion": landing_manifest["schemaVersion"],
        "role": landing_manifest["role"],
        "mode": landing_manifest["mode"],
        "runId": landing_manifest["runId"],
        "effectiveAt": landing_manifest["effectiveAt"],
        "payloadSha256": landing_manifest["payloadSha256"],
        "fileCount": landing_manifest["fileCount"],
        "coverage": landing_manifest["coverage"],
        "quarantine": landing_manifest["quarantine"],
        "landingManifestSha256": landing_manifest_sha256,
    }


def merge_existing_manifest(manifest_path: Path, proof: Mapping[str, Any]) -> dict[str, Any]:
    """Retain unrelated historical audit fields in the named proof manifest."""

    if not manifest_path.is_file():
        return dict(proof)
    existing = read_json(manifest_path)
    retained = {key: value for key, value in existing.items() if key not in proof and key != "intake"}
    return {**retained, **proof}


def freeze(source_root: Path, landing_root: Path, manifest_path: Path, *, expected_cards: int = 600, expected_asset_info: int = 595) -> dict[str, Any]:
    source_root = source_root.resolve()
    if not source_root.is_dir():
        raise FreezeError(f"source root does not exist: {source_root}")
    state = read_json(source_root / "_state" / "last_run.json")
    effective_at = iso_utc(str(state.get("lastRun") or ""))
    records = source_files(source_root)
    cards = indexed_cards(source_root)
    coverage_counts, quarantine = coverage(source_root, cards)
    if coverage_counts["cardCount"] != expected_cards:
        raise FreezeError(f"card coverage failed: {coverage_counts['cardCount']} != {expected_cards}")
    if coverage_counts["populations"] != expected_cards:
        raise FreezeError(f"population coverage failed: {coverage_counts['populations']} != {expected_cards}")
    if coverage_counts["assetInfo"] != expected_asset_info:
        raise FreezeError(f"asset identity coverage failed: {coverage_counts['assetInfo']} != {expected_asset_info}")
    if coverage_counts["quarantined"] != expected_cards - expected_asset_info:
        raise FreezeError("quarantine coverage does not account for each missing identity")
    landing_manifest = build_landing_manifest(source_root, effective_at, records, coverage_counts, quarantine)
    run_root = landing_root.resolve() / "g10" / "full" / str(landing_manifest["runId"])
    landing_manifest_bytes = canonical_json(landing_manifest, pretty=True)
    if run_root.exists():
        existing = run_root / "manifest.json"
        if not existing.is_file() or existing.read_bytes() != landing_manifest_bytes:
            raise FreezeError(f"immutable landing conflict: {run_root}")
        replayed = True
    else:
        run_root.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f".{landing_manifest['runId']}.pending-", dir=run_root.parent))
        try:
            copy_payload(source_root, staging / "payload", records)
            (staging / "manifest.json").write_bytes(landing_manifest_bytes)
            os.replace(staging, run_root)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        replayed = False
    proof = private_manifest(landing_manifest, hashlib.sha256(landing_manifest_bytes).hexdigest())
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_bytes(canonical_json(merge_existing_manifest(manifest_path, proof), pretty=True))
    return {**proof, "manifest": str(manifest_path), "landing": str(run_root), "replayed": replayed}


def verify(manifest_path: Path, landing_root: Path) -> dict[str, Any]:
    proof = read_json(manifest_path)
    run_root = landing_root.resolve() / "g10" / "full" / str(proof.get("runId") or "")
    landing_path = run_root / "manifest.json"
    if not landing_path.is_file() or hashlib.sha256(landing_path.read_bytes()).hexdigest() != proof.get("landingManifestSha256"):
        raise FreezeError("landing manifest hash does not match proof manifest")
    landing_manifest = read_json(landing_path)
    for key in ("runId", "effectiveAt", "payloadSha256", "fileCount", "coverage", "quarantine"):
        if proof.get(key) != landing_manifest.get(key):
            raise FreezeError(f"landing manifest proof mismatch: {key}")
    for record in landing_manifest.get("files", []):
        if not isinstance(record, Mapping):
            raise FreezeError("landing manifest file record is invalid")
        payload = run_root / "payload" / str(record.get("path") or "")
        if not payload.is_file() or payload.stat().st_size != record.get("bytes") or sha256_file(payload) != record.get("sha256"):
            raise FreezeError(f"landing payload hash mismatch: {record.get('path')}")
    return dict(proof)


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--landing-root", type=Path, default=DEFAULT_LANDING)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--expected-cards", type=int, default=600)
    parser.add_argument("--expected-asset-info", type=int, default=595)
    parser.add_argument("--verify", action="store_true")
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = verify(args.manifest, args.landing_root) if args.verify else freeze(
            args.source_root,
            args.landing_root,
            args.manifest,
            expected_cards=args.expected_cards,
            expected_asset_info=args.expected_asset_info,
        )
    except FreezeError as error:
        print(f"Grade10 full freeze error: {error}")
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
