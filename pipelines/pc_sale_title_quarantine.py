#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PC 成交隔離 receipt（sales 軌嘅 quarantine ledger）：title↔卡號矛盾 + 孤立價格尖刺。

2026-07-14 v1326 Latias 事故（runbook 形狀 29）：PC exact 產品頁被 PC 自己嘅
fuzzy match 塞入第二張卡嘅成交（title 印住 #060/095，我哋張卡係 #113），
$91 成交接受咗之後變成 30d 窗 anchor，出 +556%。

清毒分兩截：
  1. 未來：c11_pc_sold_ingest.verify_sale 嘅 exact-gate 加咗
     title_collector_contradiction 檢查，新毒 listing 落唔到 landing。
  2. 現在（呢個腳本）：已 landing、已 accepted 嘅毒 sales ——
     market_sale_observation 冇 status 欄，acceptance 係 append-only，
     sales history 係 VIEW，所以用 receipt ledger 隔離：
     判別器（同一份實現，import 返嚟）掃全部 PC 成交，寫
     data/runtime/operator/audit/pc_sale_title_quarantine_current.json，
     live-db-snapshot.ts bake 嗰陣讀佢，將呢啲 sale 嘅貢獻由日聚合度扣除。

2026-09-25 加第二個判別器（sale_price_outlier.is_isolated_price_outlier）：
title 睇落啱、但價錢同前後成交都唔夾嘅單（Latias & Latios GX 170/181 一單
$1,485 夾喺 ~$17k 中間，06-27 做咗 90d 錨）。一個 outlier 定義（同 quote 側
一樣嘅 [M/2.5, M*2.0] 帶），前後兩邊都判出界而且同一方向先算，所以真嘅
價位轉換（variant 419 ~$48 → ~$101）唔會中。reason = price_isolated_spike。
成交範圍用 planner 自己嘅 eligibility + fingerprint 去重
（psa10_latest_sale_quote.load_candidate_sales），唔另抄一份。

2026-09-25 加第三個判別器（c11_pc_sold_ingest.title_listing_conflict）：listing
自己話係另一樣嘢 —— 一批卡（lot / pair / sequential set）、爛殼、PSA 9 /
AUTHENTIC、或者另一個語言版本（EN 卡收咗 JP/CN/KR 成交）。同 ingest 一份實現，
reason 逐類寫低（title_lot_or_bundle / title_damaged_slab /
title_other_psa_grade / title_language_mismatch）。語言要睇卡嘅
card_language 同 set_name + canonical_name，所以三條 SQL 都帶埋佢哋。

receipt = 判別器（而家）∪ market_pc_sale_title_quarantine 已有嘅行（stored
reason 照留）。表係 upsert-only 嘅 materialisation；receipt 一定要包住張表，
否則判別結果日後翻轉 = 靜靜放返條毒成交出街（planner 同 FE 都係讀 receipt）。

receipt 係推導出嚟嘅（成日可以由判別器重新生成，唔係人手抄名單），
test_price_lane_contracts.py 有 DB gate 監住佢唔准過期：任何 accepted
矛盾 sale 唔喺 receipt 度 = 紅。

2026-09-26 放返（063）：表入面證實係真卡嘅成交，逐 id 連證據寫入
market_pc_sale_quarantine_release；「仲隔離緊」只由
market_pc_sale_title_quarantine_effective 呢一個 view 話事。放返咗嘅成交唔入
receipt，佢嗰條 reason 嘅判別器（而家只准 title_collector_contradiction）對佢
唔再算數；其他判別器（listing / 價格尖刺）照判，中咗就照入 receipt，gate 會紅
叫人 revoke。

用法：
  python -X utf8 pipelines/pc_sale_title_quarantine.py          # 重新生成 receipt
  python -X utf8 pipelines/pc_sale_title_quarantine.py --out X  # dry-run：只寫 X
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from c11_pc_sold_ingest import (  # noqa: E402
    identity_text,
    title_collector_contradiction,
    title_listing_conflict,
)
from sale_price_outlier import (  # noqa: E402
    following_sales,
    is_isolated_price_outlier,
    is_price_outlier,
    prior_sales,
)

AUDIT_DIR = ROOT / "data" / "runtime" / "operator" / "audit"
CURRENT_RECEIPT = AUDIT_DIR / "pc_sale_title_quarantine_current.json"

