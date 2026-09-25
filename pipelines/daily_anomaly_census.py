#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每日異常普查：release 之後，對住啱啱出街嗰份 snapshot 列出仲有咩唔妥。

淨係報告，唔係閘（閘各有唯一執行點：價格 DB 閘、圖片 post-bake assert、
validate_daily_release）。佢做嘅係「每日自動掃一次、新嘢即刻講」，等人唔使
靠記性去搵：

  ① 價格離群   PC = 隔離 receipt 入面嘅 price_isolated_spike（已隔離，唔重判）；
                SNK = 同一個判別器（pc_sale_title_quarantine.price_spike_verdicts）
                對 SNK 成交跑一次，只報唔隔離；eBay = 已知缺口，每日照講。
  ② OP 自動圖待審   出街緊、唔係 human 揀、冇 market_image_review_approval 嘅 OP 圖。
  ③ 被拒圖出街   出街 (variant, sha) 喺 market_image_rejection_registry（critical）；
                同一個 sha 只係喺第二張卡被拒（warn）。
  ④ Top100   價變動被扣起（changePct unavailable 但有現價）、
             單筆離群日（當日得一單而嗰單係離群）、30 日冇成交。
  ⑤ 隔離     receipt 每一條；「新」= 同上一份普查嘅集合差，唔讀 written_at。

冇自己嘅門檻：離群規則、ratio、「human」嘅定義全部 import 返唯一實現。
「新」同「連續乾」：同**前一個營業日或更早**嗰份 archive 比（唔係 current），
所以同日重跑得返同一個 baseline、同一段 message、同一個 event key。
輸入有問題（snapshot generation 對唔上、receipt 冇／過期、卡對唔到 variant、
DB 等超時）一律 raise ANOMALY_*，唔會扮「今日乾」；其他 DB 錯照原樣 raise。

用法：
  python -X utf8 pipelines/daily_anomaly_census.py --business-date D --snapshot S \
      [--expected-generation G] [--out X]     # --out = dry run，淨係寫 X
"""
from __future__ import annotations

import argparse
import html
import json
import os
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import pc_sale_title_quarantine as pcq  # noqa: E402
import pymysql  # noqa: E402
from identity_brief import MESSAGE_BUDGET  # noqa: E402
from rebuild_036 import HUMAN_IMAGE_ACCEPTED_BY  # noqa: E402

CONTRACT = "cardz-anomaly-census-v1"
ANOMALY_CENSUS_EVENT = "anomaly.census"
CURRENT = pcq.AUDIT_DIR / "anomaly_census_current.json"
ARCHIVE_PREFIX = "anomaly_census_"
DETECTORS = ("price", "imageReview", "imageRegistry", "top100", "quarantine")
WINDOWS = ("1d", "7d", "30d", "90d", "180d", "365d")
ONE_PIECE = "one-piece"
JST = timezone(timedelta(hours=9))
PLACEHOLDER_SHA = "0" * 64
ITEM_SAMPLE = 50
LIST_PER_SECTION = 5
# 改呢度 = 改 code = 要 review；缺口唔准靜靜消失。
KNOWN_GAPS = {"price.ebay": "eBay 冇 strict identity，成交停喺 2026-08-04：離群未覆蓋"}

# tcg_code from catalog_variant itself: a variant without a printing-identity
# row must still count as One Piece, not silently drop out of ②.
MAP_SQL = """
    SELECT v.id AS variant_id, COALESCE(a.public_id, v.opaque_id) AS public_id, v.tcg_code
    FROM catalog_variant v
    LEFT JOIN public_card_alias a ON a.variant_id = v.id
