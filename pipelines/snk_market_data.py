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

  # 一次覆蓋本機全部完整 SNK harvest（唔開 browser／唔發網絡請求）
  python -X utf8 pipelines/snk_market_data.py --ingest-archive-dir data/runtime/operator/collect

  # 將垃圾桶／舊本機嘅 exact PSA10 kline 收斂到現行 recovery archive
  python -X utf8 pipelines/snk_market_data.py --recover-local-history \
    --recovery-source <jsonl-or-dir> --recovery-archive-dir data/runtime/operator/collect/recovered-033 \
    --recovery-report data/editorial/one-time-033-local-snk-recovery.json
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
from datetime import date, datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from statistics import median
from typing import Any

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "pipelines"))

from failure_ledger import record_failure, record_resolution  # noqa: E402
from snkrdunk_bulk import SnkrdunkApiPool, SnkrdunkApi, bfs_discover  # noqa: E402

PSA10_CONDITION = "trading_card_single_psa10"
# Incremental kline ingest re-writes only this many days behind the last
# persisted day so a revised recent candle still replaces its old value.
KLINE_TAIL_REWRITE_DAYS = 3
SNK_KLINE_ARCHIVE_PATTERN = re.compile(r"^snk(?:_price)?_harvest.*\.jsonl$", re.IGNORECASE)
SNK_SOURCE_TIMESTAMP_PATTERN = re.compile(r"_(\d{8}T\d{6}(?:\d{6})?Z)")


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
    # Both responses carry a trades list, and they are not interchangeable.
    # The probe is the all-quantity CONTAINER: on the 809-item harvest it holds
    # 9,684 trades of which 61 are bundles (2枚 x42 up to 9枚), and dividing a
    # lot total by its count invents a unit price the market never paid --
    # item 91396's 2026-07-03 came out at 233,333 JPY from one 9枚 lot. Both
    # responses also cap at 20 rows (472 items sit at the cap), so the bundles
    # push out 60 genuine 1枚 trades across 43 items. Price therefore reads the
    # per-variant list, which is all 1枚 and a strict superset of the
    # container's singles. Volume stays on the container: counting every
    # completed sale is exactly what it is for.
    container_trades = variant_probe.get("trades", [])
    single_card_trades = history.get("trades", [])

    daily_activity = aggregate_daily_trades(container_trades)

    kline_by_day: dict[str, float] = {}
    for point in points:
        timestamp = point.get("timestamp") if isinstance(point, dict) else None
        price = point.get("price") if isinstance(point, dict) else None
        if not isinstance(timestamp, (int, float)) or not isinstance(price, (int, float)) or price <= 0:
            continue
        day = datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        kline_by_day[day] = float(price)
    kline_by_day.update(normalized_trade_unit_prices(single_card_trades))

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
        "recent_trades": single_card_trades,
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
    workers: int = 16,
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

    # A worklist that CHANGED is a superseded capture, not an accident.
    #
    # The guard below is right that rows fetched for one universe must never be
    # laundered into another. Its answer was to stop and wait for a human to
    # move three files aside, and that has now been the answer twice for the
    # same cause both times: identity discovery bound more cards, so the
    # request hash moved. Nothing about that needs a decision. Retire the old
    # capture -- moved, never deleted, so its rows stay auditable -- and let
    # this one start clean. A differing CONDITION still raises: that is a
    # caller asking for something else, not a universe that grew.
    if state_path.is_file():
        prior = json.loads(state_path.read_text(encoding="utf-8"))
        if (
            prior.get("condition") == condition_code
            and prior.get("requestSha256") != request_hash
        ):
            attic = out_path.parent / (
                f"superseded-{str(prior.get('runId') or 'unknown')}"
                f"-{str(prior.get('requestSha256') or 'unknown')[:12]}"
            )
            attic.mkdir(parents=True, exist_ok=True)
            for stale in (partial, state_path, *legacy_candidates):
                if stale.exists():
                    stale.replace(attic / stale.name)

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
    workers = max(1, int(workers or 1))
    # Concurrent by default: SNK is free JSON/CloudFront, not CDP.
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
                context={"workers": workers, "delay": delay},
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
            context={"condition": condition_code, "requested": len(item_ids), "workers": workers},
            evidence_paths=[partial, state_path],
            next_action="retry",
            error_type=type(error).__name__,
        )
        raise
    if version:
        print(f"[snk_market_data] x-version acquired workers={workers} delay={delay}")

    ok, failed = len(existing), 0
    write_lock = Lock()
    done_count = len(existing)

    def _work(iid: int) -> tuple[int, dict | None, str | None]:
        # thread-local session via dedicated client
        client = SnkrdunkApi(delay=delay)
        if version:
            client.session.headers["x-version"] = version
        try:
            data = pull_market_data(client, iid, condition_code=condition_code)
            if data.get("error"):
                return iid, None, str(data["error"])
            data["run_id"] = run_id
            return iid, data, None
        except Exception as e:  # noqa: BLE001
            return iid, None, f"{type(e).__name__}:{e}"

    with partial.open("a", encoding="utf-8") as f:
        if workers == 1 or len(missing) <= 1:
            iterator = ((_work(iid)) for iid in missing)
            for iid, data, err in iterator:
                if err or data is None:
                    failed += 1
                    record_failure(
                        source="snkrdunk",
                        stage="collect_market_data",
                        script=__file__,
                        item_key=iid,
                        reason_code="item_collect_failed",
                        message=str(err),
                        retryable=True,
                        run_id=run_id,
                        url=f"https://snkrdunk.com/en/apparels/{iid}",
                        context={"condition": condition_code},
                        evidence_paths=[partial, state_path],
                        next_action="retry",
                        error_type="CollectError",
                    )
                    print(f"  FAIL active item: {err}", file=sys.stderr)
                    continue
                f.write(json.dumps(data, ensure_ascii=False, sort_keys=True) + "\n")
                f.flush()
                ok += 1
                done_count += 1
                print(
                    f"  [{done_count}/{len(item_ids)}] "
                    f"{data.get('product_number')} — {len(data.get('kline', []))} kline, {len(data.get('recent_trades', []))} trades"
                )
        else:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futs = {ex.submit(_work, iid): iid for iid in missing}
                for fut in as_completed(futs):
                    iid, data, err = fut.result()
                    if err or data is None:
                        with write_lock:
                            failed += 1
                        record_failure(
                            source="snkrdunk",
                            stage="collect_market_data",
                            script=__file__,
                            item_key=iid,
                            reason_code="item_collect_failed",
                            message=str(err),
                            retryable=True,
                            run_id=run_id,
                            url=f"https://snkrdunk.com/en/apparels/{iid}",
                            context={"condition": condition_code, "workers": workers},
                            evidence_paths=[partial, state_path],
                            next_action="retry",
                            error_type="CollectError",
                        )
                        print(f"  FAIL active item {iid}: {err}", file=sys.stderr)
                        continue
                    with write_lock:
                        f.write(json.dumps(data, ensure_ascii=False, sort_keys=True) + "\n")
                        f.flush()
                        ok += 1
                        done_count += 1
                        print(
                            f"  [{done_count}/{len(item_ids)}] "
                            f"{data.get('product_number')} — {len(data.get('kline', []))} kline, {len(data.get('recent_trades', []))} trades"
                        )

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
    """Map SNK apparel id → variant_id for exact SNKRDUNK identities.

    The collection registry is already restricted to the locked active cohort.
    A source freeze selects the public product source; it must not suppress a
    separately exact provider identity from incremental evidence collection.
    """

    conn = _db_connect()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT identity.external_entity_id, identity.variant_id
            FROM operator_strict_source_identity AS identity
            INNER JOIN (
              SELECT metric.variant_id
              FROM market_canonical_metric_acceptance AS metric
              INNER JOIN (
                SELECT ranking_generation_sha256
                FROM market_canonical_metric_acceptance
                ORDER BY accepted_at DESC, id DESC
                LIMIT 1
              ) AS current_generation
                ON current_generation.ranking_generation_sha256=metric.ranking_generation_sha256
            ) AS active_cohort
              ON active_cohort.variant_id=identity.variant_id
            WHERE identity.source_code='snkrdunk'
              AND LOWER(identity.match_status)='exact'
              AND identity.external_entity_id IS NOT NULL
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
            prior = mapping.get(item_id)
            if prior is not None and prior != variant_id:
                raise RuntimeError(f"ambiguous accepted SNK identity: {item_id}")
            mapping[item_id] = variant_id
        return mapping
    finally:
        conn.close()


