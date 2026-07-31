from pipelines.image_source_qc import classify_content_sha256, classify_source


def test_official_one_piece_cardlist_is_known_sample_source() -> None:
    result = classify_source(
        "https://asia-en.onepiece-cardgame.com/images/cardlist/card/OP01-001.png",
        tcg_code="one-piece",
        width_px=600,
        height_px=838,
    )
    assert result["status"] == "reject"
    assert result["reason"] == "known_sample_source_official_one_piece_cardlist"


def test_one_piece_tcgplayer_sample_template_is_rejected() -> None:
    result = classify_source(
        "https://tcgplayer-cdn.tcgplayer.com/product/123_in_1000x1000.jpg",
        tcg_code="one-piece",
        width_px=600,
        height_px=837,
    )
    assert result["status"] == "reject"
    assert result["reason"] == "known_sample_source_tcgplayer_one_piece_template"


def test_tcgplayer_is_not_blanket_rejected() -> None:
    result = classify_source(
        "https://tcgplayer-cdn.tcgplayer.com/product/641620_in_1000x1000.jpg",
        tcg_code="one-piece",
        width_px=625,
        height_px=873,
    )
    assert result["status"] == "scan_required"


def test_limitless_one_piece_en_is_rejected_for_one_piece_only() -> None:
    for source in (
        "https://limitlesstcg.nyc3.cdn.digitaloceanspaces.com/one-piece/EB02/EB02-010_EN.webp",
        "https://LIMITLESSTCG.nyc3.cdn.digitaloceanspaces.com/%6FNE-PIECE/EB02/renamed-card.webp?cache=1",
        "https://onepiece.limitlesstcg.com/Card%73/OP01-001?cache=1",
    ):
        result = classify_source(
            source,
            tcg_code="one-piece",
            width_px=429,
            height_px=600,
        )
        assert result["status"] == "reject"
        assert result["family"] == "limitless-one-piece-en"
        assert result["reason"] == "known_sample_source_limitless_one_piece_en"

    other_tcg = classify_source(
        "https://onepiece.limitlesstcg.com/Cards/OP01-001?cache=1",
        tcg_code="pokemon",
        width_px=429,
        height_px=600,
    )
    assert other_tcg["status"] == "scan_required"


def test_snkrdunk_front_still_requires_sample_scan() -> None:
    result = classify_source(
        "snkrdunk:93024:https://cdn.snkrdunk.com/upload_bg_removed/card.webp",
        tcg_code="pokemon",
        width_px=429,
        height_px=600,
    )
    assert result["status"] == "scan_required"


def test_visually_confirmed_sample_content_hash_is_permanently_rejected() -> None:
    result = classify_content_sha256(
        "4a53529faf845d82b7c06ab04e9fa161792b2d2d9c1e9bee01b594cfef3895a6"
    )
    assert result["status"] == "reject"
    assert result["reason"] == "known_sample_content_visual_confirmed"
