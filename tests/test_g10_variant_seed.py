from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))
SPEC = importlib.util.spec_from_file_location(
    "g10_variant_seed",
    ROOT / "pipelines" / "g10_variant_seed.py",
)
assert SPEC and SPEC.loader
seed = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = seed
SPEC.loader.exec_module(seed)


class Cursor:
    def __init__(self, owner: int | None) -> None:
        self.owner = owner
        self.result: dict[str, int] | None = None
        self.commands: list[tuple[str, object]] = []

    def execute(self, sql: str, args: object = None) -> None:
        normalized = " ".join(sql.split())
        self.commands.append((normalized, args))
        if normalized.startswith("SELECT variant_id FROM catalog_source_identity"):
            self.result = {"variant_id": self.owner} if self.owner is not None else None
        else:
            self.result = None

    def fetchone(self) -> dict[str, int] | None:
        return self.result


def row() -> object:
    return seed.SeedRow(
        external_entity_id="12345",
        opaque="cmc_fixture",
        tcg_code="pokemon",
        card_language="en",
        canonical_name="Pikachu",
        set_name="Promo",
        collector_number="001",
        evidence_sha256="a" * 64,
        source_path="fixture/asset_info.json",
        existing_variant_id=10,
        resolution="existing_printing",
    )


class SourceIdentityInvariantTests(unittest.TestCase):
    def test_existing_owner_cannot_be_rebound(self) -> None:
        cursor = Cursor(owner=20)
        with self.assertRaisesRegex(ValueError, "ownership changed"):
            seed.bind_source_identity(cursor, row(), 10)
        self.assertFalse(
            any(
                sql.startswith(("INSERT INTO catalog_source_identity", "UPDATE catalog_source_identity"))
                for sql, _args in cursor.commands
            )
        )

    def test_new_identity_is_locked_then_inserted_without_rebind_upsert(self) -> None:
        cursor = Cursor(owner=None)
        self.assertTrue(seed.bind_source_identity(cursor, row(), 10))
        sql = "\n".join(command for command, _args in cursor.commands)
        self.assertIn("FOR UPDATE", sql)
        self.assertIn("INSERT INTO catalog_source_identity", sql)
        self.assertNotIn("variant_id=VALUES", sql)

    def test_same_owner_may_refresh_metadata_without_changing_variant(self) -> None:
        cursor = Cursor(owner=10)
        self.assertFalse(seed.bind_source_identity(cursor, row(), 10))
        update = next(
            (sql, args)
            for sql, args in cursor.commands
            if sql.startswith("UPDATE catalog_source_identity")
        )
        self.assertNotIn("variant_id=", update[0])

    def test_resolved_review_is_not_reopened_by_seed_replay(self) -> None:
        source = (ROOT / "pipelines/g10_variant_seed.py").read_text(encoding="utf-8")
        self.assertIn("INSERT IGNORE INTO market_identity_review_queue", source)
        self.assertNotIn("ON DUPLICATE KEY UPDATE status='pending'", source)


if __name__ == "__main__":
    unittest.main()
