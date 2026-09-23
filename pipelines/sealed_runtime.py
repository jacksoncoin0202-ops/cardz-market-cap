#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared runtime helpers for the sealed (原盒) pipeline.

Reused doctrine from the singles line:
- exact-bound only; candidates never auto-promote to exact
- store-all + mark (rejected rows stay, flagged), missing stays missing
- checkpoints live in market_ingest_checkpoint with adapter source_code
  and stream_key "sealed:{sealed_id}:{ext-short}"
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))
from qualified_pool_operator import db, load_env  # noqa: E402

OUT_DIR = ROOT / "data" / "runtime" / "operator" / "sealed"
HTML_DIR = ROOT / "data" / "runtime" / "sealed" / "pc_html"

SLA_HOURS = 36.0

SEALED_ADAPTERS = ("sealed_pc", "sealed_snk", "sealed_yahoo", "sealed_ebay", "sealed_mercari")

# --- box title QC ----------------------------------------------------------

REJECT_PATTERNS = [
    (re.compile(r"\b(lot|bundle|set of|repack|resale|proxy|replica|custom)\b", re.I), "bundle_or_fake"),
    (re.compile(r"\b(case|carton)\b", re.I), "case_not_box"),
    (re.compile(r"(カートン|ケース販売)", re.I), "case_not_box"),
    (re.compile(r"\b(etb|elite trainer box|booster bundle|build\s*&\s*battle|blister|mini tin|tin\b)\b", re.I), "not_booster_box"),
    (re.compile(r"(エリートトレーナー|デッキ|スターター|プロモ|バラ売り|バラパック)", re.I), "not_booster_box"),
    # other boxes of a set, and SV10's attache case sold on its own (2026-09-24 Yahoo titles)
    (re.compile(r"(アタッシュケース|ジャンボカードコレクション|ミステリーボックス|トレーナーボックス|シャイニーボックス|コレクターボックス"
                r"|mystery\s*box|trainer\s*box|shiny\s*box|collector\s*box)", re.I), "not_booster_box"),
    (re.compile(r"\b(empty|no box|box only|opened|resealed)\b", re.I), "opened_or_empty"),
    (re.compile(r"(BOX|ボックス)\s*(無し|なし)", re.I), "opened_or_empty"),
    (re.compile(r"(空箱|空BOX|空ボックス|開封済|開封品|サーチ済|中身なし|箱のみ)", re.I), "opened_or_empty"),
    (re.compile(r"(ギフトボックス|gift\s*box)", re.I), "not_booster_box"),
    (re.compile(r"(収納ケース|紙製.{0,24}カードボックス)", re.I), "not_booster_box"),
    (re.compile(r"(BOX|ボックス)用", re.I), "not_booster_box"),  # "BOX用プラスチック保護ケース": a case for a box
    # a Pokemon Center special box, a collection file set, candy sold by the box (食玩/グミ)
    (re.compile(r"(スペシャル\s*(BOX|ボックス)|コレクションファイル|食玩|グミ)", re.I), "not_booster_box"),
    # boxes of several sets in one lot: "BOX 6種セット", "蒼海の七傑他", "SV11W + SV11B"; not "他の人", "他にも出品中"
    (re.compile(r"(\d+\s*種\s*セット|(?<!その)他(?=\s|$|\d+\s*種))"), "bundle_or_fake"),
    (re.compile(r"\b(?:op|eb|prb|sv|sm|swsh|s|m|xy|bw)-?\s?\d+[a-z]?\s*[+＋]\s*(?:op|eb|prb|sv|sm|swsh|s|m|xy|bw)-?\s?\d+",
                re.I), "bundle_or_fake"),
    (re.compile(r"(BOX|ボックス)購入(キャンペーン|特典)", re.I), "promo_card"),
    (re.compile(r"\d+/[A-Z]{2,4}-P", re.I), "promo_card"),
    (re.compile(r"(イタリア版|フランス版|ドイツ版|英語版|韓国版|中国版|海外版)", re.I), "foreign_edition"),
    (re.compile(r"\b(1|one)\s*(pack|booster pack)\b", re.I), "single_pack"),
    (re.compile(r"(1パック|パック単品)", re.I), "single_pack"),
    (re.compile(r"((BOX|ボックス|パック)\s*分|相当|口分)", re.I), "box_equivalent_lot"),  # "2 box 分 48p"
    (re.compile(r"(1/2|½)\s*(BOX|ボックス)", re.I), "box_equivalent_lot"),  # "5 パック セット 1/2 ボックス"
    (re.compile(r"(まとめ売り|まとめて|引退品|福袋|オリパ)", re.I), "junk_lot_signal"),
    (re.compile(r"\b(psa|bgs|cgc|ars)\s*\d", re.I), "graded_item"),
]

