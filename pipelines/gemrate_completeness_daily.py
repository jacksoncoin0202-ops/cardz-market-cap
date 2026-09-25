#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Refresh the dynamic GemRate-qualified universe and rebuild the GROK gap queue.

The upstream identity-census stage refreshes every public GemRate set.  This
stage then refreshes exact card pages only for verified cards that the set
snapshot does not cover, merges both authority lanes at PSA-row identity, and
reconciles the resulting PSA10>=1000 universe against one read-only DB snapshot.

No count is hard-coded.  A dated inventory is always retained; the stable
``latest`` files advance only when a row-bearing known-scope inventory exists.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
PIPELINES = ROOT / "pipelines"
RUNTIME = ROOT / "data" / "runtime" / "daily-chain-v2"
GEMRATE_RUNS = ROOT / "data" / "private" / "gemrate" / "runs"
BRUTE_ROOT = ROOT / "data" / "private" / "gemrate_brute"
MINIMUM_RUN_BUDGET_SECONDS = 1_200.0
MAXIMUM_WEBSITE_BUDGET_SECONDS = 1_800
PROCESS_RESERVE_SECONDS = 240


def iso_utc(value: datetime | None = None) -> str:
    moment = value or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return document


def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.next")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def atomic_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.next")
    temporary.write_bytes(source.read_bytes())
    os.replace(temporary, target)


