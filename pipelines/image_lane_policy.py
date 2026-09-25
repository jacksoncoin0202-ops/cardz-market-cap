#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The only writer of market_canonical_image_acceptance.

Why one writer: four lanes (SNK EN collector, rebuild_036 PC image-bind, the
human pin tool, new_era_db_tidy 026) each had their own INSERT and their own
idea of what may be accepted, so a SNKRDUNK listing photo with a "SAMPLE"
watermark (or non-card-front art) kept superseding the image a human had
picked or approved on One Piece cards.  Every rule now lives here, once, and
scripts/test_image_lane_policy.py fails if any other file under pipelines/ or
scripts/ carries a raw INSERT into the table.

Rules (in this order):
  1. No lane -- human included -- accepts a (variant, content sha) that is in
     market_image_rejection_registry.  Keyed on the pair, never the sha alone:
     dae7a026ec6c... is rejected for v260 and legitimately published for v661.
  2. A head (current, not superseded) acceptance with accepted_by ==
     HUMAN_IMAGE_ACCEPTED_BY is never superseded by an automatic lane.
  3. A head whose (variant, content sha) is in market_image_review_approval is
     never superseded by an automatic lane.
  4. One Piece (catalog_printing_identity.tcg_code='one-piece'): an automatic
     SNK lane never supersedes an existing head at all -- SNK OP art is where
     the SAMPLE images come from.  It may only fill a variant with no head.
     A variant with no tcg_code is treated as One Piece (fail-closed).
  A head with the same lineage is already current: nothing is inserted.

A rule that stops an automatic lane returns ImageLaneDecision(status='held',
reason=...); it raises ImageLaneHeld only when the caller passes
raise_on_hold=True.  Commit / rollback always stay with the caller.

Missing tables are fail-closed, deliberately: both registry and approval are
read with plain SELECTs, so on a DB without them the DB error propagates and
the caller's transaction rolls back.  Skipping would re-open exactly the hole
this module closes (a rejected pair accepted again because the registry
"was not there").  Both tables exist on live 3308 (2026-09-26); every lane
already read the registry unconditionally before this module existed, and
migration 061 (still .pending) only re-declares it with IF NOT EXISTS.  The
approval table is read only when an automatic lane is about to supersede a
non-human head, so filling an empty variant never depends on it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# accepted_by of a human-designated image acceptance (pipelines/
# pin_human_card_image.py; the 2026-08-11 pin has the same value).
# rebuild_036.HUMAN_IMAGE_ACCEPTED_BY re-exports this name.
HUMAN_IMAGE_ACCEPTED_BY = "human"
ONE_PIECE_TCG_CODE = "one-piece"

HELD_REJECTED_CONTENT = "rejected_content"
HELD_HEAD_IS_HUMAN = "head_is_human"
HELD_HEAD_REVIEW_APPROVED = "head_is_review_approved"
HELD_ONE_PIECE_SNK = "one_piece_snk_cannot_supersede"

_HEX64 = re.compile(r"^[0-9a-f]{64}$")

ASSET_SQL = (
    "SELECT id FROM market_image_asset"
    " WHERE variant_id=%s AND image_kind='raw_front' AND content_sha256=%s"
    " LIMIT 1"
)
REJECTION_SQL = (
    "SELECT 1 FROM market_image_rejection_registry"
    " WHERE variant_id=%s AND content_sha256=%s"
)
HEAD_SQL = (
    "SELECT ca.id, ca.lineage_sha256, ca.accepted_by"
    " FROM market_canonical_image_acceptance ca"
    " WHERE ca.variant_id=%s"
    "  AND NOT EXISTS (SELECT 1 FROM market_canonical_image_acceptance newer"
    "                  WHERE newer.supersedes_acceptance_id=ca.id)"
    " ORDER BY ca.accepted_at DESC, ca.id DESC LIMIT 1"
)
# The head's own (variant, content sha), resolved in SQL from its asset, so
# the pair is never whatever a caller believed the head showed.
HEAD_APPROVAL_SQL = (
    "SELECT 1 FROM market_image_review_approval rv"
    " INNER JOIN market_image_asset a"
    "   ON a.variant_id=rv.variant_id AND a.content_sha256=rv.content_sha256"
    " INNER JOIN market_canonical_image_acceptance head"
    "   ON head.image_asset_id=a.id AND head.variant_id=a.variant_id"
    " WHERE head.id=%s LIMIT 1"
)
TCG_SQL = "SELECT tcg_code FROM catalog_printing_identity WHERE variant_id=%s"
# Two shapes, both the ones the lanes used before: storefront lineage (SNK EN)
# and the projection view's fallback branch (storefront_lineage_id NULL).
INSERT_STOREFRONT_SQL = (
    "INSERT INTO market_canonical_image_acceptance"
    " (variant_id,storefront_lineage_id,image_asset_id,lineage_sha256,"
    "  evidence_sha256,accepted_by,accepted_at,supersedes_acceptance_id)"
    " VALUES (%s,%s,%s,%s,%s,%s,%s,%s)"
)
INSERT_FALLBACK_SQL = (
    "INSERT INTO market_canonical_image_acceptance"
    " (variant_id,storefront_lineage_id,image_asset_id,fallback_source_path,"
    "  fallback_source_version_sha256,fallback_source_observed_at,"
    "  lineage_sha256,evidence_sha256,accepted_by,accepted_at,"
    "  supersedes_acceptance_id)"
    " VALUES (%s,NULL,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
)
REUSE_LINEAGE_SUFFIX = " ON DUPLICATE KEY UPDATE id=LAST_INSERT_ID(id)"
LINEAGE_ID_SQL = "SELECT id FROM market_canonical_image_acceptance WHERE lineage_sha256=%s"


