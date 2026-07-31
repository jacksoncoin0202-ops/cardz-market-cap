import hashlib
from pathlib import Path

from pipelines.image_review_proxy import build_dataset, public_dataset


def test_build_dataset_only_includes_prefilter_passes(tmp_path: Path) -> None:
    image = tmp_path / "card.webp"
    image.write_bytes(b"review-image")
    content_hash = hashlib.sha256(image.read_bytes()).hexdigest()
    canonical = {
        "runId": "canonical-1",
        "cards": [
            {
                "id": "cmc_one",
                "variantId": 1,
                "marketRank": 7,
                "facts": {
                    "price": {"valueUsd": "12.34"},
                    "population": {"value": 100},
                    "sales30d": {"purePsa10Count": 20},
                },
            }
        ],
    }
    prefilter = {
        "runId": "prefilter-1",
        "cards": [
            {
                "variantId": 1,
                "assetId": 11,
                "decision": "pass_sample_ocr",
                "sourcePolicy": {"family": "unknown"},
                "geometry": {"status": "passed", "widthPx": 429, "heightPx": 600},
            },
            {
                "variantId": 2,
                "assetId": 12,
                "decision": "reject_geometry",
            },
        ],
    }
    rows = {
        11: {
            "asset_id": 11,
            "variant_id": 1,
            "content_sha256": content_hash,
            "private_object_key": str(image),
            "tcg_code": "pokemon",
            "canonical_name": "Card One",
            "set_name": "Set",
            "collector_number": "001/100",
            "card_language": "en",
            "edition_code": "base",
            "parallel_code": "standard",
            "finish_code": "regular",
            "source_path": "unknown",
        }
    }
    document = build_dataset(canonical, prefilter, rows, assets_root=tmp_path)
    assert document["count"] == 1
    assert document["autoRejectedCount"] == 1
    assert document["autoRejected"][0]["decision"] == "reject_geometry"
    assert document["cards"][0]["assetId"] == 11
    assert document["cards"][0]["marketRank"] == 7
    assert "assetPaths" not in public_dataset(document)
    assert len(document["datasetSha256"]) == 64


def test_build_dataset_preserves_clean_exact_binding_without_human_review(
    tmp_path: Path,
) -> None:
    image = tmp_path / "card.webp"
    image.write_bytes(b"exact-image")
    content_hash = hashlib.sha256(image.read_bytes()).hexdigest()
    canonical = {
        "runId": "canonical-1",
        "cards": [
            {
                "id": "cmc_exact",
                "variantId": 1,
                "blockers": [],
                "facts": {
                    "image": {"semanticMatchStatus": "source_id_exact"},
                    "identity": {"printingSha256": "a" * 64},
                },
            }
        ],
    }
    prefilter = {
        "runId": "prefilter-1",
        "cards": [
            {
                "variantId": 1,
                "assetId": 11,
                "decision": "pass_sample_ocr",
                "expectedLanguage": "en",
                "sourceLanguage": "en",
            }
        ],
    }
    rows = {
        11: {
            "asset_id": 11,
            "content_sha256": content_hash,
            "private_object_key": str(image),
            "tcg_code": "pokemon",
            "canonical_name": "Exact",
            "collector_number": "001",
            "card_language": "en",
            "printing_card_language": "en",
            "printing_identity_status": "canonical",
            "canonical_printing_sha256": "a" * 64,
        }
    }
    document = build_dataset(canonical, prefilter, rows, assets_root=tmp_path)
    assert document["count"] == 0
    assert document["preservedBindingCount"] == 1
    assert document["preservedBindings"][0]["reason"] == (
        "source_id_exact_clean_prefilter"
    )


def test_build_dataset_hides_live_confirmed_binding_from_review(
    tmp_path: Path,
) -> None:
    image = tmp_path / "confirmed.webp"
    image.write_bytes(b"confirmed-image")
    content_hash = hashlib.sha256(image.read_bytes()).hexdigest()
    canonical = {
        "runId": "canonical-stale",
        "cards": [
            {
                "id": "cmc_confirmed",
                "variantId": 1,
                "blockers": [],
                "facts": {
                    "image": {"semanticMatchStatus": "pending_review"},
                    "identity": {"printingSha256": "a" * 64},
                },
            }
        ],
    }
    prefilter = {
        "runId": "prefilter-1",
        "cards": [
            {
                "variantId": 1,
                "assetId": 11,
                "decision": "pass_sample_ocr",
                "expectedLanguage": "en",
                "sourceLanguage": "en",
            }
        ],
    }
    rows = {
        11: {
            "asset_id": 11,
            "content_sha256": content_hash,
            "private_object_key": str(image),
            "tcg_code": "pokemon",
            "canonical_name": "Confirmed",
            "collector_number": "001",
            "card_language": "en",
            "printing_card_language": "en",
            "printing_identity_status": "canonical",
            "canonical_printing_sha256": "a" * 64,
            "qc_semantic_match_status": "human_or_vision_confirmed",
            "qc_public_allowed": 1,
            "pointer_public_allowed": 1,
            "qc_version": "human-review-v2",
        }
    }
    document = build_dataset(canonical, prefilter, rows, assets_root=tmp_path)
    assert document["count"] == 0
    assert document["preservedBindingCount"] == 1
    assert document["preservedBindings"][0]["reason"] == (
        "live_human_or_vision_confirmed"
    )


def test_build_dataset_keeps_v1_live_confirmation_in_review_queue(
    tmp_path: Path,
) -> None:
    image = tmp_path / "legacy-confirmed.webp"
    image.write_bytes(b"legacy-confirmed-image")
    content_hash = hashlib.sha256(image.read_bytes()).hexdigest()
    canonical = {
        "runId": "canonical-1",
        "cards": [{
            "id": "cmc_legacy",
            "variantId": 1,
            "blockers": [],
            "facts": {"identity": {"printingSha256": "a" * 64}},
        }],
    }
    prefilter = {"runId": "prefilter-1", "cards": [{
        "variantId": 1,
        "assetId": 11,
        "decision": "pass_sample_ocr",
        "expectedLanguage": "en",
        "sourceLanguage": "en",
    }]}
    rows = {11: {
        "asset_id": 11,
        "content_sha256": content_hash,
        "private_object_key": str(image),
        "card_language": "en",
        "printing_identity_status": "canonical",
        "canonical_printing_sha256": "a" * 64,
        "qc_semantic_match_status": "human_or_vision_confirmed",
        "qc_public_allowed": 1,
        "pointer_public_allowed": 1,
        "qc_version": "human-review-v1",
    }}
    document = build_dataset(canonical, prefilter, rows, assets_root=tmp_path)
    assert document["count"] == 1
    assert document["preservedBindingCount"] == 0
