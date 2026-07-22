"""Stream-verify private zstd archives against the import manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import zstandard


ROOT = Path(__file__).resolve().parents[1]


def decompressed_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    decompressor = zstandard.ZstdDecompressor()
    with path.open("rb") as compressed, decompressor.stream_reader(compressed) as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=ROOT / "manifests" / "legacy-import.json")
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    checks = [
        (
            ROOT / manifest["source"]["archive"],
            manifest["source"]["sha256"],
            "source",
        ),
        (
            ROOT / manifest["canonical"]["archive"],
            manifest["canonical"]["sha256"],
            "canonical",
        ),
    ]
    errors: list[str] = []
    verified: dict[str, str] = {}
    for path, expected, label in checks:
        actual = decompressed_sha256(path)
        verified[label] = actual
        if actual != expected:
            errors.append(f"{label} archive decompressed hash mismatch")
    print(json.dumps({"verified": verified, "errors": errors}, sort_keys=True))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
