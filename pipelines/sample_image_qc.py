# -*- coding: utf-8 -*-
"""SAMPLE / NOW DESIGNING image hard gate.

Policy: TCGplayer SAMPLE watermark = NEVER public. Call before writing
public market-assets. Prefer fail-closed when OCR is available.
"""
from __future__ import annotations

import hashlib
import re
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageEnhance, ImageOps

SAMPLE_TOKENS = ("SAMPLE", "SAMP1E", "SAMPIE", "5AMPLE")
PLACEHOLDER_TOKENS = ("NOWDESIGNING", "NOW DESIGNING")
TESS = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")


class SampleImageRejected(ValueError):
    """Raised when image is SAMPLE / placeholder watermark."""


def tesseract_command() -> str | None:
    configured = os.environ.get("TESSERACT_BIN", "").strip()
    if configured and Path(configured).is_file():
        return configured
    discovered = shutil.which("tesseract")
    if discovered:
        return discovered
    if TESS.is_file():
        return str(TESS)
    return None


def _tess(img: Image.Image, psm: int = 6) -> str:
    command = tesseract_command()
    if command is None:
        return ""
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
        tmp = Path(tf.name)
    try:
        img.save(tmp)
        out = subprocess.run(
            [
                command,
                str(tmp),
                "stdout",
                "--psm",
                str(psm),
                "-c",
                "tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ ",
            ],
            capture_output=True,
            text=True,
            timeout=12,
            check=False,
        )
        return (out.stdout or "").upper()
    except Exception:
        return ""
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass


def _token_hit(text: str) -> str | None:
    compact = re.sub(r"[^A-Z0-9]", "", (text or "").upper())
    normalized = compact.replace("5", "S").replace("1", "L").replace("I", "L")
    if "SAMPLE" in normalized:
        return "SAMPLE"
    for tok in SAMPLE_TOKENS:
        if tok in compact:
            return tok
    for tok in PLACEHOLDER_TOKENS:
        if tok.replace(" ", "") in compact:
            return "NOW_DESIGNING"
    return None


def _tess_words(img: Image.Image, psm: int = 6) -> list[dict[str, Any]]:
    command = tesseract_command()
    if command is None:
        return []
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
        tmp = Path(tf.name)
    try:
        img.save(tmp)
        out = subprocess.run(
            [
                command,
                str(tmp),
                "stdout",
                "--psm",
                str(psm),
                "-c",
                "tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ ",
                "tsv",
            ],
            capture_output=True,
            text=True,
            timeout=12,
            check=False,
        )
        words: list[dict[str, Any]] = []
        for line in (out.stdout or "").splitlines()[1:]:
            columns = line.split("\t")
            if len(columns) != 12:
                continue
            text = columns[11].strip().upper()
            if not text:
                continue
            words.append(
                {
                    "text": text,
                    "left": int(columns[6]),
                    "top": int(columns[7]),
                    "width": int(columns[8]),
                    "height": int(columns[9]),
                }
            )
        return words
    except Exception:
        return []
    finally:
        tmp.unlink(missing_ok=True)


def _position(
    *,
    left: int,
    top: int,
    width: int,
    height: int,
    canvas_width: int,
    canvas_height: int,
) -> str:
    x = (left + width / 2) / canvas_width
    y = (top + height / 2) / canvas_height
    if 0.28 <= x <= 0.72 and 0.25 <= y <= 0.75:
        return "center"
    vertical = "top" if y < 0.5 else "bottom"
    horizontal = "left" if x < 0.5 else "right"
    return f"{vertical}_{horizontal}"


