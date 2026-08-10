#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CARDZ operator control plane: agent-first daily path."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import statistics

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))
from qualified_pool_operator import db, load_env  # noqa: E402
import operator_fe_export as fe  # noqa: E402

LEGACY_PRICE_COMPOSITION_ORDER = ("snk_psa10", "snk", "snkrdunk", "pricecharting", "ebay")
BANNED_PRICE_SOURCES = ("g10_kline",)
FREEZE_KINDS = ("identity", "source", "image")
OUT_DIR = ROOT / "data" / "runtime" / "operator"
PROMOTED_PRODUCT_SNAPSHOT = OUT_DIR / "promoted-product-snapshot.json"
RELAXED_RELEASE_PROFILE = "relaxed-launch-v1"
RELAXED_POLICY_SHA256 = "b316374547268c88616cf1b9220f9797dd11b720fdbe26ff6a77825246d48a95"
TONIGHT_CARD_COUNT = 762
TONIGHT_BACKLOG_COUNT = 776
COLLECT_REGISTRY_PATH = OUT_DIR / "collect" / "collect_registry.jsonl"
CHECKPOINT_ADAPTERS = (
    "gemrate_pop",
    "snk_trades",
    "snk_price",
    "pc_ebay_sales",
    "en_price_ref",
    "snk_en_image",
)
CHECKPOINT_SLA_HOURS = 36
CANONICAL_IMAGE_POLICY_ID = "canonical-026-snk-en-exact-first-v1"
# Compatibility name for older callers. 026 applies the policy to all 762 cards,
# never to a rank-dependent Top-100 overlay.
TOP100_SNK_IMAGE_POLICY_ID = CANONICAL_IMAGE_POLICY_ID
OPERATOR_E2E_LEASE = "cardz-market-cap:operator-e2e:v1"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def utc_now_sql() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")


@contextmanager
def operator_e2e_lease(owner: str):
    """Serialize migration, collection, materialization and pass as one run."""
    load_env()
    lease_conn = db()
    lease_cur = lease_conn.cursor()
    lease_cur.execute("SELECT GET_LOCK(%s, 0) AS acquired", (OPERATOR_E2E_LEASE,))
    acquired = int((lease_cur.fetchone() or {}).get("acquired") or 0)
    if acquired != 1:
        lease_conn.close()
        raise RuntimeError(
            f"{owner} refused: another CARDZ 026 operator run owns {OPERATOR_E2E_LEASE}"
        )
    print(
        json.dumps(
            {"phase": "operator-e2e-lease-acquired", "owner": owner,
             "lease": OPERATOR_E2E_LEASE, "at": utc_now()},
            ensure_ascii=False,
        ),
        flush=True,
    )
    try:
        yield
    finally:
        try:
            lease_cur.execute("SELECT RELEASE_LOCK(%s)", (OPERATOR_E2E_LEASE,))
        finally:
            lease_conn.close()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonical_snapshot_sha256(snapshot: dict[str, Any]) -> str:
    script = r"""
const crypto = require("crypto");
let raw = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", chunk => raw += chunk);
process.stdin.on("end", () => {
  const value = JSON.parse(raw);
  const stable = input => Array.isArray(input)
    ? input.map(stable)
    : input && typeof input === "object"
      ? Object.fromEntries(Object.keys(input).sort().map(key => [key, stable(input[key])]))
      : input;
  value.generation.contentSha256 = "";
  process.stdout.write(crypto.createHash("sha256").update(JSON.stringify(stable(value))).digest("hex"));
});
"""
    result = subprocess.run(
        ["node", "-e", script],
        input=json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"), default=str),
        capture_output=True,
        text=True,
        check=True,
    )
    digest = result.stdout.strip()
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise RuntimeError("Node snapshot hash helper returned an invalid digest")
    return digest


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.next")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    os.replace(temporary, path)



def ensure_freeze_table(cur) -> None:
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS operator_binding_freeze (
            variant_id BIGINT UNSIGNED NOT NULL,
            freeze_kind VARCHAR(32) NOT NULL,
            source_code VARCHAR(32) NOT NULL DEFAULT '',
            external_entity_id VARCHAR(191) NOT NULL DEFAULT '',
            content_sha256 CHAR(64) NOT NULL DEFAULT '',
            acceptance_status VARCHAR(16) NOT NULL,
            actor VARCHAR(191) NOT NULL,
            evidence_sha256 CHAR(64) NOT NULL,
            note VARCHAR(1000) NULL,
            accepted_at DATETIME(6) NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (variant_id, freeze_kind, source_code),
            KEY ix_operator_binding_freeze_status (acceptance_status, freeze_kind)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )
    cur.execute("INSERT IGNORE INTO cardz_schema_version (version_code) VALUES ('021')")


def current_universe(cur):
    cur.execute(
        """
        SELECT id, lock_sha256, member_count, effective_at
        FROM market_universe_lock
        WHERE is_current = 1
        ORDER BY id DESC
        LIMIT 1
        """
    )
    lock = cur.fetchone()
    if not lock:
        return {"lockId": None, "lockSha256": None, "memberCount": 0, "variantIds": [], "members": []}
    cur.execute(
        """
        SELECT variant_id, market_rank, member_role, segment_code
        FROM market_universe_member
        WHERE universe_lock_id = %s
        ORDER BY COALESCE(market_rank, 999999), variant_id
        """,
        (int(lock["id"]),),
    )
    members = list(cur.fetchall())
    return {
        "lockId": int(lock["id"]),
        "lockSha256": lock["lock_sha256"],
        "memberCount": int(lock["member_count"] or len(members)),
        "effectiveAt": str(lock["effective_at"]),
        "variantIds": [int(r["variant_id"]) for r in members],
        "members": members,
    }

def freeze_map(cur, variant_ids):
    if not variant_ids:
        return {}
    ensure_freeze_table(cur)
    ph = ",".join(["%s"] * len(variant_ids))
    cur.execute(
        f"""
        SELECT variant_id, freeze_kind, source_code, external_entity_id, content_sha256,
               acceptance_status, actor, accepted_at, note
        FROM operator_binding_freeze
        WHERE acceptance_status = 'accepted'
          AND variant_id IN ({ph})
        """,
        tuple(variant_ids),
    )
    out = {}
    for row in cur.fetchall():
        out.setdefault(int(row["variant_id"]), []).append(row)
    return out


def identities_by_variant(cur, variant_ids):
    if not variant_ids:
        return {}
    ph = ",".join(["%s"] * len(variant_ids))
    cur.execute(
        f"""
        SELECT variant_id, source_code, external_entity_id, match_status
        FROM catalog_source_identity
        WHERE variant_id IN ({ph})
          AND match_status = 'exact'
        """,
        tuple(variant_ids),
    )
    out = {}
    for row in cur.fetchall():
        out.setdefault(int(row["variant_id"]), []).append(row)
    return out


def _is_snk_source(source_code: str) -> bool:
    return str(source_code or "") in {"snk_psa10", "snk", "snkrdunk"}


def _is_canonical_price_route(
    card_language: str,
    source_code: str,
    selected_route_priority: Any,
    eligible_pricecharting_exists: Any,
) -> bool:
    """Fail closed unless the selected exact source follows the 026 language route."""

    language = str(card_language or "")
    source = str(source_code or "")
    try:
        route_priority = int(selected_route_priority)
        eligible_pc = int(eligible_pricecharting_exists)
    except (TypeError, ValueError):
        return False
    if eligible_pc not in {0, 1}:
        return False
    if language == "en":
        if eligible_pc == 1:
            return source == "pricecharting" and route_priority == 10
        return source == "snkrdunk" and route_priority == 20
    if language in {"ja", "ko", "zhCN", "zhTW"}:
        return eligible_pc == 0 and source == "snkrdunk" and route_priority == 10
    return False


def _trim_mean_prices(candidates: list[dict]) -> dict | None:
    """Compose authority price from latest per-source candidates.

    Rules (DADDY 2026-08 follow-up):
    1) Prefer SNK-family (snk_psa10/snk/snkrdunk): trim extremes inside SNK, average survivors.
    2) Cross-source guard when eBay/PC peers exist:
       - if SNK authority > 2.5x peer median => SNK is extreme-high; fall back to trimmed peers
       - if SNK authority < peer median / 2.5 => keep SNK (peers often inflated lots)
    3) No SNK: trim extremes among fallback sources only, then average.
    """
    if not candidates:
        return None
    order = {s: i for i, s in enumerate(LEGACY_PRICE_COMPOSITION_ORDER)}

    def _vals(pool: list[dict]) -> list[float]:
        out = []
        for c in pool:
            try:
                out.append(float(c["price_usd"]))
            except Exception:
                continue
        return out

    def _avg_pool(pool: list[dict]) -> dict:
        vals = _vals(pool)
        avg = sum(vals) / len(vals)
        rep = min(pool, key=lambda r: order.get(str(r.get("source_code")), 99))
        out = dict(rep)
        out["price_usd"] = avg
        out["metric_status"] = str(rep.get("metric_status") or "ready")
        return out

    def _trim_pool(pool: list[dict]) -> list[dict]:
        vals = _vals(pool)
        if not vals:
            return []
        if len(vals) == 1:
            return list(pool)
        med = statistics.median(vals)
        if med <= 0:
            return list(pool)
        kept = []
        for c in pool:
            try:
                p = float(c["price_usd"])
            except Exception:
                continue
            if p > med * 2.0 or p < med / 2.5:
                continue
            kept.append(c)
        return kept or list(pool)

    snk = [c for c in candidates if _is_snk_source(c.get("source_code"))]
    peers = [c for c in candidates if str(c.get("source_code") or "") in {"ebay", "pricecharting"}]
    fallback = [c for c in candidates if not _is_snk_source(c.get("source_code"))]

    if snk:
        snk_auth = _avg_pool(_trim_pool(snk))
        peer_vals = _vals(peers)
        if peer_vals:
            peer_med = statistics.median(peer_vals)
            snk_px = float(snk_auth["price_usd"])
            # SNK extreme-high vs peer cluster => reject SNK authority for display
            if peer_med > 0 and snk_px > peer_med * 2.5:
                peer_pool = _trim_pool(peers) or peers
                return _avg_pool(peer_pool)
            # SNK far below peer cluster => keep SNK (do not let inflated eBay lift rank)
        return snk_auth

    if not fallback:
        return None
    return _avg_pool(_trim_pool(fallback))


def _variant_languages(cur, variant_ids) -> dict[int, str]:
    """card_language for authority routing. Missing => treat as non-EN (SNK chain)."""
    if not variant_ids:
        return {}
    ph = ",".join(["%s"] * len(variant_ids))
    cur.execute(
        f"SELECT variant_id AS id, card_language FROM catalog_printing_identity WHERE variant_id IN ({ph})",
        tuple(variant_ids),
    )
    out: dict[int, str] = {}
    for row in cur.fetchall():
        lang = str(row.get("card_language") or "").strip().lower()
        out[int(row["id"])] = lang
    return out


