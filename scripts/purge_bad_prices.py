#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Clear bad PSA10 price observations — delete first, refill later.

Scopes (union of delete targets):
  1. risky (ebay/g10*) vs trusted median  > ratio OR < 1/ratio
  2. same-day / latest multi-source spread > ratio → delete non-trusted extreme
  3. within-source jump: consecutive same-source points with |ratio| > jump
  4. fake-today: today price == previous same-source price (stamp class)
  5. extreme window cards from FE snapshot flags: wipe ALL risky rows for those variants
  6. optional --wipe-variant-ids / --wipe-opaque: wipe ALL prices for named cards

Never invents replacements. Write only with --write.

  python -X utf8 scripts/purge_bad_prices.py
  python -X utf8 scripts/purge_bad_prices.py --write
  python -X utf8 scripts/purge_bad_prices.py --write --from-review
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / "data/runtime/config/backend.env"
TRUSTED = frozenset({"snk_psa10", "snk", "snkrdunk", "tcgpricelookup"})
RISKY = frozenset({"ebay", "g10_kline", "g10", "tcgfish"})
RATIO = 5.0
JUMP = 5.0  # same-source consecutive day jump


def load_env() -> None:
    if not ENV.is_file():
        return
    for line in ENV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip("\r").strip('"').strip("'"))
    os.environ.setdefault("CARDZ_DB_HOST", "127.0.0.1")


def connect():
    import pymysql

    load_env()
    return pymysql.connect(
        host=os.environ.get("CARDZ_DB_HOST", "127.0.0.1"),
        port=int(os.environ.get("CARDZ_DB_PORT", "3308")),
        user=os.environ["CARDZ_DB_USER"],
        password=os.environ["CARDZ_DB_PASSWORD"],
        database=os.environ.get("CARDZ_DB_NAME", "cardz_market_cap"),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )


def resolve_snapshot_flags() -> list[str]:
    """Opaque ids from last review D_outliers + report fakeZero if present."""
    out: list[str] = []
    review = ROOT / "docs/evidence/2026-07-29-price-review"
    csv_path = review / "D_outliers.csv"
    if csv_path.is_file():
        lines = csv_path.read_text(encoding="utf-8").splitlines()[1:]
        for line in lines:
            parts = line.split(",")
            if len(parts) >= 3 and parts[2].startswith("cmc_"):
                out.append(parts[2])
    report = review / "report.json"
    if report.is_file():
        doc = json.loads(report.read_text(encoding="utf-8"))
        for block in (
            doc.get("C", {}).get("snapshot", {}).get("top100", {}).get("fakeZero") or [],
            doc.get("C", {}).get("snapshot", {}).get("watchlist", {}).get("fakeZero") or [],
        ):
            if isinstance(block, dict) and block.get("id"):
                out.append(str(block["id"]))
            elif isinstance(block, list):
                for item in block:
                    if isinstance(item, dict) and item.get("id"):
                        out.append(str(item["id"]))
    # unique preserve order
    seen: set[str] = set()
    uniq: list[str] = []
    for oid in out:
        if oid not in seen:
            seen.add(oid)
            uniq.append(oid)
    return uniq


