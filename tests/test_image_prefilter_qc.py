import hashlib
from pathlib import Path
from unittest import mock

from PIL import Image

from pipelines.image_prefilter_qc import evaluate_card, infer_source_language


def card(path: Path, *, geometry_status: str = "passed") -> dict:
    image_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "id": "cmc_test",
        "variantId": 1,
        "tcg": "pokemon",
        "imageSha256": image_hash,
        "facts": {
            "image": {
                "assetId": 11,
                "geometry": {
                    "status": geometry_status,
                    "reasons": [] if geometry_status == "passed" else ["small_card"],
                },
            }
        },
    }


def asset(path: Path, *, source_path: str = "unknown-source") -> dict:
    return {
        "asset_id": 11,
        "variant_id": 1,
        "private_object_key": str(path),
        "source_path": source_path,
        "width_px": 429,
        "height_px": 600,
    }


def test_geometry_reject_skips_ocr(tmp_path: Path) -> None:
    path = tmp_path / "card.webp"
    Image.new("RGB", (429, 600), "white").save(path)
    with mock.patch(
        "pipelines.image_prefilter_qc.detect_sample_evidence"
    ) as scan:
        result = evaluate_card(
            card(path, geometry_status="failed"),
            asset(path),
            assets_root=tmp_path,
        )
    assert result["decision"] == "reject_geometry"
    scan.assert_not_called()


def test_limitless_one_piece_source_is_rejected_before_ocr(
    tmp_path: Path,
) -> None:
    path = tmp_path / "card.webp"
    Image.new("RGB", (429, 600), "white").save(path)
    source = (
        "https://limitlesstcg.nyc3.cdn.digitaloceanspaces.com/"
        "one-piece/EB02/EB02-010_EN.webp"
    )
    value = card(path)
    value["tcg"] = "one-piece"
    with mock.patch(
        "pipelines.image_prefilter_qc.detect_sample_evidence",
    ) as scan:
        result = evaluate_card(
            value,
            asset(path, source_path=source),
            assets_root=tmp_path,
        )
    assert result["decision"] == "reject_known_sample_source"
    scan.assert_not_called()


def test_unknown_source_uses_ocr(tmp_path: Path) -> None:
    path = tmp_path / "card.webp"
    Image.new("RGB", (429, 600), "white").save(path)
    with mock.patch(
        "pipelines.image_prefilter_qc.detect_sample_evidence",
        return_value=None,
    ) as scan:
        result = evaluate_card(card(path), asset(path), assets_root=tmp_path)
    assert result["decision"] == "pass_sample_ocr"
    scan.assert_called_once()


def test_known_rejected_content_skips_ocr(tmp_path: Path) -> None:
    path = tmp_path / "card.webp"
    Image.new("RGB", (429, 600), "white").save(path)
    with (
        mock.patch(
            "pipelines.image_prefilter_qc.classify_content_sha256",
            return_value={
                "status": "reject",
                "reason": "known_sample_content_visual_confirmed",
            },
        ),
        mock.patch("pipelines.image_prefilter_qc.detect_sample_evidence") as scan,
    ):
        result = evaluate_card(card(path), asset(path), assets_root=tmp_path)
    assert result["decision"] == "reject_known_sample_content"
    scan.assert_not_called()


def test_infer_source_language_uses_only_explicit_path_markers() -> None:
    assert infer_source_language("https://cdn/ST01/ST01-012_EN.webp") == "en"
    assert infer_source_language("images/card-ja.png") == "ja"
    assert infer_source_language("https://snkrdunk.com/en/trading-cards/1") is None


def test_explicit_source_language_mismatch_skips_ocr(tmp_path: Path) -> None:
    path = tmp_path / "card.webp"
    Image.new("RGB", (429, 600), "white").save(path)
    value = card(path)
    value["tcg"] = "one-piece"
    value["facts"]["identity"] = {"cardLanguage": "ja"}
    source = "https://cdn/ST01/ST01-012_EN.webp"
    with mock.patch(
        "pipelines.image_prefilter_qc.detect_sample_evidence"
    ) as scan:
        result = evaluate_card(
            value,
            asset(path, source_path=source),
            assets_root=tmp_path,
        )
    assert result["decision"] == "reject_language_mismatch"
    assert result["reason"] == "expected_ja:source_en"
    scan.assert_not_called()
