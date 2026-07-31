#!/usr/bin/env python3
"""Cross-platform entrypoint for the vendored private Grade10 collector.

This runner deliberately separates raw acquisition from CARDZ canonical
derivation.  The legacy analytics and synthetic K-line scripts remain
available for replay compatibility, but are never run by the production
CARDZ ranking pipeline.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Iterable, Iterator


ROOT = Path(__file__).resolve().parent
DATA_ROOT = ROOT / "data"
MANIFEST_PATH = ROOT / "UPSTREAM_MANIFEST.json"
SCRAPER = ROOT / "grade10_scraper.py"
ANALYTICS = ROOT / "grade10_analytics.py"
KLINE = ROOT / "grade10_kline.py"
LOCK_PATH = DATA_ROOT / "_state" / "collector.lock"
KNOWN_MINIMUM_ASSET_INFO = 595


@contextmanager
def singleton_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        handle.close()
        raise RuntimeError("another Grade10 collector run is active") from error
    try:
        yield
    finally:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def self_check() -> dict[str, object]:
    manifest = read_json(MANIFEST_PATH)
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), list):
        raise RuntimeError("upstream manifest is invalid")
    verified: list[str] = []
    for row in manifest["files"]:
        if not isinstance(row, dict):
            raise RuntimeError("upstream manifest row is invalid")
        path = ROOT / str(row.get("path") or "")
        if not path.is_file():
            raise RuntimeError(f"vendored dependency is missing: {path.name}")
        if path.stat().st_size != int(row.get("bytes") or -1) or sha256(path) != row.get("sha256"):
            raise RuntimeError(f"vendored dependency hash mismatch: {path.name}")
        if path.suffix == ".py":
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        verified.append(path.name)
    if importlib.util.find_spec("requests") is None:
        raise RuntimeError("Python dependency 'requests' is not installed")
    return {
        "status": "ready",
        "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "platform": sys.platform,
        "verifiedFiles": verified,
        "dataRoot": str(DATA_ROOT),
    }


def run_script(script: Path, arguments: list[str], timeout: int) -> None:
    subprocess.run(
        [sys.executable, "-X", "utf8", str(script), *arguments],
        check=True,
        cwd=ROOT,
        timeout=timeout,
    )


def indexed_cards() -> set[tuple[str, str]]:
    cards: set[tuple[str, str]] = set()
    for index_name in ("ptcg", "ptcg100", "opcg"):
        path = DATA_ROOT / "index" / index_name / "constituents.json"
        if not path.is_file():
            raise RuntimeError(f"collector index output is incomplete: {path.relative_to(ROOT)}")
        document = read_json(path)
        rows = document.get("rows") if isinstance(document, dict) else None
        if not isinstance(rows, list):
            raise RuntimeError(f"collector index output is invalid: {path.relative_to(ROOT)}")
        for row in rows:
            if not isinstance(row, dict):
                continue
            parts = str(row.get("url") or "").rstrip("/").split("/")
            if len(parts) < 2 or not parts[-1] or not parts[-2]:
                continue
            source = "altxyz" if parts[-2].casefold() == "ebay" else parts[-2].casefold()
            cards.add((source, parts[-1]))
    return cards


def valid_json_object(path: Path) -> bool:
    try:
        return path.is_file() and isinstance(read_json(path), dict)
    except (OSError, ValueError):
        return False


def detail_coverage(
    cards: Iterable[tuple[str, str]] | None = None,
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    missing_assets: list[tuple[str, str]] = []
    missing_populations: list[tuple[str, str]] = []
    for source, external_id in sorted(cards if cards is not None else indexed_cards()):
        card_root = DATA_ROOT / "cards" / source / external_id
        if not valid_json_object(card_root / "asset_info.json"):
            missing_assets.append((source, external_id))
        if not valid_json_object(card_root / "populations.json"):
            missing_populations.append((source, external_id))
    return missing_assets, missing_populations


def missing_detail_cards(
    cards: Iterable[tuple[str, str]] | None = None,
) -> list[tuple[str, str]]:
    missing_assets, missing_populations = detail_coverage(cards)
    return sorted(set(missing_assets) | set(missing_populations))


def load_scraper() -> ModuleType:
    spec = importlib.util.spec_from_file_location("cardz_grade10_scraper", SCRAPER)
    if spec is None or spec.loader is None:
        raise RuntimeError("Grade10 scraper module cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def repair_missing_details(
    missing: Iterable[tuple[str, str]],
    *,
    skip_images: bool,
) -> int:
    targets = list(dict.fromkeys(missing))
    if not targets:
        return 0
    scraper = load_scraper()
    cards = {
        (str(card.get("source") or "").casefold(), str(card.get("id") or "")): card
        for card in scraper.enumerate_cards()
    }
    unresolved = [identity for identity in targets if identity not in cards]
    if unresolved:
        rendered = ", ".join(f"{source}:{external_id}" for source, external_id in unresolved)
        raise RuntimeError(f"missing Grade10 repair identities from current indexes: {rendered}")
    for identity in targets:
        scraper.scrape_card(cards[identity], skip_images=skip_images)
    return len(targets)


def validate_collection(
    expected_cards: int,
    *,
    scope: str,
    not_before: datetime,
    minimum_asset_info: int | None = None,
) -> dict[str, object]:
    state_path = DATA_ROOT / "_state" / "last_run.json"
    if not state_path.is_file():
        raise RuntimeError("collector did not produce data/_state/last_run.json")
    state = read_json(state_path)
    if not isinstance(state, dict):
        raise RuntimeError("collector state is invalid")
    card_count = int(state.get("cardCount") or 0)
    if card_count != expected_cards:
        raise RuntimeError(f"collector completeness failed: {card_count} != {expected_cards}")
    last_run = datetime.fromisoformat(str(state.get("lastRun") or "").replace("Z", "+00:00"))
    if last_run.tzinfo is None:
        raise RuntimeError("collector lastRun must include a timezone")
    if last_run.astimezone(timezone.utc) < not_before.astimezone(timezone.utc):
        raise RuntimeError("collector lastRun predates the current invocation")
    cards = indexed_cards()
    if len(cards) != expected_cards:
        raise RuntimeError(f"collector index identity coverage failed: {len(cards)} != {expected_cards}")
    asset_count = 0
    population_count = 0
    missing_assets: list[tuple[str, str]] = []
    if scope in {"cards", "full"}:
        missing_assets, missing_populations = detail_coverage(cards)
        asset_count = len(cards) - len(missing_assets)
        population_count = len(cards) - len(missing_populations)
        if population_count != expected_cards:
            raise RuntimeError(
                f"collector population detail coverage failed: {population_count} != {expected_cards}"
            )
        required_assets = (
            min(expected_cards, KNOWN_MINIMUM_ASSET_INFO)
            if minimum_asset_info is None
            else minimum_asset_info
        )
        if required_assets < 0 or required_assets > expected_cards:
            raise RuntimeError("minimum asset-info coverage must be between zero and expected cards")
        if asset_count < required_assets:
            raise RuntimeError(
                f"collector asset identity coverage failed: {asset_count} < {required_assets}"
            )
    return {
        "status": "collected",
        "cardCount": card_count,
        "indexedCards": len(cards),
        "detailCards": asset_count if scope in {"cards", "full"} else None,
        "assetInfoCards": asset_count if scope in {"cards", "full"} else None,
        "populationCards": population_count if scope in {"cards", "full"} else None,
        "missingAssetInfo": [
            {"source": source, "externalId": external_id}
            for source, external_id in missing_assets
        ],
        "lastRun": last_run.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "dataRoot": str(DATA_ROOT),
    }


def collect(
    scope: str,
    *,
    skip_images: bool,
    expected_cards: int,
    timeout: int,
    minimum_asset_info: int | None = None,
) -> dict[str, object]:
    arguments: list[str] = []
    if scope == "index":
        arguments.append("--index-only")
    elif scope == "cards":
        arguments.append("--cards-only")
    if skip_images:
        arguments.append("--skip-images")
    with singleton_lock(LOCK_PATH):
        started_at = datetime.now(timezone.utc)
        run_script(SCRAPER, arguments, timeout)
        if scope in {"cards", "full"}:
            repair_missing_details(missing_detail_cards(), skip_images=skip_images)
        return validate_collection(
            expected_cards,
            scope=scope,
            not_before=started_at,
            minimum_asset_info=minimum_asset_info,
        )


def legacy_derive(timeout: int) -> dict[str, object]:
    if not (DATA_ROOT / "_state" / "last_run.json").is_file():
        raise RuntimeError("collect source data before running legacy derivation")
    with singleton_lock(LOCK_PATH):
        run_script(ANALYTICS, [], timeout)
        run_script(KLINE, [], timeout)
    return {
        "status": "legacy-derived",
        "canonical": False,
        "warning": "legacy analytics and synthetic OHLC are not CARDZ ranking inputs",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Portable Grade10 private acquisition service")
    subparsers = parser.add_subparsers(dest="action", required=True)
    subparsers.add_parser("self-check", help="offline hash, syntax and dependency check")
    collect_parser = subparsers.add_parser("collect", help="run raw private acquisition")
    collect_parser.add_argument("--scope", choices=("index", "cards", "full"), default="index")
    collect_parser.add_argument("--skip-images", action="store_true")
    collect_parser.add_argument("--expected-cards", type=int, default=600)
    collect_parser.add_argument("--minimum-asset-info", type=int, default=KNOWN_MINIMUM_ASSET_INFO)
    collect_parser.add_argument("--timeout-seconds", type=int, default=5_400)
    legacy_parser = subparsers.add_parser("legacy-derive", help="compatibility output; never canonical")
    legacy_parser.add_argument("--timeout-seconds", type=int, default=5_400)
    args = parser.parse_args()

    if args.action == "self-check":
        result = self_check()
    elif args.action == "collect":
        result = collect(
            args.scope,
            skip_images=args.skip_images,
            expected_cards=args.expected_cards,
            timeout=args.timeout_seconds,
            minimum_asset_info=args.minimum_asset_info,
        )
    else:
        result = legacy_derive(args.timeout_seconds)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None
