#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Sale outlier band + deterministic latest-sale pick (no DB).

Owner 2026-08-23 moved the published PSA10 price onto the latest real sale.
The band is the only thing standing between a mis-listed lot and the front
page, so every fixture below is a MEASURED shape from the live board, not an
invented one:

  * 7.821x  variant 1444, 2026-07-23 $400.00 against a $51.14 prior median
  * 0.324x  variant 1254, 2026-08-17 $1,375.00 against a $4,249.99 prior median
  * 1.94x   the highest ratio that is ordinary market movement -- it MUST pass,
            or the gate is fighting the market instead of filtering poison

The last check is a min-hit assert: the fixture set itself must produce at
least one reject, one plain accept and one ungated accept.  Without it this
file could go green while `select_latest_sale` silently stopped rejecting.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from sale_price_outlier import (  # noqa: E402
    PRIOR_LIMIT,
    PRIOR_WINDOW_DAYS,
    SOLD_MIN_N,
    TRIM_HIGH,
    TRIM_LOW,
    is_price_outlier,
    ordered_candidates,
    prior_sales,
    select_latest_sale,
)

FAILED: list[str] = []
VERDICTS: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    if ok:
        print(f"ok   {label}")
    else:
        FAILED.append(label)
        print(f"FAIL {label}{': ' + detail if detail else ''}")


BASE = datetime(2026, 8, 20, 0, 0, 0)


def sale(days_ago: int, price: str, fingerprint: str, *, sale_id: int = 0, minute: int = 0):
    return {
        "saleObservationId": sale_id or (10_000 + days_ago * 10 + minute),
        "soldAt": BASE - timedelta(days=days_ago) + timedelta(minutes=minute),
        "unitPriceUsd": price,
        "transactionFingerprint": fingerprint,
    }


def run(sales):
    chosen, evidence = select_latest_sale(sales)
    guard = evidence.get("outlierGuard") or {}
    if chosen is None:
        VERDICTS.append("none")
    elif guard.get("ungated"):
        VERDICTS.append("ungated")
    else:
        VERDICTS.append("accepted")
    for item in evidence["rejectedCandidates"]:
        VERDICTS.append(item["reason"])
    return chosen, evidence


# --------------------------------------------------------------------------
# 1. the 7.821x poison is rejected and the walk-back takes the next sale
# --------------------------------------------------------------------------
prior_block = [sale(10 + i, "51.14", f"p{i:02d}") for i in range(PRIOR_LIMIT)]
poison = [sale(1, "400.00", "poison")] + [sale(3, "52.00", "good")] + prior_block
chosen, evidence = run(poison)
check("7.8x sale is rejected", chosen is not None and chosen["transactionFingerprint"] == "good",
      str(chosen))
check("rejection is recorded with a reason",
      [r["reason"] for r in evidence["rejectedCandidates"]] == ["above_band"],
      str(evidence["rejectedCandidates"]))
check("walk-back count is reported", evidence["walkbacks"] == 1, str(evidence["walkbacks"]))
check("accepted sale carries its own guard evidence",
      (evidence["outlierGuard"] or {}).get("verdict") == "accepted"
      and (evidence["outlierGuard"] or {}).get("priorN") == PRIOR_LIMIT,
      str(evidence["outlierGuard"]))

# --------------------------------------------------------------------------
# 2. the 0.324x collapse is rejected too (band is asymmetric, not one-sided)
# --------------------------------------------------------------------------
deep_prior = [sale(10 + i, "4249.99", f"d{i:02d}") for i in range(PRIOR_LIMIT)]
collapse = [sale(1, "1375.00", "collapse"), sale(4, "4100.00", "ok-low")] + deep_prior
chosen, evidence = run(collapse)
check("0.32x sale is rejected", chosen is not None and chosen["transactionFingerprint"] == "ok-low",
      str(chosen))
check("low rejection is labelled below_band",
      [r["reason"] for r in evidence["rejectedCandidates"]] == ["below_band"],
      str(evidence["rejectedCandidates"]))

# --------------------------------------------------------------------------
# 3. 1.94x -- the highest ordinary ratio measured on the board -- must PASS
# --------------------------------------------------------------------------
ordinary = [sale(1, "194.00", "ordinary")] + [
    sale(10 + i, "100.00", f"o{i:02d}") for i in range(PRIOR_LIMIT)
]
chosen, evidence = run(ordinary)
check("1.94x is ordinary movement and passes",
      chosen is not None and chosen["transactionFingerprint"] == "ordinary", str(chosen))
check("no rejection is invented for an in-band sale",
      evidence["rejectedCandidates"] == [], str(evidence["rejectedCandidates"]))
# and the two boundaries behave the way the constants say
check("exactly TRIM_HIGH passes (band is inclusive at the top)",
      is_price_outlier({"unitPriceUsd": "200", "soldAt": BASE, "transactionFingerprint": "x"},
                       [sale(5, "100.00", f"b{i}") for i in range(SOLD_MIN_N)])[0] is False, "")
check("just over TRIM_HIGH is rejected",
      is_price_outlier({"unitPriceUsd": "200.01", "soldAt": BASE, "transactionFingerprint": "x"},
                       [sale(5, "100.00", f"b{i}") for i in range(SOLD_MIN_N)])[0] is True, "")
