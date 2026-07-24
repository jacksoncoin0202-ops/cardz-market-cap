"""SNK market data pipeline for cardz-market-cap.

針對 active universe 拎 SNKRDUNK PSA 10 每日參考價、成交額、交易量。

策略：
1. 由 seed apparel IDs 開始（可以係手動指定或者 BFS 發現）
2. 逐張卡拎 master + 每日參考價 history + 最近成交
3. 計算每日成交額（trades 加總）同交易量（trades count）
4. 輸出 JSONL，可以直接 integrate 入現有 pipeline

用法：
  # 單張卡測試
  python -X utf8 pipelines/snk_market_data.py 116069 --out data/private/snk/test.jsonl

  # BFS 發現 + 批量
  python -X utf8 pipelines/snk_market_data.py --discover-from 116069 --max-ids 600 --out data/private/snk/market_data.jsonl

  # 由 ID list 批量
  python -X utf8 pipelines/snk_market_data.py --ids-file pipelines/snk_ids.txt --out data/private/snk/market_data.jsonl
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "pipelines"))

from snkrdunk_bulk import SnkrdunkApi, bfs_discover  # noqa: E402


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
        if (
            not isinstance(sold_at, str)
            or "T" not in sold_at
            or not isinstance(price, (int, float))
            or price <= 0
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
                    {"soldAt": sold_at, "price": price, "quantity": t.get("quantity")},
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
        if sale_key in seen:
            continue
        seen.add(sale_key)
        daily[day]["count"] += 1
        daily[day]["value_jpy"] += round(float(price))
    return dict(sorted(daily.items()))


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

    history = api.get_trading_history(pcid, range_="all", condition_code=condition_code)
    points = history.get("chart", {}).get("lines", [{}])[0].get("points", [])
    trades = history.get("trades", [])

    daily_activity = aggregate_daily_trades(trades)

    kline_by_day: dict[str, float] = {}
    for point in points:
        timestamp = point.get("timestamp") if isinstance(point, dict) else None
        price = point.get("price") if isinstance(point, dict) else None
        if not isinstance(timestamp, (int, float)) or not isinstance(price, (int, float)) or price <= 0:
            continue
        day = datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        kline_by_day[day] = float(price)

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
        "kline": [{"date": day, "price_jpy": price} for day, price in sorted(kline_by_day.items())],
        "recent_trades": trades,
        "daily_activity": daily_activity,
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
    version = api.fetch_x_version() if missing else None
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
                ok += 1
                kline_points = len(data.get("kline", []))
                trades = len(data.get("recent_trades", []))
                print(
                    f"  [{len(existing) + offset}/{len(item_ids)}] "
                    f"{data.get('product_number')} — {kline_points} kline, {trades} trades"
                )
            except Exception as e:
                failed += 1
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
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

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
    main()