def source_observed_at(row: dict[str, Any]) -> datetime:
    raw = str(row.get("fetched_at") or "").strip()
    if not raw:
        raise RuntimeError("SNK source row has no fetched_at provenance")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as error:
        raise RuntimeError(f"invalid SNK fetched_at provenance: {raw}") from error
    if parsed.tzinfo is None:
        raise RuntimeError("SNK fetched_at provenance must include timezone")
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def _valid_kline_points(row: dict[str, Any]) -> list[tuple[str, float]]:
    points: list[tuple[str, float]] = []
    for point in row.get("kline") or []:
        if not isinstance(point, dict):
            continue
        raw_day = str(point.get("date") or "").strip()
        price_jpy = point.get("price_jpy")
        if not raw_day or not isinstance(price_jpy, (int, float)) or float(price_jpy) <= 0:
            continue
        try:
            date.fromisoformat(raw_day)
        except ValueError:
            continue
        points.append((raw_day, float(price_jpy)))
    return points


def _recovery_source_order(path: Path) -> tuple[str, str]:
    matched = SNK_SOURCE_TIMESTAMP_PATTERN.search(path.name)
    if matched:
        return (matched.group(1), str(path).lower())
    return (datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).strftime("%Y%m%dT%H%M%SZ"), str(path).lower())


