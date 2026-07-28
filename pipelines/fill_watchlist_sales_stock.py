#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全量存量：補 940 watchlist 成交覆蓋 + 每卡記「ID + 邊個腳本得」.

策略（無 30d 限制；有幾多入幾多；窗之後反推）:
  A) G10 gemrate → 把 snkrdunk/ebay external_id 掛上 watchlist variant
  B) 重指 identity 後，把舊 variant 嘅 sale 行 UPDATE 到 watchlist（同 fingerprint）
  C) 高置信 printing 對應：同 collector + 強 name token → clone sale 到 watchlist
     （新 fingerprint = sha256(watch|old_fp)）
  D) 更新 liquidity-source-registry.jsonl（preferred source + script）

用法:
  python -X utf8 pipelines/fill_watchlist_sales_stock.py --dry-run
  python -X utf8 pipelines/fill_watchlist_sales_stock.py --write
  python -X utf8 pipelines/fill_watchlist_sales_stock.py --write --then-g10-cache
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MAP = ROOT / "data/runtime/private-source-map"
REGISTRY = MAP / "liquidity-source-registry.jsonl"
REPORT_DIR = MAP / "qualified-pool-reports"
G10_DEFAULT = ROOT.parent / "grade10-scraper" / "data" / "cards"
GEM_RE = re.compile(r"gemrate_id=([0-9a-fA-F]{40})")
STOP = {
    "the", "and", "with", "ex", "gx", "vmax", "vstar", "card", "pokemon", "one", "piece",
    "full", "art", "alternate", "illustration", "secret", "rare", "ultra", "holo",
    "special", "promo", "edition", "booster", "pack", "japanese", "english", "premium",
    "anniversary", "collection", "game",
}


def load_env() -> None:
    env = ROOT / "data/runtime/config/backend.env"
    if not env.is_file():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().replace("\r", ""))
    os.environ.setdefault("CARDZ_DB_HOST", "127.0.0.1")


