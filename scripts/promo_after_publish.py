#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Scheduled promo-pack consumer. Read-only. **NEVER posts.**

Registered by the Windows installer as `\\CARDZ-Promo-After-Publish`, 17:45 JST
daily, run hidden through `scripts/cardz_silent_run.vbs`. Its whole job is to
exercise the promo chain every day so the pack + gates cannot rot unnoticed:
read the live published snapshot, apply the freshness gate, render the copy for
every configured destination, and write pack + receipts to disk. Posting stays a
human-triggered `promo_post.py compose --confirm`.

Deliberately imports **only** `promo_chain` — never `promo_post`, playwright,
websocket, or any other module that can open a browser. `test_promo_pack.py`
asserts that after this module runs, none of them are in `sys.modules`.

Exit codes: 0 ok (PROMO_PACK_OK) / 3 stale live (PROMO_PACK_STALE) /
2 build error (PROMO_PACK_ERROR).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import promo_chain as P  # noqa: E402

DEST_FILE = ROOT / "scripts" / "promo_destinations.json"
DEST_EXAMPLE = ROOT / "scripts" / "promo_destinations.example.json"


def resolve_destinations_file(explicit: str | None = None) -> tuple[Path, list[str]]:
    """Real file if present, else the example — but say so out loud."""
    if explicit:
        return Path(explicit), []
    if DEST_FILE.is_file():
        return DEST_FILE, []
    return DEST_EXAMPLE, [
        f"PROMO_PACK_WARN {DEST_FILE.name} missing; falling back to {DEST_EXAMPLE.name}"
    ]


def destination_channels(path: Path) -> tuple[list[str], list[str]]:
    """Keys of the destinations file, filtered to channels the chain can render."""
    if not path.is_file():
        raise P.PromoError(f"destinations file missing: {path}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise P.PromoError(f"destinations file is not an object: {path}")
    channels: list[str] = []
    warnings: list[str] = []
    for key in raw:
        name = str(key)
        if name in P.CHANNEL_SCRIPT:
            channels.append(name)
        else:
            warnings.append(f"PROMO_PACK_WARN unknown destination {name!r} skipped")
    if not channels:
        raise P.PromoError(f"no usable destination in {path}")
    return channels, warnings


def load_live(live_json: str | None, *, period: str = P.DAILY_PERIOD) -> dict[str, Any]:
    """Same read as build_live_brief; --live-json swaps the HTTP GET for a file."""
    if not live_json:
        return P.build_live_brief(period=period)
    raw = json.loads(Path(live_json).read_text(encoding="utf-8"))
    health = raw.get("health") or {}
    scoped = raw.get("scoped") or {}
    return P.brief_from_payload(health, scoped, period=period)


def build_pack(
    brief: Mapping[str, Any],
    channels: Sequence[str],
    out_dir: Path,
    *,
    business_date: str,
) -> list[str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "brief.json").write_text(
        json.dumps(brief, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    copies = brief.get("copies") or {}
    written: list[str] = []
    for channel in channels:
        text = copies.get(channel)
        if not text:
            raise P.PromoError(f"{channel}: brief has no copy (board missing from live payload)")
        P.assert_text(channel, text)
        (out_dir / f"{channel}.txt").write_text(text, encoding="utf-8", newline="\n")
        P.write_action_receipt(
            business_date=business_date,
            destination=channel,
            dry_run=True,
            fill_only=False,
            posted=False,
            text=text,
            live_generated_at=brief.get("generatedAt"),
            lag_hours=brief.get("lagHours"),
            outcome="built",
        )
        written.append(channel)
    return written


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build the daily promo pack from the published snapshot. Never posts."
    )
    parser.add_argument("--live-json", help="read {health, scoped} from a file instead of live HTTP")
    parser.add_argument("--destinations", help="destinations JSON (default scripts/promo_destinations.json)")
    parser.add_argument("--out-dir", help="pack dir (default <PROMO_RUNTIME_DIR>/<business_date>)")
    parser.add_argument("--business-date", help="YYYY-MM-DD (default: today JST)")
    args = parser.parse_args(argv)

    business_date = str(args.business_date or P.business_date_jst())
    try:
        dest_path, warnings = resolve_destinations_file(args.destinations)
        channels, more = destination_channels(dest_path)
        for line in warnings + more:
            print(line, file=sys.stderr)
        brief = load_live(args.live_json)
        out_dir = Path(args.out_dir) if args.out_dir else P.promo_runtime_dir() / business_date
        written = build_pack(brief, channels, out_dir, business_date=business_date)
    except P.PromoStaleLive as error:
        print(f"PROMO_PACK_STALE {business_date} {error}")
        return 3
    except Exception as error:  # noqa: BLE001 — one scheduled task, one exit code
        print(f"PROMO_PACK_ERROR {business_date} {type(error).__name__}: {error}")
        return 2
    lag = brief.get("lagHours")
    lag_text = "unknown" if lag is None else f"{float(lag):.2f}"
    print(f"PROMO_PACK_OK {business_date} destinations={len(written)} lag_h={lag_text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
