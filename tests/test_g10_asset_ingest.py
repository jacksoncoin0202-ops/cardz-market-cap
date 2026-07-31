"""`pipelines/g10_asset_ingest.py` 嘅回歸測試。

重點守住兩條容易做錯、而且錯咗會偽造來源鏈嘅規矩：
  1. `content_sha256`（圖檔 bytes）同 `source_version_sha256`（asset_info.json
     bytes）係兩樣嘢，唔准互相頂替；配唔到 asset_info 就 quarantine。
  2. QC 只准記真係量得到嘅嘢 —— asset_info 冇 TCG 欄位，`tcg_match` 恆 0。
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from g10_asset_ingest import (  # noqa: E402
    ImageRecord,
    SourceRecord,
    Tally,
    attach_private_keys,
    build,
    build_qc,
    normalise_card_number,
    probe_image,
    scan_images,
    scan_source_records,
    write,
)
from data_routing import DEFAULT_ROUTES, load_and_validate, load_release_profile  # noqa: E402


# --------------------------------------------------------------------------- fixtures


def _write_card(cards_root: Path, provider: str, external_id: str, **overrides) -> Path:
    card_dir = cards_root / provider / external_id
    card_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "assetQueryId": {"id": external_id, "source": provider},
        "cardName": "Pikachu V",
        "setName": "Start Deck 100",
        "cardId": "1",
        "language": "jp",
        "image": f"https://cdn.example.com/{external_id}.webp",
    }
    payload.update(overrides)
    path = card_dir / "asset_info.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _write_image(images_root: Path, name: str, *, fmt: str, size=(429, 600), alpha_corners=False) -> Path:
    images_root.mkdir(parents=True, exist_ok=True)
    if alpha_corners:
        im = Image.new("RGBA", size, (0, 0, 0, 0))
        # 中間填實色，四角保持全透明
        inner = Image.new("RGBA", (size[0] // 2, size[1] // 2), (255, 0, 0, 255))
        im.paste(inner, (size[0] // 4, size[1] // 4))
    else:
        im = Image.new("RGB", size, (12, 34, 56))
    path = images_root / name
    im.save(path, format=fmt)
    return path


@pytest.fixture()
def g10_tree(tmp_path: Path) -> Path:
    root = tmp_path / "g10"
    (root / "cards").mkdir(parents=True)
    _write_card(root / "cards", "snkrdunk", "100081")
    _write_card(root / "cards", "altxyz", "uuid-a", cardId="", language="en")
    _write_image(root / "images", "snkrdunk_100081.webp", fmt="WEBP", alpha_corners=True)
    _write_image(root / "images", "altxyz_uuid-a.jpg", fmt="JPEG")
    return root


def _landing_from(images_root: Path, tmp_path: Path, *, corrupt: set[str] | None = None) -> Path:
    landing = tmp_path / "landing"
    run_dir = landing / "g10" / "full" / "runsha"
    dest = run_dir / "payload" / "images"
    dest.mkdir(parents=True)
    for src in images_root.iterdir():
        raw = src.read_bytes()
        if corrupt and src.name in corrupt:
            raw = raw + b"drift"
        (dest / src.name).write_bytes(raw)
    return landing


class _FakeCursor:
    def __init__(self, identity_rows, variant_rows):
        self._identity = identity_rows
        self._variants = variant_rows
        self._result: list = []

    def execute(self, sql, params=None):
        if "catalog_source_identity" in sql:
            self._result = self._identity
        elif "catalog_variant" in sql:
            wanted = set(params or [])
            self._result = [r for r in self._variants if r["id"] in wanted]
        else:
            self._result = []

    def fetchall(self):
        return self._result

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConnection:
    def __init__(self, identity_rows, variant_rows):
        self._identity = identity_rows
        self._variants = variant_rows

    def cursor(self):
        return _FakeCursor(self._identity, self._variants)


def _connection(*, identities, variants):
    return _FakeConnection(identities, variants)


DEFAULT_IDENTITIES = [
    {"variant_id": 11, "source_code": "snkrdunk", "external_entity_id": "100081"},
    {"variant_id": 22, "source_code": "ebay", "external_entity_id": "uuid-a"},
]
DEFAULT_VARIANTS = [
    {"id": 11, "canonical_name": "Pikachu V", "collector_number": "001", "card_language": "ja", "tcg_code": "pokemon"},
    {"id": 22, "canonical_name": "Ancient Mew", "collector_number": "", "card_language": "en", "tcg_code": "pokemon"},
]


# --------------------------------------------------------------------------- unit


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("1", "1"), ("001", "1"), ("001/024", "1024"), ("SN 001", "sn001"), ("", ""), (None, "")],
)
def test_normalise_card_number(raw, expected):
    assert normalise_card_number(raw) == expected


def test_probe_image_reads_real_format_not_extension(tmp_path: Path):
    """G10 有 114 個檔副檔名講大話（110 個 .jpg 其實係 WEBP），唔准信副檔名。"""
    liar = _write_image(tmp_path, "looks_like.jpg", fmt="WEBP", size=(716, 1000))
    width, height, mime, _ = probe_image(liar)
    assert (width, height) == (716, 1000)
    assert mime == "image/webp"


def test_probe_image_detects_alpha_corners(tmp_path: Path):
    native = _write_image(tmp_path, "native.webp", fmt="WEBP", alpha_corners=True)
    flat = _write_image(tmp_path, "flat.jpg", fmt="JPEG")
    assert probe_image(native)[3] is True
    assert probe_image(flat)[3] is False


def test_scan_images_measures_real_dimensions(g10_tree: Path):
    records, skipped = scan_images(g10_tree / "images")
    assert skipped == {}
    assert {r.external_id for r in records} == {"100081", "uuid-a"}
    for record in records:
        assert record.width_px > 0 and record.height_px > 0


def test_scan_images_skips_unparsable_filename(g10_tree: Path):
    _write_image(g10_tree / "images", "noprovider.jpg", fmt="JPEG")
    _, skipped = scan_images(g10_tree / "images")
    assert skipped["unparsable_filename"] == 1


def test_scan_source_records_hashes_file_bytes(g10_tree: Path):
    records, skipped = scan_source_records(g10_tree / "cards")
    assert skipped == {}
    for record in records:
        assert record.version_sha256 == hashlib.sha256(record.path.read_bytes()).hexdigest()


def test_scan_source_records_counts_missing_asset_info(g10_tree: Path):
    (g10_tree / "cards" / "snkrdunk" / "999").mkdir(parents=True)
    _, skipped = scan_source_records(g10_tree / "cards")
    assert skipped["missing_asset_info"] == 1


# --------------------------------------------------------------------------- private landing


def test_attach_private_keys_quarantines_content_drift(g10_tree: Path, tmp_path: Path):
    images, _ = scan_images(g10_tree / "images")
    landing = _landing_from(g10_tree / "images", tmp_path, corrupt={"altxyz_uuid-a.jpg"})
    tally = Tally()
    kept = attach_private_keys(images, landing / "g10" / "full" / "runsha", tally)
    assert {r.external_id for r in kept} == {"100081"}
    assert tally.quarantined == 1
    assert tally.reasons["private_landing_content_drift"] == 1
    assert kept[0].private_object_key == "g10/full/runsha/payload/images/snkrdunk_100081.webp"


def test_attach_private_keys_quarantines_missing_copy(g10_tree: Path, tmp_path: Path):
    images, _ = scan_images(g10_tree / "images")
    landing = _landing_from(g10_tree / "images", tmp_path)
    (landing / "g10" / "full" / "runsha" / "payload" / "images" / "altxyz_uuid-a.jpg").unlink()
    tally = Tally()
    kept = attach_private_keys(images, landing / "g10" / "full" / "runsha", tally)
    assert len(kept) == 1
    assert tally.reasons["no_private_landing_copy"] == 1


# --------------------------------------------------------------------------- 三個 sha 唔准互相頂替


def test_asset_uses_source_version_sha_not_its_own_content_sha(g10_tree: Path, tmp_path: Path):
    landing = _landing_from(g10_tree / "images", tmp_path)
    plan = build(
        _connection(identities=DEFAULT_IDENTITIES, variants=DEFAULT_VARIANTS),
        g10_tree / "cards",
        g10_tree / "images",
        landing,
    )
    assert plan["asset_rows"]
    for record in plan["asset_rows"]:
        assert record.source is not None
        # source_version_sha256 一定係 asset_info.json 嘅 sha，唔係圖檔自己嘅 sha
        assert record.source.version_sha256 == hashlib.sha256(record.source.path.read_bytes()).hexdigest()
        assert record.content_sha256 == hashlib.sha256(record.path.read_bytes()).hexdigest()
        assert record.content_sha256 != record.source.version_sha256


def test_image_without_asset_info_is_quarantined_never_faked(g10_tree: Path, tmp_path: Path):
    """冇 asset_info 就冇合法 source_version_sha256 —— 唔准攞 content sha 頂上去。"""
    _write_image(g10_tree / "images", "snkrdunk_orphan.jpg", fmt="JPEG")
    landing = _landing_from(g10_tree / "images", tmp_path)
    identities = DEFAULT_IDENTITIES + [
        {"variant_id": 33, "source_code": "snkrdunk", "external_entity_id": "orphan"}
    ]
    plan = build(
        _connection(identities=identities, variants=DEFAULT_VARIANTS),
        g10_tree / "cards",
        g10_tree / "images",
        landing,
    )
    assert "orphan" not in {r.external_id for r in plan["asset_rows"]}
    assert plan["image_tally"].reasons["no_paired_asset_info"] == 1
    assert plan["image_tally"].quarantined == 1


def test_unmatched_identity_is_rejected_not_name_matched(g10_tree: Path, tmp_path: Path):
    """卡名 match 係假嘅，對唔到 identity 就要如實計 rejected。"""
    landing = _landing_from(g10_tree / "images", tmp_path)
    plan = build(
        _connection(identities=[DEFAULT_IDENTITIES[0]], variants=DEFAULT_VARIANTS),
        g10_tree / "cards",
        g10_tree / "images",
        landing,
    )
    assert {r.external_id for r in plan["asset_rows"]} == {"100081"}
    assert plan["image_tally"].reasons["no_identity_match"] == 1
    assert plan["source_tally"].reasons["no_identity_match"] == 1


# --------------------------------------------------------------------------- QC


def test_qc_never_claims_tcg_or_public():
    image = ImageRecord(
        provider="snkrdunk",
        external_id="100081",
        path=Path("snkrdunk_100081.webp"),
        content_sha256="a" * 64,
        width_px=429,
        height_px=600,
        mime_type="image/webp",
        has_alpha_corners=True,
        captured_at=datetime(2026, 7, 25),
    )
    image.source = SourceRecord(
        provider="snkrdunk",
        external_id="100081",
        path=Path("asset_info.json"),
        version_sha256="b" * 64,
        data={"cardId": "1", "language": "jp"},
        observed_at=datetime(2026, 7, 25),
    )
    qc = build_qc(image, {"collector_number": "001", "card_language": "ja", "tcg_code": "pokemon"})
    assert qc["tcg_match"] == 0
    assert qc["public_allowed"] == 0
    assert "tcg_unstated_in_source" in qc["rejection_reason"]
    # 卡號同 raw-front 證據仍然保留；語言已退出 DB identity。
    assert qc["card_number_match"] == 1
    assert qc["language_match"] == 0
    assert "card_language_removed" in qc["rejection_reason"]
    assert qc["raw_front_confirmed"] == 1


def test_qc_reports_mismatch_instead_of_guessing():
    image = ImageRecord(
        provider="altxyz",
        external_id="uuid-a",
        path=Path("altxyz_uuid-a.jpg"),
        content_sha256="c" * 64,
        width_px=716,
        height_px=1000,
        mime_type="image/jpeg",
        has_alpha_corners=False,
        captured_at=datetime(2026, 7, 25),
    )
    image.source = SourceRecord(
        provider="altxyz",
        external_id="uuid-a",
        path=Path("asset_info.json"),
        version_sha256="d" * 64,
        data={"cardId": "", "language": "en"},
        observed_at=datetime(2026, 7, 25),
    )
    qc = build_qc(image, {"collector_number": "042", "card_language": "ja", "tcg_code": "pokemon"})
    assert qc["card_number_match"] == 0
    assert qc["language_match"] == 0
    assert qc["raw_front_confirmed"] == 0
    assert "card_number_absent_in_source" in qc["rejection_reason"]
    assert "raw_front_not_alpha_native" in qc["rejection_reason"]


def test_qc_row_per_accepted_asset(g10_tree: Path, tmp_path: Path):
    landing = _landing_from(g10_tree / "images", tmp_path)
    plan = build(
        _connection(identities=DEFAULT_IDENTITIES, variants=DEFAULT_VARIANTS),
        g10_tree / "cards",
        g10_tree / "images",
        landing,
    )
    assert set(plan["qc_rows"]) == {r.content_sha256 for r in plan["asset_rows"]}


def test_relaxed_profile_marks_valid_g10_images_public_without_metadata_qc_gate(
    g10_tree: Path, tmp_path: Path
):
    landing = _landing_from(g10_tree / "images", tmp_path)
    routing = load_and_validate(DEFAULT_ROUTES)
    relaxed = load_release_profile(routing, "relaxed-launch-v1")
    strict = load_release_profile(routing, "strict-v1")

    relaxed_plan = build(
        _connection(identities=DEFAULT_IDENTITIES, variants=DEFAULT_VARIANTS),
        g10_tree / "cards",
        g10_tree / "images",
        landing,
        release_profile=relaxed,
    )
    assert relaxed_plan["g10ImagesPublicEligible"] is True
    assert relaxed_plan["releaseProfile"] == "relaxed-launch-v1"
    assert {row["public_allowed"] for row in relaxed_plan["qc_rows"].values()} == {1}
    # The G10 fixture has no TCG proof; relaxed acceptance must not turn that
    # absence into a pre-ingest QC gate.
    assert {row["tcg_match"] for row in relaxed_plan["qc_rows"].values()} == {0}

    strict_plan = build(
        _connection(identities=DEFAULT_IDENTITIES, variants=DEFAULT_VARIANTS),
        g10_tree / "cards",
        g10_tree / "images",
        landing,
        release_profile=strict,
    )
    assert strict_plan["g10ImagesPublicEligible"] is False
    assert {row["public_allowed"] for row in strict_plan["qc_rows"].values()} == {0}

    class RecordingCursor:
        def __init__(self, connection):
            self.connection = connection
            self.lastrowid = 1
            self.row = None

        def execute(self, sql, params=None):
            self.connection.statements.append((sql, params))
            if "SELECT COUNT(*)" in sql:
                self.row = {"c": 0}
            elif "SELECT id FROM market_image_asset" in sql:
                self.row = {"id": 1}
            else:
                self.row = None

        def fetchone(self):
            return self.row

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class RecordingConnection:
        def __init__(self):
            self.statements = []
            self.committed = False

        def cursor(self):
            return RecordingCursor(self)

        def commit(self):
            self.committed = True

    connection = RecordingConnection()
    write(connection, relaxed_plan)
    pointer_writes = [
        (sql, params)
        for sql, params in connection.statements
        if "INSERT IGNORE INTO market_image_source_pointer" in sql
    ]
    qc_writes = [
        (sql, params)
        for sql, params in connection.statements
        if "INSERT INTO market_image_qc" in sql
    ]
    assert pointer_writes and qc_writes
    assert all(params[-2] == 1 for _sql, params in pointer_writes)
    assert all(params[6] == 1 for _sql, params in qc_writes)
    assert all("ON DUPLICATE KEY UPDATE" in sql for sql, _params in pointer_writes + qc_writes)
    # The upstream image URL is reduced to a hash; it is not written into a
    # public-eligible pointer field.
    assert all(
        "https://" not in str(value)
        for _sql, params in pointer_writes
        for value in params
    )


# --------------------------------------------------------------------------- run ledger


def test_tallies_are_not_all_zero(g10_tree: Path, tmp_path: Path):
    landing = _landing_from(g10_tree / "images", tmp_path)
    plan = build(
        _connection(identities=DEFAULT_IDENTITIES, variants=DEFAULT_VARIANTS),
        g10_tree / "cards",
        g10_tree / "images",
        landing,
    )
    assert plan["source_tally"].observed > 0
    assert plan["image_tally"].observed > 0
    assert plan["source_tally"].accepted > 0
    assert plan["image_tally"].accepted > 0


def test_run_key_inputs_are_content_derived(g10_tree: Path, tmp_path: Path):
    """run_key 要靠內容算，同一份輸入跑兩次先會 reuse 同一個 run。"""
    landing = _landing_from(g10_tree / "images", tmp_path)
    conn = _connection(identities=DEFAULT_IDENTITIES, variants=DEFAULT_VARIANTS)
    first = build(conn, g10_tree / "cards", g10_tree / "images", landing)
    second = build(conn, g10_tree / "cards", g10_tree / "images", landing)
    assert first["payload_sha256"] == second["payload_sha256"]
    assert first["manifest_sha256"] == second["manifest_sha256"]

    _write_card(g10_tree / "cards", "snkrdunk", "100082")
    _write_image(g10_tree / "images", "snkrdunk_100082.webp", fmt="WEBP")
    landing2 = _landing_from(g10_tree / "images", tmp_path / "second")
    conn2 = _connection(
        identities=DEFAULT_IDENTITIES
        + [{"variant_id": 44, "source_code": "snkrdunk", "external_entity_id": "100082"}],
        variants=DEFAULT_VARIANTS,
    )
    third = build(conn2, g10_tree / "cards", g10_tree / "images", landing2)
    assert third["payload_sha256"] != first["payload_sha256"]
