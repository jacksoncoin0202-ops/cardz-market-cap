#!/usr/bin/env python3
"""List or resolve one identity-review row with an append-only audit record.

``list`` is read-only. ``resolve`` is dry-run by default and requires
``--apply`` to commit. The caller must provide the current review evidence hash
so a stale operator screen cannot resolve a changed case.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from db_runtime import add_connection_args, connection_from_args  # noqa: E402


ACTIONS = {"accept", "merge", "reject"}
TERMINAL_STATUS = {
    "accept": "accepted",
    "merge": "merged",
    "reject": "rejected",
}


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def outcome_sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def validate_resolution(
    *,
    action: str,
    variant_id: int | None,
    reason: str,
    actor: str,
    evidence_sha256: str,
) -> None:
    if action not in ACTIONS:
        raise ValueError(f"unsupported action: {action}")
    if action in {"accept", "merge"} and not variant_id:
        raise ValueError(f"{action} requires --variant-id")
    if action == "reject" and variant_id is not None:
        raise ValueError("reject does not accept --variant-id")
    if not reason.strip():
        raise ValueError("--reason is required")
    if not actor.strip():
        raise ValueError("--actor is required")
    if len(evidence_sha256) != 64 or any(char not in "0123456789abcdef" for char in evidence_sha256):
        raise ValueError("--evidence-sha256 must be 64 lowercase hex characters")


def list_pending(cursor: Any, limit: int) -> list[dict[str, Any]]:
    cursor.execute(
        """
        SELECT id, source_code, external_entity_id, reason_code, evidence_sha256, created_at
        FROM market_identity_review_queue
        WHERE status='pending'
        ORDER BY created_at, id
        LIMIT %s
        """,
        (limit,),
    )
    return [dict(row) for row in cursor.fetchall()]


def _load_review(cursor: Any, review_id: int) -> dict[str, Any]:
    cursor.execute(
        """
        SELECT id, source_code, external_entity_id, reason_code, evidence_sha256,
               status, resolved_variant_id
        FROM market_identity_review_queue
        WHERE id=%s
        FOR UPDATE
        """,
        (review_id,),
    )
    row = cursor.fetchone()
    if row is None:
        raise ValueError(f"review {review_id} does not exist")
    return dict(row)


def _assert_active_variant(cursor: Any, variant_id: int) -> None:
    cursor.execute(
        """
        SELECT v.identity_status, alias.canonical_variant_id
        FROM catalog_variant AS v
        LEFT JOIN catalog_variant_alias AS alias ON alias.duplicate_variant_id=v.id
        WHERE v.id=%s
        FOR UPDATE
        """,
        (variant_id,),
    )
    row = cursor.fetchone()
    if row is None:
        raise ValueError(f"variant {variant_id} does not exist")
    if row["canonical_variant_id"] is not None or str(row["identity_status"]) == "alias":
        raise ValueError(f"variant {variant_id} is an alias, choose its canonical variant")


def _bind_source_identity(
    cursor: Any,
    review: Mapping[str, Any],
    *,
    action: str,
    variant_id: int,
) -> str:
    key = (str(review["source_code"]), str(review["external_entity_id"]))
    cursor.execute(
        """
        SELECT variant_id
        FROM catalog_source_identity
        WHERE source_code=%s AND external_entity_id=%s
        FOR UPDATE
        """,
        key,
    )
    owner = cursor.fetchone()
    if owner is None:
        if action == "merge":
            raise ValueError("merge requires an existing source identity owned by a confirmed alias")
        cursor.execute(
            """
            INSERT INTO catalog_source_identity
                (source_code, external_entity_id, variant_id, match_status, evidence_sha256)
            VALUES (%s, %s, %s, 'manual_review', %s)
            """,
            (*key, variant_id, str(review["evidence_sha256"])),
        )
        return "inserted"

    owner_id = int(owner["variant_id"])
    if owner_id == variant_id:
        return "already_owned"
    if action != "merge":
        raise ValueError(
            f"source identity is already owned by variant {owner_id}; "
            "accept cannot rebind ownership"
        )
    cursor.execute(
        """
        SELECT canonical_variant_id
        FROM catalog_variant_alias
        WHERE duplicate_variant_id=%s
        FOR UPDATE
        """,
        (owner_id,),
    )
    alias = cursor.fetchone()
    if alias is None or int(alias["canonical_variant_id"]) != variant_id:
        raise ValueError(
            f"source identity owner {owner_id} is not a confirmed alias of {variant_id}"
        )
    cursor.execute(
        """
        UPDATE catalog_source_identity
        SET variant_id=%s, match_status='manual_review_merge',
            evidence_sha256=%s
        WHERE source_code=%s AND external_entity_id=%s
        """,
        (variant_id, str(review["evidence_sha256"]), *key),
    )
    return "merged_alias_owner"


def resolve_review(
    connection: Any,
    *,
    review_id: int,
    action: str,
    variant_id: int | None,
    reason: str,
    actor: str,
    evidence_sha256: str,
    commit: bool,
) -> dict[str, Any]:
    validate_resolution(
        action=action,
        variant_id=variant_id,
        reason=reason,
        actor=actor,
        evidence_sha256=evidence_sha256,
    )
    resolved_at = datetime.now(timezone.utc).replace(tzinfo=None)
    with connection.cursor() as cursor:
        review = _load_review(cursor, review_id)
        if str(review["evidence_sha256"]) != evidence_sha256:
            raise ValueError("review evidence changed; refresh the pending list")

        cursor.execute(
            """
            SELECT resolution_action, resolved_variant_id, reason_text, actor,
                   review_evidence_sha256
            FROM market_identity_review_resolution
            WHERE review_id=%s
            FOR UPDATE
            """,
            (review_id,),
        )
        prior = cursor.fetchone()
        requested_variant = variant_id if action != "reject" else None
        if prior:
            exact = (
                str(prior["resolution_action"]) == action
                and prior["resolved_variant_id"] == requested_variant
                and str(prior["reason_text"]) == reason.strip()
                and str(prior["actor"]) == actor.strip()
                and str(prior["review_evidence_sha256"]) == evidence_sha256
            )
            if not exact:
                raise ValueError(f"review {review_id} already has a different terminal resolution")
            connection.rollback()
            return {
                "reviewId": review_id,
                "action": action,
                "status": TERMINAL_STATUS[action],
                "identityBinding": "already_resolved",
                "committed": False,
                "idempotent": True,
            }
        if str(review["status"]) != "pending":
            raise ValueError(f"review {review_id} is already {review['status']}")

        identity_binding = "unchanged"
        if action in {"accept", "merge"}:
            assert variant_id is not None
            _assert_active_variant(cursor, variant_id)
            identity_binding = _bind_source_identity(
                cursor,
                review,
                action=action,
                variant_id=variant_id,
            )

        outcome = {
            "reviewId": review_id,
            "action": action,
            "resolvedVariantId": requested_variant,
            "reason": reason.strip(),
            "actor": actor.strip(),
            "reviewEvidenceSha256": evidence_sha256,
        }
        cursor.execute(
            """
            INSERT INTO market_identity_review_resolution
                (review_id, resolution_action, resolved_variant_id, reason_text, actor,
                 review_evidence_sha256, outcome_sha256, resolved_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                review_id,
                action,
                requested_variant,
                reason.strip(),
                actor.strip(),
                evidence_sha256,
                outcome_sha256(outcome),
                resolved_at,
            ),
        )
        cursor.execute(
            """
            UPDATE market_identity_review_queue
            SET status=%s, resolved_variant_id=%s, resolved_at=%s
            WHERE id=%s AND status='pending'
            """,
            (TERMINAL_STATUS[action], requested_variant, resolved_at, review_id),
        )
        if int(cursor.rowcount) != 1:
            raise RuntimeError("review queue changed during resolution")

    if commit:
        connection.commit()
    else:
        connection.rollback()
    return {
        "reviewId": review_id,
        "action": action,
        "status": TERMINAL_STATUS[action],
        "resolvedVariantId": requested_variant,
        "identityBinding": identity_binding,
        "committed": commit,
        "idempotent": False,
    }


