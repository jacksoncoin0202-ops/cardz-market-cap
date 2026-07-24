#!/usr/bin/env python3
"""Export the web snapshot from validated canonical MySQL observations.

Ranking and market metrics come only from the canonical database.  The checked
public snapshot is used solely as a presentation pack for already-QC'd images,
localized identity text and editorial stories.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from collections import defaultdict
from datetime import date, datetime, time, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from db_runtime import add_connection_args, connection_from_args


WINDOWS = ("1d", "7d", "30d")
GRADERS = ("PSA", "BGS", "CGC", "SGC", "TAG")
CURRENCIES = ("USD", "HKD", "CNY", "GBP", "TWD", "JPY", "KRW")
TOP_GRADE = {"PSA": "10", "BGS": "10", "CGC": "10", "SGC": "10", "TAG": "10"}
# The public schema remains top100 + watchlist.  These selectors only control
# how many ordered canonical ranks are materialized into that stable shape.
PRESENTATION_VIEW_LIMITS = {
    "top100": 100,
    "top300": 300,
    "top350": 350,
    "top100_plus_200": 300,
}


class SnapshotExportError(RuntimeError):
    """Raised before an invalid canonical generation can replace a snapshot."""


def presentation_view_limit(name: str) -> int:
    normalized = str(name).strip()
    try:
        return PRESENTATION_VIEW_LIMITS[normalized]
    except KeyError as error:
        choices = ", ".join(PRESENTATION_VIEW_LIMITS)
        raise SnapshotExportError(f"unknown public presentation view {normalized!r}; expected one of {choices}") from error


def iso(value: Any) -> str:
    if isinstance(value, datetime):
        current = value
    elif isinstance(value, date):
        current = datetime.combine(value, time.min)
    else:
        current = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def number(value: Any) -> int | float | None:
    if value is None:
        return None
    converted = float(value)
    return int(converted) if converted.is_integer() else converted


def integer(value: Any) -> int | None:
    return None if value is None else int(value)


def stable_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def metric(value: float | int | None, status: str, as_of: str | None, **extra: Any) -> dict[str, Any]:
    if status not in {"ready", "stale", "accumulating", "unavailable"}:
        status = "unavailable"
    if status in {"accumulating", "unavailable"}:
        value = None
    return {"value": value, "status": status, "asOf": as_of if value is not None else None, **extra}


def load_presentation(path: Path) -> tuple[dict[str, Any], dict[str, Mapping[str, Any]]]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, Mapping):
        raise SnapshotExportError("presentation snapshot is invalid")
    cards = document.get("top100", []) + document.get("watchlist", [])
    indexed = {
        str(card["id"]): card
        for card in cards
        if isinstance(card, Mapping) and isinstance(card.get("id"), str)
    }
    if len(indexed) != len(cards):
        raise SnapshotExportError("presentation snapshot has duplicate or invalid card IDs")
    return dict(document), indexed


def fetchall(connection: Any, query: str, args: Iterable[Any] = ()) -> list[Mapping[str, Any]]:
    with connection.cursor() as cursor:
        cursor.execute(query, tuple(args))
        return list(cursor.fetchall())


def fetchone(connection: Any, query: str, args: Iterable[Any] = ()) -> Mapping[str, Any] | None:
    rows = fetchall(connection, query, args)
    return rows[0] if rows else None


def latest_generation(connection: Any) -> Mapping[str, Any]:
    row = fetchone(
        connection,
        """
        SELECT id,effective_at,effective_date,constituent_count,snapshot_sha256
        FROM market_index_snapshot
        WHERE index_code='tcg-combined'
        ORDER BY effective_at DESC,id DESC LIMIT 1
        """,
    )
    if not row:
        raise SnapshotExportError("canonical combined ranking snapshot is unavailable")
    return row


def market_rows(connection: Any, generation: Mapping[str, Any], *, required_count: int) -> list[dict[str, Any]]:
    evaluation = fetchone(
        connection,
        """
        SELECT id,effective_date FROM market_alert_evaluation
        WHERE index_code='tcg-combined' AND effective_date<=%s
        ORDER BY effective_date DESC,id DESC LIMIT 1
        """,
        (generation["effective_date"],),
    )
    evaluation_id = int(evaluation["id"]) if evaluation else -1
    top = fetchall(
        connection,
        """
        SELECT v.id AS variant_id,v.opaque_id,v.identity_status,
               c.rank_position AS rank_position,c.reference_price_usd,c.psa10_population,
               c.market_cap_usd,c.metric_status,
               d.change_1d_pct,d.change_7d_pct,d.change_30d_pct
        FROM market_index_constituent c
        JOIN catalog_variant v ON v.id=c.variant_id
        LEFT JOIN market_candidate_daily_snapshot d
          ON d.variant_id=c.variant_id AND d.evaluation_id=%s
        WHERE c.index_snapshot_id=%s AND c.rank_position<=%s
        ORDER BY c.rank_position
        """,
        (evaluation_id, generation["id"], required_count),
    )
    if len(top) != required_count:
        raise SnapshotExportError(
            f"canonical combined ranking has {len(top)} rows for requested Top {required_count} view"
        )
    if [int(row["rank_position"]) for row in top] != list(range(1, required_count + 1)):
        raise SnapshotExportError("canonical combined ranking has non-contiguous ranks")
    return [dict(row) for row in top]


def latest_sales(connection: Any, variant_ids: list[int]) -> dict[tuple[int, str], Mapping[str, Any]]:
    if not variant_ids:
        return {}
    placeholders = ",".join(["%s"] * len(variant_ids))
    rows = fetchall(
        connection,
        f"""
        SELECT variant_id,window_code,sales_count,sales_value_usd,coverage_status,window_end_at
        FROM market_tracked_sales_aggregate
        WHERE variant_id IN ({placeholders}) AND grader_code='PSA' AND grade_label='10'
        ORDER BY window_end_at DESC,id DESC
        """,
        variant_ids,
    )
    result: dict[tuple[int, str], Mapping[str, Any]] = {}
    for row in rows:
        key = (int(row["variant_id"]), str(row["window_code"]))
        result.setdefault(key, row)
    return result


def latest_populations(connection: Any, variant_ids: list[int]) -> dict[tuple[int, str], Mapping[str, Any]]:
    if not variant_ids:
        return {}
    placeholders = ",".join(["%s"] * len(variant_ids))
    rows = fetchall(
        connection,
        f"""
        SELECT variant_id,grader_code,top_grade_label,total_population,top_grade_population,
               estimated,effective_at
        FROM market_grader_population_observation
        WHERE variant_id IN ({placeholders})
        ORDER BY effective_at DESC,id DESC
        """,
        variant_ids,
    )
    result: dict[tuple[int, str], Mapping[str, Any]] = {}
    for row in rows:
        key = (int(row["variant_id"]), str(row["grader_code"]).upper())
        result.setdefault(key, row)
    return result


def daily_history(connection: Any, variant_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    if not variant_ids:
        return {}
    placeholders = ",".join(["%s"] * len(variant_ids))
    prices = fetchall(
        connection,
        f"""
        SELECT variant_id,observed_date,price_usd,metric_status,source_priority,effective_at
        FROM market_price_observation
        WHERE variant_id IN ({placeholders})
        ORDER BY observed_date DESC,source_priority ASC,effective_at DESC,id DESC
        """,
        variant_ids,
    )
    sales = fetchall(
        connection,
        f"""
        SELECT variant_id,observed_date,sales_count,sales_value_usd,coverage_status
        FROM market_daily_sales_aggregate
        WHERE variant_id IN ({placeholders})
        ORDER BY observed_date,id
        """,
        variant_ids,
    )
    sales_by_day = {(int(row["variant_id"]), str(row["observed_date"])): row for row in sales}
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    seen: set[tuple[int, str]] = set()
    for row in prices:
        variant_id = int(row["variant_id"])
        day = str(row["observed_date"])
        key = (variant_id, day)
        if key in seen:
            continue
        seen.add(key)
        sale = sales_by_day.get(key)
        grouped[variant_id].append(
            {
                "at": f"{day}T00:00:00Z",
                "priceUsd": number(row["price_usd"]),
                "priceStatus": str(row["metric_status"]),
                "trackedSalesValueUsd": number(sale["sales_value_usd"]) if sale else None,
                "trackedSalesCount": integer(sale["sales_count"]) if sale else None,
                "salesCoverage": str(sale["coverage_status"]) if sale else "unavailable",
            }
        )
    for variant_id, points in grouped.items():
        grouped[variant_id] = sorted(points[:90], key=lambda point: point["at"])
    return grouped


def currency_block(connection: Any, effective_at: str) -> dict[str, Any]:
    rows = fetchall(
        connection,
        """
        SELECT quote_currency,rate,effective_at
        FROM market_fx_rate_observation
        WHERE base_currency='USD'
        ORDER BY effective_at DESC,id DESC
        """,
    )
    latest: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        latest.setdefault(str(row["quote_currency"]).upper(), row)
    rates: dict[str, Any] = {"USD": metric(1, "ready", effective_at)}
    for currency in CURRENCIES[1:]:
        row = latest.get(currency)
        rates[currency] = (
            metric(number(row["rate"]), "ready", iso(row["effective_at"]))
            if row
            else metric(None, "unavailable", None)
        )
    dates = [value["asOf"] for value in rates.values() if value["asOf"]]
    return {"base": "USD", "supported": list(CURRENCIES), "rates": rates, "asOf": min(dates) if dates else None}


def card_from_row(
    row: Mapping[str, Any],
    presentation: Mapping[str, Any],
    effective_at: str,
    sales: Mapping[tuple[int, str], Mapping[str, Any]],
    populations: Mapping[tuple[int, str], Mapping[str, Any]],
    history: Mapping[int, list[dict[str, Any]]],
) -> dict[str, Any]:
    card = json.loads(json.dumps(presentation))
    variant_id = int(row["variant_id"])
    status = str(row.get("metric_status") or "unavailable")
    if status not in {"ready", "stale"}:
        raise SnapshotExportError(f"ranked card {row['opaque_id']} has unavailable market metrics")
    card["rank"] = int(row["rank_position"])
    card["identityStatus"] = "confirmed"
    card["pricePsa10"] = metric(number(row["reference_price_usd"]), status, effective_at)
    card["populationPsa10"] = metric(integer(row["psa10_population"]), "ready", effective_at, estimated=False)
    card["marketCap"] = metric(number(row["market_cap_usd"]), status, effective_at)
    for window, column in (("1d", "change_1d_pct"), ("7d", "change_7d_pct"), ("30d", "change_30d_pct")):
        change = number(row.get(column))
        change_metric = metric(change, "ready" if change is not None else "accumulating", effective_at)
        aggregate = sales.get((variant_id, window))
        coverage = str(aggregate["coverage_status"]) if aggregate else "unavailable"
        aggregate_at = iso(aggregate["window_end_at"]) if aggregate else None
        sales_status = "ready" if aggregate and coverage == "partial" else "unavailable"
        card["windows"][window] = {
            "changePct": change_metric,
            "trackedSales": {
                "valueUsd": metric(number(aggregate["sales_value_usd"]) if aggregate else None, sales_status, aggregate_at),
                "count": metric(integer(aggregate["sales_count"]) if aggregate else None, sales_status, aggregate_at),
                "coverage": coverage,
                "asOf": aggregate_at,
            },
        }
    for grader in GRADERS:
        observed = populations.get((variant_id, grader))
        observed_at = iso(observed["effective_at"]) if observed else None
        observed_status = "ready" if observed else "unavailable"
        card["graderPopulations"][grader] = {
            "topGrade": str(observed["top_grade_label"]) if observed else TOP_GRADE[grader],
            "total": metric(integer(observed["total_population"]) if observed else None, observed_status, observed_at, estimated=False),
            "topGradePopulation": metric(
                integer(observed["top_grade_population"]) if observed else None,
                observed_status,
                observed_at,
                estimated=bool(observed["estimated"]) if observed else False,
            ),
            "topGradePopulationChangePct": {
                window: metric(None, "accumulating", None) for window in WINDOWS
            },
        }
    card["historyDaily"] = history.get(variant_id, [])
    return card


def build_snapshot(
    connection: Any,
    presentation_path: Path,
    *,
    production: bool,
    presentation_view: str = "top300",
) -> dict[str, Any]:
    template, cards_by_id = load_presentation(presentation_path)
    generation = latest_generation(connection)
    effective_at = iso(generation["effective_at"])
    required_count = presentation_view_limit(presentation_view)
    rows = market_rows(connection, generation, required_count=required_count)
    missing = sorted(str(row["opaque_id"]) for row in rows if str(row["opaque_id"]) not in cards_by_id)
    if missing:
        raise SnapshotExportError(f"presentation pack is missing {len(missing)} canonical cards")
    variant_ids = [int(row["variant_id"]) for row in rows]
    sales = latest_sales(connection, variant_ids)
    populations = latest_populations(connection, variant_ids)
    history = daily_history(connection, variant_ids)
    cards = [
        card_from_row(row, cards_by_id[str(row["opaque_id"])], effective_at, sales, populations, history)
        for row in rows
    ]
    public_cards = cards[:required_count]
    top = public_cards[:100]
    watch = public_cards[100:]
    blockers: list[str] = []
    if len(top) != 100:
        blockers.append("combined_top100_incomplete")
    generated_at = iso(datetime.now(timezone.utc))
    generation_id = f"canonical_{str(generation['effective_date']).replace('-', '')}_{str(generation['snapshot_sha256'])[:12]}"
    snapshot = {
        "schemaVersion": "2.0.0",
        "generation": {
            "id": generation_id,
            "generatedAt": generated_at,
            "effectiveAt": effective_at,
            "contentSha256": "",
            "mode": "production" if production else "demo",
            "productionEligible": production and not blockers,
            "blockers": blockers,
        },
        "universe": {
            "populationMin": 1000,
            "grade": "PSA 10",
            "rankingMetric": "psa10_market_cap_usd",
            "windows": list(WINDOWS),
            "salesCoverage": "partial",
        },
        "coverage": {
            "requestedView": presentation_view,
            "top100Count": len(top),
            "watchlistCount": len(watch),
            "publicTop300Count": len(public_cards),
            "privateReserveExcludedCount": max(0, int(generation["constituent_count"]) - len(public_cards)),
            "changeReady": {window: sum(card["windows"][window]["changePct"]["status"] == "ready" for card in cards) for window in WINDOWS},
            "salesReady": {window: sum(card["windows"][window]["trackedSales"]["coverage"] == "partial" for card in cards) for window in WINDOWS},
            "graderPopulationReady": {
                grader: sum(card["graderPopulations"][grader]["topGradePopulation"]["status"] == "ready" for card in cards)
                for grader in GRADERS
            },
            "graderPopulationChangeReady": {
                grader: {window: 0 for window in WINDOWS} for grader in GRADERS
            },
            "completeIdentityCount": sum(card["collectorNumber"]["complete"] for card in cards),
            "localizedStoryCount": {
                locale: sum(bool(card["stories"].get(locale)) for card in cards)
                for locale in ("en", "zhTW", "zhCN", "ja")
            },
        },
        "currencies": currency_block(connection, effective_at),
        "top100": top,
        "watchlist": watch,
    }
    snapshot["generation"]["contentSha256"] = hashlib.sha256(stable_json(snapshot)).hexdigest()
    return snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--presentation", type=Path, default=ROOT / "data/public/seed-snapshot.json")
    parser.add_argument("--output", type=Path, default=ROOT / "data/public/seed-snapshot.json")
    parser.add_argument("--view", choices=tuple(PRESENTATION_VIEW_LIMITS), default="top300")
    parser.add_argument("--production", action="store_true")
    add_connection_args(parser)
    args = parser.parse_args()
    connection = connection_from_args(args)
    try:
        snapshot = build_snapshot(
            connection,
            args.presentation.resolve(),
            production=args.production,
            presentation_view=args.view,
        )
    finally:
        connection.close()
    atomic_json(args.output.resolve(), snapshot)
    print(
        json.dumps(
            {
                "generation": snapshot["generation"]["id"],
                "top100": len(snapshot["top100"]),
                "watchlist": len(snapshot["watchlist"]),
                "output": str(args.output.resolve()),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (SnapshotExportError, RuntimeError, ValueError) as error:
        print(str(error), file=os.sys.stderr)
        raise SystemExit(1) from None