BOX_KEYWORD_RE = re.compile(r"(booster box|display|\bbox\b|ボックス|ＢＯＸ|BOX)", re.I)

# Boxes in a lot; the first pattern that finds 1..24 wins. 2026-09-24: "OP-03 BOX" read 3, "BOX 10パック" and
# "Booster Box 24 Packs" read the X of BOX, "2BOXセット" read 1 (\b never falls between BOX and セ).
QTY_PATTERNS = [
    # "2BOXセット", "2 boxes", "10箱", "4ボックス"; not a set code's digits ("OP-03 BOX", "OP05 Box")
    re.compile(r"(?<![a-z0-9-])(\d+)\s*(?:boxes|box|箱|ボックス)(?![a-z])", re.I),
    # "BOX 2個セット", "4点セット", "BOX 2セット", "Booster Box 2 Set"; not a pack count ("パック 12個") nor a
    # listing's number ("_2点目")
    re.compile(r"(?<![0-9-])(?<!パック)(?<!パック )(\d+)\s*(?:個|点|セット|(?i:sets?\b))(?!目)"),
    # "BOX×3", "x2"; not the x of a word ("BOX 10", "ドリームex 5") nor packs ("パック×13", "x 24 packs")
    re.compile(r"(?:(?<![a-z])(?<!パック)x|(?<!パック)×)\s*(\d+)(?!\d)(?!\s*(?:パック|packs?\b|枚|cards?\b))", re.I),
]

SHRINK_ON_RE = re.compile(r"(シュリンク付|シュリンク有|shrink[- ]?wrapped|factory sealed)", re.I)
SHRINK_OFF_RE = re.compile(r"(シュリンクなし|シュリンク無|no shrink)", re.I)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def utc_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def sha(obj: Any) -> str:
    raw = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def qc_box_title(title: str) -> dict[str, Any]:
    """Classify a marketplace listing title for a sealed booster box.

    Returns {accepted, reason, quantity, condition}.
    """
    text = (title or "").strip()
    if not text:
        return {"accepted": False, "reason": "empty_title", "quantity": 1, "condition": "unknown"}
    for pattern, reason in REJECT_PATTERNS:
        if pattern.search(text):
            return {"accepted": False, "reason": reason, "quantity": 1, "condition": "unknown"}
    if not BOX_KEYWORD_RE.search(text):
        return {"accepted": False, "reason": "no_box_keyword", "quantity": 1, "condition": "unknown"}
    quantity = 1
    for pattern in QTY_PATTERNS:
        match = pattern.search(text)
        if match:
            try:
                candidate = int(match.group(1))
            except ValueError:
                continue
            if 1 <= candidate <= 24:
                quantity = candidate
                break
    condition = "unknown"
    if SHRINK_OFF_RE.search(text):
        condition = "sealed-no-shrink"
    elif SHRINK_ON_RE.search(text):
        condition = "sealed-shrink"
    return {"accepted": True, "reason": "", "quantity": quantity, "condition": condition}


