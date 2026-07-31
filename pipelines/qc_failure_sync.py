#!/usr/bin/env python3
"""Project canonical DB QC blockers into the existing private failure ledger.

The input is an immutable ``canonical_db_qc`` report.  Dry-run is the default;
``--write`` is required before append-only failure or resolution events are
recorded.  This module has no database or public-output dependency.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

try:
    from .failure_ledger import (
        DEFAULT_LEDGER_ROOT,
        current_failures,
        record_failure,
        record_resolution,
    )
except ImportError:  # pragma: no cover - direct script execution
    from failure_ledger import (
        DEFAULT_LEDGER_ROOT,
        current_failures,
        record_failure,
        record_resolution,
    )


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = "pipelines/qc_failure_sync.py"
SOURCE = "canonical_db_qc"
LANES = ("canonical_printing", "psa10_price", "image_approval", "sales", "general")
NEXT_ACTION_BY_LANE = {
    "canonical_printing": "resolve_canonical_printing_identity",
    "psa10_price": "fetch_exact_psa10_price_evidence",
    "image_approval": "replace_or_qc_exact_raw_front",
    "sales": "fetch_exact_pure_psa10_sales",
    "general": "agent_review",
}


def lane_for(blocker: str) -> str:
    if blocker.startswith("canonical_printing_"):
        return "canonical_printing"
    if blocker.startswith(("exact_psa10_price_", "price_anchor_", "price_source_")):
        return "psa10_price"
    if blocker.startswith("image_"):
        return "image_approval"
    if blocker.startswith(("psa10_sale", "psa10_sales", "sale_", "bundle_sale", "non_psa10_sale")):
        return "sales"
    return "general"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_report(path: Path) -> tuple[dict[str, Any], str]:
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid canonical DB QC report: {path}") from error
    if not isinstance(report, Mapping):
        raise ValueError("canonical DB QC report must be an object")
    database = report.get("database")
    counts = report.get("counts")
    if (
        report.get("readOnly") is not True
        or not isinstance(database, Mapping)
        or database.get("authority") != "canonical_mysql"
        or database.get("name") != "cardz_market_cap"
    ):
        raise ValueError("canonical DB QC report must be read-only cardz_market_cap")
    if not isinstance(report.get("runId"), str) or not report["runId"]:
        raise ValueError("canonical DB QC report runId is required")
    if not isinstance(report.get("cards"), list):
        raise ValueError("canonical DB QC report cards must be a list")
    if (
        not isinstance(counts, Mapping)
        or int(counts.get("catalog") or -1) != len(report["cards"])
    ):
        raise ValueError("canonical DB QC report catalog count does not reconcile")
    return dict(report), sha256_file(path)


def planned_items(report: Mapping[str, Any], *, report_sha256: str, report_path: Path) -> list[dict[str, Any]]:
    run_id = str(report["runId"])
    items: list[dict[str, Any]] = []
    for card in report["cards"]:
        if not isinstance(card, Mapping):
            raise ValueError("canonical DB QC report contains an invalid card")
        card_id = card.get("id")
        blockers = card.get("blockers")
        if not isinstance(card_id, str) or not card_id or not isinstance(blockers, list):
            raise ValueError("canonical DB QC card requires id and blockers")
        facts = card.get("facts")
        identity = facts.get("identity") if isinstance(facts, Mapping) else None
        identity_context = {
            key: identity.get(key)
            for key in (
                "cardLanguage",
                "set",
                "collectorNumber",
                "edition",
                "parallel",
                "finish",
                "printingSha256",
            )
            if isinstance(identity, Mapping) and identity.get(key) is not None
        }
        by_lane: dict[str, list[str]] = {}
        for blocker in blockers:
            if not isinstance(blocker, str) or not blocker:
                raise ValueError("canonical DB QC blocker must be a string")
            by_lane.setdefault(lane_for(blocker), []).append(blocker)
        for lane, lane_blockers in sorted(by_lane.items()):
            context = {
                "qcRunId": run_id,
                "qcReportSha256": report_sha256,
                "qcReportPath": str(report_path),
                "lane": lane,
                "cardId": card_id,
                "variantId": card.get("variantId"),
                "tcg": card.get("tcg"),
                **identity_context,
                "marketRank": card.get("marketRank"),
                "segment": card.get("segment"),
                "blockers": sorted(set(lane_blockers)),
                "evidenceSha256": card.get("evidenceSha256"),
            }
            items.append(
                {
                    "source": SOURCE,
                    "stage": lane,
                    "script": SCRIPT,
                    "itemKey": card_id,
                    "reasonCode": f"qc_{lane}_blocked",
                    "message": f"canonical DB QC {run_id}: {', '.join(context['blockers'])}",
                    "retryable": True,
                    "runId": run_id,
                    "context": context,
                    "evidencePaths": (str(report_path),),
                    "nextAction": NEXT_ACTION_BY_LANE[lane],
                }
            )
    return sorted(items, key=lambda item: (item["stage"], item["itemKey"]))


def sync_report(
    report: Mapping[str, Any],
    *,
    report_sha256: str,
    report_path: Path,
    ledger_root: Path = DEFAULT_LEDGER_ROOT,
    write: bool = False,
) -> dict[str, Any]:
    items = planned_items(report, report_sha256=report_sha256, report_path=report_path)
    desired = {(item["stage"], item["itemKey"]) for item in items}
    report_cards = {
        str(card["id"])
        for card in report["cards"]
        if isinstance(card, Mapping) and isinstance(card.get("id"), str)
    }
    report_card_by_variant = {
        int(card["variantId"]): str(card["id"])
        for card in report["cards"]
        if (
            isinstance(card, Mapping)
            and isinstance(card.get("id"), str)
            and isinstance(card.get("variantId"), int)
            and not isinstance(card.get("variantId"), bool)
            and int(card["variantId"]) > 0
        )
    }
    open_qc = current_failures(
        ledger_root=ledger_root,
        source=SOURCE,
        script=SCRIPT,
    )
    open_by_lane_card = {
        (str(row.get("stage")), str(row.get("itemKey"))): row
        for row in open_qc
    }
    resolution_plans: list[tuple[Mapping[str, Any], str, str | None]] = []
    for row in open_qc:
        stage = str(row.get("stage"))
        item_key = str(row.get("itemKey"))
        context = row.get("context")
        raw_variant_id = context.get("variantId") if isinstance(context, Mapping) else None
        variant_id = (
            int(raw_variant_id)
            if isinstance(raw_variant_id, int)
            and not isinstance(raw_variant_id, bool)
            and raw_variant_id > 0
            else None
        )
        current_card_id = report_card_by_variant.get(variant_id) if variant_id is not None else None
        if current_card_id is not None:
            if item_key != current_card_id:
                resolution_plans.append((row, "qc_item_key_rekeyed", current_card_id))
            elif (stage, current_card_id) not in desired:
                resolution_plans.append((row, "qc_blocker_absent_in_report", current_card_id))
        elif item_key in report_cards and (stage, item_key) not in desired:
            # Legacy events may predate variantId context.
            resolution_plans.append((row, "qc_blocker_absent_in_report", item_key))
    written_failures = 0
    written_resolutions = 0
    if write:
        for item in items:
            existing = open_by_lane_card.get((item["stage"], item["itemKey"]))
            existing_context = existing.get("context") if isinstance(existing, Mapping) else None
            if (
                isinstance(existing_context, Mapping)
                and existing.get("reasonCode") == item["reasonCode"]
                and existing_context.get("blockers") == item["context"]["blockers"]
            ):
                continue
            if record_failure(
                source=str(item["source"]),
                stage=str(item["stage"]),
                script=str(item["script"]),
                item_key=str(item["itemKey"]),
                reason_code=str(item["reasonCode"]),
                message=str(item["message"]),
                retryable=bool(item["retryable"]),
                run_id=str(item["runId"]),
                context=item["context"],
                evidence_paths=item["evidencePaths"],
                next_action=str(item["nextAction"]),
                ledger_root=ledger_root,
            ) is not None:
                written_failures += 1
        for row, resolution, current_card_id in resolution_plans:
            if record_resolution(
                source=SOURCE,
                stage=str(row["stage"]),
                script=SCRIPT,
                item_key=str(row["itemKey"]),
                run_id=str(report["runId"]),
                resolution=resolution,
                context={
                    "qcRunId": report["runId"],
                    "qcReportSha256": report_sha256,
                    "qcReportPath": str(report_path),
                    "currentCardId": current_card_id,
                },
                evidence_paths=(report_path,),
                ledger_root=ledger_root,
            ) is not None:
                written_resolutions += 1
    by_lane = {lane: sum(1 for item in items if item["stage"] == lane) for lane in LANES}
    return {
        "action": "qc-failure-sync",
        "dryRun": not write,
        "qcRunId": report["runId"],
        "qcReportSha256": report_sha256,
        "openItems": len(items),
        "openByLane": by_lane,
        "resolutions": len(resolution_plans),
        "writtenFailures": written_failures,
        "writtenResolutions": written_resolutions,
        "items": items,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--ledger-root", type=Path, default=DEFAULT_LEDGER_ROOT)
    parser.add_argument("--write", action="store_true", help="append ledger events; default is dry-run")
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="omit per-card items from stdout after the sync",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report_path = args.report.resolve()
    report, report_sha256 = load_report(report_path)
    result = sync_report(
        report,
        report_sha256=report_sha256,
        report_path=report_path,
        ledger_root=args.ledger_root.resolve(),
        write=args.write,
    )
    if args.summary_only:
        result = {key: value for key, value in result.items() if key != "items"}
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