"""
REGISTRY_SQL = "SELECT variant_id, content_sha256 FROM market_image_rejection_registry"
APPROVAL_SQL = "SELECT variant_id, content_sha256 FROM market_image_review_approval"
ACCEPTANCE_SQL = """
    SELECT ca.variant_id, a.content_sha256, ca.accepted_by
    FROM operator_binding_freeze f
    INNER JOIN market_canonical_image_acceptance ca
      ON ca.id = f.canonical_image_acceptance_id AND ca.variant_id = f.variant_id
    INNER JOIN market_image_asset a ON a.id = ca.image_asset_id
    WHERE f.freeze_kind = 'image' AND f.acceptance_status = 'accepted'
"""


# Bounded DB wait, same session knobs the repo already uses: a per-SELECT server
# budget (new_era_db_tidy MAX_EXECUTION_TIME; rebuild_036._view_count_bounded's
# 60 s telemetry default -- report-only, must not hold the main line) and the MDL
# wait (rebuild_036 daily-accept lock_wait_timeout=30; the default is one year).
# The census runs beside live-confirm and finalise_live waits for it, so a stuck
# query must end as ANOMALY_DB_TIMEOUT, never as a silent hang.  Measured
# 2026-09-25: the whole census took 9 s.
SESSION_BOUNDS = (
    "SET SESSION MAX_EXECUTION_TIME=60000",
    "SET SESSION lock_wait_timeout=30",
)
# ER_LOCK_WAIT_TIMEOUT (MDL and row locks), ER_QUERY_TIMEOUT (MAX_EXECUTION_TIME)
DB_TIMEOUT_ERRNOS = (1205, 3024)


class AnomalyInputError(RuntimeError):
    """The census cannot judge today's board; it must never report that as dry."""


def _item(fp: str, severity: str, **fields: Any) -> dict[str, Any]:
    return {"fp": fp, "severity": severity, **fields}


def _num(value: Any) -> float | None:
    return None if value is None else float(value)


def _card_label(card: Mapping[str, Any] | None) -> dict[str, Any]:
    if not card:
        return {"rank": None, "name": None}
    names = card.get("names") or {}
    return {"rank": card.get("rank"), "name": names.get("en") or card.get("officialName")}


# ---------------------------------------------------------------- detectors


def detect_price(
    receipt_entries: Iterable[Mapping[str, Any]],
    snk_verdicts: Mapping[int, Mapping[str, Any]],
    snk_sales: Mapping[int, Sequence[Mapping[str, Any]]],
    card_of_variant: Mapping[int, Mapping[str, Any]],
) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for entry in receipt_entries:
        if entry.get("reason") != pcq.REASON_PRICE:
            continue
        variant_id = int(entry["variantId"])
        items.append(_item(
            f"price:pricecharting:{int(entry['saleObservationId'])}", "quarantined",
            source="pricecharting", variantId=variant_id, soldAt=entry.get("observedDate"),
            priceUsd=_num(entry.get("unitPriceUsd")), direction=entry.get("direction"),
            priorMedianUsd=_num(entry.get("priorMedianUsd")),
            followingMedianUsd=_num(entry.get("followingMedianUsd")),
            **_card_label(card_of_variant.get(variant_id)),
        ))
    by_id = {int(sale["saleObservationId"]): sale for sales in snk_sales.values() for sale in sales}
    for sale_id in sorted(snk_verdicts):
        verdict = snk_verdicts[sale_id]
        sale = by_id[int(sale_id)]
        variant_id = int(sale["variantId"])
        items.append(_item(
            f"price:snkrdunk:{int(sale_id)}", "report",
            source="snkrdunk", variantId=variant_id, soldAt=str(sale["soldAt"])[:10],
            priceUsd=_num(sale["unitPriceUsd"]), direction=verdict.get("direction"),
            priorMedianUsd=_num(verdict.get("priorMedianUsd")),
            followingMedianUsd=_num(verdict.get("followingMedianUsd")),
            **_card_label(card_of_variant.get(variant_id)),
        ))
    return {"status": "ok", "items": items}


