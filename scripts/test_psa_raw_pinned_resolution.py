#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regression guard for the 2026-08-09 outage: an incremental GemRate refresh
appends a new payload to the append-only ``raw/`` store and re-points
``card_details.raw.receipt.json``. Readers that only follow the receipt pointer
hand the caller bytes it never accepted, so every acceptance pinned to the older
sha is judged ``raw_literal_or_hash_mismatch`` even though nothing was lost.

Run standalone: python -X utf8 scripts/test_psa_raw_pinned_resolution.py
"""
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import psa_identity_repair  # noqa: E402
from psa_identity_repair import load_psa_raw  # noqa: E402

GEMRATE_ID = "a" * 40
ACCEPTED_NAME = "2024 One Piece Japanese OP09 Gol D. Roger Manga Alternate Art"
REFRESHED_NAME = "2024 One Piece Japanese OP09 Gol D. Roger Manga Alternate Art"


def payload(description: str, pop: int) -> bytes:
    body = {
        "gemrate_id": GEMRATE_ID,
        "description": description,
        "population_data": [
            {
                "grader": "PSA",
                "description": description,
                "year": "2024",
                "set_name": "One Piece Japanese OP09",
                "card_number": "099",
                "parallel": "Manga Alternate Art",
                "psa_10": pop,
            }
        ],
    }
    return json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")


def write_capture(card_dir: Path, raw_bytes: bytes) -> str:
    sha = hashlib.sha256(raw_bytes).hexdigest()
    (card_dir / "raw").mkdir(parents=True, exist_ok=True)
    (card_dir / "raw" / f"{sha}.json").write_bytes(raw_bytes)
    (card_dir / "card_details.raw.receipt.json").write_text(
        json.dumps(
            {
                "schemaVersion": "1.0.0",
                "gemrateId": GEMRATE_ID,
                "transport": "gemrate_public_card_page",
                "fetchedAt": "2026-08-08T18:54:41Z",
                "providerEntityGemrateId": GEMRATE_ID,
                "rawStatus": "captured",
                "contentSha256": sha,
                "sourcePointer": f"raw/{sha}.json",
            }
        ),
        encoding="utf-8",
    )
    return sha


def main() -> int:
    # load_psa_raw reports paths relative to ROOT and resolves symlinks, so the
    # fixture goes directly under the repo root: data/ is a junction pointing
    # outside it, and the system temp dir is outside it too.
    tmp = Path(tempfile.mkdtemp(prefix=".tmp-psa-raw-pin-", dir=ROOT))
    failures: list[str] = []
    try:
        card_dir = tmp / GEMRATE_ID
        psa_identity_repair.RAW_ROOT = tmp

        accepted_sha = write_capture(card_dir, payload(ACCEPTED_NAME, 1284))
        refreshed_sha = write_capture(card_dir, payload(REFRESHED_NAME, 1301))

        if accepted_sha == refreshed_sha:
            failures.append("fixture is inert: the refresh produced the same sha")

        # 1. No pin: the proposal path must keep reading the newest capture.
        latest = load_psa_raw(GEMRATE_ID)
        if latest.get("rawPayloadSha256") != refreshed_sha:
            failures.append(f"unpinned read returned {latest.get('rawPayloadSha256')}, expected the refreshed capture")
        if latest.get("resolvedBy") != "receipt_pointer":
            failures.append(f"unpinned read reported resolvedBy={latest.get('resolvedBy')}")

        # 2. Pinned: verification must get back the exact bytes it accepted,
        #    even though the receipt now points somewhere else.
        pinned = load_psa_raw(GEMRATE_ID, pinned_sha=accepted_sha)
        if pinned.get("rawPayloadSha256") != accepted_sha:
            failures.append(f"pinned read returned {pinned.get('rawPayloadSha256')}, expected the accepted capture")
        if pinned.get("resolvedBy") != "pinned_sha":
            failures.append(f"pinned read reported resolvedBy={pinned.get('resolvedBy')}")
        if (pinned.get("psa") or {}).get("psa_10") != 1284:
            failures.append("pinned read did not return the accepted payload body")

        # 3. A pin that is not on disk must not silently degrade to the newest
        #    capture and pass itself off as resolved: provenance fails closed.
        missing = load_psa_raw(GEMRATE_ID, pinned_sha="f" * 64)
        if missing.get("resolvedBy") != "receipt_pointer":
            failures.append("a pin with no stored payload was reported as resolved")
        if missing.get("rawPayloadSha256") == "f" * 64:
            failures.append("a pin with no stored payload fabricated a matching sha")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        for line in failures:
            print(f"FAIL {line}")
        return 1
    print("PASS pinned-sha resolution survives an incremental capture refresh")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