check("just under M/TRIM_LOW is rejected",
      is_price_outlier({"unitPriceUsd": "39.99", "soldAt": BASE, "transactionFingerprint": "x"},
                       [sale(5, "100.00", f"b{i}") for i in range(SOLD_MIN_N)])[0] is True, "")

# --------------------------------------------------------------------------
# 4. fewer than SOLD_MIN_N priors => ungated accept, however wild the price
# --------------------------------------------------------------------------
thin = [sale(1, "9999.00", "wild"), sale(9, "10.00", "t1"), sale(20, "10.00", "t2")]
chosen, evidence = run(thin)
check("n<3 prior sales is ungated, not rejected",
      chosen is not None and chosen["transactionFingerprint"] == "wild", str(chosen))
check("ungated is stated in the evidence, never implied",
      (evidence["outlierGuard"] or {}).get("ungated") is True
      and (evidence["outlierGuard"] or {}).get("medianUsd") is None,
      str(evidence["outlierGuard"]))

# --------------------------------------------------------------------------
# 5. determinism: same-day, same-price, different fingerprint
# --------------------------------------------------------------------------
same_day = [
    sale(1, "120.00", "zzz"),
    sale(1, "120.00", "aaa"),
    sale(1, "130.00", "mmm"),
] + [sale(10 + i, "120.00", f"s{i:02d}") for i in range(PRIOR_LIMIT)]
first, _ = select_latest_sale(same_day)
second, _ = select_latest_sale(list(reversed(same_day)))
check("same-day pick is stable across input order",
      first is not None and second is not None
      and first["transactionFingerprint"] == second["transactionFingerprint"],
      f"{first} vs {second}")
check("same-day pick takes the median price, fingerprint breaks the tie",
      first is not None and first["transactionFingerprint"] == "aaa"
      and str(first["unitPriceUsd"]) == "120.00",
      str(first))
run(same_day)

# an even count of distinct same-day prices takes the LOWER middle
even_day = [sale(1, "100.00", "a"), sale(1, "500.00", "b")] + [
    sale(10 + i, "100.00", f"e{i:02d}") for i in range(PRIOR_LIMIT)
]
even_pick, _ = select_latest_sale(even_day)
check("even same-day count takes the lower middle price",
      even_pick is not None and even_pick["transactionFingerprint"] == "a", str(even_pick))

# --------------------------------------------------------------------------
# 6. prior window / prior limit really bind
# --------------------------------------------------------------------------
edge = sale(0, "100.00", "edge")
inside = [sale(PRIOR_WINDOW_DAYS - 1, "10.00", "in")]
outside = [sale(PRIOR_WINDOW_DAYS + 1, "10.00", "out")]
window = prior_sales(edge, [edge] + inside + outside)
check(f"prior window is exactly {PRIOR_WINDOW_DAYS}d and excludes the candidate",
      [item["transactionFingerprint"] for item in window] == ["in"], str(window))
deep = [sale(1 + i, "10.00", f"n{i:02d}") for i in range(PRIOR_LIMIT + 5)]
check(f"prior list is capped at {PRIOR_LIMIT}",
      len(prior_sales(edge, deep)) == PRIOR_LIMIT, str(len(prior_sales(edge, deep))))
check("prior list is the NEWEST ones, not an arbitrary slice",
      [item["transactionFingerprint"] for item in prior_sales(edge, deep)]
      == [f"n{i:02d}" for i in range(PRIOR_LIMIT)], "")

# --------------------------------------------------------------------------
# 7. every candidate rejected => no eligible sale, and the rejections survive
# --------------------------------------------------------------------------
all_bad = [sale(1, "5000.00", "x1"), sale(2, "4000.00", "x2")] + [
    sale(10 + i, "100.00", f"z{i:02d}") for i in range(PRIOR_LIMIT)
]
chosen, evidence = run(all_bad)
check("a card whose every recent sale is poison still resolves to a sale",
      chosen is not None, str(chosen))
check("rejections are kept even when a later candidate wins",
      len(evidence["rejectedCandidates"]) >= 2, str(len(evidence["rejectedCandidates"])))

nothing, empty_evidence = select_latest_sale([])
check("no sales at all returns None with an empty rejection list, never a crash",
      nothing is None and empty_evidence["rejectedCandidates"] == []
      and empty_evidence["salesScanned"] == 0, str(empty_evidence))

# ordering helper is newest-first
order = [item["transactionFingerprint"] for item in ordered_candidates(poison)]
check("ordered_candidates yields newest first", order[0] == "poison" and order[1] == "good",
      str(order[:3]))

# --------------------------------------------------------------------------
# min-hit: the fixture set must exercise all three outcomes
# --------------------------------------------------------------------------
check("fixtures produced at least one above_band rejection", "above_band" in VERDICTS, str(VERDICTS))
check("fixtures produced at least one below_band rejection", "below_band" in VERDICTS, str(VERDICTS))
check("fixtures produced at least one plain accept", "accepted" in VERDICTS, str(VERDICTS))
check("fixtures produced at least one ungated accept", "ungated" in VERDICTS, str(VERDICTS))
check("constants still equal the sealed line's",
      (TRIM_HIGH, TRIM_LOW, SOLD_MIN_N) == (2.0, 2.5, 3),
      f"{TRIM_HIGH}/{TRIM_LOW}/{SOLD_MIN_N}")

print()
if FAILED:
    print(f"FAILED {len(FAILED)}: {FAILED}")
    raise SystemExit(1)
print("all sale outlier band checks passed")
