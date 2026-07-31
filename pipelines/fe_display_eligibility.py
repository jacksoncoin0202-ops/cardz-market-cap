# -*- coding: utf-8 -*-
"""Frontend display eligibility for qualified universe (~900).

Policies applied:
- POLICY: snk_* + ebay (G10-path real market) exact; NEVER g10_kline / G10 index
- POLICY_FE_TOP100_LIQUIDITY: pure PSA10 sales in last 30d required for Top100 FE seat
- PC path: sales with external_entity_id like pc:%  OR pricecharting identity + ebay sales

Outputs:
  FRONTEND_DISPLAY_ELIGIBILITY.json / .md
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "pipelines"))

from qualified_pool_operator import db, load_env

OUT = ROOT / "data/runtime/private-reports/fill/FRONTEND_GAP_FILL"
OUT.mkdir(parents=True, exist_ok=True)

# Match QC-ish exact price after G10 policy
EXACT_PRICE_SOURCES = frozenset({"snk_psa10", "snk", "snkrdunk", "ebay"})  # no g10_kline
EXACT_GRADES = frozenset({"10", "PSA 10", "PSA10", "psa 10"})
MIN_PURE_PSA10_SALES_30D = 10


def launch_gate_from_qc(qc_card: Mapping[str, Any]) -> dict[str, Any]:
    """Use the canonical QC decision as the sole launch-readiness authority."""

    facts = qc_card.get("facts")
    facts = facts if isinstance(facts, Mapping) else {}
    sales = facts.get("sales30d")
    sales = sales if isinstance(sales, Mapping) else {}
    count = sales.get("purePsa10Count")
    sales_count = count if isinstance(count, int) and not isinstance(count, bool) else 0
    price = facts.get("price")
    price = price if isinstance(price, Mapping) else {}
    price_source = str(price.get("source") or "").casefold()
    try:
        price_value = float(price.get("valueUsd"))
    except (TypeError, ValueError):
        price_value = 0.0
    qc_passed = qc_card.get("decision") == "passed"
    return {
        "qcPassed": qc_passed,
        "salesCount": sales_count,
        "liquidityReady": qc_passed and sales_count >= MIN_PURE_PSA10_SALES_30D,
        "priceReady": (
            qc_passed
            and price_source in EXACT_PRICE_SOURCES
            and price_value > 0
        ),
        "priceSource": price.get("source"),
        "priceUsd": price.get("valueUsd"),
        "priceAt": price.get("asOf"),
    }


def latest_qc_report() -> Path | None:
    base = ROOT / "data/runtime/private-reports/canonical-db-qc"
    reps = sorted(base.glob("qc_*/report.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return reps[0] if reps else None


def main() -> int:
    load_env()
    conn = db()
    cur = conn.cursor()
    qc_path = latest_qc_report()
    qc_cards = {}
    if qc_path:
        rep = json.loads(qc_path.read_text(encoding="utf-8"))
        for c in rep.get("cards") or []:
            qc_cards[int(c["variantId"])] = c

    # Universe: prefer QC cards; else all variants with any market observation
    if qc_cards:
        vids = sorted(qc_cards.keys())
        universe = "qc_cards"
    else:
        cur.execute(
            """
            SELECT DISTINCT variant_id AS id FROM market_price_observation
            UNION
            SELECT DISTINCT variant_id FROM market_sale_observation
            """
        )
        vids = sorted(int(r["id"]) for r in cur.fetchall())
        universe = "observation_union"

    ph = ",".join(str(v) for v in vids) if vids else "0"

    # identities
    cur.execute(
        f"""
        SELECT variant_id, source_code, external_entity_id, match_status
        FROM catalog_source_identity
        WHERE variant_id IN ({ph})
        """
    )
    id_by: dict[int, list] = defaultdict(list)
    for r in cur.fetchall():
        id_by[int(r["variant_id"])].append(r)

    # latest exact-ish price
    cur.execute(
        f"""
        SELECT p.variant_id, p.source_code, p.price_usd, p.effective_at, p.metric_status
        FROM market_price_observation p
        INNER JOIN (
          SELECT variant_id, MAX(effective_at) mx
          FROM market_price_observation
          WHERE variant_id IN ({ph})
            AND source_code IN ('snk_psa10','snk','snkrdunk','ebay')
          GROUP BY variant_id
        ) t ON t.variant_id=p.variant_id AND t.mx=p.effective_at
        WHERE p.source_code IN ('snk_psa10','snk','snkrdunk','ebay')
        """
    )
    # may have multi source same timestamp — pick best later
    price_rows = list(cur.fetchall())
    prices: dict[int, list] = defaultdict(list)
    for r in price_rows:
        prices[int(r["variant_id"])].append(r)

    # sales 30d psa10
    cur.execute(
        f"""
        SELECT variant_id, source_code, external_entity_id, unit_price_usd, sold_at,
               coverage_status, timestamp_quality, grader_code, grade_label
        FROM market_sale_observation
        WHERE variant_id IN ({ph})
          AND sold_at >= (UTC_DATE() - INTERVAL 30 DAY)
        """
    )
    sales_all = list(cur.fetchall())
    sales_by: dict[int, list] = defaultdict(list)
    for r in sales_all:
        sales_by[int(r["variant_id"])].append(r)

    # names
    cur.execute(
        f"""
        SELECT id, canonical_name, collector_number, set_name, tcg_code
        FROM catalog_variant WHERE id IN ({ph})
        """
    )
    meta = {int(r["id"]): r for r in cur.fetchall()}

    rows = []
    for vid in vids:
        m = meta.get(vid) or {}
        qc = qc_cards.get(vid) or {}
        ids = id_by.get(vid) or []
        has_pc_id = any(
            i["source_code"] == "pricecharting" and i.get("match_status") == "exact" for i in ids
        )
        has_ebay_id = any(
            i["source_code"] == "ebay" and i.get("match_status") == "exact" for i in ids
        )
        has_snk_id = any(
            i["source_code"] in ("snkrdunk", "snk") and i.get("match_status") == "exact" for i in ids
        )

        px_list = prices.get(vid) or []
        # real market: ebay first, then SNK (never g10_kline / index)
        order = {"ebay": 0, "snk_psa10": 1, "snk": 2, "snkrdunk": 3}
        px_list_sorted = sorted(px_list, key=lambda r: order.get(str(r["source_code"]), 9))
        best_px = px_list_sorted[0] if px_list_sorted else None
        has_exact_price = best_px is not None
        decision = qc.get("decision")
        blockers = list(qc.get("blockers") or [])
        launch_gate = launch_gate_from_qc(qc)

        raw_sales = sales_by.get(vid) or []
        pure = []
        pc_sales = 0
        g10_uuid_sales = 0
        for s in raw_sales:
            g = str(s.get("grader_code") or "").upper()
            lab = str(s.get("grade_label") or "").upper().replace(" ", "")
            if g != "PSA" or lab not in ("10", "PSA10"):
                continue
            pure.append(s)
            ent = str(s.get("external_entity_id") or "")
            if ent.startswith("pc:"):
                pc_sales += 1
            elif "-" in ent and len(ent) > 30:
                g10_uuid_sales += 1

        # Keep raw observations diagnostic-only.  The named liquidity field
        # must mean the same >=10 threshold as the actual FE board gate.
        has_sales_30d = len(pure) >= MIN_PURE_PSA10_SALES_30D
        fe_top100_liquidity_ok = bool(launch_gate["liquidityReady"])
        mr = qc.get("marketRank")
        tcg = qc.get("tcg") or m.get("tcg_code")

        # Raw observations remain diagnostic only. Launch readiness comes from
        # the latest canonical QC card, which already validates source binding,
        # freshness, grade, sale quality, printing, and image evidence.
        price_ok = bool(launch_gate["priceReady"])
        price_stale = False
        if best_px and best_px.get("effective_at"):
            ea = best_px["effective_at"]
            if getattr(ea, "tzinfo", None) is None:
                ea = ea.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            if (now - ea).total_seconds() > 48 * 3600:
                price_stale = True
        fe_board_eligible = bool(
            launch_gate["qcPassed"]
            and launch_gate["priceReady"]
            and launch_gate["liquidityReady"]
        )

        rows.append(
            {
                "variantId": vid,
                "name": m.get("canonical_name"),
                "collector": m.get("collector_number"),
                "set": m.get("set_name"),
                "tcg": tcg,
                "marketRank": mr,
                "qcDecision": decision,
                "qcBlockers": blockers,
                "hasExactPrice": has_exact_price,
                "authoritativePriceReady": bool(launch_gate["priceReady"]),
                "priceSource": launch_gate["priceSource"],
                "priceUsd": (
                    str(launch_gate["priceUsd"])
                    if launch_gate["priceUsd"] is not None
                    else None
                ),
                "priceAt": launch_gate["priceAt"],
                "priceStale48h": price_stale,
                "priceOkPolicy": price_ok,
                "sales30dPsa10": int(launch_gate["salesCount"]),
                "observedSales30dPsa10": len(pure),
                "salesPcTagged": pc_sales,
                "salesG10UuidTagged": g10_uuid_sales,
                "hasSales30d": has_sales_30d,
                "hasPcIdentity": has_pc_id,
                "hasEbayIdentity": has_ebay_id,
                "hasSnkIdentity": has_snk_id,
                "feTop100LiquidityOk": fe_top100_liquidity_ok,
                "feBoardEligible": fe_board_eligible,
            }
        )

    # Board slices by marketRank + tcg
    def board(name, pred, limit=100):
        pool = [r for r in rows if pred(r) and r.get("marketRank") is not None]
        pool.sort(key=lambda r: int(r["marketRank"]))
        # Apply canonical QC launch readiness; raw observation presence is not
        # sufficient to occupy a public board seat.
        liquid = [r for r in pool if r["feTop100LiquidityOk"]]
        showable = [r for r in pool if r["feBoardEligible"]]
        top = showable[:limit]
        return {
            "name": name,
            "poolWithRank": len(pool),
            "withLiquidity30d": len(liquid),
            "withPriceAndLiquidity": len(showable),
            "topN": len(top),
            "seats": [
                {
                    "displayRank": i + 1,
                    "marketRank": r["marketRank"],
                    "variantId": r["variantId"],
                    "name": r["name"],
                    "priceSource": r["priceSource"],
                    "priceUsd": r["priceUsd"],
                    "sales30d": r["sales30dPsa10"],
                    "salesPc": r["salesPcTagged"],
                    "feBoardEligible": r["feBoardEligible"],
                }
                for i, r in enumerate(top)
            ],
            "excludedNoSalesInTop100MarketRank": [
                {
                    "marketRank": r["marketRank"],
                    "variantId": r["variantId"],
                    "name": r["name"],
                    "sales30d": r["sales30dPsa10"],
                }
                for r in pool
                if int(r["marketRank"]) <= 100 and not r["feTop100LiquidityOk"]
            ][:40],
        }

    boards = {
        "top100_pokemon": board(
            "top100_pokemon",
            lambda r: str(r.get("tcg") or "").lower() in ("pokemon", "poke"),
        ),
        "top100_one_piece": board(
            "top100_one_piece",
            lambda r: str(r.get("tcg") or "").lower() in ("one-piece", "one_piece", "op"),
        ),
        "top100_all_ranked": board("top100_all_ranked", lambda r: r.get("marketRank") is not None),
    }

    summary = {
        "asOf": datetime.now(timezone.utc).isoformat(),
        "qcReport": str(qc_path) if qc_path else None,
        "universe": universe,
        "n": len(rows),
        "counts": {
            "hasExactPrice": sum(1 for r in rows if r["hasExactPrice"]),
            "authoritativePriceReady": sum(
                1 for r in rows if r["authoritativePriceReady"]
            ),
            "priceOkPolicy": sum(1 for r in rows if r["priceOkPolicy"]),
            "hasSales30d": sum(1 for r in rows if r["hasSales30d"]),
            "feTop100LiquidityOk": sum(1 for r in rows if r["feTop100LiquidityOk"]),
            "feBoardEligible": sum(1 for r in rows if r["feBoardEligible"]),
            "hasPcIdentity": sum(1 for r in rows if r["hasPcIdentity"]),
            "salesWithPcTag": sum(1 for r in rows if r["salesPcTagged"] > 0),
            "priceSource": dict(Counter(r["priceSource"] for r in rows if r["priceSource"])),
        },
        "policies": [
            "POLICY_G10_EXACT_PRICE",
            "POLICY_FE_TOP100_LIQUIDITY",
            "POLICY_DB_PROVENANCE",
        ],
    }

    out = {"summary": summary, "boards": boards, "cards": rows}
    (OUT / "FRONTEND_DISPLAY_ELIGIBILITY.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )

    # markdown brief
    md = [
        "# Frontend Display Eligibility",
        "",
        f"asOf: {summary['asOf']}",
        f"QC: `{qc_path.parent.name if qc_path else 'n/a'}`",
        f"Universe n=**{summary['n']}** ({universe})",
        "",
        "## Counts",
        "",
        f"- hasExactPrice (SNK|G10|ebay): **{summary['counts']['hasExactPrice']}**",
        f"- priceOkPolicy (not stale 48h): **{summary['counts']['priceOkPolicy']}**",
        f"- hasSales30d PSA10: **{summary['counts']['hasSales30d']}**",
        f"- feBoardEligible (price+sales+print/img QC): **{summary['counts']['feBoardEligible']}**",
        f"- has PC identity: **{summary['counts']['hasPcIdentity']}**",
        f"- has PC-tagged sales (`pc:`): **{summary['counts']['salesWithPcTag']}**",
        f"- priceSource mix: `{summary['counts']['priceSource']}`",
        "",
        "## Top100 after liquidity filter (30d sales required)",
        "",
    ]
    for bname, b in boards.items():
        md.append(f"### {bname}")
        md.append(
            f"- ranked pool: {b['poolWithRank']} · with 30d sales: {b['withLiquidity30d']} · "
            f"price+liquidity: {b['withPriceAndLiquidity']} · seats filled: **{b['topN']}**/100"
        )
        md.append(f"- excluded (marketRank≤100, 0 sales): **{len(b['excludedNoSalesInTop100MarketRank'])}**")
        md.append("")
        md.append("| disp | mRank | vid | name | priceSrc | sales30d | PC sales |")
        md.append("|-----:|------:|----:|------|----------|---------:|---------:|")
        for s in b["seats"][:25]:
            md.append(
                f"| {s['displayRank']} | {s['marketRank']} | {s['variantId']} | "
                f"{(s['name'] or '')[:40]} | {s['priceSource']} | {s['sales30d']} | {s['salesPc']} |"
            )
        md.append("")

    (OUT / "FRONTEND_DISPLAY_ELIGIBILITY.md").write_text("\n".join(md), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    for bname, b in boards.items():
        print(
            bname,
            "pool",
            b["poolWithRank"],
            "liq",
            b["withLiquidity30d"],
            "show",
            b["withPriceAndLiquidity"],
            "seats",
            b["topN"],
            "excl0sales@top100",
            len(b["excludedNoSalesInTop100MarketRank"]),
        )
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
