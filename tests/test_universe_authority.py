from __future__ import annotations

import copy
import hashlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import db_runtime  # noqa: E402
import universe_authority  # noqa: E402


BACKEND_SPEC = importlib.util.spec_from_file_location(
    "cardz_backend_universe_tests", ROOT / "scripts/backend.py"
)
assert BACKEND_SPEC and BACKEND_SPEC.loader
backend = importlib.util.module_from_spec(BACKEND_SPEC)
BACKEND_SPEC.loader.exec_module(backend)


def printing_identity(
    tcg: str,
    card_language: str,
    set_name: str,
    collector_number: str,
    edition: str,
    parallel: str,
    finish: str,
) -> dict[str, object]:
    key = tuple(
        part.strip().casefold()
        for part in (
            tcg,
            card_language,
            set_name,
            collector_number,
            edition,
            parallel,
            finish,
        )
    )
    return {
        "status": "canonical",
        "cardLanguage": card_language,
        "setName": set_name,
        "collectorNumber": collector_number,
        "editionCode": edition,
        "parallelCode": parallel,
        "finishCode": finish,
        "canonicalPrintingSha256": hashlib.sha256(
            "|".join(key).encode("utf-8")
        ).hexdigest(),
    }


def population_printing_fields(
    tcg: str,
    set_name: str,
    collector_number: str,
    card_language: str = "ja",
    edition: str = "standard",
    parallel: str = "normal",
    finish: str = "foil",
) -> dict[str, object]:
    identity = printing_identity(
        tcg,
        card_language,
        set_name,
        collector_number,
        edition,
        parallel,
        finish,
    )
    return {
        "card_language": card_language,
        "printing_tcg_code": tcg,
        "printing_card_language": card_language,
        "printing_set_name": set_name,
        "printing_collector_number": collector_number,
        "edition_code": edition,
        "parallel_code": parallel,
        "finish_code": finish,
        "canonical_printing_sha256": identity["canonicalPrintingSha256"],
        "printing_identity_status": "canonical",
        "printing_hash_count": 1,
        "printing_tuple_count": 1,
    }


def candidate_document() -> dict[str, object]:
    formal = {
        "pokedexId": "cmc_formal",
        "canonicalVariantId": 10,
        "canonicalSourceCode": "gemrate",
        "canonicalExternalId": "a" * 40,
        "gemrateId": "a" * 40,
        "pokedexStatus": "confirmed",
        "tcg": "pokemon",
        "name": "Pikachu",
        "setName": "Promo",
        "collectorNumber": "001",
        "populationPsa10": 1001,
        "populationAsOf": "2026-07-29T00:00:00.000000Z",
        "populationObservedDate": "2026-07-29",
        "populationPayloadSha256": "1" * 64,
        "populationSourceState": "gemrate_exact",
        "populationEstimated": False,
        "printingIdentity": printing_identity(
            "pokemon", "ja", "Promo", "001", "standard", "normal", "holo"
        ),
        "rankMemberships": {},
    }
    monitoring = {
        "pokedexId": "cmc_monitor",
        "canonicalVariantId": 20,
        "canonicalSourceCode": "gemrate",
        "canonicalExternalId": "b" * 40,
        "gemrateId": "b" * 40,
        "pokedexStatus": "confirmed",
        "tcg": "one-piece",
        "name": "Luffy",
        "setName": "OP01",
        "collectorNumber": "OP01-001",
        "populationPsa10": 980,
        "populationAsOf": "2026-07-29T00:00:00.000000Z",
        "populationObservedDate": "2026-07-29",
        "populationPayloadSha256": "2" * 64,
        "populationSourceState": "gemrate_exact",
        "populationEstimated": False,
        "printingIdentity": printing_identity(
            "one-piece", "ja", "OP01", "OP01-001", "standard", "normal", "foil"
        ),
        "rankMemberships": {},
        "monitoringState": "pre_entry_population_971_999",
        "collectionCadence": "daily",
        "reasons": ["population_971_999"],
    }
    cards = [formal]
    monitoring_cards = [monitoring]
    document: dict[str, object] = {
        "schemaVersion": "5.0.0",
        "generatedAt": "2026-07-29T00:00:00.000000Z",
        "effectiveAt": "2026-07-29T00:00:00.000000Z",
        "authority": {
            "database": "cardz_market_cap",
            "source": "canonical_mysql",
        },
        "policy": {
            "indexes": ["tcg", "pokemon", "one-piece"],
            "canonicalMembership": "complete_eligible",
            "languagePartitioning": False,
            "requireExactPopulation": True,
            "populationAuthority": "gemrate",
            "populationMinimumInclusive": 1000,
            "monitoringPopulationRangeInclusive": {
                "minimum": 971,
                "maximum": 999,
            },
            "selection": "latest_exact_psa10_per_canonical_variant",
            "requireCanonicalPrintingIdentity": True,
            "printingIdentityUniqueness": "canonical_sha256_and_full_tuple",
        },
        "cards": cards,
        "monitoringCandidates": monitoring_cards,
        "counts": {
            "qualified": 1,
            "monitoring": 1,
            "collection": 2,
            "discoveryEvidence": 0,
        },
    }
    document["payloadSha256"] = db_runtime.sha256(
        db_runtime.canonical_json(cards)
    )
    document["monitoringPayloadSha256"] = db_runtime.sha256(
        db_runtime.canonical_json(monitoring_cards)
    )
    document["collectionPayloadSha256"] = db_runtime.sha256(
        db_runtime.canonical_json(
            {"cards": cards, "monitoringCandidates": monitoring_cards}
        )
    )
    return document