def _sales_unit_authority(
    cur,
    variant_ids,
    *,
    source_codes: tuple[str, ...] = ("snkrdunk", "snk", "snk_psa10"),
    authority_code: str = "snk_sales",
    min_n: int = 3,
    lookback_days: int = 30,
    max_rows: int = 30,
    require_quantity: bool = True,
) -> dict[int, dict]:
    """Compose display price from completed sales unit prices (qty unitized when present).

    Polaris (DADDY 2026-08-03):
    - EN chain uses eBay sales (`ebay` -> authority_code ebay_sales)
    - JA/other chain uses SNK sales (`snkrdunk/snk/snk_psa10` -> snk_sales)
    - Prefer last 30d; if sparse take latest max_rows
    - Require >= min_n after extreme trim; median of survivors
    """
    if not variant_ids or not source_codes:
        return {}
    ph = ",".join(["%s"] * len(variant_ids))
    sources = ",".join(f"'{s}'" for s in source_codes)
    qty_clause = "AND quantity IS NOT NULL AND quantity > 0" if require_quantity else ""
    cur.execute(
        f"""
        SELECT variant_id, source_code, unit_price_usd, quantity, sold_at, transaction_value_usd
        FROM market_sale_observation
        WHERE variant_id IN ({ph})
          AND source_code IN ({sources})
          AND unit_price_usd IS NOT NULL
          AND unit_price_usd > 0
          {qty_clause}
        ORDER BY sold_at DESC
        """,
        tuple(variant_ids),
    )
    by: dict[int, list] = {}
    for row in cur.fetchall():
        by.setdefault(int(row["variant_id"]), []).append(row)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    out: dict[int, dict] = {}
    for vid, rows in by.items():
        window = []
        for r in rows:
            sold = r.get("sold_at")
            if sold is None:
                continue
            if getattr(sold, "tzinfo", None) is not None:
                sold_naive = sold.replace(tzinfo=None)
            else:
                sold_naive = sold
            try:
                age = (now - sold_naive).days
            except Exception:
                continue
            if age <= lookback_days:
                window.append(r)
        # EN/eBay: only trust true 30d window (no ancient max_rows backfill as authority).
        if authority_code == "ebay_sales":
            pool = window
        else:
            pool = window if len(window) >= min_n else rows[:max_rows]
        units = []
        for r in pool:
            try:
                units.append(float(r["unit_price_usd"]))
            except Exception:
                continue
        if len(units) < min_n:
            continue
        med = statistics.median(units)
        if med <= 0:
            continue
        kept = [u for u in units if not (u > med * 2.0 or u < med / 2.5)]
        if len(kept) < min_n:
            kept = units
        auth = float(statistics.median(kept))
        latest = pool[0]
        sold = latest.get("sold_at")
        if sold is not None and getattr(sold, "tzinfo", None) is not None:
            sold = sold.replace(tzinfo=None)
        observed = sold.date() if hasattr(sold, "date") else sold
        out[vid] = {
            "variant_id": vid,
            "source_code": authority_code,
            "price_usd": auth,
            "effective_at": sold,
            "observed_date": observed,
            "metric_status": "ready",
            "sales_n": len(kept),
            "sales_pool_n": len(units),
        }
    return out


def _latest_price_rows(cur, variant_ids, source_codes: tuple[str, ...]) -> dict[int, list]:
    if not variant_ids or not source_codes:
        return {}
    ph = ",".join(["%s"] * len(variant_ids))
    sources = ",".join(f"'{s}'" for s in source_codes)
    banned = ",".join(f"'{s}'" for s in BANNED_PRICE_SOURCES)
    cur.execute(
        f"""
        SELECT variant_id, source_code, price_usd, effective_at, metric_status, observed_date
        FROM (
            SELECT p.variant_id, p.source_code, p.price_usd, p.effective_at, p.metric_status, p.observed_date,
                   ROW_NUMBER() OVER (
                     PARTITION BY p.variant_id, p.source_code
                     ORDER BY p.observed_date DESC, p.effective_at DESC
                   ) AS rn
            FROM market_price_observation p
            WHERE p.variant_id IN ({ph})
              AND p.source_code IN ({sources})
              AND p.source_code NOT IN ({banned})
              AND p.metric_status NOT IN ('banned_g10_kline')
              AND p.price_usd IS NOT NULL
              AND p.price_usd > 0
        ) t
        WHERE rn = 1
        """,
        tuple(variant_ids),
    )
    by_vid: dict[int, list] = {}
    for row in cur.fetchall():
        by_vid.setdefault(int(row["variant_id"]), []).append(row)
    return by_vid


def _pick_prefer_sources(rows: list[dict], prefer: tuple[str, ...]) -> dict | None:
    """Pick single-source preference order (first available), no averaging across families."""
    if not rows:
        return None
    by = {str(r.get("source_code") or ""): r for r in rows}
    for code in prefer:
        if code in by:
            out = dict(by[code])
            out["metric_status"] = str(out.get("metric_status") or "ready")
            return out
    return _trim_mean_prices(rows)


def latest_prices(cur, variant_ids):
    """Pick display/authority price per variant.

    Polaris pricing (DADDY 2026-08-03 review):
    - EN cards: eBay sales 30d primary; else eBay then PriceCharting reference.
      SNK does not hard-top EN boards (JP market ≠ EN truth).
    - JA / other: SNK sales 30d primary; else SNK reference only.
      eBay/PC do not hard-top JA boards (unless SNK extreme-high guard in trim path).
    - g10_kline still banned everywhere.
    """
    if not variant_ids:
        return {}

    langs = _variant_languages(cur, variant_ids)
    en_ids = [vid for vid in variant_ids if langs.get(vid) == "en"]
    other_ids = [vid for vid in variant_ids if langs.get(vid) != "en"]

    best: dict[int, dict] = {}

    # --- EN chain: eBay first ---
    if en_ids:
        best.update(
            _sales_unit_authority(
                cur,
                en_ids,
                source_codes=("ebay",),
                authority_code="ebay_sales",
                min_n=3,
                lookback_days=30,
                max_rows=30,
                require_quantity=False,
            )
        )
        missing_en = [vid for vid in en_ids if vid not in best]
        if missing_en:
            by_vid = _latest_price_rows(cur, missing_en, ("ebay", "pricecharting"))
            for vid, rows in by_vid.items():
                picked = _pick_prefer_sources(rows, ("ebay", "pricecharting"))
                if picked is not None:
                    best[vid] = picked
        # Residual only: if still no EN peer price/sales, allow SNK so board is not blank.
        # Does not hard-top EN when eBay/PC exists; last-resort completeness for JP-listed EN boards.
        still_en = [vid for vid in en_ids if vid not in best]
        if still_en:
            by_vid = _latest_price_rows(cur, still_en, ("snk_psa10", "snk", "snkrdunk"))
            for vid, rows in by_vid.items():
                picked = _pick_prefer_sources(rows, ("snk_psa10", "snkrdunk", "snk"))
                if picked is not None:
                    out = dict(picked)
                    out["metric_status"] = str(out.get("metric_status") or "ready")
                    best[vid] = out

    # --- JA / other chain: SNK first ---
    if other_ids:
        best.update(
            _sales_unit_authority(
                cur,
                other_ids,
                source_codes=("snkrdunk", "snk", "snk_psa10"),
                authority_code="snk_sales",
                min_n=3,
                lookback_days=30,
                max_rows=30,
                require_quantity=True,
            )
        )
        # If SNK sales composition diverges hard from SNK listing/ref, prefer ref.
        # Fixes false -40%~-60% boards when sales pool mixes grades/units.
        snk_ref_rows = _latest_price_rows(cur, other_ids, ("snk_psa10", "snk", "snkrdunk"))
        for vid in list(other_ids):
            sales = best.get(vid)
            if not sales or str(sales.get("source_code")) != "snk_sales":
                continue
            ref = _pick_prefer_sources(snk_ref_rows.get(vid) or [], ("snk_psa10", "snkrdunk", "snk"))
            if not ref:
                continue
            try:
                s_px = float(sales["price_usd"])
                r_px = float(ref["price_usd"])
            except Exception:
                continue
            if r_px <= 0 or s_px <= 0:
                continue
            # Sales vs SNK ref: >1.75x divergence usually means mixed-grade/unit pollution
            # (Sylveon/Charizard ex false -40% boards were ~1.77–1.83x under ref).
            if s_px > r_px * 1.75 or s_px < r_px / 1.75:
                best[vid] = ref

                missing_other = [vid for vid in other_ids if vid not in best]
        if missing_other:
            by_vid = _latest_price_rows(cur, missing_other, ("snk_psa10", "snk", "snkrdunk"))
            for vid, rows in by_vid.items():
                # JA fallback: SNK refs only first.
                picked = _trim_mean_prices(rows)
                if picked is not None:
                    best[vid] = picked
        # Residual only: JA with no SNK sales/ref can use PC/eBay so board not blank.
        still_other = [vid for vid in other_ids if vid not in best]
        if still_other:
            by_vid = _latest_price_rows(cur, still_other, ("pricecharting", "ebay"))
            for vid, rows in by_vid.items():
                picked = _pick_prefer_sources(rows, ("pricecharting", "ebay"))
                if picked is not None:
                    best[vid] = picked

    return best


def image_rows(cur, variant_ids, *, prefer_snk_ids=()):
    """Pick display image per variant.

    For explicitly selected variants, an exact SNK asset with a public source
    pointer and currently approved raw-front QC takes precedence over every
    other source. Remaining variants keep the accepted freeze preference.

    Prefer accepted operator_binding_freeze(image).content_sha256 when present,
    so rebinds (e.g. OP JA SNK superseding TCGplayer) actually show in snapshot.
    Fallback: latest approved/public raw_front asset.
    """
    if not variant_ids:
        return {}
    ph = ",".join(["%s"] * len(variant_ids))
    # accepted freeze sha preference: snkrdunk > tcgplayer > blank/other
    cur.execute(
        f"""
        SELECT variant_id, source_code, content_sha256, accepted_at
        FROM operator_binding_freeze
        WHERE freeze_kind='image'
          AND acceptance_status='accepted'
          AND variant_id IN ({ph})
          AND content_sha256 IS NOT NULL
          AND content_sha256 <> ''
          AND content_sha256 <> REPEAT('0', 64)
        """,
        tuple(variant_ids),
    )
    pref = {"snkrdunk": 0, "tcgplayer": 1, "": 2}
    freeze_sha = {}
    for row in cur.fetchall():
        vid = int(row["variant_id"])
        src = str(row.get("source_code") or "")
        sha = str(row.get("content_sha256") or "")
        prev = freeze_sha.get(vid)
        if prev is None or pref.get(src, 9) < pref.get(str(prev.get("source_code") or ""), 9):
            freeze_sha[vid] = {"source_code": src, "content_sha256": sha}

    cur.execute(
        f"""
        SELECT a.variant_id, a.id AS image_asset_id, a.content_sha256, a.width_px, a.height_px,
               q.semantic_match_status, q.public_allowed, q.raw_front_confirmed,
               CASE WHEN b.image_asset_id IS NULL THEN 0 ELSE 1 END AS review_approved,
               EXISTS(
                   SELECT 1
                   FROM market_image_source_pointer p
                   WHERE p.variant_id=a.variant_id
                     AND p.image_kind=a.image_kind
                     AND p.source_version_sha256=a.source_version_sha256
                     AND p.public_allowed=1
                     AND p.source_path LIKE 'snkrdunk:%%'
               ) AS snk_source,
               EXISTS(
                   SELECT 1
                   FROM market_image_qc sq
                   WHERE sq.image_asset_id=a.id
                     AND sq.public_allowed=1
                     AND sq.raw_front_confirmed=1
                     AND sq.semantic_match_status='human_or_vision_confirmed'
               ) AS snk_public_qc
        FROM market_image_asset a
        LEFT JOIN market_image_qc q ON q.image_asset_id = a.id
        LEFT JOIN market_image_review_approval b ON b.image_asset_id = a.id
        WHERE a.variant_id IN ({ph})
          AND a.image_kind = 'raw_front'
        ORDER BY a.variant_id, review_approved DESC, q.public_allowed DESC, a.id DESC
        """,
        tuple(variant_ids),
    )
    prefer_snk = {int(vid) for vid in prefer_snk_ids}
    by_vid_sha = {}
    fallback = {}
    snk_preferred = {}
    for row in cur.fetchall():
        vid = int(row["variant_id"])
        sha = str(row.get("content_sha256") or "")
        by_vid_sha[(vid, sha)] = row
        if (
            vid in prefer_snk
            and bool(row.get("snk_source"))
            and bool(row.get("snk_public_qc"))
            and vid not in snk_preferred
        ):
            snk_preferred[vid] = row
        if vid not in fallback:
            fallback[vid] = row

    out = {}
    for vid in variant_ids:
        vid = int(vid)
        chosen = snk_preferred.get(vid)
        fz = freeze_sha.get(vid)
        if chosen is None and fz:
            chosen = by_vid_sha.get((vid, str(fz["content_sha256"])))
            assets_root = ROOT / "data" / "public" / "market-assets"
            freeze_pub = assets_root / f"{fz['content_sha256']}.webp"
            # freeze sha missing on disk => prefer another public-existing asset
            if chosen is not None and not freeze_pub.exists():
                alt = fallback.get(vid)
                if alt is not None:
                    alt_sha = str(alt.get("content_sha256") or "")
                    if alt_sha and (assets_root / f"{alt_sha}.webp").exists():
                        chosen = alt
            if chosen is None and freeze_pub.exists():
                chosen = {
                    "variant_id": vid,
                    "image_asset_id": None,
                    "content_sha256": fz["content_sha256"],
                    "width_px": None,
                    "height_px": None,
                    "semantic_match_status": "human_or_vision_confirmed",
                    "public_allowed": 1,
                    "raw_front_confirmed": 1,
                    "review_approved": 1,
                }
        if chosen is None:
            alt = fallback.get(vid)
            if alt is not None:
                assets_root = ROOT / "data" / "public" / "market-assets"
                alt_sha = str(alt.get("content_sha256") or "")
                if alt_sha and (assets_root / f"{alt_sha}.webp").exists():
                    chosen = alt
                else:
                    chosen = alt
        if chosen is not None:
            out[vid] = chosen
    return out


