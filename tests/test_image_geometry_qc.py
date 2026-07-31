from __future__ import annotations

from PIL import Image, ImageDraw

from pipelines.image_geometry_qc import inspect_image


def card_canvas(
    *,
    bounds: tuple[int, int, int, int] = (4, 5, 425, 596),
) -> Image.Image:
    image = Image.new("RGBA", (429, 600), (0, 0, 0, 0))
    ImageDraw.Draw(image).rounded_rectangle(bounds, radius=24, fill=(20, 40, 60, 255))
    return image


def test_standard_near_full_card_passes() -> None:
    result = inspect_image(card_canvas())
    assert result["status"] == "passed"
    assert result["fillWidthRatio"] > 0.98
    assert result["fillHeightRatio"] > 0.98


def test_correct_canvas_with_tiny_card_fails_fill_gate() -> None:
    result = inspect_image(card_canvas(bounds=(100, 120, 329, 480)))
    assert result["status"] == "failed"
    assert "card_fill_too_small" in result["reasons"]


def test_wrong_canvas_and_solid_rgb_do_not_pass_as_standard() -> None:
    result = inspect_image(Image.new("RGB", (400, 560), "white"))
    assert result["status"] == "failed"
    assert result["reasons"] == [
        "canvas_size_mismatch",
        "transparent_canvas_missing",
    ]


def test_off_center_or_wrong_aspect_card_fails() -> None:
    result = inspect_image(card_canvas(bounds=(0, 4, 300, 596)))
    assert result["status"] == "failed"
    assert "card_aspect_invalid" in result["reasons"]
    assert "card_off_center" in result["reasons"]
