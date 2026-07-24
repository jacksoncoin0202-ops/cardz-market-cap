from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import db_runtime  # noqa: E402


def formal_card() -> dict[str, object]:
    return {
        "pokedexId": "formal-1",
        "pokedexStatus": "confirmed",
        "canonicalSourceCode": "fixture",
        "canonicalExternalId": "formal-1",
        "gemrateId": "gem-formal-1",
        "tcg": "pokemon",
        "language": "ja",
        "name": "Formal Card",
        "setName": "Fixture Set",
        "collectorNumber": "001/TEST",
        "populationPsa10": 1000,
        "populationSourceState": "gemrate_direct",
        "rankMemberships": {"tcg": 1, "pokemon": 1},
    }


def monitoring_card() -> dict[str, object]:
    return {
        "pokedexId": "monitor-1",
        "pokedexStatus": "confirmed",
        "canonicalSourceCode": "fixture",
        "canonicalExternalId": "monitor-1",
        "gemrateId": "gem-monitor-1",
        "tcg": "pokemon",
        "language": "ja",
        "name": "Pre-entry Card",
        "setName": "Fixture Set",
        "collectorNumber": "002/TEST",
        "populationPsa10": 999,
        "populationSourceState": "gemrate_public_card_page",
        "monitoringState": "pre_entry_population_971_999",
        "collectionCadence": "daily",
        "reasons": ["population_971_999"],
    }


def schema5_document() -> dict[str, object]:
    cards = [formal_card()]
    monitoring = [monitoring_card()]
    return {
        "schemaVersion": "5.0.0",
        "effectiveAt": "2026-07-24T00:00:00Z",
        "policy": {
            "indexes": ["tcg", "pokemon", "one-piece"],
            "canonicalMembership": "complete_eligible",
            "languagePartitioning": False,
            "monitoringPopulationRangeInclusive": {"minimum": 971, "maximum": 999},
        },
        "cards": cards,
        "monitoringCandidates": monitoring,
        "payloadSha256": db_runtime.sha256(db_runtime.canonical_json(cards)),
        "monitoringPayloadSha256": db_runtime.sha256(db_runtime.canonical_json(monitoring)),
        "collectionPayloadSha256": db_runtime.sha256(
            db_runtime.canonical_json({"cards": cards, "monitoringCandidates": monitoring})
        ),
    }


class Cursor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.lastrowid = 1

    def __enter__(self) -> "Cursor":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, query: str, args: object = None) -> None:
        self.calls.append((" ".join(query.split()), args))

    def fetchone(self) -> dict[str, int]:
        if self.calls[-1][0].startswith("SELECT GET_LOCK"):
            return {"acquired": 1}
        if "FROM catalog_variant" in self.calls[-1][0] or "FROM catalog_source_identity" in self.calls[-1][0]:
            return None  # type: ignore[return-value]
        return {"id": 1}


class Connection:
    def __init__(self) -> None:
        self.cursor_value = Cursor()
        self.commits = 0
        self.rollbacks = 0

    def cursor(self) -> Cursor:
        return self.cursor_value

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


class IdentityCursor(Cursor):
    def __init__(
        self,
        *,
        variant: dict[str, object] | None,
        source: dict[str, int] | None,
        sources: dict[tuple[str, str], dict[str, int] | None] | None = None,
    ) -> None:
        super().__init__()
        self.variant = variant
        self.source = source
        self.sources = sources

    def fetchone(self) -> dict[str, object] | None:
        query = self.calls[-1][0]
        if "FROM catalog_variant" in query:
            return self.variant
        if "FROM catalog_source_identity" in query:
            if self.sources is not None:
                source_code, external_id = self.calls[-1][1]  # type: ignore[misc]
                return self.sources.get((str(source_code), str(external_id)))
            return self.source
        return {"id": self.lastrowid}


