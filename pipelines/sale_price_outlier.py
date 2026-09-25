#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Outlier band + deterministic latest-sale picker for the PSA10 sale lane.

Owner 2026-08-23: the published PSA10 price is the latest REAL sale, and an
outrageous sale must not become that price.  Before this module the singles
path had no price-magnitude check at all (`insert_quote_revision` checked the
registry, `price>0` and sha shape and nothing else), and the only real trim in
the repo lived on the sealed line.  So the numbers here are deliberately the
sealed line's numbers -- one definition of "outlier" for the whole repo, not
two:

    sealed_price_compose.TRIM_HIGH=2.0 / TRIM_LOW=2.5 / SOLD_MIN_N=3

Measured on the live board (1,600 variants, latest sale vs median of the prior
<=10 sales): a [1/1.5, 1.5] band flags 52 cards (3.3%) -- that is ordinary
market movement, not contamination.  [M/2.5, M*2.0] flags 5 (0.3%), and the
gap between the highest ordinary ratio (1.94x) and the worst poison (7.82x) is
wide enough that the threshold is not sitting on top of real data.  The band is
asymmetric because the distribution is left-skewed (p10 0.823 vs p90 1.134):
a cheap sale is usually condition or a rushed listing, not poison.

The same band is used two ways, never with a second set of numbers:
  * one-sided (`select_latest_sale`): the headline quote walks back past a
    newest sale that sits outside the band of the sales BEFORE it.
  * two-sided (`is_isolated_price_outlier`, 2026-09-25): a sale is an isolated
    price spike only when it sits outside the band of the sales before it AND
    of the sales after it, on the same side.  This is what the PC sale
    quarantine receipt (pc_sale_title_quarantine.py) removes from the sales
    history.  Prior-only would flag ~901 PC sales on the live board and kill
    real regime shifts (variant 419 went ~$48 -> ~$101 around 2026-06-01 and
    stayed there); asking the following sales too flags ~550, and on Latias &
    Latios GX 170/181 exactly the three $1,485 / $1,908 / $4,662 sales among
    ~$17k neighbours.

This module is pure: no DB, no clock, no I/O.  Everything it needs arrives in
the `sales` sequence.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from statistics import median
from typing import Any, Iterator, Mapping, Sequence

# Copied from sealed_price_compose (46-49) on purpose -- see module docstring.
TRIM_HIGH = 2.0
TRIM_LOW = 2.5
SOLD_MIN_N = 3
PRIOR_LIMIT = 10
PRIOR_WINDOW_DAYS = 180
BAND_LABEL = (f"M/{TRIM_LOW}", f"M*{TRIM_HIGH}")

VERDICT_ACCEPTED = "accepted"
REASON_ABOVE = "above_band"
REASON_BELOW = "below_band"


def _price(value: Any) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.000001"))


def price_text(value: Any) -> str:
    """Same six-decimal text form current_quote_revision hashes."""

    return format(_price(value), "f")


def _sold_at(sale: Mapping[str, Any]) -> datetime:
    value = sale.get("soldAt")
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value
    text = str(value).replace("Z", "+00:00").replace(" ", "T")
    parsed = datetime.fromisoformat(text)
    return parsed.astimezone(timezone.utc).replace(tzinfo=None) if parsed.tzinfo else parsed


def _fingerprint(sale: Mapping[str, Any]) -> str:
    return str(sale.get("transactionFingerprint") or "")


def _unit_price(sale: Mapping[str, Any]) -> Decimal:
    return _price(sale.get("unitPriceUsd"))


