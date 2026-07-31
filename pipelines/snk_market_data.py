"""SNK market data pipeline for cardz-market-cap.

針對 active universe 拎 SNKRDUNK PSA 10 每日參考價、成交額、交易量。

策略：
1. 由 seed apparel IDs 開始（可以係手動指定或者 BFS 發現）
2. 逐張卡拎 master + 每日參考價 history + 最近成交
3. 計算每日成交額（trades 加總）同交易量（trades count）
4. 輸出 JSONL，可以直接 integrate 入現有 pipeline
5. 可選：將 PSA10 kline 以 exact catalog_source_identity 寫入 market_price_observation

用法：
  # 單張卡測試
  python -X utf8 pipelines/snk_market_data.py 116069 --out data/private/snk/test.jsonl

  # BFS 發現 + 批量
  python -X utf8 pipelines/snk_market_data.py --discover-from 116069 --max-ids 600 --out data/private/snk/market_data.jsonl

  # 由 ID list 批量
  python -X utf8 pipelines/snk_market_data.py --ids-file pipelines/snk_ids.txt --out data/private/snk/market_data.jsonl

  # exact-bound kline → DB（唔發明價；missing 保持 null）
  python -X utf8 pipelines/snk_market_data.py --ingest-jsonl path.jsonl --condition trading_card_single_psa10
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from collections import defaultdict
from datetime import date, datetime, time as dt_time, timezone
from pathlib import Path
from statistics import median
from typing import Any

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "pipelines"))

from failure_ledger import record_failure, record_resolution  # noqa: E402
from snkrdunk_bulk import SnkrdunkApi, bfs_discover  # noqa: E402

PSA10_CONDITION = "trading_card_single_psa10"


def stable_request_hash(item_ids: list[int], condition_code: str | None) -> str:
    payload = json.dumps(
        {"condition": condition_code, "item_ids": sorted(item_ids)},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def load_partial_rows(
    path: Path,
    requested: set[int],
    condition_code: str | None,
    *,
    allow_outside_requested: bool = False,
) -> dict[int, dict]:
    rows: dict[int, dict] = {}
    if not path.is_file():
        return rows
    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as error:
                raise RuntimeError(f"invalid resumable SNK JSONL at line {line_number}: {path}") from error
            item_id = row.get("item_id") if isinstance(row, dict) else None
            if not isinstance(item_id, int):
                raise RuntimeError(f"resumable SNK row has no numeric identity: {path}:{line_number}")
            if row.get("condition_filter") != condition_code:
                raise RuntimeError(f"resumable SNK condition mismatch: {path}:{line_number}")
            if row.get("error"):
                raise RuntimeError(f"resumable SNK row contains an upstream error: {path}:{line_number}")
            if item_id not in requested:
                if allow_outside_requested:
                    continue
                raise RuntimeError(f"resumable SNK row is outside the locked active universe: {path}:{line_number}")
            if item_id in rows:
                raise RuntimeError(f"duplicate resumable SNK item: {path}:{line_number}")
            rows[item_id] = row
    return rows


def write_state(path: Path, *, run_id: str, request_hash: str, condition_code: str | None, total: int) -> None:
    state = {
        "schemaVersion": "1.0.0",
        "runId": run_id,
        "requestSha256": request_hash,
        "condition": condition_code,
        "total": total,
    }
    temporary = path.with_name(f".{path.name}.{os.getpid()}.next")
    temporary.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)


QUANTITY_LABEL = re.compile(r"^(\d+)枚$")


def trade_quantity(trade: dict) -> int | None:
    quantity = trade.get("quantity")
    if isinstance(quantity, int) and not isinstance(quantity, bool) and quantity > 0:
        return quantity
    match = QUANTITY_LABEL.fullmatch(str(trade.get("label") or "").strip())
    if match:
        return int(match.group(1))
    return None


def aggregate_daily_trades(trades: list[dict]) -> dict[str, dict]:
    """Group uniquely identified completed PSA 10 trades by UTC day.

    The endpoint is already condition-scoped by the caller.  ``usedMinPrice``
    is intentionally not considered here: it is a live listing ask, not a
    completed sale.
    """
    daily = defaultdict(lambda: {"count": 0, "value_jpy": 0})
    seen: set[str] = set()
    for t in trades:
        sold_at = t.get("soldAt") if isinstance(t, dict) else None
        price = t.get("price") if isinstance(t, dict) else None
        quantity = trade_quantity(t) if isinstance(t, dict) else None
        if (
            not isinstance(sold_at, str)
            or "T" not in sold_at
            or not isinstance(price, (int, float))
            or price <= 0
            or quantity is None
        ):
            continue
        try:
            day = datetime.fromisoformat(sold_at.replace("Z", "+00:00")).date().isoformat()
        except ValueError:
            continue
        transaction_id = t.get("transactionId")
        if isinstance(transaction_id, str) and transaction_id:
            sale_key = f"id:{transaction_id}"
        else:
            sale_key = "payload:" + hashlib.sha256(
                json.dumps(
                    {"soldAt": sold_at, "price": price, "quantity": quantity},
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
        if sale_key in seen:
            continue
        seen.add(sale_key)
        daily[day]["count"] += quantity
        daily[day]["value_jpy"] += round(float(price))
    return dict(sorted(daily.items()))


def one_card_variant_id(history: dict) -> int:
    """Return SNK's per-product 1-card variant; never infer it from price."""

    options = ((history.get("filters") or {}).get("variants") or {}).get("options") or []
    matches = [
        option.get("id")
        for option in options
        if isinstance(option, dict) and str(option.get("name") or "").strip() == "1枚"
    ]
    if len(matches) != 1 or not isinstance(matches[0], int):
        raise RuntimeError("SNK PSA10 history has no unique 1枚 variant")
    return matches[0]


