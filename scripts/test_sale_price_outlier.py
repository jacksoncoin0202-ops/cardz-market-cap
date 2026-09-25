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

Section 8 (2026-09-25) pins the two-sided `is_isolated_price_outlier` that the
PC sale quarantine receipt uses, on two variants' REAL PriceCharting sales
(pulled read-only from market_sale_observation): Latias & Latios GX 170/181
(1148, three sub-$5k sales among ~$17k) and variant 419 (a $500 poison plus a
real ~$48 -> ~$101 level change on 2026-06-01 that prior-only would kill).
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
    following_sales,
    is_isolated_price_outlier,
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
# 8. two-sided isolated spike -- what the PC sale quarantine receipt removes
# --------------------------------------------------------------------------
def pc_sales(rows):
    """(saleObservationId, "MM-DD" in 2026, unit price) -> sale dicts, PC is date-only."""

    return [
        {
            "saleObservationId": sale_id,
            "soldAt": datetime(2026, int(month_day[:2]), int(month_day[3:])),
            "unitPriceUsd": price,
            "transactionFingerprint": f"pc{sale_id}",
        }
        for sale_id, month_day, price in rows
    ]


def isolated(sales):
    flagged = {}
    for item in sales:
        verdict = is_isolated_price_outlier(item, sales)
        if verdict:
            flagged[item["saleObservationId"]] = verdict
    return flagged


# Latias & Latios GX 170/181, every eligible PC PSA10 sale on 2026-09-25.
LATIAS_1148 = pc_sales([
    (1790527, "05-31", "17400"), (1790526, "06-01", "16900"), (1829886, "06-01", "18000"), (1829887, "06-01", "17942.6"),
    (1829885, "06-03", "16800"), (1829883, "06-05", "19200"), (1829884, "06-05", "15500"), (1829882, "06-08", "20000"),
    (1829881, "06-11", "18000"), (1829879, "06-12", "19999"), (1829880, "06-12", "17600"), (1829878, "06-14", "17170"),
    (1829877, "06-15", "10150"), (1829876, "06-16", "17588"), (1829875, "06-22", "19800"), (1829873, "06-25", "17800"),
    (1829874, "06-25", "17700"), (1829872, "06-26", "17100"), (1920226, "06-27", "1485"), (1829871, "06-29", "17700"),
    (1920225, "07-02", "1908.06"), (1829870, "07-03", "17150"), (1829869, "07-05", "15985.92"), (1829868, "07-09", "15600"),
    (1829866, "07-13", "15999"), (1829867, "07-13", "15900"), (1928800, "07-14", "18000"), (1920224, "07-17", "15000"),
    (1829865, "07-18", "15699"), (1829864, "07-19", "15450"), (1829863, "07-20", "17450"), (2089950, "07-21", "14600"),
    (1829862, "07-23", "15500"), (1928799, "07-30", "14988.88"), (2249919, "07-30", "4662.42"), (2089948, "08-01", "16000"),
    (2089949, "08-01", "15000"), (1829861, "08-03", "15300"), (1829860, "08-07", "15600"), (1829858, "08-09", "14900"),
    (1829859, "08-09", "15750"), (1928859, "08-12", "15150"), (2089946, "08-13", "18500"), (2089947, "08-13", "17500"),
    (2114694, "08-13", "18000"), (2089945, "08-14", "18000"), (2249917, "08-17", "15499"), (2249918, "08-17", "15000"),
    (2249916, "08-18", "15100"), (2249915, "08-20", "14000"), (2301553, "08-21", "17250"), (2180088, "08-23", "14200"),
    (2249914, "08-28", "21600"), (2301551, "08-28", "15399"), (2301552, "08-28", "15500"), (2301549, "08-29", "14500"),
    (2301550, "08-29", "15300"), (2288034, "08-30", "17376.34"), (2288035, "08-30", "16000"), (2288033, "08-31", "15300"),
    (2326457, "09-01", "15000"), (2326456, "09-03", "13900"), (2413285, "09-05", "20000"), (2364794, "09-06", "20100"),
    (2377172, "09-06", "15000"), (2377171, "09-07", "19500"), (2413283, "09-09", "18321.98"), (2413284, "09-09", "16575.86"),
    (2448505, "09-09", "16527.42"), (2448503, "09-13", "16500"), (2448504, "09-13", "15905"), (2460664, "09-14", "15600"),
    (2474173, "09-14", "14400"), (2520943, "09-18", "15750"), (2520944, "09-18", "15000"), (2557617, "09-22", "15100"),
])
flagged = isolated(LATIAS_1148)
check("Latias 1148: exactly the $1,485 / $1,908 / $4,662 sales are isolated spikes",
      flagged == {1920226: "below_band", 1920225: "below_band", 2249919: "below_band"}, str(flagged))

