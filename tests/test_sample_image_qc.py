from unittest import mock

import pytest
from PIL import Image

from pipelines import sample_image_qc as qc


def test_wsl_tesseract_is_discovered_from_path() -> None:
    with (
        mock.patch.dict("os.environ", {}, clear=True),
        mock.patch.object(qc.shutil, "which", return_value="/usr/bin/tesseract"),
    ):
        assert qc.tesseract_command() == "/usr/bin/tesseract"


def test_configured_tesseract_takes_priority() -> None:
    with (
        mock.patch.dict("os.environ", {"TESSERACT_BIN": "/opt/tesseract"}),
        mock.patch.object(qc.Path, "is_file", return_value=True),
        mock.patch.object(qc.shutil, "which", return_value="/usr/bin/tesseract"),
    ):
        assert qc.tesseract_command() == "/opt/tesseract"


def test_known_rejected_content_hash_is_blocked_before_ocr() -> None:
    raw = b"known-sample-bytes"
    rejected_hash = (
        "4a53529faf845d82b7c06ab04e9fa161792b2d2d9c1e9bee01b594cfef3895a6"
    )
    digest = mock.Mock()
    digest.hexdigest.return_value = rejected_hash
    with (
        mock.patch.object(qc.hashlib, "sha256", return_value=digest),
        mock.patch.object(qc, "assert_not_sample") as ocr,
        pytest.raises(qc.SampleImageRejected, match="known_sample_content"),
    ):
        qc.assert_raw_bytes_not_sample(raw)
    ocr.assert_not_called()


def test_tess_uses_discovered_command() -> None:
    completed = mock.Mock(stdout="SAMPLE", returncode=0)
    with (
        mock.patch.object(qc, "tesseract_command", return_value="/usr/bin/tesseract"),
        mock.patch.object(qc.subprocess, "run", return_value=completed) as run,
    ):
        assert qc._tess(Image.new("L", (80, 80))) == "SAMPLE"
    assert run.call_args.args[0][0] == "/usr/bin/tesseract"


def test_known_sample_source_rejects_before_ocr() -> None:
    import io

    output = io.BytesIO()
    Image.new("RGB", (600, 838), "white").save(output, format="PNG")
    with mock.patch.object(qc, "assert_raw_bytes_not_sample") as deep_scan:
        try:
            qc.assert_raw_source_not_sample(
                output.getvalue(),
                source_path=(
                    "https://asia-en.onepiece-cardgame.com/"
                    "images/cardlist/card/OP01-001.png"
                ),
                tcg_code="one-piece",
            )
        except qc.SampleImageRejected:
            pass
        else:
            raise AssertionError("known SAMPLE source was accepted")
    deep_scan.assert_not_called()


def test_limitless_one_piece_source_is_rejected_before_ocr() -> None:
    import io

    output = io.BytesIO()
    Image.new("RGB", (429, 600), "white").save(output, format="WEBP")
    with mock.patch.object(qc, "assert_raw_bytes_not_sample") as deep_scan:
        with pytest.raises(qc.SampleImageRejected, match="limitless"):
            qc.assert_raw_source_not_sample(
                output.getvalue(),
                source_path=(
                    "https://limitlesstcg.nyc3.cdn.digitaloceanspaces.com/"
                    "one-piece/EB02/EB02-010_EN.webp"
                ),
                tcg_code="one-piece",
            )
    deep_scan.assert_not_called()


def test_large_white_sample_reports_center_position() -> None:
    from PIL import ImageDraw, ImageFont

    image = Image.new("RGB", (429, 600), "#a22")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype("DejaVuSans-Bold.ttf", 72)
    draw.text((55, 240), "SAMPLE", fill="white", font=font)
    evidence = qc.detect_sample_evidence(image)
    assert evidence is not None
    assert evidence["token"] == "SAMPLE"
    assert evidence["position"] == "center"
