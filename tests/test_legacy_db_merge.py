from __future__ import annotations

import importlib.util
import sys
from datetime import datetime
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "legacy_db_merge",
    ROOT / "pipelines" / "legacy_db_merge.py",
)
assert SPEC and SPEC.loader
legacy_db_merge = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = legacy_db_merge
SPEC.loader.exec_module(legacy_db_merge)


def test_schema_name_is_strictly_identifier_safe() -> None:
    assert legacy_db_merge.validate_schema_name("cardz_wsl_merge_audit_20260728") == (
        "cardz_wsl_merge_audit_20260728"
    )
    for unsafe in ("cardz-market", "cardz.market", "`cardz`", "cardz;DROP"):
        with pytest.raises(ValueError):
            legacy_db_merge.validate_schema_name(unsafe)


def test_plan_hash_is_stable_for_dates_and_key_order() -> None:
    left = {
        "at": datetime(2026, 7, 28, 10, 30),
        "counts": {"raw": 696, "price": 539},
    }
    right = {
        "counts": {"price": 539, "raw": 696},
        "at": datetime(2026, 7, 28, 10, 30),
    }
    assert legacy_db_merge.sha256_json(left) == legacy_db_merge.sha256_json(right)


def test_apply_requires_exact_plan_hash_and_zero_collision_guards() -> None:
    digest = "a" * 64
    legacy_db_merge.assert_apply_safe(
        {"planSha256": digest, "collisionGuards": {"newer": 0}},
        digest,
    )
    with pytest.raises(RuntimeError, match="plan hash mismatch"):
        legacy_db_merge.assert_apply_safe(
            {"planSha256": digest, "collisionGuards": {"newer": 0}},
            "b" * 64,
        )
    with pytest.raises(RuntimeError, match="unsafe collisions"):
        legacy_db_merge.assert_apply_safe(
            {"planSha256": digest, "collisionGuards": {"newer": 1}},
            digest,
        )


def test_all_legacy_tables_are_explicitly_in_scope() -> None:
    expected = {
        "catalog_variant",
        "catalog_variant_locale",
        "market_source_observation",
        "market_price_observation",
        "market_grader_population_observation",
        "market_daily_sales_aggregate",
        "market_candidate_daily_snapshot",
        "market_index_snapshot",
        "market_alert",
    }
    assert expected <= set(legacy_db_merge.SOURCE_TABLES)


def test_story_and_effective_pointer_tables_are_preserved() -> None:
    assert "catalog_story_pointer" in legacy_db_merge.CANONICAL_ONLY_PRESERVED
    assert (
        "market_source_effective_observation"
        in legacy_db_merge.CANONICAL_ONLY_PRESERVED
    )