class ImageLanePolicyError(RuntimeError):
    """A caller broke the contract (bad input, asset/sha mismatch): always raised."""


class ImageLaneHeld(RuntimeError):
    """Raised for a held decision only when the caller passed raise_on_hold=True."""

    def __init__(self, decision: "ImageLaneDecision") -> None:
        self.decision = decision
        super().__init__(
            f"image lane held: variant {decision.variant_id}"
            f" {decision.accepted_by}: {decision.reason}"
            f" (head acceptance {decision.head_acceptance_id})"
        )


@dataclass(frozen=True)
class ImageLaneDecision:
    """status: 'accepted' (new row), 'current' (same lineage is head), 'held'."""

    status: str
    reason: str
    variant_id: int
    accepted_by: str
    acceptance_id: int | None
    head_acceptance_id: int | None
    inserted: bool

    @property
    def held(self) -> bool:
        return self.status == "held"


def _is_human(accepted_by: str) -> bool:
    return accepted_by == HUMAN_IMAGE_ACCEPTED_BY


def _is_snk_lane(*, accepted_by: str, storefront_lineage_id: Any,
                 fallback_source_path: Any) -> bool:
    """SNK by the data it writes, not by a flag a caller could forget.

    storefront_lineage_id points into market_snk_en_storefront_lineage (the
    only storefront lineage table); a fallback path naming snkrdunk (the
    projection view's own test) or an actor naming snk counts too -- widening
    only ever holds more.  Not bare "snk" in the path: PC image ids are random
    alphanumerics and would match by chance.
    """

    return (
        storefront_lineage_id is not None
        or "snkrdunk" in str(fallback_source_path or "").lower()
        or "snk" in str(accepted_by).lower()
    )


def _asset_id(cursor, variant_id: int, content_sha256: str) -> int | None:
    cursor.execute(ASSET_SQL, (variant_id, content_sha256))
    row = cursor.fetchone()
    return int(row["id"]) if row else None


def _rejected(cursor, variant_id: int, content_sha256: str) -> bool:
    cursor.execute(REJECTION_SQL, (variant_id, content_sha256))
    return cursor.fetchone() is not None


def _head(cursor, variant_id: int) -> dict[str, Any] | None:
    cursor.execute(HEAD_SQL, (variant_id,))
    row = cursor.fetchone()
    return dict(row) if row else None


def _head_review_approved(cursor, head_acceptance_id: int) -> bool:
    cursor.execute(HEAD_APPROVAL_SQL, (head_acceptance_id,))
    return cursor.fetchone() is not None


def _tcg_code(cursor, variant_id: int) -> str | None:
    cursor.execute(TCG_SQL, (variant_id,))
    row = cursor.fetchone()
    value = (row or {}).get("tcg_code")
    return str(value).strip().lower() if value is not None else None


