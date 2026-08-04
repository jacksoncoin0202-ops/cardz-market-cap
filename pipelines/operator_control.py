#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CARDZ operator control plane: agent-first daily path."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import statistics

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))
from qualified_pool_operator import db, load_env  # noqa: E402
import operator_fe_export as fe  # noqa: E402
from release_frontend_bundle import (  # noqa: E402
    FRONTEND_POLICY_ID,
    bundle_manifest,
)

EXACT_PRICE_SOURCES = ("snk_psa10", "snk", "snkrdunk", "ebay", "pricecharting", "tcgpricelookup")
BANNED_PRICE_SOURCES = ("g10_kline",)
FREEZE_KINDS = ("identity", "source", "image")
OUT_DIR = ROOT / "data" / "runtime" / "operator"
PROMOTED_PRODUCT_SNAPSHOT = OUT_DIR / "promoted-product-snapshot.json"
RELAXED_RELEASE_PROFILE = "relaxed-launch-v1"
RELAXED_POLICY_SHA256 = "5c7c7aa96f5669379d3436ce2d3c04c2dfe1972a5feba70c319ec91e066f4125"
TONIGHT_CARD_COUNT = 762
TONIGHT_BACKLOG_COUNT = 776
COLLECT_REGISTRY_PATH = OUT_DIR / "collect" / "collect_registry.jsonl"
FORMAL_REFRESH_RECEIPT = OUT_DIR / "collect" / "formal_daily_refresh_20260804T110637Z.json"
TARGETED_REFRESH_RECEIPT = OUT_DIR / "collect" / "last_incr.json"
SNK_RESUME_RECEIPT = OUT_DIR / "collect" / "snk_price_checkpoint_resume.json"
SNK_BINDING_DELTA_RECEIPT = OUT_DIR / "collect" / "binding_delta_snk_refresh.json"
CHECKPOINT_ADAPTERS = (
    "gemrate_pop",
    "snk_trades",
    "snk_price",
    "pc_ebay_sales",
    "en_price_ref",
)
CHECKPOINT_SLA_HOURS = 36
TOP100_SNK_IMAGE_POLICY_ID = "displayed-top100-snk-public-exact-first-v1"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def utc_now_sql() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonical_snapshot_sha256(snapshot: dict[str, Any]) -> str:
    normalized = json.loads(json.dumps(snapshot, ensure_ascii=False, default=str))
    normalized["generation"]["contentSha256"] = ""
    return hashlib.sha256(
        json.dumps(
            normalized,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.next")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    os.replace(temporary, path)



def derive_set_code(
    *,
    tcg: str | None,
    collector: str | None,
    set_name: str | None,
    edition_code: str | None = None,
    card_language: str | None = None,
) -> str | None:
    """Badge setCode from collector prefix / set text. Never use parallel/rarity."""
    import re

    c = str(collector or "").strip().upper()
    text = " ".join(x for x in (set_name or "", edition_code or "", c) if x)
    # One Piece / structured collector: OP06-118, ST10-010
    m = re.match(r"^((?:OP|ST|EB|PRB)\d{1,2})-", c)
    if m:
        return m.group(1)
    # Gallery / shiny-vault style codes printed as collector
    if re.match(r"^(?:GG|TG|SV)\d+", c):
        m = re.match(r"^([A-Z]{1,4}\d{1,3})", c)
        if m:
            # SV107 is shiny vault number, not set code; map shining fates separately
            if m.group(1).startswith("SV") and len(m.group(1)) > 3:
                pass
            else:
                return m.group(1)
    # Explicit set tokens: SV2a, SWSH09, S12a, OP13
    for m in re.finditer(
        r"\b((?:SV|SWSH|SM|XY|BW|OP|ST|EB|PRB|S|M)\d{1,2}[A-Za-z]?)\b",
        text,
        re.I,
    ):
        tok = m.group(1).upper()
        m3 = re.match(r"^(OP|ST|EB|PRB)(\d{1,2})$", tok)
        if m3:
            return f"{m3.group(1)}{int(m3.group(2)):02d}"
        return tok

    low = text.lower()
    lang = str(card_language or "").lower()
    if re.search(r"card\s*151|\b151\b|sv2a|\bmew\b", low):
        if lang.startswith("ja") or re.search(r"japanese|sv2a", low):
            return "SV2A"
        return "MEW"

    rules = [
        (r"brilliant\s*stars", "BRS"),
        (r"evolving\s*skies", "EVS"),
        (r"crown\s*zenith", "CRZ"),
        (r"journey\s*together", "JTG"),
        (r"destined\s*rivals", "DRI"),
        (r"black\s*bolt", "BLK"),
        (r"white\s*flare", "WHT"),
        (r"stellar\s*crown", "SCR"),
        (r"prismatic\s*evolutions", "PRE"),
        (r"terastal\s*fest", "SV8A"),
        (r"paldea[n]?\s*evolved", "PAL"),
        (r"obsidian\s*flames", "OBF"),
        (r"temporal\s*forces", "TEF"),
        (r"twilight\s*masquerade", "TWM"),
        (r"shrouded\s*fable", "SFA"),
        (r"surging\s*sparks", "SSP"),
        (r"paradox\s*rift", "PAR"),
        (r"paldean\s*fates", "PAF"),
        (r"silver\s*tempest", "SIT"),
        (r"lost\s*origin", "LOR"),
        (r"astral\s*radiance", "ASR"),
        (r"chilling\s*reign", "CRE"),
        (r"fusion\s*strike", "FST"),
        (r"celebrations", "CEL"),
        (r"pokemon\s*go", "PGO"),
        (r"dark\s*explorers", "DEX"),
        (r"vivid\s*voltage", "VIV"),
        (r"shining\s*fates", "SHF"),
        (r"champion'?s\s*path", "CPA"),
        (r"phantasmal\s*flames|inferno\s*x", "PFL"),
        (r"\bmega\s*evolution\b", "MEG"),
        (r"paramount war", "OP02"),
        (r"pillars of strength", "OP03"),
        (r"kingdoms of intrigue", "OP04"),
        (r"awakening of the new era", "OP05"),
        (r"wings of the captain", "OP06"),
        (r"500 years in the future", "OP07"),
        (r"two legends", "OP08"),
        (r"emperors in the new world", "OP09"),
        (r"royal blood", "OP10"),
        (r"a fist of divine speed", "OP11"),
        (r"carrying on his will", "OP13"),
        (r"azure sea", "OP14"),
        (r"romance dawn", "OP01"),
    ]
    for pat, code in rules:
        if re.search(pat, low):
            return code
    return None


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
        """,
        tuple(variant_ids),
    )
    out = {}
    for row in cur.fetchall():
        out.setdefault(int(row["variant_id"]), []).append(row)
    return out


def _is_snk_source(source_code: str) -> bool:
    return str(source_code or "") in {"snk_psa10", "snk", "snkrdunk"}


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
    order = {s: i for i, s in enumerate(EXACT_PRICE_SOURCES)}

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
        f"SELECT id, card_language FROM catalog_variant WHERE id IN ({ph})",
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
            if str(r.get("match_status") or "") in {"exact", "confirmed", "attached"}
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
        ids = identities_by_variant(cur, vids)
        prices = latest_prices(cur, vids)
        images = image_rows(cur, vids)
        meta = variant_meta(cur, vids)
        gaps = classify_gaps(universe.get("members") or [], meta, ids, prices, images, freezes)
        frozen_identity = sum(1 for rows in freezes.values() if any(r["freeze_kind"] == "identity" for r in rows))
        frozen_source = sum(1 for rows in freezes.values() if any(r["freeze_kind"] == "source" for r in rows))
        frozen_image = sum(1 for rows in freezes.values() if any(r["freeze_kind"] == "image" for r in rows))
        product_ready = 0
        for vid in vids:
            kinds = {r["freeze_kind"] for r in freezes.get(vid, [])}
            if {"identity", "source", "image"} <= kinds and vid in prices and vid in images:
                product_ready += 1
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
                "withExactSource": sum(1 for vid in vids if ids.get(vid)),
                "withPrice": len(prices),
                "withImage": len(images),
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
        freezes = freeze_map(cur, vids)
        ids = identities_by_variant(cur, vids)
        prices = latest_prices(cur, vids)
        images = image_rows(cur, vids)
        meta = variant_meta(cur, vids)
        gaps = classify_gaps(universe.get("members") or [], meta, ids, prices, images, freezes)
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


def _metric(value, status, as_of):
    return {"value": value, "status": status, "asOf": as_of}


def _localized(text):
    base = text or "unknown"
    return {"en": base, "zhTW": None, "zhCN": None, "ja": None}


def _window_blank():
    blank = _metric(None, "unavailable", None)
    return {
        "changePct": blank,
        "marketCapChangePct": blank,
        "trackedSalesChangePct": blank,
        "trackedSales": {"valueUsd": blank, "count": blank, "coverage": "unavailable", "asOf": None},
    }


def _grader_blank():
    blank = _metric(None, "unavailable", None)
    return {
        "topGrade": "10",
        "total": {**blank, "estimated": False},
        "topGradePopulation": {**blank, "estimated": False},
        "topGradePopulationChangePct": {"1d": blank, "7d": blank, "30d": blank},
    }


def build_operator_cards(cur, members, product_subset: bool):
    vids = [int(m["variant_id"]) for m in members]
    meta = variant_meta(cur, vids)
    ids = identities_by_variant(cur, vids)
    prices = latest_prices(cur, vids)
    freezes = freeze_map(cur, vids)
    bulk = fe.load_bulk(cur, vids)

    # richer latest price with observed_date
    # SNK-first: latest per source, then source priority (never let newer eBay beat SNK)
    latest_price_full = latest_prices(cur, vids)

    latest_pop_full = {}
    for vid, by_g in bulk["pop_rows"].items():
        series = by_g.get("PSA") or []
        if series:
            latest_pop_full[vid] = series[-1]

    def _current_market_cap(variant_id: int) -> float | None:
        price = latest_price_full.get(variant_id) or prices.get(variant_id)
        pop = latest_pop_full.get(variant_id)
        if not price or price.get("price_usd") is None:
            return None
        if not pop or pop.get("top_grade_population") is None:
            return None
        return float(price["price_usd"]) * int(pop["top_grade_population"])

    ranked_vids = sorted(
        vids,
        key=lambda variant_id: (
            -(
                _current_market_cap(variant_id)
                if _current_market_cap(variant_id) is not None
                else -1.0
            ),
            str((meta.get(variant_id) or {}).get("opaque_id") or f"variant_{variant_id}"),
        ),
    )
    top100_vids = set(ranked_vids[:100])
    images = image_rows(
        cur,
        vids,
        prefer_snk_ids=top100_vids if product_subset else (),
    )

    cards = []
    for member in members:
        vid = int(member["variant_id"])
        m = meta.get(vid) or {}
        freeze_rows = freezes.get(vid) or []
        frozen_kinds = {str(r["freeze_kind"]) for r in freeze_rows}
        exact_sources = {
            str(r["source_code"])
            for r in (ids.get(vid) or [])
            if str(r.get("match_status") or "") in {"exact", "confirmed", "attached"}
        }
        price = latest_price_full.get(vid) or prices.get(vid)
        has_price = price is not None and price.get("price_usd") is not None
        img = images.get(vid)
        has_image = bool(img)
        if product_subset:
            if not ({"identity", "source", "image"} <= frozen_kinds and has_price and has_image):
                continue

        price_usd = float(price["price_usd"]) if has_price else None
        price_at = fe.asof_iso(price.get("effective_at")) if price else None
        pop = latest_pop_full.get(vid)
        pop_val = int(pop["top_grade_population"]) if pop and pop.get("top_grade_population") is not None else None
        pop_at = fe.asof_iso((pop or {}).get("effective_at") or (pop or {}).get("observed_date")) if pop else None
        market_cap = price_usd * pop_val if price_usd is not None and pop_val is not None else None

        history, windows, grader_pops = fe.build_history_windows_graders(vid, bulk, price, pop)
        if pop_val is not None:
            grader_pops["PSA"]["topGradePopulation"] = {**fe.metric(pop_val, "ready", pop_at), "estimated": False}

        opaque = str(m.get("opaque_id") or f"variant_{vid}")
        image_sha = str((img or {}).get("content_sha256") or ("0" * 64))
        image_src = f"/market-assets/{image_sha}.webp" if has_image else "/card-placeholder.svg"
        rank = int(member.get("market_rank") or 0) or (len(cards) + 1)
        name = str(m.get("canonical_name") or opaque)
        set_name = str(m.get("set_name") or "unknown")
        collector = str(m.get("collector_number") or "unknown")
        tcg_raw = str(m.get("tcg_code") or "other").lower()
        if "poke" in tcg_raw:
            tcg = "pokemon"
        elif "one" in tcg_raw or tcg_raw in {"op", "one_piece"}:
            tcg = "one-piece"
        else:
            tcg = "other"
        lang = m.get("card_language")
        if lang not in {"en", "ja", "ko", "zhCN", "zhTW"}:
            lang = None

        loc = bulk["locales"].get(vid) or {}
        names = {
            "en": (loc.get("en") or {}).get("localized_name") or name,
            "zhTW": (loc.get("zhTW") or loc.get("zh-TW") or {}).get("localized_name"),
            "zhCN": (loc.get("zhCN") or loc.get("zh-CN") or {}).get("localized_name"),
            "ja": (loc.get("ja") or {}).get("localized_name"),
            "ko": (loc.get("ko") or {}).get("localized_name"),
        }
        sets = {
            "en": (loc.get("en") or {}).get("localized_set_name") or set_name,
            "zhTW": (loc.get("zhTW") or loc.get("zh-TW") or {}).get("localized_set_name"),
            "zhCN": (loc.get("zhCN") or loc.get("zh-CN") or {}).get("localized_set_name"),
            "ja": (loc.get("ja") or {}).get("localized_set_name"),
            "ko": (loc.get("ko") or {}).get("localized_set_name"),
        }
        # Calm default market note — no hype, same meaning across locales.
        # Prefer DB locale story when present; otherwise keep a short neutral template.
        def _story(loc_code: str, template: str):
            row = loc.get(loc_code) or loc.get(loc_code.replace("zhTW", "zh-TW").replace("zhCN", "zh-CN")) or {}
            val = row.get("market_story")
            if isinstance(val, str) and val.strip():
                return val.strip()
            return template

        stories = {
            "en": _story("en", f"{name} is tracked on CARDZ for PSA 10 price, population and market cap."),
            "zhTW": _story("zhTW", f"{name} 於 CARDZ 追蹤 PSA 10 價格、數量與市值。"),
            "zhCN": _story("zhCN", f"{name} 在 CARDZ 追踪 PSA 10 价格、数量与市值。"),
            "ja": _story("ja", f"{name} は CARDZ で PSA 10 価格・枚数・時価総額を追跡しています。"),
            "ko": _story("ko", f"{name} 는 CARDZ 에서 PSA 10 가격·매수·시가총액을 추적합니다."),
        }

        pr = bulk["printing"].get(vid)
        printing_identity = None
        if pr:
            printing_identity = {
                "setName": str(pr.get("set_name") or set_name),
                "collectorNumber": str(pr.get("collector_number") or collector),
                "editionCode": str(pr.get("edition_code") or ""),
                "parallelCode": str(pr.get("parallel_code") or ""),
                "finishCode": str(pr.get("finish_code") or ""),
                "cardLanguage": pr.get("card_language") if pr.get("card_language") in {"en", "ja", "ko", "zhCN", "zhTW"} else lang,
                "canonicalPrintingSha256": str(pr.get("canonical_printing_sha256") or ("0" * 64)),
                "evidenceSha256": str(pr.get("evidence_sha256") or ("0" * 64)),
                "setCode": derive_set_code(
                    tcg=str(pr.get("tcg_code") or tcg),
                    collector=str(pr.get("collector_number") or collector),
                    set_name=str(pr.get("set_name") or set_name),
                    edition_code=str(pr.get("edition_code") or ""),
                    card_language=str(pr.get("card_language") or lang or ""),
                ),
                # Never mirror parallel into rarity; hide until real rarity exists.
                "rarityCode": None,
                "printingCode": None,
            }

        un = bulk["ungraded"].get(vid)
        if un and un.get("price_usd") is not None:
            ungraded = fe.metric(float(un["price_usd"]), "ready", fe.asof_iso(un.get("observed_at")))
        else:
            ungraded = fe.metric(None, "unavailable", None)

        image_policy_applies = bool(product_subset and vid in top100_vids)
        snk_eligible = bool(
            image_policy_applies
            and (img or {}).get("snk_source")
            and (img or {}).get("snk_public_qc")
        )
        selected_source = "snkrdunk" if snk_eligible else "accepted-freeze"

        card = {
            "id": opaque,
            "rank": rank,
            "marketRank": rank,
            "viewRank": rank,
            "tcg": tcg,
            "cardLanguage": lang,
            "collectorNumber": {
                "display": collector,
                "normalized": collector.lower(),
                "complete": "/" in collector or collector not in {"", "unknown"},
            },
            "identityStatus": "confirmed" if "identity" in frozen_kinds else "provisional",
            "names": names,
            "sets": sets,
            "stories": stories,
            "image": {
                "src": image_src,
                "sha256": image_sha if has_image else ("0" * 64),
                "kind": "raw_front",
                "width": int((img or {}).get("width_px") or 429),
                "height": int((img or {}).get("height_px") or 600),
                "alt": {"en": name, "zhTW": None, "zhCN": None, "ja": None, "ko": None},
                "qcAt": utc_now(),
                "variants": {
                    "200": f"/market-assets/{image_sha}_200.webp",
                    "600": f"/market-assets/{image_sha}_600.webp",
                } if has_image else None,
            },
            "pricePsa10": fe.metric(price_usd, "ready" if price_usd is not None else "unavailable", price_at),
            "priceUngradedReference": ungraded,
            "populationPsa10": {**fe.metric(pop_val, "ready" if pop_val is not None else "unavailable", pop_at), "estimated": False},
            "marketCap": fe.metric(market_cap, "ready" if market_cap is not None else "unavailable", price_at or pop_at),
            "windows": windows,
            "graderPopulations": grader_pops,
            "historyDaily": history,
            "operator": {
                "variantId": vid,
                "frozenKinds": sorted(frozen_kinds),
                "exactSources": sorted(exact_sources),
                "hasPrice": has_price,
                "hasImage": has_image,
                "productEligible": {"identity", "source", "image"} <= frozen_kinds and has_price and has_image,
                "imagePolicy": {
                    "policyId": TOP100_SNK_IMAGE_POLICY_ID if image_policy_applies else "accepted-freeze-v1",
                    "applies": image_policy_applies,
                    "snkEligible": snk_eligible,
                    "selectedSource": selected_source,
                    "selectedSha256": image_sha,
                },
            },
        }
        if printing_identity:
            card["printingIdentity"] = printing_identity
        cards.append(card)

    cards.sort(
        key=lambda c: (
            -(c["marketCap"]["value"] if isinstance(c.get("marketCap"), dict) and c["marketCap"].get("value") is not None else -1),
            c["id"],
        )
    )
    for i, card in enumerate(cards, start=1):
        card["rank"] = i
        card["viewRank"] = i
        card["marketRank"] = i
    if product_subset:
        displayed_top100 = {
            int((card.get("operator") or {}).get("variantId"))
            for card in cards[:100]
        }
        if displayed_top100 != top100_vids:
            raise RuntimeError("displayed Top 100 does not match current market-cap ranking")
    # stash fx for write_snapshot via function attribute
    build_operator_cards._last_fx = bulk.get("fx") or {}
    return cards

def write_snapshot(cards, *, generation_prefix, mode, blockers, output: Path):
    now = utc_now()
    top = cards[:100]
    watch = cards[100:]
    generation_id = f"{generation_prefix}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    snapshot = {
        "schemaVersion": "2.0.0",
        "generation": {
            "id": generation_id,
            "generatedAt": now,
            "effectiveAt": now,
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
        "currencies": fe.currencies_block(now, getattr(build_operator_cards, "_last_fx", {})),
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
        ensure_freeze_table(cur)
        conn.commit()
        universe = current_universe(cur)
        cards = build_operator_cards(cur, universe.get("members") or [], product_subset=False)
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
    *,
    frontend_bundle: dict[str, Any] | None = None,
):
    load_env()
    conn = db()
    try:
        cur = conn.cursor()
        ensure_freeze_table(cur)
        conn.commit()
        universe = current_universe(cur)
        cards = build_operator_cards(cur, universe.get("members") or [], product_subset=True)
        top100_cards = cards[:100]
        image_decisions = []
        for card in top100_cards:
            operator_row = card.get("operator") or {}
            decision = operator_row.get("imagePolicy") or {}
            image_decisions.append(
                {
                    "marketRank": int(card.get("marketRank") or 0),
                    "variantId": int(operator_row.get("variantId") or 0),
                    "opaqueId": card.get("id"),
                    "snkEligible": decision.get("snkEligible") is True,
                    "selectedSource": decision.get("selectedSource"),
                    "selectedSha256": decision.get("selectedSha256"),
                }
            )
        image_policy = {
            "policyId": TOP100_SNK_IMAGE_POLICY_ID,
            "scope": "displayed-top100-by-current-market-cap",
            "top100Cards": len(image_decisions),
            "snkEligible": sum(row["snkEligible"] for row in image_decisions),
            "snkSelected": sum(
                row["snkEligible"] and row["selectedSource"] == "snkrdunk"
                for row in image_decisions
            ),
            "nonSnkOnlyWithoutEligible": sum(
                not row["snkEligible"] and row["selectedSource"] != "snkrdunk"
                for row in image_decisions
            ),
            "decisions": image_decisions,
        }
        if image_policy["top100Cards"] != 100:
            raise RuntimeError("SNK image policy requires exactly 100 displayed cards")
        if image_policy["snkEligible"] != image_policy["snkSelected"]:
            raise RuntimeError("an eligible displayed Top-100 SNK image was not selected")
        if image_policy["nonSnkOnlyWithoutEligible"] != 100 - image_policy["snkEligible"]:
            raise RuntimeError("a non-eligible card selected an unexpected SNK image")
        if frontend_bundle is None:
            frontend_bundle = bundle_manifest(ROOT)
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
        result["frontendBundle"] = frontend_bundle
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return result
    finally:
        conn.close()


def cmd_snk_image_priority_status() -> dict[str, Any]:
    """Report the displayed current-market-cap Top-100 SNK preference without writing."""

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
        top100_cards = cards[:100]
        variant_ids = [int((card.get("operator") or {})["variantId"]) for card in top100_cards]
        baseline = image_rows(cur, variant_ids)

        eligible = []
        switched = []
        already_selected = []
        for card in top100_cards:
            operator_row = card.get("operator") or {}
            decision = operator_row.get("imagePolicy") or {}
            if decision.get("snkEligible") is not True:
                continue
            variant_id = int(operator_row["variantId"])
            item = {
                "marketRank": int(card["marketRank"]),
                "variantId": variant_id,
                "opaqueId": card.get("id"),
                "currentSha256": str((baseline.get(variant_id) or {}).get("content_sha256") or ""),
                "snkSha256": str((card.get("image") or {}).get("sha256") or ""),
            }
            eligible.append(item)
            if item["currentSha256"] == item["snkSha256"]:
                already_selected.append(item)
            else:
                switched.append(item)

        result = {
            "action": "snk-image-priority-status",
            "policyId": TOP100_SNK_IMAGE_POLICY_ID,
            "scope": "displayed-top100-by-current-market-cap",
            "top100Cards": len(variant_ids),
            "snkEligible": len(eligible),
            "alreadySnkSelected": len(already_selected),
            "willSwitchToSnk": len(switched),
            "withoutEligibleSnk": len(variant_ids) - len(eligible),
            "switches": sorted(switched, key=lambda row: row["marketRank"]),
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
    """Reuse one completed five-adapter refresh without repeating network work."""

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
        failures.append("refresh report does not contain the exact five adapters")
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
              AND match_status IN ('exact','confirmed','attached')
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


def cmd_finalize_pass_from_checkpoints(
    *,
    formal_receipt_path: Path,
    targeted_receipt_path: Path,
    snk_resume_path: Path,
    snk_binding_delta_path: Path,
) -> dict[str, Any]:
    """Finalize tonight's pass without repeating the single formal network refresh."""
    frontend_bundle = bundle_manifest(ROOT)
    formal = _read_json_object(formal_receipt_path, "single formal refresh receipt")
    targeted = _read_json_object(targeted_receipt_path, "targeted PC/EN refresh receipt")
    snk_resume = _read_json_object(snk_resume_path, "SNK checkpoint resume receipt")
    snk_binding_delta = _read_json_object(
        snk_binding_delta_path,
        "post-binding SNK delta refresh receipt",
    )
    formal_results = {
        str(row.get("adapter")): row
        for row in (formal.get("results") or [])
        if isinstance(row, dict)
    }
    targeted_results = {
        str(row.get("adapter")): row
        for row in (targeted.get("results") or [])
        if isinstance(row, dict)
    }
    snk_delta_results = {
        str(row.get("adapter")): row
        for row in (snk_binding_delta.get("results") or [])
        if isinstance(row, dict)
    }
    if formal.get("requestedAdapters") != list(CHECKPOINT_ADAPTERS):
        raise RuntimeError("formal receipt is not the locked five-adapter run")
    if any((formal_results.get(adapter) or {}).get("ok") is not True for adapter in ("gemrate_pop", "snk_trades")):
        raise RuntimeError("formal GemRate or SNK trades adapter did not complete")
    if set(targeted.get("requestedAdapters") or []) != {"pc_ebay_sales", "en_price_ref"}:
        raise RuntimeError("targeted receipt is not limited to PC/eBay and EN price reference")
    if targeted.get("ok") is not True or any(
        (targeted_results.get(adapter) or {}).get("ok") is not True
        for adapter in ("pc_ebay_sales", "en_price_ref")
    ):
        raise RuntimeError("targeted PC/EN refresh did not complete")
    if not (
        int(snk_resume.get("polled") or 0) == int((formal_results.get("snk_price") or {}).get("processed") or -1)
        and int(snk_resume.get("checkpointed") or 0) == int(snk_resume.get("polled") or -1)
        and int(snk_resume.get("networkRequests", -1)) == 0
        and int(snk_resume.get("ingestRuns", -1)) == 0
        and int(snk_resume.get("runId") or 0) > 0
    ):
        raise RuntimeError("SNK price resume receipt contract failed")
    if set(snk_binding_delta.get("requestedAdapters") or []) != {"snk_trades", "snk_price"}:
        raise RuntimeError("post-binding SNK receipt is not limited to the two SNK adapters")
    if snk_binding_delta.get("ok") is not True or any(
        (snk_delta_results.get(adapter) or {}).get("ok") is not True
        for adapter in ("snk_trades", "snk_price")
    ):
        raise RuntimeError("post-binding SNK delta refresh did not complete")
    for adapter in ("snk_trades", "snk_price"):
        delta = snk_delta_results[adapter]
        if not (
            int(delta.get("processed") or 0) > 0
            and int(delta.get("processed") or -1) == int(delta.get("checkpointed") or -2)
            and int(delta.get("processed") or -1) == int(delta.get("due") or -2)
        ):
            raise RuntimeError(f"post-binding {adapter} delta receipt contract failed")

    load_env()
    conn = db()
    try:
        cur = conn.cursor()
        universe = current_universe(cur)
        active_ids = set(universe.get("variantIds") or [])
        if len(active_ids) != TONIGHT_CARD_COUNT:
            raise RuntimeError("active universe is not the locked 762-card cohort")
        checkpoint_gate = _active_checkpoint_gate(cur, active_ids=active_ids)
    finally:
        conn.close()

    status = cmd_status()
    candidates = cmd_scan_candidates(min_pop=1000)
    gaps = cmd_export_gaps()
    counts = (status or {}).get("counts") or {}
    candidate_counts = (candidates or {}).get("counts") or {}
    if any(int(counts.get(field) or -1) != TONIGHT_CARD_COUNT for field in (
        "members",
        "withExactSource",
        "withPrice",
        "withImage",
        "frozenIdentity",
        "frozenSource",
        "frozenImage",
        "productSubsetReady",
    )):
        raise RuntimeError("current DB readiness counts do not cover all 762 active cards")
    if int(counts.get("gapCards", -1)) != 0 or int((gaps or {}).get("count", -1)) != 0:
        raise RuntimeError("active DB gaps remain")
    if int(candidate_counts.get("activeUniverse") or -1) != TONIGHT_CARD_COUNT:
        raise RuntimeError("candidate scan active universe count changed")
    if int(candidate_counts.get("newCandidates") or -1) != TONIGHT_BACKLOG_COUNT:
        raise RuntimeError("qualified candidate backlog changed from the locked 776")

    export_result = cmd_export_product_subset(frontend_bundle=frontend_bundle)
    if int(export_result.get("cards") or -1) != TONIGHT_CARD_COUNT:
        raise RuntimeError("product export does not contain all 762 active cards")
    snk_trade_delta = snk_delta_results["snk_trades"]
    snk_price_delta = snk_delta_results["snk_price"]
    combined_snk_trades = {
        **formal_results["snk_trades"],
        "due": int(formal_results["snk_trades"].get("due") or 0) + int(snk_trade_delta["due"]),
        "processed": int(formal_results["snk_trades"].get("processed") or 0) + int(snk_trade_delta["processed"]),
        "checkpointed": int(formal_results["snk_trades"].get("checkpointed") or 0) + int(snk_trade_delta["checkpointed"]),
        "runIds": [
            int(value)
            for value in (formal_results["snk_trades"].get("runId"), snk_trade_delta.get("runId"))
            if int(value or 0) > 0
        ],
        "postBindingDelta": True,
        "ok": True,
    }
    combined_snk_price = {
        "adapter": "snk_price",
        "mode": "incr",
        "due": int(snk_resume["polled"]) + int(snk_price_delta["due"]),
        "processed": int(snk_resume["polled"]) + int(snk_price_delta["processed"]),
        "checkpointed": int(snk_resume["checkpointed"]) + int(snk_price_delta["checkpointed"]),
        "accepted": int(snk_resume.get("accepted") or 0) + int(snk_price_delta.get("accepted") or 0),
        "emptyKline": int(snk_resume.get("emptyKline") or 0) + int(snk_price_delta.get("emptyKline") or 0),
        "runIds": [
            int(value)
            for value in (snk_resume.get("runId"), snk_price_delta.get("runId"))
            if int(value or 0) > 0
        ],
        "ok": True,
        "resumedFromFormalReceipt": True,
        "postBindingDelta": True,
    }
    combined_results = [
        formal_results["gemrate_pop"],
        combined_snk_trades,
        combined_snk_price,
        targeted_results["pc_ebay_sales"],
        targeted_results["en_price_ref"],
    ]
    refresh = {
        "enabled": True,
        "ok": True,
        "method": "checkpoint_finalization_after_single_formal_run",
        "formalReceipt": str(formal_receipt_path),
        "formalReceiptSha256": hashlib.sha256(formal_receipt_path.read_bytes()).hexdigest(),
        "targetedReceipt": str(targeted_receipt_path),
        "targetedReceiptSha256": hashlib.sha256(targeted_receipt_path.read_bytes()).hexdigest(),
        "snkResumeReceipt": str(snk_resume_path),
        "snkResumeReceiptSha256": hashlib.sha256(snk_resume_path.read_bytes()).hexdigest(),
        "snkBindingDeltaReceipt": str(snk_binding_delta_path),
        "snkBindingDeltaReceiptSha256": hashlib.sha256(snk_binding_delta_path.read_bytes()).hexdigest(),
        "results": combined_results,
        "postFreshness": checkpoint_gate,
    }
    receipt = {
        "asOf": utc_now(),
        "action": "pass",
        "passMethod": "finalize-from-checkpoints-without-network-retry",
        "productExport": export_result,
        "imagePolicy": export_result["imagePolicy"],
        "frontendBundle": export_result["frontendBundle"],
        "universe": (status or {}).get("universe"),
        "refresh": refresh,
        "promote": {
            "status": "awaiting_daddy_promote",
            "snapshotPath": str(OUT_DIR / "product-subset-snapshot.json"),
        },
        "statusCounts": counts,
        "candidateCounts": candidate_counts,
        "gapCount": int((gaps or {}).get("count") or 0),
    }
    atomic_json(OUT_DIR / "pass_receipt.json", receipt)
    print(json.dumps({
        "action": "finalize-pass-from-checkpoints",
        "receipt": str(OUT_DIR / "pass_receipt.json"),
        "cards": export_result.get("cards"),
        "gapCards": receipt["gapCount"],
        "newCandidates": candidate_counts.get("newCandidates"),
        "refreshOk": True,
    }, ensure_ascii=False, indent=2))
    return receipt


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
    frontend_bundle = receipt.get("frontendBundle") or {}
    current_frontend_bundle = bundle_manifest(ROOT)
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
    required_adapters = {
        "gemrate_pop",
        "snk_trades",
        "snk_price",
        "pc_ebay_sales",
        "en_price_ref",
    }
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
        image_top100_count = int(image_policy.get("top100Cards"))
        snk_eligible_count = int(image_policy.get("snkEligible"))
        snk_selected_count = int(image_policy.get("snkSelected"))
        non_snk_count = int(image_policy.get("nonSnkOnlyWithoutEligible"))
    except (TypeError, ValueError):
        image_top100_count = -1
        snk_eligible_count = -1
        snk_selected_count = -2
        non_snk_count = -1

    failures: list[str] = []
    if receipt.get("action") != "pass":
        failures.append("receipt action is not pass")
    if image_policy.get("policyId") != TOP100_SNK_IMAGE_POLICY_ID:
        failures.append("receipt does not use the approved Top-100 SNK image policy")
    if product_export.get("imagePolicy") != image_policy:
        failures.append("product export image policy does not match the pass receipt")
    if image_top100_count != 100:
        failures.append("image policy does not cover exactly 100 displayed cards")
    if snk_eligible_count != snk_selected_count:
        failures.append("one or more eligible SNK images were not selected")
    if non_snk_count != 100 - snk_eligible_count:
        failures.append("non-SNK image count does not equal the no-eligible-SNK count")
    if len(decisions_by_id) != 100:
        failures.append("image policy decisions are not unique for all displayed cards")
    for card in snapshot.get("top100") or []:
        decision = decisions_by_id.get(str(card.get("id"))) or {}
        if (
            int(decision.get("marketRank") or -1) != int(card.get("marketRank") or -2)
            or decision.get("selectedSha256") != (card.get("image") or {}).get("sha256")
            or (
                decision.get("snkEligible") is True
                and decision.get("selectedSource") != "snkrdunk"
            )
        ):
            failures.append(f"image decision mismatch for displayed card {card.get('id')}")
            break
    if frontend_bundle != current_frontend_bundle:
        failures.append("current frontend bundle differs from the pass-bound bundle")
    if product_export.get("frontendBundle") != frontend_bundle:
        failures.append("product export frontend bundle does not match the pass receipt")
    if frontend_bundle.get("policyId") != FRONTEND_POLICY_ID:
        failures.append("frontend bundle does not use the no-graders product policy")
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
                "frontendBundle": frontend_bundle,
                "cards": [
                    {
                        "id": card.get("id"),
                        "marketRank": card.get("marketRank"),
                        "pricePsa10": card.get("pricePsa10"),
                        "populationPsa10": card.get("populationPsa10"),
                        "imageSha256": (card.get("image") or {}).get("sha256"),
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
            "qcReceiptSha256": receipt_sha256,
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
        "frontendBundle": frontend_bundle,
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
        # human reminder short file
        remind = {
            "asOf": utc_now(),
            "title": "CARDZ human attention",
            "newCandidates": len(candidates),
            "incompleteInPool": len(attention),
            "next": [
                "Review data/runtime/operator/candidates.json",
                "Bind links + full-stock harvest for new candidates",
                "Freeze identity/source/image when ready",
                "Daily incremental for frozen cards; pass => export-product-subset + promote",
            ],
            "candidateSample": candidates[:20],
            "incompleteSample": attention[:20],
        }
        remind_path = OUT_DIR / "human_attention.json"
        remind_path.write_text(json.dumps(remind, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(json.dumps({"action": "scan-candidates", "path": str(path), "remindPath": str(remind_path), **payload["counts"]}, ensure_ascii=False, indent=2))
        return payload
    finally:
        conn.close()


def cmd_daily(
    *,
    do_pass: bool = False,
    refresh: bool = False,
    refresh_report_path: Path | None = None,
):
    """Daily operator loop: full active refresh, then candidates, gaps and pass."""
    load_env()
    pass_frontend_bundle = bundle_manifest(ROOT) if do_pass else None
    refresh_report: dict[str, Any] = {
        "enabled": refresh or refresh_report_path is not None,
        "ok": not refresh and refresh_report_path is None,
        "results": [],
    }
    if refresh_report_path is not None:
        refresh_report = _authorized_refresh_report(refresh_report_path)
    elif refresh:
        import subprocess

        py = sys.executable
        collect = ROOT / "pipelines" / "collect_control.py"
        command = [
            py,
            "-X",
            "utf8",
            str(collect),
            "incr",
            "--adapter",
            "all",
            "--delay",
            "1.0",
        ]
        started = datetime.now(timezone.utc)
        runner: dict[str, Any]
        try:
            result = subprocess.run(
                command,
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                timeout=21600,
                encoding="utf-8",
                errors="replace",
            )
            runner = {
                "exit": result.returncode,
                "stdoutTail": (result.stdout or "")[-8000:],
                "stderrTail": (result.stderr or "")[-3000:],
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
            refresh_report = {**collected, "enabled": True, "runner": runner}
            refresh_report["ok"] = bool(collected.get("ok")) and runner.get("exit") == 0
        except Exception as exc:  # noqa: BLE001
            refresh_report = {
                "enabled": True,
                "ok": False,
                "results": [],
                "runner": runner,
                "error": f"refresh_receipt:{type(exc).__name__}:{exc}",
            }

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
            atomic_json(OUT_DIR / "daily_summary.json", summary)
            print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
            return 1
        export_result = cmd_export_product_subset(
            frontend_bundle=pass_frontend_bundle,
        )
        if int(export_result.get("cards") or 0) != int(counts.get("members") or -1):
            raise RuntimeError("product export does not contain the full active universe")
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        receipt = {
            "asOf": utc_now(),
            "action": "pass",
            "productExport": export_result,
            "imagePolicy": export_result["imagePolicy"],
            "frontendBundle": export_result["frontendBundle"],
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
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        atomic_json(OUT_DIR / "daily_summary.json", summary)
        print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return 0 if refresh_report.get("ok") is not False else 1






def cmd_db_tidy():
    """Apply new-era warehouse migration + quarantine banned sources."""
    import subprocess
    py = sys.executable
    script = ROOT / "pipelines" / "new_era_db_tidy.py"
    r = subprocess.run([py, "-X", "utf8", str(script)], cwd=str(ROOT))
    if r.returncode != 0:
        raise SystemExit(r.returncode)
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
        help="read-only Top-100 SNK image eligibility and switch count",
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
    p_finalize = sub.add_parser(
        "finalize-pass-from-checkpoints",
        help="finalize the one formal run using durable targeted receipts; no network",
    )
    p_finalize.add_argument("--formal-receipt", type=Path, default=FORMAL_REFRESH_RECEIPT)
    p_finalize.add_argument("--targeted-receipt", type=Path, default=TARGETED_REFRESH_RECEIPT)
    p_finalize.add_argument("--snk-resume-receipt", type=Path, default=SNK_RESUME_RECEIPT)
    p_finalize.add_argument(
        "--snk-binding-delta-receipt",
        type=Path,
        default=SNK_BINDING_DELTA_RECEIPT,
    )
    p_cand = sub.add_parser("scan-candidates", help="PSA10 pop>=1000 candidate scan + human attention")
    p_cand.add_argument("--min-pop", type=int, default=1000)
    p_daily = sub.add_parser("daily", help="daily candidates+gaps; --pass exports product subset receipt")
    sub.add_parser("db-tidy", help="apply new-era warehouse + quarantine banned sources")
    p_daily.add_argument("--pass", dest="do_pass", action="store_true", help="DADDY pass: export product subset + promote receipt")
    refresh_group = p_daily.add_mutually_exclusive_group()
    refresh_group.add_argument("--refresh", action="store_true", help="run collect_control status+incr (real exact-id harvest, not --help)")
    refresh_group.add_argument(
        "--refresh-report",
        type=Path,
        help="reuse one already-completed five-adapter refresh receipt; performs no network work",
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
    elif args.cmd == "finalize-pass-from-checkpoints":
        cmd_finalize_pass_from_checkpoints(
            formal_receipt_path=args.formal_receipt,
            targeted_receipt_path=args.targeted_receipt,
            snk_resume_path=args.snk_resume_receipt,
            snk_binding_delta_path=args.snk_binding_delta_receipt,
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
        cmd_db_tidy()
    else:
        raise SystemExit(f"unknown command: {args.cmd}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