def rehash_candidate(document: dict[str, object]) -> None:
    cards = document["cards"]
    monitoring = document["monitoringCandidates"]
    document["payloadSha256"] = db_runtime.sha256(
        db_runtime.canonical_json(cards)
    )
    document["monitoringPayloadSha256"] = db_runtime.sha256(
        db_runtime.canonical_json(monitoring)
    )
    document["collectionPayloadSha256"] = db_runtime.sha256(
        db_runtime.canonical_json(
            {"cards": cards, "monitoringCandidates": monitoring}
        )
    )


def write_generation_fixture(
    root: Path,
    *,
    generated_at: str = "2026-07-29T00:30:00Z",
    receipt_overrides: dict[str, object] | None = None,
) -> tuple[dict[str, object], dict[str, object]]:
    generation_id = "daily_20260729T003000Z"
    base_hash = "a" * 64
    derivative_200_hash = "b" * 64
    derivative_600_hash = "c" * 64
    db_qc = {
        "runId": "daily_20260729_fixture",
        "receiptSha256": "1" * 64,
        "database": "cardz_market_cap",
        "universeCandidateSha256": "2" * 64,
    }
    card_media = {
        "base": {
            "key": f"market-assets/{base_hash}.webp",
            "sha256": base_hash,
            "width": 429,
            "height": 600,
        },
        "200": {
            "key": f"market-assets/{base_hash}_200.webp",
            "sha256": derivative_200_hash,
            "width": 200,
            "height": 280,
        },
        "600": {
            "key": f"market-assets/{base_hash}_600.webp",
            "sha256": derivative_600_hash,
            "width": 429,
            "height": 600,
        },
    }
    media_assets = [
        {
            **entry,
            "variant": variant,
            "baseSha256": base_hash,
        }
        for variant, entry in card_media.items()
    ]
    snapshot: dict[str, object] = {
        "schemaVersion": "2.0.0",
        "generation": {
            "id": generation_id,
            "generatedAt": generated_at,
            "effectiveAt": "2026-07-29T00:00:00Z",
            "contentSha256": "",
            "qcReceiptSha256": "",
            "dbQc": db_qc,
            "mode": "production",
            "productionEligible": True,
            "blockers": [],
        },
        "coverage": {
            "claim": "verified-top-n",
            "requestedCount": 100,
            "verifiedCount": 1,
        },
        "top100": [
            {
                "id": "cmc_fixture",
                "pricePsa10": {"value": 100},
                "marketCap": {"value": 100_000},
                "image": {
                    "sha256": base_hash,
                    "width": 429,
                    "height": 600,
                },
            }
        ],
        "watchlist": [],
    }
    receipt: dict[str, object] = {
        "schemaVersion": 1,
        "runId": db_qc["runId"],
        "generationId": generation_id,
        "dbQc": db_qc,
        "dbQcReceiptSha256": db_qc["receiptSha256"],
        "universeCandidateSha256": db_qc["universeCandidateSha256"],
        "snapshotContentSha256": hashlib.sha256(
            db_runtime.canonical_json(snapshot)
        ).hexdigest(),
        "checkedAt": generated_at,
        "status": "passed",
        "claim": "verified-top-n",
        "requestedCount": 100,
        "verifiedCount": 1,
        "cards": [
            {
                "id": "cmc_fixture",
                "imageSha256": base_hash,
                "decision": "passed",
                "evidenceSha256": "e" * 64,
                "media": card_media,
            }
        ],
        "media": {
            "prefix": "market-assets/",
            "assets": media_assets,
        },
        "blockers": [],
    }
    if receipt_overrides:
        receipt.update(receipt_overrides)
    receipt_bytes = (
        json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    receipt_hash = hashlib.sha256(receipt_bytes).hexdigest()
    snapshot["generation"]["qcReceiptSha256"] = receipt_hash  # type: ignore[index]
    snapshot["generation"]["contentSha256"] = hashlib.sha256(  # type: ignore[index]
        db_runtime.canonical_json(snapshot)
    ).hexdigest()
    generation_root = root / "generations" / generation_id
    generation_root.mkdir(parents=True)
    (generation_root / "snapshot.json").write_text(
        json.dumps(snapshot),
        encoding="utf-8",
    )
    (generation_root / "qc-receipt.json").write_bytes(receipt_bytes)
    pointer: dict[str, object] = {
        "schemaVersion": 1,
        "generationId": generation_id,
        "generatedAt": generated_at,
        "snapshotKey": f"generations/{generation_id}/snapshot.json",
        "sha256": snapshot["generation"]["contentSha256"],  # type: ignore[index]
        "qcReceiptKey": f"generations/{generation_id}/qc-receipt.json",
        "qcReceiptSha256": receipt_hash,
        "dbQc": db_qc,
        "media": {
            "prefix": "market-assets/",
            "hashes": [base_hash],
            "assets": [
                {
                    "key": f"market-assets/{base_hash}.webp",
                    "sha256": base_hash,
                    "width": 429,
                    "height": 600,
                    "variant": "base",
                    "baseSha256": base_hash,
                },
                {
                    "key": f"market-assets/{base_hash}_200.webp",
                    "sha256": derivative_200_hash,
                    "width": 200,
                    "height": 280,
                    "variant": "200",
                    "baseSha256": base_hash,
                },
                {
                    "key": f"market-assets/{base_hash}_600.webp",
                    "sha256": derivative_600_hash,
                    "width": 429,
                    "height": 600,
                    "variant": "600",
                    "baseSha256": base_hash,
                },
            ],
            "remoteVerified": True,
            "remoteScope": "d" * 16,
        },
    }
    (root / "latest.json").write_text(
        json.dumps(pointer),
        encoding="utf-8",
    )
    return snapshot, pointer


class PopulationCursor:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows
        self.result: list[dict[str, object]] = []
        self.commands: list[str] = []

    def __enter__(self) -> "PopulationCursor":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, sql: str, _params: object = None) -> None:
        normalized = " ".join(sql.split())
        self.commands.append(normalized)
        if normalized.startswith("SELECT DATABASE()"):
            self.result = [{"database_name": "cardz_market_cap"}]
        elif "FROM market_grader_population_observation AS observation" in normalized:
            self.result = copy.deepcopy(self.rows)
        else:
            raise AssertionError(f"unexpected SQL: {normalized}")

    def fetchone(self) -> dict[str, object] | None:
        return self.result[0] if self.result else None

    def fetchall(self) -> list[dict[str, object]]:
        return self.result