class DbRuntimeSchema5Tests(unittest.TestCase):
    def test_schema5_validates_formal_and_pre_entry_but_returns_collection_union(self) -> None:
        document = schema5_document()

        collection = db_runtime.validate_active_universe(document)

        self.assertEqual([row["pokedexId"] for row in collection], ["formal-1", "monitor-1"])
        self.assertNotIn("rankMemberships", collection[1])

    def test_schema5_rejects_population_or_membership_drift(self) -> None:
        document = schema5_document()
        document["cards"][0]["populationPsa10"] = 999  # type: ignore[index]
        document["payloadSha256"] = db_runtime.sha256(db_runtime.canonical_json(document["cards"]))
        with self.assertRaisesRegex(ValueError, "GemRate POP >= 1000"):
            db_runtime.validate_active_universe(document)

        document = schema5_document()
        document["monitoringCandidates"][0]["rankMemberships"] = {"tcg": 2}  # type: ignore[index]
        document["monitoringPayloadSha256"] = db_runtime.sha256(
            db_runtime.canonical_json(document["monitoringCandidates"])
        )
        with self.assertRaisesRegex(ValueError, "must not have formal rank membership"):
            db_runtime.validate_active_universe(document)

    def test_schema5_rejects_estimated_out_of_band_or_incomplete_monitoring_identity(self) -> None:
        document = schema5_document()
        document["monitoringCandidates"][0]["populationEstimated"] = True  # type: ignore[index]
        document["monitoringPayloadSha256"] = db_runtime.sha256(
            db_runtime.canonical_json(document["monitoringCandidates"])
        )
        with self.assertRaisesRegex(ValueError, "exact GemRate POP 971-999"):
            db_runtime.validate_active_universe(document)

        document = schema5_document()
        document["monitoringCandidates"][0].pop("setName")  # type: ignore[index]
        document["monitoringPayloadSha256"] = db_runtime.sha256(
            db_runtime.canonical_json(document["monitoringCandidates"])
        )
        with self.assertRaisesRegex(ValueError, "is incomplete"):
            db_runtime.validate_active_universe(document)

    def test_schema5_lock_hash_tracks_monitoring_separately_from_formal_ranking_hash(self) -> None:
        first = schema5_document()
        second = schema5_document()
        second["monitoringCandidates"][0]["reasons"] = ["population_971_999", "price_accelerating"]  # type: ignore[index]
        second["monitoringPayloadSha256"] = db_runtime.sha256(
            db_runtime.canonical_json(second["monitoringCandidates"])
        )
        second["collectionPayloadSha256"] = db_runtime.sha256(
            db_runtime.canonical_json(
                {"cards": second["cards"], "monitoringCandidates": second["monitoringCandidates"]}
            )
        )

        self.assertEqual(first["payloadSha256"], second["payloadSha256"])
        self.assertNotEqual(db_runtime.active_universe_lock_hash(first), db_runtime.active_universe_lock_hash(second))

    def test_schema5_rejects_collection_hash_drift(self) -> None:
        document = schema5_document()
        document["monitoringCandidates"][0]["reasons"] = ["population_971_999", "price_accelerating"]  # type: ignore[index]
        document["monitoringPayloadSha256"] = db_runtime.sha256(
            db_runtime.canonical_json(document["monitoringCandidates"])
        )
        with self.assertRaisesRegex(ValueError, "collection payload hash mismatch"):
            db_runtime.active_universe_lock_hash(document)

    def test_schema5_import_persists_monitoring_identity_without_rank_membership(self) -> None:
        connection = Connection()
        mapping, _ = db_runtime.import_lock(connection, schema5_document())

        self.assertEqual(set(mapping), {("fixture", "formal-1"), ("fixture", "monitor-1")})
        catalog_upserts = [query for query, _ in connection.cursor_value.calls if "INSERT INTO catalog_variant" in query]
        self.assertEqual(len(catalog_upserts), 2)
        member_arguments = [
            args for query, args in connection.cursor_value.calls
            if "INSERT INTO market_universe_member" in query
        ]
        self.assertEqual(len(member_arguments), 2)
        self.assertEqual(member_arguments[0][2:5], ("tracked", "candidate", 1))  # type: ignore[index]
        self.assertEqual(member_arguments[1][2:5], ("pre-entry", "monitoring", None))  # type: ignore[index]
        self.assertEqual(connection.commits, 1)


