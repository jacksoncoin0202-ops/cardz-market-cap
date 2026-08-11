#!/usr/bin/env python3
"""Fail-closed contract for CARDZ 036 / FE03 baked releases."""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
GUARDRAILS = json.loads(
    (ROOT / "data" / "policy" / "daily-release-guardrails.json").read_text(
        encoding="utf-8"
    )
)
if GUARDRAILS.get("contract") != "cardz-daily-release-guardrails-v1":
    raise RuntimeError("daily release guardrail contract is missing or unsupported")
PRICE_MAX_AGE_DAYS = max(
    int(days) for days in GUARDRAILS["priceMaxAgeDaysBySource"].values()
)
AWAITING_FRESH_PRICE_MAX_RATIO = float(GUARDRAILS["awaitingFreshPriceMaxRatio"])
EXPECTED_CARDS = 1322
EXPECTED_COMPLETE = int(GUARDRAILS["minimumCompleteCollectorNumbers"])
TARGET_ID = "cmc_f698284d7bc333408782e4c6"
TARGET_NUMBER = "170/181"


def validate(snapshot: dict[str, Any], asset_root: Path, now: datetime) -> dict[str, Any]:
    cards = [*(snapshot.get("top100") or []), *(snapshot.get("watchlist") or [])]
    if len(cards) != EXPECTED_CARDS:
        raise AssertionError(f"cards={len(cards)}, expected={EXPECTED_CARDS}")
    ids = [str(card.get("id") or "") for card in cards]
    if len(set(ids)) != len(ids) or any(not value for value in ids):
        raise AssertionError("card ids are missing or duplicated")

    complete = []
    incomplete = []
    stale = []
    awaiting_price = []
    missing_assets = []
    cutoff = now - timedelta(days=PRICE_MAX_AGE_DAYS)
    for card in cards:
        number = card.get("collectorNumber") or {}
        display = str(number.get("display") or "").strip()
        name = str(card.get("officialName") or "").strip()
        if number.get("complete") is True:
            complete.append(card)
            if not display or not name.casefold().endswith(display.casefold()):
                raise AssertionError(f"official name/full number mismatch: {card.get('id')}")
        else:
            incomplete.append(card)

        price = card.get("pricePsa10") or {}
        market_cap = card.get("marketCap") or {}
        as_of = price.get("asOf")
        safely_unranked = (
            price.get("value") is None
            and price.get("status") in {"accumulating", "unavailable"}
            and int(card.get("marketRank") or 0) == 0
            and int(card.get("viewRank") or 0) == 0
            and market_cap.get("value") is None
            and market_cap.get("status") in {"accumulating", "unavailable"}
        )
        if safely_unranked:
            awaiting_price.append({"id": card.get("id"), "asOf": as_of})
        elif as_of:
            observed = datetime.fromisoformat(str(as_of).replace("Z", "+00:00"))
            if observed < cutoff:
                stale.append({"id": card.get("id"), "asOf": as_of})

        image = card.get("image") or {}
        for value in [image.get("src"), *((image.get("variants") or {}).values())]:
            if isinstance(value, str) and value.startswith("/market-assets/"):
                name_part = value.rsplit("/", 1)[-1]
                if not (asset_root / name_part).is_file():
                    missing_assets.append(name_part)

    if len(complete) != EXPECTED_COMPLETE:
        raise AssertionError(f"complete collector numbers={len(complete)}, expected={EXPECTED_COMPLETE}")
    if len(incomplete) != EXPECTED_CARDS - EXPECTED_COMPLETE:
        raise AssertionError(f"genuine no-denominator cards={len(incomplete)}, expected=33")
    if stale:
        raise AssertionError(
            f"ranked prices older than {PRICE_MAX_AGE_DAYS} days: {stale[:10]}"
        )
    max_awaiting = max(1, math.floor(len(cards) * AWAITING_FRESH_PRICE_MAX_RATIO))
    if len(awaiting_price) > max_awaiting:
        raise AssertionError(
            "awaiting-fresh-price cards exceed committed limit:"
            f" {len(awaiting_price)} > {max_awaiting}"
        )
    if missing_assets:
        raise AssertionError(f"missing public assets: {sorted(set(missing_assets))[:10]}")

    target = next((card for card in cards if card.get("id") == TARGET_ID), None)
    if target is None:
        raise AssertionError("170/181 target card is absent")
    if (target.get("collectorNumber") or {}).get("display") != TARGET_NUMBER:
        raise AssertionError("170/181 target collector number regressed")
    if not str(target.get("officialName") or "").endswith(TARGET_NUMBER):
        raise AssertionError("170/181 target official name regressed")

    return {
        "generation": (snapshot.get("generation") or {}).get("id"),
        "cards": len(cards),
        "uniqueIds": len(set(ids)),
        "completeCollectorNumbers": len(complete),
        "genuineNoDenominator": len(incomplete),
        "stalePricesOverPolicy": 0,
        "awaitingFreshPrice": len(awaiting_price),
        "maximumAwaitingFreshPrice": max_awaiting,
        "target": TARGET_NUMBER,
    }