def main() -> int:
    ap = argparse.ArgumentParser(description="Purge bad price observations (delete only)")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--ratio", type=float, default=RATIO)
    ap.add_argument("--jump", type=float, default=JUMP)
    ap.add_argument("--from-review", action="store_true", help="wipe risky for review-flagged opaque ids")
    ap.add_argument(
        "--wipe-opaque",
        action="append",
        default=[],
        help="opaque_id to wipe ALL prices for (repeatable)",
    )
    ap.add_argument(
        "--wipe-all-prices-for-flagged",
        action="store_true",
        help="for review-flagged cards, delete ALL price rows (not only risky)",
    )
    ap.add_argument("--today", type=str, default=None, help="YYYY-MM-DD for fake-today")
    ap.add_argument(
        "--out",
        type=Path,
        default=ROOT / "docs/evidence/2026-07-29-price-review/PURGE_BAD_PRICES.json",
    )
    args = ap.parse_args()
    today = date.fromisoformat(args.today) if args.today else date.today()

    conn = connect()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, variant_id, source_code, price_usd, observed_date, effective_at
        FROM market_price_observation
        WHERE price_usd IS NOT NULL AND price_usd > 0
        """
    )
    all_rows = list(cur.fetchall())
    by_v: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for r in all_rows:
        by_v[int(r["variant_id"])].append(r)

    delete: dict[int, str] = {}  # id -> reason
    samples: list[dict[str, Any]] = []

    def mark(rid: int, reason: str, sample: dict[str, Any] | None = None) -> None:
        if rid in delete:
            return
        delete[rid] = reason
        if sample is not None and len(samples) < 80:
            samples.append(sample)

    # ── 1) risky vs full trusted median ──────────────────────────────
    for vid, rows in by_v.items():
        trusted_vals = [
            float(r["price_usd"])
            for r in rows
            if str(r["source_code"]).casefold() in TRUSTED
        ]
        if not trusted_vals:
            continue
        med = statistics.median(trusted_vals)
        if med <= 0:
            continue
        for r in rows:
            src = str(r["source_code"]).casefold()
            if src not in RISKY:
                continue
            p = float(r["price_usd"])
            if p > med * args.ratio or p < med / args.ratio:
                mark(
                    int(r["id"]),
                    "risky_vs_trusted_median",
                    {
                        "id": int(r["id"]),
                        "variant_id": vid,
                        "source": src,
                        "price": p,
                        "trusted_median": med,
                        "ratio": round(p / med, 2),
                        "reason": "risky_vs_trusted_median",
                    },
                )

    # ── 2) latest-per-source spread: delete extremes (any source) ────
    # When latest quotes disagree by >ratio, keep only points inside
    # [med/ratio, med*ratio]. Includes trusted-vs-trusted (wrong bind).
    for vid, rows in by_v.items():
        latest: dict[str, dict[str, Any]] = {}
        for r in rows:
            src = str(r["source_code"]).casefold()
            d = r["observed_date"]
            prev = latest.get(src)
            if prev is None or d > prev["observed_date"]:
                latest[src] = r
        if len(latest) < 2:
            continue
        prices = [float(r["price_usd"]) for r in latest.values()]
        lo, hi = min(prices), max(prices)
        if lo <= 0 or hi / lo <= args.ratio:
            continue
        trusted_latest = [
            float(r["price_usd"])
            for src, r in latest.items()
            if src in TRUSTED
        ]
        med = statistics.median(trusted_latest) if trusted_latest else statistics.median(prices)
        if med <= 0:
            continue
        for src, r in latest.items():
            p = float(r["price_usd"])
            if p > med * args.ratio or p < med / args.ratio:
                mark(
                    int(r["id"]),
                    "latest_spread_extreme",
                    {
                        "id": int(r["id"]),
                        "variant_id": vid,
                        "source": src,
                        "price": p,
                        "median": med,
                        "ratio": round(p / med, 2),
                        "reason": "latest_spread_extreme",
                    },
                )

    # ── 3) same-source series outliers (one-shot vs median, not peel) ─
    for vid, rows in by_v.items():
        by_src: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in rows:
            by_src[str(r["source_code"]).casefold()].append(r)
        for src, pts in by_src.items():
            if len(pts) < 3:
                continue
            series = [float(p["price_usd"]) for p in pts]
            med = statistics.median(series)
            if med <= 0:
                continue
            for r in pts:
                p = float(r["price_usd"])
                if p > med * args.jump or p < med / args.jump:
                    mark(
                        int(r["id"]),
                        f"same_source_outlier:{src}",
                        {
                            "id": int(r["id"]),
                            "variant_id": vid,
                            "source": src,
                            "price": p,
                            "series_median": med,
                            "ratio": round(p / med, 2),
                            "reason": f"same_source_outlier:{src}",
                        },
                    )

    # ── 4) fake-today identical stamp ────────────────────────────────
    for vid, rows in by_v.items():
        by_src: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in rows:
            by_src[str(r["source_code"]).casefold()].append(r)
        for src, pts in by_src.items():
            pts_sorted = sorted(pts, key=lambda x: x["observed_date"])
            for r in pts_sorted:
                d = r["observed_date"]
                if isinstance(d, datetime):
                    d = d.date()
                if d != today:
                    continue
                prev = [p for p in pts_sorted if p["observed_date"] < r["observed_date"]]
                if not prev:
                    continue
                last = prev[-1]
                if abs(float(r["price_usd"]) - float(last["price_usd"])) < 0.005:
                    mark(
                        int(r["id"]),
                        "fake_today_same_price",
                        {
                            "id": int(r["id"]),
                            "variant_id": vid,
                            "source": src,
                            "price": float(r["price_usd"]),
                            "prev_date": str(last["observed_date"]),
                            "reason": "fake_today_same_price",
                        },
                    )

    # ── 5/6) review-flagged cards ────────────────────────────────────
    wipe_opaque = list(args.wipe_opaque or [])
    if args.from_review or args.wipe_all_prices_for_flagged:
        wipe_opaque.extend(resolve_snapshot_flags())
    wipe_opaque = list(dict.fromkeys(wipe_opaque))

    flagged_vids: list[int] = []
    if wipe_opaque:
        ph = ",".join(["%s"] * len(wipe_opaque))
        cur.execute(
            f"SELECT id, opaque_id, canonical_name FROM catalog_variant WHERE opaque_id IN ({ph})",
            wipe_opaque,
        )
        for row in cur.fetchall():
            flagged_vids.append(int(row["id"]))
            vid = int(row["id"])
            rows = by_v.get(vid) or []
            for r in rows:
                src = str(r["source_code"]).casefold()
                if args.wipe_all_prices_for_flagged:
                    mark(
                        int(r["id"]),
                        "flagged_card_wipe_all",
                        {
                            "id": int(r["id"]),
                            "variant_id": vid,
                            "opaque": row["opaque_id"],
                            "name": row["canonical_name"],
                            "source": src,
                            "price": float(r["price_usd"]),
                            "reason": "flagged_card_wipe_all",
                        },
                    )
                elif src in RISKY or src == "tcgfish":
                    mark(
                        int(r["id"]),
                        "flagged_card_wipe_risky",
                        {
                            "id": int(r["id"]),
                            "variant_id": vid,
                            "opaque": row["opaque_id"],
                            "name": row["canonical_name"],
                            "source": src,
                            "price": float(r["price_usd"]),
                            "reason": "flagged_card_wipe_risky",
                        },
                    )

    # reason tallies
    tallies: dict[str, int] = defaultdict(int)
    for reason in delete.values():
        tallies[reason.split(":")[0]] += 1

    delete_ids = sorted(delete.keys())
    print(
        f"delete_ids={len(delete_ids)} reasons={dict(tallies)} "
        f"flagged_variants={len(flagged_vids)} wipe_opaque={len(wipe_opaque)}"
    )

    if args.write and delete_ids:
        for i in range(0, len(delete_ids), 500):
            chunk = delete_ids[i : i + 500]
            ph = ",".join(["%s"] * len(chunk))
            cur.execute(f"DELETE FROM market_price_observation WHERE id IN ({ph})", chunk)
        conn.commit()
        print(f"DELETED={len(delete_ids)}")
    elif not args.write:
        print("dry-run only (pass --write to delete)")

    out_path = args.out if args.out.is_absolute() else ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "asOf": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "write": bool(args.write),
        "today": today.isoformat(),
        "ratio": args.ratio,
        "jump": args.jump,
        "deleteCount": len(delete_ids),
        "reasonTally": dict(tallies),
        "flaggedOpaque": wipe_opaque,
        "flaggedVariantIds": flagged_vids,
        "samples": samples,
        "deleteIdsHead": delete_ids[:100],
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("report", out_path)
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