class DbRuntimeIdentityContinuityTests(unittest.TestCase):
    def test_opaque_identity_is_locked_and_cannot_change(self) -> None:
        card = formal_card()
        cursor = IdentityCursor(
            variant={
                "id": 5,
                "tcg_code": "one-piece",
                "card_language": "ja",
                "set_name": "Fixture Set",
                "collector_number": "001/TEST",
            },
            source=None,
        )

        with self.assertRaisesRegex(ValueError, "opaque_id canonical identity changed"):
            db_runtime.upsert_variant(cursor, card)

        self.assertIn("FOR UPDATE", cursor.calls[0][0])
        self.assertFalse(any("catalog_source_identity" in query for query, _ in cursor.calls))

    def test_source_identity_is_locked_before_insert_and_cannot_change_owner(self) -> None:
        card = formal_card()
        cursor = IdentityCursor(
            variant={
                "id": 5,
                "tcg_code": "pokemon",
                "card_language": "ja",
                "set_name": "Fixture Set",
                "collector_number": "001/TEST",
            },
            source={"variant_id": 9},
        )

        with self.assertRaisesRegex(ValueError, "source identity ownership changed"):
            db_runtime.upsert_variant(cursor, card)

        source_queries = [query for query, _ in cursor.calls if "catalog_source_identity" in query]
        self.assertEqual(len(source_queries), 1)
        self.assertIn("FOR UPDATE", source_queries[0])
        self.assertNotIn("ON DUPLICATE KEY UPDATE", source_queries[0])

    def test_new_source_identity_is_locked_before_insert(self) -> None:
        cursor = IdentityCursor(variant=None, source=None)

        db_runtime.upsert_variant(cursor, formal_card())

        source_select = next(
            index
            for index, (query, _) in enumerate(cursor.calls)
            if "SELECT variant_id FROM catalog_source_identity" in query
        )
        source_insert = next(
            index
            for index, (query, _) in enumerate(cursor.calls)
            if "INSERT INTO catalog_source_identity" in query
        )
        self.assertLess(source_select, source_insert)
        self.assertIn("FOR UPDATE", cursor.calls[source_select][0])
        self.assertNotIn("ON DUPLICATE KEY UPDATE", cursor.calls[source_insert][0])

    def test_gemrate_identity_is_locked_separately_from_the_primary_source(self) -> None:
        card = formal_card()
        card["gemrateId"] = "a" * 40
        cursor = IdentityCursor(
            variant={
                "id": 5,
                "tcg_code": "pokemon",
                "card_language": "ja",
                "set_name": "Fixture Set",
                "collector_number": "001/TEST",
            },
            source={"variant_id": 9},
        )
        # The first source lookup is the primary fixture identity; allow it,
        # but reject the durable GemRate binding to another variant.
        source_results = iter(({"variant_id": 5}, {"variant_id": 9}))
        cursor.fetchone = lambda: next(source_results) if "catalog_source_identity" in cursor.calls[-1][0] else cursor.variant  # type: ignore[method-assign]

        with self.assertRaisesRegex(ValueError, "source identity ownership changed: gemrate"):
            db_runtime.upsert_variant(cursor, card)

    def test_canonical_name_may_update_without_reassigning_source_identity(self) -> None:
        card = formal_card()
        card["name"] = "Corrected Formal Card"
        cursor = IdentityCursor(
            variant={
                "id": 5,
                "tcg_code": "pokemon",
                "card_language": "ja",
                "set_name": "Fixture Set",
                "collector_number": "001/TEST",
            },
            source={"variant_id": 5},
        )

        self.assertEqual(db_runtime.upsert_variant(cursor, card), 5)

        catalog_update = next(args for query, args in cursor.calls if query.startswith("UPDATE catalog_variant"))
        self.assertEqual(catalog_update, ("Corrected Formal Card", 5))
        source_update = next(query for query, _ in cursor.calls if query.startswith("UPDATE catalog_source_identity"))
        self.assertNotIn("variant_id", source_update)

    def test_exact_gemrate_id_is_bound_alongside_non_gemrate_canonical_identity(self) -> None:
        card = formal_card()
        card["canonicalSourceCode"] = "snkrdunk"
        card["canonicalExternalId"] = "123"
        card["gemrateId"] = "a" * 40
        cursor = IdentityCursor(variant=None, source=None)

        self.assertEqual(db_runtime.upsert_variant(cursor, card), 1)

        inserts = [
            args for query, args in cursor.calls
            if "INSERT INTO catalog_source_identity" in query
        ]
        self.assertEqual([(args[0], args[1]) for args in inserts], [("snkrdunk", "123"), ("gemrate", "a" * 40)])

    def test_exact_snk_item_is_bound_alongside_gemrate_canonical_identity(self) -> None:
        card = formal_card()
        card["canonicalSourceCode"] = "gemrate_direct"
        card["canonicalExternalId"] = "a" * 40
        card["gemrateId"] = "a" * 40
        card["snkItemId"] = 456
        cursor = IdentityCursor(variant=None, source=None)

        self.assertEqual(db_runtime.upsert_variant(cursor, card), 1)

        inserts = [
            args for query, args in cursor.calls
            if "INSERT INTO catalog_source_identity" in query
        ]
        self.assertEqual(
            [(args[0], args[1]) for args in inserts],
            [("gemrate_direct", "a" * 40), ("gemrate", "a" * 40), ("snkrdunk", "456")],
        )

    def test_exact_gemrate_id_owner_mismatch_fails_without_rebinding(self) -> None:
        card = formal_card()
        card["canonicalSourceCode"] = "snkrdunk"
        card["canonicalExternalId"] = "123"
        card["gemrateId"] = "b" * 40
        cursor = IdentityCursor(
            variant={
                "id": 5,
                "tcg_code": "pokemon",
                "card_language": "ja",
                "set_name": "Fixture Set",
                "collector_number": "001/TEST",
            },
            source=None,
            sources={
                ("snkrdunk", "123"): {"variant_id": 5},
                ("gemrate", "b" * 40): {"variant_id": 9},
            },
        )

        with self.assertRaisesRegex(ValueError, "source identity ownership changed: gemrate"):
            db_runtime.upsert_variant(cursor, card)

        gemrate_updates = [
            query for query, args in cursor.calls
            if "catalog_source_identity" in query and args and args[0] == "gemrate"
        ]
        self.assertEqual(len(gemrate_updates), 1)

    def test_noncanonical_or_malformed_gemrate_id_is_not_persisted_as_identity(self) -> None:
        cursor = IdentityCursor(variant=None, source=None)

        db_runtime.upsert_variant(cursor, formal_card())

        source_inserts = [args for query, args in cursor.calls if "INSERT INTO catalog_source_identity" in query]
        self.assertEqual([(args[0], args[1]) for args in source_inserts], [("fixture", "formal-1")])