PACK_WORD_RE = re.compile(r"(拡張パック|強化拡張パック|ハイクラスパック|ブースターパック|エクストラブースター|プレミアムブースター)")
# Era names printed on every box of the era ("ソード＆シールド 拡張パック 反逆クラッシュ"). They hold set names (S1W ソード,
# S1H シールド, SM1+ サン&ムーン), so an era name is never a set name on its own: 2026-09-23 S2's own titles were rejected
# as foreign, and SM1+'s 「サン&ムーン」 would have matched every Sun & Moon box.
SERIES_RE = re.compile(r"(?:ソード|サン|スカーレット|ブラック|ダイヤモンド|ハートゴールド)(?:&|アンド|・)"
                       r"(?:シールド|ムーン|バイオレット|ホワイト|パール|ソウルシルバー)"
                       r"|(?:sword|sun|scarlet|black|diamond|heartgold)(?:&|and)(?:shield|moon|violet|white|pearl|soulsilver)")
# How sellers write a catalog name: M2a メガドリームex as MEGAドリームex, M6 ストームエメラルダ (pokemon-card.com/ex/m6)
# as ストームエメラルド. Titles and names both pass through, so a bundle naming MEGAドリームex is foreign to SV10.
SELLER_SPELLINGS = (("mega", "メガ"), ("ストームエメラルド", "ストームエメラルダ"))


def _title_norm(text: str | None) -> str:
    """Width, case, spaces and quote brackets do not name a set: 'THE BEST Vol.2' is 'THE BEST vol.2', ＆ is &."""
    text = re.sub(r"[\s「」『』【】\"“”]+", "", unicodedata.normalize("NFKC", text or "").casefold())
    for seller, name in SELLER_SPELLINGS:
        text = text.replace(seller, name)
    return text


def _name_tokens(name: str | None) -> list[str]:
    """Distinctive tokens for a set name: the name, and the name without its pack word; never an era name alone."""
    text = _title_norm(name)
    cleaned = PACK_WORD_RE.sub("", text)
    return [t for t in {cleaned, text} if len(t) >= 2 and not SERIES_RE.fullmatch(t)]


def title_set_contamination(title: str, own_names: list[str], foreign_names: list[str]) -> dict[str, Any]:
    """Require own set name in title; reject titles naming other sets too.

    Longest name first, and each match is used up: in 'THE BEST Vol.2' PRB-02 is named, not PRB-01's 'THE BEST';
    an era name in the title is used up before the set names inside it."""
    text = _title_norm(title)
    kinds: dict[str, set[str]] = {}
    for name in own_names:
        for token in _name_tokens(name):
            kinds.setdefault(token, set()).add("own")
    for name in foreign_names:
        for token in _name_tokens(name):
            if len(token) >= 3:
                kinds.setdefault(token, set()).add("foreign")
    for era in SERIES_RE.findall(text):
        kinds.setdefault(era, set()).add("era")
    hits: set[str] = set()
    for token in sorted(kinds, key=len, reverse=True):
        if token in text:
            hits |= kinds[token]
            text = text.replace(token, "\x00")
    if any("own" in k for k in kinds.values()) and "own" not in hits:
        return {"accepted": False, "reason": "own_set_name_missing"}
    if "foreign" in hits:
        return {"accepted": False, "reason": "foreign_set_in_title"}
    return {"accepted": True, "reason": ""}


# --- FX ---------------------------------------------------------------------


def fx_units_per_usd(cur, currency: str) -> float | None:
    """Return units of `currency` per 1 USD from the latest FX observation."""
    code = currency.upper()
    if code == "USD":
        return 1.0
    cur.execute(
        """
        SELECT base_currency, quote_currency, rate FROM market_fx_rate_observation
        WHERE (base_currency='USD' AND quote_currency=%s) OR (base_currency=%s AND quote_currency='USD')
        ORDER BY effective_date DESC LIMIT 1
        """,
        (code, code),
    )
    row = cur.fetchone()
    if not row:
        return None
    rate = float(row["rate"])
    if row["base_currency"] == "USD":
        return rate
    return (1.0 / rate) if rate else None