DISCRIMINATOR_TITLE = "c11_pc_sold_ingest.title_collector_contradiction"
DISCRIMINATOR_LISTING = "c11_pc_sold_ingest.title_listing_conflict"
DISCRIMINATOR_PRICE = "sale_price_outlier.is_isolated_price_outlier"
REASON_TITLE = "title_collector_contradiction"
REASON_PRICE = "price_isolated_spike"
# 063: the release table's CHECK constraint allows exactly these reasons; a
# release of any other reason cannot be re-judged here and raises.
RELEASABLE_REASONS = (REASON_TITLE,)

SCAN_SQL = """
    SELECT s.id, s.variant_id, s.sold_at, s.unit_price_usd, s.quantity,
           s.transaction_value_usd, s.listing_title, p.collector_number,
           p.card_language, p.set_name, v.canonical_name
    FROM market_sale_observation s
    INNER JOIN catalog_printing_identity p ON p.variant_id = s.variant_id
    LEFT JOIN catalog_variant v ON v.id = s.variant_id
    WHERE s.source_code = 'pricecharting' AND s.listing_title IS NOT NULL
"""

# LEFT JOIN：058 張表冇 FK 去 landing（見 migration 註釋），landing 行冇咗
# 嗰條隔離一樣要留喺 receipt，唔係跟住消失。
STORED_SQL = """
    SELECT tq.sale_observation_id AS id, tq.variant_id, tq.reason,
           s.sold_at, s.unit_price_usd, s.quantity, s.transaction_value_usd,
           s.listing_title, p.collector_number, p.card_language, p.set_name,
           v.canonical_name
    FROM market_pc_sale_title_quarantine tq
    LEFT JOIN market_sale_observation s ON s.id = tq.sale_observation_id
    LEFT JOIN catalog_printing_identity p ON p.variant_id = tq.variant_id
    LEFT JOIN catalog_variant v ON v.id = tq.variant_id
"""

# 063: same rows, plus whether the one release predicate
# (market_pc_sale_title_quarantine_effective) still quarantines each of them.
# r is joined for the evidence hash only; the view alone decides `released`.
STORED_RELEASE_SQL = """
    SELECT tq.sale_observation_id AS id, tq.variant_id, tq.reason,
           s.sold_at, s.unit_price_usd, s.quantity, s.transaction_value_usd,
           s.listing_title, p.collector_number, p.card_language, p.set_name,
           v.canonical_name,
           CASE WHEN e.sale_observation_id IS NULL THEN 1 ELSE 0 END AS released,
           r.evidence_sha256 AS release_evidence_sha256
    FROM market_pc_sale_title_quarantine tq
    LEFT JOIN market_pc_sale_title_quarantine_effective e
      ON e.sale_observation_id = tq.sale_observation_id
    LEFT JOIN market_pc_sale_quarantine_release r
      ON r.sale_observation_id = tq.sale_observation_id AND r.status = 'released'
    LEFT JOIN market_sale_observation s ON s.id = tq.sale_observation_id
    LEFT JOIN catalog_printing_identity p ON p.variant_id = tq.variant_id
    LEFT JOIN catalog_variant v ON v.id = tq.variant_id
"""

# 價格判別器手上嘅 planner 行冇 transaction_value_usd，但 FE 係用佢由日聚合
# 扣數（view 係 SUM(transaction_value_usd)），所以逐 id 返 landing 攞真值。
DETAIL_SQL = """
    SELECT s.id, s.variant_id, s.sold_at, s.unit_price_usd, s.quantity,
           s.transaction_value_usd, s.listing_title, p.collector_number,
           p.card_language, p.set_name, v.canonical_name
    FROM market_sale_observation s
    LEFT JOIN catalog_printing_identity p ON p.variant_id = s.variant_id
    LEFT JOIN catalog_variant v ON v.id = s.variant_id
    WHERE s.id IN ({marks})
"""