def detect_sample_evidence(
    image: Image.Image,
    *,
    deep: bool = True,
) -> dict[str, Any] | None:
    """Return SAMPLE/placeholder evidence with a coarse visible location."""
    im = image.convert("RGBA")
    w, h = im.size
    if w < 40 or h < 40:
        return None

    rgb = im.convert("RGB")
    arr = np.asarray(rgb).astype(np.float32)
    r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
    yellow = (r > 150) & (g > 120) & (b < 150) & ((r + g - b) > 250)
    ymask = Image.fromarray((yellow.astype(np.uint8) * 255))
    gray = ImageEnhance.Contrast(ImageOps.grayscale(rgb)).enhance(2.5)

    # Large white SAMPLE overlays need a high binary threshold.  Tesseract
    # commonly reads the L as I, so _token_hit normalizes that one glyph.
    enhanced = ImageOps.autocontrast(
        ImageEnhance.Contrast(ImageOps.grayscale(rgb)).enhance(3.0)
    )
    thresholds = (160, 180, 200) if deep else (180,)
    for threshold in thresholds:
        binary = enhanced.point(lambda value, cutoff=threshold: 255 if value > cutoff else 0)
        for word in _tess_words(binary, 6):
            token = _token_hit(word["text"])
            if token:
                position = _position(
                    left=word["left"],
                    top=word["top"],
                    width=word["width"],
                    height=word["height"],
                    canvas_width=w,
                    canvas_height=h,
                )
                return {
                    "reason": f"ocr_{token}",
                    "token": token,
                    "position": position,
                    "bounds": {
                        "left": word["left"],
                        "top": word["top"],
                        "width": word["width"],
                        "height": word["height"],
                    },
                    "method": f"tesseract_threshold_{threshold}",
                }

    angles = (-35, 35, -30, 30) if deep else (-35, 35)
    for base in (ymask, gray):
        crop = base.crop((w // 12, h // 12, w * 11 // 12, h * 11 // 12))
        for angle in (0,) + angles:
            frame = crop if angle == 0 else crop.rotate(angle, expand=True, fillcolor=0)
            tok = _token_hit(_tess(frame, 6))
            if tok:
                return {
                    "reason": f"ocr_{tok}",
                    "token": tok,
                    "position": "center" if angle == 0 else "diagonal_center",
                    "bounds": None,
                    "method": f"tesseract_rotated_{angle}",
                }

    # Strong diagonal yellow band without OCR (Tesseract missing) — fail-closed for public store
    a = np.asarray(im)[..., 3]
    opaque = a > 180
    if opaque.sum() > 500:
        yy, xx = np.mgrid[0:h, 0:w]
        diag = np.abs(yy / h - xx / w) < 0.08
        mid = opaque & (yy > h * 0.2) & (yy < h * 0.8) & (xx > w * 0.15) & (xx < w * 0.85)
        yf = float(yellow.sum()) / float(opaque.sum())
        y_diag = float((yellow & diag & mid).sum()) / max(int((diag & mid).sum()), 1)
        y_off = float((yellow & opaque & ~diag).sum()) / max(int((opaque & ~diag).sum()), 1)
        conc = y_diag / max(y_off, 1e-6)
        if (
            0.015 < yf < 0.10
            and conc >= 3.0
            and y_diag >= 0.06
            and tesseract_command() is None
        ):
            return {
                "reason": "pixel_sample_suspect_no_tesseract",
                "token": "SAMPLE_SUSPECT",
                "position": "diagonal_center",
                "bounds": None,
                "method": "pixel_diagonal",
            }

    return None


def detect_sample_reason(image: Image.Image, *, deep: bool = True) -> str | None:
    evidence = detect_sample_evidence(image, deep=deep)
    return str(evidence["reason"]) if evidence else None


def assert_not_sample(image: Image.Image, *, context: str = "") -> None:
    evidence = detect_sample_evidence(image, deep=True)
    if evidence:
        prefix = f"{context}: " if context else ""
        raise SampleImageRejected(
            f"{prefix}SAMPLE/placeholder rejected "
            f"({evidence['reason']}@{evidence['position']})"
        )


def assert_raw_bytes_not_sample(raw: bytes, *, context: str = "") -> None:
    import io

    try:
        from .image_source_qc import classify_content_sha256
    except ImportError:
        from image_source_qc import classify_content_sha256

    content_policy = classify_content_sha256(hashlib.sha256(raw).hexdigest())
    if content_policy["status"] == "reject":
        prefix = f"{context}: " if context else ""
        raise SampleImageRejected(
            f"{prefix}SAMPLE content rejected ({content_policy['reason']})"
        )
    with Image.open(io.BytesIO(raw)) as opened:
        assert_not_sample(opened.copy(), context=context)


def assert_raw_source_not_sample(
    raw: bytes,
    *,
    source_path: str | None,
    tcg_code: str | None,
    context: str = "",
) -> dict[str, object]:
    """Apply source reject/clean rules before falling through to OCR."""

    import io

    try:
        from .image_source_qc import classify_source
    except ImportError:
        from image_source_qc import classify_source

    with Image.open(io.BytesIO(raw)) as opened:
        source_policy = classify_source(
            source_path,
            tcg_code=tcg_code,
            width_px=opened.width,
            height_px=opened.height,
        )
    if source_policy["status"] == "reject":
        prefix = f"{context}: " if context else ""
        raise SampleImageRejected(
            f"{prefix}SAMPLE source rejected ({source_policy['reason']})"
        )
    if source_policy["status"] == "scan_required":
        assert_raw_bytes_not_sample(raw, context=context)
    return source_policy