def accept_canonical_image(
    cursor,
    *,
    variant_id: int,
    image_asset_id: int,
    content_sha256: str,
    lineage_sha256: str,
    evidence_sha256: str,
    accepted_by: str,
    accepted_at: Any,
    storefront_lineage_id: int | None = None,
    fallback_source_path: str | None = None,
    fallback_source_version_sha256: str | None = None,
    fallback_source_observed_at: Any = None,
    reuse_existing_lineage: bool = False,
    raise_on_hold: bool = False,
) -> ImageLaneDecision:
    """Insert one canonical image acceptance, or say why not.

    Supersedes the variant's current head (read here, on the caller's cursor
    and transaction).  reuse_existing_lineage keeps new_era_db_tidy's
    ON DUPLICATE KEY behaviour (an existing lineage row's id comes back).
    """

    if isinstance(variant_id, bool) or not isinstance(variant_id, int) or variant_id <= 0:
        raise ImageLanePolicyError(f"variant_id must be a positive int: {variant_id!r}")
    if isinstance(image_asset_id, bool) or not isinstance(image_asset_id, int) or image_asset_id <= 0:
        raise ImageLanePolicyError(f"image_asset_id must be a positive int: {image_asset_id!r}")
    if not isinstance(content_sha256, str) or not _HEX64.fullmatch(content_sha256):
        raise ImageLanePolicyError(f"content_sha256 must be 64 lowercase hex: {content_sha256!r}")
    if not isinstance(accepted_by, str) or not accepted_by.strip():
        raise ImageLanePolicyError("accepted_by must be a non-empty string")
    # The pair the rules check must be the content the row will point at.
    found = _asset_id(cursor, variant_id, content_sha256)
    if found != image_asset_id:
        raise ImageLanePolicyError(
            f"variant {variant_id}: asset {image_asset_id} is not the raw_front"
            f" asset of content {content_sha256} (found {found})"
        )

    human = _is_human(accepted_by)
    head = _head(cursor, variant_id)
    head_id = int(head["id"]) if head else None

    def decision(status: str, reason: str, acceptance_id: int | None = None,
                 inserted: bool = False) -> ImageLaneDecision:
        result = ImageLaneDecision(
            status=status, reason=reason, variant_id=variant_id,
            accepted_by=accepted_by, acceptance_id=acceptance_id,
            head_acceptance_id=head_id, inserted=inserted,
        )
        if result.held and raise_on_hold:
            raise ImageLaneHeld(result)
        return result

    # Rule 1: every lane, human included.
    if _rejected(cursor, variant_id, content_sha256):
        return decision("held", HELD_REJECTED_CONTENT)
    if head is not None and str(head.get("lineage_sha256") or "") == lineage_sha256:
        return decision("current", "same_lineage_is_head", acceptance_id=head_id)

    if head is not None and not human:
        # Rule 2
        if _is_human(str(head.get("accepted_by") or "")):
            return decision("held", HELD_HEAD_IS_HUMAN)
        # Rule 3
        if _head_review_approved(cursor, head_id):
            return decision("held", HELD_HEAD_REVIEW_APPROVED)
        # Rule 4
        if _is_snk_lane(
            accepted_by=accepted_by,
            storefront_lineage_id=storefront_lineage_id,
            fallback_source_path=fallback_source_path,
        ) and _tcg_code(cursor, variant_id) in (ONE_PIECE_TCG_CODE, None):
            return decision("held", HELD_ONE_PIECE_SNK)

    suffix = REUSE_LINEAGE_SUFFIX if reuse_existing_lineage else ""
    if storefront_lineage_id is not None:
        cursor.execute(
            INSERT_STOREFRONT_SQL + suffix,
            (variant_id, storefront_lineage_id, image_asset_id, lineage_sha256,
             evidence_sha256, accepted_by, accepted_at, head_id),
        )
    else:
        cursor.execute(
            INSERT_FALLBACK_SQL + suffix,
            (variant_id, image_asset_id, fallback_source_path,
             fallback_source_version_sha256, fallback_source_observed_at,
             lineage_sha256, evidence_sha256, accepted_by, accepted_at, head_id),
        )
    # A plain INSERT that returned inserted a row; ON DUPLICATE KEY reports 1
    # only for a new row.
    inserted = (not reuse_existing_lineage) or int(cursor.rowcount or 0) == 1
    acceptance_id = int(cursor.lastrowid or 0)
    if acceptance_id <= 0 and reuse_existing_lineage:
        cursor.execute(LINEAGE_ID_SQL, (lineage_sha256,))
        acceptance_id = int((cursor.fetchone() or {}).get("id") or 0)
    if acceptance_id <= 0:
        raise ImageLanePolicyError(
            f"variant {variant_id}: acceptance insert returned no id"
        )
    return decision("accepted", "superseded_head" if head_id else "filled",
                    acceptance_id=acceptance_id, inserted=inserted)