def pending_reviews(cur):
    cur.execute("SELECT COUNT(*) AS n FROM market_identity_review_queue WHERE status = 'pending'")
    row = cur.fetchone() or {"n": 0}
    return int(row["n"] or 0)


def variant_meta(cur, variant_ids):
    if not variant_ids:
        return {}
    ph = ",".join(["%s"] * len(variant_ids))
    cur.execute(
        f"""
        SELECT id, opaque_id, tcg_code, card_language, canonical_name, set_name,
               collector_number, identity_status
        FROM catalog_variant
        WHERE id IN ({ph})
        """,
        tuple(variant_ids),
    )
    return {int(r["id"]): r for r in cur.fetchall()}

def classify_gaps(members, meta, ids, prices, images, freezes):
    gaps = []
    for member in members:
        vid = int(member["variant_id"])
        m = meta.get(vid) or {}
        freeze_rows = freezes.get(vid) or []
        frozen_kinds = {
            str(r["freeze_kind"])
            for r in freeze_rows
            if r.get("acceptance_status") == "accepted"
        }
        frozen_sources = {
            str(r["source_code"])
            for r in freeze_rows
            if r.get("freeze_kind") == "source" and r.get("acceptance_status") == "accepted"
        }
        identity_list = ids.get(vid) or []
        exact_sources = {
            str(r["source_code"])
            for r in identity_list
            if str(r.get("match_status") or "") == "exact"
        }
        reasons = []
        if "identity" not in frozen_kinds:
            if str(m.get("identity_status") or "") not in {"confirmed", "canonical"}:
                reasons.append("identity_unconfirmed")
            else:
                reasons.append("identity_not_frozen")
        if not exact_sources:
            reasons.append("source_binding_missing")
        elif not (exact_sources & frozen_sources) and "source" not in frozen_kinds:
            reasons.append("source_not_frozen")
        if vid not in prices:
            reasons.append("price_missing")
        img = images.get(vid)
        if not img:
            reasons.append("image_missing")
        elif "image" not in frozen_kinds:
            approved = (
                int(img.get("review_approved") or 0) == 1
                or str(img.get("semantic_match_status") or "") == "human_or_vision_confirmed"
            )
            reasons.append("image_unapproved" if not approved else "image_not_frozen")
        if not reasons:
            continue
        gaps.append(
            {
                "variantId": vid,
                "opaqueId": m.get("opaque_id"),
                "name": m.get("canonical_name"),
                "set": m.get("set_name"),
                "collector": m.get("collector_number"),
                "tcg": m.get("tcg_code"),
                "marketRank": member.get("market_rank"),
                "memberRole": member.get("member_role"),
                "identityStatus": m.get("identity_status"),
                "exactSources": sorted(exact_sources),
                "frozenKinds": sorted(frozen_kinds),
                "hasPrice": vid in prices,
                "priceUsd": float(prices[vid]["price_usd"]) if vid in prices else None,
                "priceSource": prices[vid]["source_code"] if vid in prices else None,
                "hasImage": bool(img),
                "imageSha256": (img or {}).get("content_sha256"),
                "reasons": reasons,
            }
        )
    return gaps