def discover_local_recovery_paths(sources: list[Path]) -> list[Path]:
    """Find JSONL evidence only within caller-supplied SNK raw-data roots."""

    found: dict[str, Path] = {}
    for source in sources:
        if source.is_file():
            candidates = [source]
        elif source.is_dir():
            candidates = [path for path in source.rglob("*.jsonl") if path.is_file()]
        else:
            raise RuntimeError(f"local SNK recovery source missing: {source}")
        for candidate in candidates:
            if candidate.suffix.lower() != ".jsonl" or candidate.name.endswith(".partial"):
                continue
            found[str(candidate.resolve()).lower()] = candidate
    return sorted(found.values(), key=_recovery_source_order)


def _load_current_price_as_of(active_variant_ids: set[int]) -> dict[int, date]:
    """Return current canonical price days for the current active generation."""

    if not active_variant_ids:
        return {}
    placeholders = ",".join(["%s"] * len(active_variant_ids))
    conn = _db_connect()
    try:
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT metric.variant_id, price.observed_date
            FROM market_canonical_metric_acceptance AS metric
            INNER JOIN market_metric_history_acceptance AS history
              ON history.id=metric.price_history_acceptance_id
            INNER JOIN market_price_observation AS price
              ON price.id=history.source_record_id AND price.variant_id=metric.variant_id
            INNER JOIN (
              SELECT ranking_generation_sha256
              FROM market_canonical_metric_acceptance
              ORDER BY accepted_at DESC, id DESC
              LIMIT 1
            ) AS current_generation
              ON current_generation.ranking_generation_sha256=metric.ranking_generation_sha256
            WHERE metric.variant_id IN ({placeholders})
            """,
            tuple(sorted(active_variant_ids)),
        )
        result: dict[int, date] = {}
        for row in cur.fetchall():
            observed = row.get("observed_date")
            if observed is None:
                continue
            result[int(row["variant_id"])] = observed if isinstance(observed, date) else date.fromisoformat(str(observed))
        return result
    finally:
        conn.close()


def _load_existing_price_dates(active_variant_ids: set[int]) -> dict[int, set[date]]:
    """Load already accepted exact history so recovery only fetches true gaps."""

    if not active_variant_ids:
        return {}
    placeholders = ",".join(["%s"] * len(active_variant_ids))
    conn = _db_connect()
    try:
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT price.variant_id, price.observed_date
            FROM market_metric_history_acceptance AS history
            INNER JOIN market_price_observation AS price
              ON history.source_record_type='market_price_observation'
             AND history.source_record_id=price.id
             AND history.variant_id=price.variant_id
            WHERE history.metric_kind='psa10_price'
              AND price.variant_id IN ({placeholders})
            """,
            tuple(sorted(active_variant_ids)),
        )
        result: dict[int, set[date]] = defaultdict(set)
        for row in cur.fetchall():
            observed = row.get("observed_date")
            if observed is not None:
                result[int(row["variant_id"])].add(
                    observed if isinstance(observed, date) else date.fromisoformat(str(observed))
                )
        return dict(result)
    finally:
        conn.close()


def _load_last_persisted_kline_day(cur: Any, item_ids: set[int]) -> dict[int, date]:
    """Last persisted SNK kline day per exact item; the DB is the append index."""

    if not item_ids:
        return {}
    placeholders = ",".join(["%s"] * len(item_ids))
    cur.execute(
        f"""
        SELECT external_entity_id, MAX(observed_date) AS last_observed
        FROM market_source_observation
        WHERE source_code='snkrdunk' AND observation_kind='psa10_reference_price'
          AND external_entity_id IN ({placeholders})
        GROUP BY external_entity_id
        """,
        tuple(str(item_id) for item_id in sorted(item_ids)),
    )
    result: dict[int, date] = {}
    for row in cur.fetchall():
        observed = row.get("last_observed")
        if observed is None:
            continue
        result[int(str(row["external_entity_id"]).strip())] = (
            observed if isinstance(observed, date) else date.fromisoformat(str(observed))
        )
    return result


def _load_latest_persisted_kline(
    cur: Any, item_id: int
) -> dict[str, tuple[int, str, float | None]]:
    """Latest persisted kline row per day for one item: day -> (id, sha, priceJpy).

    The unique key ends in payload_sha256 (which embeds fetchedAt), so a
    re-capture of an unchanged candle would always insert a fresh row. This
    map is what lets the ingester tell "new evidence" from "same candle,
    new fetch" — group-wise newest row per observed day.
    """

    cur.execute(
        """
        SELECT s.id, s.observed_date, s.payload_sha256,
               CAST(JSON_UNQUOTE(JSON_EXTRACT(s.payload_json, '$.priceJpy')) AS DOUBLE) AS price_jpy
        FROM market_source_observation s
        INNER JOIN (
            SELECT observed_date, MAX(id) AS id
            FROM market_source_observation
            WHERE source_code='snkrdunk' AND observation_kind='psa10_reference_price'
              AND external_entity_id=%s
            GROUP BY observed_date
        ) latest ON latest.id = s.id
        """,
        (str(item_id),),
    )
    result: dict[str, tuple[int, str, float | None]] = {}
    for row in cur.fetchall():
        observed = row["observed_date"]
        day = observed.isoformat() if isinstance(observed, date) else str(observed)
        price = row.get("price_jpy")
        result[day] = (
            int(row["id"]),
            str(row["payload_sha256"]),
            float(price) if price is not None else None,
        )
    return result


