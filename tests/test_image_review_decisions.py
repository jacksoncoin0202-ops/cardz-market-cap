import base64
import hashlib
import json

import pytest
from PIL import Image

from pipelines.card_identity import printing_key7, printing_key7_sha256
from pipelines.image_review_decisions import (
    PREFIX,
    assert_live_printing_identity,
    assert_ok_allowed,
    assert_public_derivatives_ready,
    dataset_hash,
    decision_plan,
    historical_human_rejections,
    sync_historical_rejection_registry,
    write_decision,
)


def encode(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return PREFIX + base64.urlsafe_b64encode(raw).decode().rstrip("=")


def dataset() -> dict:
    value = {
        "schemaVersion": 1,
        "canonicalRunId": "canonical",
        "prefilterRunId": "prefilter",
        "count": 1,
        "cards": [
            {
                "variantId": 1,
                "assetId": 11,
                "contentSha256": "a" * 64,
                "cardId": "cmc_one",
                "name": "Card One",
                "canonicalPrintingSha256": "b" * 64,
                "expectedLanguage": "ja",
                "sourceLanguage": "ja",
            }
        ],
        "assetPaths": {"token": "/private/card.webp"},
    }
    value["datasetSha256"] = dataset_hash(value)
    return value


def test_decision_code_binds_dataset_variant_asset_and_hash() -> None:
    value = dataset()
    code = encode({"v": 2, "d": value["datasetSha256"], "i": [[1, 11, "o", "b" * 64, "ja", "ja"]]})
    plan = decision_plan(code, value)
    assert plan == [
        {
            "variantId": 1,
            "assetId": 11,
            "contentSha256": "a" * 64,
            "decision": "ok",
            "cardId": "cmc_one",
            "name": "Card One",
            "reason": None,
            "samplePosition": None,
            "note": None,
            "expectedCanonicalPrintingSha256": "b" * 64,
            "expectedLanguage": "ja",
            "sourceLanguageEvidence": "ja",
        }
    ]


def test_decision_code_rejects_other_dataset() -> None:
    value = dataset()
    code = encode({"v": 2, "d": "b" * 64, "i": [[1, 11, "o", "b" * 64, "ja", "ja"]]})
    with pytest.raises(ValueError, match="dataset_mismatch"):
        decision_plan(code, value)


def test_explicit_human_ok_supplies_language_when_source_metadata_is_missing() -> None:
    value = dataset()
    value["cards"][0]["sourceLanguage"] = None
    value["datasetSha256"] = dataset_hash(value)
    code = encode(
        {
            "v": 2,
            "d": value["datasetSha256"],
            "i": [[1, 11, "o", "b" * 64, "ja", "ja"]],
        }
    )
    assert decision_plan(code, value)[0]["sourceLanguageEvidence"] == "ja"


def test_explicit_human_ok_cannot_override_conflicting_source_metadata() -> None:
    value = dataset()
    value["cards"][0]["sourceLanguage"] = "en"
    value["datasetSha256"] = dataset_hash(value)
    code = encode(
        {
            "v": 2,
            "d": value["datasetSha256"],
            "i": [[1, 11, "o", "b" * 64, "ja", "ja"]],
        }
    )
    with pytest.raises(ValueError, match="immutable_evidence_dataset_mismatch"):
        decision_plan(code, value)


def test_decision_code_rejects_unknown_card() -> None:
    value = dataset()
    code = encode({"v": 2, "d": value["datasetSha256"], "i": [[2, 22, "r"]]})
    with pytest.raises(ValueError, match="not_in_dataset"):
        decision_plan(code, value)


def test_reject_decision_preserves_reason_position_and_note() -> None:
    value = dataset()
    code = encode(
        {
            "v": 2,
            "d": value["datasetSha256"],
            "i": [[1, 11, "r", "sample", "center", "白色大字"]],
        }
    )
    plan = decision_plan(code, value)
    assert plan[0]["decision"] == "reject"
    assert plan[0]["reason"] == "sample"
    assert plan[0]["samplePosition"] == "center"
    assert plan[0]["note"] == "白色大字"


def test_reject_decision_requires_known_reason() -> None:
    value = dataset()
    code = encode(
        {"v": 2, "d": value["datasetSha256"], "i": [[1, 11, "r"]]}
    )
    with pytest.raises(ValueError, match="reject_reason"):
        decision_plan(code, value)


def test_ok_is_blocked_for_known_limitless_source_family() -> None:
    value = dataset()
    value["cards"][0]["sourceFamily"] = "limitless-one-piece-en"
    value["datasetSha256"] = dataset_hash(value)
    code = encode({"v": 2, "d": value["datasetSha256"], "i": [[1, 11, "o", "b" * 64, "ja", "ja"]]})
    with pytest.raises(ValueError, match="source_family_historically_rejected"):
        decision_plan(code, value)


def test_same_variant_and_content_rejection_cannot_be_promoted_but_new_hash_can_review() -> None:
    old_hash = "a" * 64
    rejected = historical_human_rejections([
        {
            "source": "human_image_review",
            "reasonCode": "human_review_rejected",
            "itemKey": f"variant:1:asset:11:{old_hash}",
        }
    ])
    assert rejected == {(1, old_hash)}
    with pytest.raises(RuntimeError, match="content_previously_human_rejected"):
        assert_ok_allowed(
            {"decision": "ok", "variantId": 1, "assetId": 12, "contentSha256": old_hash},
            source_policy={"family": "snkrdunk-card-front", "status": "scan_required"},
            historical_rejections=rejected,
        )
    assert_ok_allowed(
        {"decision": "ok", "variantId": 1, "assetId": 13, "contentSha256": "b" * 64},
        source_policy={"family": "snkrdunk-card-front", "status": "scan_required"},
        historical_rejections=rejected,
    )


def test_visually_rejected_content_policy_cannot_be_approved() -> None:
    rejected_hash = (
        "4a53529faf845d82b7c06ab04e9fa161792b2d2d9c1e9bee01b594cfef3895a6"
    )
    with pytest.raises(RuntimeError, match="content_policy_rejected"):
        assert_ok_allowed(
            {
                "decision": "ok",
                "variantId": 1741,
                "assetId": 1747,
                "contentSha256": rejected_hash,
            },
            source_policy={
                "family": "snkrdunk-card-front",
                "status": "scan_required",
            },
            historical_rejections=set(),
        )


def test_public_approval_requires_both_responsive_derivatives(tmp_path) -> None:
    sha = "a" * 64
    with pytest.raises(RuntimeError, match="public_derivative_missing:200"):
        assert_public_derivatives_ready(sha, tmp_path)
    Image.new("RGBA", (200, 280), (0, 0, 0, 0)).save(tmp_path / f"{sha}_200.webp", "WEBP")
    with pytest.raises(RuntimeError, match="public_derivative_missing:600"):
        assert_public_derivatives_ready(sha, tmp_path)
    Image.new("RGBA", (429, 600), (0, 0, 0, 0)).save(tmp_path / f"{sha}_600.webp", "WEBP")
    assert_public_derivatives_ready(sha, tmp_path)


def test_ok_requires_immutable_printing_and_language_evidence() -> None:
    value = dataset()
    code = encode({"v": 2, "d": value["datasetSha256"], "i": [[1, 11, "o"]]})
    with pytest.raises(ValueError, match="printing_language_evidence_required"):
        decision_plan(code, value)


def test_existing_qc_row_is_upgraded_to_current_review_version() -> None:
    class Cursor:
        def __init__(self) -> None:
            self.calls: list[tuple[str, object]] = []

        def execute(self, query: str, params: object = None) -> None:
            self.calls.append((query, params))

    cursor = Cursor()
    write_decision(
        cursor,
        {
            "variantId": 1,
            "assetId": 11,
            "decision": "reject",
            "reason": "language_mismatch",
            "samplePosition": None,
        },
        {
            "content_sha256": "c" * 64,
            "source_version_sha256": "a" * 64,
        },
        run_id="test-reject",
        decision_code_sha256="d" * 64,
    )
    upsert = next(
        query for query, _params in cursor.calls if "INSERT INTO market_image_qc" in query
    )
    assert "qc_version=VALUES(qc_version)" in upsert
    assert any(
        "UPDATE market_image_qc" in query
        and "SET public_allowed=0" in query
        and params == (11,)
        for query, params in cursor.calls
    )
    assert any(
        "INSERT INTO market_image_rejection_registry" in query
        for query, _params in cursor.calls
    )


def test_ok_upserts_exact_source_pointer_when_missing() -> None:
    class Cursor:
        def __init__(self) -> None:
            self.calls: list[tuple[str, object]] = []

        def execute(self, query: str, params: object = None) -> None:
            self.calls.append((query, params))

    cursor = Cursor()
    source_path = "data/runtime/private/image-store/asset.webp"
    source_version_sha256 = "b" * 64
    write_decision(
        cursor,
        {
            "variantId": 1,
            "assetId": 11,
            "decision": "ok",
            "expectedCanonicalPrintingSha256": "e" * 64,
            "expectedLanguage": "en",
        },
        {
            "content_sha256": "c" * 64,
            "source_version_sha256": source_version_sha256,
            "source_path": None,
            "private_object_key": source_path,
        },
        run_id="test-ok",
        decision_code_sha256="d" * 64,
    )
    pointer_upsert = next(
        (query, params)
        for query, params in cursor.calls
        if "INSERT INTO market_image_source_pointer" in query
    )
    assert "ON DUPLICATE KEY UPDATE" in pointer_upsert[0]
    assert "public_allowed=1" in pointer_upsert[0]
    assert pointer_upsert[1] == (
        1,
        hashlib.sha256(source_path.encode("utf-8")).hexdigest(),
        source_path,
        source_version_sha256,
    )
    approval_insert = next(
        (query, params)
        for query, params in cursor.calls
        if "INSERT INTO market_image_review_approval" in query
    )
    assert "binding_sha256=IF" in approval_insert[0]
    assert approval_insert[1][0:6] == (
        11,
        1,
        "c" * 64,
        source_version_sha256,
        "e" * 64,
        "en",
    )


def test_historical_rejections_are_synced_to_permanent_registry() -> None:
    class Cursor:
        def __init__(self) -> None:
            self.calls: list[tuple[str, object]] = []

        def execute(self, query: str, params: object = None) -> None:
            self.calls.append((query, params))

    cursor = Cursor()
    sync_historical_rejection_registry(
        cursor,
        {(9, "a" * 64), (9, "a" * 64)},
        run_id="ledger-sync-test",
    )
    assert len(cursor.calls) == 1
    query, params = cursor.calls[0]
    assert "INSERT INTO market_image_rejection_registry" in query
    assert params[0:2] == (9, "a" * 64)


def test_live_printing_identity_recomputes_all_seven_segments() -> None:
    key = printing_key7(
        "one-piece",
        "ja",
        "OP-05",
        "OP05-119",
        "first-edition",
        "manga",
        "foil",
    )
    row = {
        "tcg_code": "one-piece",
        "card_language": "ja",
        "variant_set_name": "OP-05",
        "variant_collector_number": "OP05-119",
        "printing_identity_status": "canonical",
        "printing_tcg_code": "one-piece",
        "printing_card_language": "ja",
        "printing_set_name": "OP-05",
        "printing_collector_number": "OP05-119",
        "printing_edition_code": "first-edition",
        "printing_parallel_code": "manga",
        "printing_finish_code": "foil",
        "canonical_printing_sha256": printing_key7_sha256(key),
    }
    assert_live_printing_identity(
        row,
        expected_printing_sha256=printing_key7_sha256(key),
        expected_language="ja",
    )

    corrupt = {**row, "printing_finish_code": "non-foil"}
    with pytest.raises(RuntimeError, match="printing_hash_invalid"):
        assert_live_printing_identity(
            corrupt,
            expected_printing_sha256=printing_key7_sha256(key),
            expected_language="ja",
        )

    missing_language = {**row, "printing_card_language": ""}
    with pytest.raises(RuntimeError, match="printing_incomplete"):
        assert_live_printing_identity(
            missing_language,
            expected_printing_sha256=printing_key7_sha256(key),
            expected_language="ja",
        )
