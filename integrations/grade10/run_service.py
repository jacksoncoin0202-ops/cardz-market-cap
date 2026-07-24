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
from typing import Iterator


ROOT = Path(__file__).resolve().parent
DATA_ROOT = ROOT / "data"
MANIFEST_PATH = ROOT / "UPSTREAM_MANIFEST.json"
SCRAPER = ROOT / "grade10_scraper.py"
ANALYTICS = ROOT / "grade10_analytics.py"
KLINE = ROOT / "grade10_kline.py"
LOCK_PATH = DATA_ROOT / "_state" / "collector.lock"


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


def validate_collection(expected_cards: int, *, scope: str, not_before: datetime) -> dict[str, object]:
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
    detail_count = 0
    if scope in {"cards", "full"}:
        for source, external_id in cards:
            card_root = DATA_ROOT / "cards" / source / external_id
            asset = card_root / "asset_info.json"
            populations = card_root / "populations.json"
            if not asset.is_file() or not populations.is_file():
                continue
            if not isinstance(read_json(asset), dict) or not isinstance(read_json(populations), dict):
                continue
            detail_count += 1
        if detail_count != expected_cards:
            raise RuntimeError(f"collector detail coverage failed: {detail_count} != {expected_cards}")
    return {
        "status": "collected",
        "cardCount": card_count,
        "indexedCards": len(cards),
        "detailCards": detail_count if scope in {"cards", "full"} else None,
        "lastRun": last_run.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "dataRoot": str(DATA_ROOT),
    }


def collect(scope: str, *, skip_images: bool, expected_cards: int, timeout: int) -> dict[str, object]:
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
        return validate_collection(expected_cards, scope=scope, not_before=started_at)


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
