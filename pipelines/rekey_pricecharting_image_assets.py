#!/usr/bin/env python3
"""Rekey unreadable image assets to already-landed exact PriceCharting bytes.

This is deliberately a metadata-only recovery.  Dry-run is the default;
``--write`` is required before a transaction can update a row.  It never
downloads, copies, transforms, or changes image content, source pointers, QC,
or catalog identity.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from db_runtime import add_connection_args, connection_from_args  # noqa: E402

REPORT_DEFAULT = ROOT / "data" / "runtime" / "private-reports" / "canonical-db-qc" / "full-stock-printing3-20260731T0615JST" / "report.json"
APPROVED_ROOT = ROOT / "data" / "private" / "pricecharting_images"
RECEIPT_DIR = ROOT / "data" / "runtime" / "private-reports" / "image-rekey"


def load_db_env() -> None:
    env_path = ROOT / "data" / "runtime" / "config" / "backend.env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def relative_approved_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(APPROVED_ROOT.resolve()).as_posix()
    except ValueError as exc:
        raise RuntimeError(f"candidate escapes approved root: {path}") from exc


def resolve_old_path(key: str, content_sha256: str) -> Path | None:
    key = str(key or "").strip()
    candidates: list[Path] = []
    if key:
        path = Path(key)
        candidates.append(path if path.is_absolute() else ROOT / path)
    candidates.append(ROOT / "data" / "public" / "market-assets" / f"{content_sha256}.webp")
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None


def pc_byte_index(root: Path = APPROVED_ROOT) -> dict[str, Path]:
    index: dict[str, Path] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix.casefold() in {".jpg", ".jpeg", ".png", ".webp"}:
            digest = sha256_file(path)
            if digest in index:
                raise RuntimeError(f"ambiguous PriceCharting byte SHA: {digest}")
            index[digest] = path.resolve()
    return index


def report_targets(report: Mapping[str, Any]) -> dict[int, str]:
    targets: dict[int, str] = {}
    for card in report.get("cards") or []:
        if "image_asset_missing" not in (card.get("blockers") or []):
            continue
        image = ((card.get("facts") or {}).get("image") or {})
        asset_id = image.get("assetId")
        content_sha256 = card.get("imageSha256") or image.get("contentSha256")
        if not isinstance(asset_id, int) or not isinstance(content_sha256, str) or len(content_sha256) != 64:
            raise RuntimeError(f"invalid QC image target for variant {card.get('variantId')}")
        if asset_id in targets and targets[asset_id] != content_sha256:
            raise RuntimeError(f"conflicting QC target for asset {asset_id}")
        targets[asset_id] = content_sha256.casefold()
    return targets


def build_plan(rows: Sequence[Mapping[str, Any]], targets: Mapping[int, str], index: Mapping[str, Path]) -> dict[str, Any]:
    planned: list[dict[str, Any]] = []
    replayed: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    seen: set[int] = set()
    for source in rows:
        row = dict(source)
        asset_id = int(row["id"])
        expected_sha = targets.get(asset_id)
        if expected_sha is None:
            continue
        seen.add(asset_id)
        content_sha = str(row.get("content_sha256") or "").casefold()
        candidate = index.get(expected_sha)
        old_key = str(row.get("private_object_key") or "")
        if str(row.get("image_kind") or "") != "raw_front":
            blocked.append({"assetId": asset_id, "reason": "not_raw_front"})
        elif content_sha != expected_sha:
            blocked.append({"assetId": asset_id, "reason": "qc_content_sha_mismatch"})
        elif candidate is None:
            blocked.append({"assetId": asset_id, "reason": "local_exact_bytes_missing"})
        else:
            relative = relative_approved_path(candidate)
            new_key = f"data/private/pricecharting_images/{relative}"
            if sha256_file(candidate) != content_sha:
                blocked.append({"assetId": asset_id, "reason": "local_sha_mismatch"})
            elif old_key == new_key:
                replayed.append({"assetId": asset_id, "variantId": int(row["variant_id"])})
            elif resolve_old_path(old_key, content_sha) is not None:
                blocked.append({"assetId": asset_id, "reason": "old_path_still_readable"})
            else:
                planned.append({
                    "assetId": asset_id,
                    "variantId": int(row["variant_id"]),
                    "contentSha256": content_sha,
                    "oldPrivateObjectKey": old_key,
                    "newPrivateObjectKey": new_key,
                })
    for asset_id in sorted(set(targets) - seen):
        blocked.append({"assetId": asset_id, "reason": "asset_not_current"})
    planned.sort(key=lambda item: item["assetId"])
    replayed.sort(key=lambda item: item["assetId"])
    blocked.sort(key=lambda item: item["assetId"])
    plan = {"targetCount": len(targets), "rekeys": planned, "replayed": replayed, "blocked": blocked}
    plan["planSha256"] = hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return plan


def write_receipt(plan: Mapping[str, Any], changed: int) -> Path:
    RECEIPT_DIR.mkdir(parents=True, exist_ok=True)
    receipt = {
        "schemaVersion": 1,
        "operation": "exact_hash_private_object_rekey",
        "planSha256": plan["planSha256"],
        "changedRows": changed,
        "rekeys": plan["rekeys"],
        "recordedAt": datetime.now(timezone.utc).isoformat(),
    }
    path = RECEIPT_DIR / f"{plan['planSha256']}.json"
    try:
        with path.open("x", encoding="utf-8") as handle:
            json.dump(receipt, handle, indent=2, sort_keys=True)
            handle.write("\n")
    except FileExistsError:
        pass
    return path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    add_connection_args(parser)
    parser.add_argument("--report", type=Path, default=REPORT_DEFAULT)
    parser.add_argument("--write", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    load_db_env()
    report = json.loads(args.report.read_text(encoding="utf-8"))
    targets = report_targets(report)
    index = pc_byte_index()
    connection = connection_from_args(args)
    try:
        with connection.cursor() as cursor:
            marks = ", ".join(["%s"] * len(targets))
            cursor.execute(
                f"""
                SELECT a.id, a.variant_id, a.image_kind, a.content_sha256, a.private_object_key
                FROM market_image_asset a
                JOIN (
                    SELECT variant_id, MAX(CONCAT(DATE_FORMAT(captured_at, '%%Y%%m%%d%%H%%i%%s%%f'), LPAD(id, 20, '0'))) AS current_key
                    FROM market_image_asset
                    WHERE image_kind='raw_front'
                    GROUP BY variant_id
                ) current ON current.variant_id=a.variant_id
                    AND CONCAT(DATE_FORMAT(a.captured_at, '%%Y%%m%%d%%H%%i%%s%%f'), LPAD(a.id, 20, '0'))=current.current_key
                WHERE a.id IN ({marks})
                """,
                tuple(sorted(targets)),
            )
            plan = build_plan(cursor.fetchall(), targets, index)
            plan["write"] = bool(args.write)
            if plan["blocked"] or not args.write:
                connection.rollback()
                print(json.dumps(plan, indent=2, sort_keys=True))
                return 2 if plan["blocked"] else 0
            changed = 0
            for item in plan["rekeys"]:
                cursor.execute(
                    """
                    UPDATE market_image_asset
                    SET private_object_key=%s
                    WHERE id=%s AND variant_id=%s AND image_kind='raw_front'
                      AND content_sha256=%s AND private_object_key=%s
                    """,
                    (item["newPrivateObjectKey"], item["assetId"], item["variantId"], item["contentSha256"], item["oldPrivateObjectKey"]),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError(f"asset CAS failed: {item['assetId']}")
                changed += 1
            connection.commit()
            plan["changedRows"] = changed
            plan["receipt"] = str(write_receipt(plan, changed).relative_to(ROOT)).replace("\\", "/")
            print(json.dumps(plan, indent=2, sort_keys=True))
            return 0
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