def to_usd(amount: float | None, currency: str, units_per_usd: float | None) -> float | None:
    if amount is None:
        return None
    if currency.upper() == "USD":
        return round(float(amount), 2)
    if not units_per_usd:
        return None
    return round(float(amount) / units_per_usd, 2)


# --- catalog / binding loads -------------------------------------------------


def ensure_sealed_fullname_columns(cur) -> None:
    cur.execute(
        """
        SELECT COLUMN_NAME FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='catalog_sealed_product'
          AND COLUMN_NAME IN ('full_name_en','full_name_ja','full_name_source')
        """
    )
    have = {str(r["COLUMN_NAME"]) for r in cur.fetchall()}
    if "full_name_en" not in have:
        cur.execute("ALTER TABLE catalog_sealed_product ADD COLUMN full_name_en VARCHAR(512) NULL AFTER name_jp")
    if "full_name_ja" not in have:
        cur.execute("ALTER TABLE catalog_sealed_product ADD COLUMN full_name_ja VARCHAR(512) NULL AFTER full_name_en")
    if "full_name_source" not in have:
        cur.execute("ALTER TABLE catalog_sealed_product ADD COLUMN full_name_source VARCHAR(32) NULL AFTER full_name_ja")
    cur.execute("INSERT IGNORE INTO cardz_schema_version (version_code) VALUES ('026')")


def load_sealed_products(cur, *, statuses: tuple[str, ...] = ("active", "unreleased")) -> list[dict]:
    placeholders = ",".join(["%s"] * len(statuses))
    cur.execute(
        f"""
        SELECT id, sku_id, slug, game, lang, group_code, set_code, name_en, name_jp,
               release_month, packs_per_box, product_kind, print_wave, official_url, status, notes
        FROM catalog_sealed_product
        WHERE status IN ({placeholders})
        ORDER BY id
        """,
        statuses,
    )
    return [dict(r) for r in cur.fetchall()]


def load_sealed_bindings(
    cur,
    *,
    source_code: str,
    require_accepted: bool = True,
) -> list[dict]:
    """Bindings for one source. accepted = the SKU's accepted source freeze names this very item. A freeze on the
    SKU is not enough: 2026-09-23 JU EN (freeze on the 1st edition Jungle box) also pulled its unlimited-box candidate."""
    cur.execute(
        """
        SELECT i.source_code, i.external_entity_id, i.sealed_id, i.canonical_url,
               i.match_status, i.resolved,
               p.sku_id, p.slug, p.group_code, p.game, p.lang, p.set_code,
               p.name_en, p.name_jp, p.status,
               EXISTS(
                 SELECT 1 FROM operator_sealed_binding_freeze f
                 WHERE f.sealed_id=i.sealed_id AND f.freeze_kind='source'
                   AND f.source_code=i.source_code AND f.acceptance_status='accepted'
                   AND f.external_entity_id=i.external_entity_id
               ) AS source_accepted
        FROM catalog_sealed_source_identity i
        JOIN catalog_sealed_product p ON p.id = i.sealed_id
        WHERE i.source_code=%s AND i.match_status <> 'rejected' AND p.status <> 'no-box'
        ORDER BY i.sealed_id
        """,
        (source_code,),
    )
    rows = [dict(r) for r in cur.fetchall()]
    if require_accepted:
        rows = [r for r in rows if int(r["source_accepted"] or 0) == 1]
    return rows


def load_sealed_hints(cur, *, source_code: str, hint_kinds: tuple[str, ...]) -> list[dict]:
    placeholders = ",".join(["%s"] * len(hint_kinds))
    cur.execute(
        f"""
        SELECT h.sealed_id, h.source_code, h.hint_kind, h.url,
               p.sku_id, p.slug, p.group_code, p.game, p.lang, p.set_code, p.name_en, p.name_jp,
               p.print_wave, p.status
        FROM catalog_sealed_source_hint h
        JOIN catalog_sealed_product p ON p.id = h.sealed_id
        WHERE h.source_code=%s AND h.hint_kind IN ({placeholders}) AND p.status <> 'no-box'
        ORDER BY h.sealed_id
        """,
        (source_code, *hint_kinds),
    )
    return [dict(r) for r in cur.fetchall()]


