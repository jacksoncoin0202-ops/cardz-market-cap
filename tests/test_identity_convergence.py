from __future__ import annotations

import importlib.util
import sys
import unittest
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))
SPEC = importlib.util.spec_from_file_location(
    "identity_convergence",
    ROOT / "pipelines" / "identity_convergence.py",
)
assert SPEC and SPEC.loader
convergence = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = convergence
SPEC.loader.exec_module(convergence)


class PairCursor:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows
        self.sql = ""

    def execute(self, sql: str, _args: object = None) -> None:
        self.sql = " ".join(sql.split())

    def fetchall(self) -> list[dict[str, object]]:
        return self.rows


class CollisionCursor:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows
        self.commands: list[tuple[str, object]] = []
        self.rowcount = 0

    def execute(self, sql: str, args: object = None) -> None:
        normalized = " ".join(sql.split())
        self.commands.append((normalized, args))
        self.rowcount = 0

    def fetchall(self) -> list[dict[str, object]]:
        return self.rows


class LockCursor:
    def __init__(self, outcomes: list[int]) -> None:
        self.outcomes = iter(outcomes)
        self.result: dict[str, int] | None = None
        self.commands: list[tuple[str, object]] = []

    def execute(self, sql: str, args: object = None) -> None:
        normalized = " ".join(sql.split())
        self.commands.append((normalized, args))
        if normalized.startswith("SELECT GET_LOCK"):
            self.result = {"acquired": next(self.outcomes)}
        else:
            self.result = None

    def fetchone(self) -> dict[str, int] | None:
        return self.result


class ImageCursor:
    def __init__(
        self,
        assets: list[dict[str, object]],
        qcs: list[dict[str, object]],
    ) -> None:
        self.assets = assets
        self.qcs = qcs
        self.rows: list[dict[str, object]] = []
        self.commands: list[tuple[str, object]] = []

    def execute(self, sql: str, args: object = None) -> None:
        normalized = " ".join(sql.split())
        self.commands.append((normalized, args))
        if normalized.startswith("SELECT id, variant_id, image_kind"):
            self.rows = self.assets
        elif normalized.startswith("SELECT id, image_asset_id, qc_version"):
            self.rows = self.qcs
        else:
            self.rows = []

    def fetchall(self) -> list[dict[str, object]]:
        return self.rows


class DryRunCursor:
    def __init__(self, connection: "DryRunConnection") -> None:
        self.connection = connection
        self.result: dict[str, object] | None = None
        self.rows: list[dict[str, object]] = []
        self.rowcount = 0

    def __enter__(self) -> "DryRunCursor":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, sql: str, _args: object = None) -> None:
        normalized = " ".join(sql.split())
        self.connection.commands.append(normalized)
        self.rowcount = 0
        self.rows = []
        if normalized.startswith("SELECT COUNT(*) AS n FROM market_source_observation"):
            self.result = {"n": 42}
        elif "FROM catalog_printing_identity" in normalized and "identity_status='review'" in normalized:
            self.result = {"n": 10}
        elif normalized.startswith("SELECT COUNT(*) AS n FROM market_identity_review_queue"):
            self.result = {"n": 216}
        elif normalized.startswith("SELECT canonical_variant_id FROM catalog_variant_alias"):
            self.result = None
        elif normalized.startswith("SELECT ") and " WHERE variant_id IN " in normalized:
            self.result = None
            self.rows = []
        else:
            self.result = None

    def fetchone(self) -> dict[str, object] | None:
        return self.result

    def fetchall(self) -> list[dict[str, object]]:
        return self.rows


class DryRunConnection:
    def __init__(self) -> None:
        self.commands: list[str] = []
        self.committed = False
        self.rolled_back = False

    def cursor(self) -> DryRunCursor:
        return DryRunCursor(self)

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True


def universe_member(
    variant_id: int,
    *,
    segment: str = "pokemon",
    role: str = "top100",
    rank: int | None = 1,
) -> dict[str, object]:
    return {
        "variant_id": variant_id,
        "segment_code": segment,
        "member_role": role,
        "market_rank": rank,
        "watch_position": None,
        "watch_score": None,
        "selection_signals_json": {"accepted": True},
    }