def _pick_head(pool: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    """The one sale that represents `pool`'s newest moment.

    Tie-break order is fixed because payload_sha256 and quote_lineage_sha256
    both hash the chosen sale: two runs over the same rows must choose the same
    sale or every replay mints a new revision.

      1. newest sale day
      2. within that day, the latest `sold_at` (PriceCharting stores date-only,
         so this step usually decides nothing and step 3 does the work)
      3. the median unit price of that moment; an even count takes the LOWER
         middle, so a single inflated same-day listing cannot carry the day
      4. the smallest transaction fingerprint
    """

    newest_day = max(_sold_at(sale).date() for sale in pool)
    day_pool = [sale for sale in pool if _sold_at(sale).date() == newest_day]
    newest_stamp = max(_sold_at(sale) for sale in day_pool)
    moment = [sale for sale in day_pool if _sold_at(sale) == newest_stamp]
    prices = sorted(_unit_price(sale) for sale in moment)
    median_price = prices[(len(prices) - 1) // 2]
    tied = [sale for sale in moment if _unit_price(sale) == median_price]
    tied.sort(key=_fingerprint)
    return tied[0]


def ordered_candidates(sales: Sequence[Mapping[str, Any]]) -> Iterator[Mapping[str, Any]]:
    """Yield eligible sales newest-first under the deterministic tie-break."""

    pool = list(sales)
    while pool:
        head = _pick_head(pool)
        yield head
        pool = [sale for sale in pool if sale is not head]


def prior_sales(
    candidate: Mapping[str, Any],
    sales: Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    """Up to PRIOR_LIMIT eligible sales strictly older than `candidate`.

    Window is PRIOR_WINDOW_DAYS, not 30: on the live board a 30-day window
    leaves 137 cards with fewer than 3 prior sales (i.e. ungated), 180 days
    leaves 5.  A gate that cannot fire for 8% of the board is not a gate.
    """

    edge = _sold_at(candidate)
    floor = edge - timedelta(days=PRIOR_WINDOW_DAYS)
    older = [sale for sale in sales if floor <= _sold_at(sale) < edge]
    older.sort(key=lambda sale: (_sold_at(sale), _fingerprint(sale)), reverse=True)
    return older[:PRIOR_LIMIT]


def following_sales(
    candidate: Mapping[str, Any],
    sales: Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    """Mirror of prior_sales: up to PRIOR_LIMIT sales strictly NEWER than `candidate`.

    Same window, same limit, nearest first.  Strict on both sides, so a
    PriceCharting same-day sale (date-only sold_at) is neither before nor after
    the candidate and never votes on it.
    """

    edge = _sold_at(candidate)
    ceiling = edge + timedelta(days=PRIOR_WINDOW_DAYS)
    newer = [sale for sale in sales if edge < _sold_at(sale) <= ceiling]
    newer.sort(key=lambda sale: (_sold_at(sale), _fingerprint(sale)))
    return newer[:PRIOR_LIMIT]


def is_isolated_price_outlier(
    candidate: Mapping[str, Any],
    sales: Sequence[Mapping[str, Any]],
) -> str | None:
    """REASON_ABOVE / REASON_BELOW when `candidate` is an isolated spike, else None.

    Both neighbourhoods have to condemn it, each gated on its own (SOLD_MIN_N
    sales on that side, via is_price_outlier), and in the SAME direction.  A
    sale at the start of a new price level is out of band against the old level
    but inside the band of what followed, so a real regime shift is never
    flagged; a sale sitting between two levels (above the old, below the new)
    is not a spike either, so opposite verdicts are not a flag.  Newest sales
    have no followers yet and stay unflagged until the market answers.
    """

    bad_before, before = is_price_outlier(candidate, prior_sales(candidate, sales))
    if not bad_before:
        return None
    bad_after, after = is_price_outlier(candidate, following_sales(candidate, sales))
    if not bad_after or after["verdict"] != before["verdict"]:
        return None
    return str(before["verdict"])


def is_price_outlier(
    candidate: Mapping[str, Any],
    prior: Sequence[Mapping[str, Any]],
) -> tuple[bool, dict[str, Any]]:
    """(rejected?, evidence).  Fewer than SOLD_MIN_N priors => ungated accept."""

    unit = _unit_price(candidate)
    evidence: dict[str, Any] = {
        "band": list(BAND_LABEL),
        "medianUsd": None,
        "priorN": len(prior),
        "priorWindowDays": PRIOR_WINDOW_DAYS,
        "verdict": VERDICT_ACCEPTED,
        "ungated": False,
        "ratio": None,
    }
    if len(prior) < SOLD_MIN_N:
        evidence["ungated"] = True
        return False, evidence
    med = _price(median(sorted(_unit_price(sale) for sale in prior)))
    evidence["medianUsd"] = price_text(med)
    if med <= 0:
        evidence["ungated"] = True
        return False, evidence
    ratio = (unit / med).quantize(Decimal("0.001"))
    evidence["ratio"] = format(ratio, "f")
    if unit > med * Decimal(str(TRIM_HIGH)):
        evidence["verdict"] = REASON_ABOVE
        return True, evidence
    if unit < med / Decimal(str(TRIM_LOW)):
        evidence["verdict"] = REASON_BELOW
        return True, evidence
    return False, evidence


def select_latest_sale(
    sales: Sequence[Mapping[str, Any]],
) -> tuple[Mapping[str, Any] | None, dict[str, Any]]:
    """Newest eligible sale that survives the band, walking back on rejection.

    Every rejection is kept in `rejectedCandidates` even when the list ends up
    empty: a gate that throws away its rejections cannot tell "nothing came
    close" apart from "the filter never ran".
    """

    rejected: list[dict[str, Any]] = []
    evidence: dict[str, Any] = {
        "salesScanned": len(sales),
        "walkbacks": 0,
        "outlierGuard": None,
        "rejectedCandidates": rejected,
    }
    for candidate in ordered_candidates(sales):
        prior = prior_sales(candidate, sales)
        bad, guard = is_price_outlier(candidate, prior)
        if not bad:
            evidence["outlierGuard"] = guard
            evidence["walkbacks"] = len(rejected)
            return candidate, evidence
        rejected.append({
            "saleObservationId": candidate.get("saleObservationId"),
            "fingerprint": _fingerprint(candidate),
            "soldAt": _sold_at(candidate).isoformat(sep=" "),
            "unitPriceUsd": price_text(_unit_price(candidate)),
            "medianUsd": guard["medianUsd"],
            "priorN": guard["priorN"],
            "ratio": guard["ratio"],
            "reason": guard["verdict"],
        })
    evidence["walkbacks"] = len(rejected)
    return None, evidence