class PopulationConnection:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.cursor_instance = PopulationCursor(rows)

    def cursor(self) -> PopulationCursor:
        return self.cursor_instance


class MaterializeCursor:
    def __init__(self, connection: "MaterializeConnection") -> None:
        self.connection = connection
        self.result: list[dict[str, object]] = []
        self.lastrowid = 0

    def __enter__(self) -> "MaterializeCursor":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, sql: str, params: tuple[object, ...] = ()) -> None:
        normalized = " ".join(sql.split())
        self.connection.commands.append(normalized)
        self.result = []
        if normalized.startswith("SELECT DATABASE()"):
            self.result = [{"database_name": "cardz_market_cap"}]
        elif normalized.startswith("SELECT GET_LOCK"):
            self.result = [{"acquired": 1}]
        elif normalized.startswith("SELECT RELEASE_LOCK"):
            self.result = [{"released": 1}]
        elif (
            "FROM catalog_variant AS variant" in normalized
            and "catalog_printing_identity AS printing" in normalized
        ):
            self.result = [
                copy.deepcopy(row)
                for variant_id, row in self.connection.printings.items()
                if variant_id in params
            ]
        elif "FROM catalog_variant AS variant" in normalized:
            self.result = [
                {"opaque_id": opaque_id, "resolved_variant_id": variant_id}
                for opaque_id, variant_id in self.connection.opaque.items()
                if opaque_id in params
            ]
        elif "FROM catalog_source_identity AS source" in normalized:
            self.result = [
                {
                    "external_entity_id": external_id,
                    "resolved_variant_id": variant_id,
                }
                for external_id, variant_id in self.connection.sources.items()
                if external_id in params
            ]
        elif (
            "FROM market_universe_lock" in normalized
            and "WHERE lock_sha256 = %s" in normalized
        ):
            target = str(params[0])
            row = next(
                (
                    copy.deepcopy(lock)
                    for lock in self.connection.locks.values()
                    if lock["lock_sha256"] == target
                ),
                None,
            )
            self.result = [row] if row else []
        elif normalized.startswith("INSERT INTO market_universe_lock"):
            lock_id = self.connection.next_lock_id
            self.connection.next_lock_id += 1
            self.lastrowid = lock_id
            self.connection.locks[lock_id] = {
                "id": lock_id,
                "lock_sha256": params[0],
                "effective_at": params[1],
                "policy_json": params[2],
                "member_count": params[3],
                "is_current": 0,
            }
            self.connection.members[lock_id] = []
        elif "FROM market_universe_member" in normalized:
            lock_id = int(params[0])
            self.result = copy.deepcopy(self.connection.members.get(lock_id, []))
        elif normalized.startswith(
            "SELECT id FROM market_universe_lock WHERE is_current = 1"
        ):
            self.result = [
                {"id": lock_id}
                for lock_id, lock in sorted(self.connection.locks.items())
                if lock["is_current"] == 1
            ]
        elif normalized.startswith(
            "UPDATE market_universe_lock SET is_current = 0"
        ):
            target = int(params[0])
            for lock_id, lock in self.connection.locks.items():
                if lock_id != target and lock["is_current"] == 1:
                    lock["is_current"] = 0
        elif normalized.startswith(
            "UPDATE market_universe_lock SET is_current = 1"
        ):
            self.connection.locks[int(params[0])]["is_current"] = 1
        else:
            raise AssertionError(f"unexpected SQL: {normalized}")

    def executemany(
        self, sql: str, values: list[tuple[object, ...]]
    ) -> None:
        normalized = " ".join(sql.split())
        self.connection.commands.append(normalized)
        if not normalized.startswith("INSERT INTO market_universe_member"):
            raise AssertionError(f"unexpected SQL: {normalized}")
        for value in values:
            lock_id = int(value[0])
            self.connection.members.setdefault(lock_id, []).append(
                {
                    "variant_id": value[1],
                    "segment_code": value[2],
                    "member_role": value[3],
                    "market_rank": value[4],
                    "watch_position": value[5],
                    "watch_score": value[6],
                    "selection_signals_json": value[7],
                }
            )
        for rows in self.connection.members.values():
            rows.sort(key=lambda row: int(row["variant_id"]))

    def fetchone(self) -> dict[str, object] | None:
        return self.result[0] if self.result else None

    def fetchall(self) -> list[dict[str, object]]:
        return self.result


class MaterializeConnection:
    def __init__(self) -> None:
        self.opaque = {"cmc_formal": 10, "cmc_monitor": 20}
        self.sources = {"a" * 40: 10, "b" * 40: 20}
        self.printings = {
            10: {
                "variant_id": 10,
                "tcg_code": "pokemon",
                "set_name": "Promo",
                "collector_number": "001",
                **population_printing_fields(
                    "pokemon",
                    "Promo",
                    "001",
                    edition="standard",
                    parallel="normal",
                    finish="holo",
                ),
            },
            20: {
                "variant_id": 20,
                "tcg_code": "one-piece",
                "set_name": "OP01",
                "collector_number": "OP01-001",
                **population_printing_fields(
                    "one-piece",
                    "OP01",
                    "OP01-001",
                ),
            },
        }
        self.locks: dict[int, dict[str, object]] = {
            1: {
                "id": 1,
                "lock_sha256": "f" * 64,
                "effective_at": datetime(2026, 7, 28),
                "policy_json": "{}",
                "member_count": 0,
                "is_current": 1,
            }
        }
        self.members: dict[int, list[dict[str, object]]] = {1: []}
        self.next_lock_id = 2
        self.commands: list[str] = []
        self.begins = 0
        self.commits = 0
        self.rollbacks = 0

    def cursor(self) -> MaterializeCursor:
        return MaterializeCursor(self)

    def begin(self) -> None:
        self.begins += 1

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