def _entry(row: Mapping[str, Any], reason: str) -> dict:
    """One receipt entry.  Every path (title / price / stored) goes through here."""

    title = row.get("listing_title")
    wanted = row.get("collector_number")
    sold_at = row.get("sold_at")
    return {
        "saleObservationId": int(row["id"]),
        "variantId": int(row["variant_id"]),
        "observedDate": str(sold_at)[:10] if sold_at is not None else None,
        "unitPriceUsd": float(row["unit_price_usd"]) if row.get("unit_price_usd") is not None else None,
        "quantity": int(row["quantity"]) if row.get("quantity") is not None else None,
        "transactionValueUsd": float(row["transaction_value_usd"]) if row.get("transaction_value_usd") is not None else None,
        "wantedCollectorNumber": str(wanted) if wanted is not None else None,
        "listingTitle": str(title)[:200] if title is not None else None,
        "reason": reason,
    }


def title_reason(row: Mapping[str, Any], *, released_reason: str | None = None) -> str | None:
    """Why this stored sale's title condemns it, or None.

    The collector-number rule goes first so the reason of every sale it
    already condemned (and the 058 rows written from it) stays the same.

    `released_reason` (063) is the table reason a live release row lifted for
    this sale: that rule no longer counts against it, every other rule does.
    """

    if released_reason is not None and released_reason not in RELEASABLE_REASONS:
        raise ValueError(f"release of {released_reason!r} cannot be re-judged here")
    title = str(row["listing_title"])
    if released_reason != REASON_TITLE and title_collector_contradiction(
        title, str(row["collector_number"])
    ):
        return REASON_TITLE
    return title_listing_conflict(
        title,
        card_language=str(row.get("card_language") or ""),
        identity_text=identity_text(row),
    )


def build_receipt(rows, released: Mapping[int, str] | None = None) -> list[dict]:
    """Title entries (discriminators 1 and 2), sorted by id.

    `released` maps a released sale id to the table reason its release lifted.
    """

    released = released or {}
    entries: list[dict] = []
    for r in rows:
        reason = title_reason(r, released_reason=released.get(int(r["id"])))
        if reason is None:
            continue
        entries.append(_entry(r, reason))
    entries.sort(key=lambda e: e["saleObservationId"])
    return entries


def price_spike_verdicts(
    sales_by_variant: Mapping[int, Sequence[Mapping[str, Any]]],
) -> dict[int, dict[str, Any]]:
    """sale id -> {direction, priorMedianUsd, followingMedianUsd} (discriminator 2).

    `sales_by_variant` is exactly what psa10_latest_sale_quote.load_candidate_sales
    returns, so the neighbourhood a sale is judged against is the one the
    planner prices from.  Medians are evidence for the reviewer only; the
    verdict itself is is_isolated_price_outlier and nothing else.
    """

    flagged: dict[int, dict[str, Any]] = {}
    for variant_id in sorted(sales_by_variant):
        sales = sales_by_variant[variant_id]
        for sale in sales:
            direction = is_isolated_price_outlier(sale, sales)
            if direction is None:
                continue
            _, before = is_price_outlier(sale, prior_sales(sale, sales))
            _, after = is_price_outlier(sale, following_sales(sale, sales))
            flagged[int(sale["saleObservationId"])] = {
                "direction": direction,
                "priorMedianUsd": before["medianUsd"],
                "followingMedianUsd": after["medianUsd"],
            }
    return flagged


def compose_entries(
    title_entries: Iterable[Mapping[str, Any]],
    verdicts: Mapping[int, Mapping[str, Any]],
    detail_rows: Iterable[Mapping[str, Any]],
    stored_rows: Iterable[Mapping[str, Any]],
) -> list[dict]:
    """One entry per sale id, sorted by id.

    Precedence: a stored table row keeps its stored reason (first reason wins,
    same rule as collect_control's upsert, so receipt and table never disagree
    on why a sale is out); otherwise title beats price.  A stored row that no
    discriminator flags any more is STILL an entry -- that union is what keeps
    table ⊆ receipt, and dropping it would re-admit the sale for the planner
    and the FE the moment a verdict flips.
    """

    details = {int(row["id"]): row for row in detail_rows}
    by_id: dict[int, dict] = {}
    for sale_id, verdict in verdicts.items():
        row = details.get(int(sale_id))
        if row is None:
            # The id came out of market_sale_observation a moment ago; losing it
            # here would silently un-quarantine a sale the rule just condemned.
            raise RuntimeError(f"price-spike sale {sale_id} has no landing row detail")
        by_id[int(sale_id)] = {**_entry(row, REASON_PRICE), **verdict}
    for entry in title_entries:
        by_id[int(entry["saleObservationId"])] = dict(entry)
    for row in stored_rows:
        sale_id = int(row["id"])
        reason = str(row["reason"])
        if sale_id in by_id:
            by_id[sale_id]["reason"] = reason
        else:
            by_id[sale_id] = _entry(row, reason)
    return [by_id[sale_id] for sale_id in sorted(by_id)]