class DbRuntimeTransactionTests(unittest.TestCase):
    def test_import_all_commits_once_after_lock_batches_and_promotion(self) -> None:
        connection = Connection()
        document = schema5_document()
        variants = {("fixture", "formal-1"): 1, ("fixture", "monitor-1"): 2}
        batches = [(Path("one.json"), {"runId": "one"})]
        with (
            patch.object(db_runtime, "read_json", return_value=document),
            patch.object(db_runtime, "import_lock", return_value=(variants, 41)) as import_lock,
            patch.object(db_runtime, "iter_batches", return_value=batches),
            patch.object(db_runtime, "import_batch", return_value={"inserted": 3, "replayed": False}) as import_batch,
            patch.object(db_runtime, "promote_lock") as promote_lock,
        ):
            result = db_runtime.import_all(connection, Path("active.json"), Path("landing"))

        self.assertEqual(result, {"active": 2, "batches": 1, "replayedBatches": 0, "observations": 3})
        import_lock.assert_called_once_with(connection, document, commit=False)
        import_batch.assert_called_once_with(connection, batches[0][0], batches[0][1], variants, document["collectionPayloadSha256"], commit=False)
        promote_lock.assert_called_once_with(connection, 41, 2, commit=False)
        self.assertEqual(connection.commits, 1)
        self.assertEqual(connection.rollbacks, 0)
        self.assertTrue(any("RELEASE_LOCK" in query for query, _ in connection.cursor_value.calls))


class AliasCursor(Cursor):
    def __init__(self, anchors: dict[str, int]) -> None:
        super().__init__()
        self.anchors = anchors
        self.aliases: dict[tuple[str, str, str, str, str], int] = {}

    def execute(self, query: str, args: object = None) -> None:
        super().execute(query, args)
        normalized = self.calls[-1][0]
        values = tuple(args or ())  # type: ignore[arg-type]
        if normalized.startswith("INSERT INTO catalog_provider_identity_alias"):
            self.aliases[tuple(str(value) for value in values[:5])] = int(values[5])

    def fetchone(self) -> dict[str, int] | None:
        query, args = self.calls[-1]
        values = tuple(args or ())  # type: ignore[arg-type]
        if "FROM catalog_source_identity" in query:
            variant_id = self.anchors.get(str(values[0]))
            return {"variant_id": variant_id} if variant_id is not None else None
        if "FROM catalog_provider_identity_alias" in query:
            variant_id = self.aliases.get(tuple(str(value) for value in values))
            return {"variant_id": variant_id} if variant_id is not None else None
        return super().fetchone()

    def fetchall(self) -> list[dict[str, int]]:
        query, args = self.calls[-1]
        values = tuple(str(value) for value in tuple(args or ()))  # type: ignore[arg-type]
        if "SELECT DISTINCT variant_id" in query:
            return [
                {"variant_id": variant_id}
                for key, variant_id in self.aliases.items()
                if key[:4] == values
            ]
        return []


class AliasConnection(Connection):
    def __init__(self, anchors: dict[str, int]) -> None:
        super().__init__()
        self.cursor_value = AliasCursor(anchors)