class MigrationTests(unittest.TestCase):
    def test_migration_is_schema_only_and_additive(self) -> None:
        document = (
            ROOT / "pipelines/migrations/011_identity_convergence.mysql.sql"
        ).read_text(encoding="utf-8")
        self.assertIn("CREATE TABLE IF NOT EXISTS catalog_variant_alias", document)
        self.assertIn("CREATE TABLE IF NOT EXISTS market_identity_review_resolution", document)
        self.assertIn("VALUES ('011')", document)
        upper = document.upper()
        self.assertNotIn("DELETE FROM", upper)
        self.assertNotIn("UPDATE CATALOG_", upper)
        self.assertNotIn("UPDATE MARKET_", upper)


class LockTests(unittest.TestCase):
    def test_import_lock_is_acquired_before_convergence_lock(self) -> None:
        cursor = LockCursor([1, 1, 1])
        acquired = convergence.acquire_locks(cursor)
        self.assertEqual(
            acquired,
            [
                convergence.IMPORT_ADVISORY_LOCK,
                convergence.ALERT_ADVISORY_LOCK,
                convergence.CONVERGENCE_ADVISORY_LOCK,
            ],
        )
        lock_args = [
            args
            for sql, args in cursor.commands
            if sql.startswith("SELECT GET_LOCK")
        ]
        self.assertEqual(
            lock_args,
            [
                (convergence.IMPORT_ADVISORY_LOCK,),
                (convergence.ALERT_ADVISORY_LOCK,),
                (convergence.CONVERGENCE_ADVISORY_LOCK,),
            ],
        )

    def test_partial_lock_acquisition_releases_import_lock(self) -> None:
        cursor = LockCursor([1, 0])
        with self.assertRaisesRegex(RuntimeError, "writer lock is busy"):
            convergence.acquire_locks(cursor)
        self.assertTrue(
            any(
                sql.startswith("SELECT RELEASE_LOCK")
                and args == (convergence.IMPORT_ADVISORY_LOCK,)
                for sql, args in cursor.commands
            )
        )


class PairSelectionTests(unittest.TestCase):
    def test_only_explicit_duplicate_to_canonical_pairs_are_selected(self) -> None:
        cursor = PairCursor(
            [
                {
                    "duplicate_variant_id": 20,
                    "canonical_variant_id": 10,
                    "evidence_sha256": "a" * 64,
                }
            ]
        )
        pairs = convergence.load_confirmed_pairs(cursor)
        self.assertEqual(
            pairs,
            [convergence.DuplicatePair(20, 10, "a" * 64)],
        )
        self.assertIn("duplicate_identity.identity_status='duplicate'", cursor.sql)
        self.assertIn("canonical_identity.identity_status='canonical'", cursor.sql)
        self.assertNotIn("identity_status='review'", cursor.sql)

    def test_ambiguous_canonical_is_rejected(self) -> None:
        cursor = PairCursor(
            [
                {
                    "duplicate_variant_id": 20,
                    "canonical_variant_id": 10,
                    "evidence_sha256": "a" * 64,
                },
                {
                    "duplicate_variant_id": 20,
                    "canonical_variant_id": 11,
                    "evidence_sha256": "a" * 64,
                },
            ]
        )
        with self.assertRaisesRegex(RuntimeError, "ambiguous canonicals"):
            convergence.load_confirmed_pairs(cursor)


class CollisionMergeTests(unittest.TestCase):
    def test_latest_effective_at_then_highest_id_wins(self) -> None:
        old = datetime(2026, 7, 27, 1, 0)
        new = datetime(2026, 7, 27, 2, 0)
        cursor = CollisionCursor(
            [
                {
                    "id": 100,
                    "variant_id": 10,
                    "source_code": "snk",
                    "observed_date": "2026-07-27",
                    "effective_at": old,
                },
                {
                    "id": 200,
                    "variant_id": 20,
                    "source_code": "snk",
                    "observed_date": "2026-07-27",
                    "effective_at": new,
                },
            ]
        )
        result = convergence.merge_collision_table(
            cursor,
            convergence.CollisionTable(
                "market_price_observation",
                ("source_code", "observed_date"),
                ("effective_at", "id"),
            ),
            20,
            10,
        )
        self.assertEqual(result, {"moved": 1, "deletedCollisions": 1})
        self.assertTrue(
            any(
                sql == "DELETE FROM market_price_observation WHERE id IN (%s)"
                and args == (100,)
                for sql, args in cursor.commands
            )
        )
        self.assertTrue(
            any(
                sql == "UPDATE market_price_observation SET variant_id=%s WHERE id=%s"
                and args == (10, 200)
                for sql, args in cursor.commands
            )
        )

    def test_highest_id_breaks_same_effective_at_tie(self) -> None:
        moment = datetime(2026, 7, 27, 2, 0)
        cursor = CollisionCursor(
            [
                {
                    "id": 200,
                    "variant_id": 10,
                    "source_code": "snk",
                    "observed_date": "2026-07-27",
                    "effective_at": moment,
                },
                {
                    "id": 201,
                    "variant_id": 20,
                    "source_code": "snk",
                    "observed_date": "2026-07-27",
                    "effective_at": moment,
                },
            ]
        )
        convergence.merge_collision_table(
            cursor,
            convergence.CollisionTable(
                "market_price_observation",
                ("source_code", "observed_date"),
                ("effective_at", "id"),
            ),
            20,
            10,
        )
        deleted = next(args for sql, args in cursor.commands if sql.startswith("DELETE FROM"))
        self.assertEqual(deleted, (200,))