def split_released(
    stored_rows: Iterable[Mapping[str, Any]],
) -> tuple[list[Mapping[str, Any]], dict[int, Mapping[str, Any]]]:
    """(rows the effective view still quarantines, {released id: row}).

    `released` comes from STORED_RELEASE_SQL, i.e. from
    market_pc_sale_title_quarantine_effective alone; a row without the key
    (pre-063 STORED_SQL) is still quarantined.  A release this module cannot
    re-judge raises instead of being honoured blind.
    """

    active: list[Mapping[str, Any]] = []
    released: dict[int, Mapping[str, Any]] = {}
    for row in stored_rows:
        if not row.get("released"):
            active.append(row)
            continue
        if str(row["reason"]) not in RELEASABLE_REASONS:
            raise RuntimeError(f"sale {row['id']} released from unsupported reason {row['reason']!r}")
        released[int(row["id"])] = row
    return active, released


def release_problems(
    stored_rows: Iterable[Mapping[str, Any]], receipt_ids: Iterable[int],
) -> list[str]:
    """What test_price_lane_contracts' DB gate refuses about releases.

    * a released sale in the receipt: another rule condemns it (revoke the
      release: status='revoked'), or the receipt predates the release.  Either
      way the DB readers admit a sale the receipt readers drop.
    * a released sale without a 64-hex evidence hash on a live release row.
    """

    _, released = split_released(stored_rows)
    problems: list[str] = []
    back = sorted(set(released) & {int(i) for i in receipt_ids})
    if back:
        problems.append(f"released sales still in the receipt: {back[:10]}")
    unproven = sorted(
        sale_id for sale_id, row in released.items()
        if not re.fullmatch(r"[0-9a-f]{64}", str(row.get("release_evidence_sha256") or ""))
    )
    if unproven:
        problems.append(f"released without 64-hex evidence: {unproven[:10]}")
    return problems


def build_document(
    *,
    stamp: str,
    title_rows: Sequence[Mapping[str, Any]],
    sales_by_variant: Mapping[int, Sequence[Mapping[str, Any]]],
    detail_loader,
    stored_rows: Sequence[Mapping[str, Any]],
) -> dict:
    """Pure receipt document.  Same inputs + same stamp => byte-identical JSON."""

    active_rows, released = split_released(stored_rows)
    title_entries = build_receipt(
        title_rows, {sale_id: str(row["reason"]) for sale_id, row in released.items()}
    )
    verdicts = price_spike_verdicts(sales_by_variant)
    detail_rows = detail_loader(sorted(verdicts)) if verdicts else []
    entries = compose_entries(title_entries, verdicts, detail_rows, active_rows)
    entry_reason = {entry["saleObservationId"]: entry["reason"] for entry in entries}
    released_block = [
        {
            "saleObservationId": sale_id,
            "variantId": int(released[sale_id]["variant_id"]),
            "releasedReason": str(released[sale_id]["reason"]),
            "evidenceSha256": released[sale_id].get("release_evidence_sha256"),
            # Non-null = another rule condemns it now; the DB gate goes red.
            "condemnedBy": entry_reason.get(sale_id),
        }
        for sale_id in sorted(released)
    ]
    return {
        "generatedAt": stamp,
        # Old single-discriminator field kept for readers that still expect it.
        "discriminator": DISCRIMINATOR_TITLE,
        "discriminators": [DISCRIMINATOR_TITLE, DISCRIMINATOR_LISTING, DISCRIMINATOR_PRICE],
        "scannedSales": len(title_rows),
        "priceScannedSales": sum(len(sales) for sales in sales_by_variant.values()),
        "quarantinedSales": len(entries),
        "reasons": dict(sorted(Counter(entry["reason"] for entry in entries).items())),
        "releasedSales": len(released_block),
        "released": released_block,
        "entries": entries,
    }


def render(doc: Mapping[str, Any]) -> str:
    return json.dumps(doc, ensure_ascii=False, indent=1)


