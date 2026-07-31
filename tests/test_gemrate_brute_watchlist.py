from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "gemrate_brute_harvest",
    ROOT / "pipelines" / "gemrate_brute_harvest.py",
)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def card(population: int, gemrate_id: str = "a" * 40) -> dict[str, object]:
    return {"psa_10": str(population), "psa_id": gemrate_id}


def test_watchlist_threshold_includes_1000() -> None:
    rows = module.qualifying_psa10_cards(
        [card(999), card(1000), card(1001), card(2000, "not-an-id")]
    )
    assert [row["psa_10"] for row in rows] == ["1000", "1001"]


def test_watchlist_schema_keeps_unmapped_provider_rows() -> None:
    migration = (
        ROOT / "pipelines/migrations/013_gemrate_psa10_watchlist.mysql.sql"
    ).read_text(encoding="utf-8")
    assert "variant_id BIGINT UNSIGNED NULL" in migration
    assert "PRIMARY KEY (gemrate_id)" in migration


def test_cards_id_is_stable_and_each_gemrate_version_is_independent() -> None:
    first = "a" * 40
    second = "b" * 40
    assert module.cards_opaque_id(first) == module.cards_opaque_id(first.upper())
    assert module.cards_opaque_id(first) != module.cards_opaque_id(second)


def test_only_pokemon_and_one_piece_are_admitted() -> None:
    assert module.tcg_code({"set_name": "Pokemon Scarlet and Violet", "card_number": "232"}) == "pokemon"
    assert module.tcg_code({"set_name": "One Piece Romance Dawn", "card_number": "OP01-120"}) == "one-piece"
    assert module.tcg_code({"set_name": "Unknown", "card_number": "ST21-014"}) == "one-piece"
    assert module.tcg_code({"set_name": "Magic Set", "card_number": "123"}) is None