class PresentationMergeTests(unittest.TestCase):
    def test_all_variant_owned_presentation_refs_have_a_convergence_path(self) -> None:
        keyed = {spec.name for spec in convergence.KEYED_TABLES}
        self.assertTrue(
            {
                "catalog_variant_locale",
                "catalog_story_pointer",
                "market_image_source_pointer",
            }
            <= keyed
        )
        simple = set(convergence.SIMPLE_VARIANT_REFS)
        self.assertIn(
            ("market_identity_review_queue", "resolved_variant_id"),
            simple,
        )
        self.assertIn(
            ("market_identity_review_resolution", "resolved_variant_id"),
            simple,
        )
        self.assertTrue(callable(convergence.merge_image_assets))

    def test_locale_collision_keeps_latest_row_and_moves_it_to_canonical(self) -> None:
        cursor = CollisionCursor(
            [
                {
                    "variant_id": 10,
                    "locale_code": "en",
                    "updated_at": datetime(2026, 7, 27, 1, 0),
                },
                {
                    "variant_id": 20,
                    "locale_code": "en",
                    "updated_at": datetime(2026, 7, 27, 2, 0),
                },
            ]
        )
        result = convergence.merge_keyed_table(
            cursor,
            convergence.KeyedTable(
                "catalog_variant_locale",
                ("locale_code",),
                "updated_at",
            ),
            20,
            10,
        )
        self.assertEqual(result, {"moved": 1, "deletedCollisions": 1})
        self.assertTrue(
            any(
                sql.startswith("DELETE FROM catalog_variant_locale")
                and args == (10, "en")
                for sql, args in cursor.commands
            )
        )
        self.assertTrue(
            any(
                sql.startswith("UPDATE catalog_variant_locale SET variant_id=%s")
                and args[0] == 10
                for sql, args in cursor.commands
            )
        )

    def test_asset_collision_repoints_qc_before_deleting_loser_asset(self) -> None:
        cursor = ImageCursor(
            assets=[
                {
                    "id": 100,
                    "variant_id": 10,
                    "image_kind": "raw_front",
                    "content_sha256": "a" * 64,
                    "captured_at": datetime(2026, 7, 27, 1, 0),
                },
                {
                    "id": 200,
                    "variant_id": 20,
                    "image_kind": "raw_front",
                    "content_sha256": "a" * 64,
                    "captured_at": datetime(2026, 7, 27, 2, 0),
                },
            ],
            qcs=[
                {
                    "id": 10,
                    "image_asset_id": 100,
                    "qc_version": "v1",
                    "checked_at": datetime(2026, 7, 27, 4, 0),
                },
                {
                    "id": 20,
                    "image_asset_id": 200,
                    "qc_version": "v1",
                    "checked_at": datetime(2026, 7, 27, 3, 0),
                },
            ],
        )
        result = convergence.merge_image_assets(cursor, 20, 10)
        self.assertEqual(
            result,
            {
                "moved": 1,
                "deletedCollisions": 1,
                "qcMoved": 1,
                "qcDeletedCollisions": 1,
            },
        )
        commands = [sql for sql, _args in cursor.commands]
        qc_update = commands.index(
            "UPDATE market_image_qc SET image_asset_id=%s WHERE id=%s"
        )
        asset_delete = commands.index("DELETE FROM market_image_asset WHERE id=%s")
        self.assertLess(qc_update, asset_delete)


