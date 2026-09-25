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
MINIMUM_COMPLETE_COLLECTOR_NUMBERS = int(
    GUARDRAILS["minimumCompleteCollectorNumbers"]
)
SELF_TEST_CARDS = 1322
SELF_TEST_COMPLETE = MINIMUM_COMPLETE_COLLECTOR_NUMBERS
TARGET_ID = "cmc_f698284d7bc333408782e4c6"
TARGET_NUMBER = "170/181"
BOX_MAX_ASOF_AGE_HOURS = 36.0
BOX_COLLISION_BASELINE = ROOT / "data" / "policy" / "box-image-collision-baseline.json"
# The release commits the snapshot and GitHub refuses any file over 100 MiB.
# Stop 5 MiB short of that with a reason, not with a push GitHub rejects.
SNAPSHOT_MAX_BYTES = 95 * 1024 * 1024


def load_snapshot(path: Path) -> dict[str, Any]:
    size = path.stat().st_size
    if size > SNAPSHOT_MAX_BYTES:
        raise AssertionError(
            f"{path.name} is {size:,} bytes, over the {SNAPSHOT_MAX_BYTES:,}-byte (95 MiB)"
            " release limit: GitHub refuses files over 100 MiB, so this release could"
            " not push. Shrink the public snapshot before releasing."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def validate_box(
    box: dict[str, Any],
    previous: dict[str, Any] | None,
    asset_root: Path,
    now: datetime,
) -> dict[str, Any]:
    products = box.get("products")
    if not isinstance(products, list) or not products:
        raise AssertionError("box sidecar has no products")
    ids = [str(p.get("id") or "") for p in products]
    if any(not i for i in ids) or len(set(ids)) != len(ids):
        raise AssertionError("box ids missing or duplicated")
    prev_count = len((previous or {}).get("products") or []) if previous else 0
    if len(products) < prev_count:
        raise AssertionError(f"box product count regressed: {len(products)} < {prev_count}")
    as_of = str(box.get("asOf") or "")
    if not as_of:
        raise AssertionError("box asOf missing")
    age_h = (now - datetime.fromisoformat(as_of.replace("Z", "+00:00"))).total_seconds() / 3600.0
    if age_h < -1 or age_h > BOX_MAX_ASOF_AGE_HOURS:
        raise AssertionError(f"box asOf {as_of} is {age_h:.1f}h old (> {BOX_MAX_ASOF_AGE_HOURS}h)")
    missing: list[str] = []
    by_sha: dict[str, list[str]] = {}
    imaged = priced = 0
    for p in products:
        image = p.get("image") or {}
        src = image.get("src")
        if isinstance(src, str) and src.startswith("/market-assets/"):
            imaged += 1
            name = src.rsplit("/", 1)[-1]
            by_sha.setdefault(str(image.get("sha256") or name.split(".")[0]), []).append(str(p["id"]))
            for candidate in (name, name.replace(".webp", "_200.webp"), name.replace(".webp", "_600.webp")):
                if not (asset_root / candidate).is_file():
                    missing.append(candidate)
        if (p.get("price") or {}).get("usd") is not None:
            priced += 1
    if missing:
        raise AssertionError(f"missing box assets: {sorted(set(missing))[:10]}")
    coverage = box.get("coverage") or {}
    if coverage and (
        int(coverage.get("total") or -1) != len(products)
        or int(coverage.get("imaged") or -1) != imaged
        or int(coverage.get("priced") or -1) != priced
    ):
        raise AssertionError(
            f"box coverage {coverage} != counted total={len(products)} priced={priced} imaged={imaged}"
        )
    collisions = {s: sorted(v) for s, v in by_sha.items() if len(v) > 1}
    baseline: dict[str, list[str]] = {}
    if BOX_COLLISION_BASELINE.is_file():
        baseline = {
            k: sorted(v)
            for k, v in (json.loads(BOX_COLLISION_BASELINE.read_text(encoding="utf-8")).get("collisions") or {}).items()
        }
    unknown = {s: v for s, v in collisions.items() if baseline.get(s) != v}
    if unknown or len(collisions) > len(baseline):
        raise AssertionError(f"box image sha shared by multiple ids beyond baseline: {list(unknown.items())[:5]}")
    return {
        "boxProducts": len(products),
        "boxPriced": priced,
        "boxImaged": imaged,
        "boxAsOf": as_of,
        "boxAgeHours": round(age_h, 1),
        "boxPreviousProducts": prev_count,
        "boxShaCollisions": len(collisions),
    }


def validate(snapshot: dict[str, Any], asset_root: Path, now: datetime) -> dict[str, Any]:
    cards = [*(snapshot.get("top100") or []), *(snapshot.get("watchlist") or [])]
    expected_cards = int((snapshot.get("universe") or {}).get("memberCount") or 0)
    if expected_cards < 1:
        raise AssertionError("snapshot universe.memberCount is missing or invalid")
    if len(cards) != expected_cards:
        raise AssertionError(f"cards={len(cards)}, universe members={expected_cards}")
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

    if len(complete) < MINIMUM_COMPLETE_COLLECTOR_NUMBERS:
        raise AssertionError(
            f"complete collector numbers regressed: {len(complete)}"
            f" < {MINIMUM_COMPLETE_COLLECTOR_NUMBERS}"
        )
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
        "universeMemberCount": expected_cards,
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
        for index in range(1, SELF_TEST_COMPLETE)
    ] + [
        {
            "id": f"promo-{index}",
            "officialName": f"Promo SM{index}",
            "collectorNumber": {"display": f"SM{index}", "complete": False},
            "pricePsa10": {"asOf": now.isoformat()},
            "image": {},
        }
        for index in range(SELF_TEST_CARDS - SELF_TEST_COMPLETE)
    ]
    document = {
        "generation": {"id": "self-test"},
        "universe": {"memberCount": SELF_TEST_CARDS},
        "top100": cards[:100],
        "watchlist": cards[100:],
    }
    validate(document, Path("."), now)

    growth = json.loads(json.dumps(document))
    growth["watchlist"].append({
        "id": "growth-card",
        "officialName": "Growth Card NEW-001",
        "collectorNumber": {"display": "NEW-001", "complete": True},
        "pricePsa10": {"asOf": now.isoformat()},
        "image": {},
    })
    growth["universe"]["memberCount"] += 1
    validate(growth, Path("."), now)
    print("POSITIVE_OK universe growth is accepted when snapshot membership agrees")

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
    membership_drop = json.loads(json.dumps(document))
    membership_drop["watchlist"].pop()
    cases.append(("membership", membership_drop))
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
        math.floor(SELF_TEST_CARDS * AWAITING_FRESH_PRICE_MAX_RATIO),
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
            print(f"NEGATIVE_OK {label} fixture was rejected")
            continue
        raise AssertionError(f"negative self-test did not fire: {label}")

    import tempfile

    with tempfile.TemporaryDirectory() as folder:
        oversized = Path(folder) / "seed-snapshot.json"
        with oversized.open("wb") as handle:
            handle.truncate(SNAPSHOT_MAX_BYTES + 1)
        try:
            load_snapshot(oversized)
        except AssertionError:
            print("NEGATIVE_OK snapshot-size fixture was rejected")
        else:
            raise AssertionError("negative self-test did not fire: snapshot-size")

    with tempfile.TemporaryDirectory() as folder:
        assets = Path(folder)
        sha = "a" * 64
        for suffix in ("", "_200", "_600"):
            (assets / f"{sha}{suffix}.webp").write_bytes(b"webp")
        box = {
            "asOf": now.isoformat().replace("+00:00", "Z"),
            "coverage": {"total": 1, "priced": 1, "imaged": 1},
            "products": [
                {
                    "id": "optcg-en-op-01-booster-box-std",
                    "price": {"usd": 1.0},
                    "image": {"src": f"/market-assets/{sha}.webp", "sha256": sha},
                }
            ],
        }
        validate_box(box, None, assets, now)
        print("POSITIVE_OK box sidecar validates")
        stale = json.loads(json.dumps(box))
        stale["asOf"] = (now - timedelta(hours=48)).isoformat().replace("+00:00", "Z")
        box_cases = [
            ("box-stale-asof", stale, None),
            (
                "box-count-regression",
                box,
                {"products": [{"id": "a"}, {"id": "b"}]},
            ),
        ]
        missing_600 = json.loads(json.dumps(box))
        (assets / f"{sha}_600.webp").unlink()
        box_cases.append(("box-missing-asset", missing_600, None))
        for label, current, previous in box_cases:
            try:
                validate_box(current, previous, assets, now)
            except AssertionError:
                print(f"NEGATIVE_OK {label} fixture was rejected")
                continue
            raise AssertionError(f"negative self-test did not fire: {label}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--assets", type=Path)
    parser.add_argument("--box", type=Path, help="data/public/box-subset.json in the release tree")
    parser.add_argument("--box-previous", type=Path, help="HEAD copy of box-subset.json (may be empty)")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
    if args.snapshot:
        if args.assets is None:
            parser.error("--assets is required with --snapshot")
        snapshot = load_snapshot(args.snapshot)
        report = validate(snapshot, args.assets, datetime.now(timezone.utc))
        report["snapshotBytes"] = args.snapshot.stat().st_size
        if args.box:
            box = json.loads(args.box.read_text(encoding="utf-8"))
            previous = None
            if args.box_previous and args.box_previous.is_file() and args.box_previous.stat().st_size > 0:
                previous = json.loads(args.box_previous.read_text(encoding="utf-8"))
            report.update(validate_box(box, previous, args.assets, datetime.now(timezone.utc)))
        print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
