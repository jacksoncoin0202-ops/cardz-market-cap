"""036 S6 file leg: replay local PriceCharting captures (PLAN §6.5).

Copy-first and read-only towards every source root: candidate HTML files are
content-gated by is_replayable(), passing files are copied (never moved) into
<new checkout>/data/private/pricecharting_session/html/replay-<generation>/
under the canonical {pcProductId}_{slug}.html name with a JSON sidecar acting
as the capture receipt. Rejects are never silent: every failing file lands in
replay-rejects.json with its reason and sha so the residual live-fetch list
can be built from it. This script touches files only; the orchestrator's S6
stage verifies sidecars and writes catalog_provider_capture_receipt rows.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pricecharting_page_parse as pc_parse

NEW_ROOT = Path(__file__).resolve().parents[1]
OLD_CHECKOUT_ROOT = Path("C:/Users/jackson0202/Documents/Playground/cardz-market-cap")
DEFAULT_SOURCE_ROOTS = [
    OLD_CHECKOUT_ROOT / "data" / "private" / "pricecharting_session",
    NEW_ROOT / "data" / "private" / "pricecharting_session",
]
GENERATION_RE = re.compile(r"^036_\d{8}T\d{6}Z$")
# Self-truthing parser fingerprint: the receipt names the exact parser build.
PARSER_VERSION = "pcparse_" + hashlib.sha256(
    (Path(__file__).resolve().parent / "pricecharting_page_parse.py").read_bytes()
).hexdigest()[:12]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _assert_write_target(path: Path) -> None:
    """Executable narrowing point (§6.5): writes must stay inside NEW_ROOT."""

    if os.path.commonpath([str(path.resolve()), str(NEW_ROOT)]) != str(NEW_ROOT):
        raise AssertionError(f"write target escapes the new checkout: {path}")


def is_replayable(text: str) -> tuple[bool, str, dict[str, Any] | None]:
    """Content gate (§6.5): four checks, cheapest first; returns parse too."""

    if "VGPC.chart_data" not in text:
        return False, "no_chart_data", None
    if "completed-auctions-manual-only" not in text:
        return False, "completed_auctions_tab_missing", None
    parsed = pc_parse.parse_product_html(text)
    if not parsed.get("ok"):
        return False, str(parsed.get("error") or "parse_failed"), None
    chart = parsed.get("chart") or {}
    if not chart:
        # The balanced-brace extraction found nothing usable: truncation.
        return False, "chart_data_unparseable", None
    manualonly = chart.get("manualonly") or {}
    series = manualonly.get("series") or []
    valid_points = [
        pt for pt in series
        if isinstance(pt, list) and len(pt) >= 2
        and isinstance(pt[0], (int, float)) and isinstance(pt[1], (int, float))
    ]
    if not valid_points:
        return False, "manualonly_missing_or_empty", None
    return True, "", parsed


def _slug_from_name(name: str) -> str:
    stem = name[:-5] if name.lower().endswith(".html") else name
    parts = stem.split("_")
    while parts and parts[0].isdigit():
        parts.pop(0)
    return "_".join(parts) or stem


def scan_sources(source_roots: list[Path]) -> list[Path]:
    files: list[Path] = []
    for root in source_roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.html")):
            if any(part.startswith("replay-") for part in path.parts):
                continue
            files.append(path)
    return files


def run_replay(generation: str, source_roots: list[Path], out_root: Path) -> dict[str, Any]:
    replay_dir = out_root / f"replay-{generation}"
    _assert_write_target(replay_dir)
    replay_dir.mkdir(parents=True, exist_ok=True)

    seen_shas: set[str] = set()
    rejects: list[dict[str, Any]] = []
    winners: dict[int, dict[str, Any]] = {}
    duplicates: list[dict[str, Any]] = []
    scanned = 0
    for path in scan_sources(source_roots):
        scanned += 1
        digest = sha256_file(path)
        if digest in seen_shas:
            continue
        seen_shas.add(digest)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as error:
            rejects.append({"file": str(path), "reason": f"unreadable:{error}", "sha256": digest})
            continue
        ok, reason, parsed = is_replayable(text)
        if not ok:
            rejects.append({"file": str(path), "reason": reason, "sha256": digest})
            continue
        product = (parsed or {}).get("product") or {}
        product_id = product.get("id")
        if not isinstance(product_id, int) or product_id <= 0:
            rejects.append({"file": str(path), "reason": "product_id_missing", "sha256": digest})
            continue
        manualonly = ((parsed or {}).get("chart") or {}).get("manualonly") or {}
        completed = (((parsed or {}).get("sales") or {})
                     .get("completed-auctions-manual-only") or {})
        candidate = {
            "sourcePath": str(path),
            "sha256": digest,
            "bytes": path.stat().st_size,
            "mtimeUtc": datetime.fromtimestamp(
                path.stat().st_mtime, tz=timezone.utc
            ).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "slug": _slug_from_name(path.name),
            "manualonlyPoints": int(manualonly.get("points") or 0),
            "lastUsd": manualonly.get("last_usd"),
            "completedAuctionCount": int(completed.get("count") or 0),
        }
        incumbent = winners.get(product_id)
        if incumbent is None:
            winners[product_id] = candidate
            continue
        # One replay file per product: most manualonly points wins, then more
        # bytes, then the lexicographically greater source path (deterministic).
        better = (
            (candidate["manualonlyPoints"], candidate["bytes"], candidate["sourcePath"])
            > (incumbent["manualonlyPoints"], incumbent["bytes"], incumbent["sourcePath"])
        )
        if better:
            duplicates.append({**incumbent, "supersededBy": candidate["sha256"], "pcProductId": product_id})
            winners[product_id] = candidate
        else:
            duplicates.append({**candidate, "supersededBy": incumbent["sha256"], "pcProductId": product_id})

    copied = 0
    for product_id, chosen in sorted(winners.items()):
        target = replay_dir / f"{product_id}_{chosen['slug']}.html"
        _assert_write_target(target)
        shutil.copyfile(chosen["sourcePath"], target)
        if sha256_file(target) != chosen["sha256"]:
            raise AssertionError(f"copy corrupted for product {product_id}")
        sidecar = {
            "schemaVersion": "1.0.0",
            "generation": generation,
            "pcProductId": product_id,
            "slug": chosen["slug"],
            "captureSha256": chosen["sha256"],
            "capturePath": str(target.relative_to(NEW_ROOT)).replace("\\", "/"),
            "sourcePath": chosen["sourcePath"],
            "capturedAtUtc": chosen["mtimeUtc"],
            "parserVersion": PARSER_VERSION,
            "manualonlyPoints": chosen["manualonlyPoints"],
            "lastUsd": chosen["lastUsd"],
            "completedAuctionCount": chosen["completedAuctionCount"],
        }
        sidecar_path = target.with_suffix(".json")
        _assert_write_target(sidecar_path)
        sidecar_path.write_text(
            json.dumps(sidecar, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        copied += 1

    reject_reasons: dict[str, int] = {}
    for row in rejects:
        key = row["reason"].split(":", 1)[0]
        reject_reasons[key] = reject_reasons.get(key, 0) + 1
    manifest = {
        "schemaVersion": "1.0.0",
        "generation": generation,
        "builtAtUtc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "parserVersion": PARSER_VERSION,
        "sourceRoots": [str(root) for root in source_roots],
        "filesScanned": scanned,
        "distinctShas": len(seen_shas),
        "products": copied,
        "duplicatesSuperseded": len(duplicates),
        "rejects": len(rejects),
        "rejectReasons": dict(sorted(reject_reasons.items())),
    }
    (replay_dir / "replay-rejects.json").write_text(
        json.dumps({"generation": generation, "rejects": rejects}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    (replay_dir / "replay-duplicates.json").write_text(
        json.dumps({"generation": generation, "duplicates": duplicates}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    (replay_dir / "replay-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8",
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="036 PriceCharting local cache replay (§6.5)")
    parser.add_argument("--generation", required=True)
    parser.add_argument(
        "--source-root", action="append", default=None,
        help="repeatable; defaults to both checkouts' pricecharting_session roots",
    )
    parser.add_argument(
        "--out-root",
        default=str(NEW_ROOT / "data" / "private" / "pricecharting_session" / "html"),
    )
    args = parser.parse_args()
    if not GENERATION_RE.match(args.generation):
        print(f"--generation must match {GENERATION_RE.pattern}", file=sys.stderr)
        return 2
    source_roots = [Path(root) for root in (args.source_root or [])] or DEFAULT_SOURCE_ROOTS
    manifest = run_replay(args.generation, source_roots, Path(args.out_root))
    print(json.dumps(manifest, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