# Variant 419, every eligible PC PSA10 sale on 2026-09-25: a $500 poison on 04-27,
# and a REAL level change -- ~$48 until 05-26, ~$101 from 06-01 on, and it stayed.
V419 = pc_sales([
    (1622777, "04-02", "59.99"), (1718152, "04-05", "32"), (1718151, "04-13", "42"), (1718150, "04-19", "44"),
    (1718149, "04-27", "500"), (1718148, "04-28", "47"), (1718147, "04-30", "49.99"), (1718146, "05-04", "55.99"),
    (1718145, "05-23", "41"), (1718144, "05-26", "64.99"), (1718143, "06-01", "100"), (1622764, "06-02", "110.64"),
    (1718140, "06-02", "134.54"), (1718141, "06-02", "107.31"), (1718142, "06-02", "100"), (1718139, "06-06", "119.99"),
    (1718138, "06-09", "95"), (1718137, "06-10", "107.5"), (1718136, "06-12", "115.5"), (1718135, "06-17", "91"),
    (1718134, "06-20", "91"), (1718133, "06-22", "116"), (1718132, "06-24", "59.99"), (1718131, "06-28", "91"),
    (1718129, "06-30", "110"), (1718130, "06-30", "100"), (1718128, "07-02", "115"), (1718126, "07-13", "119"),
    (1718127, "07-13", "129.99"), (1718125, "07-17", "83.32"), (1987329, "08-13", "94"), (2572412, "08-27", "117"),
    (2340712, "09-04", "149"), (2353344, "09-04", "100"),
])
V419_NEW_LEVEL = (1718143, 1622764, 1718140, 1718141, 1718142)
by_id = {item["saleObservationId"]: item for item in V419}
flagged = isolated(V419)
check("variant 419: the new-level sales ARE above band against the old level (fixture has teeth)",
      all(is_price_outlier(by_id[i], prior_sales(by_id[i], V419))[1]["verdict"] == "above_band"
          for i in V419_NEW_LEVEL), "")
check("variant 419: a real level change is not a spike; only the $500 poison is",
      flagged == {1718149: "above_band"}, str(flagged))

# the followers side is gated exactly like the prior side
steady = [sale(40 + i, "100.00", f"f{i:02d}") for i in range(PRIOR_LIMIT)]
spike = sale(20, "1000.00", "spike")
two_after = [sale(10, "100.00", "a1"), sale(5, "100.00", "a2")]
check(f"only {SOLD_MIN_N - 1} followers cannot condemn a sale (followers side ungated)",
      is_isolated_price_outlier(spike, steady + [spike] + two_after) is None,
      str(is_price_outlier(spike, following_sales(spike, steady + [spike] + two_after))))
check("control: a third follower makes the same sale an above_band spike",
      is_isolated_price_outlier(spike, steady + [spike] + two_after + [sale(2, "100.00", "a3")])
      == "above_band", "")
check("the newest sale has no followers and is never flagged, however wild",
      is_isolated_price_outlier(spike, steady + [spike]) is None, "")
same_day = sale(20, "100.00", "same-day", minute=0)
check("a same-day sale is neither before nor after the candidate",
      following_sales(spike, [spike, same_day]) == [] and prior_sales(spike, [spike, same_day]) == [], "")
deep_after = [sale(19 - i, "100.00", f"g{i:02d}") for i in range(PRIOR_LIMIT + 5)]
check(f"followers are the NEAREST {PRIOR_LIMIT}, oldest first",
      [item["transactionFingerprint"] for item in following_sales(spike, deep_after)]
      == [f"g{i:02d}" for i in range(PRIOR_LIMIT)], "")

# a sale between two levels (above the old, below the new) is not a spike
low_level = [sale(60 + i, "100.00", f"l{i:02d}") for i in range(5)]
between = sale(40, "250.00", "between")
high_level = [sale(30 - i, "1000.00", f"h{i:02d}") for i in range(5)]
v_pool = low_level + [between] + high_level
check("between-levels fixture really has opposite one-sided verdicts",
      is_price_outlier(between, prior_sales(between, v_pool))[1]["verdict"] == "above_band"
      and is_price_outlier(between, following_sales(between, v_pool))[1]["verdict"] == "below_band", "")
check("opposite verdicts on the two sides are not an isolated spike",
      is_isolated_price_outlier(between, v_pool) is None, "")

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
