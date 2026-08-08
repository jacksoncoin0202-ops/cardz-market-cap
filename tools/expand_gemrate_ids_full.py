# -*- coding: utf-8 -*-
"""Expand pipelines/gemrate_ids.txt to the FULL GemRate id universe.

036 Gate 1: the discovery/pop-land universe must merge every id ever seen,
not only current exact bindings (the pre-036 version read match_status='exact'
only, which silently dropped rejected / manual_review / legacy ids):

  1. catalog_source_identity source_code='gemrate' — ALL match_status rows
     (exact + rejected + manual_review + anything else).  Retired GemRate ids
     still resolve on the provider side, so they stay in the universe.
  2. market_source_observation source_code='gemrate' — legacy observation ids.
  3. Raw cache dirs data/private/gemrate/cards/<id>/ in BOTH checkouts.
  4. Existing gemrate_ids.txt in BOTH checkouts (prior discovery output).

Usage:
  python -X utf8 tools/expand_gemrate_ids_full.py
  python -X utf8 tools/expand_gemrate_ids_full.py --run-daily
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "pipelines"))

from qualified_pool_operator import db, load_env

IDS = ROOT / "pipelines" / "gemrate_ids.txt"
BACKUP = ROOT / "pipelines" / "gemrate_ids.txt.bak_before_full"
DEFAULT_LEGACY_CHECKOUT = Path(
    "C:/Users/jackson0202/Documents/Playground/cardz-market-cap"
)
ID_PATTERN = re.compile(r"^[0-9a-f]{32,64}$")


def valid_id(value: str) -> bool:
    return bool(ID_PATTERN.fullmatch(value.strip().lower()))


def ids_from_db() -> tuple[set[str], set[str]]:
    cur = db().cursor()
    cur.execute(
        """
        SELECT DISTINCT external_entity_id
        FROM catalog_source_identity
        WHERE source_code='gemrate'
          AND external_entity_id IS NOT NULL
        """
    )
    identity = {
        str(row["external_entity_id"]).strip().lower()
        for row in cur.fetchall()
    }
    cur.execute(
        """
        SELECT DISTINCT external_entity_id
        FROM market_source_observation
        WHERE source_code='gemrate'
          AND external_entity_id IS NOT NULL
        """
    )
    observation = {
        str(row["external_entity_id"]).strip().lower()
        for row in cur.fetchall()
    }
    return (
        {value for value in identity if valid_id(value)},
        {value for value in observation if valid_id(value)},
    )


def ids_from_cards_cache(checkout: Path) -> set[str]:
    cards = checkout / "data" / "private" / "gemrate" / "cards"
    if not cards.is_dir():
        return set()
    return {
        entry.name.lower()
        for entry in cards.iterdir()
        if entry.is_dir() and valid_id(entry.name)
    }


def ids_from_file(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    return {
        line.strip().lower()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#") and valid_id(line)
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-daily", action="store_true", help="after expand, run gemrate daily")
    ap.add_argument(
        "--legacy-checkout",
        type=Path,
        default=DEFAULT_LEGACY_CHECKOUT,
        help="old checkout whose raw cache / ids file also feed the universe",
    )
    args = ap.parse_args()
    load_env()

    identity_ids, observation_ids = ids_from_db()
    cache_here = ids_from_cards_cache(ROOT)
    cache_legacy = ids_from_cards_cache(args.legacy_checkout)
    file_here = ids_from_file(IDS)
    file_legacy = ids_from_file(args.legacy_checkout / "pipelines" / "gemrate_ids.txt")

    if IDS.is_file() and not BACKUP.is_file():
        BACKUP.write_text(IDS.read_text(encoding="utf-8"), encoding="utf-8")

    merged = sorted(
        identity_ids
        | observation_ids
        | cache_here
        | cache_legacy
        | file_here
        | file_legacy
    )
    IDS.write_text("\n".join(merged) + "\n", encoding="utf-8")
    print(
        "gemrate_ids universe: "
        f"identity(all-status)={len(identity_ids)} "
        f"observation={len(observation_ids)} "
        f"cache(new)={len(cache_here)} cache(legacy)={len(cache_legacy)} "
        f"file(new)={len(file_here)} file(legacy)={len(file_legacy)} "
        f"→ merged={len(merged)}"
    )
    if args.run_daily:
        env = dict(**__import__("os").environ)
        # load_env already set process env
        cmd = [
            sys.executable,
            "-X",
            "utf8",
            str(ROOT / "pipelines" / "gemrate_source.py"),
            "daily",
            "--ids-file",
            str(IDS),
        ]
        print("starting:", " ".join(cmd), flush=True)
        r = subprocess.run(cmd, cwd=str(ROOT), env=env)
        return r.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
