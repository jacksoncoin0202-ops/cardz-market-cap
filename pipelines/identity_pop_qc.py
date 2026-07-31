#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Full-universe identity + POP QC (operator 2026-07-30).

Verifies every POP-eligible card in the DB against the four-source identity
criteria. This is NOT a rubber stamp of releaseReady — it re-checks whether
the printing behind each high-POP seat is consistent enough to trust ranking.

Criteria: docs/IDENTITY_VERIFICATION_CRITERIA.md

Usage:
  python -X utf8 pipelines/identity_pop_qc.py --run-id id_pop_qc_20260730_r1
  python -X utf8 pipelines/identity_pop_qc.py --run-id ... --min-pop 1000
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from snk_image_promotion import db  # noqa: E402

OUT_ROOT = ROOT / "data/runtime/private-reports/identity-pop-qc"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def load_universe(cur, min_pop: int) -> list[dict[str, Any]]:
    """POP-eligible seats: latest PSA top_grade_population >= min_pop."""
    cur.execute(
        """
        SELECT v.id AS variant_id, v.opaque_id, v.tcg_code, v.canonical_name,
               v.set_name, v.collector_number, v.identity_status,
               p.top_grade_population AS psa10_pop,
               p.total_population AS total_pop,
               p.effective_at AS pop_effective_at,
               p.external_entity_id AS pop_external_id,
               p.source_code AS pop_source
        FROM catalog_variant v
        INNER JOIN (
          SELECT variant_id, MAX(effective_at) AS mx
          FROM market_grader_population_observation
          WHERE grader_code='psa' AND top_grade_population IS NOT NULL
          GROUP BY variant_id
        ) latest ON latest.variant_id = v.id
        INNER JOIN market_grader_population_observation p
          ON p.variant_id = latest.variant_id
         AND p.effective_at = latest.mx
         AND p.grader_code = 'psa'
        WHERE p.top_grade_population >= %s
        ORDER BY p.top_grade_population DESC, v.id
        """,
        (min_pop,),
    )
    return list(cur.fetchall())


def identities_for(cur, variant_id: int) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT source_code, external_entity_id, match_status
        FROM catalog_source_identity
        WHERE variant_id=%s
        ORDER BY source_code, match_status
        """,
        (variant_id,),
    )
    return list(cur.fetchall())


def all_pops_for(cur, variant_id: int) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT source_code, external_entity_id, top_grade_population, total_population, effective_at
        FROM market_grader_population_observation
        WHERE variant_id=%s AND grader_code='psa'
        ORDER BY effective_at DESC
        LIMIT 12
        """,
        (variant_id,),
    )
    return list(cur.fetchall())