class UniverseTests(unittest.TestCase):
    def test_replacement_deduplicates_and_prefers_existing_canonical_membership(self) -> None:
        canonical = universe_member(10, rank=3)
        duplicate = universe_member(20, rank=7)
        rows, changes = convergence._resolved_universe_members(
            [duplicate, canonical],
            {20: 10},
        )
        self.assertEqual(changes, 1)
        self.assertEqual(rows, [canonical])

    def test_conflicting_roles_fail_closed(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "membership conflict"):
            convergence._resolved_universe_members(
                [
                    universe_member(10, role="top100"),
                    universe_member(20, role="watchlist"),
                ],
                {20: 10},
            )

    def test_formal_alias_membership_outranks_canonical_monitoring_membership(self) -> None:
        canonical_monitoring = universe_member(
            10,
            segment="pre-entry",
            role="monitoring",
            rank=None,
        )
        formal_alias = universe_member(
            20,
            segment="tracked",
            role="candidate",
            rank=7,
        )
        rows, changes = convergence._resolved_universe_members(
            [canonical_monitoring, formal_alias],
            {20: 10},
        )
        self.assertEqual(changes, 1)
        self.assertEqual(
            rows,
            [{**formal_alias, "variant_id": 10}],
        )

    def test_plan_hash_is_deterministic(self) -> None:
        pair = convergence.DuplicatePair(20, 10, "a" * 64)
        lock = {
            "id": 9,
            "lock_sha256": "b" * 64,
            "member_count": 2,
        }
        members = [universe_member(10), universe_member(20)]
        first = convergence.build_plan([pair], lock, members)
        second = convergence.build_plan([pair], lock, members)
        self.assertEqual(convergence.sha256_json(first), convergence.sha256_json(second))

    def test_affected_table_state_changes_plan_hash(self) -> None:
        pair = convergence.DuplicatePair(20, 10, "a" * 64)
        first_state = convergence.table_state_entry(
            [
                {
                    "id": 200,
                    "variant_id": 20,
                    "effective_at": datetime(2026, 7, 27, 1, 0),
                }
            ],
            id_column="id",
        )
        second_state = convergence.table_state_entry(
            [
                {
                    "id": 200,
                    "variant_id": 20,
                    "effective_at": datetime(2026, 7, 27, 2, 0),
                }
            ],
            id_column="id",
        )
        self.assertEqual(first_state["rows"], second_state["rows"])
        self.assertEqual(first_state["maxId"], second_state["maxId"])
        self.assertNotEqual(
            first_state["operationSha256"],
            second_state["operationSha256"],
        )
        first = convergence.build_plan(
            [pair],
            None,
            [],
            {"market_price_observation": first_state},
        )
        second = convergence.build_plan(
            [pair],
            None,
            [],
            {"market_price_observation": second_state},
        )
        self.assertNotEqual(convergence.sha256_json(first), convergence.sha256_json(second))


class RawEvidenceBoundaryTests(unittest.TestCase):
    def test_convergence_never_writes_raw_or_review_tables(self) -> None:
        source = (ROOT / "pipelines/identity_convergence.py").read_text(encoding="utf-8")
        upper = source.upper()
        self.assertNotIn("UPDATE MARKET_SOURCE_OBSERVATION", upper)
        self.assertNotIn("DELETE FROM MARKET_SOURCE_OBSERVATION", upper)
        self.assertNotIn("DELETE FROM MARKET_IDENTITY_REVIEW_QUEUE", upper)
        self.assertIn(
            '("MARKET_IDENTITY_REVIEW_QUEUE", "RESOLVED_VARIANT_ID")',
            upper,
        )

    def test_historical_derived_rows_are_explicitly_immutable(self) -> None:
        source = (ROOT / "pipelines/identity_convergence.py").read_text(encoding="utf-8")
        upper = source.upper()
        for table in convergence.IMMUTABLE_HISTORICAL_VARIANT_TABLES:
            self.assertNotIn(f"UPDATE {table.upper()}", upper)
            self.assertNotIn(f"DELETE FROM {table.upper()}", upper)

    def test_fixture_dry_run_executes_plan_then_rolls_back(self) -> None:
        connection = DryRunConnection()
        result = convergence.apply_plan(
            connection,
            [convergence.DuplicatePair(20, 10, "a" * 64)],
            None,
            [],
            "b" * 64,
            commit=False,
        )
        self.assertTrue(connection.rolled_back)
        self.assertFalse(connection.committed)
        self.assertFalse(result["committed"])
        self.assertEqual(
            result["guard"],
            {
                "rawSourceObservationRows": 42,
                "printingReviewRows": 10,
                "reviewQueueRows": 216,
            },
        )
        self.assertTrue(
            any(sql.startswith("INSERT INTO catalog_variant_alias") for sql in connection.commands)
        )


if __name__ == "__main__":
    unittest.main()