def recover_local_history(
    *,
    sources: list[Path],
    archive_dir: Path,
    report_path: Path,
) -> dict[str, Any]:
    """Materialize useful local SNK history into one current-repo archive.

    This is a data recovery operation, not a second collector: rows are retained
    only when their PSA10 condition, active exact SNK identity, observed dates,
    price points and fetched-at provenance are all already present locally.
    Later source files replace earlier values for the same exact item/day.
    """

    paths = discover_local_recovery_paths(sources)
    item_to_variant = load_exact_snk_item_to_variant()
    if not item_to_variant:
        raise RuntimeError("no active exact SNK identities available for local recovery")

    by_item: dict[int, dict[str, Any]] = {}
    source_reports: list[dict[str, Any]] = []
    for path in paths:
        source_stats = {
            "path": str(path),
            "rowsSeen": 0,
            "rowsRetained": 0,
            "pointsRetained": 0,
            "skippedInvalidJson": 0,
            "skippedError": 0,
            "skippedCondition": 0,
            "skippedNoExactIdentity": 0,
            "skippedEmptyKline": 0,
            "skippedMissingFetchedAt": 0,
        }
        with path.open(encoding="utf-8", errors="replace") as source:
            for line in source:
                if not line.strip():
                    continue
                source_stats["rowsSeen"] += 1
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    source_stats["skippedInvalidJson"] += 1
                    continue
                if not isinstance(row, dict):
                    source_stats["skippedInvalidJson"] += 1
                    continue
                if row.get("error"):
                    source_stats["skippedError"] += 1
                    continue
                if row.get("condition_filter") != PSA10_CONDITION:
                    source_stats["skippedCondition"] += 1
                    continue
                item_id = row.get("item_id")
                if not isinstance(item_id, int) or item_id not in item_to_variant:
                    source_stats["skippedNoExactIdentity"] += 1
                    continue
                points = _valid_kline_points(row)
                if not points:
                    source_stats["skippedEmptyKline"] += 1
                    continue
                try:
                    observed_at = source_observed_at(row)
                except RuntimeError:
                    source_stats["skippedMissingFetchedAt"] += 1
                    continue
                source_stats["rowsRetained"] += 1
                source_stats["pointsRetained"] += len(points)
                state = by_item.get(item_id)
                if state is None:
                    state = {
                        "row": dict(row),
                        "latestObservedAt": observed_at,
                        "points": {},
                        "sources": [],
                    }
                    by_item[item_id] = state
                elif observed_at >= state["latestObservedAt"]:
                    state["row"] = dict(row)
                    state["latestObservedAt"] = observed_at
                state["sources"].append(str(path))
                for day, price_jpy in points:
                    state["points"][day] = price_jpy
        source_reports.append(source_stats)

    if not by_item:
        raise RuntimeError("local SNK recovery found no active exact PSA10 kline rows")

    archive_dir.mkdir(parents=True, exist_ok=True)
    # Keep an earlier recovery artifact for reference but keep it outside the
    # importer pattern.  A recovery generation is replaced as one unit.
    for prior in archive_dir.glob("snk_price_harvest_*_recovered033.jsonl"):
        os.replace(prior, prior.with_suffix(".superseded"))
    recovered_at = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive_path = archive_dir / f"snk_price_harvest_{recovered_at}_recovered033.jsonl"
    temporary = archive_path.with_name(f".{archive_path.name}.{os.getpid()}.next")
    local_dates: dict[int, set[date]] = defaultdict(set)
    with temporary.open("w", encoding="utf-8") as destination:
        for item_id in sorted(by_item):
            state = by_item[item_id]
            row = state["row"]
            row["kline"] = [
                {"date": day, "price_jpy": price_jpy}
                for day, price_jpy in sorted(state["points"].items())
            ]
            row["fetched_at"] = state["latestObservedAt"].isoformat() + "Z"
            row["recovery_sources"] = sorted(set(state["sources"]))
            destination.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            variant_id = item_to_variant[item_id]
            local_dates[variant_id].update(date.fromisoformat(point["date"]) for point in row["kline"])
    os.replace(temporary, archive_path)

    active_variant_ids = set(item_to_variant.values())
    current_as_of = _load_current_price_as_of(active_variant_ids)
    existing_dates = _load_existing_price_dates(active_variant_ids)
    remaining: list[dict[str, Any]] = []
    for item_id, variant_id in sorted(item_to_variant.items(), key=lambda value: value[1]):
        as_of = current_as_of.get(variant_id)
        if as_of is None:
            continue
        target = as_of.fromordinal(as_of.toordinal() - 30)
        available_dates = set(existing_dates.get(variant_id, set())) | set(local_dates.get(variant_id, set()))
        if not any(abs((candidate - target).days) <= 5 for candidate in available_dates):
            remaining.append(
                {
                    "variantId": variant_id,
                    "snkItemId": item_id,
                    "priceAsOf": as_of.isoformat(),
                    "targetDate": target.isoformat(),
                }
            )

    worklist_path = archive_dir / "remaining-30d-exact-snk-ids.txt"
    worklist_path.write_text(
        "".join(f"{row['snkItemId']}\n" for row in remaining),
        encoding="ascii",
    )
    report = {
        "schema": "one-time-033-local-snk-recovery-v1",
        "archivePath": str(archive_path),
        "worklistPath": str(worklist_path),
        "sourceFiles": len(paths),
        "sourceReports": source_reports,
        "activeExactSnkItems": len(item_to_variant),
        "recoveredCards": len(by_item),
        "recoveredPricePoints": sum(len(state["points"]) for state in by_item.values()),
        "remaining30dExactSnk": remaining,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return report


def discover_kline_archive_paths(archive_dir: Path) -> list[Path]:
    """Return complete local SNK harvests in deterministic source-run order."""

    if not archive_dir.is_dir():
        raise RuntimeError(f"SNK archive directory missing: {archive_dir}")

    def archive_order(path: Path) -> tuple[str, str]:
        matched = re.search(r"_(\d{8}T\d{6}(?:\d{6})?Z)", path.name)
        return (matched.group(1) if matched else "", path.name)

    paths = sorted(
        (
            path for path in archive_dir.iterdir()
            if path.is_file()
            and path.suffix == ".jsonl"
            and SNK_KLINE_ARCHIVE_PATTERN.fullmatch(path.name) is not None
        ),
        key=archive_order,
    )
    if not paths:
        raise RuntimeError(f"SNK archive has no complete harvest JSONL: {archive_dir}")
    return paths


def ingest_kline_jsonl(
    jsonl_path: Path,
    *,
    condition_code: str = PSA10_CONDITION,
    run_key: str | None = None,
    variant_allowlist: set[int] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    return ingest_kline_jsonls(
        [jsonl_path],
        condition_code=condition_code,
        run_key=run_key,
        variant_allowlist=variant_allowlist,
        dry_run=dry_run,
    )


def ingest_kline_archive_dir(
    archive_dir: Path,
    *,
    condition_code: str = PSA10_CONDITION,
    run_key: str | None = None,
    variant_allowlist: set[int] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    return ingest_kline_jsonls(
        discover_kline_archive_paths(archive_dir),
        condition_code=condition_code,
        run_key=run_key,
        variant_allowlist=variant_allowlist,
        dry_run=dry_run,
        ingest_mode="backfill",
    )


def ingest_kline_jsonls(
    jsonl_paths: list[Path],
    *,
    condition_code: str = PSA10_CONDITION,
    run_key: str | None = None,
    variant_allowlist: set[int] | None = None,
    dry_run: bool = False,
    ingest_mode: str = "incremental",
    conn: Any | None = None,
    item_to_variant: dict[int, int] | None = None,
) -> dict[str, Any]:
    """Write PSA10 kline points into market_price_observation.

    Fail-closed rules:
    - only rows with condition_filter == PSA10 condition
    - only item_ids with exact catalog_source_identity
    - never invent prices; empty kline → skipped (missing stays missing)
    - every effective_at is the observed-day EOD; fetch time remains provenance only
    - multiple inputs are processed in source-run order, so later same-key evidence wins
    - incremental mode appends: only days newer than the last persisted SNK
      observation minus KLINE_TAIL_REWRITE_DAYS are written (the tail window
      lets a revised recent candle replace its old value via
      uq_market_price_daily); backfill mode still covers full history
    - a candle only mints a new source row when its (card, day) value is new
      or changed. The unique key ends in payload_sha256, which embeds
      fetchedAt, so it cannot stop a re-fetch of an unchanged candle from
      inserting a duplicate — pre-fix collector polls bloated the table to
      ~790k redundant rows, and the rebuild backfill path kept replaying
      full histories after the incremental fix (2026-08-08 S8 rerun: 2,850
      duplicate rows for 7 re-fetched items, 92% already-persisted days).
      An unchanged re-capture reuses the persisted row id so the price
      upsert still lands on the current variant binding.
    - within one run the last evidence for a (card, day) wins; multi-file
      archive replays update in place instead of stacking one row per file

    A caller that already holds a writer connection (e.g. rebuild orchestrator
    under writer freeze) passes it via `conn` — it is committed but never
    closed here. `item_to_variant` overrides the exact-identity map for callers
    that scope it more strictly than catalog-wide (per-row exact ownership is
    still re-asserted against catalog_source_identity).
    """

    if condition_code != PSA10_CONDITION:
        raise RuntimeError(f"ingest only accepts {PSA10_CONDITION}, got {condition_code}")
    if not jsonl_paths:
        raise RuntimeError("SNK ingest needs at least one JSONL")
    for jsonl_path in jsonl_paths:
        if not jsonl_path.is_file():
            raise RuntimeError(f"ingest jsonl missing: {jsonl_path}")

    if item_to_variant is None:
        item_to_variant = load_exact_snk_item_to_variant(variant_allowlist=variant_allowlist)
    rows: list[dict[str, Any]] = []
    for jsonl_path in jsonl_paths:
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

    owns_conn = conn is None
    if owns_conn:
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
        if owns_conn:
            conn.close()
        raise RuntimeError("USD/JPY FX rate missing; refuse to invent conversion")

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    key = run_key or f"snk_kline_ingest_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    seed = hashlib.sha256(key.encode("utf-8")).hexdigest()

    stats: dict[str, Any] = {
        "runKey": key,
        "ingestMode": ingest_mode,
        "jsonls": [str(path) for path in jsonl_paths],
        "sourceFiles": len(jsonl_paths),
        "jpyPerUsd": jpy_per_usd,
        "rowsSeen": len(rows),
        "cardsAccepted": 0,
        "pricePoints": 0,
        "pricePointsAlreadyPersisted": 0,
        "quoteHeadsRestamped": 0,
        "sourceRowsDeduped": 0,
        "skippedNoExactIdentity": 0,
        "skippedCondition": 0,
        "skippedEmptyKline": 0,
        "skippedErrorRow": 0,
        "skippedAllowlist": 0,
        "quarantineReleased": 0,
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
        if owns_conn:
            conn.close()
        print(json.dumps(stats, ensure_ascii=False, sort_keys=True))
        return stats

    cur.execute(
        """
        INSERT INTO market_ingest_run
            (run_key, source_code, ingest_mode, effective_at, payload_sha256, manifest_sha256,
             status, observed_count, accepted_count, quarantined_count, rejected_count, started_at)
        VALUES (%s, 'snk_psa10', %s, %s, %s, %s, 'running', 0, 0, 0, 0, %s)
        ON DUPLICATE KEY UPDATE
            id=LAST_INSERT_ID(id), status='running', started_at=VALUES(started_at)
        """,
        (key, ingest_mode, now, seed, seed, now),
    )
    run_id = cur.lastrowid
    stats["runId"] = run_id
    incremental = ingest_mode == "incremental"
    last_day_by_item: dict[int, date] = {}
    if incremental:
        last_day_by_item = _load_last_persisted_kline_day(
            cur,
            {
                row["item_id"]
                for row in rows
                if isinstance(row.get("item_id"), int) and row["item_id"] in item_to_variant
            },
        )
    # Keyed by (card, day): the last evidence in source-run order wins, so a
    # multi-file archive replay updates in place instead of stacking rows.
    source_rows_by_key: dict[tuple[str, str], tuple[Any, ...]] = {}
    pending_price_by_key: dict[
        tuple[str, str],
        tuple[tuple[str, str, str] | None, int | None, tuple[Any, ...], tuple[Any, ...]],
    ] = {}
    persisted_kline_cache: dict[int, dict[str, tuple[int, str, float | None]]] = {}
    resolved_cards: list[tuple[int, int, int]] = []
    fetch_by_item: dict[str, Any] = {}

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
        observed_at = source_observed_at(row)
        previous_fetch = fetch_by_item.get(str(item_id))
        if previous_fetch is None or observed_at > previous_fetch:
            fetch_by_item[str(item_id)] = observed_at
        cur.execute(
            """
            SELECT COUNT(*) AS n
            FROM catalog_source_identity AS identity
            WHERE identity.variant_id=%s AND identity.source_code='snkrdunk'
              AND identity.external_entity_id=%s AND identity.match_status='exact'
            """,
            (variant_id, str(item_id)),
        )
        if int(cur.fetchone()["n"]) != 1:
            raise RuntimeError(
                f"SNK identity lost exact ownership: item={item_id} variant={variant_id}"
            )
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

        stats["cardsAccepted"] += 1
        stats["acceptedItemIds"].append(item_id)
        to_write = valid
        if incremental:
            last_day = last_day_by_item.get(item_id)
            if last_day is not None:
                # Append-only with a small tail-rewrite window: days newer than
                # the last persisted day are new candles; days inside the window
                # are re-upserted so a revised recent candle still replaces its
                # old value. Older days are already persisted and stay untouched.
                cutoff = (last_day - timedelta(days=KLINE_TAIL_REWRITE_DAYS)).isoformat()
                to_write = [(day, price_jpy) for day, price_jpy in valid if day >= cutoff]
                skipped_as_persisted = len(valid) - len(to_write)
                # A provider may retract newer candles.  In that case the
                # current snapshot's real head can be older than the DB append
                # index and fall outside the tail-rewrite window.  The chart
                # history remains append-only, but the head still has to be
                # replayed so this successful daily check mints a fresh quote
                # revision instead of leaving the variant permanently stale.
                source_head = max(valid, key=lambda point: point[0])
                if all(day != source_head[0] for day, _price in to_write):
                    to_write.append(source_head)
                    skipped_as_persisted -= 1
                    stats["quoteHeadsRestamped"] += 1
                stats["pricePointsAlreadyPersisted"] += skipped_as_persisted
        source_row_sha256 = hashlib.sha256(
            json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        persisted_latest = persisted_kline_cache.get(item_id)
        if persisted_latest is None:
            persisted_latest = _load_latest_persisted_kline(cur, item_id)
            persisted_kline_cache[item_id] = persisted_latest
        for day, price_jpy in to_write:
            price_usd = round(price_jpy / jpy_per_usd, 6)
            # The market date is authoritative for chart anchors and public as-of.
            observed = date.fromisoformat(day)
            effective = datetime.combine(observed, dt_time(23, 59, 59))
            dedup_key = (str(item_id), day)
            persisted = persisted_latest.get(day)
            if persisted is not None and persisted[2] is not None and persisted[2] == price_jpy:
                # Unchanged candle: the persisted row already carries this
                # evidence, and a fresh fetch differs only in provenance.
                # Reuse the persisted row id so the price upsert still lands
                # on the current variant binding.
                stats["sourceRowsDeduped"] += 1
                source_rows_by_key.pop(dedup_key, None)
                pending_price_by_key[dedup_key] = (
                    None,
                    persisted[0],
                    (run_id, variant_id, "snkrdunk", str(item_id)),
                    (day, effective, price_usd, price_jpy, "JPY", 50, "ready", persisted[1]),
                )
                stats["pricePoints"] += 1
                continue
            payload = {
                "source": "snk_market_data_kline",
                "itemId": item_id,
                "priceJpy": price_jpy,
                "priceUsd": price_usd,
                "observedDate": day,
                "stampedAsNow": False,
                "fetchedAt": str(row.get("fetched_at")),
                "sourceRowSha256": source_row_sha256,
            }
            payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            payload_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
            source_rows_by_key[dedup_key] = (
                run_id, str(item_id), effective, day, payload_hash, payload_json,
                observed_at,
            )
            pending_price_by_key[dedup_key] = (
                (str(item_id), day, payload_hash),
                None,
                (run_id, variant_id, "snkrdunk", str(item_id)),
                (day, effective, price_usd, price_jpy, "JPY", 50, "ready", payload_hash),
            )
            stats["pricePoints"] += 1
        resolved_cards.append((item_id, variant_id, len(to_write)))

    source_upsert = """
        INSERT INTO market_source_observation
            (run_id, source_code, external_entity_id, observation_kind, effective_at,
             observed_date, payload_sha256, payload_json, observed_at)
        VALUES (%s, 'snkrdunk', %s, 'psa10_reference_price', %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            run_id=VALUES(run_id),
            effective_at=VALUES(effective_at), payload_sha256=VALUES(payload_sha256),
            payload_json=VALUES(payload_json),
            observed_at=VALUES(observed_at)
    """
    source_rows_to_write = list(source_rows_by_key.values())
    for offset in range(0, len(source_rows_to_write), 500):
        cur.executemany(
            source_upsert,
            source_rows_to_write[offset : offset + 500],
        )

    # Resolve batched observation ids through the same unique key the upsert
    # deduplicates on; every pending price row must find exactly one owner.
    # Deduped rows already carry the persisted id and skip the lookup.
    pending_price_rows = list(pending_price_by_key.values())
    source_observation_ids: dict[tuple[str, str, str], int] = {}
    lookup_keys = list(dict.fromkeys(
        key for key, _direct_id, _, _ in pending_price_rows if key is not None
    ))
    for offset in range(0, len(lookup_keys), 500):
        chunk = lookup_keys[offset : offset + 500]
        predicate = " OR ".join(
            ["(external_entity_id=%s AND observed_date=%s AND payload_sha256=%s)"] * len(chunk)
        )
        cur.execute(
            f"""
            SELECT id, external_entity_id, observed_date, payload_sha256
            FROM market_source_observation
            WHERE source_code='snkrdunk' AND observation_kind='psa10_reference_price'
              AND ({predicate})
            """,
            tuple(value for lookup_key in chunk for value in lookup_key),
        )
        for found in cur.fetchall():
            observed = found["observed_date"]
            day_text = observed.isoformat() if isinstance(observed, date) else str(observed)
            source_observation_ids[
                (str(found["external_entity_id"]), day_text, str(found["payload_sha256"]))
            ] = int(found["id"])

    price_rows_to_write: list[tuple[Any, ...]] = []
    for lookup_key, direct_id, head, tail in pending_price_rows:
        if lookup_key is None:
            source_observation_id = int(direct_id or 0)
        else:
            source_observation_id = int(source_observation_ids.get(lookup_key) or 0)
        if source_observation_id <= 0:
            raise RuntimeError("SNK source observation upsert returned no id")
        price_rows_to_write.append((*head, source_observation_id, *tail))

    price_upsert = """
        INSERT INTO market_price_observation
            (run_id, variant_id, source_code, source_external_entity_id,
             source_observation_id, observed_date, effective_at, price_usd,
             native_price, native_currency, source_priority, metric_status, payload_sha256)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            last_run_id=VALUES(run_id),
            restamp_count=restamp_count+1,
            source_external_entity_id=VALUES(source_external_entity_id),
            source_observation_id=VALUES(source_observation_id),
            effective_at=VALUES(effective_at),
            price_usd=VALUES(price_usd),
            native_price=VALUES(native_price),
            native_currency=VALUES(native_currency),
            source_priority=VALUES(source_priority),
            metric_status=CASE WHEN market_price_observation.metric_status='quarantined'
                               THEN 'quarantined' ELSE VALUES(metric_status) END,
            payload_sha256=VALUES(payload_sha256)
    """
    for offset in range(0, len(price_rows_to_write), 1000):
        cur.executemany(
            price_upsert,
            price_rows_to_write[offset : offset + 1000],
        )

    # The K-line head bar is no longer minted as a quote (owner 2026-08-23).
    # A candle is a chart level, not a trade: it was the published price of all
    # 420 JP cards while their real completed trades sat unused in
    # market_sale_observation.  Every daily bar still lands in
    # market_price_observation above -- the charts are untouched -- and the
    # price is minted from the trades by pipelines/psa10_latest_sale_quote.py.

    # Release stale quarantines this run just re-verified.
    #
    # A binding repair quarantines a card's price rows because rows captured
    # under a doubtful binding must not be laundered by a later fetch of the
    # same days -- hence the sticky CASE in the upsert above. But nothing ever
    # wrote the status back, so a card whose binding was LATER proven by a
    # provider page stayed unpriceable forever: S12 refuses to rank a
    # product_ready card it cannot price, and two Japanese One Piece cards
    # (PSA10 populations 2,363 and 1,589) aborted activation with 834 rows of
    # perfectly good evidence frozen behind a flag from a superseded guess.
    #
    # The proof is the release. operator_strict_source_identity holds only
    # bindings proven exact, so a row qualifies when this run's fresh capture
    # landed on it AND it carries the very provider item that binding names.
    # Rows this run did not touch, and rows whose external id does not match
    # the proven binding, keep their quarantine.
    cur.execute(
        """
        UPDATE market_price_observation p
        INNER JOIN operator_strict_source_identity si
           ON si.variant_id=p.variant_id
          AND si.source_code='snkrdunk'
          AND si.external_entity_id=p.source_external_entity_id
        SET p.metric_status='ready'
        WHERE p.last_run_id=%s
          AND p.metric_status='quarantined'
          AND p.source_code IN ('snkrdunk','snk','snk_psa10')
        """,
        (run_id,),
    )
    stats["quarantineReleased"] = int(cur.rowcount)

    cur.execute(
        """
        UPDATE market_ingest_run
        SET status='complete', observed_count=%s, accepted_count=%s, completed_at=%s
        WHERE id=%s
        """,
        (stats["pricePoints"], stats["pricePoints"], now, run_id),
    )
    conn.commit()
    if owns_conn:
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
                evidence_paths=jsonl_paths,
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
    ap.add_argument("--delay", type=float, default=0.0, help="per-worker min spacing; 0 for max throughput")
    ap.add_argument("--workers", type=int, default=16, help="concurrent SNK workers (API, not CDP)")
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
        "--ingest-archive-dir",
        type=Path,
        help="ingest every complete local snk[_price]_harvest*.jsonl in source-run order",
    )
    ap.add_argument(
        "--recover-local-history",
        action="store_true",
        help="collect useful local exact PSA10 kline into one current-repo recovery archive",
    )
    ap.add_argument(
        "--recovery-source",
        type=Path,
        action="append",
        default=[],
        help="JSONL file or raw-data directory to include in local recovery; repeatable",
    )
    ap.add_argument(
        "--recovery-archive-dir",
        type=Path,
        help="current-repo directory for recovered snk_price_harvest JSONL",
    )
    ap.add_argument(
        "--recovery-report",
        type=Path,
        help="one-time JSON index for local recovery and remaining exact SNK 30d IDs",
    )
    ap.add_argument(
        "--variant-allowlist",
        type=Path,
        help="optional worklist JSON/ids limiting which variants may receive price writes",
    )
    ap.add_argument("--dry-run", action="store_true", help="with --ingest-jsonl, report only")
    args = ap.parse_args()

    if args.ingest_jsonl is not None and args.ingest_archive_dir is not None:
        ap.error("--ingest-jsonl and --ingest-archive-dir cannot be combined")

    if args.recover_local_history:
        if args.ingest_jsonl is not None or args.ingest_archive_dir is not None or args.out is not None:
            ap.error("--recover-local-history cannot be combined with ingest or harvest output options")
        if not args.recovery_source or args.recovery_archive_dir is None or args.recovery_report is None:
            ap.error("--recover-local-history needs --recovery-source, --recovery-archive-dir and --recovery-report")
        recover_local_history(
            sources=args.recovery_source,
            archive_dir=args.recovery_archive_dir,
            report_path=args.recovery_report,
        )
        return

    if args.ingest_jsonl is not None or args.ingest_archive_dir is not None:
        allowlist = _parse_variant_allowlist(args.variant_allowlist)
        if args.ingest_archive_dir is not None:
            report = ingest_kline_archive_dir(
                args.ingest_archive_dir,
                condition_code=args.condition,
                run_key=args.run_id,
                variant_allowlist=allowlist,
                dry_run=args.dry_run,
            )
        else:
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
    report = run(item_ids, args.out, args.delay, args.condition, run_id, workers=getattr(args, 'workers', 16))
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