def prices_for(cur, variant_id: int) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT source_code, price_usd, effective_at, metric_status
        FROM market_price_observation
        WHERE variant_id=%s AND metric_status='ready'
        ORDER BY effective_at DESC
        LIMIT 8
        """,
        (variant_id,),
    )
    return list(cur.fetchall())


def sales_psa10_30d(cur, variant_id: int) -> dict[str, Any]:
    cur.execute(
        """
        SELECT source_code, COUNT(*) AS n, AVG(unit_price_usd) AS avg_p
        FROM market_sale_observation
        WHERE variant_id=%s AND LOWER(grader_code)='psa'
          AND grade_label IN ('10','PSA 10','psa10')
          AND sold_at >= (UTC_DATE() - INTERVAL 30 DAY)
        GROUP BY source_code
        """,
        (variant_id,),
    )
    rows = list(cur.fetchall())
    total = sum(int(r["n"]) for r in rows)
    return {"by_source": rows, "total": total}


def ledger_sources(cur, variant_id: int) -> set[str]:
    try:
        cur.execute(
            """
            SELECT DISTINCT source_code FROM catalog_identity_evidence
            WHERE variant_id=%s
            """,
            (variant_id,),
        )
        return {str(r["source_code"]) for r in cur.fetchall()}
    except Exception:
        return set()


def evaluate_card(cur, row: dict[str, Any]) -> dict[str, Any]:
    vid = int(row["variant_id"])
    ids = identities_for(cur, vid)
    pops = all_pops_for(cur, vid)
    prices = prices_for(cur, vid)
    sales = sales_psa10_30d(cur, vid)
    ledger = ledger_sources(cur, vid)

    gem_exact = [
        i for i in ids if i["source_code"] == "gemrate" and i["match_status"] == "exact"
    ]
    gem_conflict = [
        i for i in ids if i["source_code"] == "gemrate" and i["match_status"] == "conflict"
    ]
    snk_exact = [
        i
        for i in ids
        if i["source_code"] in ("snkrdunk", "snk_psa10", "snk")
        and i["match_status"] == "exact"
    ]
    # only numeric SNK apparel ids count as real SNK catalog
    snk_numeric = [
        i for i in snk_exact if re.fullmatch(r"\d+", str(i["external_entity_id"] or ""))
    ]
    pc_bind = [i for i in ids if i["source_code"] == "pricecharting"]
    has_pc_ledger = "pricecharting" in ledger

    flags: list[str] = []
    # Four-source presence (criteria IDENTITY_VERIFICATION_CRITERIA)
    has_gemrate = len(gem_exact) == 1
    has_snk = len(snk_numeric) >= 1
    has_pc = bool(pc_bind) or has_pc_ledger
    has_psa_align = False  # we use GemRate POP as PSA10 authority; flag if dual POP diverge

    if len(gem_exact) == 0:
        flags.append("no_gemrate_exact")
    if len(gem_exact) > 1:
        flags.append("multi_gemrate_exact")
    if gem_conflict:
        flags.append("gemrate_conflict_present")
    if not snk_numeric:
        flags.append("no_snk_exact_numeric")
    if len(snk_numeric) > 1:
        flags.append("multi_snk_exact")
    if not has_pc:
        flags.append("no_pricecharting_evidence")
    # PSA official: not yet bound as source — criteria still requires eventual align
    flags.append("psa_official_not_attached")  # will clear when we have PSA URL evidence

    # POP integrity: multiple distinct gemrate external ids with materially different PSA10
    pop_by_entity: dict[str, list[int]] = {}
    for p in pops:
        eid = str(p.get("external_entity_id") or "")
        # normalize
        bare = eid.replace("gemrate:", "")
        val = p.get("top_grade_population")
        if val is None:
            continue
        pop_by_entity.setdefault(bare, []).append(int(val))
    if len(pop_by_entity) > 1:
        latest = {k: max(v) for k, v in pop_by_entity.items()}
        vals = list(latest.values())
        if max(vals) > 0 and min(vals) / max(vals) < 0.7:
            flags.append(
                f"pop_entity_diverge:{json.dumps(latest, sort_keys=True)}"
            )
            has_psa_align = False
        else:
            has_psa_align = True  # close enough
    elif len(pop_by_entity) == 1:
        has_psa_align = True
    else:
        flags.append("no_pop_rows")

    # Clear psa flag if we only mean "gemrate is PSA authority and single entity"
    if has_psa_align and "psa_official_not_attached" in flags:
        # keep the flag as coverage gap, not identity fail — separate severity
        pass

    # price vs sales 3x when both exist
    snk_price = next(
        (float(p["price_usd"]) for p in prices if p["source_code"] in ("snk_psa10", "snk", "snkrdunk")),
        None,
    )
    if snk_price and sales["total"] >= 3:
        avgs = [
            float(r["avg_p"])
            for r in sales["by_source"]
            if r["avg_p"] is not None and float(r["avg_p"]) > 0
        ]
        if avgs:
            avg = sum(avgs) / len(avgs)
            if avg > 0 and (snk_price / avg > 3 or avg / snk_price > 3):
                flags.append(f"price_vs_sales30d_3x price={snk_price:.2f} sales_avg={avg:.2f}")

    collector = str(row.get("collector_number") or "")
    if not collector or collector.upper() in ("UNKNOWN", "NONE"):
        flags.append("collector_unknown")

    # severity
    hard = {
        "no_gemrate_exact",
        "multi_gemrate_exact",
        "collector_unknown",
        "no_pop_rows",
    }
    identity_hard_fail = any(
        f in hard or f.startswith("pop_entity_diverge") for f in flags
    )
    four_source_ok = (
        has_gemrate
        and has_snk
        and has_pc
        and has_psa_align
        and not identity_hard_fail
    )

    # ranking trust: POP eligible but identity incomplete → board risk
    ranking_trust = "trusted" if four_source_ok and not identity_hard_fail else (
        "identity_risk" if identity_hard_fail else "incomplete_four_source"
    )

    return {
        "variantId": vid,
        "opaqueId": row.get("opaque_id"),
        "tcg": row.get("tcg_code"),
        "name": row.get("canonical_name"),
        "set": row.get("set_name"),
        "collector": collector,
        "psa10_pop": int(row["psa10_pop"]) if row.get("psa10_pop") is not None else None,
        "total_pop": int(row["total_pop"]) if row.get("total_pop") is not None else None,
        "pop_effective_at": str(row.get("pop_effective_at")),
        "pop_external_id": row.get("pop_external_id"),
        "gem_exact": [i["external_entity_id"] for i in gem_exact],
        "gem_conflict": [i["external_entity_id"] for i in gem_conflict],
        "snk_exact": [i["external_entity_id"] for i in snk_numeric],
        "has_pricecharting": has_pc,
        "has_psa_pop_align": has_psa_align,
        "four_source_ok": four_source_ok,
        "ranking_trust": ranking_trust,
        "flags": flags,
        "sales30d_psa10": sales["total"],
        "price_sources": list({p["source_code"] for p in prices}),
        "identity_hard_fail": identity_hard_fail,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--min-pop", type=int, default=1000)
    ap.add_argument(
        "--out-root",
        type=Path,
        default=OUT_ROOT,
    )
    args = ap.parse_args()

    out_dir = args.out_root / args.run_id
    if out_dir.exists():
        # immutable: refuse overwrite with different content later; if exists fail
        existing = out_dir / "report.json"
        if existing.exists():
            raise SystemExit(f"immutable QC already exists: {out_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)

    conn = db()
    cur = conn.cursor()
    universe = load_universe(cur, args.min_pop)
    cards = []
    for row in universe:
        cards.append(evaluate_card(cur, row))

    # sort by pop desc
    cards.sort(key=lambda c: (-(c["psa10_pop"] or 0), c["variantId"]))

    trusted = [c for c in cards if c["ranking_trust"] == "trusted"]
    incomplete = [c for c in cards if c["ranking_trust"] == "incomplete_four_source"]
    risk = [c for c in cards if c["ranking_trust"] == "identity_risk"]

    flag_counts: dict[str, int] = {}
    for c in cards:
        for f in c["flags"]:
            key = f.split(":")[0]
            flag_counts[key] = flag_counts.get(key, 0) + 1

    report = {
        "kind": "identity-pop-qc",
        "schemaVersion": 1,
        "runId": args.run_id,
        "asOf": utc_now(),
        "criteria": "docs/IDENTITY_VERIFICATION_CRITERIA.md",
        "minPop": args.min_pop,
        "universeCount": len(cards),
        "summary": {
            "trusted_four_source": len(trusted),
            "incomplete_four_source": len(incomplete),
            "identity_risk": len(risk),
            "flag_counts": flag_counts,
        },
        "rankingImplication": {
            "note": "Only trusted seats should be treated as identity-safe for board. incomplete/risk may still have POP>=1000 but ranks can change after rebind/demote.",
            "pop_eligible_but_not_trusted": len(incomplete) + len(risk),
        },
        "cards": cards,
        "riskCards": risk,
        "incompleteSample": incomplete[:80],
    }

    raw = json.dumps(report, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    report["reportSha256"] = sha256_bytes(raw)
    report_path = out_dir / "report.json"
    report_path.write_bytes(
        json.dumps(report, ensure_ascii=False, indent=2, default=str).encode("utf-8")
    )
    # recompute sha of written pretty form for receipt binding
    written = report_path.read_bytes()
    receipt = {
        "kind": "identity-pop-qc-receipt",
        "runId": args.run_id,
        "asOf": report["asOf"],
        "reportPath": str(report_path.relative_to(ROOT)).replace("\\", "/"),
        "reportSha256": sha256_bytes(written),
        "summary": report["summary"],
        "universeCount": len(cards),
        "minPop": args.min_pop,
    }
    receipt_path = out_dir / "receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2, ensure_ascii=False), encoding="utf-8")

    # human-facing summary tables
    summary_md = out_dir / "SUMMARY.md"
    lines = [
        f"# Identity+POP QC `{args.run_id}`",
        "",
        f"- asOf: `{report['asOf']}`",
        f"- min POP: **{args.min_pop}**",
        f"- universe (POP-eligible): **{len(cards)}**",
        f"- four-source **trusted**: **{len(trusted)}**",
        f"- incomplete four-source: **{len(incomplete)}**",
        f"- identity **risk**: **{len(risk)}**",
        "",
        "## Flag counts",
        "",
    ]
    for k, v in sorted(flag_counts.items(), key=lambda x: -x[1]):
        lines.append(f"- `{k}`: {v}")
    lines += ["", "## Identity risk (hard) — top by POP", ""]
    lines.append("| pop | vid | name | collector | flags |")
    lines.append("|----:|----:|------|-----------|-------|")
    for c in risk[:40]:
        lines.append(
            f"| {c['psa10_pop']} | {c['variantId']} | {c['name']} | {c['collector']} | {', '.join(c['flags'][:4])} |"
        )
    lines += ["", "## Incomplete four-source — top by POP (first 40)", ""]
    lines.append("| pop | vid | name | collector | missing flags |")
    lines.append("|----:|----:|------|-----------|---------------|")
    for c in incomplete[:40]:
        miss = [f for f in c["flags"] if f.startswith("no_") or "not_attached" in f]
        lines.append(
            f"| {c['psa10_pop']} | {c['variantId']} | {c['name']} | {c['collector']} | {', '.join(miss)} |"
        )
    summary_md.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # also write PROGRESS slice
    progress = {
        "asOf": report["asOf"],
        "identityPopQcRunId": args.run_id,
        "popEligible": len(cards),
        "trusted": len(trusted),
        "incomplete": len(incomplete),
        "identityRisk": len(risk),
        "note": "Board ranks may change after identity rebind; ready count is not identity-trust count.",
    }
    prog_path = (
        ROOT
        / "data/runtime/private-reports/fill/MAIN-PROGRESS/IDENTITY_POP_QC_LATEST.json"
    )
    prog_path.write_text(json.dumps(progress, indent=2), encoding="utf-8")

    print(
        json.dumps(
            {
                "action": "identity-pop-qc",
                "runId": args.run_id,
                "universe": len(cards),
                "trusted": len(trusted),
                "incomplete": len(incomplete),
                "risk": len(risk),
                "report": str(report_path),
                "summary": str(summary_md),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