def detect_image_review(
    acceptances: Iterable[Mapping[str, Any]],
    published_pairs: set[tuple[int, str]],
    approvals: set[tuple[int, str]],
    tcg_of: Mapping[int, str],
    card_of_variant: Mapping[int, Mapping[str, Any]],
) -> dict[str, Any]:
    """Published One Piece images no human picked and no human approved.

    "Not human" is the one definition of an automatic lane, so a lane added
    later is caught without a second list of lane names.
    """

    items: dict[str, dict[str, Any]] = {}
    for row in acceptances:
        variant_id = int(row["variant_id"])
        sha = str(row["content_sha256"] or "").lower()
        if str(row["accepted_by"]) == HUMAN_IMAGE_ACCEPTED_BY or tcg_of.get(variant_id) != ONE_PIECE:
            continue
        if (variant_id, sha) not in published_pairs or (variant_id, sha) in approvals:
            continue
        fp = f"image-review:{variant_id}:{sha}"
        items.setdefault(fp, _item(
            fp, "review", variantId=variant_id, sha256=sha, lane=str(row["accepted_by"]),
            **_card_label(card_of_variant.get(variant_id)),
        ))
    return {"status": "ok", "items": list(items.values())}


def detect_image_registry(
    published: Iterable[tuple[Mapping[str, Any], int, str]],
    registry: set[tuple[int, str]],
) -> dict[str, Any]:
    """(card, variant, sha) published vs human rejections, keyed on the pair."""

    rejected_for: dict[str, set[int]] = {}
    for variant_id, sha in registry:
        rejected_for.setdefault(sha, set()).add(variant_id)
    items: list[dict[str, Any]] = []
    for card, variant_id, sha in published:
        if not sha or sha == PLACEHOLDER_SHA:
            continue
        if (variant_id, sha) in registry:
            items.append(_item(f"image-registry:{variant_id}:{sha}", "critical",
                               variantId=variant_id, sha256=sha, **_card_label(card)))
        elif sha in rejected_for:
            items.append(_item(f"image-xvariant:{variant_id}:{sha}", "warn", variantId=variant_id,
                               sha256=sha, rejectedFor=sorted(rejected_for[sha]), **_card_label(card)))
    return {"status": "ok", "items": items}


def _same_price(a: Any, b: Any) -> bool:
    a, b = float(a), float(b)
    return abs(a - b) <= max(0.01, 0.005 * abs(b))


def detect_top100(
    top100: Iterable[Mapping[str, Any]],
    variant_of: Mapping[str, int],
    flagged_by_day: Mapping[tuple[int, str], Sequence[Mapping[str, Any]]],
) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for card in top100:
        card_id = str(card["id"])
        variant_id = variant_of[card_id]
        base = {"cardId": card_id, "variantId": variant_id, **_card_label(card)}
        windows = card.get("windows") or {}
        # withheld == the FE's own "implausible" verdict (live-db-snapshot.ts
        # MAX_WINDOW_RATIO): a current price with an unavailable change.
        if (card.get("pricePsa10") or {}).get("value") is not None:
            for window in WINDOWS:
                status = ((windows.get(window) or {}).get("changePct") or {}).get("status")
                if status == "unavailable":
                    items.append(_item(f"top100-withheld:{card_id}:{window}", "warn",
                                       kind="withheld", window=window, **base))
        for point in card.get("historyDaily") or []:
            if point.get("trackedSalesCount") != 1 or point.get("priceUsd") is None:
                continue
            day = str(point.get("at") or "")[:10]
            for sale in flagged_by_day.get((variant_id, day), ()):
                if not _same_price(point["priceUsd"], sale["priceUsd"]):
                    continue
                # A PC sale in the receipt must already be subtracted by the FE;
                # seeing it as the day's only sale means that subtraction failed.
                severity = "error" if sale["source"] == "pricecharting" else "warn"
                items.append(_item(f"top100-flagged-day:{card_id}:{day}", severity, kind="flagged-day",
                                   day=day, source=sale["source"], priceUsd=float(point["priceUsd"]),
                                   saleObservationId=sale["saleObservationId"], **base))
                break
        month = windows.get("30d") or {}
        count = ((month.get("trackedSales") or {}).get("count") or {}).get("value")
        if not count:
            stale = (month.get("changePct") or {}).get("status") == "ready"
            items.append(_item(f"top100-no30d:{card_id}", "warn", kind="no30d", staleReady=stale, **base))
    return {"status": "ok", "items": items}


