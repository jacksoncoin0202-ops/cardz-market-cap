"""Build the GemRate freeze "Pass C" spare-quota id list.

Why this exists
---------------
The GemRate service key dies 2026-07-29. The priority freeze (Pass A + Pass B,
184 cards / 368 calls) does not consume a whole daily quota window, and unspent
quota is a one-way door: it cannot be reclaimed after the key expires. Pass C
fills the remainder of every window with the highest-value roster cards that
still have no ``history_full.json`` at all.

Contract
--------
* Reads the roster ``data/runtime/private-source-map/tracked-gemrate-ids.txt``
  (1,468 ids -- the only current roster; see CLAUDE.md "Roster 檔案圖").
* Subtracts every id that already has ``data/private/gemrate/cards/<id>/history_full.json``.
  Run this AFTER Pass A/B so their fresh output is excluded automatically.
* Orders the remainder: ranked cards first (best index rank first), then
  market-cap desc, then roster file order. Ordering is file-based only --
  no DB, so a dead database never blocks the harvest.
* Writes ``data/runtime/private-source-map/freeze-pass-c-spare-ids.txt``.

The output is deliberately NOT under ``temp/``: a scheduled task must never
depend on a directory that gets cleaned (``tests/test_verify_doc_refs.py``).

Exit codes: 0 wrote a non-empty list / 3 nothing left to fetch / 2 input missing.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_MAP = ROOT / "data" / "runtime" / "private-source-map"
ROSTER = SOURCE_MAP / "tracked-gemrate-ids.txt"
UNIVERSE = SOURCE_MAP / "tracked-universe.json"
CARDS = ROOT / "data" / "private" / "gemrate" / "cards"
DEFAULT_OUT = SOURCE_MAP / "freeze-pass-c-spare-ids.txt"


def read_ids(path: Path) -> list[str]:
    # tracked-gemrate-ids.txt is CRLF; strip() is mandatory (see CLAUDE.md CRLF trap).
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def cards_with_history() -> set[str]:
    if not CARDS.is_dir():
        return set()
    return {d.name for d in CARDS.iterdir() if d.is_dir() and (d / "history_full.json").is_file()}


def priority_index() -> dict[str, tuple[int, float]]:
    """gemrate id -> (best index rank, market cap). Missing signals sort last."""
    if not UNIVERSE.is_file():
        return {}
    try:
        payload = json.loads(UNIVERSE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out: dict[str, tuple[int, float]] = {}
    for card in payload.get("cards", []):
        gid = card.get("gemrateId")
        if not gid:
            continue
        memberships = card.get("rankMemberships") or {}
        ranks = [int(v) for v in memberships.values() if isinstance(v, (int, float))]
        best = min(ranks) if ranks else 10**9
        cap = card.get("marketCapUsd")
        out[gid] = (best, float(cap) if isinstance(cap, (int, float)) else 0.0)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--limit", type=int, default=0, help="cap the list length (0 = no cap)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not ROSTER.is_file():
        print(f"roster missing: {ROSTER}", file=sys.stderr)
        return 2

    roster = read_ids(ROSTER)
    order = {gid: i for i, gid in enumerate(roster)}
    have = cards_with_history()
    missing = [gid for gid in roster if gid not in have]

    ranks = priority_index()
    missing.sort(key=lambda gid: (ranks.get(gid, (10**9, 0.0))[0], -ranks.get(gid, (10**9, 0.0))[1], order[gid]))
    if args.limit > 0:
        missing = missing[: args.limit]

    ranked_head = sum(1 for gid in missing if ranks.get(gid, (10**9, 0.0))[0] < 10**9)
    print(f"roster={len(roster)} have_history={len(have & set(roster))} missing={len(missing)} ranked_first={ranked_head}")

    if not missing:
        print("nothing left to fetch")
        return 3

    if args.dry_run:
        print(f"dry-run; would write {len(missing)} ids to {args.out}")
        return 0

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(missing) + "\n", encoding="utf-8")
    print(f"wrote {len(missing)} ids -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
