#!/usr/bin/env python3
"""Deterministic front-card canvas geometry QC.

The public CARDZ card image contract is a 429x600 transparent RGBA canvas with
one centred, near-full-size 2.5x3.5 card.  File dimensions alone are not
enough: a tiny card centred inside a correctly-sized transparent canvas would
otherwise pass and render visibly shrunken in the frontend.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image

POLICY_ID = "cardz-front-geometry-v1"
CANVAS_WIDTH = 429
CANVAS_HEIGHT = 600
ALPHA_THRESHOLD = 16
MIN_FILL_RATIO = 0.95
MIN_ASPECT_RATIO = 0.68
MAX_ASPECT_RATIO = 0.75
MAX_CENTER_OFFSET_PX = 6.0


def _rounded(value: float) -> float:
    return round(value, 6)


def inspect_image(image: Image.Image) -> dict[str, Any]:
    """Return stable geometry evidence without changing image bytes."""

    width, height = image.size
    reasons: list[str] = []
    if (width, height) != (CANVAS_WIDTH, CANVAS_HEIGHT):
        reasons.append("canvas_size_mismatch")

    if "A" not in image.getbands():
        reasons.append("transparent_canvas_missing")
        bbox = None
    else:
        alpha = image.getchannel("A")
        if alpha.getextrema()[0] >= ALPHA_THRESHOLD:
            reasons.append("transparent_canvas_missing")
            bbox = None
        else:
            mask = alpha.point(
                lambda value: 255 if value >= ALPHA_THRESHOLD else 0,
                mode="1",
            )
            bbox = mask.getbbox()
            if bbox is None:
                reasons.append("card_bounds_missing")

    evidence: dict[str, Any] = {
        "policyId": POLICY_ID,
        "status": "failed",
        "widthPx": width,
        "heightPx": height,
        "cardBounds": list(bbox) if bbox is not None else None,
        "fillWidthRatio": None,
        "fillHeightRatio": None,
        "areaRatio": None,
        "cardAspectRatio": None,
        "centerOffsetPx": None,
        "reasons": reasons,
    }
    if bbox is not None:
        left, top, right, bottom = bbox
        card_width = right - left
        card_height = bottom - top
        fill_width = card_width / width if width else 0.0
        fill_height = card_height / height if height else 0.0
        aspect = card_width / card_height if card_height else 0.0
        canvas_center_x = width / 2
        canvas_center_y = height / 2
        card_center_x = (left + right) / 2
        card_center_y = (top + bottom) / 2
        center_offset = max(
            abs(card_center_x - canvas_center_x),
            abs(card_center_y - canvas_center_y),
        )
        evidence.update(
            {
                "fillWidthRatio": _rounded(fill_width),
                "fillHeightRatio": _rounded(fill_height),
                "areaRatio": _rounded(fill_width * fill_height),
                "cardAspectRatio": _rounded(aspect),
                "centerOffsetPx": _rounded(center_offset),
            }
        )
        if fill_width < MIN_FILL_RATIO or fill_height < MIN_FILL_RATIO:
            reasons.append("card_fill_too_small")
        if not MIN_ASPECT_RATIO <= aspect <= MAX_ASPECT_RATIO:
            reasons.append("card_aspect_invalid")
        if center_offset > MAX_CENTER_OFFSET_PX:
            reasons.append("card_off_center")

    evidence["reasons"] = sorted(set(reasons))
    evidence["status"] = "passed" if not reasons else "failed"
    return evidence


def inspect_path(path: Path) -> dict[str, Any]:
    with Image.open(path) as image:
        image.load()
        return inspect_image(image)