# --- writes ------------------------------------------------------------------


# ON DUPLICATE head of every sealed price write (upsert_sealed_price and sealed_collect's PC / SNK history). A row
# names the item whose price it holds: a write from another item carries its item id in, and compose reads a row
# only while that id is the SKU's frozen item (sealed_price_compose.load_frozen_items). metric_status goes first,
# while external_entity_id still holds the row's old item (MySQL assigns left to right), so a row an operator
# quarantined stays out when the same item writes it again.
PRICE_UPSERT_HEAD = (
    "metric_status=IF(metric_status='quarantined' AND external_entity_id=VALUES(external_entity_id), "
    "metric_status, VALUES(metric_status)), external_entity_id=VALUES(external_entity_id)"
)


def upsert_sealed_price(
    cur,
    *,
    sealed_id: int,
    source_code: str,
    price_kind: str,
    observed_date: str,
    native_price: float | None,
    native_currency: str | None,
    price_usd: float | None,
    external_entity_id: str = "",
    source_url: str | None = None,
    metric_status: str = "ok",
    ingest_run_key: str = "",
) -> None:
    cur.execute(
        """
        INSERT INTO market_sealed_price_observation
          (sealed_id, source_code, price_kind, observed_date, native_price, native_currency,
           price_usd, external_entity_id, source_url, metric_status, ingest_run_key)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON DUPLICATE KEY UPDATE
          """ + PRICE_UPSERT_HEAD + """, native_price=VALUES(native_price), native_currency=VALUES(native_currency),
          price_usd=VALUES(price_usd), source_url=VALUES(source_url), ingest_run_key=VALUES(ingest_run_key)
        """,
        (
            sealed_id, source_code, price_kind, observed_date, native_price, native_currency,
            price_usd, external_entity_id or "", (source_url or "")[:700] or None, metric_status,
            ingest_run_key,
        ),
    )


