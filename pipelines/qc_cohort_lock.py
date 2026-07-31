#!/usr/bin/env python3
"""Materialize a hash-bound canonical-QC cohort as a non-current staging lock.

This is deliberately not a formal universe promotion and never changes the
active lock or any public pointer.  Its only purpose is to preserve the exact
QC discovery cohort for later, separately-gated work.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

from universe_authority import (
    CANONICAL_DATABASE,
    _assert_canonical_database,
    _environment_values,
    _add_connection_args,
    canonical_json,
    connect_from_values,
)


ADVISORY_LOCK = "cardz_market_cap_qc_cohort_lock"
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "data" / "runtime" / "config" / "backend.env"


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def load_db_env(path: Path = DEFAULT_CONFIG) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def load_staging_plan(report_path: Path, expected_candidate_sha256: str) -> dict[str, Any]:
    """Validate receipt-bound QC evidence and derive an idempotent staging plan."""

    expected = str(expected_candidate_sha256 or "").strip().casefold()
    if len(expected) != 64 or any(char not in "0123456789abcdef" for char in expected):
        raise ValueError("--expected-universe-candidate-sha256 must be 64 hexadecimal characters")
    raw_report = report_path.resolve().read_bytes()
    report = json.loads(raw_report)
    receipt = json.loads(report_path.resolve().with_name("receipt.json").read_text(encoding="utf-8"))
    universe = report.get("universe") if isinstance(report, Mapping) else None
    if not isinstance(universe, Mapping) or universe.get("schemaVersion") != "qc-discovery-v1":
        raise RuntimeError("QC report universe contract is invalid")
    report_sha = _sha256(raw_report)
    if (
        str(universe.get("candidateSha256") or "").casefold() != expected
        or str(receipt.get("universeCandidateSha256") or "").casefold() != expected
        or str(receipt.get("reportSha256") or "").casefold() != report_sha
    ):
        raise RuntimeError("QC report/receipt does not match the explicit candidate hash")
    cards = report.get("cards")
    if not isinstance(cards, list) or int(universe.get("qualified") or -1) != len(cards):
        raise RuntimeError("QC report qualified cohort is invalid")
    rows: list[dict[str, Any]] = []
    for card in cards:
        if not isinstance(card, Mapping):
            raise RuntimeError("QC report card is invalid")
        variant_id = int(card.get("variantId") or 0)
        if variant_id <= 0:
            raise RuntimeError("QC report contains an invalid variant id")
        rows.append(
            {
                "variantId": variant_id,
                "marketRank": int(card["marketRank"]) if card.get("marketRank") is not None else None,
                "evidenceSha256": str(card.get("evidenceSha256") or ""),
                "decision": str(card.get("decision") or ""),
                "blockers": list(card.get("blockers") or []),
                "formalUniverseEligible": bool(card.get("facts", {}).get("identity", {}).get("formalUniverseEligible", False)),
            }
        )
    rows.sort(key=lambda row: row["variantId"])
    if len({row["variantId"] for row in rows}) != len(rows):
        raise RuntimeError("QC report contains duplicate variant ids")
    policy = {
        "mode": "qc_discovery_staging",
        "releaseEligible": False,
        "promotion": "forbidden",
        "selection": "canonical_db_qc.receipt_bound_discovery_cohort",
        "universeCandidateSha256": expected,
        "reportSha256": report_sha,
        "qcRunId": str(report.get("runId") or ""),
    }
    plan_input = {"policy": policy, "members": rows}
    return {
        "lockSha256": _sha256(canonical_json(plan_input)),
        "policy": policy,
        "members": rows,
        "reportSha256": report_sha,
        "candidateSha256": expected,
    }


def _database_rows(cursor: Any, lock_id: int) -> list[dict[str, Any]]:
    cursor.execute(
        """SELECT variant_id,segment_code,member_role,market_rank,selection_signals_json
           FROM market_universe_member WHERE universe_lock_id=%s ORDER BY variant_id""",
        (lock_id,),
    )
    return [
        {
            "variantId": int(row["variant_id"]),
            "marketRank": int(row["market_rank"]) if row.get("market_rank") is not None else None,
            "segmentCode": str(row["segment_code"]),
            "memberRole": str(row["member_role"]),
            "signals": json.loads(row["selection_signals_json"]),
        }
        for row in cursor.fetchall()
    ]


def _expected_rows(cursor: Any, plan: Mapping[str, Any]) -> list[dict[str, Any]]:
    members = list(plan["members"])
    ids = [int(row["variantId"]) for row in members]
    marks = ",".join(["%s"] * len(ids))
    cursor.execute(f"SELECT id FROM catalog_variant WHERE id IN ({marks})", ids)
    if {int(row["id"]) for row in cursor.fetchall()} != set(ids):
        raise RuntimeError("QC cohort no longer resolves to the canonical catalog")
    return [
        {
            "variantId": int(row["variantId"]),
            "marketRank": row["marketRank"],
            "signals": {
                "candidateSha256": plan["candidateSha256"],
                "reportSha256": plan["reportSha256"],
                "qcRunId": plan["policy"]["qcRunId"],
                "evidenceSha256": row["evidenceSha256"],
                "decision": row["decision"],
                "blockers": row["blockers"],
                "formalUniverseEligible": row["formalUniverseEligible"],
            },
        }
        for row in members
    ]


def materialize_staging_lock(connection: Any, plan: Mapping[str, Any], *, write: bool) -> dict[str, Any]:
    """Preview by default; write is a transactional, content-addressed insert only."""

    with connection.cursor() as cursor:
        _assert_canonical_database(cursor)
        expected = _expected_rows(cursor, plan)
        cursor.execute("SELECT id FROM market_universe_lock WHERE lock_sha256=%s", (plan["lockSha256"],))
        existing = cursor.fetchone()
    if not write:
        return {"status": "dry-run", "lockSha256": plan["lockSha256"], "members": len(expected), "wouldCreateLock": existing is None, "wouldCreateMembers": len(expected) if existing is None else 0, "isCurrent": False, "releaseEligible": False}

    with connection.cursor() as cursor:
        cursor.execute("SELECT GET_LOCK(%s,0) AS acquired", (ADVISORY_LOCK,))
        if int((cursor.fetchone() or {}).get("acquired") or 0) != 1:
            raise RuntimeError("another QC cohort lock materialization is already running")
    try:
        connection.begin()
        with connection.cursor() as cursor:
            cursor.execute("SELECT id,policy_json,member_count,is_current FROM market_universe_lock WHERE lock_sha256=%s FOR UPDATE", (plan["lockSha256"],))
            existing = cursor.fetchone()
            if existing is not None:
                if int(existing["member_count"]) != len(expected) or int(existing["is_current"] or 0) != 0 or json.loads(existing["policy_json"]) != plan["policy"]:
                    raise RuntimeError("existing QC staging lock hash is corrupt; refusing rewrite")
                lock_id = int(existing["id"])
                actual = _database_rows(cursor, lock_id)
                wanted = [{"variantId": row["variantId"], "marketRank": row["marketRank"], "segmentCode": "qc-discovery", "memberRole": "staging", "signals": row["signals"]} for row in expected]
                if actual != wanted:
                    raise RuntimeError("existing QC staging lock member rows are corrupt; refusing rewrite")
                created = False
            else:
                cursor.execute("INSERT INTO market_universe_lock (lock_sha256,effective_at,policy_json,member_count,is_current) VALUES (%s,UTC_TIMESTAMP(6),%s,%s,0)", (plan["lockSha256"], json.dumps(plan["policy"], sort_keys=True), len(expected)))
                lock_id = int(cursor.lastrowid)
                cursor.executemany("INSERT INTO market_universe_member (universe_lock_id,variant_id,segment_code,member_role,market_rank,watch_position,watch_score,selection_signals_json) VALUES (%s,%s,'qc-discovery','staging',%s,NULL,NULL,%s)", [(lock_id, row["variantId"], row["marketRank"], json.dumps(row["signals"], sort_keys=True)) for row in expected])
                created = True
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SELECT RELEASE_LOCK(%s)", (ADVISORY_LOCK,))
    return {"status": "materialized", "lockId": lock_id, "lockSha256": plan["lockSha256"], "members": len(expected), "created": created, "replayed": not created, "isCurrent": False, "releaseEligible": False}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    _add_connection_args(parser)
    parser.add_argument("--qc-report", type=Path, required=True)
    parser.add_argument("--expected-universe-candidate-sha256", required=True)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    plan = load_staging_plan(args.qc_report, args.expected_universe_candidate_sha256)
    load_db_env()
    values = _environment_values(args)
    connection = connect_from_values(values, read_only=not args.write)
    try:
        print(json.dumps(materialize_staging_lock(connection, plan, write=args.write), sort_keys=True))
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