def self_test() -> None:
    now = datetime.now(timezone.utc)
    card = {
        "id": TARGET_ID,
        "officialName": f"Example {TARGET_NUMBER}",
        "collectorNumber": {"display": TARGET_NUMBER, "complete": True},
        "pricePsa10": {"asOf": now.isoformat()},
        "image": {},
    }
    cards = [card] + [
        {
            "id": f"complete-{index}",
            "officialName": f"Card {index} {index}/9999",
            "collectorNumber": {"display": f"{index}/9999", "complete": True},
            "pricePsa10": {"asOf": now.isoformat()},
            "image": {},
        }
        for index in range(1, EXPECTED_COMPLETE)
    ] + [
        {
            "id": f"promo-{index}",
            "officialName": f"Promo SM{index}",
            "collectorNumber": {"display": f"SM{index}", "complete": False},
            "pricePsa10": {"asOf": now.isoformat()},
            "image": {},
        }
        for index in range(EXPECTED_CARDS - EXPECTED_COMPLETE)
    ]
    document = {"generation": {"id": "self-test"}, "top100": cards[:100], "watchlist": cards[100:]}
    validate(document, Path("."), now)

    waiting = document["watchlist"][-1]
    waiting["pricePsa10"] = {
        "value": None,
        "status": "accumulating",
        "asOf": (now - timedelta(days=31)).isoformat(),
    }
    waiting["marketCap"] = {"value": None, "status": "accumulating", "asOf": None}
    waiting["marketRank"] = 0
    waiting["viewRank"] = 0
    validate(document, Path("."), now)

    cases = []
    broken_name = json.loads(json.dumps(document))
    broken_name["top100"][0]["officialName"] = "Example 170"
    cases.append(("name", broken_name))
    duplicate_id = json.loads(json.dumps(document))
    duplicate_id["top100"][1]["id"] = duplicate_id["top100"][0]["id"]
    cases.append(("duplicate", duplicate_id))
    stale_price = json.loads(json.dumps(document))
    stale_price["top100"][0]["pricePsa10"] = {
        "value": 10,
        "status": "ready",
        "asOf": (now - timedelta(days=PRICE_MAX_AGE_DAYS + 1)).isoformat(),
    }
    stale_price["top100"][0]["marketCap"] = {"value": 10000, "status": "ready", "asOf": None}
    stale_price["top100"][0]["marketRank"] = 1
    stale_price["top100"][0]["viewRank"] = 1
    cases.append(("stale", stale_price))
    too_many_waiting = json.loads(json.dumps(document))
    waiting_limit = max(
        1,
        math.floor(EXPECTED_CARDS * AWAITING_FRESH_PRICE_MAX_RATIO),
    )
    waiting_cards = [
        *too_many_waiting["top100"],
        *too_many_waiting["watchlist"],
    ][-(waiting_limit + 1):]
    for waiting_card in waiting_cards:
        waiting_card["pricePsa10"] = {
            "value": None,
            "status": "accumulating",
            "asOf": (now - timedelta(days=PRICE_MAX_AGE_DAYS + 1)).isoformat(),
        }
        waiting_card["marketCap"] = {
            "value": None,
            "status": "accumulating",
            "asOf": None,
        }
        waiting_card["marketRank"] = 0
        waiting_card["viewRank"] = 0
    cases.append(("awaiting-limit", too_many_waiting))
    for label, broken in cases:
        try:
            validate(broken, Path("."), now)
        except AssertionError:
            continue
        raise AssertionError(f"negative self-test did not fire: {label}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--assets", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
    if args.snapshot:
        if args.assets is None:
            parser.error("--assets is required with --snapshot")
        snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
        print(json.dumps(validate(snapshot, args.assets, datetime.now(timezone.utc)), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