class DbRuntimeGemRateAliasTests(unittest.TestCase):
    def alias_document(
        self,
        *,
        requested: str = "a" * 40,
        universal: str = "b" * 40,
        canonical_key: str = "one-piece|romance dawn|op01-120|ja|standard|manga|foil",
    ) -> dict[str, object]:
        common = {
            "receiptPayloadSha256": "c" * 64,
            "requestedGemrateId": requested,
            "canonicalPrintingKey": canonical_key,
            "sourcePointer": "population.json",
            "fetchedAt": "2026-07-24T00:00:00Z",
            "status": "exact_confirmed",
        }
        return {
            "schemaVersion": 2,
            "cards": [],
            "gemrateAliases": [
                {**common, "aliasType": "entity", "aliasValue": requested, "grader": None},
                {**common, "aliasType": "universal", "aliasValue": universal, "grader": None},
                {**common, "aliasType": "grader_member", "aliasValue": "d" * 40, "grader": "psa"},
                {**common, "aliasType": "spec", "aliasValue": "123", "grader": "psa"},
            ],
        }

    def test_aliases_insert_once_and_replay_without_new_rows(self) -> None:
        connection = AliasConnection({"a" * 40: 7})
        document = self.alias_document()

        first = db_runtime.import_gemrate_identity_aliases(connection, document)
        second = db_runtime.import_gemrate_identity_aliases(connection, document)

        self.assertEqual(first, {"inserted": 4, "replayed": 0, "unanchored": 0})
        self.assertEqual(second, {"inserted": 0, "replayed": 4, "unanchored": 0})
        self.assertEqual(len(connection.cursor_value.aliases), 4)

    def test_same_universal_can_link_multiple_members_only_for_one_variant(self) -> None:
        universal = "b" * 40
        first = self.alias_document(requested="a" * 40, universal=universal)
        second = self.alias_document(requested="e" * 40, universal=universal)
        second["gemrateAliases"] = [  # type: ignore[index]
            {
                **second["gemrateAliases"][1],  # type: ignore[index]
                "aliasValue": universal,
            }
        ]
        connection = AliasConnection({"a" * 40: 7, "e" * 40: 7})
        db_runtime.import_gemrate_identity_aliases(connection, first)
        self.assertEqual(
            db_runtime.import_gemrate_identity_aliases(connection, second),
            {"inserted": 1, "replayed": 0, "unanchored": 0},
        )

        conflict = AliasConnection({"a" * 40: 7, "e" * 40: 9})
        db_runtime.import_gemrate_identity_aliases(conflict, first)
        with self.assertRaisesRegex(ValueError, "provider alias ownership changed"):
            db_runtime.import_gemrate_identity_aliases(conflict, second)

    def test_unanchored_alias_is_retained_outside_db_without_promotion(self) -> None:
        connection = AliasConnection({})
        self.assertEqual(
            db_runtime.import_gemrate_identity_aliases(connection, self.alias_document()),
            {"inserted": 0, "replayed": 0, "unanchored": 4},
        )
        self.assertEqual(connection.cursor_value.aliases, {})

    def test_import_all_rolls_back_everything_and_releases_lock_on_batch_failure(self) -> None:
        connection = Connection()
        document = schema5_document()
        variants = {("fixture", "formal-1"): 1, ("fixture", "monitor-1"): 2}
        batches = [(Path("bad.json"), {"runId": "bad"})]
        with (
            patch.object(db_runtime, "read_json", return_value=document),
            patch.object(db_runtime, "import_lock", return_value=(variants, 41)) as import_lock,
            patch.object(db_runtime, "iter_batches", return_value=batches),
            patch.object(db_runtime, "import_batch", side_effect=RuntimeError("bad batch")) as import_batch,
            patch.object(db_runtime, "promote_lock") as promote_lock,
        ):
            with self.assertRaisesRegex(RuntimeError, "bad batch"):
                db_runtime.import_all(connection, Path("active.json"), Path("landing"))

        import_lock.assert_called_once_with(connection, document, commit=False)
        import_batch.assert_called_once_with(connection, batches[0][0], batches[0][1], variants, document["collectionPayloadSha256"], commit=False)
        promote_lock.assert_not_called()
        self.assertEqual(connection.commits, 0)
        self.assertEqual(connection.rollbacks, 1)
        self.assertTrue(any("RELEASE_LOCK" in query for query, _ in connection.cursor_value.calls))


if __name__ == "__main__":
    unittest.main()