def insert_sealed_sale(
    cur,
    *,
    sealed_id: int,
    source_code: str,
    lot_id: str,
    sold_at: str,
    unit_price_usd: float | None,
    native_price: float | None,
    native_currency: str | None,
    quantity: int,
    total_native_price: float | None,
    box_condition: str,
    title: str | None,
    raw_url: str | None,
    metric_status: str,
    parser: str,
    ingest_run_key: str,
) -> int:
    cur.execute(
        """
        INSERT IGNORE INTO market_sealed_sale_observation
          (sealed_id, source_code, lot_id, sold_at, unit_price_usd, native_price, native_currency,
           quantity, total_native_price, box_condition, title, raw_url, transaction_fingerprint,
           metric_status, parser, ingest_run_key)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        (
            sealed_id, source_code, lot_id[:191], sold_at, unit_price_usd, native_price,
            native_currency, quantity, total_native_price, box_condition,
            (title or "")[:500] or None, (raw_url or "")[:700] or None,
            sha({"s": source_code, "l": lot_id, "t": sold_at}),
            metric_status, parser[:64], ingest_run_key,
        ),
    )
    return cur.rowcount


def warehouse_sealed(
    cur,
    *,
    sealed_id: int,
    source_code: str,
    external_entity_id: str,
    observation_kind: str,
    payload: Any,
    ingest_run_key: str,
) -> None:
    content = sha({"source": source_code, "ext": external_entity_id, "kind": observation_kind, "payload": payload})
    cur.execute(
        """
        INSERT INTO market_sealed_source_warehouse
          (sealed_id, source_code, external_entity_id, observation_kind, observed_at,
           field_map_json, raw_payload_json, content_sha256, ingest_run_key)
        VALUES (%s,%s,%s,%s,%s,CAST(%s AS JSON),CAST(%s AS JSON),%s,%s)
        ON DUPLICATE KEY UPDATE observed_at=VALUES(observed_at)
        """,
        (
            sealed_id, source_code, external_entity_id[:191],
            observation_kind, utc_naive().strftime("%Y-%m-%d %H:%M:%S.%f"),
            json.dumps(payload, ensure_ascii=False, default=str),
            json.dumps(payload, ensure_ascii=False, default=str),
            content, ingest_run_key,
        ),
    )


# --- checkpoints / runs -------------------------------------------------------


def stream_key(sealed_id: int, external_id: str) -> str:
    ext = str(external_id or "")
    if len(ext) > 70:
        ext = hashlib.sha256(ext.encode("utf-8")).hexdigest()[:16]
    return f"sealed:{int(sealed_id)}:{ext}"[:100]


def record_sealed_run(
    conn,
    *,
    adapter: str,
    mode: str,
    items: list[dict[str, Any]],
    payload: Any,
    started_at: datetime,
) -> dict[str, Any]:
    """Insert market_ingest_run + per-item checkpoints for a sealed adapter poll."""
    if not items:
        return {"runId": None, "checkpointed": 0}
    completed_at = utc_naive()
    if started_at.tzinfo is not None:
        started_at = started_at.astimezone(timezone.utc).replace(tzinfo=None)
    cur = conn.cursor()
    payload_sha = sha(payload)
    run_key = sha(
        {
            "adapter": adapter,
            "mode": mode,
            "startedAt": started_at.isoformat(),
            "payloadSha256": payload_sha,
            "streams": [stream_key(int(i["sealedId"]), str(i.get("externalId") or "")) for i in items],
        }
    )
    cur.execute(
        """
        INSERT INTO market_ingest_run
            (run_key, source_code, ingest_mode, effective_at, payload_sha256,
             manifest_sha256, status, observed_count, accepted_count,
             quarantined_count, rejected_count, started_at, completed_at)
        VALUES (%s,%s,%s,%s,%s,%s,'completed',%s,%s,0,0,%s,%s)
        ON DUPLICATE KEY UPDATE id=LAST_INSERT_ID(id), status='completed',
            observed_count=VALUES(observed_count), accepted_count=VALUES(accepted_count),
            completed_at=VALUES(completed_at)
        """,
        (
            run_key, adapter, "incremental" if mode == "incr" else "stock", completed_at,
            payload_sha, payload_sha, len(items), len(items), started_at, completed_at,
        ),
    )
    run_id = int(cur.lastrowid)
    for item in items:
        cur.execute(
            """
            INSERT INTO market_ingest_checkpoint
                (source_code, stream_key, last_effective_at, last_payload_sha256, last_run_id, updated_at)
            VALUES (%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                last_effective_at=VALUES(last_effective_at),
                last_payload_sha256=VALUES(last_payload_sha256),
                last_run_id=VALUES(last_run_id),
                updated_at=VALUES(updated_at)
            """,
            (
                adapter,
                stream_key(int(item["sealedId"]), str(item.get("externalId") or "")),
                completed_at,
                sha({"adapter": adapter, "item": item.get("externalId"), "at": completed_at.isoformat()}),
                run_id,
                completed_at,
            ),
        )
    return {"runId": run_id, "checkpointed": len(items)}


def load_sealed_checkpoints(cur, adapter: str) -> dict[str, datetime]:
    cur.execute(
        "SELECT stream_key, last_effective_at FROM market_ingest_checkpoint WHERE source_code=%s",
        (adapter,),
    )
    return {str(r["stream_key"]): r["last_effective_at"] for r in cur.fetchall()}


def checkpoint_age_hours(checkpoints: dict[str, datetime], key: str) -> float | None:
    dt = checkpoints.get(key)
    if dt is None:
        return None
    return max(0.0, (utc_naive() - dt).total_seconds() / 3600.0)