def db():
    import pymysql

    load_env()
    return pymysql.connect(
        host=os.environ.get("CARDZ_DB_HOST", "127.0.0.1"),
        port=int(os.environ.get("CARDZ_DB_PORT", "3308")),
        user=os.environ["CARDZ_DB_USER"],
        password=os.environ["CARDZ_DB_PASSWORD"],
        database=os.environ["CARDZ_DB_NAME"],
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def norm_col(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (value or "").upper())


def tokens(value: str) -> set[str]:
    return {
        t
        for t in re.findall(r"[a-z0-9]+", (value or "").casefold())
        if len(t) > 2 and t not in STOP
    }


def extract_gemrate(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    m = GEM_RE.search(text)
    if m:
        return m.group(1).lower()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    for k in ("gemrate_id", "gemrateId"):
        v = data.get(k)
        if isinstance(v, str) and re.fullmatch(r"[0-9a-fA-F]{40}", v):
            return v.lower()
    for k in ("source", "url", "href"):
        v = data.get(k)
        if isinstance(v, str):
            m = GEM_RE.search(v)
            if m:
                return m.group(1).lower()
    return None


def scan_g10(g10_root: Path) -> dict[str, list[dict[str, str]]]:
    """gemrate_id -> [{provider, external_id, dir}]"""
    out: dict[str, list[dict[str, str]]] = defaultdict(list)
    for provider, source_code in (("snkrdunk", "snkrdunk"), ("altxyz", "ebay")):
        root = g10_root / provider
        if not root.is_dir():
            continue
        for d in root.iterdir():
            if not d.is_dir():
                continue
            gem = None
            for fname in ("populations.json", "asset_info.json", "meta.json"):
                gem = extract_gemrate(d / fname)
                if gem:
                    break
            if not gem:
                continue
            out[gem].append(
                {
                    "provider": provider,
                    "source_code": source_code,
                    "external_id": d.name,
                    "dir": str(d),
                }
            )
    return out


def load_registry() -> dict[int, dict[str, Any]]:
    by: dict[int, dict[str, Any]] = {}
    if not REGISTRY.is_file():
        return by
    for line in REGISTRY.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        by[int(row["variantId"])] = row
    return by


def save_registry(by: dict[int, dict[str, Any]]) -> None:
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    with REGISTRY.open("w", encoding="utf-8") as fh:
        for vid in sorted(by):
            fh.write(json.dumps(by[vid], ensure_ascii=False, default=str) + "\n")


def upsert_identity(cur, source_code: str, external_id: str, variant_id: int) -> None:
    evidence = hashlib.sha256(
        f"{source_code}:{external_id}:{variant_id}:watchlist_reattach".encode()
    ).hexdigest()
    cur.execute(
        """
        INSERT INTO catalog_source_identity
            (source_code, external_entity_id, variant_id, match_status, evidence_sha256)
        VALUES (%s, %s, %s, 'exact', %s)
        ON DUPLICATE KEY UPDATE
            variant_id=VALUES(variant_id),
            match_status='exact',
            evidence_sha256=VALUES(evidence_sha256),
            updated_at=CURRENT_TIMESTAMP
        """,
        (source_code, external_id, variant_id, evidence),
    )


def reattach_g10(
    cur,
    watch: list[dict[str, Any]],
    has_sale: set[int],
    g10_map: dict[str, list[dict[str, str]]],
    *,
    write: bool,
) -> dict[str, Any]:
    identity_writes = 0
    sale_moves = 0
    attached_cards: list[dict[str, Any]] = []
    reg_updates: list[dict[str, Any]] = []

    for w in watch:
        vid = int(w["variant_id"])
        gem = (w.get("gemrate_id") or "").lower()
        if not gem or gem not in g10_map:
            continue
        for hit in g10_map[gem]:
            source_code = hit["source_code"]
            external_id = hit["external_id"]
            # previous variant for this external id
            cur.execute(
                """
                SELECT variant_id FROM catalog_source_identity
                WHERE source_code=%s AND external_entity_id=%s
                """,
                (source_code, external_id),
            )
            prev = cur.fetchone()
            prev_vid = int(prev["variant_id"]) if prev else None
            if write:
                upsert_identity(cur, source_code, external_id, vid)
                identity_writes += 1
            # move sales from previous variant if different
            if prev_vid is not None and prev_vid != vid:
                if write:
                    cur.execute(
                        """
                        UPDATE market_sale_observation
                        SET variant_id=%s
                        WHERE variant_id=%s
                          AND source_code=%s
                          AND external_entity_id=%s
                        """,
                        (vid, prev_vid, source_code, external_id),
                    )
                    sale_moves += int(cur.rowcount or 0)
                else:
                    cur.execute(
                        """
                        SELECT COUNT(*) c FROM market_sale_observation
                        WHERE variant_id=%s AND source_code=%s AND external_entity_id=%s
                        """,
                        (prev_vid, source_code, external_id),
                    )
                    sale_moves += int(cur.fetchone()["c"])
            attached_cards.append(
                {
                    "variantId": vid,
                    "source": source_code,
                    "externalId": external_id,
                    "prevVariantId": prev_vid,
                    "hadSale": vid in has_sale,
                }
            )
            reg_updates.append(
                {
                    "variantId": vid,
                    "preferredLiquiditySource": source_code if source_code == "snkrdunk" else "ebay",
                    "externalId": external_id,
                    "script": "pipelines/fill_watchlist_sales_stock.py#g10_reattach",
                    "updatedAt": utc_now(),
                }
            )
    return {
        "identityWrites": identity_writes,
        "saleMoves": sale_moves,
        "attachRows": len(attached_cards),
        "cards": attached_cards[:50],
        "registryUpdates": reg_updates,
    }


def clone_sales_from_twins(
    cur,
    watch: list[dict[str, Any]],
    has_sale: set[int],
    *,
    write: bool,
    min_name_hits: int = 2,
) -> dict[str, Any]:
    """Clone sales onto watchlist when another variant shares collector + strong name."""
    cur.execute(
        """
        SELECT v.id, v.collector_number, v.canonical_name, v.set_name
        FROM catalog_variant v
        WHERE EXISTS (
          SELECT 1 FROM market_sale_observation s
          WHERE s.variant_id=v.id AND s.sold_at IS NOT NULL
        )
        """
    )
    donors = list(cur.fetchall())
    by_col: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for d in donors:
        by_col[norm_col(str(d.get("collector_number") or ""))].append(d)

    clones = 0
    cards = 0
    reg_updates: list[dict[str, Any]] = []
    pairs: list[dict[str, Any]] = []

    for w in watch:
        vid = int(w["variant_id"])
        if vid in has_sale:
            continue
        ck = norm_col(str(w.get("collector_number") or ""))
        if not ck:
            continue
        wname = str(w.get("card_name") or "")
        wt = tokens(wname)
        # first real name token must match (blocks Moltres↔Unown via "art")
        raw_first = re.findall(r"[a-z0-9]+", wname.casefold())
        first = next((t for t in raw_first if t not in STOP and len(t) > 2), None)
        if not first or not wt:
            continue
        best = None
        best_sc = 0
        op_style = bool(re.match(r"^(OP|ST|EB|PRB)", ck)) or bool(
            re.search(r"[A-Z]{2,}\d", str(w.get("collector_number") or ""), re.I)
        )
        for d in by_col.get(ck, []):
            if int(d["id"]) == vid:
                continue
            dname = str(d.get("canonical_name") or "")
            dt = tokens(dname)
            if first not in dname.casefold() and first not in dt:
                continue
            sc = len(wt & dt)
            # pure digits need stronger name overlap; OP/ST codes can pass with 1
            need = 1 if op_style else max(min_name_hits, 2)
            if sc >= need and sc > best_sc:
                best_sc = sc
                best = d
        if not best:
            continue
        donor_id = int(best["id"])
        cur.execute(
            """
            SELECT * FROM market_sale_observation
            WHERE variant_id=%s AND sold_at IS NOT NULL
            """,
            (donor_id,),
        )
        rows = list(cur.fetchall())
        if not rows:
            continue
        cards += 1
        written_here = 0
        for row in rows:
            old_fp = str(row.get("transaction_fingerprint") or "")
            new_fp = hashlib.sha256(f"watchclone|{vid}|{old_fp}".encode()).hexdigest()
            if write:
                cur.execute(
                    """
                    INSERT IGNORE INTO market_sale_observation
                        (run_id, variant_id, source_code, external_entity_id, transaction_fingerprint,
                         grader_code, grade_label, sold_at, source_date_text, fetched_at,
                         timestamp_quality, unit_price_usd, quantity, transaction_value_usd,
                         source_payload_sha256, coverage_status)
                    VALUES
                        (%s, %s, %s, %s, %s,
                         %s, %s, %s, %s, %s,
                         %s, %s, %s, %s,
                         %s, %s)
                    """,
                    (
                        row.get("run_id"),
                        vid,
                        row.get("source_code"),
                        row.get("external_entity_id"),
                        new_fp,
                        row.get("grader_code"),
                        row.get("grade_label"),
                        row.get("sold_at"),
                        row.get("source_date_text"),
                        row.get("fetched_at"),
                        row.get("timestamp_quality"),
                        row.get("unit_price_usd"),
                        row.get("quantity"),
                        row.get("transaction_value_usd"),
                        row.get("source_payload_sha256"),
                        row.get("coverage_status") or "partial",
                    ),
                )
                written_here += int(cur.rowcount or 0)
            else:
                written_here += 1
        clones += written_here
        pairs.append(
            {
                "watchVariantId": vid,
                "donorVariantId": donor_id,
                "collector": w.get("collector_number"),
                "name": w.get("card_name"),
                "donorName": best.get("canonical_name"),
                "nameHits": best_sc,
                "saleRows": written_here,
            }
        )
        reg_updates.append(
            {
                "variantId": vid,
                "preferredLiquiditySource": "clone_from_variant",
                "donorVariantId": donor_id,
                "script": "pipelines/fill_watchlist_sales_stock.py#clone_twin",
                "tradesIngested": written_here,
                "updatedAt": utc_now(),
            }
        )
        has_sale.add(vid)

    return {
        "cardsClonedOnto": cards,
        "saleRowsCloned": clones,
        "pairs": pairs[:40],
        "registryUpdates": reg_updates,
    }


def coverage(cur) -> dict[str, int]:
    cur.execute(
        """
        SELECT
          COUNT(DISTINCT CASE WHEN s.sold_at >= DATE_SUB(UTC_TIMESTAMP(), INTERVAL 1 DAY) THEN s.variant_id END) AS sale_1d,
          COUNT(DISTINCT CASE WHEN s.sold_at >= DATE_SUB(UTC_TIMESTAMP(), INTERVAL 7 DAY) THEN s.variant_id END) AS sale_7d,
          COUNT(DISTINCT CASE WHEN s.sold_at >= DATE_SUB(UTC_TIMESTAMP(), INTERVAL 21 DAY) THEN s.variant_id END) AS sale_21d,
          COUNT(DISTINCT CASE WHEN s.sold_at >= DATE_SUB(UTC_TIMESTAMP(), INTERVAL 30 DAY) THEN s.variant_id END) AS sale_30d,
          COUNT(DISTINCT s.variant_id) AS sale_any
        FROM market_sale_observation s
        WHERE s.variant_id IN (SELECT variant_id FROM market_gemrate_psa10_watchlist)
          AND s.sold_at IS NOT NULL
        """
    )
    row = cur.fetchone() or {}
    return {k: int(v or 0) for k, v in row.items()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--g10-root", type=Path, default=G10_DEFAULT)
    ap.add_argument("--then-g10-cache", action="store_true", help="after write, run g10_sales_cache_ingest snkrdunk")
    ap.add_argument("--min-name-hits", type=int, default=2)
    args = ap.parse_args()

    conn = db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT variant_id, gemrate_id, card_name, collector_number, set_name, psa10_population
        FROM market_gemrate_psa10_watchlist
        ORDER BY psa10_population DESC
        """
    )
    watch = list(cur.fetchall())
    cur.execute(
        "SELECT DISTINCT variant_id FROM market_sale_observation WHERE sold_at IS NOT NULL"
    )
    has_sale = {int(r["variant_id"]) for r in cur.fetchall()}
    before = coverage(cur)

    g10_map = scan_g10(args.g10_root)
    a = reattach_g10(cur, watch, has_sale, g10_map, write=args.write)
    if args.write:
        conn.commit()
        # refresh has_sale after moves
        cur.execute(
            "SELECT DISTINCT variant_id FROM market_sale_observation WHERE sold_at IS NOT NULL"
        )
        has_sale = {int(r["variant_id"]) for r in cur.fetchall()}

    b = clone_sales_from_twins(
        cur, watch, has_sale, write=args.write, min_name_hits=args.min_name_hits
    )
    if args.write:
        conn.commit()

    after = coverage(cur)

    reg = load_registry()
    for u in a.get("registryUpdates") or []:
        vid = int(u["variantId"])
        reg[vid] = {**(reg.get(vid) or {}), **u}
    for u in b.get("registryUpdates") or []:
        vid = int(u["variantId"])
        reg[vid] = {**(reg.get(vid) or {}), **u}
    if args.write:
        save_registry(reg)

    summary = {
        "write": args.write,
        "watch": len(watch),
        "g10GemrateKeys": len(g10_map),
        "before": before,
        "after": after,
        "g10Reattach": {
            "identityWrites": a["identityWrites"],
            "saleMoves": a["saleMoves"],
            "attachRows": a["attachRows"],
            "sample": a["cards"][:10],
        },
        "cloneTwins": {
            "cardsClonedOnto": b["cardsClonedOnto"],
            "saleRowsCloned": b["saleRowsCloned"],
            "sample": b["pairs"][:10],
        },
        "registryRows": len(reg),
        "note": "Sales stored in full; 1d/7d/30d derived. Per-card script in liquidity-source-registry.jsonl",
    }
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    rep = REPORT_DIR / f"fill_sales_stock_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    rep.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    print("report", rep)
    conn.close()

    if args.write and args.then_g10_cache:
        cmd = [
            sys.executable,
            "-X",
            "utf8",
            str(ROOT / "pipelines" / "g10_sales_cache_ingest.py"),
            "--write",
            "--platforms",
            "snkrdunk",
            "--g10-root",
            str(args.g10_root.parent if args.g10_root.name == "cards" else args.g10_root),
        ]
        # g10_sales_cache expects root containing sales_cache; pass grade10-scraper root
        g10_project = args.g10_root.parent if args.g10_root.name == "cards" else args.g10_root
        # check CLI
        help_cmd = [sys.executable, "-X", "utf8", str(ROOT / "pipelines" / "g10_sales_cache_ingest.py"), "--help"]
        print("running g10_sales_cache_ingest...", flush=True)
        # Use default path inside script if env set
        env = os.environ.copy()
        r = subprocess.run(
            [
                sys.executable,
                "-X",
                "utf8",
                str(ROOT / "pipelines" / "g10_sales_cache_ingest.py"),
                "--write",
                "--platforms",
                "snkrdunk",
            ],
            cwd=str(ROOT),
            env=env,
        )
        summary["g10CacheExit"] = r.returncode
        print("g10_sales_cache exit", r.returncode)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
