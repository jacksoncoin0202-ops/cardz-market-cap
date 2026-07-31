from __future__ import annotations

from pipelines.card_identity import printing_identity_sha256_from_row
from pipelines.repair_printing_hash7 import build_plan


def row(variant_id: int, *, current: str = "old", finish: str = "foil") -> dict:
    return {
        "variant_id": variant_id,
        "tcg_code": "pokemon",
        "card_language": "en",
        "set_name": f"set-{variant_id}",
        "collector_number": str(variant_id),
        "edition_code": "base",
        "parallel_code": "rare",
        "finish_code": finish,
        "canonical_printing_sha256": current,
    }


def test_plans_only_requested_drift() -> None:
    first = row(1)
    second = row(2)
    second["canonical_printing_sha256"] = printing_identity_sha256_from_row(second)

    plan = build_plan([first, second], {1, 2})

    assert [item["variantId"] for item in plan["repairs"]] == [1]
    assert plan["unchanged"] == [2]
    assert not plan["incomplete"]
    assert not plan["conflicts"]


def test_incomplete_row_fails_closed() -> None:
    plan = build_plan([row(1, finish="")], {1})

    assert plan["incomplete"] == [1]
    assert not plan["repairs"]


def test_duplicate_expected_hash_fails_closed() -> None:
    first = row(1)
    second = {**first, "variant_id": 2}

    plan = build_plan([first, second], {1})

    assert plan["conflicts"][0]["owners"] == [1, 2]
    assert not plan["repairs"]