def _connection_parser(subparsers: Any, name: str, **kwargs: Any) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(name, **kwargs)
    add_connection_args(parser)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    listing = _connection_parser(subparsers, "list", help="list pending cases")
    listing.add_argument("--limit", type=int, default=50)

    resolving = _connection_parser(subparsers, "resolve", help="resolve one case")
    resolving.add_argument("--review-id", type=int, required=True)
    resolving.add_argument("--action", choices=sorted(ACTIONS), required=True)
    resolving.add_argument("--variant-id", type=int)
    resolving.add_argument("--reason", required=True)
    resolving.add_argument("--actor", required=True)
    resolving.add_argument("--evidence-sha256", required=True)
    resolving.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)

    connection = connection_from_args(args)
    try:
        if args.command == "list":
            if args.limit < 1 or args.limit > 500:
                raise ValueError("--limit must be between 1 and 500")
            with connection.cursor() as cursor:
                report = {"pending": list_pending(cursor, args.limit)}
            connection.rollback()
        else:
            report = resolve_review(
                connection,
                review_id=args.review_id,
                action=args.action,
                variant_id=args.variant_id,
                reason=args.reason,
                actor=args.actor,
                evidence_sha256=args.evidence_sha256,
                commit=bool(args.apply),
            )
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        return 0
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
