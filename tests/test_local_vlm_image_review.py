from __future__ import annotations

from unittest.mock import Mock

import pytest

from pipelines.local_vlm_image_review import (
    exclusive_output,
    make_receipt,
    prioritize_rows,
    review_rows,
    validate_observation,
)


ROW = {"asset_id": 7, "variant_id": 70, "content_sha256": "a" * 64, "source_version_sha256": "b" * 64, "card_name": "Kadabra", "collector_number": "064/165", "card_language": "en", "finish_code": "foil", "edition_code": "base", "parallel_code": "normal"}


def test_validator_accepts_exact_number_and_unknown_visual_fields() -> None:
    decision, reasons = validate_observation({"collectorNumber": "64/165", "language": "en", "finish": "unknown", "edition": "unknown", "parallel": "unknown"}, ROW)
    assert decision == "vision_confirm_candidate"
    assert reasons == []


def test_validator_rejects_conflicting_number() -> None:
    decision, reasons = validate_observation({"collectorNumber": "65/165", "language": "en"}, ROW)
    assert decision == "reject"
    assert reasons == ["collector_number_not_confirmed"]


def test_receipt_is_content_addressed_and_does_not_promote() -> None:
    raw = '{"collectorNumber":"064/165","language":"en","finish":"unknown","edition":"unknown","parallel":"unknown"}'
    receipt = make_receipt(ROW, "c" * 64, {"full": "prompt", "collectorCrop": "crop"}, {"full": raw, "collectorCrop": '{"collectorNumber":"unknown","language":"unknown"}'})
    assert receipt["decision"] == "vision_confirm_candidate"
    assert len(receipt["receiptSha256"]) == 64
    assert receipt["rawResponseSha256"] != receipt["receiptSha256"]


def test_one_worker_failure_does_not_discard_other_receipts() -> None:
    rows = [
        ROW,
        {**ROW, "asset_id": 8, "variant_id": 71, "content_sha256": "d" * 64},
    ]

    def job(row, model_digest):
        if row["asset_id"] == 7:
            raise TimeoutError("ollama timed out")
        return make_receipt(
            row,
            model_digest,
            {"full": "prompt", "collectorCrop": "crop"},
            {
                "full": '{"collectorNumber":"064/165","language":"en"}',
                "collectorCrop": '{"collectorNumber":"unknown","language":"unknown"}',
            },
        )

    failure = Mock()
    resolution = Mock()
    receipts, failures = review_rows(
        rows,
        "c" * 64,
        workers=2,
        job=job,
        record_failure_fn=failure,
        record_resolution_fn=resolution,
    )

    assert [receipt["assetId"] for receipt in receipts] == [8]
    assert failures == [{
        "itemKey": f"variant:70:asset:7:{'a' * 64}",
        "errorType": "TimeoutError",
    }]
    assert failure.call_count == 1
    assert failure.call_args.kwargs["retryable"] is True
    assert failure.call_args.kwargs["stage"] == "image_review"
    assert resolution.call_count == 1


def test_receipt_output_has_one_process_owner(tmp_path) -> None:
    output = tmp_path / "tier-a.jsonl"
    with exclusive_output(output):
        with pytest.raises(RuntimeError, match="local_vlm_output_locked"):
            with exclusive_output(output):
                pass
    assert not output.with_name("tier-a.jsonl.lock").exists()


def test_one_piece_rows_are_prioritized_without_losing_stable_order() -> None:
    rows = [
        {**ROW, "asset_id": 9, "variant_id": 90, "tcg_code": "pokemon"},
        {**ROW, "asset_id": 8, "variant_id": 80, "tcg_code": "one-piece"},
        {**ROW, "asset_id": 7, "variant_id": 70, "tcg_code": "one-piece"},
    ]
    ordered = prioritize_rows(rows, "one-piece")
    assert [(row["variant_id"], row["asset_id"]) for row in ordered] == [
        (70, 7),
        (80, 8),
        (90, 9),
    ]
