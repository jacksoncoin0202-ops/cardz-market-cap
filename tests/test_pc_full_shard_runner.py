from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import pc_full_shard_runner as runner  # noqa: E402
from pc_full_shard_runner import page_identity_ok  # noqa: E402


def test_saved_raw_snapshot_prevents_provider_refetch(tmp_path, monkeypatch) -> None:
    url = "https://www.pricecharting.com/game/one-piece/test-card-op01-001"
    saved = tmp_path / "snapshot.html"
    saved.write_text(
        '<html><head><title>Test Card OP01-001 Prices | One Piece Cards</title>'
        f'<link rel="canonical" href="{url}"></head><body>saved</body></html>',
        encoding="utf-8",
    )

    def fail_fetch(*args, **kwargs):
        raise AssertionError("saved raw snapshot must prevent a provider refetch")

    monkeypatch.setattr(runner, "fetch_locked", fail_fetch)
    snapshot, reused = runner.load_or_fetch_snapshot(url, saved)

    assert reused is True
    assert snapshot is not None
    assert snapshot["final_url"] == url


def test_one_piece_page_requires_exact_collector_number() -> None:
    work = {
        "tcg": "one-piece",
        "language": "en",
        "num": "OP11-040",
    }
    assert page_identity_ok(
        work,
        "Monkey D. Luffy OP11-040 Prices | One Piece Cards",
        "https://www.pricecharting.com/game/one-piece/fist-of-divine-speed/"
        "monkey-d-luffy-op11-040",
    )
    assert not page_identity_ok(
        work,
        "Monkey D. Luffy PRB02-005 Prices | One Piece Cards",
        "https://www.pricecharting.com/game/one-piece-promo/"
        "monkey-d-luffy-red-bull-double-don-prb02-005",
    )


def test_short_first_token_name_matches_exact_phrase_in_title() -> None:
    assert runner.name_ok(
        "Mr. Mime",
        "Mr. Mime #179 Prices | Pokemon Japanese Scarlet & Violet 151",
    )


def test_one_piece_page_requires_language_route() -> None:
    english = {
        "tcg": "one-piece",
        "language": "en",
        "num": "OP13-119",
    }
    japanese = {
        "tcg": "one-piece",
        "language": "ja",
        "num": "OP13-119",
    }
    title = (
        "Portgas D. Ace [Red Manga] OP13-119 Prices | "
        "One Piece Japanese Carrying on His Will"
    )
    url = (
        "https://www.pricecharting.com/game/"
        "one-piece-japanese-carrying-on-his-will/"
        "portgas-d-ace-red-manga-op13-119"
    )
    assert not page_identity_ok(english, title, url)
    assert page_identity_ok(japanese, title, url)


def test_catalog_language_hydration_overrides_null_and_stale_shard_values() -> None:
    work = [
        {"vid": 7, "tcg": "pokemon", "language": None},
        {"vid": 8, "tcg": "pokemon", "language": "en"},
    ]
    hydrated = runner.apply_catalog_languages(
        work,
        [
            {"requested_variant_id": 7, "tcg_code": "pokemon", "card_language": "ja"},
            {"requested_variant_id": 8, "tcg_code": "pokemon", "card_language": "ja"},
        ],
    )

    assert [row["language"] for row in hydrated] == ["ja", "ja"]
    assert all("_language_blocked_reason" not in row for row in hydrated)


def test_catalog_language_hydration_blocks_missing_or_unsupported_language() -> None:
    hydrated = runner.apply_catalog_languages(
        [{"vid": 7}, {"vid": 8}],
        [
            {"requested_variant_id": 7, "tcg_code": "pokemon", "card_language": None},
            {"requested_variant_id": 8, "tcg_code": "pokemon", "card_language": "ko"},
        ],
    )

    assert [row["_language_blocked_reason"] for row in hydrated] == [
        "catalog_language_missing",
        "catalog_language_unsupported",
    ]


def test_pokemon_japanese_language_rejects_english_route() -> None:
    japanese = {"tcg": "pokemon", "language": "ja"}
    english_url = "https://www.pricecharting.com/game/pokemon-base-set/charizard-4"
    japanese_url = "https://www.pricecharting.com/game/pokemon-japanese-base-set/charizard"

    assert not page_identity_ok(japanese, "Charizard Prices | Pokemon Cards", english_url)
    assert page_identity_ok(
        japanese,
        "Charizard Prices | Pokemon Japanese Base Set",
        japanese_url,
    )


def test_japanese_151_requires_the_exact_collection_route() -> None:
    work = {
        "tcg": "pokemon",
        "language": "ja",
        "set": "SV2a: Pokemon Card 151",
        "num": "094/165",
    }
    assert page_identity_ok(
        work,
        "Gengar #94 Prices | Pokemon Japanese Scarlet & Violet 151",
        "https://www.pricecharting.com/game/"
        "pokemon-japanese-scarlet-&-violet-151/gengar-94",
    )
    assert not page_identity_ok(
        work,
        "Gengar #94 Prices | Pokemon Japanese 1997 Carddass",
        "https://www.pricecharting.com/game/"
        "pokemon-japanese-1997-carddass/gengar-94",
    )
    assert not page_identity_ok(
        work,
        "Gengar #94 Prices | Pokemon Scarlet & Violet 151",
        "https://www.pricecharting.com/game/"
        "pokemon-scarlet-&-violet-151/gengar-94",
    )


def test_celebrations_cannot_bind_pop_series_same_number() -> None:
    work = {
        "tcg": "pokemon",
        "language": "en",
        "set": "Pokemon Sword and Shield Celebrations Classic Collection",
        "num": "17/17",
    }
    assert page_identity_ok(
        work,
        "Umbreon Star #17 Prices | Pokemon Celebrations",
        "https://www.pricecharting.com/game/pokemon-celebrations/"
        "umbreon-star-17",
    )
    assert not page_identity_ok(
        work,
        "Umbreon Gold Star #17 Prices | Pokemon POP Series 5",
        "https://www.pricecharting.com/game/pokemon-pop-series-5/"
        "umbreon-gold-star-17",
    )
