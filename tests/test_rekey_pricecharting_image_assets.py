from __future__ import annotations

import hashlib
from pathlib import Path

from pipelines import rekey_pricecharting_image_assets as subject


def test_report_targets_selects_only_qc_missing_assets() -> None:
    report = {"cards": [
        {"blockers": ["image_asset_missing"], "imageSha256": "a" * 64, "facts": {"image": {"assetId": 7}}},
        {"blockers": [], "imageSha256": "b" * 64, "facts": {"image": {"assetId": 8}}},
    ]}
    assert subject.report_targets(report) == {7: "a" * 64}


def test_build_plan_requires_unreadable_old_path_and_exact_local_bytes(tmp_path: Path, monkeypatch) -> None:
    approved = tmp_path / "approved"
    approved.mkdir()
    image = approved / "7_123_1600.jpg"
    image.write_bytes(b"exact-image")
    digest = hashlib.sha256(b"exact-image").hexdigest()
    monkeypatch.setattr(subject, "APPROVED_ROOT", approved)
    monkeypatch.setattr(subject, "ROOT", tmp_path)

    plan = subject.build_plan(
        [{"id": 7, "variant_id": 70, "image_kind": "raw_front", "content_sha256": digest, "private_object_key": "gone.webp"}],
        {7: digest},
        {digest: image},
    )

    assert not plan["blocked"]
    assert plan["rekeys"][0]["newPrivateObjectKey"] == "data/private/pricecharting_images/7_123_1600.jpg"


def test_build_plan_fails_closed_when_old_key_still_reads(tmp_path: Path, monkeypatch) -> None:
    approved = tmp_path / "approved"
    approved.mkdir()
    image = approved / "7_123_1600.jpg"
    image.write_bytes(b"exact-image")
    digest = hashlib.sha256(b"exact-image").hexdigest()
    old = tmp_path / "already-readable.webp"
    old.write_bytes(b"stale")
    monkeypatch.setattr(subject, "APPROVED_ROOT", approved)
    monkeypatch.setattr(subject, "ROOT", tmp_path)

    plan = subject.build_plan(
        [{"id": 7, "variant_id": 70, "image_kind": "raw_front", "content_sha256": digest, "private_object_key": str(old)}],
        {7: digest},
        {digest: image},
    )

    assert plan["blocked"] == [{"assetId": 7, "reason": "old_path_still_readable"}]


def test_build_plan_recognises_exact_replay(tmp_path: Path, monkeypatch) -> None:
    approved = tmp_path / "approved"
    approved.mkdir()
    image = approved / "7_123_1600.jpg"
    image.write_bytes(b"exact-image")
    digest = hashlib.sha256(b"exact-image").hexdigest()
    monkeypatch.setattr(subject, "APPROVED_ROOT", approved)
    monkeypatch.setattr(subject, "ROOT", tmp_path)

    plan = subject.build_plan(
        [{"id": 7, "variant_id": 70, "image_kind": "raw_front", "content_sha256": digest, "private_object_key": "data/private/pricecharting_images/7_123_1600.jpg"}],
        {7: digest},
        {digest: image},
    )

    assert not plan["blocked"]
    assert not plan["rekeys"]
    assert plan["replayed"] == [{"assetId": 7, "variantId": 70}]
