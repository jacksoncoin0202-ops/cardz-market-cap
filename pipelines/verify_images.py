"""Verify that every public snapshot image is local, content-addressed, and intact."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify(
    snapshot_path: Path,
    assets_path: Path,
    manifest_path: Path | None = None,
    strict_semantic: bool = False,
) -> list[str]:
    errors: list[str] = []
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    cards = [*snapshot.get("top100", []), *snapshot.get("watchlist", [])]
    if len(snapshot.get("top100", [])) != 100:
        errors.append("top100 does not contain exactly 100 cards")

    seen: set[str] = set()
    qc_by_hash: dict[str, dict] = {}
    if manifest_path is not None:
        if not manifest_path.is_file():
            errors.append("image QC manifest is missing")
        else:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            qc_by_hash = {
                str(record.get("contentSha256")): record
                for record in manifest.get("records", [])
                if isinstance(record, dict) and record.get("publicAllowed")
            }
    for card in cards:
        image = card.get("image", {})
        filename = Path(image.get("src", "")).name
        if not filename or filename in {".", ".."}:
            errors.append(f"{card.get('id')}: invalid image path")
            continue
        if filename in seen:
            continue
        seen.add(filename)
        path = assets_path / filename
        if not path.is_file():
            errors.append(f"{card.get('id')}: image is missing")
            continue
        actual_sha = sha256_file(path)
        if actual_sha != image.get("sha256"):
            errors.append(f"{card.get('id')}: image hash mismatch")
        if not filename.startswith(actual_sha):
            errors.append(f"{card.get('id')}: image is not content-addressed")
        if manifest_path is not None:
            qc = qc_by_hash.get(actual_sha)
            if qc is None:
                errors.append(f"{card.get('id')}: image has no public-allowed QC record")
            elif strict_semantic and qc.get("semanticMatchStatus") != "human_or_vision_confirmed":
                errors.append(f"{card.get('id')}: image semantic QC is not human_or_vision_confirmed")
        try:
            with Image.open(path) as opened:
                if list(opened.size) != [image.get("width"), image.get("height")]:
                    errors.append(f"{card.get('id')}: image dimensions do not match")
        except Exception:
            errors.append(f"{card.get('id')}: image cannot be decoded")

    extras = sorted(path.name for path in assets_path.glob("*") if path.is_file() and path.name not in seen)
    if extras:
        errors.append(f"public image directory contains {len(extras)} unreferenced files")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path, default=ROOT / "data" / "public" / "seed-snapshot.json")
    parser.add_argument("--assets", type=Path, default=ROOT / "data" / "public" / "market-assets")
    parser.add_argument("--manifest", type=Path, default=ROOT / "manifests" / "image-qc.json")
    parser.add_argument("--strict-semantic", action="store_true")
    args = parser.parse_args()
    errors = verify(args.snapshot, args.assets, args.manifest, args.strict_semantic)
    print(json.dumps({"imagesVerified": len(list(args.assets.glob('*'))), "errors": errors}, sort_keys=True))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
