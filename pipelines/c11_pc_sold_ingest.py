#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""C11 PriceCharting HTML → exact-bound PSA10 sale observations.

Reads data/runtime/private-source-map/c11_pc_ebay_map_full900.jsonl (ready_for_c12 rows),
parses saved PC product HTML, verifies PSA10 sold rows against card metadata,
and writes market_sale_observation under the exact PriceCharting product identity.

Identity policy (hard):
  - catalog_source_identity source_code='pricecharting' owns the exact product id.
  - eBay listing item ids remain transaction metadata, never provider identity.
  - Sales attach only through the accepted exact PriceCharting product binding.

external_entity_id for sales: {pc_product_id}  (the accepted provider entity id)

Usage:
  python -X utf8 pipelines/c11_pc_sold_ingest.py --dry-run
  python -X utf8 pipelines/c11_pc_sold_ingest.py --write
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, time, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from failure_ledger import record_failure, record_resolution  # noqa: E402
from pc_sale_identity import pc_sale_fingerprint, pc_sale_price_text  # noqa: E402
from pricecharting_page_parse import parse_product_html  # noqa: E402
from pc_page_cache import load_page as load_pc_page  # noqa: E402

# The consolidated map every other reader uses (collect_control, pc_cdp_sold_refresh_win,
# pc_ungraded_reference_ingest, new_era_db_tidy, rebuild_036). This script used to default
# to the pre-consolidation c11_pc_ebay_map.jsonl shard -- 166 KB against 1.1 MB -- so a run
# without --map ingested a fraction of the roster and reported success. The runbook carried
# "always pass it explicitly" as a bold sentence, which is a step that works exactly as long
# as somebody remembers it.
MAP_DEFAULT = ROOT / "data/runtime/private-source-map/c11_pc_ebay_map_full900.jsonl"
REGISTRY = ROOT / "data/runtime/private-source-map/liquidity-source-registry.jsonl"
REPORT_DIR = ROOT / "data/runtime/private-source-map/qualified-pool-reports"
SOURCE_CODE = "pricecharting"
GRADER = "psa"
GRADE = "10"
ACCEPTED = "partial"
OTHER_GRADE_RE = re.compile(r"\b(?:BGS|CGC|SGC|TAG)\s*10\b", re.IGNORECASE)
RAW_RE = re.compile(r"\b(?:raw|ungraded|proxy|orica|reprint)\b", re.IGNORECASE)
BUNDLE_RE = re.compile(r"\b(?:lot|bundle|set of|x\s*\d+)\b", re.IGNORECASE)
JP_RE = re.compile(r"\b(?:japanese|japan|jp)\b", re.IGNORECASE)
STOP = {
    "the", "and", "with", "ex", "gx", "vmax", "vstar", "card", "pokemon",
    "full", "art", "special", "rare", "ultra", "holo", "promo",
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


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def norm_col(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (value or "").upper())


# Listing 自己認身份嘅兩種寫法：
#   pair：060/095、101/SV-P。斜線兩邊唔准有空格（「PSA 10 / Lost Origins」嘅
#         分隔符斜線唔係卡號，回掃 v433 誤中）；分母要有數字或者係 XY-P 形
#         promo 尾（「PSA 10/POP 3」個「10/POP」唔係卡號，回掃 v852 誤中）。
#   bare：#086（# 開頭、後面唔係 /）。限 1-3 位：賣家成日寫「#2024」年份 tag
#         （回掃 v2228/v1096 誤中），現代卡號冇 4 位數。
COLLECTOR_PAIR_RE = re.compile(r"#?\b0*(\d{1,4})/([A-Za-z0-9][A-Za-z0-9-]{0,7})\b")
COLLECTOR_PROMO_DENOM_RE = re.compile(r"^[A-Za-z]{1,3}-P$", re.IGNORECASE)
COLLECTOR_BARE_RE = re.compile(r"#\s*0*(\d{1,3})\b(?!\s*/|\d)")


def _collector_claims(title: str) -> set[int]:
    claims = {
        int(m.group(1))
        for m in COLLECTOR_PAIR_RE.finditer(title or "")
        if any(ch.isdigit() for ch in m.group(2)) or COLLECTOR_PROMO_DENOM_RE.match(m.group(2))
    }
    claims |= {int(m.group(1)) for m in COLLECTOR_BARE_RE.finditer(title or "")}
    return claims


def title_collector_contradiction(title: str, collector_number: str) -> bool:
    """PC exact 產品頁都會被 PC 自己嘅 fuzzy match 塞入第二張卡嘅成交。

    2026-07-14 v1326 Latias 事故：Team Up #113 產品頁（binding exact，冇綁錯）
    嘅 completed-sales 入面混咗一條 Tag Bolt「#060/095」嘅 $91 成交，接受咗
    之後 30d 窗出 +556%。產品身份 exact 唔代表逐條 listing 都係嗰張卡 ——
    title 印住第二張卡嘅卡號，就係 listing 自己認咗第二張卡。

    比較用卡號數字部分嘅整數值：catalog 通常淨存 number（"113"），title 會
    零墊（"060/095"）；int 比較兩邊都免疫。賣家成日 JP/EN 兩個號一齊印
    （"#091/071 #086"），所以係「全部 claim 都對唔上」先算矛盾；title 冇
    任何 claim → 唔算矛盾（exact gate 原意：唔逼賣家寫卡號）。
    """
    m = re.match(r"^\s*#?\s*0*(\d{1,4})\s*(?:/|$)", (collector_number or "").strip())
    if not m:
        return False
    wanted = int(m.group(1))
    claims = _collector_claims(title)
    if not claims or wanted in claims:
        return False
    # 赦免：wanted 以裸數字出現喺 title（「Jasmine's Gaze 245 … 231/182」——
    # 賣家報咗我哋張卡個號，旁邊個 pair 只係 set 大小/JP 對應）。裸數字唔做
    # fire 信號（太嘈），但做 pass 信號係安全方向：只會少殺，唔會多殺。
    if re.search(rf"(?<![\d/.])0*{wanted}(?![\d/])", title or ""):
        return False
    return True


def name_tokens(value: str) -> set[str]:
    return {
        t
        for t in re.findall(r"[a-z0-9]+", (value or "").casefold())
        if len(t) > 2 and t not in STOP
    }


def resolve_html_path(row: dict[str, Any]) -> Path | None:
    notes = str(row.get("notes") or "")
    m = re.search(r"html=([^;]+)", notes)
    candidates: list[Path] = []
    if m:
        candidates.append(Path(m.group(1).strip()))
    html_path = row.get("htmlPath") or row.get("html_path")
    if html_path:
        candidates.append(Path(str(html_path)))
    for p in candidates:
        if p.is_file():
            return p
        alt = ROOT / p
        if alt.is_file():
            return alt
    # fallback: c11/{variant}_{slug}.html
    vid = row.get("variant_id")
    if vid is not None:
        c11 = ROOT / "data/private/pricecharting_session/html/c11"
        if c11.is_dir():
            for p in c11.glob(f"{vid}_*.html"):
                if p.is_file():
                    return p
    return None


def parse_sold_date(text: str) -> date | None:
    raw = (text or "").strip()
    for fmt in ("%Y-%m-%d", "%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def gate_map_rows_against_exact_products(
    map_rows: list[dict[str, Any]],
    *,
    canonical_by_variant: Mapping[int, int],
    exact_products_by_canonical_variant: Mapping[int, set[str]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Keep only rows whose current exact PC identity proves this product/card pair.

    Historical C11 maps are useful transport hints, but only the current canonical
    `catalog_source_identity` can authorize their product id for sale attachment.
    Alias rows attach to the canonical variant.  An absent, stale, or ambiguous
    exact identity fails closed.
    """

    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for row in map_rows:
        variant_id = int(row["variant_id"])
        canonical_variant_id = int(canonical_by_variant.get(variant_id, variant_id))
        product_id = str(row.get("pc_product_id") or "").strip()
        exact_products = exact_products_by_canonical_variant.get(
            canonical_variant_id, set()
        )
        if not exact_products:
            rejected.append(
                {
                    "variant_id": canonical_variant_id,
                    "status": "pc_exact_identity_missing",
                    "pc_product_id": product_id or None,
                }
            )
            continue
        if len(exact_products) != 1:
            rejected.append(
                {
                    "variant_id": canonical_variant_id,
                    "status": "pc_exact_identity_ambiguous",
                    "pc_product_id": product_id or None,
                }
            )
            continue
        if product_id not in exact_products:
            rejected.append(
                {
                    "variant_id": canonical_variant_id,
                    "status": "pc_exact_product_stale",
                    "pc_product_id": product_id or None,
                }
            )
            continue
        gated = dict(row)
        gated["variant_id"] = canonical_variant_id
        gated["_pc_exact_product_gate"] = True
        accepted.append(gated)
    return accepted, rejected


def gate_map_rows_against_db_exact_products(
    cur,
    map_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Resolve aliases and authoritative exact PC identities in one read-only gate."""

    variant_ids = sorted({int(row["variant_id"]) for row in map_rows})
    if not variant_ids:
        return [], []
    placeholders = ",".join(["%s"] * len(variant_ids))
    cur.execute(
        f"""
        SELECT duplicate_variant_id, canonical_variant_id
        FROM catalog_variant_alias
        WHERE duplicate_variant_id IN ({placeholders})
        """,
        variant_ids,
    )
    canonical_by_variant = {
        int(row["duplicate_variant_id"]): int(row["canonical_variant_id"])
        for row in cur.fetchall()
    }
    canonical_ids = sorted(
        {canonical_by_variant.get(variant_id, variant_id) for variant_id in variant_ids}
    )
    canonical_placeholders = ",".join(["%s"] * len(canonical_ids))
    cur.execute(
        f"""
        SELECT COALESCE(alias.canonical_variant_id, identity.variant_id)
                   AS canonical_variant_id,
               identity.external_entity_id
        FROM catalog_source_identity AS identity
        LEFT JOIN catalog_variant_alias AS alias
          ON alias.duplicate_variant_id = identity.variant_id
        WHERE identity.source_code='pricecharting'
          AND identity.match_status='exact'
          AND (
            identity.variant_id IN ({placeholders})
            OR alias.canonical_variant_id IN ({canonical_placeholders})
          )
        """,
        variant_ids + canonical_ids,
    )
    exact_products_by_canonical_variant: dict[int, set[str]] = defaultdict(set)
    for identity in cur.fetchall():
        external_entity_id = str(identity.get("external_entity_id") or "").strip()
        if external_entity_id:
            exact_products_by_canonical_variant[
                int(identity["canonical_variant_id"])
            ].add(external_entity_id)
    return gate_map_rows_against_exact_products(
        map_rows,
        canonical_by_variant=canonical_by_variant,
        exact_products_by_canonical_variant=exact_products_by_canonical_variant,
    )


def verify_sale(
    row: dict[str, Any],
    sale: dict[str, Any],
    stats: Counter,
) -> dict[str, Any] | None:
    title = str(sale.get("title") or "").strip()
    itm = str(sale.get("ebay_itm") or "").strip()
    url = str(sale.get("ebay_url") or "").strip()
    price = sale.get("price_usd")
    date_text = str(sale.get("date") or "").strip()
    if not title or not itm or not re.fullmatch(r"\d{9,15}", itm):
        stats["reject_identity"] += 1
        return None
    if not isinstance(price, (int, float)) or float(price) <= 0:
        stats["reject_price"] += 1
        return None
    sold = parse_sold_date(date_text)
    if sold is None:
        stats["reject_date"] += 1
        return None
    if OTHER_GRADE_RE.search(title) or RAW_RE.search(title):
        stats["reject_grade_conflict"] += 1
        return None
    # No `PSA 10` check on the title. These rows come out of PriceCharting's own
    # completed-auctions-manual-only div -- that div IS the PSA 10 tab, so the
    # source has already decided the grade, and re-deriving it from free-text
    # seller copy can only lose rows. Measured over the 1,936 saved product
    # pages in data/private/pricecharting_session/html/full900: 439 of the
    # 41,450 rows that survive the checks below carry no literal "PSA 10" in
    # their title (1.06%). They are not other grades -- they are sellers who
    # wrote "PSA GEM MINT 10", "PSA GRADE 10", or no grade at all
    # ("Electrode 101/165 | SV - MEW en: 151 | Holo - English | Pokemon NM").
    # OTHER_GRADE_RE / RAW_RE / BUNDLE_RE stay: those catch titles that
    # contradict the tab (a BGS 10, a raw copy, a lot), which is a real signal.
    if BUNDLE_RE.search(title):
        stats["reject_bundle"] += 1
        return None
    # A current exact PC product identity already proves product ↔ canonical card.
    # Its completed-sale listing titles are transport metadata, not a second
    # identity authority.  Ungated callers retain the legacy title checks.
    if row.get("_pc_exact_product_gate"):
        # 一個例外：listing title 明確印住第二張卡嘅 NNN/NNN 卡號 = PC 自己
        # fuzzy match 塞錯咗（v1326 Latias +556% 事故）。呢條唔係第二個身份
        # 權威，係矛盾偵測 —— 冇卡號照放行。
        if title_collector_contradiction(title, str(row.get("collector_number") or "")):
            stats["reject_collector_contradiction"] += 1
            return None
    else:
        collector = str(row.get("collector_number") or "")
        wanted = norm_col(collector)
        if wanted and wanted not in norm_col(title):
            # also accept bare number with # prefix patterns already covered by norm
            stats["reject_collector"] += 1
            return None
        # Japanese map cards must look JP (or path already scored high in C11)
        set_name = str(row.get("set_name") or "")
        pc_url = str(row.get("pc_url") or "")
        is_jp = "japanese" in set_name.casefold() or "japanese" in pc_url.casefold()
        if is_jp and not JP_RE.search(title):
            stats["reject_language"] += 1
            return None
        tokens = name_tokens(str(row.get("card_name") or ""))
        title_cf = title.casefold()
        if tokens and not any(t in title_cf for t in tokens):
            stats["reject_name"] += 1
            return None
    unit = Decimal(str(price))
    product_id = row.get("pc_product_id")
    if product_id is None:
        stats["reject_no_product"] += 1
        return None
    external = str(int(product_id))
    fp = pc_sale_fingerprint(product_id, date_text, unit, itm)
    payload = {
        "transport": "pricecharting_c11",
        "pc_product_id": int(product_id),
        "ebay_itm": itm,
        "title": title[:200],
        "date": date_text,
        "price_usd": float(unit),
        "variant_id": int(row["variant_id"]),
    }
    return {
        "variant_id": int(row["variant_id"]),
        "external_entity_id": external,
        "fingerprint": fp,
        "sold_at": datetime.combine(sold, time.min),
        "source_date_text": date_text[:100],
        "unit_price_usd": unit,
        "payload_sha256": sha256_text(json.dumps(payload, sort_keys=True)),
        "ebay_itm": itm,
        "ebay_url": url,
        "title": title,
    }


def load_map(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            continue
        if row.get("ready_for_c12") is not True:
            continue
        if str(row.get("confidence") or "").casefold() != "high":
            continue
        rows.append(row)
    return rows


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


def before_counts(cur, vids: list[int]) -> dict[str, Any]:
    if not vids:
        return {"pricecharting_identity_n": 0, "pricecharting_identity_v": 0, "pricecharting_sale_n": 0, "pricecharting_sale_v": 0, "any_sale_n": 0, "any_sale_v": 0}
    ph = ",".join(["%s"] * len(vids))
    cur.execute(
        f"SELECT COUNT(*) n, COUNT(DISTINCT variant_id) v FROM catalog_source_identity "
        f"WHERE source_code='pricecharting' AND variant_id IN ({ph})",
        vids,
    )
    id_row = cur.fetchone()
    cur.execute(
        f"SELECT COUNT(*) n, COUNT(DISTINCT variant_id) v FROM market_sale_observation "
        f"WHERE source_code='pricecharting' AND grader_code='psa' AND grade_label='10' "
        f"AND variant_id IN ({ph})",
        vids,
    )
    sale_row = cur.fetchone()
    cur.execute(
        f"SELECT COUNT(*) n, COUNT(DISTINCT variant_id) v FROM market_sale_observation "
        f"WHERE variant_id IN ({ph})",
        vids,
    )
    any_row = cur.fetchone()
    return {
        "pricecharting_identity_n": int(id_row["n"]),
        "pricecharting_identity_v": int(id_row["v"]),
        "pricecharting_sale_n": int(sale_row["n"]),
        "pricecharting_sale_v": int(sale_row["v"]),
        "any_sale_n": int(any_row["n"]),
        "any_sale_v": int(any_row["v"]),
    }


def partition_existing_variants(
    cur,
    map_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Keep the ingest inside the current canonical catalog."""

    variant_ids = sorted({int(row["variant_id"]) for row in map_rows})
    if not variant_ids:
        return [], []
    placeholders = ",".join(["%s"] * len(variant_ids))
    cur.execute(
        f"SELECT id FROM catalog_variant WHERE id IN ({placeholders})",
        variant_ids,
    )
    existing = {int(row["id"]) for row in cur.fetchall()}
    accepted = [row for row in map_rows if int(row["variant_id"]) in existing]
    missing = [row for row in map_rows if int(row["variant_id"]) not in existing]
    return accepted, missing


def collect_sales(map_rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], Counter, list[dict[str, Any]]]:
    stats: Counter = Counter()
    sales: list[dict[str, Any]] = []
    card_reports: list[dict[str, Any]] = []
    seen_fp: set[str] = set()

    for row in map_rows:
        stats["cards_seen"] += 1
        vid = int(row["variant_id"])
        html_path = resolve_html_path(row)
        if html_path is None:
            stats["cards_no_html"] += 1
            card_reports.append({"variant_id": vid, "status": "no_html"})
            continue
        # A05 2026-08-23: shared read+parse cache (pc_page_cache); the parent
        # lane validated this same page moments ago.
        page = load_pc_page(html_path, source_url=str(row.get("pc_url") or ""))
        if page is None:
            stats["cards_no_html"] += 1
            card_reports.append({"variant_id": vid, "status": "no_html"})
            continue
        parsed = page.parsed
        if not parsed.get("ok"):
            stats["cards_parse_fail"] += 1
            card_reports.append({"variant_id": vid, "status": "parse_fail", "error": parsed.get("error")})
            continue
        product = parsed.get("product") if isinstance(parsed.get("product"), dict) else {}
        # ensure product id from map or parse
        work = dict(row)
        if work.get("pc_product_id") is None and product.get("id"):
            work["pc_product_id"] = product["id"]
        psa10 = parsed.get("psa10") if isinstance(parsed.get("psa10"), dict) else {}
        block = psa10.get("completed_sales") if isinstance(psa10, dict) else None
        raw_rows = (block or {}).get("rows") if isinstance(block, dict) else []
        if not isinstance(raw_rows, list) or not raw_rows:
            stats["cards_no_sales"] += 1
            card_reports.append({"variant_id": vid, "status": "no_psa10_sales", "html": str(html_path)})
            continue
        accepted = 0
        for sale in raw_rows:
            if not isinstance(sale, dict):
                continue
            stats["sales_seen"] += 1
            verified = verify_sale(work, sale, stats)
            if verified is None:
                continue
            if verified["fingerprint"] in seen_fp:
                stats["sales_dup_fp"] += 1
                continue
            seen_fp.add(verified["fingerprint"])
            sales.append(verified)
            accepted += 1
            stats["sales_accepted"] += 1
        try:
            html_rel = str(html_path.relative_to(ROOT))
        except ValueError:
            html_rel = str(html_path)
        if accepted:
            stats["cards_with_sales"] += 1
            card_reports.append(
                {
                    "variant_id": vid,
                    "status": "ok",
                    "accepted": accepted,
                    "raw": len(raw_rows),
                    "pc_product_id": work.get("pc_product_id"),
                    "external_entity_id": (
                        str(int(work["pc_product_id"])) if work.get("pc_product_id") else None
                    ),
                    "html": html_rel,
                }
            )
        else:
            stats["cards_zero_after_verify"] += 1
            card_reports.append(
                {
                    "variant_id": vid,
                    "status": "zero_after_verify",
                    "raw": len(raw_rows),
                    "html": html_rel,
                }
            )
    return sales, stats, card_reports


def record_card_outcomes(
    card_reports: list[dict[str, Any]],
    card_meta: dict[int, dict[str, Any]],
) -> None:
    for report in card_reports:
        variant_id = int(report["variant_id"])
        status = str(report.get("status") or "unknown_failure")
        meta = card_meta.get(variant_id) or {}
        evidence = [report["html"]] if report.get("html") else []
        context = {
            "pcProductId": meta.get("pc_product_id"),
            "accepted": report.get("accepted"),
            "rawSales": report.get("raw"),
        }
        if status == "ok":
            record_resolution(
                source="pricecharting",
                stage="normalize_psa10_sales",
                script=__file__,
                item_key=variant_id,
                resolution="accepted_psa10_sales",
                context=context,
                evidence_paths=evidence,
            )
            continue
        retryable = status in {"no_html", "parse_fail"}
        record_failure(
            source="pricecharting",
            stage="normalize_psa10_sales",
            script=__file__,
            item_key=variant_id,
            reason_code=status,
            message=str(report.get("error") or status),
            retryable=retryable,
            url=str(meta.get("pc_url") or "") or None,
            context=context,
            evidence_paths=evidence,
            next_action="retry" if retryable else "agent_review",
        )


def existing_sale_keys(cur, sales: list[dict[str, Any]]) -> set[tuple[str, str]]:
    """Return already-stored keys using the table's composite unique contract."""
    existing: set[tuple[str, str]] = set()
    chunk = 400
    for i in range(0, len(sales), chunk):
        batch = sales[i : i + chunk]
        placeholders = ",".join(["(%s, %s)"] * len(batch))
        params: list[Any] = [SOURCE_CODE]
        for sale in batch:
            params.extend([sale["external_entity_id"], sale["fingerprint"]])
        cur.execute(
            f"""
            SELECT external_entity_id, transaction_fingerprint
            FROM market_sale_observation
            WHERE source_code=%s
              AND (external_entity_id, transaction_fingerprint) IN ({placeholders})
            """,
            params,
        )
        existing.update(
            (str(row["external_entity_id"]), str(row["transaction_fingerprint"]))
            for row in cur.fetchall()
        )
    return existing


def write_sales(conn, sales: list[dict[str, Any]], map_path: Path) -> dict[str, Any]:
    if not sales:
        return {
            "run_id": None,
            "inserted_or_updated": 0,
            "skipped_existing": 0,
            "inserted_variant_ids": [],
        }
    with conn.cursor() as cur:
        existing = existing_sale_keys(cur, sales)
        new_sales = [
            sale
            for sale in sales
            if (str(sale["external_entity_id"]), str(sale["fingerprint"])) not in existing
        ]
        # Rebind 遺物自動歸位（形狀 29 rebind 變種，2026-08-13 v188 事故）：
        # dedupe key 係 (ext, fingerprint)，variant 唔喺 key 入面，所以 product
        # 改綁之後歷史行仍然 stamp 住舊主——新主 FE 永遠零成交，舊主靠 strict
        # join 先冇出毒。map 行嘅 variant 就係 ext 嘅現任 exact 主人（上游
        # recheck_exact_bindings 已證），存在行歸屬唔同就跟現任主人走。
        # daily-accept 嘅 psa10_sale ON DUP 會跟住將 acceptance 一齊re-point。
        restamped = 0
        by_ext_variant: dict[tuple[str, int], int] = {}
        for sale in sales:
            key = (str(sale["external_entity_id"]), int(sale["variant_id"]))
            by_ext_variant[key] = by_ext_variant.get(key, 0) + 1
        for (ext, variant_id) in sorted(by_ext_variant):
            cur.execute(
                """
                UPDATE market_sale_observation
                SET variant_id=%s
                WHERE source_code=%s AND external_entity_id=%s AND variant_id<>%s
                """,
                (variant_id, SOURCE_CODE, ext, variant_id),
            )
            restamped += int(cur.rowcount)
        # The run row lands even when every sale deduped: observed_count carries
        # what the pages evidenced tonight, and the daily sales manifest below
        # needs a completed run as its trust anchor for daily-accept.
        started = datetime.now(timezone.utc).replace(tzinfo=None)
        run_key = sha256_text(f"c11_pc_sold|{started.isoformat()}|{len(new_sales)}")
        payload_sha = sha256_text("\n".join(sorted(s["fingerprint"] for s in sales)))
        # The bytes actually read, not a constant. This used to hash the literal
        # string "c11_pc_ebay_map.jsonl", so every run in this script's history
        # carries the same manifest_sha256 -- it identified neither which map was
        # passed nor what was in it, while looking in the ledger like it did.
        manifest_sha = hashlib.sha256(map_path.read_bytes()).hexdigest()
        fetched_at = started
        cur.execute(
            """
            INSERT INTO market_ingest_run
                (run_key, source_code, ingest_mode, effective_at, payload_sha256, manifest_sha256,
                 status, observed_count, started_at)
            VALUES (%s, %s, 'backfill', %s, %s, %s, 'running', %s, %s)
            """,
            (run_key, SOURCE_CODE, started, payload_sha, manifest_sha, len(sales), started),
        )
        run_id = int(cur.lastrowid)
        payload = [
            (
                run_id,
                s["variant_id"],
                SOURCE_CODE,
                s["external_entity_id"],
                s["fingerprint"],
                GRADER,
                GRADE,
                s["sold_at"],
                s["source_date_text"],
                fetched_at,
                "exact_date",
                pc_sale_price_text(s["unit_price_usd"]),
                pc_sale_price_text(s["unit_price_usd"]),
                s["payload_sha256"],
                ACCEPTED,
                s["ebay_itm"][:32],
                s["ebay_url"][:512],
                s["title"][:255],
            )
            for s in new_sales
        ]
        # chunk insert
        chunk = 400
        for i in range(0, len(payload), chunk):
            cur.executemany(
                """
                INSERT INTO market_sale_observation
                    (run_id, variant_id, source_code, external_entity_id, transaction_fingerprint,
                     grader_code, grade_label, sold_at, source_date_text, fetched_at, timestamp_quality,
                     unit_price_usd, quantity, transaction_value_usd, source_payload_sha256, coverage_status,
                     listing_item_id, listing_url, listing_title)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1, %s, %s, %s, %s, %s, %s)
                """,
                payload[i : i + chunk],
            )
        cur.execute(
            """
            UPDATE market_ingest_run
            SET status='completed', accepted_count=%s, observed_count=%s, completed_at=%s
            WHERE id=%s
            """,
            (len(new_sales), len(sales), datetime.now(timezone.utc).replace(tzinfo=None), run_id),
        )
    conn.commit()
    manifest_path = write_daily_sales_manifest(run_key, sales)
    return {
        "run_id": run_id,
        "run_key": run_key,
        "inserted_or_updated": len(new_sales),
        "skipped_existing": len(sales) - len(new_sales),
        "restamped_to_current_owner": restamped,
        "inserted_variant_ids": sorted({int(sale["variant_id"]) for sale in new_sales}),
        "daily_sales_manifest": str(manifest_path),
    }


def write_daily_sales_manifest(run_key: str, sales: list[dict[str, Any]]) -> Path:
    """Every page-evidenced fingerprint of this run, whether or not its sale
    row already existed.

    Row-level dedup keeps the FIRST observer's run_id forever, so daily-accept
    cannot see a re-observation through market_sale_observation alone. This
    manifest is the §6.7 mechanism extended to daily runs: fingerprints enter
    acceptance only via a manifest whose run_key resolves to a completed
    post-activation ingest run (daily-accept enforces that)."""

    manifest_dir = ROOT / "data" / "runtime" / "rebuild-036" / "daily-sales-manifests"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = manifest_dir / f"pc-{run_key[:16]}.jsonl"
    lines = [json.dumps({
        "contract": "daily-sales-manifest-v1",
        "runKey": run_key,
        "sourceCode": SOURCE_CODE,
        "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "entries": len(sales),
    }, sort_keys=True)]
    lines.extend(
        json.dumps({
            "variantId": int(sale["variant_id"]),
            "fingerprint": str(sale["fingerprint"]),
        }, sort_keys=True)
        for sale in sales
    )
    manifest_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return manifest_path


def update_registry(vids_with_sales: set[int], card_meta: dict[int, dict[str, Any]]) -> int:
    reg = load_registry()
    n = 0
    now = utc_now()
    for vid in vids_with_sales:
        meta = card_meta.get(vid) or {}
        prev = reg.get(vid) or {"variantId": vid}
        prev.update(
            {
                "variantId": vid,
                "preferredLiquiditySource": "ebay",
                "liquidityScript": "pipelines/c11_pc_sold_ingest.py",
                "ebayTransport": "pricecharting_c11",
                "pcProductId": meta.get("pc_product_id"),
                "updatedAt": now,
            }
        )
        reg[vid] = prev
        n += 1
    save_registry(reg)
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description="Ingest C11 PC PSA10 sold → market_sale_observation")
    ap.add_argument("--map", type=Path, default=MAP_DEFAULT)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--report", type=Path, default=None)
    args = ap.parse_args()
    write = bool(args.write) and not args.dry_run

    map_rows = load_map(args.map)
    if args.limit:
        map_rows = map_rows[: args.limit]
    map_ready_high = len(map_rows)
    card_meta = {int(r["variant_id"]): r for r in map_rows}

    conn = db()
    cur = conn.cursor()
    map_rows, missing_variant_rows = partition_existing_variants(cur, map_rows)
    map_rows, exact_product_rejections = gate_map_rows_against_db_exact_products(
        cur, map_rows
    )
    # Gated alias rows now carry the canonical variant id, which is also the
    # only id allowed in downstream sale/registry records.
    card_meta.update({int(row["variant_id"]): row for row in map_rows})
    vids = [int(r["variant_id"]) for r in map_rows]
    before = before_counts(cur, vids)

    sales, stats, card_reports = collect_sales(map_rows)
    if missing_variant_rows:
        stats["cards_missing_variant"] = len(missing_variant_rows)
        card_reports = [
            {
                "variant_id": int(row["variant_id"]),
                "status": "catalog_variant_missing",
            }
            for row in missing_variant_rows
        ] + card_reports
    if exact_product_rejections:
        stats["cards_exact_product_gate_rejected"] = len(exact_product_rejections)
        for rejection in exact_product_rejections:
            stats[f"cards_{rejection['status']}"] += 1
        card_reports = exact_product_rejections + card_reports
    # --dry-run is a read-only inventory pass.  The JSON report carries its
    # outcomes; durable retry/ledger mutations happen only with --write.
    if write:
        record_card_outcomes(card_reports, card_meta)
    write_info: dict[str, Any] = {
        "run_id": None,
        "inserted_or_updated": 0,
        "inserted_variant_ids": [],
    }
    reg_n = 0
    dry_run_existing_sales = 0
    if write and sales:
        try:
            write_info = write_sales(conn, sales, args.map)
        except Exception as error:
            conn.rollback()
            record_failure(
                source="pricecharting",
                stage="write_psa10_sales",
                script=__file__,
                item_key=args.map.name,
                reason_code="db_write_failed",
                message=str(error),
                retryable=True,
                next_action="retry",
                error_type=type(error).__name__,
            )
            conn.close()
            raise
        inserted_variant_ids = set(write_info["inserted_variant_ids"])
        if inserted_variant_ids:
            reg_n = update_registry(inserted_variant_ids, card_meta)
    elif sales:
        dry_run_existing_sales = len(existing_sale_keys(cur, sales))

    after = before_counts(cur, vids)
    conn.close()

    # delta by variant
    by_vid: dict[int, int] = defaultdict(int)
    for s in sales:
        by_vid[s["variant_id"]] += 1

    summary = {
        "asOf": utc_now(),
        "write": write,
        "mapPath": str(args.map),
        "mapReadyHigh": map_ready_high,
        "mapExistingVariant": len(map_rows),
        "mapExactProductGateRejected": len(exact_product_rejections),
        "missingVariantIds": sorted(
            int(row["variant_id"]) for row in missing_variant_rows
        ),
        "identityPolicy": {
            "ebay_identity_written": 0,
            "reason": "C11 ebay_uuid_or_item is listing item_id, not altxyz UUID; schema PK allows string but contract is UUID; no invent",
        },
        "stats": dict(stats),
        "salesAccepted": len(sales),
        "variantsWithAcceptedSales": len(by_vid),
        "dryRunExistingSales": dry_run_existing_sales,
        "dryRunNetNewSales": len(sales) - dry_run_existing_sales if not write else None,
        "before": before,
        "after": after,
        "delta": {
            "pricecharting_identity_v": (
                after["pricecharting_identity_v"] - before["pricecharting_identity_v"]
            ),
            "pricecharting_sale_n": (
                after["pricecharting_sale_n"] - before["pricecharting_sale_n"]
            ),
            "pricecharting_sale_v": (
                after["pricecharting_sale_v"] - before["pricecharting_sale_v"]
            ),
        },
        "writeInfo": write_info,
        "registryUpdated": reg_n,
        "sampleCards": sorted(card_reports, key=lambda r: (-int(r.get("accepted") or 0), int(r["variant_id"])))[:20],
        "zeroVerifySample": [r for r in card_reports if r.get("status") == "zero_after_verify"][:15],
    }
    text = json.dumps(summary, ensure_ascii=False, indent=2, default=str)
    print(text)

    report_path = args.report or (REPORT_DIR / f"c11_pc_sold_{'write' if write else 'dry'}.json")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(text, encoding="utf-8")
    print(f"REPORT {report_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    try:
        exit_code = main()
    except Exception as error:
        record_failure(
            source="pricecharting",
            stage="c11_ingest_run",
            script=__file__,
            item_key="c11-pc-sold",
            reason_code="run_failed",
            message="C11 PriceCharting sold ingest aborted",
            retryable=True,
            next_action="agent_review_then_retry",
            error_type=type(error).__name__,
        )
        raise
    record_resolution(
        source="pricecharting",
        stage="c11_ingest_run",
        script=__file__,
        item_key="c11-pc-sold",
        resolution="run_completed",
    )
    raise SystemExit(exit_code)