def detect_quarantine(receipt_entries: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    items = [
        _item(f"quarantine:{int(entry['saleObservationId'])}:{entry['reason']}", "quarantined",
              variantId=int(entry["variantId"]), reason=str(entry["reason"]),
              soldAt=entry.get("observedDate"))
        for entry in receipt_entries
    ]
    return {"status": "ok", "items": items}


def flagged_sales_by_day(
    receipt_entries: Iterable[Mapping[str, Any]],
    snk_verdicts: Mapping[int, Mapping[str, Any]],
    snk_sales: Mapping[int, Sequence[Mapping[str, Any]]],
) -> dict[tuple[int, str], list[dict[str, Any]]]:
    out: dict[tuple[int, str], list[dict[str, Any]]] = {}
    for entry in receipt_entries:
        if entry.get("reason") == pcq.REASON_PRICE and entry.get("unitPriceUsd") is not None:
            out.setdefault((int(entry["variantId"]), str(entry.get("observedDate"))), []).append({
                "source": "pricecharting", "saleObservationId": int(entry["saleObservationId"]),
                "priceUsd": float(entry["unitPriceUsd"]),
            })
    for sales in snk_sales.values():
        for sale in sales:
            if int(sale["saleObservationId"]) in snk_verdicts:
                out.setdefault((int(sale["variantId"]), str(sale["soldAt"])[:10]), []).append({
                    "source": "snkrdunk", "saleObservationId": int(sale["saleObservationId"]),
                    "priceUsd": float(sale["unitPriceUsd"]),
                })
    return out


# ------------------------------------------------------------------ receipt


def assemble(
    *,
    business_date: str,
    generation: str,
    snapshot_sha256: str,
    inputs: Mapping[str, Any],
    results: Mapping[str, Mapping[str, Any]],
    previous: Mapping[str, Any] | None,
) -> dict[str, Any]:
    baseline = previous is None
    previous_detectors = (previous or {}).get("detectors") or {}
    detectors: dict[str, Any] = {}
    new_total = open_total = 0
    for name in DETECTORS:
        result = results[name]
        by_fp: dict[str, dict[str, Any]] = {}
        for item in result["items"]:
            by_fp.setdefault(item["fp"], item)
        open_fps = sorted(by_fp)
        seen = set((previous_detectors.get(name) or {}).get("open") or [])
        new = [] if baseline else [fp for fp in open_fps if fp not in seen]
        new_set = set(new)
        shown = [by_fp[fp] for fp in new] + [by_fp[fp] for fp in open_fps if fp not in new_set][:ITEM_SAMPLE]
        detectors[name] = {
            "status": result["status"], "openCount": len(open_fps), "newCount": len(new),
            "open": open_fps, "new": new, "items": shown,
        }
        new_total += len(new)
        open_total += len(open_fps)
    dry = not baseline and new_total == 0
    streak = 0
    if dry and previous is not None:
        yesterday = (date.fromisoformat(business_date) - timedelta(days=1)).isoformat()
        if previous.get("businessDate") == yesterday:
            streak = int(previous.get("consecutiveDryDays") or 0) + 1
    doc = {
        "contract": CONTRACT,
        "businessDate": business_date,
        "generation": generation,
        "snapshotSha256": snapshot_sha256,
        "inputs": dict(inputs),
        "baseline": baseline,
        "knownGaps": dict(KNOWN_GAPS),
        "detectors": detectors,
        "openCount": open_total,
        "newCount": new_total,
        "dry": dry,
        "consecutiveDryDays": streak,
    }
    doc["message"] = render(doc)
    return doc


def _money(value: Any) -> str:
    return "-" if value is None else f"${float(value):,.2f}"


def _who(item: Mapping[str, Any]) -> str:
    rank = item.get("rank")
    head = f"#{rank}" if rank else f"v{item.get('variantId')}"
    name = str(item.get("name") or "")
    return html.escape(f"{head} {name[:48]}".strip())


def _price_line(item: Mapping[str, Any]) -> str:
    return (f"• {_who(item)} {_money(item.get('priceUsd'))} vs 前 {_money(item.get('priorMedianUsd'))}"
            f" / 後 {_money(item.get('followingMedianUsd'))}"
            f"（{html.escape(str(item.get('source')))} {html.escape(str(item.get('soldAt')))}）")


def _top_line(item: Mapping[str, Any]) -> str:
    kind = item.get("kind")
    if kind == "withheld":
        tail = f"{item.get('window')} 變動被扣起"
    elif kind == "flagged-day":
        tail = f"{item.get('day')} 單筆離群日 {_money(item.get('priceUsd'))}（{item.get('source')}）"
    else:
        tail = "30 日冇成交" + ("，但 30d 變動仲標 ready" if item.get("staleReady") else "")
    return f"• {_who(item)} {html.escape(str(tail))}"


def render(doc: Mapping[str, Any]) -> str:
    d = doc["detectors"]

    def new_items(name: str, predicate=lambda _item: True) -> list[Mapping[str, Any]]:
        fresh = set(d[name]["new"])
        return [item for item in d[name]["items"] if item["fp"] in fresh and predicate(item)]

    head = [
        f"🧪 <b>CARDZ 異常普查</b> {html.escape(str(doc['businessDate']))}",
        f"generation <code>{html.escape(str(doc['generation']))}</code>",
        ("首日基線（冇前一份可比，今日唔計新）" if doc["baseline"]
         else f"新 {doc['newCount']}｜未清 {doc['openCount']}｜連續乾 {doc['consecutiveDryDays']} 日"),
    ]
    price_open = d["price"]["open"]
    body = [
        "① 價格離群："
        f"PC 已隔離 {sum(fp.startswith('price:pricecharting:') for fp in price_open)}｜"
        f"SNK 只報 {sum(fp.startswith('price:snkrdunk:') for fp in price_open)}｜"
        + "｜".join(html.escape(text) for text in doc["knownGaps"].values()),
        *(_price_line(item) for item in new_items("price")[:LIST_PER_SECTION]),
        f"② OP 自動圖待審：新 {d['imageReview']['newCount']}｜積壓 {d['imageReview']['openCount']}",
        "③ 被拒圖出街："
        f"同卡 {sum(fp.startswith('image-registry:') for fp in d['imageRegistry']['open'])}｜"
        f"跨卡 {sum(fp.startswith('image-xvariant:') for fp in d['imageRegistry']['open'])}",
        *(f"• {_who(item)} {html.escape(item['sha256'][:12])} {item['severity']}"
          for item in new_items("imageRegistry")[:LIST_PER_SECTION]),
        "④ Top100："
        f"變動被扣起 {sum(fp.startswith('top100-withheld:') for fp in d['top100']['open'])}｜"
        f"單筆離群日 {sum(fp.startswith('top100-flagged-day:') for fp in d['top100']['open'])}｜"
        f"30 日冇成交 {sum(fp.startswith('top100-no30d:') for fp in d['top100']['open'])}",
        *(_top_line(item) for item in new_items("top100")[:LIST_PER_SECTION]),
        "⑤ 新隔離：" + (", ".join(
            f"{html.escape(reason)} {n}" for reason, n in sorted(Counter(
                fp.rsplit(":", 1)[1] for fp in d["quarantine"]["new"]).items())) or "0"),
    ]
    tail = f"receipt <code>{html.escape(CURRENT.name)}</code>"
    lines = head + body
    text = "\n".join(lines + [tail])
    if len(text) <= MESSAGE_BUDGET:
        return text
    kept: list[str] = []
    for line in lines:
        dropped = len(lines) - len(kept) - 1
        more = f"…仲有 {dropped} 行 → {tail}"
        if len("\n".join(kept + [line, more])) > MESSAGE_BUDGET:
            break
        kept.append(line)
    return "\n".join(kept + [f"…仲有 {len(lines) - len(kept)} 行 → {tail}"])


# ---------------------------------------------------------------------- I/O


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError(f"not JSON serialisable: {type(value).__name__}")


def dumps(doc: Mapping[str, Any]) -> str:
    return json.dumps(doc, ensure_ascii=False, indent=1, default=_json_default) + "\n"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_bytes(text.encode("utf-8"))
    os.replace(temp, path)


def previous_receipt(business_date: str, audit_dir: Path = pcq.AUDIT_DIR) -> tuple[Path, dict] | None:
    """Newest archive of an EARLIER business date (never today's, never current)."""

    best: tuple[str, Path] | None = None
    for path in audit_dir.glob(f"{ARCHIVE_PREFIX}????-??-??_*.json"):
        day = path.name[len(ARCHIVE_PREFIX):len(ARCHIVE_PREFIX) + 10]
        if day < business_date and (best is None or path.name > best[1].name):
            best = (day, path)
    if best is None:
        return None
    return best[1], json.loads(best[1].read_text(encoding="utf-8"))


def load_snapshot(path: Path, expected_generation: str | None) -> tuple[dict, str]:
    raw = path.read_bytes()
    snapshot = json.loads(raw)
    generation = str((snapshot.get("generation") or {}).get("id") or "")
    if expected_generation and generation != expected_generation:
        raise AnomalyInputError(
            f"ANOMALY_SNAPSHOT_MISMATCH: {path} is {generation!r}, expected {expected_generation!r}"
        )
    return snapshot, sha256(raw).hexdigest()


def receipt_business_day(stamp: str) -> str | None:
    """The JST day a UTC `YYYYmmddTHHMMSSZ` stamp falls on (business dates are JST).

    Comparing the raw UTC digits instead called a receipt written at 00:30 JST
    on D (15:30Z on D-1) stale. Unparseable -> None -> stale (fail closed).
    """

    try:
        at = datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return at.astimezone(JST).date().isoformat()


def load_quarantine_receipt(business_date: str, path: Path = pcq.CURRENT_RECEIPT) -> dict:
    if not path.is_file():
        raise AnomalyInputError(f"ANOMALY_RECEIPT_MISSING: {path}")
    doc = json.loads(path.read_text(encoding="utf-8"))
    stamp = str(doc.get("generatedAt") or "")
    day = receipt_business_day(stamp)
    if not isinstance(doc.get("entries"), list) or day is None or day < business_date:
        raise AnomalyInputError(f"ANOMALY_RECEIPT_STALE: {path} generatedAt={stamp!r} < {business_date} (JST)")
    return doc


def collect(cursor: Any, snapshot: Mapping[str, Any], receipt: Mapping[str, Any]) -> dict[str, dict]:
    """Every detector's result from one read-only cursor (SELECT only)."""

    from psa10_latest_sale_quote import current_universe_variant_ids, load_candidate_sales

    cursor.execute(MAP_SQL)
    variant_of: dict[str, int] = {}
    tcg_of: dict[int, str] = {}
    for row in cursor.fetchall():
        variant_of[str(row["public_id"])] = int(row["variant_id"])
        tcg_of[int(row["variant_id"])] = str(row["tcg_code"] or "")
    cards = list(snapshot.get("top100") or []) + list(snapshot.get("watchlist") or [])
    unmapped = [str(card.get("id")) for card in cards if str(card.get("id")) not in variant_of]
    if unmapped:
        raise AnomalyInputError(f"ANOMALY_CARD_UNMAPPED: {len(unmapped)} card(s), e.g. {unmapped[:5]}")
    published = [(card, variant_of[str(card["id"])], str((card.get("image") or {}).get("sha256") or "").lower())
                 for card in cards]
    card_of_variant = {variant_id: card for card, variant_id, _sha in published}

    cursor.execute(REGISTRY_SQL)
    registry = {(int(r["variant_id"]), str(r["content_sha256"]).lower()) for r in cursor.fetchall()}
    cursor.execute(APPROVAL_SQL)
    approvals = {(int(r["variant_id"]), str(r["content_sha256"]).lower()) for r in cursor.fetchall()}
    cursor.execute(ACCEPTANCE_SQL)
    acceptances = [dict(row) for row in cursor.fetchall()]

    snk_sales = load_candidate_sales(
        cursor, source="snkrdunk", variant_ids=current_universe_variant_ids(cursor), quarantined_sale_ids=set(),
    )
    snk_verdicts = pcq.price_spike_verdicts(snk_sales)
    entries = list(receipt["entries"])
    return {
        "price": detect_price(entries, snk_verdicts, snk_sales, card_of_variant),
        "imageReview": detect_image_review(
            acceptances, {(v, s) for _c, v, s in published}, approvals, tcg_of, card_of_variant),
        "imageRegistry": detect_image_registry(published, registry),
        "top100": detect_top100(snapshot.get("top100") or [], variant_of,
                                flagged_sales_by_day(entries, snk_verdicts, snk_sales)),
        "quarantine": detect_quarantine(entries),
    }


def run(
    *,
    business_date: str,
    snapshot_path: Path,
    expected_generation: str | None = None,
    out: Path | None = None,
) -> dict[str, Any]:
    from rebuild_036 import DAILY_CREDENTIALS_ENV, connect

    snapshot, snapshot_digest = load_snapshot(Path(snapshot_path), expected_generation)
    receipt = load_quarantine_receipt(business_date)
    conn = connect(DAILY_CREDENTIALS_ENV)
    try:
        cursor = conn.cursor()
        for statement in SESSION_BOUNDS:
            cursor.execute(statement)
        cursor.execute("SET SESSION TRANSACTION READ ONLY")
        cursor.execute("START TRANSACTION READ ONLY")
        results = collect(cursor, snapshot, receipt)
    except pymysql.err.OperationalError as error:
        if error.args and error.args[0] in DB_TIMEOUT_ERRNOS:
            raise AnomalyInputError(f"ANOMALY_DB_TIMEOUT: MySQL {error.args[0]} {error.args[1:]!r}") from error
        raise
    finally:
        conn.rollback()
        conn.close()
    previous = previous_receipt(business_date)
    doc = assemble(
        business_date=business_date,
        generation=str(snapshot["generation"]["id"]),
        snapshot_sha256=snapshot_digest,
        inputs={
            "snapshot": str(snapshot_path),
            "quarantineReceipt": {"path": str(pcq.CURRENT_RECEIPT), "generatedAt": receipt.get("generatedAt")},
            "previousReceipt": None if previous is None else {
                "path": str(previous[0]), "businessDate": previous[1].get("businessDate")},
        },
        results=results,
        previous=None if previous is None else previous[1],
    )
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    doc["generatedAt"] = stamp
    text = dumps(doc)
    if out is not None:
        _write(Path(out), text)
        return doc
    _write(CURRENT, text)
    _write(pcq.AUDIT_DIR / f"{ARCHIVE_PREFIX}{business_date}_{stamp}.json", text)
    return doc


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Post-release anomaly census (report-only)")
    parser.add_argument("--business-date", required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--expected-generation", default=None)
    parser.add_argument("--out", type=Path, default=None, help="dry run: write only this path")
    args = parser.parse_args(argv)
    try:
        doc = run(business_date=args.business_date, snapshot_path=args.snapshot,
                  expected_generation=args.expected_generation, out=args.out)
    except AnomalyInputError as error:
        print(str(error), file=sys.stderr)
        return 2
    print(doc["message"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