class UniverseAuthorityTests(unittest.TestCase):
    def test_printing_language_is_hash_and_tuple_dimension(self) -> None:
        common = ("pokemon", "SV2a", "201/165")
        english = {
            "tcg_code": "pokemon",
            "set_name": "SV2a",
            "collector_number": "201/165",
            **population_printing_fields(*common, card_language="en"),
        }
        japanese = {
            "tcg_code": "pokemon",
            "set_name": "SV2a",
            "collector_number": "201/165",
            **population_printing_fields(*common, card_language="ja"),
        }

        english_identity = universe_authority._printing_row_identity(english)
        japanese_identity = universe_authority._printing_row_identity(japanese)

        self.assertIsNotNone(english_identity)
        self.assertIsNotNone(japanese_identity)
        assert english_identity is not None and japanese_identity is not None
        self.assertEqual(english_identity[1]["cardLanguage"], "en")
        self.assertEqual(japanese_identity[1]["cardLanguage"], "ja")
        self.assertNotEqual(english_identity[0], japanese_identity[0])
        self.assertNotEqual(
            english_identity[1]["canonicalPrintingSha256"],
            japanese_identity[1]["canonicalPrintingSha256"],
        )

    def test_candidate_requires_unique_canonical_printing_and_counts_discovery_evidence(
        self,
    ) -> None:
        rows = [
            {
                "observation_id": 1,
                "observed_variant_id": 10,
                "resolved_variant_id": 10,
                "opaque_id": "cmc_formal",
                "tcg_code": "pokemon",
                "canonical_name": "Pikachu",
                "set_name": "Promo",
                "collector_number": "001",
                "gemrate_id": "a" * 40,
                "top_grade_population": 1100,
                "effective_at": datetime(2026, 7, 27),
                "observed_date": datetime(2026, 7, 27).date(),
                "payload_sha256": "1" * 64,
                **population_printing_fields("pokemon", "Promo", "001"),
            },
            {
                "observation_id": 2,
                "observed_variant_id": 99,
                "resolved_variant_id": 10,
                "opaque_id": "cmc_formal",
                "tcg_code": "pokemon",
                "canonical_name": "Pikachu",
                "set_name": "Promo",
                "collector_number": "001",
                "gemrate_id": "a" * 40,
                "top_grade_population": 1200,
                "effective_at": datetime(2026, 7, 29),
                "observed_date": datetime(2026, 7, 29).date(),
                "payload_sha256": "2" * 64,
                **population_printing_fields("pokemon", "Promo", "001"),
            },
            {
                "observation_id": 3,
                "observed_variant_id": 20,
                "resolved_variant_id": 20,
                "opaque_id": "cmc_monitor",
                "tcg_code": "one-piece",
                "canonical_name": "Luffy",
                "set_name": "OP01",
                "collector_number": "OP01-001",
                "gemrate_id": "b" * 40,
                "top_grade_population": 980,
                "effective_at": datetime(2026, 7, 28),
                "observed_date": datetime(2026, 7, 28).date(),
                "payload_sha256": "3" * 64,
                **population_printing_fields(
                    "one-piece", "OP01", "OP01-001"
                ),
            },
            {
                "observation_id": 4,
                "observed_variant_id": 30,
                "resolved_variant_id": 30,
                "opaque_id": "cmc_incomplete",
                "tcg_code": "pokemon",
                "canonical_name": "Incomplete",
                "set_name": "Promo",
                "collector_number": "002",
                "gemrate_id": "c" * 40,
                "top_grade_population": 1300,
                "effective_at": datetime(2026, 7, 29),
                "observed_date": datetime(2026, 7, 29).date(),
                "payload_sha256": "4" * 64,
                **{
                    **population_printing_fields("pokemon", "Promo", "002"),
                    "printing_identity_status": "candidate",
                },
            },
            {
                "observation_id": 5,
                "observed_variant_id": 40,
                "resolved_variant_id": 40,
                "opaque_id": "cmc_conflict_a",
                "tcg_code": "pokemon",
                "canonical_name": "Conflict A",
                "set_name": "Conflict",
                "collector_number": "999",
                "gemrate_id": "d" * 40,
                "top_grade_population": 1050,
                "effective_at": datetime(2026, 7, 29),
                "observed_date": datetime(2026, 7, 29).date(),
                "payload_sha256": "5" * 64,
                **{
                    **population_printing_fields(
                        "pokemon", "Conflict", "999"
                    ),
                    "printing_hash_count": 2,
                    "printing_tuple_count": 2,
                },
            },
            {
                "observation_id": 6,
                "observed_variant_id": 50,
                "resolved_variant_id": 50,
                "opaque_id": "cmc_conflict_b",
                "tcg_code": "pokemon",
                "canonical_name": "Conflict B",
                "set_name": "Conflict",
                "collector_number": "999",
                "gemrate_id": "e" * 40,
                "top_grade_population": 990,
                "effective_at": datetime(2026, 7, 29),
                "observed_date": datetime(2026, 7, 29).date(),
                "payload_sha256": "6" * 64,
                **{
                    **population_printing_fields(
                        "pokemon", "Conflict", "999"
                    ),
                    "printing_hash_count": 2,
                    "printing_tuple_count": 2,
                },
            },
        ]
        first_connection = PopulationConnection(rows)
        second_connection = PopulationConnection(list(reversed(rows)))
        first = universe_authority.build_candidate(first_connection)  # type: ignore[arg-type]
        second = universe_authority.build_candidate(second_connection)  # type: ignore[arg-type]

        self.assertEqual(first, second)
        self.assertEqual(first["cards"][0]["populationPsa10"], 1200)
        self.assertEqual(len(first["monitoringCandidates"]), 1)
        self.assertEqual(first["counts"]["discoveryEvidence"], 3)
        self.assertEqual(
            first["cards"][0]["printingIdentity"]["status"],
            "canonical",
        )
        self.assertNotIn("language", first["cards"][0])
        self.assertNotIn("canonicalVariantId", first["cards"][0])
        sql = " ".join(first_connection.cursor_instance.commands)
        self.assertIn("catalog_variant_alias", sql)
        self.assertIn("catalog_printing_identity", sql)
        self.assertIn("card_language", sql)
        self.assertEqual(
            db_runtime.validate_active_universe(first),
            [*first["cards"], *first["monitoringCandidates"]],
        )

    def test_immutable_candidate_replays_and_refuses_same_hash_corruption(self) -> None:
        document = candidate_document()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = universe_authority.write_immutable_candidate(document, root)
            second = universe_authority.write_immutable_candidate(document, root)
            self.assertFalse(first["replayed"])
            self.assertTrue(second["replayed"])

            corrupt = copy.deepcopy(document)
            corrupt["authority"] = {"database": "wrong-metadata"}
            with self.assertRaisesRegex(RuntimeError, "immutable universe candidate mismatch"):
                universe_authority.write_immutable_candidate(corrupt, root)

    def test_materialize_promote_is_transactional_and_second_run_is_idempotent(self) -> None:
        connection = MaterializeConnection()
        document = candidate_document()
        first = universe_authority.materialize_candidate(  # type: ignore[arg-type]
            connection, document, promote=True
        )
        second = universe_authority.materialize_candidate(  # type: ignore[arg-type]
            connection, document, promote=True
        )

        self.assertTrue(first["created"])
        self.assertTrue(first["promoted"])
        self.assertEqual(first["priorCurrentLockIds"], [1])
        self.assertFalse(second["created"])
        self.assertTrue(second["replayed"])
        self.assertFalse(second["promoted"])
        self.assertEqual(connection.locks[1]["is_current"], 0)
        self.assertEqual(connection.locks[2]["is_current"], 1)
        self.assertEqual(connection.begins, 2)
        self.assertEqual(connection.commits, 2)
        self.assertEqual(connection.rollbacks, 0)
        self.assertFalse(any(command.startswith("DELETE") for command in connection.commands))
        self.assertFalse(any("ON DUPLICATE KEY" in command for command in connection.commands))

    def test_materialize_refuses_printing_identity_changed_after_build(self) -> None:
        connection = MaterializeConnection()
        connection.printings[10]["printing_identity_status"] = "review"

        with self.assertRaisesRegex(
            RuntimeError,
            "canonical printing identity changed after build",
        ):
            universe_authority.materialize_candidate(  # type: ignore[arg-type]
                connection,
                candidate_document(),
                promote=False,
            )

        self.assertEqual(connection.commits, 0)
        self.assertEqual(connection.rollbacks, 1)
        self.assertEqual(connection.locks[1]["is_current"], 1)

    def test_corrupt_same_hash_lock_is_refused_without_replacing_current(self) -> None:
        connection = MaterializeConnection()
        document = candidate_document()
        lock_hash = db_runtime.active_universe_lock_hash(document)
        connection.locks[2] = {
            "id": 2,
            "lock_sha256": lock_hash,
            "effective_at": datetime(2026, 7, 29),
            "policy_json": db_runtime.canonical_json(document["policy"]).decode("utf-8"),
            "member_count": 2,
            "is_current": 0,
        }
        connection.members[2] = [
            {
                "variant_id": 999,
                "segment_code": "tracked",
                "member_role": "candidate",
                "market_rank": None,
                "watch_position": None,
                "watch_score": None,
                "selection_signals_json": "{}",
            }
        ]
        connection.next_lock_id = 3

        with self.assertRaisesRegex(RuntimeError, "corrupt; refusing rewrite"):
            universe_authority.materialize_candidate(  # type: ignore[arg-type]
                connection, document, promote=True
            )

        self.assertEqual(connection.rollbacks, 1)
        self.assertEqual(connection.locks[1]["is_current"], 1)
        self.assertEqual(connection.locks[2]["is_current"], 0)
        self.assertFalse(any(command.startswith("DELETE") for command in connection.commands))
        self.assertFalse(
            any(
                command.startswith("UPDATE market_universe_lock")
                for command in connection.commands
            )
        )

    def test_promote_archives_active_before_db_and_then_atomically_replaces_it(self) -> None:
        old = candidate_document()
        candidate = copy.deepcopy(old)
        candidate["cards"][0]["populationPsa10"] = 1002
        candidate["cards"][0]["populationPayloadSha256"] = "3" * 64
        rehash_candidate(candidate)
        old_payload = (
            json.dumps(old, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        ).encode("utf-8")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            active = root / "tracked-universe.json"
            archive = root / "archive"
            active.write_bytes(old_payload)

            def promoted(
                _connection: object,
                _document: object,
                *,
                promote: bool,
            ) -> dict[str, object]:
                self.assertTrue(promote)
                self.assertEqual(active.read_bytes(), old_payload)
                archived = list(archive.glob("active_*.json"))
                self.assertEqual(len(archived), 1)
                self.assertEqual(archived[0].read_bytes(), old_payload)
                return {
                    "lockId": 2,
                    "lockSha256": db_runtime.active_universe_lock_hash(candidate),
                    "promoted": True,
                }

            with mock.patch.object(
                universe_authority,
                "materialize_candidate",
                side_effect=promoted,
            ):
                report = universe_authority.promote_candidate(  # type: ignore[arg-type]
                    object(),
                    candidate,
                    active_path=active,
                    archive_root=archive,
                )

            self.assertEqual(
                universe_authority.read_candidate(active),
                candidate,
            )
            archived_path = Path(report["archivedActive"]["path"])
            self.assertEqual(archived_path.read_bytes(), old_payload)
            self.assertEqual(
                report["activeUniverse"]["lockSha256"],
                db_runtime.active_universe_lock_hash(candidate),
            )

    def test_active_replace_failure_preserves_old_evidence_and_status_blocker(self) -> None:
        old = candidate_document()
        candidate = copy.deepcopy(old)
        candidate["cards"][0]["populationPsa10"] = 1002
        candidate["cards"][0]["populationPayloadSha256"] = "3" * 64
        rehash_candidate(candidate)
        old_payload = (
            json.dumps(old, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        ).encode("utf-8")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            active = root / "tracked-universe.json"
            archive = root / "archive"
            active.write_bytes(old_payload)
            with (
                mock.patch.object(
                    universe_authority,
                    "materialize_candidate",
                    return_value={"lockId": 2, "promoted": True},
                ),
                mock.patch.object(
                    universe_authority.os,
                    "replace",
                    side_effect=OSError("replace failed"),
                ),
                self.assertRaisesRegex(OSError, "replace failed"),
            ):
                universe_authority.promote_candidate(  # type: ignore[arg-type]
                    object(),
                    candidate,
                    active_path=active,
                    archive_root=archive,
                )

            self.assertEqual(active.read_bytes(), old_payload)
            self.assertEqual(len(list(archive.glob("active_*.json"))), 1)
            active_hash, active_error, blockers = (
                universe_authority.active_evidence_integrity(
                    active,
                    db_runtime.active_universe_lock_hash(candidate),
                )
            )
            self.assertEqual(active_hash, db_runtime.active_universe_lock_hash(old))
            self.assertIsNone(active_error)
            self.assertEqual(blockers, ["active_universe_hash_mismatch"])

    def test_noncanonical_database_is_rejected_before_connect(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "non-canonical database"):
            universe_authority.connect_from_values(
                {
                    "CARDZ_DB_NAME": "jlp",
                    "CARDZ_DB_PASSWORD": "not-used",
                },
                read_only=True,
            )

    def test_generation_status_reports_deterministic_age_and_full_pointer_contract(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_generation_fixture(root)
            report, blockers = universe_authority.generation_status(
                root,
                reference_now=datetime(
                    2026,
                    7,
                    29,
                    3,
                    0,
                    tzinfo=timezone.utc,
                ),
            )

        self.assertEqual(blockers, [])
        self.assertEqual(report["status"], "valid")
        self.assertEqual(report["generatedAt"], "2026-07-29T00:30:00Z")
        self.assertEqual(report["ageHours"], 2.5)
        self.assertEqual(report["media"]["hashCount"], 1)
        self.assertEqual(report["media"]["assetCount"], 3)
        self.assertTrue(report["media"]["remoteVerified"])
        self.assertEqual(
            report["qcReceiptFileSha256"],
            report["pointerQcReceiptSha256"],
        )

    def test_generation_status_rejects_semantically_invalid_qc_receipts(
        self,
    ) -> None:
        cases = (
            (
                "failed receipt",
                {"status": "failed", "blockers": ["card_failed"]},
            ),
            ("missing DB binding", {"dbQcReceiptSha256": None}),
            ("wrong generation", {"generationId": "daily_other"}),
            (
                "wrong card image",
                {
                    "cards": [
                        {
                            "id": "cmc_fixture",
                            "imageSha256": "f" * 64,
                            "decision": "passed",
                            "evidenceSha256": "e" * 64,
                        }
                    ]
                },
            ),
        )
        for label, overrides in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                write_generation_fixture(root, receipt_overrides=overrides)
                report, blockers = universe_authority.generation_status(
                    root,
                    reference_now=datetime(
                        2026,
                        7,
                        29,
                        3,
                        0,
                        tzinfo=timezone.utc,
                    ),
                )

                self.assertEqual(report["status"], "blocked")
                self.assertIn("generation_qc_receipt_invalid", blockers)

    def test_generation_status_rejects_resealed_snapshot_with_reused_receipt(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            snapshot, pointer = write_generation_fixture(root)
            card = snapshot["top100"][0]  # type: ignore[index]
            card["pricePsa10"]["value"] = 101  # type: ignore[index]
            card["marketCap"]["value"] = 101_000  # type: ignore[index]
            snapshot["generation"]["contentSha256"] = ""  # type: ignore[index]
            snapshot["generation"]["contentSha256"] = hashlib.sha256(  # type: ignore[index]
                db_runtime.canonical_json(snapshot)
            ).hexdigest()
            generation_id = snapshot["generation"]["id"]  # type: ignore[index]
            generation_path = (
                root / "generations" / str(generation_id) / "snapshot.json"
            )
            generation_path.write_text(json.dumps(snapshot), encoding="utf-8")
            pointer["sha256"] = snapshot["generation"]["contentSha256"]  # type: ignore[index]
            (root / "latest.json").write_text(
                json.dumps(pointer),
                encoding="utf-8",
            )

            report, blockers = universe_authority.generation_status(
                root,
                reference_now=datetime(
                    2026,
                    7,
                    29,
                    3,
                    0,
                    tzinfo=timezone.utc,
                ),
            )

        self.assertEqual(report["status"], "blocked")
        self.assertIn("generation_qc_receipt_invalid", blockers)

    def test_generation_status_blocks_future_and_stale_generations(self) -> None:
        cases = (
            (
                "future",
                "2026-07-29T03:00:01Z",
                "generation_in_future",
            ),
            (
                "stale",
                "2026-07-27T02:59:59Z",
                "generation_stale",
            ),
        )
        for label, generated_at, expected_blocker in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                write_generation_fixture(root, generated_at=generated_at)
                report, blockers = universe_authority.generation_status(
                    root,
                    reference_now=datetime(
                        2026,
                        7,
                        29,
                        3,
                        0,
                        tzinfo=timezone.utc,
                    ),
                )

                self.assertEqual(report["status"], "blocked")
                self.assertIn(expected_blocker, blockers)

    def test_generation_status_blocks_invalid_timestamp_and_legacy_pointer_fields(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _snapshot, pointer = write_generation_fixture(
                root,
                generated_at="not-a-timestamp",
            )
            pointer.pop("qcReceiptKey")
            pointer.pop("qcReceiptSha256")
            pointer["media"] = {"prefix": "market-assets/"}
            (root / "latest.json").write_text(
                json.dumps(pointer),
                encoding="utf-8",
            )
            report, blockers = universe_authority.generation_status(
                root,
                reference_now=datetime(
                    2026,
                    7,
                    29,
                    3,
                    0,
                    tzinfo=timezone.utc,
                ),
            )

        self.assertEqual(report["status"], "blocked")
        self.assertIsNone(report["ageHours"])
        self.assertIn("generation_generated_at_invalid", blockers)
        self.assertIn("generation_qc_receipt_key_invalid", blockers)
        self.assertIn("generation_pointer_qc_receipt_hash_invalid", blockers)
        self.assertIn("generation_media_hashes_invalid", blockers)
        self.assertIn("generation_media_assets_invalid", blockers)
        self.assertIn("generation_media_remote_unverified", blockers)

    def test_latest_full_db_qc_receipt_is_hash_bound_and_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_id = "qc_20260729_fixture"
            run_root = root / run_id
            run_root.mkdir()
            counts = {
                "qualified": 2,
                "monitoring": 0,
                "releaseReadyQualified": 0,
                "releaseBlockedQualified": 2,
            }
            gate = {
                "eligible": False,
                "blockerCount": 4,
                "blockerCardCount": 2,
                "blockers": {"printing_identity_missing": 2},
            }
            report = {
                "runId": run_id,
                "asOf": "2026-07-29T00:00:00Z",
                "status": "blocked",
                "counts": {**counts, "auditedCandidates": 2},
                "releaseGate": gate,
            }
            report_bytes = (
                json.dumps(report, sort_keys=True, indent=2) + "\n"
            ).encode("utf-8")
            (run_root / "report.json").write_bytes(report_bytes)
            receipt = {
                "schemaVersion": 1,
                "runId": run_id,
                "asOf": report["asOf"],
                "status": "blocked",
                "readOnly": True,
                "database": "cardz_market_cap",
                "reportSha256": hashlib.sha256(report_bytes).hexdigest(),
                "counts": counts,
                "releaseGate": gate,
            }
            (run_root / "receipt.json").write_text(
                json.dumps(receipt),
                encoding="utf-8",
            )

            status, blockers = (
                universe_authority.latest_canonical_db_qc_status(
                    root,
                    reference_now=datetime(
                        2026,
                        7,
                        29,
                        2,
                        0,
                        tzinfo=timezone.utc,
                    ),
                )
            )
            self.assertEqual(status["fullDbQcReleaseBlocked"], 2)
            self.assertEqual(status["fullDbQcAgeHours"], 2.0)
            self.assertEqual(blockers, ["canonical_db_qc_failed"])

            (run_root / "report.json").write_text("{}", encoding="utf-8")
            invalid, invalid_blockers = (
                universe_authority.latest_canonical_db_qc_status(root)
            )
            self.assertEqual(invalid["fullDbQcStatus"], "invalid")
            self.assertEqual(
                invalid_blockers,
                ["canonical_db_qc_evidence_invalid"],
            )

    def test_status_reports_zero_qualified_as_universe_blocker_not_database_failure(
        self,
    ) -> None:
        class StatusCursor:
            def __init__(self) -> None:
                self.result: list[dict[str, object]] = []

            def __enter__(self) -> "StatusCursor":
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def execute(self, sql: str, _params: object = None) -> None:
                normalized = " ".join(sql.split())
                if normalized.startswith("SELECT DATABASE()"):
                    self.result = [{"database_name": "cardz_market_cap"}]
                elif (
                    "FROM market_universe_lock" in normalized
                    and "WHERE is_current = 1" in normalized
                ):
                    self.result = []
                else:
                    raise AssertionError(f"unexpected SQL: {normalized}")

            def fetchone(self) -> dict[str, object] | None:
                return self.result[0] if self.result else None

        class StatusConnection:
            def cursor(self) -> StatusCursor:
                return StatusCursor()

        with (
            mock.patch.object(
                universe_authority,
                "_select_candidate_members",
                return_value=([], [], 932),
            ),
            mock.patch.object(
                universe_authority,
                "qc_pending",
                return_value=(
                    {"status": "available"},
                    {"status": "available"},
                ),
            ),
            mock.patch.object(
                universe_authority,
                "latest_canonical_db_qc_status",
                return_value=(
                    {"fullDbQcStatus": "passed"},
                    [],
                ),
            ),
            mock.patch.object(
                universe_authority,
                "generation_status",
                return_value=({"status": "valid"}, []),
            ),
        ):
            report = universe_authority.machine_status(  # type: ignore[arg-type]
                StatusConnection(),
                active_path=None,
            )
            with self.assertRaisesRegex(RuntimeError, "no promotable"):
                universe_authority.build_candidate(StatusConnection())  # type: ignore[arg-type]

        self.assertTrue(report["database"]["connected"])
        self.assertEqual(report["universeIntegrity"]["candidateQualified"], 0)
        self.assertEqual(
            report["universeIntegrity"]["candidateDiscoveryEvidence"],
            932,
        )
        self.assertIn(
            "universe_candidate_no_qualified_printings",
            report["releaseGate"]["blockers"],
        )

    def test_backend_status_json_is_read_only_and_surfaces_blockers(self) -> None:
        class FakeConnection:
            def close(self) -> None:
                return None

        authority = mock.Mock()
        authority.connect_from_values.return_value = FakeConnection()
        authority.machine_status.return_value = {
            "database": {
                "authority": "canonical_mysql",
                "name": "cardz_market_cap",
                "connected": True,
            },
            "universeIntegrity": {
                "status": "blocked",
                "matchesCandidate": False,
            },
            "qc": {"status": "available", "imageChecksRejected": 2},
            "pending": {"status": "available", "identityReviews": 3},
            "generation": {
                "status": "blocked",
                "productionEligible": False,
            },
            "releaseGate": {
                "eligible": False,
                "blockers": ["current_universe_hash_mismatch"],
            },
        }
        output = io.StringIO()
        with (
            mock.patch.object(sys, "argv", ["backend.py", "status", "--json"]),
            mock.patch.object(
                backend,
                "read_only_runtime_config",
                return_value={
                    "CARDZ_DB_HOST": "db",
                    "CARDZ_DB_PORT": "3308",
                    "CARDZ_DB_NAME": "cardz_market_cap",
                    "CARDZ_DB_USER": "cardz",
                    "CARDZ_DB_PASSWORD": "redacted",
                },
            ),
            mock.patch.object(
                backend, "load_universe_authority_module", return_value=authority
            ),
            mock.patch.object(backend, "ensure_python_environment") as ensure_venv,
            mock.patch.object(backend, "runtime_config") as mutating_config,
            mock.patch.object(backend, "write_local_config") as write_config,
            redirect_stdout(output),
        ):
            self.assertEqual(backend.main(), 0)

        report = json.loads(output.getvalue())
        self.assertTrue(report["readOnly"])
        self.assertEqual(report["status"], "blocked")
        self.assertIn("universeIntegrity", report)
        self.assertIn("qc", report)
        self.assertIn("pending", report)
        self.assertIn("generation", report)
        self.assertEqual(
            report["releaseGate"]["blockers"],
            ["current_universe_hash_mismatch"],
        )
        ensure_venv.assert_not_called()
        mutating_config.assert_not_called()
        write_config.assert_not_called()

    def test_read_only_config_never_generates_password_or_writes_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config_path = Path(temporary) / "backend.env"
            config_path.write_text(
                "CARDZ_DB_HOST=127.0.0.1\n"
                "CARDZ_DB_PORT=3308\n"
                "CARDZ_DB_NAME=cardz_market_cap\n"
                "CARDZ_DB_USER=cardz\n"
                "CARDZ_DB_PASSWORD=fixture-secret\r\n",
                encoding="utf-8",
            )
            with (
                mock.patch.object(backend, "CONFIG_PATH", config_path),
                mock.patch.dict(os.environ, {}, clear=True),
                mock.patch.object(backend, "write_local_config") as writer,
                mock.patch.object(backend.secrets, "token_urlsafe") as token,
            ):
                values = backend.read_only_runtime_config(external=False)
            self.assertEqual(values["CARDZ_DB_NAME"], "cardz_market_cap")
            self.assertEqual(values["CARDZ_DB_PASSWORD"], "fixture-secret")
        writer.assert_not_called()
        token.assert_not_called()

    def test_backend_universe_promote_accepts_explicit_active_consumer(self) -> None:
        class FakeConnection:
            def close(self) -> None:
                return None

        authority = mock.Mock()
        authority.connect_from_values.return_value = FakeConnection()
        authority.read_candidate.return_value = candidate_document()
        authority.promote_candidate.return_value = {
            "lockId": 2,
            "promoted": True,
            "archivedActive": {"contentSha256": "1" * 64},
            "activeUniverse": {"lockSha256": "2" * 64},
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = root / "candidate.json"
            active = root / "tracked-universe.json"
            output = io.StringIO()
            with (
                mock.patch.object(
                    sys,
                    "argv",
                    [
                        "backend.py",
                        "universe-promote",
                        "--universe-candidate",
                        str(candidate),
                        "--active-universe",
                        str(active),
                        "--json",
                    ],
                ),
                mock.patch.object(
                    backend,
                    "read_only_runtime_config",
                    return_value={
                        "CARDZ_DB_HOST": "db",
                        "CARDZ_DB_PORT": "3308",
                        "CARDZ_DB_NAME": "cardz_market_cap",
                        "CARDZ_DB_USER": "cardz",
                        "CARDZ_DB_PASSWORD": "redacted",
                    },
                ),
                mock.patch.object(
                    backend,
                    "load_universe_authority_module",
                    return_value=authority,
                ),
                redirect_stdout(output),
            ):
                self.assertEqual(backend.main(), 0)

            authority.promote_candidate.assert_called_once_with(
                authority.connect_from_values.return_value,
                authority.read_candidate.return_value,
                active_path=active.resolve(),
            )
            self.assertEqual(json.loads(output.getvalue())["status"], "complete")


if __name__ == "__main__":
    unittest.main()