def run_command(command: Sequence[str], *, timeout: int) -> dict[str, Any]:
    completed = subprocess.run(
        list(command),
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    output = ((completed.stdout or "") + "\n" + (completed.stderr or "")).strip()
    return {
        "command": list(command),
        "exitCode": int(completed.returncode),
        "outputTail": "\n".join(output.splitlines()[-30:]),
    }


def newest_manifest(run_suffix: str, started_at: float) -> Path:
    candidates = [
        path
        for path in GEMRATE_RUNS.glob(f"daily_*_{run_suffix}/manifest.json")
        if path.stat().st_mtime >= started_at - 2
    ]
    if not candidates:
        raise RuntimeError(f"exact refresh produced no manifest for suffix {run_suffix}")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def psa_ids(document: Mapping[str, Any]) -> set[str]:
    return {
        str(row.get("gemrate", {}).get("psaId") or "").lower()
        for row in document.get("rows") or []
        if isinstance(row, Mapping)
        and isinstance(row.get("gemrate"), Mapping)
        and row.get("gemrate", {}).get("psaId")
    }


def qualified_census_jsonl(document: Mapping[str, Any]) -> str:
    """Project the merged authority rows into the intake census contract."""

    projected: list[dict[str, Any]] = []
    for row in document.get("rows") or []:
        if not isinstance(row, Mapping) or not isinstance(row.get("gemrate"), Mapping):
            continue
        gemrate = row["gemrate"]
        projected.append(
            {
                "psa_id": gemrate.get("psaId"),
                "psa_10": gemrate.get("psa10Population"),
                "name": gemrate.get("fullName"),
                "full_name": gemrate.get("fullName"),
                "raw_full_name": gemrate.get("rawFullName"),
                "name_source": gemrate.get("nameSource"),
                "name_provenance": gemrate.get("nameProvenance"),
                "category": gemrate.get("category"),
                "year": gemrate.get("year"),
                "set_name": gemrate.get("setName"),
                "card_number": gemrate.get("collectorNumber"),
                "parallel": gemrate.get("parallel"),
                "psa_details": gemrate.get("psaDetails"),
                "psa_url": gemrate.get("psaUrl"),
                "psa_date": gemrate.get("psaObservedDate"),
                "_set_id": gemrate.get("sourceSetId"),
                "_set_name": gemrate.get("setName"),
            }
        )
    projected.sort(key=lambda row: (-int(row.get("psa_10") or 0), str(row.get("psa_id") or "")))
    return "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in projected)


def render_fresh_only_inventory(
    census_path: Path,
    refresh_not_before: str,
    json_path: Path,
    markdown_path: Path,
) -> None:
    from gemrate_db_completeness import (
        atomic_write,
        build_known_scope_inventory_payload,
        json_value,
        load_database_snapshot,
        parse_instant,
        render_known_scope_inventory_markdown,
        validate_fresh_census,
    )

    rows, metadata, _all_ids = validate_fresh_census(
        census_path,
        parse_instant(refresh_not_before),
    )
    snapshot = load_database_snapshot(ROOT, [str(row["psa_id"]).lower() for row in rows])
    payload = build_known_scope_inventory_payload(rows, metadata, snapshot)
    atomic_write(json_path, json.dumps(json_value(payload), ensure_ascii=False, indent=2) + "\n")
    atomic_write(markdown_path, render_known_scope_inventory_markdown(payload))


def run_daily_completeness(
    *,
    business_date: str,
    budget_seconds: float | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    moment = now or datetime.now(timezone.utc)
    run_root = RUNTIME / "gemrate-completeness" / business_date
    run_root.mkdir(parents=True, exist_ok=True)
    receipt_path = RUNTIME / f"gemrate-completeness-{business_date}.receipt.json"
    receipt: dict[str, Any] = {
        "stage": "identity-completeness",
        "businessDate": business_date,
        "generatedAt": iso_utc(moment),
        "databaseMode": "read-only consistent snapshot",
        "populationPolicy": "PSA10 >= 1000, recomputed from current GemRate authority evidence",
        "hardCodedEligibleCount": False,
        "latestAdvanced": False,
    }
    if budget_seconds is not None and budget_seconds < MINIMUM_RUN_BUDGET_SECONDS:
        receipt.update(
            {
                "status": "SKIPPED_INSUFFICIENT_TICK_BUDGET",
                "budgetSeconds": round(float(budget_seconds), 1),
                "minimumBudgetSeconds": MINIMUM_RUN_BUDGET_SECONDS,
            }
        )
        atomic_text(receipt_path, json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
        return receipt

    all_cards = BRUTE_ROOT / "all_cards.jsonl"
    qualified = BRUTE_ROOT / "psa10_1000_plus.jsonl"
    for required in (all_cards, qualified):
        if not required.is_file():
            raise RuntimeError(f"missing daily GemRate census artifact: {required}")
    artifact_floor = min(all_cards.stat().st_mtime, qualified.stat().st_mtime)
    refresh_not_before = iso_utc(
        datetime.fromtimestamp(artifact_floor, timezone.utc) - timedelta(seconds=1)
    )

    ids_path = run_root / "exact-refresh-ids.txt"
    excluded_path = run_root / "registry-only-excluded.txt"
    metadata_path = run_root / "worklist-metadata.json"
    build_result = run_command(
        [
            sys.executable,
            "-X",
            "utf8",
            str(PIPELINES / "gemrate_refresh_worklist.py"),
            "--authority-repo",
            str(ROOT),
            "--ids-output",
            str(ids_path),
            "--excluded-registry-output",
            str(excluded_path),
            "--metadata-output",
            str(metadata_path),
        ],
        timeout=600,
    )
    receipt["worklistBuild"] = build_result
    if build_result["exitCode"] != 0:
        raise RuntimeError(f"GemRate worklist build failed: {build_result['outputTail']}")
    metadata = read_json(metadata_path)
    worklist_count = int(metadata.get("worklistCount") or 0)
    receipt["exactRefreshWorklistCount"] = worklist_count
    receipt["publicSnapshotQualifiedCount"] = int(
        (metadata.get("counts") or {}).get("freshBruteQualified") or 0
    )
    receipt["registryOnlyExcludedCount"] = int(
        metadata.get("unverifiedRegistryCandidatesExcludedCount") or 0
    )

    dated_json = run_root / "CARDZ_GEMRATE_DB_COMPLETENESS_TASKS.json"
    dated_markdown = run_root / "CARDZ_GEMRATE_DB_COMPLETENESS_HANDOFF.md"
    exact_result: dict[str, Any] | None = None
    manifest_path: Path | None = None
    if worklist_count:
        available = (
            MAXIMUM_WEBSITE_BUDGET_SECONDS
            if budget_seconds is None
            else max(1, int(budget_seconds - PROCESS_RESERVE_SECONDS))
        )
        website_budget = min(MAXIMUM_WEBSITE_BUDGET_SECONDS, available)
        run_suffix = f"completeness-{business_date.replace('-', '')}"
        started_at = time.time()
        exact_result = run_command(
            [
                sys.executable,
                "-X",
                "utf8",
                str(PIPELINES / "gemrate_source.py"),
                "daily",
                "--ids-file",
                str(ids_path),
                "--speed",
                "fast",
                "--website-budget-seconds",
                str(website_budget),
                "--workers",
                "4",
                "--run-suffix",
                run_suffix,
                "--skip-grader-volume",
            ],
            timeout=website_budget + 180,
        )
        receipt["exactRefresh"] = exact_result
        manifest_path = newest_manifest(run_suffix, started_at)
        receipt["exactManifestPath"] = str(manifest_path)
        completeness_command = [
            sys.executable,
            "-X",
            "utf8",
            str(PIPELINES / "gemrate_db_completeness.py"),
            "--repo",
            str(ROOT),
            "--census",
            str(qualified),
            "--manifest",
            str(manifest_path),
            "--worklist-metadata",
            str(metadata_path),
            "--refresh-not-before",
            refresh_not_before,
            "--json-out",
            str(dated_json),
            "--markdown-out",
            str(dated_markdown),
        ]
        inventory_result = run_command(completeness_command, timeout=900)
        receipt["inventoryBuild"] = inventory_result
        if inventory_result["exitCode"] not in (0,):
            raise RuntimeError(f"GemRate inventory build failed: {inventory_result['outputTail']}")
    else:
        render_fresh_only_inventory(qualified, refresh_not_before, dated_json, dated_markdown)
        receipt["exactRefresh"] = {"skipped": True, "reason": "public snapshot covers verified scope"}

    current = read_json(dated_json)
    rows = current.get("rows") or []
    if not isinstance(rows, list) or not rows:
        raise RuntimeError("daily GemRate completeness inventory has no rows")
    latest_json = RUNTIME / "gemrate-completeness-latest.json"
    latest_markdown = RUNTIME / "gemrate-completeness-latest.md"
    dated_census = run_root / "gemrate-qualified.jsonl"
    latest_census = RUNTIME / "gemrate-qualified-latest.jsonl"
    latest_census_meta = RUNTIME / "gemrate-qualified-latest.metadata.json"
    previous = read_json(latest_json) if latest_json.is_file() else {}
    current_ids = psa_ids(current)
    previous_ids = psa_ids(previous)
    census_text = qualified_census_jsonl(current)
    if census_text.count("\n") != len(current_ids):
        raise RuntimeError("qualified intake census row count does not match unique PSA IDs")
    atomic_text(dated_census, census_text)
    atomic_copy(dated_json, latest_json)
    atomic_copy(dated_markdown, latest_markdown)
    atomic_copy(dated_census, latest_census)
    atomic_text(
        latest_census_meta,
        json.dumps(current.get("census") or {}, ensure_ascii=False, indent=2) + "\n",
    )
    receipt.update(
        {
            "status": str(current.get("status") or current.get("census", {}).get("status") or "COMPLETE"),
            "eligibleKnownStrictPsaIds": len(current_ids),
            "enteredQualifiedCount": len(current_ids - previous_ids),
            "leftQualifiedCount": len(previous_ids - current_ids),
            "enteredQualifiedPsaIds": sorted(current_ids - previous_ids),
            "leftQualifiedPsaIds": sorted(previous_ids - current_ids),
            "unresolvedQualifiedPrintings": len(current.get("unresolvedQualifiedPrintings") or []),
            "sourceFailures": len(current.get("sourceFailures") or []),
            "grokQueueCount": len(current.get("grokQueue") or []),
            "datedJson": str(dated_json),
            "datedMarkdown": str(dated_markdown),
            "latestJson": str(latest_json),
            "latestMarkdown": str(latest_markdown),
            "qualifiedCensusJsonl": str(latest_census),
            "qualifiedCensusMetadata": str(latest_census_meta),
            "latestAdvanced": True,
        }
    )
    receipt["receiptPath"] = str(receipt_path)
    atomic_text(receipt_path, json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--business-date", required=True)
    parser.add_argument("--budget-seconds", type=float, default=None)
    args = parser.parse_args()
    result = run_daily_completeness(
        business_date=args.business_date,
        budget_seconds=args.budget_seconds,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