def normalized_trade_unit_prices(trades: list[dict]) -> dict[str, float]:
    """Return a robust daily single-card price from labelled SNK transactions."""

    prices: dict[str, list[float]] = defaultdict(list)
    for trade in trades:
        if not isinstance(trade, dict):
            continue
        sold_at = trade.get("soldAt")
        total_price = trade.get("price")
        quantity = trade_quantity(trade)
        if (
            not isinstance(sold_at, str)
            or "T" not in sold_at
            or not isinstance(total_price, (int, float))
            or total_price <= 0
            or quantity is None
        ):
            continue
        try:
            day = datetime.fromisoformat(sold_at.replace("Z", "+00:00")).date().isoformat()
        except ValueError:
            continue
        prices[day].append(float(total_price) / quantity)
    return {day: float(median(values)) for day, values in sorted(prices.items())}


def pull_market_data(
    api: SnkrdunkApi,
    item_id: int,
    condition_code: str | None = None,
) -> dict:
    """Pull one card's market data: master + daily reference history + sales.

    ``chart.lines.points`` is retained as a provider daily reference series;
    this collector never invents OHLC candles or substitutes an ask for a sale.
    """
    master = api.get_master(item_id)
    pcid = master.get("productCatalogId")
    if not pcid:
        return {"item_id": item_id, "error": "no productCatalogId"}

    variant_probe = api.get_trading_history(pcid, range_="all", condition_code=condition_code)
    single_variant_id = one_card_variant_id(variant_probe)
    history = api.get_trading_history(
        pcid,
        range_="all",
        condition_code=condition_code,
        variant_id=single_variant_id,
    )
    points = history.get("chart", {}).get("lines", [{}])[0].get("points", [])
    # The single-variant response intentionally excludes bundles. Use the
    # condition-scoped probe's labelled completed trades and normalize their
    # total values back to a per-card price.
    trades = variant_probe.get("trades", [])

    daily_activity = aggregate_daily_trades(trades)

    kline_by_day: dict[str, float] = {}
    for point in points:
        timestamp = point.get("timestamp") if isinstance(point, dict) else None
        price = point.get("price") if isinstance(point, dict) else None
        if not isinstance(timestamp, (int, float)) or not isinstance(price, (int, float)) or price <= 0:
            continue
        day = datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        kline_by_day[day] = float(price)
    kline_by_day.update(normalized_trade_unit_prices(trades))

    return {
        "item_id": item_id,
        "product_catalog_id": pcid,
        "product_number": master.get("productNumber"),
        "name": master.get("name"),
        "localized_name": master.get("localizedName"),
        "image_url": (master.get("primaryMedia") or {}).get("imageUrl"),
        "released_at": master.get("releasedAt"),
        "used_min_price": master.get("usedMinPrice"),
        "used_listing_count": master.get("usedListingCount"),
        "condition_filter": condition_code,
        "quantity_filter": "per_card_normalized",
        "quantity_variant_id": single_variant_id,
        "kline": [{"date": day, "price_jpy": price} for day, price in sorted(kline_by_day.items())],
        "recent_trades": trades,
        "daily_activity": daily_activity,
        "source_payload": {
            "master": master,
            "condition_history": variant_probe,
            "single_card_history": history,
        },
        "fetched_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }


def run(
    item_ids: list[int],
    out_path: Path,
    delay: float,
    condition_code: str | None,
    run_id: str,
) -> dict:
    requested = set(item_ids)
    request_hash = stable_request_hash(item_ids, condition_code)
    if out_path.exists():
        existing = load_partial_rows(out_path, requested, condition_code)
        if set(existing) == requested:
            return {
                "run_id": run_id,
                "total": len(item_ids),
                "ok": len(item_ids),
                "failed": 0,
                "out": str(out_path),
                "replayed": True,
                "finished_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            }
        raise RuntimeError(f"existing SNK run is incomplete or belongs to another condition: {out_path}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    partial = out_path.with_suffix(out_path.suffix + ".partial")
    state_path = partial.with_suffix(partial.suffix + ".state.json")

    # Recover the pre-resume implementation's process-id temporary file once.
    # It is valid only when every decoded row belongs to this exact request.
    legacy_candidates = sorted(
        out_path.parent.glob(f".{out_path.name}.*.next"),
        key=lambda value: value.stat().st_size,
        reverse=True,
    )
    if not partial.exists() and legacy_candidates:
        legacy = legacy_candidates[0]
        recovered = load_partial_rows(
            legacy,
            requested,
            condition_code,
            allow_outside_requested=True,
        )
        with partial.open("w", encoding="utf-8") as destination:
            for item_id in item_ids:
                row = recovered.get(item_id)
                if row is not None:
                    destination.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    existing = load_partial_rows(partial, requested, condition_code)
    if state_path.is_file():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if state.get("requestSha256") != request_hash or state.get("condition") != condition_code:
            raise RuntimeError(f"resumable SNK state belongs to another locked universe: {state_path}")
        if state.get("runId") != run_id:
            raise RuntimeError(f"resumable SNK state belongs to another run id: {state_path}")
    else:
        row_run_ids = {str(row.get("run_id") or "") for row in existing.values()}
        if row_run_ids and row_run_ids != {run_id}:
            raise RuntimeError(f"resumable SNK rows belong to another run id: {partial}")
        write_state(
            state_path,
            run_id=run_id,
            request_hash=request_hash,
            condition_code=condition_code,
            total=len(item_ids),
        )

    missing = [item_id for item_id in item_ids if item_id not in existing]
    api = SnkrdunkApi(delay=delay)
    try:
        version = api.fetch_x_version() if missing else None
        if missing:
            record_resolution(
                source="snkrdunk",
                stage="open_collection_session",
                script=__file__,
                item_key=request_hash,
                run_id=run_id,
                resolution="session_ready",
            )
    except Exception as error:
        record_failure(
            source="snkrdunk",
            stage="open_collection_session",
            script=__file__,
            item_key=request_hash,
            reason_code="session_open_failed",
            message=str(error),
            retryable=True,
            run_id=run_id,
            context={"condition": condition_code, "requested": len(item_ids)},
            evidence_paths=[partial, state_path],
            next_action="retry",
            error_type=type(error).__name__,
        )
        raise
    if version:
        print(f"[snk_market_data] x-version acquired")

    ok, failed = len(existing), 0
    with partial.open("a", encoding="utf-8") as f:
        for offset, iid in enumerate(missing, 1):
            try:
                data = pull_market_data(api, iid, condition_code=condition_code)
                if data.get("error"):
                    raise RuntimeError(str(data["error"]))
                data["run_id"] = run_id
                f.write(json.dumps(data, ensure_ascii=False, sort_keys=True) + "\n")
                f.flush()
                record_resolution(
                    source="snkrdunk",
                    stage="collect_market_data",
                    script=__file__,
                    item_key=iid,
                    run_id=run_id,
                    resolution="accepted_payload",
                    context={
                        "condition": condition_code,
                        "klinePoints": len(data.get("kline", [])),
                        "trades": len(data.get("recent_trades", [])),
                    },
                    evidence_paths=[partial],
                )
                ok += 1
                kline_points = len(data.get("kline", []))
                trades = len(data.get("recent_trades", []))
                print(
                    f"  [{len(existing) + offset}/{len(item_ids)}] "
                    f"{data.get('product_number')} — {kline_points} kline, {trades} trades"
                )
            except Exception as e:
                failed += 1
                record_failure(
                    source="snkrdunk",
                    stage="collect_market_data",
                    script=__file__,
                    item_key=iid,
                    reason_code="item_collect_failed",
                    message=str(e),
                    retryable=True,
                    run_id=run_id,
                    url=f"https://snkrdunk.com/en/apparels/{iid}",
                    context={"condition": condition_code},
                    evidence_paths=[partial, state_path],
                    next_action="retry",
                    error_type=type(e).__name__,
                )
                print(f"  FAIL active item: {e}", file=sys.stderr)

    completed = load_partial_rows(partial, requested, condition_code)
    if not failed and set(completed) == requested:
        os.replace(partial, out_path)
        state_path.unlink(missing_ok=True)
    elif not failed:
        failed = len(requested - set(completed))

    return {
        "x_version": version,
        "run_id": run_id,
        "total": len(item_ids),
        "ok": len(completed),
        "failed": failed,
        "remaining": len(requested - set(completed)),
        "request_sha256": request_hash,
        "out": str(out_path),
        "replayed": False,
        "finished_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }


def _load_backend_env() -> None:
    env = BASE_DIR / "data/runtime/config/backend.env"
    if not env.is_file():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())
    os.environ.setdefault("CARDZ_DB_HOST", "127.0.0.1")


def _db_connect():
    import pymysql

    _load_backend_env()
    return pymysql.connect(
        host=os.environ.get("CARDZ_DB_HOST", "127.0.0.1"),
        port=int(os.environ.get("CARDZ_DB_PORT", "3308")),
        user=os.environ["CARDZ_DB_USER"],
        password=os.environ["CARDZ_DB_PASSWORD"],
        database=os.environ["CARDZ_DB_NAME"],
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )


def _parse_variant_allowlist(path: Path | None) -> set[int] | None:
    if path is None:
        return None
    text = path.read_text(encoding="utf-8")
    allowed: set[int] = set()
    try:
        document = json.loads(text)
    except json.JSONDecodeError:
        document = None
    if isinstance(document, dict):
        cards = document.get("cards") or document.get("variants") or []
        if isinstance(cards, list):
            for row in cards:
                if isinstance(row, dict) and row.get("variantId") is not None:
                    allowed.add(int(row["variantId"]))
                elif isinstance(row, int):
                    allowed.add(int(row))
    elif isinstance(document, list):
        for row in document:
            if isinstance(row, dict) and row.get("variantId") is not None:
                allowed.add(int(row["variantId"]))
            elif isinstance(row, int):
                allowed.add(int(row))
    if allowed:
        return allowed
    for line in text.splitlines():
        line = line.strip()
        if line.isdigit():
            allowed.add(int(line))
    return allowed or None


def load_exact_snk_item_to_variant(
    *,
    variant_allowlist: set[int] | None = None,
) -> dict[int, int]:
    """Map SNK apparel id → variant_id for exact catalog binds only."""

    conn = _db_connect()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT external_entity_id, variant_id, match_status
            FROM catalog_source_identity
            WHERE source_code IN ('snkrdunk', 'snk', 'snk_psa10')
              AND LOWER(match_status) = 'exact'
            """
        )
        mapping: dict[int, int] = {}
        for row in cur.fetchall():
            try:
                item_id = int(str(row["external_entity_id"]).strip())
                variant_id = int(row["variant_id"])
            except (TypeError, ValueError):
                continue
            if variant_allowlist is not None and variant_id not in variant_allowlist:
                continue
            # first exact wins; collisions stay first-bound
            mapping.setdefault(item_id, variant_id)
        return mapping
    finally:
        conn.close()


def ingest_kline_jsonl(
    jsonl_path: Path,
    *,
    condition_code: str = PSA10_CONDITION,
    run_key: str | None = None,
    variant_allowlist: set[int] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Write PSA10 kline points into market_price_observation.

    Fail-closed rules:
    - only rows with condition_filter == PSA10 condition
    - only item_ids with exact catalog_source_identity
    - never invent prices; empty kline → skipped (missing stays missing)
    - historical effective_at uses observed day EOD so same-method 30d anchors work;
      the newest point uses ingest-now so current-price freshness stays ready
    """

    if condition_code != PSA10_CONDITION:
        raise RuntimeError(f"ingest only accepts {PSA10_CONDITION}, got {condition_code}")
    if not jsonl_path.is_file():
        raise RuntimeError(f"ingest jsonl missing: {jsonl_path}")

    item_to_variant = load_exact_snk_item_to_variant(variant_allowlist=variant_allowlist)
    rows: list[dict[str, Any]] = []
    with jsonl_path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as error:
                raise RuntimeError(f"invalid SNK jsonl at line {line_number}: {jsonl_path}") from error
            if not isinstance(row, dict):
                raise RuntimeError(f"SNK jsonl row is not an object: {jsonl_path}:{line_number}")
            rows.append(row)

    conn = _db_connect()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT rate FROM market_fx_rate_observation
        WHERE base_currency='USD' AND quote_currency='JPY'
        ORDER BY effective_date DESC, id DESC
        LIMIT 1
        """
    )
    fx_row = cur.fetchone()
    jpy_per_usd = float(fx_row["rate"]) if fx_row and fx_row.get("rate") is not None else None
    if jpy_per_usd is None or jpy_per_usd <= 0:
        conn.close()
        raise RuntimeError("USD/JPY FX rate missing; refuse to invent conversion")

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    key = run_key or f"snk_kline_ingest_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    seed = hashlib.sha256(key.encode("utf-8")).hexdigest()

    stats: dict[str, Any] = {
        "runKey": key,
        "jsonl": str(jsonl_path),
        "jpyPerUsd": jpy_per_usd,
        "rowsSeen": len(rows),
        "cardsAccepted": 0,
        "pricePoints": 0,
        "skippedNoExactIdentity": 0,
        "skippedCondition": 0,
        "skippedEmptyKline": 0,
        "skippedErrorRow": 0,
        "skippedAllowlist": 0,
        "dryRun": dry_run,
        "acceptedItemIds": [],
        "skipped": [],
    }

    if dry_run:
        for row in rows:
            item_id = row.get("item_id")
            if row.get("error"):
                stats["skippedErrorRow"] += 1
                continue
            if row.get("condition_filter") != condition_code:
                stats["skippedCondition"] += 1
                continue
            if not isinstance(item_id, int) or item_id not in item_to_variant:
                stats["skippedNoExactIdentity"] += 1
                continue
            variant_id = item_to_variant[item_id]
            if variant_allowlist is not None and variant_id not in variant_allowlist:
                stats["skippedAllowlist"] += 1
                continue
            kline = row.get("kline") or []
            valid = [
                pt
                for pt in kline
                if isinstance(pt, dict)
                and isinstance(pt.get("price_jpy"), (int, float))
                and float(pt["price_jpy"]) > 0
                and pt.get("date")
            ]
            if not valid:
                stats["skippedEmptyKline"] += 1
                stats["skipped"].append({"itemId": item_id, "reason": "empty_kline"})
                continue
            stats["cardsAccepted"] += 1
            stats["pricePoints"] += len(valid)
            stats["acceptedItemIds"].append(item_id)
        conn.close()
        print(json.dumps(stats, ensure_ascii=False, sort_keys=True))
        return stats

    cur.execute(
        """
        INSERT INTO market_ingest_run
            (run_key, source_code, ingest_mode, effective_at, payload_sha256, manifest_sha256,
             status, observed_count, accepted_count, quarantined_count, rejected_count, started_at)
        VALUES (%s, 'snk_psa10', 'incremental', %s, %s, %s, 'running', 0, 0, 0, 0, %s)
        """,
        (key, now, seed, seed, now),
    )
    run_id = cur.lastrowid
    stats["runId"] = run_id
    price_rows_to_write: list[tuple[Any, ...]] = []
    resolved_cards: list[tuple[int, int, int]] = []

    for row in rows:
        item_id = row.get("item_id")
        if row.get("error"):
            stats["skippedErrorRow"] += 1
            continue
        if row.get("condition_filter") != condition_code:
            stats["skippedCondition"] += 1
            continue
        if not isinstance(item_id, int) or item_id not in item_to_variant:
            stats["skippedNoExactIdentity"] += 1
            stats["skipped"].append({"itemId": item_id, "reason": "no_exact_identity"})
            continue
        variant_id = item_to_variant[item_id]
        if variant_allowlist is not None and variant_id not in variant_allowlist:
            stats["skippedAllowlist"] += 1
            continue
        kline = row.get("kline") or []
        valid: list[tuple[str, float]] = []
        for pt in kline:
            if not isinstance(pt, dict):
                continue
            day = str(pt.get("date") or "").strip()
            price_jpy = pt.get("price_jpy")
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
                continue
            if not isinstance(price_jpy, (int, float)) or float(price_jpy) <= 0:
                continue
            valid.append((day, float(price_jpy)))
        if not valid:
            stats["skippedEmptyKline"] += 1
            stats["skipped"].append({"itemId": item_id, "variantId": variant_id, "reason": "empty_kline"})
            record_failure(
                source="snkrdunk",
                stage="normalize_psa10_price",
                script=__file__,
                item_key=item_id,
                reason_code="empty_kline",
                message="source snapshot has no valid PSA 10 price history",
                retryable=True,
                run_id=key,
                context={"variantId": variant_id},
                evidence_paths=[jsonl_path],
                next_action="retry_later_or_cross_source",
            )
            continue

        latest_day = max(day for day, _ in valid)
        # Fail-closed freshness stamp (2026-07-30 poison incident):
        # never promote an ancient last-trade to effective_at=now. QC PRICE_MAX_AGE
        # is 48h; allow a short grace window for timezone/EOD lag only.
        # Stale latest day is written as honest EOD (may stay release-blocked).
        STALE_LATEST_MAX_DAYS = 7
        latest_age_days = (now.date() - date.fromisoformat(latest_day)).days
        stamp_latest_as_now = latest_age_days <= STALE_LATEST_MAX_DAYS
        if not stamp_latest_as_now:
            stats.setdefault("staleLatestEodOnly", 0)
            stats["staleLatestEodOnly"] += 1
            stats.setdefault("staleLatestSamples", [])
            if len(stats["staleLatestSamples"]) < 20:
                stats["staleLatestSamples"].append(
                    {
                        "itemId": item_id,
                        "variantId": variant_id,
                        "latestDay": latest_day,
                        "ageDays": latest_age_days,
                    }
                )
        stats["cardsAccepted"] += 1
        stats["acceptedItemIds"].append(item_id)
        for day, price_jpy in valid:
            price_usd = round(price_jpy / jpy_per_usd, 6)
            if day == latest_day and stamp_latest_as_now:
                effective = now
            else:
                # EOD UTC on observed day — same-method anchors use effective_at date.
                observed = date.fromisoformat(day)
                effective = datetime.combine(observed, dt_time(23, 59, 59))
            payload = {
                "source": "snk_market_data_kline",
                "itemId": item_id,
                "priceJpy": price_jpy,
                "priceUsd": price_usd,
                "observedDate": day,
                "stampedAsNow": bool(day == latest_day and stamp_latest_as_now),
            }
            payload_hash = hashlib.sha256(
                json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            price_rows_to_write.append(
                (
                    run_id,
                    variant_id,
                    "snk_psa10",
                    day,
                    effective,
                    price_usd,
                    price_jpy,
                    "JPY",
                    50,
                    "ready",
                    payload_hash,
                )
            )
            stats["pricePoints"] += 1
        resolved_cards.append((item_id, variant_id, len(valid)))

    price_upsert = """
        INSERT INTO market_price_observation
            (run_id, variant_id, source_code, observed_date, effective_at, price_usd,
             native_price, native_currency, source_priority, metric_status, payload_sha256)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            run_id=VALUES(run_id),
            effective_at=VALUES(effective_at),
            price_usd=VALUES(price_usd),
            native_price=VALUES(native_price),
            native_currency=VALUES(native_currency),
            source_priority=VALUES(source_priority),
            metric_status=VALUES(metric_status),
            payload_sha256=VALUES(payload_sha256)
    """
    for offset in range(0, len(price_rows_to_write), 1000):
        cur.executemany(
            price_upsert,
            price_rows_to_write[offset : offset + 1000],
        )

    cur.execute(
        """
        UPDATE market_ingest_run
        SET status='complete', observed_count=%s, accepted_count=%s, completed_at=%s
        WHERE id=%s
        """,
        (stats["pricePoints"], stats["pricePoints"], now, run_id),
    )
    conn.commit()
    conn.close()
    for item_id, variant_id, point_count in resolved_cards:
        record_resolution(
            source="snkrdunk",
            stage="normalize_psa10_price",
            script=__file__,
            item_key=item_id,
            run_id=key,
            resolution="accepted_psa10_price_history",
            context={"variantId": variant_id, "pricePoints": point_count},
            evidence_paths=[jsonl_path],
        )
    print(json.dumps(stats, ensure_ascii=False, sort_keys=True))
    return stats


def main() -> None:
    ap = argparse.ArgumentParser(description="SNK market data pipeline")
    ap.add_argument("ids", nargs="*", type=int, help="apparel IDs to pull")
    ap.add_argument("--ids-file", type=Path, help="text file with one ID per line")
    ap.add_argument("--discover-from", type=int, nargs="*", default=[],
                    help="seed IDs for BFS discovery")
    ap.add_argument("--max-ids", type=int, default=600)
    ap.add_argument("--delay", type=float, default=1.5)
    ap.add_argument("--condition", default="trading_card_single_psa10",
                    help="condition_code (e.g. trading_card_single_psa10)")
    ap.add_argument("--run-id", help="immutable run id; defaults to the current UTC timestamp")
    ap.add_argument("--discover-only-out", type=Path,
                    help="write discovered ids only and exit without fetching card payloads")
    ap.add_argument("--out", type=Path, help="JSONL output for harvest mode")
    ap.add_argument(
        "--ingest-jsonl",
        type=Path,
        help="ingest PSA10 kline JSONL into market_price_observation for exact SNK binds only",
    )
    ap.add_argument(
        "--variant-allowlist",
        type=Path,
        help="optional worklist JSON/ids limiting which variants may receive price writes",
    )
    ap.add_argument("--dry-run", action="store_true", help="with --ingest-jsonl, report only")
    args = ap.parse_args()

    if args.ingest_jsonl is not None:
        allowlist = _parse_variant_allowlist(args.variant_allowlist)
        report = ingest_kline_jsonl(
            args.ingest_jsonl,
            condition_code=args.condition,
            run_key=args.run_id,
            variant_allowlist=allowlist,
            dry_run=args.dry_run,
        )
        if args.out is not None:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        sys.exit(0 if report.get("cardsAccepted", 0) >= 0 else 1)

    if args.out is None and args.discover_only_out is None:
        ap.error("--out is required unless --ingest-jsonl or --discover-only-out is used")

    item_ids = list(args.ids)

    if args.ids_file:
        item_ids.extend(
            int(line.strip())
            for line in args.ids_file.read_text().splitlines()
            if line.strip() and line.strip().isdigit()
        )

    if args.discover_from:
        api = SnkrdunkApi(delay=args.delay)
        discovered = bfs_discover(api, args.discover_from, max_ids=args.max_ids)
        print(f"[snk_market_data] BFS discovered {len(discovered)} IDs")
        item_ids.extend(discovered)

    if args.discover_only_out is not None:
        item_ids = list(dict.fromkeys(item_ids))
        args.discover_only_out.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.discover_only_out.with_name(f".{args.discover_only_out.name}.{os.getpid()}.next")
        temporary.write_text("\n".join(str(value) for value in item_ids) + "\n", encoding="ascii")
        os.replace(temporary, args.discover_only_out)
        print(json.dumps({"discovered": len(item_ids), "out": str(args.discover_only_out)}, sort_keys=True))
        return

    # dedupe
    item_ids = list(dict.fromkeys(item_ids))

    if not item_ids:
        print("ERROR: no IDs to pull", file=sys.stderr)
        sys.exit(1)

    run_id = args.run_id or datetime.now(timezone.utc).strftime("snk_%Y%m%dT%H%M%SZ")
    report = run(item_ids, args.out, args.delay, args.condition, run_id)
    report_path = args.out.parent / f"{args.out.stem}_report.json"
    report_tmp = report_path.with_name(f".{report_path.name}.{os.getpid()}.next")
    report_tmp.write_text(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    os.replace(report_tmp, report_path)
    print(f"[snk_market_data] report -> {report_path}")

    sys.exit(0 if report["failed"] == 0 else 1)


if __name__ == "__main__":
    try:
        main()
    except SystemExit as exit_error:
        if exit_error.code in (None, 0):
            record_resolution(
                source="snkrdunk",
                stage="run",
                script=__file__,
                item_key="snk-market-data",
                resolution="run_completed",
            )
        raise
    except Exception as error:
        record_failure(
            source="snkrdunk",
            stage="run",
            script=__file__,
            item_key="snk-market-data",
            reason_code="run_failed",
            message="SNK market-data run aborted",
            retryable=True,
            next_action="agent_review_then_retry",
            error_type=type(error).__name__,
        )
        raise
    else:
        record_resolution(
            source="snkrdunk",
            stage="run",
            script=__file__,
            item_key="snk-market-data",
            resolution="run_completed",
        )
