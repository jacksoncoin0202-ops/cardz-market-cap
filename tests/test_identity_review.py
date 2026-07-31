from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))
SPEC = importlib.util.spec_from_file_location(
    "identity_review",
    ROOT / "pipelines" / "identity_review.py",
)
assert SPEC and SPEC.loader
review = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = review
SPEC.loader.exec_module(review)


class BindingCursor:
    def __init__(
        self,
        *,
        owner: int | None,
        alias_target: int | None = None,
    ) -> None:
        self.owner = owner
        self.alias_target = alias_target
        self.result: dict[str, object] | None = None
        self.commands: list[tuple[str, object]] = []
        self.rowcount = 0

    def execute(self, sql: str, args: object = None) -> None:
        normalized = " ".join(sql.split())
        self.commands.append((normalized, args))
        self.rowcount = 0
        if normalized.startswith("SELECT variant_id FROM catalog_source_identity"):
            self.result = {"variant_id": self.owner} if self.owner is not None else None
        elif normalized.startswith("SELECT canonical_variant_id FROM catalog_variant_alias"):
            self.result = (
                {"canonical_variant_id": self.alias_target}
                if self.alias_target is not None
                else None
            )
        else:
            self.result = None

    def fetchone(self) -> dict[str, object] | None:
        return self.result


REVIEW = {
    "source_code": "snkrdunk",
    "external_entity_id": "12345",
    "evidence_sha256": "a" * 64,
}


class ValidationTests(unittest.TestCase):
    def test_accept_and_merge_require_variant(self) -> None:
        for action in ("accept", "merge"):
            with self.subTest(action=action):
                with self.assertRaisesRegex(ValueError, "requires --variant-id"):
                    review.validate_resolution(
                        action=action,
                        variant_id=None,
                        reason="verified exact receipt",
                        actor="operator",
                        evidence_sha256="a" * 64,
                    )

    def test_reject_forbids_variant(self) -> None:
        with self.assertRaisesRegex(ValueError, "does not accept"):
            review.validate_resolution(
                action="reject",
                variant_id=1,
                reason="wrong printing",
                actor="operator",
                evidence_sha256="a" * 64,
            )

    def test_evidence_hash_is_mandatory_and_exact(self) -> None:
        with self.assertRaisesRegex(ValueError, "64 lowercase hex"):
            review.validate_resolution(
                action="reject",
                variant_id=None,
                reason="wrong printing",
                actor="operator",
                evidence_sha256="ABC",
            )

    def test_outcome_hash_is_deterministic(self) -> None:
        outcome = {
            "reviewId": 3,
            "action": "accept",
            "resolvedVariantId": 10,
        }
        self.assertEqual(review.outcome_sha256(outcome), review.outcome_sha256(dict(outcome)))


class SourceOwnershipTests(unittest.TestCase):
    def test_accept_never_rebinds_an_existing_owner(self) -> None:
        cursor = BindingCursor(owner=20)
        with self.assertRaisesRegex(ValueError, "cannot rebind ownership"):
            review._bind_source_identity(
                cursor,
                REVIEW,
                action="accept",
                variant_id=10,
            )
        self.assertFalse(any(sql.startswith("UPDATE catalog_source_identity") for sql, _ in cursor.commands))

    def test_merge_only_rebinds_a_confirmed_alias_owner(self) -> None:
        cursor = BindingCursor(owner=20, alias_target=10)
        outcome = review._bind_source_identity(
            cursor,
            REVIEW,
            action="merge",
            variant_id=10,
        )
        self.assertEqual(outcome, "merged_alias_owner")
        update = next(
            (sql, args)
            for sql, args in cursor.commands
            if sql.startswith("UPDATE catalog_source_identity")
        )
        self.assertEqual(update[1][0], 10)

    def test_merge_rejects_unowned_identity(self) -> None:
        cursor = BindingCursor(owner=None)
        with self.assertRaisesRegex(ValueError, "requires an existing source identity"):
            review._bind_source_identity(
                cursor,
                REVIEW,
                action="merge",
                variant_id=10,
            )

    def test_accept_inserts_an_unowned_identity_without_upsert(self) -> None:
        cursor = BindingCursor(owner=None)
        outcome = review._bind_source_identity(
            cursor,
            REVIEW,
            action="accept",
            variant_id=10,
        )
        self.assertEqual(outcome, "inserted")
        insert = next(
            sql
            for sql, _args in cursor.commands
            if sql.startswith("INSERT INTO catalog_source_identity")
        )
        self.assertNotIn("ON DUPLICATE KEY UPDATE", insert)


class AuditBoundaryTests(unittest.TestCase):
    def test_resolution_updates_queue_and_appends_audit_row(self) -> None:
        source = (ROOT / "pipelines/identity_review.py").read_text(encoding="utf-8")
        self.assertIn("INSERT INTO market_identity_review_resolution", source)
        self.assertIn("UPDATE market_identity_review_queue", source)
        self.assertNotIn("DELETE FROM market_identity_review", source)


if __name__ == "__main__":
    unittest.main()