def load_stored_rows(cursor: Any) -> list[dict]:
    # 1146 on STORED_RELEASE_SQL = 063 not applied yet (no effective view /
    # release table): nothing is released, read the raw table.  1146 on
    # STORED_SQL = 058 not applied yet: nothing is stored and there is nothing
    # to keep sticky.  Same tolerated bootstrap window as
    # collect_control._sync_pc_sale_title_quarantine.  Anything else raises.
    for sql in (STORED_RELEASE_SQL, STORED_SQL):
        try:
            cursor.execute(sql)
        except Exception as error:  # noqa: BLE001 - re-raised unless it is 1146
            if tuple(getattr(error, "args", ()))[:1] != (1146,):
                raise
            continue
        return [dict(row) for row in cursor.fetchall()]
    return []


def collect_document(cursor: Any, *, stamp: str) -> dict:
    """Read everything the receipt needs through `cursor` (SELECT only)."""

    from psa10_latest_sale_quote import (  # noqa: E402
        current_universe_variant_ids,
        load_candidate_sales,
    )

    cursor.execute(SCAN_SQL)
    title_rows = [dict(row) for row in cursor.fetchall()]
    stored_rows = load_stored_rows(cursor)
    active_rows, released = split_released(stored_rows)
    title_ids = {
        entry["saleObservationId"]
        for entry in build_receipt(
            title_rows, {sale_id: str(row["reason"]) for sale_id, row in released.items()}
        )
    }
    stored_ids = {int(row["id"]) for row in active_rows}
    # Already-quarantined sales are not neighbours: a condemned sale must not
    # vote on whether the sale next to it is a spike.  A released sale (063) is
    # a real sale again: it votes, and the price rule judges it like any other.
    # 2026-09-25: the SNKRDUNK lane is judged too (its JPY 1,999,999 placeholder
    # and source-withdrawn trades were minted/anchored).  Each lane is judged
    # only against its own sales, so the group key is (source, variant id);
    # price_spike_verdicts uses the key for grouping and nothing else.
    # scripts/test_pc_sale_quarantine_snk_lane.py.
    universe = current_universe_variant_ids(cursor)
    sales_by_variant = {
        (source, variant_id): sales
        for source in ("pricecharting", "snkrdunk")
        for variant_id, sales in load_candidate_sales(
            cursor,
            source=source,
            variant_ids=universe,
            quarantined_sale_ids=title_ids | stored_ids,
        ).items()
    }

    def detail_loader(sale_ids: Sequence[int]) -> list[dict]:
        rows: list[dict] = []
        for start in range(0, len(sale_ids), 400):
            chunk = list(sale_ids[start:start + 400])
            cursor.execute(
                DETAIL_SQL.format(marks=",".join(["%s"] * len(chunk))), tuple(chunk)
            )
            rows.extend(dict(row) for row in cursor.fetchall())
        return rows

    return build_document(
        stamp=stamp,
        title_rows=title_rows,
        sales_by_variant=sales_by_variant,
        detail_loader=detail_loader,
        stored_rows=stored_rows,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Regenerate the PC sale quarantine receipt")
    parser.add_argument(
        "--out", default=None,
        help="write only this path (dry-run); default rewrites the current receipt + archive",
    )
    args = parser.parse_args(argv)

    from rebuild_036 import connect, DAILY_CREDENTIALS_ENV  # noqa: E402

    conn = connect(DAILY_CREDENTIALS_ENV)
    try:
        cur = conn.cursor()
        # One read-only snapshot: the title scan, the stored rows and the sales
        # the price rule reads must describe the same moment.
        cur.execute("START TRANSACTION READ ONLY")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        doc = collect_document(cur, stamp=stamp)
        conn.rollback()
    finally:
        conn.close()

    text = render(doc)
    print(
        f"scanned {doc['scannedSales']} pc sales (price rule {doc['priceScannedSales']});"
        f" quarantined {doc['quarantinedSales']} {doc['reasons']}; released {doc['releasedSales']}"
    )
    if args.out:
        target = Path(args.out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode("utf-8"))
        print(f"receipt (dry-run): {target}")
        return 0
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    CURRENT_RECEIPT.write_bytes(text.encode("utf-8"))
    archive = AUDIT_DIR / f"pc_sale_title_quarantine_{stamp}.json"
    archive.write_bytes(text.encode("utf-8"))
    print(f"receipt: {CURRENT_RECEIPT}")
    print(f"archive: {archive}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
