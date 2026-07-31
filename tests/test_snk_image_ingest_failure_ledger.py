from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from pipelines import snk_image_ingest as ingest


def test_needs_review_enters_agent_retry_ledger() -> None:
    result = {
        "snkId": 849532,
        "variantId": 1751,
        "status": "needs_review",
        "reason": "needs_review:species_ok_num_weak",
        "name": "Charizard",
        "collector": "25",
    }
    with patch.object(ingest, "record_failure") as failure:
        ingest.record_operational_result(
            result,
            report_path=Path("report.jsonl"),
            run_id="run-1",
        )
    kwargs = failure.call_args.kwargs
    assert kwargs["item_key"] == "snk:849532"
    assert kwargs["next_action"] == "agent_review"
    assert kwargs["retryable"] is True


def test_written_image_resolves_prior_retry_item() -> None:
    result = {
        "snkId": 849532,
        "variantId": 1751,
        "status": "written_pending",
    }
    with patch.object(ingest, "record_resolution") as resolution:
        ingest.record_operational_result(
            result,
            report_path=Path("report.jsonl"),
            run_id="run-2",
        )
    assert resolution.call_args.kwargs["item_key"] == "snk:849532"
    assert (
        resolution.call_args.kwargs["resolution"]
        == "image_landed_or_already_confirmed"
    )
