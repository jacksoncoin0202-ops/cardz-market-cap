# -*- coding: utf-8 -*-
"""每日卡圖自愈寫 QC record 嘅 regression tests。

規矩：`resolverEvidence.sourceContentSha256` 要係**正規化之前**嗰份原始 bytes
嘅 sha256，唔係轉換之後嗰張圖（嗰個係 `contentSha256`）。
`tests/data/privacy-and-images.test.mjs` 對每條 publicAllowed record 都會驗呢個欄位，
漏咗嘅話成條 publish 鏈見紅——而且係喺新卡入列嗰日先爆。
"""

from __future__ import annotations

import hashlib
import io
import json
import sys
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipelines"))

import ensure_std_card_images as mod  # noqa: E402
import native_image_resolver as nir  # noqa: E402


def make_source(width: int = 120, height: int = 168) -> bytes:
    """砌一張非 429x600 嘅方角卡：resize 同補圓角兩條支路都會行到。"""
    image = Image.new("RGBA", (width, height), (30, 90, 200, 255))
    buffer = io.BytesIO()
    image.save(buffer, "WEBP", quality=92)
    return buffer.getvalue()


@pytest.fixture()
def bench(tmp_path, monkeypatch):
    """將 assets / QC / snapshot 全部導去 tmp，唔准掂真 data/public。"""
    assets = tmp_path / "assets"
    assets.mkdir()
    qc = tmp_path / "image-qc.json"
    qc.write_text(json.dumps({"records": []}), encoding="utf-8")
    monkeypatch.setattr(nir, "ASSETS", assets)
    monkeypatch.setattr(mod, "QC", qc)
    monkeypatch.setattr(mod, "LATEST_POINTERS", ())
    return assets, qc, tmp_path


def seed_card(assets: Path, card_id: str = "cmc_test_0001") -> tuple[dict, str]:
    raw = make_source()
    sha = hashlib.sha256(raw).hexdigest()
    (assets / f"{sha}.webp").write_bytes(raw)
    with Image.open(io.BytesIO(raw)) as probe:
        width, height = probe.size
    card = {
        "id": card_id,
        "names": {"en": "Test Card"},
        "image": {
            "sha256": sha,
            "src": f"/market-assets/{sha}.webp",
            "kind": "raw_front",
            "width": width,
            "height": height,
        },
    }
    return card, sha


def run_main(tmp_path: Path, cards: list[dict], monkeypatch) -> list[dict]:
    snapshot_path = tmp_path / "snapshot.json"
    snapshot_path.write_text(
        json.dumps({"generation": {"contentSha256": ""}, "top100": cards, "watchlist": []}),
        encoding="utf-8",
    )
    monkeypatch.setattr(sys, "argv", ["ensure_std_card_images.py", str(snapshot_path), "--write"])
    assert mod.main() == 0
    return json.loads(mod.QC.read_text(encoding="utf-8"))["records"]


def test_origins_report_pre_normalisation_bytes(bench):
    """`ensure_cards` 要交返原始 bytes 嘅 sha，而唔係轉換後嗰個。"""
    assets, _, _ = bench
    card, source_sha = seed_card(assets)
    stats, origins = mod.ensure_cards([card], write=True)

    assert origins[card["id"]] == source_sha
    assert stats["missingAsset"] == 0
    # 真係換咗圖先算行過條轉換路
    assert card["image"]["sha256"] != source_sha


def test_qc_record_carries_source_content_sha256(bench, monkeypatch):
    """寫出嚟嘅 record 要有合規 sourceContentSha256，同 contentSha256 唔同。"""
    assets, _, tmp_path = bench
    card, source_sha = seed_card(assets)

    records = run_main(tmp_path, [card], monkeypatch)

    assert len(records) == 1
    evidence = records[0]["resolverEvidence"]
    assert evidence["sourceContentSha256"] == source_sha
    assert len(evidence["sourceContentSha256"]) == 64
    assert evidence["sourceContentSha256"] != records[0]["contentSha256"]
    assert records[0]["publicAllowed"] is False


def test_missing_asset_writes_no_public_record(bench, monkeypatch):
    """冇實體 asset 就冇 evidence 可言，寧願唔出 record 都唔可以出條殘缺嘅。"""
    _, _, tmp_path = bench
    card = {
        "id": "cmc_test_gone",
        "names": {"en": "Gone"},
        "image": {"sha256": "0" * 64, "src": "/market-assets/x.webp",
                  "kind": "raw_front", "width": 429, "height": 600},
    }
    ghost, _ = seed_card(bench[0], card_id="cmc_test_real")

    records = run_main(tmp_path, [card, ghost], monkeypatch)

    assert [r["publicId"] for r in records] == ["cmc_test_real"]


def test_write_refuses_the_snapshot_selected_by_latest_pointer(bench, monkeypatch):
    """Active last-good generation係 immutable；image self-heal只准改 candidate。"""

    assets, qc, tmp_path = bench
    publish_root = tmp_path / "data" / "runtime" / "publish-staging"
    snapshot_path = publish_root / "generations" / "g1" / "snapshot.json"
    snapshot_path.parent.mkdir(parents=True)
    snapshot_bytes = json.dumps(
        {"generation": {"contentSha256": ""}, "top100": [], "watchlist": []}
    ).encode("utf-8")
    snapshot_path.write_bytes(snapshot_bytes)
    pointer = publish_root / "latest.json"
    pointer.write_text(
        json.dumps({"snapshotKey": "generations/g1/snapshot.json"}),
        encoding="utf-8",
    )
    before_qc = qc.read_bytes()
    before_assets = sorted(path.name for path in assets.iterdir())
    monkeypatch.setattr(mod, "LATEST_POINTERS", (pointer,))
    monkeypatch.setattr(
        sys,
        "argv",
        ["ensure_std_card_images.py", str(snapshot_path), "--write"],
    )

    with pytest.raises(RuntimeError, match="active pointed generation"):
        mod.main()

    assert snapshot_path.read_bytes() == snapshot_bytes
    assert qc.read_bytes() == before_qc
    assert sorted(path.name for path in assets.iterdir()) == before_assets