def cmd_status():
    load_env()
    conn = db()
    try:
        cur = conn.cursor()
        ensure_freeze_table(cur)
        conn.commit()
        universe = current_universe(cur)
        vids = universe["variantIds"]
        freezes = freeze_map(cur, vids)
        products = (fe.load_bulk(cur, vids, include_daily=False).get("product") or {})
        gaps = [
            {
                "variantId": vid,
                "opaqueId": (products.get(vid) or {}).get("opaque_id"),
                "marketRank": (products.get(vid) or {}).get("canonical_market_rank"),
                "reasons": ["026_product_projection_not_ready"],
            }
            for vid in vids
            if int((products.get(vid) or {}).get("product_ready") or 0) != 1
        ]
        frozen_identity = sum(1 for rows in freezes.values() if any(r["freeze_kind"] == "identity" for r in rows))
        frozen_source = sum(1 for rows in freezes.values() if any(r["freeze_kind"] == "source" for r in rows))
        frozen_image = sum(1 for rows in freezes.values() if any(r["freeze_kind"] == "image" for r in rows))
        product_ready = sum(
            int((products.get(vid) or {}).get("product_ready") or 0) == 1
            for vid in vids
        )
        exact_metric = sum(
            _is_canonical_price_route(
                str((products.get(vid) or {}).get("card_language") or ""),
                str((products.get(vid) or {}).get("psa10_price_source_code") or ""),
                (products.get(vid) or {}).get("selected_price_route_priority"),
                (products.get(vid) or {}).get("eligible_pricecharting_exists"),
            )
            and str((products.get(vid) or {}).get("population_source_code") or "")
            == "gemrate"
            and len(str((products.get(vid) or {}).get("metric_lineage_sha256") or ""))
            == 64
            for vid in vids
        )
        canonical_images = sum(
            bool((products.get(vid) or {}).get("canonical_image_content_sha256"))
            and int((products.get(vid) or {}).get("canonical_image_acceptance_id") or 0) > 0
            for vid in vids
        )
        payload = {
            "asOf": utc_now(),
            "mode": "operator",
            "database": os.environ.get("CARDZ_DB_NAME", "cardz_market_cap"),
            "universe": {
                "lockId": universe.get("lockId"),
                "lockSha256": universe.get("lockSha256"),
                "memberCount": universe.get("memberCount"),
                "effectiveAt": universe.get("effectiveAt"),
            },
            "counts": {
                "members": len(vids),
                "withExactSource": exact_metric,
                "withPrice": exact_metric,
                "withImage": canonical_images,
                "frozenIdentity": frozen_identity,
                "frozenSource": frozen_source,
                "frozenImage": frozen_image,
                "gapCards": len(gaps),
                "pendingIdentityReviews": pending_reviews(cur),
                "productSubsetReady": product_ready,
            },
            "rules": {
                "acceptedBindingsFrozen": True,
                "universeLocked": True,
                "productUsesSnapshotOnly": True,
                "operatorReadsMysql": True,
            },
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return payload
    finally:
        conn.close()


def cmd_export_gaps(limit=None):
    load_env()
    conn = db()
    try:
        cur = conn.cursor()
        ensure_freeze_table(cur)
        conn.commit()
        universe = current_universe(cur)
        vids = universe["variantIds"]
        products = (fe.load_bulk(cur, vids, include_daily=False).get("product") or {})
        gaps = []
        for vid in vids:
            row = products.get(vid) or {}
            reasons = []
            if int(row.get("identity_complete") or 0) != 1:
                reasons.append("identity_not_ready")
            if int(row.get("official_name_complete") or 0) != 1:
                reasons.append("official_name_not_ready")
            if int(row.get("canonical_metric_complete") or 0) != 1:
                reasons.append("canonical_metric_not_ready")
            if int(row.get("canonical_image_complete") or 0) != 1:
                reasons.append("canonical_image_not_ready")
            if int(row.get("canonical_rank_complete") or 0) != 1:
                reasons.append("canonical_rank_not_ready")
            if int(row.get("product_ready") or 0) != 1 and not reasons:
                reasons.append("026_product_projection_not_ready")
            if reasons:
                gaps.append(
                    {
                        "variantId": vid,
                        "opaqueId": row.get("opaque_id"),
                        "marketRank": row.get("canonical_market_rank"),
                        "officialName": row.get("official_full_name"),
                        "priceSource": row.get("psa10_price_source_code"),
                        "populationSource": row.get("population_source_code"),
                        "reasons": reasons,
                    }
                )
        if limit is not None:
            gaps = gaps[: max(0, limit)]
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        path = OUT_DIR / "gaps.json"
        payload = {
            "asOf": utc_now(),
            "universeLockId": universe.get("lockId"),
            "universeLockSha256": universe.get("lockSha256"),
            "count": len(gaps),
            "gaps": gaps,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(json.dumps({"action": "export-gaps", "count": len(gaps), "path": str(path)}, ensure_ascii=False))
        return payload
    finally:
        conn.close()

def accept_binding(*, variant_id, freeze_kind, source_code="", actor="daddy", note=None):
    if freeze_kind not in FREEZE_KINDS:
        raise SystemExit(f"freeze_kind must be one of {FREEZE_KINDS}")
    load_env()
    conn = db()
    try:
        cur = conn.cursor()
        ensure_freeze_table(cur)
        cur.execute("SELECT id, opaque_id, identity_status FROM catalog_variant WHERE id=%s", (variant_id,))
        variant = cur.fetchone()
        if not variant:
            raise SystemExit(f"variant not found: {variant_id}")
        external_entity_id = ""
        content_sha256 = ""
        if freeze_kind == "identity":
            if str(variant.get("identity_status") or "") not in {"confirmed", "canonical"}:
                note = (note or "") + f" | identity_status={variant.get('identity_status')}"
        elif freeze_kind == "source":
            if not source_code:
                raise SystemExit("source freeze requires --source-code")
            cur.execute(
                """
                SELECT external_entity_id, match_status
                FROM catalog_source_identity
                WHERE variant_id=%s AND source_code=%s
                LIMIT 1
                """,
                (variant_id, source_code),
            )
            row = cur.fetchone()
            if not row:
                raise SystemExit(f"no catalog_source_identity for variant={variant_id} source={source_code}")
            external_entity_id = str(row["external_entity_id"])
        elif freeze_kind == "image":
            cur.execute(
                """
                SELECT a.content_sha256
                FROM market_image_asset a
                LEFT JOIN market_image_review_approval b ON b.image_asset_id=a.id
                LEFT JOIN market_image_qc q ON q.image_asset_id=a.id
                WHERE a.variant_id=%s AND a.image_kind='raw_front'
                ORDER BY CASE WHEN b.image_asset_id IS NULL THEN 0 ELSE 1 END DESC,
                         q.public_allowed DESC, a.id DESC
                LIMIT 1
                """,
                (variant_id,),
            )
            row = cur.fetchone()
            if not row:
                raise SystemExit(f"no raw_front image for variant={variant_id}")
            content_sha256 = str(row["content_sha256"])
        evidence = sha256_text(
            "|".join([str(variant_id), freeze_kind, source_code or "", external_entity_id, content_sha256, actor, utc_now()])
        )
        cur.execute(
            """
            INSERT INTO operator_binding_freeze
                (variant_id, freeze_kind, source_code, external_entity_id, content_sha256,
                 acceptance_status, actor, evidence_sha256, note, accepted_at)
            VALUES (%s,%s,%s,%s,%s,'accepted',%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                external_entity_id=VALUES(external_entity_id),
                content_sha256=VALUES(content_sha256),
                acceptance_status='accepted',
                actor=VALUES(actor),
                evidence_sha256=VALUES(evidence_sha256),
                note=VALUES(note),
                accepted_at=VALUES(accepted_at)
            """,
            (
                variant_id,
                freeze_kind,
                source_code or "",
                external_entity_id,
                content_sha256,
                actor,
                evidence,
                note,
                utc_now_sql(),
            ),
        )
        conn.commit()
        payload = {
            "action": "accept-binding",
            "variantId": variant_id,
            "opaqueId": variant.get("opaque_id"),
            "freezeKind": freeze_kind,
            "sourceCode": source_code or None,
            "externalEntityId": external_entity_id or None,
            "contentSha256": content_sha256 or None,
            "acceptanceStatus": "accepted",
            "actor": actor,
            "evidenceSha256": evidence,
            "acceptedAt": utc_now(),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return payload
    finally:
        conn.close()


def _projection_locales(value: Any) -> dict[str, str | None]:
    raw = value if isinstance(value, dict) else {}
    out: dict[str, str | None] = {}
    for locale in ("en", "zhTW", "zhCN", "ja", "ko"):
        text = raw.get(locale)
        if isinstance(text, str) and text.strip():
            out[locale] = text.strip()
        else:
            out[locale] = None
    return out


def _latest_evidence_at(*values: Any) -> str | None:
    normalized = [fe.asof_iso(value) for value in values if value is not None]
    normalized = [value for value in normalized if value]
    return max(normalized) if normalized else None


def build_operator_cards(cur, members, product_subset: bool):
    """Export the public card contract from the two canonical projections only."""

    vids = [int(member["variant_id"]) for member in members]
    bulk = fe.load_bulk(cur, vids)
    products: dict[int, dict[str, Any]] = bulk.get("product") or {}
    if product_subset:
        if len(vids) != TONIGHT_CARD_COUNT:
            raise RuntimeError(
                f"active universe is {len(vids)} cards, expected locked {TONIGHT_CARD_COUNT}"
            )
        incomplete = [
            vid
            for vid in vids
            if vid not in products or int((products.get(vid) or {}).get("product_ready") or 0) != 1
        ]
        if incomplete:
            raise RuntimeError(
                "canonical product projection is incomplete for "
                f"{len(incomplete)} locked cards: {incomplete[:12]}"
            )

    ranked_vids = sorted(
        vids,
        key=lambda vid: (
            int((products.get(vid) or {}).get("canonical_market_rank") or 4294967295),
            vid,
        ),
    )
    projected_ranks = [
        int((products.get(vid) or {}).get("canonical_market_rank") or 0)
        for vid in ranked_vids
    ]
    if product_subset and projected_ranks != list(range(1, len(ranked_vids) + 1)):
        raise RuntimeError("026 canonical market ranks are missing, duplicated or non-contiguous")
    cards: list[dict[str, Any]] = []
    evidence_times: list[str] = []

    for vid in ranked_vids:
        row = products.get(vid)
        if not row:
            if product_subset:
                raise RuntimeError(f"missing canonical product projection for variant {vid}")
            continue

        opaque = str(row.get("opaque_id") or f"variant_{vid}")
        tcg_raw = str(row.get("tcg_code") or "other").lower()
        if tcg_raw not in {"pokemon", "one-piece"}:
            if product_subset:
                raise RuntimeError(
                    f"unsupported canonical TCG code for variant {vid}: {tcg_raw!r}"
                )
            tcg = "other"
        else:
            tcg = tcg_raw

        lang = row.get("card_language")
        if lang not in {"en", "ja", "ko", "zhCN", "zhTW"}:
            lang = None
        collector = str(row.get("collector_number") or "")
        official_name = str(row.get("official_full_name") or "").strip()
        if product_subset and not official_name:
            raise RuntimeError(f"missing accepted PSA/GemRate official name for variant {vid}")
        names = _projection_locales(row.get("localized_names_json"))
        sets = _projection_locales(row.get("localized_set_names_json"))
        stories = _projection_locales(row.get("localized_stories_json"))
        image_alt = _projection_locales(row.get("image_alt_json"))
        if official_name:
            # Locale evidence remains canonical in MySQL, but public display
            # names and accessibility text have one authority: the accepted
            # PSA/GemRate full name. No translated alias can change card identity.
            names = {
                locale: official_name for locale in ("en", "zhTW", "zhCN", "ja", "ko")
            }
            image_alt = dict(names)

        price_usd = (
            float(row["psa10_price_usd"])
            if row.get("psa10_price_usd") is not None
            else None
        )
        pop_value = (
            int(row["psa10_population"])
            if row.get("psa10_population") is not None
            else None
        )
        market_cap = (
            float(row["market_cap_usd"])
            if row.get("market_cap_usd") is not None
            else None
        )
        canonical_rank = int(row.get("canonical_market_rank") or 0)
        metric_lineage_sha256 = str(row.get("metric_lineage_sha256") or "")
        if product_subset:
            if not _is_canonical_price_route(
                str(row.get("card_language") or ""),
                str(row.get("psa10_price_source_code") or ""),
                row.get("selected_price_route_priority"),
                row.get("eligible_pricecharting_exists"),
            ):
                raise RuntimeError(
                    f"variant {vid} does not follow the canonical EN PC-to-SNK or non-EN SNK-only price route"
                )
            if str(row.get("population_source_code") or "") != "gemrate":
                raise RuntimeError(f"variant {vid} does not use exact GemRate POP authority")
            if len(metric_lineage_sha256) != 64:
                raise RuntimeError(f"variant {vid} has no canonical metric lineage")
            expected_market_cap = round(float(price_usd or 0) * int(pop_value or 0), 6)
            if market_cap is None or abs(float(market_cap) - expected_market_cap) > 0.000001:
                raise RuntimeError(f"variant {vid} market cap is not exact price multiplied by POP")
        price_at = fe.asof_iso(
            row.get("psa10_price_observed_date")
            or row.get("psa10_price_effective_at")
            or row.get("psa10_price_source_observed_at")
        )
        pop_at = fe.asof_iso(row.get("population_effective_at"))
        cap_at = _latest_evidence_at(price_at, pop_at)
        history, windows = fe.build_history_windows(vid, bulk)

        ungraded_value = (
            float(row["ungraded_reference_price_usd"])
            if row.get("ungraded_reference_price_usd") is not None
            else None
        )
        ungraded_at = fe.asof_iso(row.get("ungraded_reference_observed_at"))
        image_sha = str(row.get("canonical_image_content_sha256") or "")
        has_image = bool(image_sha)
        image_width = int(row.get("canonical_image_width") or 0)
        image_height = int(row.get("canonical_image_height") or 0)
        image_qc_at = row.get("canonical_image_qc_at")
        image_source_path = str(row.get("canonical_image_source_path") or "")
        image_acceptance_id = int(row.get("canonical_image_acceptance_id") or 0)
        image_lineage_sha256 = str(row.get("canonical_image_lineage_sha256") or "")
        selected_snk_en = image_source_path.startswith("snkrdunk-en:")
        if product_subset and (
            not has_image
            or image_width <= 0
            or image_height <= 0
            or image_acceptance_id <= 0
            or len(image_lineage_sha256) != 64
        ):
            raise RuntimeError(f"variant {vid} has no accepted canonical 026 image lineage")

        exact_sources = sorted(
            source.strip()
            for source in str(row.get("exact_source_codes") or "").split(",")
            if source.strip()
        )
        printing_identity = {
            "setName": str(row.get("canonical_set_name") or ""),
            "collectorNumber": collector,
            "editionCode": str(row.get("edition_code") or ""),
            "finishCode": str(row.get("finish_code") or ""),
            "cardLanguage": lang,
            "canonicalPrintingSha256": str(row.get("canonical_printing_sha256") or ""),
            "evidenceSha256": str(row.get("printing_evidence_sha256") or ""),
            # Never derive a display set code from names or collector text.
            "setCode": str(row.get("set_code") or "") or None,
        }

        card = {
            "id": opaque,
            "rank": canonical_rank,
            "marketRank": canonical_rank,
            "viewRank": canonical_rank,
            "tcg": tcg,
            "cardLanguage": lang,
            "collectorNumber": {
                "display": collector,
                "normalized": collector.lower(),
                "complete": bool(collector) and ("/" in collector or collector.lower() != "unknown"),
            },
            "identityStatus": (
                "confirmed"
                if str(row.get("identity_status") or "") in {"confirmed", "canonical"}
                and int(row.get("identity_complete") or 0) == 1
                else "provisional"
            ),
            "officialName": official_name,
            "names": names,
            "sets": sets,
            "stories": stories,
            "image": {
                "src": f"/market-assets/{image_sha}.webp" if has_image else "/card-placeholder.svg",
                "sha256": image_sha if has_image else "0" * 64,
                "kind": "raw_front",
                "width": image_width,
                "height": image_height,
                "alt": image_alt,
                "qcAt": fe.asof_iso(image_qc_at),
                "variants": {
                    "200": f"/market-assets/{image_sha}_200.webp",
                    "600": f"/market-assets/{image_sha}_600.webp",
                } if has_image else None,
            },
            "pricePsa10": fe.metric(
                price_usd,
                "ready" if price_usd is not None else "unavailable",
                price_at,
            ),
            "priceUngradedReference": fe.metric(
                ungraded_value,
                "ready" if ungraded_value is not None else "unavailable",
                ungraded_at if ungraded_value is not None else None,
            ),
            "populationPsa10": {
                **fe.metric(
                    pop_value,
                    "ready" if pop_value is not None else "unavailable",
                    pop_at,
                ),
                "estimated": False,
            },
            "marketCap": fe.metric(
                market_cap,
                "ready" if market_cap is not None else "unavailable",
                cap_at if market_cap is not None else None,
            ),
            "windows": windows,
            "historyDaily": history,
            "printingIdentity": printing_identity,
            "operator": {
                "variantId": vid,
                "exactSources": exact_sources,
                "hasPrice": price_usd is not None,
                "hasImage": has_image,
                "productEligible": int(row.get("product_ready") or 0) == 1,
                "imagePolicy": {
                    "policyId": CANONICAL_IMAGE_POLICY_ID,
                    "applies": True,
                    "snkEnSelected": selected_snk_en,
                    "selectedSource": "snkrdunk-en" if selected_snk_en else "accepted-freeze",
                    "selectedSha256": image_sha,
                    "acceptanceId": image_acceptance_id,
                    "lineageSha256": image_lineage_sha256,
                },
                "officialNameAcceptanceId": int(row.get("official_name_acceptance_id") or 0),
                "officialNameEvidenceSha256": str(row.get("official_name_evidence_sha256") or ""),
                "metricLineageSha256": metric_lineage_sha256,
            },
        }
        cards.append(card)
        daily_evidence = [
            value
            for fact in (bulk.get("daily") or {}).get(vid, [])
            for value in (
                fact.get("fact_effective_at"),
                fact.get("price_source_observed_at"),
                fact.get("sales_evidence_at"),
            )
            if value is not None
        ]
        card_evidence = _latest_evidence_at(
            price_at,
            pop_at,
            ungraded_at,
            row.get("identity_accepted_at"),
            row.get("locale_latest_observed_at"),
            row.get("canonical_image_qc_at"),
            row.get("canonical_image_source_observed_at"),
            row.get("official_name_observed_at"),
            *daily_evidence,
        )
        if card_evidence:
            evidence_times.append(card_evidence)

    if product_subset and len(cards) != TONIGHT_CARD_COUNT:
        raise RuntimeError(
            f"canonical product export has {len(cards)} cards, expected {TONIGHT_CARD_COUNT}"
        )
    build_operator_cards._last_fx = bulk.get("fx") or {}
    build_operator_cards._effective_at = max(evidence_times) if evidence_times else None
    return cards

def write_snapshot(cards, *, generation_prefix, mode, blockers, output: Path):
    now = utc_now()
    effective_at = getattr(build_operator_cards, "_effective_at", None)
    if not effective_at:
        raise RuntimeError("canonical projection did not provide an evidence effective time")
    top = cards[:100]
    watch = cards[100:]
    generation_id = f"{generation_prefix}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    snapshot = {
        "schemaVersion": "2.0.0",
        "generation": {
            "id": generation_id,
            "generatedAt": now,
            "effectiveAt": effective_at,
            "contentSha256": "",
            "qcReceiptSha256": "",
            "mode": mode,
            "productionEligible": False,
            "blockers": blockers,
        },
        "universe": {
            "populationMin": 1000,
            "grade": "PSA 10",
            "rankingMetric": "psa10_market_cap_usd",
            "windows": ["1d", "7d", "30d"],
            "salesCoverage": "partial",
        },
        "coverage": fe.coverage_from_cards(cards, top, watch),
        "currencies": fe.currencies_block(effective_at, getattr(build_operator_cards, "_last_fx", {})),
        "top100": top,
        "watchlist": watch,
    }
    digest = canonical_snapshot_sha256(snapshot)
    snapshot["generation"]["contentSha256"] = digest
    atomic_json(output, snapshot)
    return {
        "action": "export-snapshot",
        "path": str(output),
        "generationId": generation_id,
        "cards": len(cards),
        "top100": len(top),
        "watchlist": len(watch),
        "contentSha256": digest,
        "mode": mode,
        "blockers": blockers,
    }


def cmd_export_operator_snapshot(output=None):
    load_env()
    conn = db()
    try:
        cur = conn.cursor()
        members = fe.load_active_members(cur)
        cards = build_operator_cards(cur, members, product_subset=False)
        out = output or (OUT_DIR / "operator-snapshot.json")
        result = write_snapshot(
            cards,
            generation_prefix="operator",
            mode="demo",
            blockers=["operator_live_db_view", "not_a_product_release"],
            output=out,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return result
    finally:
        conn.close()


def cmd_export_product_subset(
    output=None,
):
    load_env()
    conn = db()
    try:
        cur = conn.cursor()
        members = fe.load_active_members(cur)
        cards = build_operator_cards(cur, members, product_subset=True)
        image_decisions = []
        for card in cards:
            operator_row = card.get("operator") or {}
            decision = operator_row.get("imagePolicy") or {}
            image_decisions.append(
                {
                    "marketRank": int(card.get("marketRank") or 0),
                    "variantId": int(operator_row.get("variantId") or 0),
                    "opaqueId": card.get("id"),
                    "snkEnSelected": decision.get("snkEnSelected") is True,
                    "selectedSource": decision.get("selectedSource"),
                    "selectedSha256": decision.get("selectedSha256"),
                    "acceptanceId": int(decision.get("acceptanceId") or 0),
                    "lineageSha256": decision.get("lineageSha256"),
                    "officialNameAcceptanceId": int(
                        operator_row.get("officialNameAcceptanceId") or 0
                    ),
                    "officialNameEvidenceSha256": operator_row.get(
                        "officialNameEvidenceSha256"
                    ),
                    "metricLineageSha256": operator_row.get("metricLineageSha256"),
                }
            )
        image_policy = {
            "policyId": CANONICAL_IMAGE_POLICY_ID,
            "scope": "locked-active-cohort-canonical-pointer",
            "cardCount": len(image_decisions),
            "snkEnSelected": sum(row["snkEnSelected"] for row in image_decisions),
            "acceptedFallback": sum(
                not row["snkEnSelected"] and row["selectedSource"] == "accepted-freeze"
                for row in image_decisions
            ),
            "decisions": image_decisions,
        }
        if image_policy["cardCount"] != TONIGHT_CARD_COUNT:
            raise RuntimeError("026 canonical image policy requires all 762 active cards")
        if image_policy["snkEnSelected"] + image_policy["acceptedFallback"] != TONIGHT_CARD_COUNT:
            raise RuntimeError("one or more cards have no accepted canonical image decision")
        public_cards = []
        for card in cards:
            clean = dict(card)
            clean.pop("operator", None)
            public_cards.append(clean)
        out = output or (OUT_DIR / "product-subset-snapshot.json")
        blockers = ["product_subset_export", "awaiting_daddy_promote"]
        if not public_cards:
            blockers.append("no_freeze_qualified_cards")
        result = write_snapshot(
            public_cards,
            generation_prefix="product_subset",
            mode="demo",
            blockers=blockers,
            output=out,
        )
        result["productSubsetReady"] = len(public_cards)
        result["imagePolicy"] = image_policy
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return result
    finally:
        conn.close()


def cmd_snk_image_priority_status() -> dict[str, Any]:
    """Report the 026 cohort-wide canonical SNK EN image selection without writing."""

    load_env()
    conn = db()
    try:
        cur = conn.cursor()
        universe = current_universe(cur)
        cards = build_operator_cards(
            cur,
            universe.get("members") or [],
            product_subset=True,
        )
        variant_ids = [int((card.get("operator") or {})["variantId"]) for card in cards]
        selected = []
        fallback = []
        for card in cards:
            operator_row = card.get("operator") or {}
            decision = operator_row.get("imagePolicy") or {}
            item = {
                "marketRank": int(card["marketRank"]),
                "variantId": int(operator_row["variantId"]),
                "opaqueId": card.get("id"),
                "selectedSource": decision.get("selectedSource"),
                "selectedSha256": str((card.get("image") or {}).get("sha256") or ""),
                "acceptanceId": int(decision.get("acceptanceId") or 0),
                "lineageSha256": decision.get("lineageSha256"),
            }
            if decision.get("snkEnSelected") is True:
                selected.append(item)
            else:
                fallback.append(item)

        result = {
            "action": "snk-image-priority-status",
            "policyId": CANONICAL_IMAGE_POLICY_ID,
            "scope": "locked-active-cohort-canonical-pointer",
            "cards": len(variant_ids),
            "snkEnSelected": len(selected),
            "acceptedFallback": len(fallback),
            "selected": sorted(selected, key=lambda row: row["marketRank"]),
            "writes": False,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return result
    finally:
        conn.close()


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise RuntimeError(f"{label} is missing or invalid: {path}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} must be a JSON object: {path}")
    return value


def _authorized_refresh_report(path: Path) -> dict[str, Any]:
    """Reuse one completed six-adapter refresh without repeating network work."""

    raw_bytes = path.read_bytes()
    source_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    raw = _read_json_object(path, "authorized refresh report")
    refresh = raw.get("refresh") if raw.get("action") == "pass" else raw
    if not isinstance(refresh, dict):
        raise RuntimeError("authorized refresh report has no refresh object")
    adapter_results = {
        str(row.get("adapter")): row
        for row in (refresh.get("results") or [])
        if isinstance(row, dict)
    }
    required_adapters = set(CHECKPOINT_ADAPTERS)
    polls = ((refresh.get("postFreshness") or {}).get("polls") or {})
    failures: list[str] = []
    if refresh.get("ok") is not True:
        failures.append("refresh report is not successful")
    if set(adapter_results) != required_adapters:
        failures.append("refresh report does not contain the exact six adapters")
    if any(
        (adapter_results.get(adapter) or {}).get("ok") is not True
        for adapter in required_adapters
    ):
        failures.append("one or more refresh adapters did not complete")
    if any((polls.get(adapter) or {}).get("slaOk") is not True for adapter in required_adapters):
        failures.append("one or more refresh checkpoints exceed the recorded SLA")
    if failures:
        raise RuntimeError("authorized refresh report rejected: " + "; ".join(failures))

    source_receipt = path.resolve()
    if source_receipt == (OUT_DIR / "pass_receipt.json").resolve():
        source_receipt = (
            OUT_DIR
            / "collect"
            / f"authorized-refresh-reuse-{source_sha256[:24]}.json"
        ).resolve()
        if not source_receipt.exists():
            source_receipt.parent.mkdir(parents=True, exist_ok=True)
            temporary = source_receipt.with_name(f".{source_receipt.name}.{os.getpid()}.next")
            temporary.write_bytes(raw_bytes)
            os.replace(temporary, source_receipt)

    reused = json.loads(json.dumps(refresh, ensure_ascii=False, default=str))
    reused.update(
        {
            "enabled": True,
            "ok": True,
            "method": "authorized_completed_refresh_receipt",
            "reusedWithoutNetwork": True,
            "sourceReceipt": str(source_receipt),
            "sourceReceiptSha256": source_sha256,
        }
    )
    return reused


def _read_jsonl(path: Path, label: str) -> list[dict[str, Any]]:
    try:
        rows = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8-sig").splitlines()
            if line.strip()
        ]
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise RuntimeError(f"{label} is missing or invalid: {path}") from exc
    if any(not isinstance(row, dict) for row in rows):
        raise RuntimeError(f"{label} must contain JSON objects: {path}")
    return rows


def _checkpoint_stream_key(variant_id: int, external_id: Any) -> str:
    return f"{int(variant_id)}:{str(external_id or '').strip()}"[:100]


def _active_checkpoint_gate(cur, *, active_ids: set[int]) -> dict[str, Any]:
    registry = _read_jsonl(COLLECT_REGISTRY_PATH, "active collect registry")
    registry_ids = {int(row["variantId"]) for row in registry}
    if registry_ids != active_ids:
        raise RuntimeError("collect registry does not match the current active universe")
    placeholders = ",".join(["%s"] * len(CHECKPOINT_ADAPTERS))
    cur.execute(
        f"""
        SELECT source_code, stream_key, last_effective_at, last_payload_sha256,
               last_run_id
        FROM market_ingest_checkpoint
        WHERE source_code IN ({placeholders})
        """,
        CHECKPOINT_ADAPTERS,
    )
    checkpoints = {
        (str(row["source_code"]), str(row["stream_key"])): row
        for row in cur.fetchall()
    }
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    polls: dict[str, Any] = {}
    for adapter in CHECKPOINT_ADAPTERS:
        streams = [row for row in registry if row.get("adapter") == adapter]
        if not streams:
            raise RuntimeError(f"active registry has no streams for {adapter}")
        keys = [
            _checkpoint_stream_key(int(row["variantId"]), row.get("externalId"))
            for row in streams
        ]
        if len(keys) != len(set(keys)):
            raise RuntimeError(f"active registry has duplicate stream keys for {adapter}")
        missing: list[int] = []
        stamps: list[datetime] = []
        run_ids: set[int] = set()
        for row, stream_key in zip(streams, keys):
            checkpoint = checkpoints.get((adapter, stream_key))
            stamp = (checkpoint or {}).get("last_effective_at")
            if checkpoint is None or stamp is None:
                missing.append(int(row["variantId"]))
                continue
            if stamp.tzinfo is not None:
                stamp = stamp.astimezone(timezone.utc).replace(tzinfo=None)
            stamps.append(stamp)
            if checkpoint.get("last_run_id") is not None:
                run_ids.add(int(checkpoint["last_run_id"]))
        oldest = min(stamps) if stamps else None
        newest = max(stamps) if stamps else None
        max_age = (now - oldest).total_seconds() / 3600 if oldest else None
        if missing or max_age is None or max_age > CHECKPOINT_SLA_HOURS:
            raise RuntimeError(
                f"checkpoint gate failed {adapter}: streams={len(streams)} "
                f"missing={len(missing)} maxAgeHours={max_age}"
            )
        polls[adapter] = {
            "expectedStreams": len(streams),
            "checkpointedStreams": len(stamps),
            "missingStreams": len(missing),
            "oldestSuccessAt": oldest.isoformat(sep=" "),
            "newestSuccessAt": newest.isoformat(sep=" "),
            "oldestAgeHours": round(max_age, 2),
            "runIds": sorted(run_ids),
            "slaOk": True,
        }
    return {
        "asOf": utc_now(),
        "slaHours": CHECKPOINT_SLA_HOURS,
        "polls": polls,
    }


def cmd_freeze_active_sources_from_checkpoints(*, actor: str, authorization_note: str) -> dict[str, Any]:
    """Freeze missing active GemRate source bindings only after fresh exact checkpoints."""
    if not actor.strip() or actor.strip().lower() == "daddy":
        raise RuntimeError("a truthful non-DADDY actor is required")
    if not authorization_note.strip():
        raise RuntimeError("--authorization-note is required")
    registry = _read_jsonl(COLLECT_REGISTRY_PATH, "active collect registry")
    gemrate_rows = [row for row in registry if row.get("adapter") == "gemrate_pop"]
    by_variant = {int(row["variantId"]): row for row in gemrate_rows}
    if len(by_variant) != len(gemrate_rows):
        raise RuntimeError("GemRate active registry contains duplicate variants")

    load_env()
    conn = db()
    try:
        cur = conn.cursor()
        ensure_freeze_table(cur)
        universe = current_universe(cur)
        active_ids = set(universe.get("variantIds") or [])
        if len(active_ids) != TONIGHT_CARD_COUNT or set(by_variant) != active_ids:
            raise RuntimeError("GemRate registry does not cover the locked 762-card cohort")
        placeholders = ",".join(["%s"] * len(active_ids))
        cur.execute(
            f"""
            SELECT variant_id, external_entity_id, match_status
            FROM catalog_source_identity
            WHERE source_code='gemrate' AND variant_id IN ({placeholders})
              AND match_status = 'exact'
            """,
            tuple(sorted(active_ids)),
        )
        identity_rows: dict[int, set[str]] = {}
        for row in cur.fetchall():
            identity_rows.setdefault(int(row["variant_id"]), set()).add(
                str(row["external_entity_id"])
            )
        mismatched = [
            variant_id
            for variant_id, row in by_variant.items()
            if str(row.get("externalId") or "") not in identity_rows.get(variant_id, set())
        ]
        if mismatched:
            raise RuntimeError(f"GemRate exact identity mismatch for {len(mismatched)} active variants")

        cur.execute(
            f"""
            SELECT variant_id
            FROM operator_binding_freeze
            WHERE freeze_kind='source' AND acceptance_status='accepted'
              AND variant_id IN ({placeholders})
            """,
            tuple(sorted(active_ids)),
        )
        already_frozen = {int(row["variant_id"]) for row in cur.fetchall()}
        missing_ids = sorted(active_ids - already_frozen)

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        checkpoint_rows: dict[str, dict[str, Any]] = {}
        cur.execute(
            """
            SELECT stream_key, last_effective_at, last_payload_sha256, last_run_id
            FROM market_ingest_checkpoint
            WHERE source_code='gemrate_pop'
            """
        )
        for row in cur.fetchall():
            checkpoint_rows[str(row["stream_key"])] = row

        accepted_at = utc_now_sql()
        evidence_rows: list[dict[str, Any]] = []
        for variant_id in missing_ids:
            external_id = str(by_variant[variant_id]["externalId"])
            stream_key = _checkpoint_stream_key(variant_id, external_id)
            checkpoint = checkpoint_rows.get(stream_key)
            stamp = (checkpoint or {}).get("last_effective_at")
            if checkpoint is None or stamp is None:
                raise RuntimeError(f"fresh GemRate checkpoint missing for active variant {variant_id}")
            if stamp.tzinfo is not None:
                stamp = stamp.astimezone(timezone.utc).replace(tzinfo=None)
            age_hours = (now - stamp).total_seconds() / 3600
            if age_hours > CHECKPOINT_SLA_HOURS:
                raise RuntimeError(f"GemRate checkpoint is stale for active variant {variant_id}")
            evidence_payload = {
                "contract": "launch_source_freeze_from_fresh_exact_checkpoint_v1",
                "variantId": variant_id,
                "sourceCode": "gemrate",
                "externalEntityId": external_id,
                "checkpointSource": "gemrate_pop",
                "checkpointRunId": int(checkpoint["last_run_id"]),
                "checkpointPayloadSha256": str(checkpoint["last_payload_sha256"]),
                "checkpointEffectiveAt": stamp.isoformat(),
                "actor": actor,
                "authorizationNote": authorization_note,
            }
            evidence_sha = sha256_text(
                json.dumps(evidence_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            )
            cur.execute(
                """
                INSERT INTO operator_binding_freeze
                    (variant_id, freeze_kind, source_code, external_entity_id,
                     content_sha256, acceptance_status, actor, evidence_sha256,
                     note, accepted_at)
                VALUES (%s,'source','gemrate',%s,'','accepted',%s,%s,%s,%s)
                """,
                (
                    variant_id,
                    external_id,
                    actor,
                    evidence_sha,
                    authorization_note,
                    accepted_at,
                ),
            )
            evidence_rows.append(
                {
                    "variantId": variant_id,
                    "checkpointRunId": int(checkpoint["last_run_id"]),
                    "checkpointEffectiveAt": stamp.isoformat(),
                    "evidenceSha256": evidence_sha,
                }
            )
        conn.commit()
        result = {
            "action": "freeze-active-sources-from-checkpoints",
            "ok": True,
            "universeLockId": universe.get("lockId"),
            "activeCards": len(active_ids),
            "sourceFrozenBefore": len(already_frozen),
            "inserted": len(evidence_rows),
            "sourceFrozenAfter": len(already_frozen) + len(evidence_rows),
            "sourceCode": "gemrate",
            "checkpointSource": "gemrate_pop",
            "actor": actor,
            "authorizationNote": authorization_note,
            "evidence": evidence_rows,
        }
        output = OUT_DIR / "launch-source-freeze-receipt.json"
        atomic_json(output, result)
        print(json.dumps({key: value for key, value in result.items() if key != "evidence"}, ensure_ascii=False, indent=2))
        print(f"RECEIPT {output}")
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def cmd_promote_product_subset(
    *,
    snapshot_path: Path,
    receipt_path: Path,
    output_path: Path,
    expected_cards: int = TONIGHT_CARD_COUNT,
    expected_backlog: int = TONIGHT_BACKLOG_COUNT,
) -> dict[str, Any]:
    """Bind one passed operator export to production bytes without deploying it."""

    snapshot = _read_json_object(snapshot_path, "product subset snapshot")
    receipt_bytes = receipt_path.read_bytes()
    try:
        receipt = json.loads(receipt_bytes.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise RuntimeError(f"pass receipt is missing or invalid: {receipt_path}") from exc
    if not isinstance(receipt, dict):
        raise RuntimeError("pass receipt must be a JSON object")

    generation = snapshot.get("generation") or {}
    coverage = snapshot.get("coverage") or {}
    product_export = receipt.get("productExport") or {}
    status_counts = receipt.get("statusCounts") or {}
    candidate_counts = receipt.get("candidateCounts") or {}
    universe = receipt.get("universe") or {}
    refresh = receipt.get("refresh") or {}
    image_policy = receipt.get("imagePolicy") or {}
    cards = list(snapshot.get("top100") or []) + list(snapshot.get("watchlist") or [])
    current_hash = canonical_snapshot_sha256(snapshot)
    required_counts = (
        "members",
        "withExactSource",
        "withPrice",
        "withImage",
        "frozenIdentity",
        "frozenSource",
        "frozenImage",
        "productSubsetReady",
    )
    adapter_results = {
        str(row.get("adapter")): row
        for row in (refresh.get("results") or [])
        if isinstance(row, dict)
    }
    required_adapters = set(CHECKPOINT_ADAPTERS)
    freshness = refresh.get("postFreshness") or {}
    polls = freshness.get("polls") or {}
    allowed_staging_blockers = {"product_subset_export", "awaiting_daddy_promote"}
    blockers = set(generation.get("blockers") or [])
    image_decisions = image_policy.get("decisions") or []
    decisions_by_id = {
        str(row.get("opaqueId")): row
        for row in image_decisions
        if isinstance(row, dict) and row.get("opaqueId")
    }
    try:
        image_card_count = int(image_policy.get("cardCount"))
        snk_en_count = int(image_policy.get("snkEnSelected"))
        accepted_fallback_count = int(image_policy.get("acceptedFallback"))
    except (TypeError, ValueError):
        image_card_count = -1
        snk_en_count = -1
        accepted_fallback_count = -1

    failures: list[str] = []
    if receipt.get("action") != "pass":
        failures.append("receipt action is not pass")
    if image_policy.get("policyId") != CANONICAL_IMAGE_POLICY_ID:
        failures.append("receipt does not use the approved 026 canonical image policy")
    if product_export.get("imagePolicy") != image_policy:
        failures.append("product export image policy does not match the pass receipt")
    if image_card_count != expected_cards:
        failures.append("image policy does not cover the full active cohort")
    if snk_en_count + accepted_fallback_count != expected_cards:
        failures.append("canonical image decisions do not resolve every active card")
    if len(decisions_by_id) != expected_cards:
        failures.append("image policy decisions are not unique for the full active cohort")
    for card in cards:
        decision = decisions_by_id.get(str(card.get("id"))) or {}
        if (
            int(decision.get("marketRank") or -1) != int(card.get("marketRank") or -2)
            or decision.get("selectedSha256") != (card.get("image") or {}).get("sha256")
            or int(decision.get("acceptanceId") or 0) <= 0
            or len(str(decision.get("lineageSha256") or "")) != 64
            or int(decision.get("officialNameAcceptanceId") or 0) <= 0
            or len(str(decision.get("officialNameEvidenceSha256") or "")) != 64
            or len(str(decision.get("metricLineageSha256") or "")) != 64
            or not str(card.get("officialName") or "").strip()
        ):
            failures.append(f"026 canonical decision mismatch for card {card.get('id')}")
            break
    if refresh.get("ok") is not True:
        failures.append("active refresh did not complete")
    if set(adapter_results) != required_adapters or any(
        adapter_results.get(adapter, {}).get("ok") is not True
        for adapter in required_adapters
    ):
        failures.append("one or more required adapters did not complete")
    if any((polls.get(adapter) or {}).get("slaOk") is not True for adapter in required_adapters):
        failures.append("one or more active adapter checkpoints exceed 36 hours")
    if len(cards) != expected_cards:
        failures.append(f"snapshot card count is {len(cards)}, expected {expected_cards}")
    if len(snapshot.get("top100") or []) != 100:
        failures.append("snapshot top100 is not exactly 100")
    if int(product_export.get("cards") or -1) != expected_cards:
        failures.append("receipt product export card count mismatch")
    if product_export.get("generationId") != generation.get("id"):
        failures.append("receipt generation ID mismatch")
    if product_export.get("contentSha256") != generation.get("contentSha256"):
        failures.append("receipt content hash mismatch")
    if generation.get("contentSha256") != current_hash:
        failures.append("staged snapshot content hash is invalid")
    if generation.get("mode") != "demo" or generation.get("productionEligible") is not False:
        failures.append("input is not a blocked staging snapshot")
    if blockers != allowed_staging_blockers:
        failures.append("staging blockers are not the expected promotion-only blockers")
    if any(int(status_counts.get(field) or -1) != expected_cards for field in required_counts):
        failures.append("receipt readiness counts do not cover the full active cohort")
    if int(status_counts.get("gapCards", -1)) != 0 or int(receipt.get("gapCount", -1)) != 0:
        failures.append("receipt still has active gaps")
    if int(candidate_counts.get("activeUniverse") or -1) != expected_cards:
        failures.append("receipt active universe count mismatch")
    if int(candidate_counts.get("newCandidates") or -1) != expected_backlog:
        failures.append("candidate backlog count mismatch")
    if int(universe.get("memberCount") or -1) != expected_cards:
        failures.append("receipt universe member count mismatch")
    if not isinstance(universe.get("lockId"), int) or int(universe.get("lockId") or 0) <= 0:
        failures.append("receipt universe lock ID is invalid")
    lock_sha = str(universe.get("lockSha256") or "")
    if len(lock_sha) != 64 or any(char not in "0123456789abcdef" for char in lock_sha):
        failures.append("receipt universe lock hash is invalid")
    if int(coverage.get("completeIdentityCount") or -1) != expected_cards:
        failures.append("snapshot complete identity count mismatch")
    if failures:
        raise RuntimeError("promotion blocked: " + "; ".join(failures))

    receipt_sha256 = hashlib.sha256(receipt_bytes).hexdigest()
    fingerprint = sha256_text(
        json.dumps(
            {
                "database": "cardz_market_cap",
                "universeLockId": universe["lockId"],
                "universeLockSha256": lock_sha,
                "stagedContentSha256": generation["contentSha256"],
                "passReceiptSha256": receipt_sha256,
                "imagePolicy": image_policy,
                "cards": [
                    {
                        "id": card.get("id"),
                        "marketRank": card.get("marketRank"),
                        "officialName": card.get("officialName"),
                        "pricePsa10": card.get("pricePsa10"),
                        "populationPsa10": card.get("populationPsa10"),
                        "imageSha256": (card.get("image") or {}).get("sha256"),
                        "metricLineageSha256": (
                            decisions_by_id.get(str(card.get("id"))) or {}
                        ).get("metricLineageSha256"),
                    }
                    for card in cards
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    )
    run_id = f"operator_pass_{universe['lockId']}_{generation['id']}"
    if len(run_id) > 96:
        run_id = f"operator_pass_{universe['lockId']}_{receipt_sha256[:24]}"
    promoted = json.loads(json.dumps(snapshot, ensure_ascii=False, default=str))
    promoted_generation = promoted["generation"]
    promoted_generation.update(
        {
            # The immutable public QC receipt is created by the one-time
            # release materializer. The pass receipt remains DB lineage only.
            "qcReceiptSha256": "",
            "dbQc": {
                "runId": run_id,
                "database": "cardz_market_cap",
                "receiptSha256": receipt_sha256,
                "universeCandidateSha256": lock_sha,
            },
            "releaseProfile": RELAXED_RELEASE_PROFILE,
            "policySha256": RELAXED_POLICY_SHA256,
            "dbFingerprint": fingerprint,
            "evaluationId": int(universe["lockId"]),
            "mode": "production",
            "productionEligible": True,
            "blockers": [],
        }
    )
    promoted["coverage"]["claim"] = "verified-top-100"
    promoted["coverage"]["verifiedCount"] = len(cards)
    promoted["coverage"]["top100Count"] = len(promoted["top100"])
    promoted["coverage"]["watchlistCount"] = len(promoted["watchlist"])
    promoted_generation["contentSha256"] = canonical_snapshot_sha256(promoted)
    atomic_json(output_path, promoted)
    result = {
        "action": "promote-product-subset",
        "status": "promotion_candidate_ready",
        "output": str(output_path),
        "generationId": promoted_generation["id"],
        "cards": len(cards),
        "backlog": expected_backlog,
        "passReceiptSha256": receipt_sha256,
        "contentSha256": promoted_generation["contentSha256"],
        "imagePolicy": image_policy,
        "productionEligible": True,
        "deployed": False,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result



def latest_psa10_pop(cur):
    """Return map variant_id -> latest PSA10 pop (>=0)."""
    cur.execute(
        """
        SELECT g.variant_id, g.top_grade_population, g.effective_at
        FROM market_grader_population_observation g
        INNER JOIN (
            SELECT variant_id, MAX(effective_at) AS mx
            FROM market_grader_population_observation
            WHERE grader_code = 'PSA'
            GROUP BY variant_id
        ) t ON t.variant_id = g.variant_id AND t.mx = g.effective_at
        WHERE g.grader_code = 'PSA'
        """
    )
    out = {}
    for row in cur.fetchall():
        try:
            pop = int(row["top_grade_population"]) if row["top_grade_population"] is not None else None
        except Exception:
            pop = None
        out[int(row["variant_id"])] = {
            "psa10Pop": pop,
            "asOf": str(row["effective_at"]) if row.get("effective_at") is not None else None,
        }
    return out


def cmd_scan_candidates(min_pop: int = 1000):
    """GemRate/DB PSA10 pop gate: auto candidate list + human attention file."""
    load_env()
    conn = db()
    try:
        cur = conn.cursor()
        ensure_freeze_table(cur)
        conn.commit()
        universe = current_universe(cur)
        member_ids = set(universe.get("variantIds") or [])
        pops = latest_psa10_pop(cur)
        qualified_ids = {
            vid for vid, info in pops.items()
            if isinstance(info.get("psa10Pop"), int) and info["psa10Pop"] >= min_pop
        }
        # new candidates: qualified but not in active universe
        new_ids = sorted(qualified_ids - member_ids)
        # in-universe incomplete freezes
        freezes = freeze_map(cur, sorted(member_ids))
        incomplete = []
        for vid in sorted(member_ids):
            kinds = {r["freeze_kind"] for r in freezes.get(vid, [])}
            if not {"identity", "source", "image"} <= kinds:
                incomplete.append(vid)

        meta = variant_meta(cur, new_ids + incomplete)
        candidates = []
        for vid in new_ids:
            m = meta.get(vid) or {}
            info = pops.get(vid) or {}
            candidates.append(
                {
                    "variantId": vid,
                    "opaqueId": m.get("opaque_id"),
                    "name": m.get("canonical_name"),
                    "set": m.get("set_name"),
                    "collector": m.get("collector_number"),
                    "tcg": m.get("tcg_code"),
                    "psa10Pop": info.get("psa10Pop"),
                    "popAsOf": info.get("asOf"),
                    "state": "candidate_new",
                    "action": "human_or_agent_bind_and_full_stock",
                }
            )
        attention = []
        for vid in incomplete:
            m = meta.get(vid) or {}
            kinds = {r["freeze_kind"] for r in freezes.get(vid, [])}
            attention.append(
                {
                    "variantId": vid,
                    "opaqueId": m.get("opaque_id"),
                    "name": m.get("canonical_name"),
                    "missingFreeze": sorted({"identity", "source", "image"} - kinds),
                    "state": "in_universe_incomplete",
                    "action": "finish_freeze_or_fill_gaps",
                }
            )

        OUT_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "asOf": utc_now(),
            "minPop": min_pop,
            "rules": {
                "gate": "gemrate_psa10_pop",
                "popOnlyIncreases": True,
                "autoCandidateOnCross": True,
                "humanRequiredToFinish": True,
            },
            "counts": {
                "qualifiedInDb": len(qualified_ids),
                "activeUniverse": len(member_ids),
                "newCandidates": len(candidates),
                "inUniverseIncomplete": len(attention),
            },
            "newCandidates": candidates,
            "inUniverseIncomplete": attention,
        }
        path = OUT_DIR / "candidates.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(json.dumps({"action": "scan-candidates", "path": str(path), **payload["counts"]}, ensure_ascii=False, indent=2))
        return payload
    finally:
        conn.close()


def _cmd_daily_unlocked(
    *,
    do_pass: bool = False,
    refresh: bool = False,
    refresh_report_path: Path | None = None,
):
    """Daily operator loop: full active refresh, then candidates, gaps and pass."""
    load_env()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    refresh_report: dict[str, Any] = {
        "enabled": refresh or refresh_report_path is not None,
        "ok": not refresh and refresh_report_path is None,
        "results": [],
    }
    if refresh_report_path is not None:
        refresh_report = _authorized_refresh_report(refresh_report_path)

    migration = None
    # A hash-bound completed refresh receipt is the presentation-only replay
    # lane.  Re-running migrations and db-tidy here turns a pass regeneration
    # into the whole 026 writer runtime again and can overwrite the already
    # committed acceptance generation.  A live --refresh remains the sole
    # migration/tidy owner; the reused-receipt lane is guarded below by the
    # current 762-card status and gap checks before export.
    if refresh:
        import subprocess

        py = sys.executable
        migration = subprocess.run(
            [
                py,
                "-X",
                "utf8",
                str(ROOT / "pipelines" / "db_runtime.py"),
                "migrate",
                "--only",
                "031_active_exact_identity_market_repair.mysql.sql",
            ],
            cwd=str(ROOT),
            timeout=900,
        )
        if migration.returncode != 0:
            refresh_report = {
                "enabled": True,
                "ok": False,
                "results": [],
                "migration": {
                    "exit": migration.returncode,
                    "output": "streamed-to-single-operator-stdout",
                },
                "error": "023-030 projection migration failed before canonicalization",
            }
            print(json.dumps(refresh_report, ensure_ascii=False, indent=2))
            return 1

    source_identity_preparation: dict[str, Any] | None = None
    if refresh:
        prepared = subprocess.run(
            [
                py,
                "-X",
                "utf8",
                str(ROOT / "pipelines" / "new_era_db_tidy.py"),
                "--prepare-source-identities",
            ],
            cwd=str(ROOT),
            timeout=300,
        )
        source_identity_preparation = {
            "exit": prepared.returncode,
            "output": "streamed-to-single-operator-stdout",
        }
        if prepared.returncode != 0:
            refresh_report = {
                "enabled": True,
                "ok": False,
                "results": [],
                "migration": {
                    "exit": migration.returncode,
                    "output": "streamed-to-single-operator-stdout",
                },
                "sourceIdentityPreparation": source_identity_preparation,
                "error": "026 exact source-owner preparation failed before collection",
            }
            print(json.dumps(refresh_report, ensure_ascii=False, indent=2))
            return 1

        binding_manifest = ROOT / "data" / "editorial" / "snk-en-exact-bindings-026.json"
        bound = subprocess.run(
            [
                py,
                "-X",
                "utf8",
                str(ROOT / "pipelines" / "apply_verified_source_bindings.py"),
                "--manifest",
                str(binding_manifest),
            ],
            cwd=str(ROOT),
            timeout=300,
        )
        source_identity_preparation["snkEnExactBindingsExit"] = bound.returncode
        if bound.returncode != 0:
            refresh_report = {
                "enabled": True,
                "ok": False,
                "results": [],
                "sourceIdentityPreparation": source_identity_preparation,
                "error": "026 SNK EN exact bindings failed before collection",
            }
            print(json.dumps(refresh_report, ensure_ascii=False, indent=2))
            return 1

        collect = ROOT / "pipelines" / "collect_control.py"
        command = [
            py,
            "-X",
            "utf8",
            str(collect),
            "incr",
            "--adapter",
            "all",
            "--ensure-browser",
            "--pc-sleep",
            "4.0",
            "--delay",
            "0.0",
            "--workers",
            "24",
        ]
        started = datetime.now(timezone.utc)
        runner: dict[str, Any]
        try:
            result = subprocess.run(
                command,
                cwd=str(ROOT),
                timeout=21600,
            )
            runner = {
                "exit": result.returncode,
                "output": "streamed-to-single-operator-stdout",
            }
        except Exception as exc:  # noqa: BLE001
            runner = {"exit": 1, "error": f"{type(exc).__name__}:{exc}"}
        collect_report_path = OUT_DIR / "collect" / "last_incr.json"
        try:
            collected = _read_json_object(collect_report_path, "incremental refresh report")
            collected_at = datetime.fromisoformat(
                str(collected.get("asOf") or "").replace("Z", "+00:00")
            )
            if collected_at < started:
                raise RuntimeError("incremental refresh report is stale")
            refresh_report = {
                **collected,
                "enabled": True,
                "runner": runner,
                "sourceIdentityPreparation": source_identity_preparation,
            }
            refresh_report["ok"] = bool(collected.get("ok")) and runner.get("exit") == 0
        except Exception as exc:  # noqa: BLE001
            refresh_report = {
                "enabled": True,
                "ok": False,
                "results": [],
                "runner": runner,
                "error": f"refresh_receipt:{type(exc).__name__}:{exc}",
            }

    if refresh and refresh_report.get("ok") is True:
        tidy = subprocess.run(
            [
                py,
                "-X",
                "utf8",
                str(ROOT / "pipelines" / "new_era_db_tidy.py"),
            ],
            cwd=str(ROOT),
            timeout=3600,
        )
        refresh_report["migration"] = {
            "exit": migration.returncode,
            "output": "streamed-to-single-operator-stdout",
        }
        refresh_report["dbTidy026"] = {
            "exit": tidy.returncode,
            "output": "streamed-to-single-operator-stdout",
        }
        refresh_report["ok"] = tidy.returncode == 0
        if tidy.returncode != 0:
            refresh_report["error"] = "026 canonical acceptance/materialization failed"

    status = cmd_status()
    candidates = cmd_scan_candidates(min_pop=1000)
    gaps = cmd_export_gaps()

    export_result = None
    if do_pass:
        pass_failures = []
        counts = (status or {}).get("counts") or {}
        if refresh_report.get("enabled") is not True or refresh_report.get("ok") is not True:
            pass_failures.append("full active refresh failed")
        if int(counts.get("members") or 0) != int(counts.get("productSubsetReady") or -1):
            pass_failures.append("not every active member is product-ready")
        if int(counts.get("gapCards", -1)) != 0 or int((gaps or {}).get("count", -1)) != 0:
            pass_failures.append("active gaps remain")
        if pass_failures:
            summary = {
                "action": "daily-pass",
                "asOf": utc_now(),
                "status": "failed",
                "errors": pass_failures,
                "statusCounts": counts,
                "candidateCounts": (candidates or {}).get("counts"),
                "gapCount": (gaps or {}).get("count"),
                "refresh": refresh_report,
            }
            print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
            return 1
        export_result = cmd_export_product_subset()
        if int(export_result.get("cards") or 0) != int(counts.get("members") or -1):
            raise RuntimeError("product export does not contain the full active universe")
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        receipt = {
            "asOf": utc_now(),
            "action": "pass",
            "productExport": export_result,
            "imagePolicy": export_result["imagePolicy"],
            "universe": (status or {}).get("universe"),
            "refresh": refresh_report,
            "promote": {
                "status": "awaiting_daddy_promote",
                "snapshotPath": str(OUT_DIR / "product-subset-snapshot.json"),
                "instructions": [
                    "Preview with CARDZ_DATA_MODE=product + MARKET_DATA_SNAPSHOT_PATH",
                    "Run promote-product-subset to create production candidate bytes",
                    "After DADDY visual approval, bake the exact candidate into the clean release",
                ],
            },
            "statusCounts": (status or {}).get("counts"),
            "candidateCounts": (candidates or {}).get("counts"),
            "gapCount": (gaps or {}).get("count"),
        }
        atomic_json(OUT_DIR / "pass_receipt.json", receipt)
        print(json.dumps({"action": "daily-pass", "receipt": str(OUT_DIR / "pass_receipt.json")}, ensure_ascii=False, indent=2))
    else:
        summary = {
            "action": "daily",
            "asOf": utc_now(),
            "statusCounts": (status or {}).get("counts"),
            "candidateCounts": (candidates or {}).get("counts"),
            "gapCount": (gaps or {}).get("count"),
            "refresh": refresh_report,
            "next": "Fix gaps/candidates, then rerun with --pass after DADDY approval to export product subset",
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return 0 if refresh_report.get("ok") is not False else 1






def cmd_daily(
    *,
    do_pass: bool = False,
    refresh: bool = False,
    refresh_report_path: Path | None = None,
):
    with operator_e2e_lease("daily"):
        return _cmd_daily_unlocked(
            do_pass=do_pass,
            refresh=refresh,
            refresh_report_path=refresh_report_path,
        )


def cmd_db_tidy(*, snk_history_archive_dir: Path | None, project_ingested_history: bool):
    """Ingest local history when requested, then rebuild canonical projections."""
    import subprocess
    load_env()
    if not project_ingested_history and (
        snk_history_archive_dir is None or not snk_history_archive_dir.is_dir()
    ):
        raise RuntimeError(f"SNK history archive directory missing: {snk_history_archive_dir}")
    py = sys.executable
    commands = [[
        py,
        "-X",
        "utf8",
        str(ROOT / "pipelines" / "db_runtime.py"),
        "migrate",
        "--only",
        "032_pricecharting_local_history_merge.mysql.sql",
    ]]
    if not project_ingested_history:
        commands.append([
            py,
            "-X",
            "utf8",
            str(ROOT / "pipelines" / "snk_market_data.py"),
            "--ingest-archive-dir",
            str(snk_history_archive_dir),
        ])
    commands.append([py, "-X", "utf8", str(ROOT / "pipelines" / "new_era_db_tidy.py")])
    with operator_e2e_lease("db-tidy"):
        for index, command in enumerate(commands, start=1):
            print(
                json.dumps(
                    {
                        "phase": "db-tidy-child-start",
                        "step": index,
                        "steps": len(commands),
                        "entrypoint": Path(command[3]).name,
                        "at": utc_now(),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            result = subprocess.run(command, cwd=str(ROOT))
            print(
                json.dumps(
                    {
                        "phase": "db-tidy-child-finish",
                        "step": index,
                        "returncode": result.returncode,
                        "at": utc_now(),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            if result.returncode != 0:
                raise SystemExit(result.returncode)
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="CARDZ operator dual-mode control plane")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    p_gaps = sub.add_parser("export-gaps")
    p_gaps.add_argument("--limit", type=int)
    p_accept = sub.add_parser("accept-binding")
    p_accept.add_argument("--variant-id", type=int, required=True)
    p_accept.add_argument("--kind", choices=FREEZE_KINDS, required=True)
    p_accept.add_argument("--source-code", default="")
    p_accept.add_argument("--actor", default="daddy")
    p_accept.add_argument("--note", default=None)
    p_op = sub.add_parser("export-operator-snapshot")
    p_op.add_argument("--output", type=Path)
    p_prod = sub.add_parser("export-product-subset")
    p_prod.add_argument("--output", type=Path)
    sub.add_parser(
        "snk-image-priority-status",
        help="read-only 026 cohort-wide canonical SNK EN image status",
    )
    p_promote = sub.add_parser(
        "promote-product-subset",
        help="bind a product snapshot to its pass receipt; never deploy",
    )
    p_promote.add_argument(
        "--snapshot",
        type=Path,
        default=OUT_DIR / "product-subset-snapshot.json",
    )
    p_promote.add_argument(
        "--receipt",
        type=Path,
        default=OUT_DIR / "pass_receipt.json",
    )
    p_promote.add_argument("--output", type=Path, default=PROMOTED_PRODUCT_SNAPSHOT)
    p_promote.add_argument("--expected-cards", type=int, default=TONIGHT_CARD_COUNT)
    p_promote.add_argument("--expected-backlog", type=int, default=TONIGHT_BACKLOG_COUNT)
    p_freeze_launch = sub.add_parser(
        "freeze-active-sources-from-checkpoints",
        help="one-time launch reconciliation for missing active source freezes",
    )
    p_freeze_launch.add_argument("--actor", required=True)
    p_freeze_launch.add_argument("--authorization-note", required=True)
    p_cand = sub.add_parser("scan-candidates", help="PSA10 pop>=1000 candidate scan + human attention")
    p_cand.add_argument("--min-pop", type=int, default=1000)
    p_daily = sub.add_parser("daily", help="daily candidates+gaps; --pass exports product subset receipt")
    p_tidy = sub.add_parser("db-tidy", help="replay exact local market history + rebuild current projections")
    p_tidy.add_argument(
        "--snk-history-archive-dir",
        type=Path,
        help="directory containing complete snk[_price]_harvest*.jsonl source batches",
    )
    p_tidy.add_argument(
        "--project-ingested-history",
        action="store_true",
        help="rebuild from a successfully ingested local archive without replaying it",
    )
    p_rebuild = sub.add_parser(
        "rebuild-036",
        help="036 identity-first rebuild orchestrator (S0..S11; activation is a separate subcommand)",
    )
    p_rebuild.add_argument("--generation", required=True, help="036_<UTC>, e.g. 036_20260808T120000Z")
    p_rebuild.add_argument("--resume", action="store_true", help="explicit alias; linear runs always resume from checkpoints")
    p_rebuild.add_argument("--stage", help="run exactly one stage (post-activation stages require this)")
    p_rebuild.add_argument("--invalidate-from", dest="invalidate_from", help="reset this stage and all downstream to pending, then exit")
    p_rebuild.add_argument("--force-stage", dest="force_stage", action="store_true", help="required to rerun a failed or complete stage")
    p_rebuild.add_argument("--dry-run", dest="dry_run", action="store_true")
    p_rebuild.add_argument("--credentials-env", dest="credentials_env", type=Path, help="default data/runtime/config/rebuild.env")
    p_rebuild.add_argument("--freshness-hours", dest="freshness_hours", type=float, default=72.0)
    p_activate = sub.add_parser(
        "rebuild-036-activate",
        help="S12: switch current generation; refuses unless the recomputed receipt sha matches",
    )
    p_activate.add_argument("--generation", required=True)
    p_activate.add_argument(
        "--receipt-sha256", dest="receipt_sha256",
        help="optional; default is the generation's latest passed receipt, which"
             " activate revalidates in-process either way",
    )
    p_activate.add_argument("--credentials-env", dest="credentials_env", type=Path)
    p_e2e = sub.add_parser(
        "rebuild-036-e2e",
        help="scheduler off -> freeze -> S0..S11 -> activate -> S13/S14 -> unfreeze"
             " -> scheduler back -> bake, with teardown on any failure",
    )
    p_e2e.add_argument("--generation", required=True)
    p_e2e.add_argument("--invalidate-from", dest="invalidate_from", help="reset this stage and downstream first")
    p_e2e.add_argument("--credentials-env", dest="credentials_env", type=Path)
    p_e2e.add_argument("--freshness-hours", dest="freshness_hours", type=float, default=72.0)
    p_e2e.add_argument("--skip-bake", dest="skip_bake", action="store_true")
    p_freeze = sub.add_parser(
        "rebuild-036-freeze",
        help="setup: revoke cardz DML, create cardz_rebuild, prove the freeze (1142) before returning",
    )
    p_freeze.add_argument("--credentials-env", dest="credentials_env", type=Path)
    p_unfreeze = sub.add_parser(
        "rebuild-036-unfreeze",
        help="teardown: restore cardz DML grant, drop cardz_rebuild (success AND failure paths)",
    )
    p_unfreeze.add_argument("--confirm", action="store_true")
    p_daily_accept = sub.add_parser(
        "daily-accept",
        help="nightly: accept fresh evidence + re-rank the current universe (no lock rewrite)",
    )
    p_daily_accept.add_argument("--credentials-env", dest="credentials_env", type=Path)
    p_pc_reverify = sub.add_parser(
        "pc-identity-reverify",
        help="promote manual_review PC bindings the fresh full900 pages can prove (dry-run without --write)",
    )
    p_pc_reverify.add_argument("--write", action="store_true")
    p_pc_reverify.add_argument("--pages-dir", dest="pages_dir", type=Path)
    p_snk_reverify = sub.add_parser(
        "snk-identity-reverify",
        help="promote manual_review SNK bindings a fresh master fetch can prove (dry-run without --write)",
    )
    p_snk_reverify.add_argument("--write", action="store_true")
    p_snk_reverify.add_argument("--credentials-env", dest="credentials_env", type=Path)
    p_snk_discover = sub.add_parser(
        "snk-identity-discover",
        help="propose a first SNK binding for qualified variants that have no price source at all (dry-run without --write)",
    )
    import snk_identity_discover as _snk_discover_mod

    _snk_discover_mod.add_arguments(p_snk_discover)
    p_pc_reverify.add_argument("--map", type=Path)
    p_pc_reverify.add_argument("--credentials-env", dest="credentials_env", type=Path)
    p_daily.add_argument("--pass", dest="do_pass", action="store_true", help="DADDY pass: export product subset + promote receipt")
    refresh_group = p_daily.add_mutually_exclusive_group()
    refresh_group.add_argument("--refresh", action="store_true", help="run collect_control status+incr (real exact-id harvest, not --help)")
    refresh_group.add_argument(
        "--refresh-report",
        type=Path,
        help="reuse one already-completed six-adapter refresh receipt; performs no network work",
    )
    args = parser.parse_args()
    if args.cmd == "status":
        cmd_status()
    elif args.cmd == "export-gaps":
        cmd_export_gaps(limit=args.limit)
    elif args.cmd == "accept-binding":
        accept_binding(
            variant_id=args.variant_id,
            freeze_kind=args.kind,
            source_code=args.source_code,
            actor=args.actor,
            note=args.note,
        )
    elif args.cmd == "export-operator-snapshot":
        cmd_export_operator_snapshot(output=args.output)
    elif args.cmd == "export-product-subset":
        cmd_export_product_subset(output=args.output)
    elif args.cmd == "snk-image-priority-status":
        cmd_snk_image_priority_status()
    elif args.cmd == "promote-product-subset":
        cmd_promote_product_subset(
            snapshot_path=args.snapshot,
            receipt_path=args.receipt,
            output_path=args.output,
            expected_cards=args.expected_cards,
            expected_backlog=args.expected_backlog,
        )
    elif args.cmd == "freeze-active-sources-from-checkpoints":
        cmd_freeze_active_sources_from_checkpoints(
            actor=args.actor,
            authorization_note=args.authorization_note,
        )
    elif args.cmd == "scan-candidates":
        cmd_scan_candidates(min_pop=args.min_pop)
    elif args.cmd == "daily":
        return cmd_daily(
            do_pass=args.do_pass,
            refresh=args.refresh,
            refresh_report_path=args.refresh_report,
        )
    elif args.cmd == "db-tidy":
        cmd_db_tidy(
            snk_history_archive_dir=args.snk_history_archive_dir,
            project_ingested_history=args.project_ingested_history,
        )
    elif args.cmd == "rebuild-036":
        import rebuild_036

        return rebuild_036.cmd_rebuild(args)
    elif args.cmd == "rebuild-036-activate":
        import rebuild_036

        return rebuild_036.cmd_activate(args)
    elif args.cmd == "rebuild-036-e2e":
        import rebuild_036

        return rebuild_036.cmd_e2e(args)
    elif args.cmd == "rebuild-036-freeze":
        import rebuild_036

        return rebuild_036.cmd_freeze(args)
    elif args.cmd == "rebuild-036-unfreeze":
        import rebuild_036

        return rebuild_036.cmd_unfreeze(args)
    elif args.cmd == "daily-accept":
        import rebuild_036

        return rebuild_036.cmd_daily_accept(args)
    elif args.cmd == "pc-identity-reverify":
        import rebuild_036

        return rebuild_036.cmd_pc_identity_reverify(args)
    elif args.cmd == "snk-identity-reverify":
        import rebuild_036

        return rebuild_036.cmd_snk_identity_reverify(args)
    elif args.cmd == "snk-identity-discover":
        import snk_identity_discover

        return snk_identity_discover.cmd_snk_identity_discover(args)
    else:
        raise SystemExit(f"unknown command: {args.cmd}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
