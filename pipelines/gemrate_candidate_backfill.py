#!/usr/bin/env python3
"""Resolve GemRate candidate POP from exact GemRate and Grade10 transports.

This is a private backfill state machine, not a ranking importer.  Direct
GemRate payloads are preferred and may carry history.  The Grade10-held
GemRate mirror may supply a current PSA 10 POP only when a candidate has an
exact storage path; it can never manufacture population history.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from gemrate_source import (
    DIRECT_TRANSPORT,
    MIRROR_TRANSPORT,
    WEBSITE_TRANSPORT,
    _direct_population,
    _mirror_population,
    _website_population,
    collect_public_card_details,
)
from source_crosswalk import (
    build_gemrate_direct_identity_proposal,
    build_gemrate_receipt_identity_proposal,
    canonical_language,
    normalized_identity_part,
    same_collector_number_evidence,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_UNIVERSE = ROOT / "data/runtime/private-source-map/active-universe.json"
DEFAULT_ONE_PIECE = ROOT / "data/runtime/private-source-map/one-piece-gemrate-candidates.json"
DEFAULT_CROSSWALK = ROOT / "data/runtime/private-source-map/source-crosswalk.json"
DEFAULT_DIRECT = ROOT / "data/private/gemrate/cards"
DEFAULT_PUBLIC = DEFAULT_DIRECT
DEFAULT_FREEZE_MANIFEST = ROOT / "manifests/g10-full-freeze.json"
DEFAULT_LANDING = ROOT / "data/runtime/private-landing"
DEFAULT_OUT = ROOT / "data/runtime/private-source-map/gemrate-candidate-backfill"
DEFAULT_RECEIPT_MAPPINGS = ROOT / "data/runtime/private-source-map/gemrate-receipt-mappings.json"
HEX40 = re.compile(r"^[0-9a-f]{40}$", re.IGNORECASE)
HEX64 = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _atomic_write_text(path: Path, content: str) -> None:
    """Durably replace a run artifact without corrupting its last good version."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, text=True
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    _atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2))


def _is_exact_id(value: object) -> bool:
    return isinstance(value, str) and bool(HEX40.fullmatch(value))


def _safe_segment(value: object) -> str | None:
    if not isinstance(value, str) or not value or value in {".", ".."} or "/" in value or "\\" in value:
        return None
    return value


def resolve_immutable_mirror_root(freeze_manifest: Path, landing_root: Path) -> Path:
    """Resolve only the immutable G10 payload proven by the full-freeze manifest."""

    proof = _load_json(freeze_manifest)
    if proof is None:
        raise RuntimeError(f"G10 full-freeze manifest is missing or invalid: {freeze_manifest}")
    run_id = proof.get("runId")
    if not isinstance(run_id, str) or not HEX64.fullmatch(run_id):
        raise RuntimeError(f"G10 full-freeze manifest has invalid runId: {freeze_manifest}")
    expected_hash = proof.get("landingManifestSha256")
    if not isinstance(expected_hash, str) or not HEX64.fullmatch(expected_hash):
        raise RuntimeError(f"G10 full-freeze manifest has invalid landing manifest hash: {freeze_manifest}")
    run_root = landing_root / "g10" / "full" / run_id
    landing_manifest = run_root / "manifest.json"
    payload_root = run_root / "payload"
    if not landing_manifest.is_file() or not payload_root.is_dir():
        raise RuntimeError(
            "G10 immutable mirror payload is absent for the freeze run; "
            f"expected {payload_root}"
        )
    actual_hash = hashlib.sha256(landing_manifest.read_bytes()).hexdigest()
    if actual_hash != expected_hash:
        raise RuntimeError(
            "G10 immutable mirror landing manifest hash mismatch; "
            f"expected {expected_hash}, got {actual_hash}"
        )
    return payload_root


def _crosswalk_by_gemrate(document: Mapping[str, Any]) -> dict[str, Mapping[str, Any] | None]:
    records = document.get("cards")
    if not isinstance(records, list):
        raise ValueError("crosswalk cards are missing")
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for row in records:
        if isinstance(row, Mapping) and _is_exact_id(row.get("gemrateId")):
            grouped.setdefault(str(row["gemrateId"]), []).append(row)
    return {gemrate_id: rows[0] if len(rows) == 1 else None for gemrate_id, rows in grouped.items()}


def _mapping_identity_key(row: Mapping[str, Any]) -> str:
    key = row.get("canonicalPrintingKey")
    return str(key).strip() if isinstance(key, str) else ""


def merge_receipt_mappings(
    crosswalk: Mapping[str, Any], receipt_mappings: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Overlay durable exact GemRate receipts without allowing an ID rebind.

    The frozen Grade10 crosswalk remains the base.  Public-card receipts only
    add identities that were absent from it.  A second receipt claiming a
    different printing for an existing GemRate opaque ID is a hard data error,
    never an implicit remap.
    """

    base_cards = crosswalk.get("cards")
    if not isinstance(base_cards, list):
        raise ValueError("crosswalk cards are missing")
    merged = [dict(row) for row in base_cards if isinstance(row, Mapping)]
    if receipt_mappings is None:
        return {**dict(crosswalk), "cards": merged}
    schema_version = receipt_mappings.get("schemaVersion", 1)
    if schema_version not in {1, 2}:
        raise ValueError("receipt mapping schema is unsupported")
    mapped_cards = receipt_mappings.get("cards")
    if not isinstance(mapped_cards, list):
        raise ValueError("receipt mapping cards are missing")
    _validate_receipt_aliases(receipt_mappings)
    existing: dict[str, str] = {}
    for row in merged:
        gemrate_id = str(row.get("gemrateId") or "")
        key = _mapping_identity_key(row)
        if _is_exact_id(gemrate_id) and key:
            previous = existing.get(gemrate_id)
            if previous is not None and previous != key:
                raise RuntimeError(f"GemRate ID rebind blocked for {gemrate_id}")
            existing[gemrate_id] = key
    for raw in mapped_cards:
        if not isinstance(raw, Mapping):
            raise ValueError("receipt mapping card is invalid")
        row = dict(raw)
        gemrate_id = str(row.get("gemrateId") or "")
        key = _mapping_identity_key(row)
        if not _is_exact_id(gemrate_id) or not key or row.get("identityStatus") != "confirmed":
            raise ValueError("receipt mapping card is incomplete")
        known = existing.get(gemrate_id)
        if known is not None:
            if known != key:
                raise RuntimeError(f"GemRate ID rebind blocked for {gemrate_id}")
            continue
        # A base review row has no settled printing key.  Once a verified
        # receipt settles that same opaque ID, replace the review evidence in
        # the active overlay instead of retaining two rows that would create a
        # permanent duplicate-ID conflict.  The frozen raw crosswalk remains
        # unchanged on disk for provenance.
        merged = [card for card in merged if str(card.get("gemrateId") or "") != gemrate_id]
        existing[gemrate_id] = key
        merged.append(row)
    merged.sort(key=lambda row: (str(row.get("market") or ""), str(row.get("gemrateId") or "")))
    return {**dict(crosswalk), "cards": merged}


def _receipt_mapping_card(candidate: Mapping[str, Any]) -> dict[str, Any] | None:
    if candidate.get("identityStatus") != "exact_confirmed":
        return None
    source = candidate.get("canonicalSource")
    identity = candidate.get("canonicalIdentity")
    if not isinstance(source, Mapping) or not isinstance(identity, Mapping):
        return None
    if source.get("sourceCode") not in {"gemrate_public_card_page", "gemrate_direct"}:
        return None
    gemrate_id = str(candidate.get("gemrateId") or "")
    key = str(candidate.get("canonicalPrintingKey") or "")
    if not _is_exact_id(gemrate_id) or not key:
        return None
    return {
        "canonicalSourceCode": source["sourceCode"],
        "canonicalExternalId": gemrate_id,
        "storageScope": source.get("storageScope"),
        "market": identity.get("tcg"),
        "gemrateId": gemrate_id,
        "snkItemId": None,
        "name": candidate.get("nameEvidence") or "",
        "collectorNumberRaw": identity.get("collectorNumber"),
        "language": identity.get("language"),
        "setName": identity.get("setName"),
        "edition": identity.get("edition"),
        "parallel": identity.get("parallel"),
        "finish": identity.get("finish"),
        "canonicalPrintingKey": key,
        "identityStatus": "confirmed",
        "reviewReasons": [],
    }


def _direct_receipt_aliases(candidate: Mapping[str, Any]) -> list[dict[str, Any]]:
    receipt = candidate.get("directIdentityReceipt")
    if not isinstance(receipt, Mapping):
        return []
    requested = str(receipt.get("requestedGemrateId") or "")
    entity = str(receipt.get("entityGemrateId") or "")
    candidate_id = str(candidate.get("gemrateId") or "")
    digest = str(receipt.get("payloadSha256") or "")
    canonical_key = str(candidate.get("canonicalPrintingKey") or "")
    source_pointer = str(receipt.get("sourcePointer") or "")
    fetched_at = str(receipt.get("fetchedAt") or "")
    if (
        not _is_exact_id(requested)
        or requested != candidate_id
        or entity != requested
        or not HEX64.fullmatch(digest)
        or not canonical_key
    ):
        raise ValueError("confirmed GemRate receipt alias evidence is inconsistent")
    if not source_pointer or Path(source_pointer).is_absolute() or ".." in Path(source_pointer).parts:
        raise ValueError("confirmed GemRate receipt alias source pointer is invalid")
    try:
        parsed_fetched_at = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("confirmed GemRate receipt alias fetchedAt is invalid") from error
    if parsed_fetched_at.tzinfo is None:
        raise ValueError("confirmed GemRate receipt alias fetchedAt is invalid")
    if candidate.get("identityStatus") != "exact_confirmed":
        return []
    status = str(candidate.get("identityStatus") or "review")
    aliases: list[dict[str, Any]] = []
    def add(kind: str, value: object, grader: object = None) -> None:
        alias = str(value or "")
        if not alias:
            return
        aliases.append({
            "aliasType": kind, "aliasValue": alias,
            "receiptPayloadSha256": digest, "requestedGemrateId": requested,
            "grader": grader if isinstance(grader, str) and grader else None,
            "canonicalPrintingKey": canonical_key,
            "sourcePointer": source_pointer,
            "fetchedAt": fetched_at,
            "status": status,
        })
    add("entity", entity)
    add("universal", receipt.get("universalGemrateId"))
    graders = receipt.get("graders")
    if isinstance(graders, Mapping):
        for grader, member in graders.items():
            if not isinstance(member, Mapping):
                continue
            add("grader_member", member.get("gemrateId"), grader)
            add("spec", member.get("specId"), grader)
    return aliases


def _alias_semantic_key(raw: Mapping[str, Any]) -> tuple[str, str, str]:
    kind = str(raw.get("aliasType") or "")
    grader = str(raw.get("grader") or "").casefold() if kind in {"grader_member", "spec"} else ""
    return kind, str(raw.get("aliasValue") or ""), grader


def _alias_association_key(raw: Mapping[str, Any]) -> tuple[str, str, str, str]:
    return (*_alias_semantic_key(raw), str(raw.get("requestedGemrateId") or ""))


def _validate_receipt_aliases(document: Mapping[str, Any]) -> dict[tuple[str, str, str], str]:
    aliases = document.get("gemrateAliases", [])
    if not isinstance(aliases, list):
        raise ValueError("receipt mapping aliases are invalid")
    known: dict[tuple[str, str, str], str] = {}
    associations: set[tuple[str, str, str, str]] = set()
    for raw in aliases:
        if not isinstance(raw, Mapping):
            raise ValueError("receipt mapping alias is invalid")
        kind = str(raw.get("aliasType") or "")
        value = str(raw.get("aliasValue") or "")
        requested = str(raw.get("requestedGemrateId") or "")
        digest = str(raw.get("receiptPayloadSha256") or "")
        canonical_key = str(raw.get("canonicalPrintingKey") or "")
        grader = str(raw.get("grader") or "")
        if (
            kind not in {"entity", "universal", "grader_member", "spec"}
            or not value
            or not _is_exact_id(requested)
            or not HEX64.fullmatch(digest)
            or not canonical_key
            or (kind in {"grader_member", "spec"} and not grader)
            or (kind not in {"grader_member", "spec"} and grader)
        ):
            raise ValueError("receipt mapping alias is incomplete")
        key = _alias_semantic_key(raw)
        previous = known.get(key)
        if previous is not None and previous != canonical_key:
            raise RuntimeError(f"GemRate alias rebind blocked for {kind}:{value}")
        association = _alias_association_key(raw)
        if association in associations:
            raise ValueError("receipt mapping alias association is duplicated")
        associations.add(association)
        known[key] = canonical_key
    return known


def persist_receipt_identity_mappings(
    candidates: Iterable[Mapping[str, Any]], *, path: Path,
) -> dict[str, int]:
    """Persist confirmed receipt identities as a reusable private crosswalk overlay."""

    prior = _load_json(path) if path.is_file() else None
    if prior is None:
        prior = {"schemaVersion": 2, "cards": [], "gemrateAliases": []}
    existing = merge_receipt_mappings({"cards": []}, prior)
    known = {str(row.get("gemrateId")): _mapping_identity_key(row) for row in existing["cards"]}
    cards = [dict(row) for row in existing["cards"]]
    aliases = [dict(row) for row in prior.get("gemrateAliases", [])] if isinstance(prior.get("gemrateAliases", []), list) else []
    known_aliases = _validate_receipt_aliases({"gemrateAliases": aliases})
    alias_positions = {
        _alias_association_key(alias): index
        for index, alias in enumerate(aliases)
    }
    added = 0
    aliases_added = 0
    aliases_refreshed = 0
    for candidate in candidates:
        for alias in _direct_receipt_aliases(candidate):
            alias_key = _alias_semantic_key(alias)
            association_key = _alias_association_key(alias)
            previous_alias = known_aliases.get(alias_key)
            if previous_alias is not None:
                if previous_alias != str(alias["canonicalPrintingKey"]):
                    raise RuntimeError(f"GemRate alias rebind blocked for {alias_key[0]}:{alias_key[1]}")
            else:
                known_aliases[alias_key] = str(alias["canonicalPrintingKey"])
            position = alias_positions.get(association_key)
            if position is not None:
                if aliases[position] != alias:
                    aliases[position] = alias
                    aliases_refreshed += 1
                continue
            alias_positions[association_key] = len(aliases)
            aliases.append(alias)
            aliases_added += 1
        row = _receipt_mapping_card(candidate)
        if row is None:
            continue
        gemrate_id = str(row["gemrateId"])
        key = _mapping_identity_key(row)
        previous = known.get(gemrate_id)
        if previous is not None:
            if previous != key:
                raise RuntimeError(f"GemRate ID rebind blocked for {gemrate_id}")
            continue
        known[gemrate_id] = key
        cards.append(row)
        added += 1
    cards.sort(key=lambda row: (str(row.get("market") or ""), str(row.get("gemrateId") or "")))
    if added or aliases_added or aliases_refreshed or prior.get("schemaVersion") != 2:
        _atomic_write_json(path, {
            "schemaVersion": 2,
            "generatedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "cards": cards,
            "gemrateAliases": sorted(
                aliases,
                key=lambda row: (
                    str(row.get("aliasType")),
                    str(row.get("aliasValue")),
                    str(row.get("grader") or ""),
                    str(row.get("requestedGemrateId") or ""),
                ),
            ),
        })
    return {
        "added": added,
        "total": len(cards),
        "aliasesAdded": aliases_added,
        "aliasesRefreshed": aliases_refreshed,
        "aliasesTotal": len(aliases),
    }


_CANDIDATE_IDENTITY_EVIDENCE = (
    "tcg",
    "collectorNumberEvidence",
    "setEvidence",
    "languageEvidence",
    "editionEvidence",
    "parallelEvidence",
    "finishEvidence",
)


def _same_candidate_identity_evidence(field: str, left: Any, right: Any) -> bool:
    """Compare supplied evidence without treating language aliases as conflicts."""

    if field == "collectorNumberEvidence":
        return same_collector_number_evidence(left, right)
    if field == "languageEvidence":
        left_language = canonical_language(left)
        right_language = canonical_language(right)
        if left_language and right_language:
            return left_language == right_language
    return normalized_identity_part(left) == normalized_identity_part(right)


def _add_roster_candidate(
    chosen: dict[str, dict[str, Any]], candidate: Mapping[str, Any], *, origin: str,
) -> None:
    """Merge duplicate opaque IDs or hold conflicting evidence for review.

    A GemRate opaque ID is a durable source identity.  Multiple discovery
    inputs may fill missing evidence, but they must never replace a populated
    identity field with a different value.
    """

    gemrate_id = str(candidate["gemrateId"])
    incoming = dict(candidate)
    current = chosen.get(gemrate_id)
    if current is None:
        incoming["candidateSources"] = [origin]
        chosen[gemrate_id] = incoming
        return
    sources = {str(value) for value in current.get("candidateSources", []) if str(value)}
    sources.add(origin)
    current["candidateSources"] = sorted(sources)
    conflicts = set(str(value) for value in current.get("duplicateGemrateIdReviewReasons", []) if str(value))
    for field in _CANDIDATE_IDENTITY_EVIDENCE:
        existing = current.get(field)
        proposed = incoming.get(field)
        if existing in (None, "") and proposed not in (None, ""):
            current[field] = proposed
        elif existing not in (None, "") and proposed not in (None, "") and not (
            _same_candidate_identity_evidence(field, existing, proposed)
        ):
            conflicts.add(f"duplicate_gemrate_id_{field}_conflict")
    if conflicts:
        current["duplicateGemrateIdReviewReasons"] = sorted(conflicts)


def build_candidate_roster(
    active_universe: Mapping[str, Any],
    one_piece_discovery: Mapping[str, Any],
    crosswalk: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Build a deduplicated Pokémon + One Piece roster without search POP values."""

    crosswalk_by_id = _crosswalk_by_gemrate(crosswalk)
    chosen: dict[str, dict[str, Any]] = {}
    # The frozen Grade10 crosswalk is the full Pokémon candidate reservoir.
    # ``active-universe`` is only the current ranked/watch subset, so using it
    # alone would silently omit valid Pokémon monitoring candidates.
    crosswalk_cards = crosswalk.get("cards")
    if not isinstance(crosswalk_cards, list):
        raise ValueError("crosswalk cards are missing")
    for row in crosswalk_cards:
        if not isinstance(row, Mapping) or str(row.get("market")) != "pokemon" or not _is_exact_id(row.get("gemrateId")):
            continue
        gemrate_id = str(row["gemrateId"])
        _add_roster_candidate(chosen, {
            "gemrateId": gemrate_id,
            "tcg": "pokemon",
            "nameEvidence": row.get("name"),
            "collectorNumberEvidence": row.get("collectorNumberRaw"),
            "setEvidence": row.get("setName"),
            "languageEvidence": row.get("language"),
            "editionEvidence": row.get("edition"),
            "parallelEvidence": row.get("parallel"),
            "finishEvidence": row.get("finish"),
        }, origin="g10_crosswalk")
    active_cards = active_universe.get("cards")
    if not isinstance(active_cards, list):
        raise ValueError("active universe cards are missing")
    for row in active_cards:
        if not isinstance(row, Mapping) or str(row.get("tcg")) != "pokemon" or not _is_exact_id(row.get("gemrateId")):
            continue
        gemrate_id = str(row["gemrateId"])
        _add_roster_candidate(chosen, {
            "gemrateId": gemrate_id,
            "tcg": "pokemon",
            "nameEvidence": row.get("name"),
            "collectorNumberEvidence": row.get("collectorNumber"),
            "setEvidence": row.get("setName"),
            "languageEvidence": row.get("language"),
            "editionEvidence": row.get("edition"),
            "parallelEvidence": row.get("parallel"),
            "finishEvidence": row.get("finish"),
        }, origin="active_universe")
    discovery_cards = one_piece_discovery.get("candidates")
    if not isinstance(discovery_cards, list):
        raise ValueError("One Piece discovery candidates are missing")
    for row in discovery_cards:
        if not isinstance(row, Mapping) or not _is_exact_id(row.get("gemrateId")):
            continue
        gemrate_id = str(row["gemrateId"])
        candidate = {
            "gemrateId": gemrate_id,
            "tcg": "one-piece",
            "nameEvidence": row.get("nameEvidence"),
            "collectorNumberEvidence": row.get("collectorNumberEvidence"),
            "setEvidence": row.get("setEvidence"),
            "parallelEvidence": row.get("parallelEvidence"),
            "languageEvidence": row.get("languageEvidence"),
            "descriptionEvidence": row.get("descriptionEvidence"),
        }
        for field in ("editionEvidence", "finishEvidence"):
            if field in row:
                candidate[field] = row.get(field)
        _add_roster_candidate(chosen, candidate, origin="one_piece_discovery")
    roster: list[dict[str, Any]] = []
    for gemrate_id, candidate in sorted(chosen.items(), key=lambda item: (item[1]["tcg"], item[0])):
        collision_reasons = candidate.get("duplicateGemrateIdReviewReasons")
        mapping = crosswalk_by_id.get(gemrate_id)
        if isinstance(collision_reasons, list) and collision_reasons:
            candidate["identityStatus"] = "review"
            candidate["identity"] = None
            candidate["identityReviewReasons"] = list(collision_reasons)
        elif mapping is None and gemrate_id in crosswalk_by_id:
            candidate["identityStatus"] = "review"
            candidate["identity"] = None
        elif mapping is None:
            candidate["identityStatus"] = "unmapped"
            candidate["identity"] = None
        else:
            canonical_identity = {
                "tcg": str(mapping.get("market") or candidate["tcg"]),
                "setName": str(mapping.get("setName") or "").strip(),
                "collectorNumber": str(mapping.get("collectorNumberRaw") or "").strip(),
                "language": str(mapping.get("language") or "").strip().casefold(),
                "edition": str(mapping.get("edition") or "").strip(),
                "parallel": str(mapping.get("parallel") or "").strip(),
                "finish": str(mapping.get("finish") or "").strip(),
            }
            canonical_source = {
                "sourceCode": mapping.get("canonicalSourceCode"),
                "externalId": mapping.get("canonicalExternalId"),
                "storageScope": mapping.get("storageScope"),
                "snkItemId": mapping.get("snkItemId"),
            }
            candidate["identityStatus"] = "exact_confirmed"
            candidate["canonicalIdentity"] = canonical_identity
            candidate["canonicalSource"] = canonical_source
            candidate["canonicalPrintingKey"] = mapping.get("canonicalPrintingKey")
            # Flat fields retain compatibility with the exact SNK worklist.
            candidate.update({
                "canonicalSourceCode": canonical_source["sourceCode"],
                "canonicalExternalId": canonical_source["externalId"],
                "storageScope": canonical_source["storageScope"],
                "snkItemId": canonical_source["snkItemId"],
                **canonical_identity,
            })
            candidate["identity"] = {
                "storageScope": mapping.get("storageScope"),
                "canonicalExternalId": mapping.get("canonicalExternalId"),
            }
        roster.append(candidate)
    return roster


def apply_public_receipt_identity_proposals(
    candidates: Iterable[Mapping[str, Any]], *, public_root: Path, direct_root: Path | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Resolve identity from a direct receipt first, then public page evidence.

    Existing G10 crosswalk confirmations are not overwritten.  Each receipt is
    an opaque-ID-bound proposal and yields either an exact canonical identity
    or a compact review row.  This lets new GemRate discovery evidence enter
    the same review/confirmation lane instead of being permanently limited to
    the frozen G10 600-card universe.
    """

    resolved: list[dict[str, Any]] = []
    reviews: list[dict[str, Any]] = []
    for raw in candidates:
        candidate = dict(raw)
        candidate.setdefault("identityStatus", "unmapped")
        status = str(candidate.get("identityStatus") or "")
        gemrate_id = str(candidate.get("gemrateId") or "")
        public_receipt, public_error = _verified_public_card_receipt(
            public_root / gemrate_id / "card_details.json", gemrate_id
        )
        page = public_receipt.get("publicCardPage") if isinstance(public_receipt, Mapping) else None
        direct_receipt, direct_payload, direct_error = _verified_direct_identity_receipt(direct_root, gemrate_id) if direct_root else (None, None, None)
        proposal: Mapping[str, Any] | None = None
        if direct_error:
            proposal = {"identityStatus": "review", "reviewReasons": [direct_error], "identityEvidence": {"directReceipt": {"error": direct_error}}}
        elif direct_receipt is not None and direct_payload is not None:
            candidate["directIdentityReceipt"] = dict(direct_receipt)
            proposal = build_gemrate_direct_identity_proposal(candidate, direct_receipt, direct_payload)
            candidate["directIdentityProposal"] = proposal
            if isinstance(page, Mapping) and isinstance(page.get("identity"), Mapping):
                public_proposal = build_gemrate_receipt_identity_proposal(candidate, public_receipt)
                candidate["publicIdentityProposal"] = public_proposal
                direct_key = str(proposal.get("canonicalPrintingKey") or "")
                public_key = str(public_proposal.get("canonicalPrintingKey") or "")
                public_conflicts = {
                    "set_conflict", "collector_number_conflict", "parallel_conflict", "language_conflict",
                }.intersection(str(reason) for reason in public_proposal.get("reviewReasons", []))
                if direct_key and ((public_key and direct_key != public_key) or public_conflicts):
                    proposal = {**dict(proposal), "identityStatus": "review", "canonicalIdentity": None,
                                "canonicalPrintingKey": None,
                                "reviewReasons": sorted(set([*proposal.get("reviewReasons", []), "gemrate_direct_public_identity_conflict"]))}
        elif isinstance(page, Mapping) and isinstance(page.get("identity"), Mapping):
            proposal = build_gemrate_receipt_identity_proposal(candidate, public_receipt)
            candidate["publicIdentityProposal"] = proposal
        elif public_error:
            proposal = {
                "identityStatus": "review",
                "reviewReasons": [public_error],
                "identityEvidence": {"publicReceipt": {"error": public_error}},
            }
        if proposal is None:
            resolved.append(candidate)
            continue
        candidate["identityProposal"] = proposal
        previous_key = str(candidate.get("canonicalPrintingKey") or "").strip()
        proposal_key = str(proposal.get("canonicalPrintingKey") or "").strip()
        if proposal.get("identityStatus") != "exact_confirmed":
            candidate["identityStatus"] = "review"
            candidate["identityReviewReasons"] = list(proposal.get("reviewReasons", []))
        elif status == "exact_confirmed" and (not previous_key or previous_key != proposal_key):
            candidate["identityStatus"] = "review"
            candidate["identityReviewReasons"] = ["gemrate_receipt_identity_conflict"]
        elif status in {"", "unmapped", "exact_confirmed"} or (
            status == "review"
            and not previous_key
            and not candidate.get("identityReviewReasons")
            and not candidate.get("duplicateGemrateIdReviewReasons")
        ):
            identity = proposal["canonicalIdentity"]
            source = proposal["canonicalSource"]
            assert isinstance(identity, Mapping) and isinstance(source, Mapping)
            candidate.update({"identityStatus": "exact_confirmed", "canonicalIdentity": dict(identity),
                              "canonicalSource": dict(source), "canonicalSourceCode": source["sourceCode"],
                              "canonicalExternalId": source["externalId"], "storageScope": source["storageScope"],
                              "snkItemId": None, "canonicalPrintingKey": proposal["canonicalPrintingKey"], **dict(identity)})
        if candidate.get("identityStatus") == "review":
            reviews.append({"gemrateId": gemrate_id, "tcg": candidate.get("tcg"),
                            "reasons": list(candidate.get("identityReviewReasons", [])),
                            "evidence": proposal.get("identityEvidence", {})})
        resolved.append(candidate)
    reviews.sort(key=lambda row: (str(row["tcg"]), str(row["gemrateId"])))
    return resolved, reviews


def _load_json(path: Path) -> Mapping[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, Mapping) else None


def _verified_direct_identity_receipt(
    direct_root: Path | None, gemrate_id: str,
) -> tuple[Mapping[str, Any] | None, Mapping[str, Any] | None, str | None]:
    if direct_root is None:
        return None, None, None
    card_dir = direct_root / gemrate_id
    receipt_path = card_dir / "identity.receipt.json"
    if not receipt_path.is_file():
        return (None, None, "direct_receipt_missing") if (card_dir / "population.json").is_file() else (None, None, None)
    receipt = _load_json(receipt_path)
    if receipt is None:
        return None, None, "direct_receipt_invalid"
    pointer = receipt.get("sourcePointer")
    if pointer != "population.json":
        return None, None, "direct_receipt_source_pointer_invalid"
    try:
        source_path = (card_dir / pointer).resolve()
        if card_dir.resolve() not in source_path.parents:
            return None, None, "direct_receipt_source_pointer_invalid"
    except OSError:
        return None, None, "direct_receipt_source_pointer_invalid"
    payload = _load_json(source_path)
    if payload is None:
        return None, None, "direct_receipt_source_payload_missing"
    digest = str(receipt.get("payloadSha256") or "")
    if not HEX64.fullmatch(digest) or hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest() != digest:
        return None, None, "direct_receipt_payload_sha256_mismatch"
    data = payload.get("data")
    if (
        str(receipt.get("requestedGemrateId") or "").casefold() != gemrate_id.casefold()
        or str(receipt.get("entityGemrateId") or "").casefold() != gemrate_id.casefold()
        or not isinstance(data, Mapping)
        or str(data.get("gemrate_id") or "").casefold() != gemrate_id.casefold()
    ):
        return None, None, "direct_receipt_entity_mismatch"
    return receipt, payload, None


def _verified_public_card_receipt(
    path: Path, gemrate_id: str,
) -> tuple[Mapping[str, Any] | None, str | None]:
    """Verify an exact public-card capture without imposing POP freshness."""

    receipt_path = path.with_name("card_details.raw.receipt.json")
    if not path.is_file() and not receipt_path.is_file():
        return None, None
    document = _load_json(path)
    sidecar_receipt = _load_json(receipt_path)
    if document is None or not isinstance(sidecar_receipt, Mapping):
        return None, "public_receipt_invalid"
    if str(document.get("gemrate_id") or "").casefold() != gemrate_id.casefold():
        return None, "public_receipt_entity_mismatch"
    private_receipt = document.get("privateSourceReceipt")
    if not isinstance(private_receipt, Mapping) or dict(private_receipt) != dict(sidecar_receipt):
        return None, "public_receipt_sidecar_mismatch"
    if (
        str(private_receipt.get("gemrateId") or "").casefold() != gemrate_id.casefold()
        or private_receipt.get("transport") != WEBSITE_TRANSPORT
    ):
        return None, "public_receipt_binding_mismatch"
    digest = str(private_receipt.get("contentSha256") or "")
    if not HEX64.fullmatch(digest):
        return None, "public_receipt_hash_invalid"
    page_metadata = document.get("publicCardPage")
    expected_url = f"https://www.gemrate.com/card/{gemrate_id}".casefold()
    if (
        not isinstance(page_metadata, Mapping)
        or page_metadata.get("routeVerified") is not True
        or str(page_metadata.get("canonicalUrl") or "").rstrip("/").casefold() != expected_url
        or not str(page_metadata.get("title") or "").strip()
    ):
        return None, "public_receipt_route_unverified"
    if private_receipt.get("rawStatus") == "captured":
        pointer = private_receipt.get("sourcePointer")
        if not isinstance(pointer, str) or not pointer or pointer.startswith(("/", "\\")):
            return None, "public_receipt_source_pointer_invalid"
        card_dir = path.parent.resolve()
        try:
            raw_path = (card_dir / pointer).resolve()
            if card_dir not in raw_path.parents or not raw_path.is_file():
                return None, "public_receipt_source_pointer_invalid"
            if hashlib.sha256(raw_path.read_bytes()).hexdigest() != digest:
                return None, "public_receipt_payload_sha256_mismatch"
        except OSError:
            return None, "public_receipt_source_pointer_invalid"
    elif private_receipt.get("rawStatus") == "dom_evidence_only":
        if (
            private_receipt.get("sourcePointer") is not None
            or private_receipt.get("populationMode") != "dom_labelled_fallback"
            or page_metadata.get("populationMode") != "dom_labelled_fallback"
            or page_metadata.get("domSha256") != digest
        ):
            return None, "public_receipt_dom_evidence_invalid"
    else:
        return None, "public_receipt_raw_status_invalid"
    observed_at = private_receipt.get("fetchedAt")
    if not isinstance(observed_at, str):
        return None, "public_receipt_fetched_at_invalid"
    try:
        observed = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    except ValueError:
        return None, "public_receipt_fetched_at_invalid"
    if observed.tzinfo is None:
        return None, "public_receipt_fetched_at_invalid"
    return document, None


def _path_fetched_at(path: Path, fallback: str) -> str:
    """Use the cache file's real write time when the payload has no source date."""

    try:
        observed = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    except OSError:
        return fallback
    return observed.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _is_fresh(point: Mapping[str, Any] | None, as_of: date, *, max_age_days: int = 2) -> bool:
    if point is None:
        return False
    try:
        effective = date.fromisoformat(str(point["effectiveDate"]))
    except (KeyError, TypeError, ValueError):
        return False
    age = (as_of - effective).days
    return 0 <= age <= max_age_days


def _direct_current(direct_root: Path, gemrate_id: str, fetched_at: str) -> dict[str, Any] | None:
    receipt, document, error = _verified_direct_identity_receipt(direct_root, gemrate_id)
    if error or receipt is None or document is None:
        return None
    observed_at = str(receipt.get("fetchedAt") or _path_fetched_at(direct_root / gemrate_id / "population.json", fetched_at))
    return _direct_population(document, observed_at)


def _public_current(path: Path, as_of: date, gemrate_id: str) -> dict[str, Any] | None:
    document, error = _verified_public_card_receipt(path, gemrate_id)
    if error or document is None:
        return None
    private_receipt = document["privateSourceReceipt"]
    observed_at = private_receipt.get("fetchedAt")
    observed = datetime.fromisoformat(str(observed_at).replace("Z", "+00:00"))
    receipt_age_days = (as_of - observed.date()).days
    if receipt_age_days < 0 or receipt_age_days > 2:
        return None
    point = _website_population(document, observed_at)
    return point if _is_fresh(point, as_of) else None


def _mirror_current(path: Path, as_of: date) -> dict[str, Any] | None:
    document = _load_json(path)
    fallback = f"{as_of.isoformat()}T00:00:00Z"
    return _mirror_population(document, _path_fetched_at(path, fallback)) if document is not None else None


def _exact_mirror_path(root: Path, candidate: Mapping[str, Any]) -> Path | None:
    identity = candidate.get("identity")
    if not isinstance(identity, Mapping):
        return None
    scope = _safe_segment(identity.get("storageScope"))
    external_id = _safe_segment(identity.get("canonicalExternalId"))
    if scope is None or external_id is None:
        return None
    return root / "cards" / scope / external_id / "populations.json"


def _checkpoint_rows(checkpoint: Mapping[str, Any] | None, as_of: date) -> dict[str, Mapping[str, Any]]:
    if not isinstance(checkpoint, Mapping):
        return {}
    try:
        checkpoint_date = date.fromisoformat(str(checkpoint["asOf"]))
    except (KeyError, TypeError, ValueError):
        return {}
    if not 0 <= (as_of - checkpoint_date).days <= 2:
        return {}
    rows = checkpoint.get("candidates")
    if not isinstance(rows, list):
        return {}
    return {
        str(row["gemrateId"]): row
        for row in rows
        if isinstance(row, Mapping)
        and _is_exact_id(row.get("gemrateId"))
        and str(row.get("status")) in {"resolved", "below-threshold"}
        and isinstance(row.get("populationPsa10"), int)
        and _is_fresh(row, as_of)
    }


def build_public_card_details_worklist(
    candidates: Iterable[Mapping[str, Any]],
    *,
    direct_root: Path,
    public_root: Path,
    as_of: date | None = None,
) -> dict[str, Any]:
    """Return a deterministic exact-ID worklist for keyless current POP repair.

    Search results remain discovery evidence only.  A candidate enters this
    worklist only through its pre-resolved GemRate ID and only when neither the
    direct cache nor existing public card-page cache has a current value.
    Identity conflicts stay review-only and are not sent to a collector.
    """

    rows = [dict(row) for row in candidates]
    if len({str(row.get("gemrateId")) for row in rows}) != len(rows):
        raise ValueError("candidate worklist contains duplicate GemRate IDs")
    if any(not _is_exact_id(row.get("gemrateId")) for row in rows):
        raise ValueError("candidate worklist requires exact GemRate IDs")

    as_of = as_of or date.today()
    worklist: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for candidate in sorted(rows, key=lambda row: (str(row.get("tcg")), str(row["gemrateId"]))):
        gemrate_id = str(candidate["gemrateId"])
        if str(candidate.get("identityStatus") or "") == "review":
            skipped.append({"gemrateId": gemrate_id, "reason": "identity_review"})
            continue
        fetched_at = "1970-01-01T00:00:00Z"
        direct = _direct_current(direct_root, gemrate_id, fetched_at)
        public = _public_current(public_root / gemrate_id / "card_details.json", as_of, gemrate_id)
        if _is_fresh(direct, as_of):
            skipped.append({"gemrateId": gemrate_id, "reason": "direct_current_cached"})
        elif _is_fresh(public, as_of):
            skipped.append({"gemrateId": gemrate_id, "reason": "public_current_cached"})
        else:
            worklist.append({"gemrateId": gemrate_id, "tcg": candidate.get("tcg")})
    ids = [str(row["gemrateId"]) for row in worklist]
    return {
        "schemaVersion": 1,
        "authority": "gemrate",
        "transport": "gemrate_public_card_page",
        "ids": ids,
        "candidateCount": len(rows),
        "worklistCount": len(worklist),
        "worklist": worklist,
        "skipped": skipped,
        "candidateInputSha256": hashlib.sha256(
            _canonical_json([{"gemrateId": str(row["gemrateId"]), "tcg": row.get("tcg")} for row in rows]).encode("utf-8")
        ).hexdigest(),
    }


def _summarize(
    rows: Iterable[Mapping[str, Any]], *, carried_forward: int, direct_current: int,
    public_current: int, mirror_current: int, direct_history: int,
) -> dict[str, int]:
    result = {
        "candidateCount": 0,
        "attempted": 0,
        "resolved": 0,
        "belowThreshold": 0,
        "unavailable": 0,
        "review": 0,
        "carriedForward": carried_forward,
        "directCurrent": direct_current,
        "publicCurrent": public_current,
        "mirrorCurrent": mirror_current,
        "directHistory": direct_history,
        "eligible": 0,
        "preEntryRadar": 0,
        "outsideRadar": 0,
        "trackedIdentityUnresolved": 0,
    }
    for row in rows:
        result["candidateCount"] += 1
        if not row.get("carriedForward"):
            result["attempted"] += 1
        status = str(row.get("status"))
        if status == "resolved":
            result["resolved"] += 1
        elif status == "below-threshold":
            result["belowThreshold"] += 1
        elif status == "review":
            result["review"] += 1
        else:
            result["unavailable"] += 1
        tracking = str(row.get("trackingStatus") or "")
        if tracking == "eligible":
            result["eligible"] += 1
        elif tracking == "pre_entry_radar":
            result["preEntryRadar"] += 1
        elif tracking == "outside_radar":
            result["outsideRadar"] += 1
        if tracking in {"eligible", "pre_entry_radar"} and row.get("snkEligibility") != "eligible":
            result["trackedIdentityUnresolved"] += 1
    return result


def _resolved_identity_fields(candidate: Mapping[str, Any], tracking_status: str) -> dict[str, Any]:
    """Carry the exact crosswalk evidence needed by the SNK-only price lane."""

    identity_status = str(candidate.get("identityStatus") or "")
    tracked = tracking_status in {"eligible", "pre_entry_radar"}
    if identity_status != "exact_confirmed":
        return {
            "identityStatus": identity_status or "unmapped",
            "snkEligibility": "review" if tracked else "not_required",
        }
    canonical_identity = candidate.get("canonicalIdentity")
    canonical_source = candidate.get("canonicalSource")
    if not isinstance(canonical_identity, Mapping) or not isinstance(canonical_source, Mapping):
        return {"identityStatus": "unmapped", "snkEligibility": "review" if tracked else "not_required"}
    return {
        "identityStatus": "exact_confirmed",
        "canonicalIdentity": dict(canonical_identity),
        "canonicalSource": dict(canonical_source),
        "canonicalSourceCode": candidate.get("canonicalSourceCode"),
        "canonicalExternalId": candidate.get("canonicalExternalId"),
        "storageScope": candidate.get("storageScope"),
        "snkItemId": candidate.get("snkItemId"),
        "canonicalPrintingKey": candidate.get("canonicalPrintingKey"),
        "setName": candidate.get("setName"),
        "collectorNumber": candidate.get("collectorNumber"),
        "language": candidate.get("language"),
        "edition": candidate.get("edition"),
        "parallel": candidate.get("parallel"),
        "finish": candidate.get("finish"),
        "snkEligibility": "eligible" if tracked else "not_required",
    }


def run_offline_backfill(
    candidates: Iterable[Mapping[str, Any]],
    *,
    direct_root: Path,
    mirror_root: Path,
    public_root: Path | None = None,
    as_of: date,
    checkpoint: Mapping[str, Any] | None = None,
    resume: bool = False,
) -> dict[str, Any]:
    """Resolve a finite candidate batch from private, already-collected payloads."""

    candidate_rows = [dict(row) for row in candidates]
    if len({str(row.get("gemrateId")) for row in candidate_rows}) != len(candidate_rows):
        raise ValueError("candidate batch contains duplicate GemRate IDs")
    if any(not _is_exact_id(row.get("gemrateId")) for row in candidate_rows):
        raise ValueError("candidate batch requires exact GemRate IDs")
    prior = _checkpoint_rows(checkpoint, as_of) if resume else {}
    public_root = public_root or direct_root
    rows: list[dict[str, Any]] = []
    carried_forward = direct_current = public_current = mirror_current = direct_history = 0
    fetched_at = f"{as_of.isoformat()}T00:00:00Z"
    for candidate in sorted(candidate_rows, key=lambda row: (str(row.get("tcg")), str(row["gemrateId"]))):
        gemrate_id = str(candidate["gemrateId"])
        identity_status = str(candidate.get("identityStatus") or "")
        if identity_status == "review":
            rows.append({"gemrateId": gemrate_id, "tcg": candidate.get("tcg"), "status": "review", "reason": "identity_conflict", "carriedForward": False})
            continue
        prior_row = prior.get(gemrate_id)
        current_key = str(candidate.get("canonicalPrintingKey") or "")
        if (
            prior_row is not None
            and identity_status == "exact_confirmed"
            and current_key
            and str(prior_row.get("canonicalPrintingKey") or "") == current_key
        ):
            row = dict(prior_row)
            row["carriedForward"] = True
            rows.append(row)
            carried_forward += 1
            continue
        direct_dir = direct_root / gemrate_id
        direct = _direct_current(direct_root, gemrate_id, fetched_at)
        history_ready = (direct_dir / "history_full.json").is_file() if direct is not None else False
        public = _public_current(public_root / gemrate_id / "card_details.json", as_of, gemrate_id)
        mirror_path = _exact_mirror_path(mirror_root, candidate)
        mirror = _mirror_current(mirror_path, as_of) if mirror_path is not None else None
        if _is_fresh(direct, as_of):
            direct_current += 1
        if _is_fresh(public, as_of):
            public_current += 1
        if _is_fresh(mirror, as_of):
            mirror_current += 1
        if history_ready:
            direct_history += 1
        points = [point for point in (direct, public, mirror) if _is_fresh(point, as_of)]
        transport_labels = {
            DIRECT_TRANSPORT: "gemrate_direct",
            WEBSITE_TRANSPORT: WEBSITE_TRANSPORT,
            MIRROR_TRANSPORT: "grade10_gemrate_mirror",
        }
        transport_observations = [
            {
                "authority": "gemrate",
                "transport": transport_labels[str(point["transport"])],
                "populationPsa10": point["populationPsa10"],
                "effectiveDate": point["effectiveDate"],
                "effectiveDateSource": point["effectiveDateSource"],
                "historyStatus": "ready" if point["transport"] == DIRECT_TRANSPORT and history_ready else "unavailable",
            }
            for point in points
        ]
        comparable_points = [point for point in points if point["transport"] != WEBSITE_TRANSPORT]
        mismatch = next(
            (
                {
                    "effectiveDate": left["effectiveDate"],
                    "leftTransport": left["transport"],
                    "leftPsa10": left["populationPsa10"],
                    "rightTransport": right["transport"],
                    "rightPsa10": right["populationPsa10"],
                }
                for index, left in enumerate(comparable_points)
                for right in comparable_points[index + 1:]
                if left["effectiveDate"] == right["effectiveDate"]
                and left["populationPsa10"] != right["populationPsa10"]
            ),
            None,
        )
        if mismatch is not None:
            rows.append({
                "gemrateId": gemrate_id,
                "tcg": candidate.get("tcg"),
                "status": "review",
                "reason": "same_day_transport_conflict",
                "transportObservations": transport_observations,
                "transportMismatch": mismatch,
                "carriedForward": False,
            })
            continue
        # Official direct API outranks live public card-page evidence, which
        # outranks the exact Grade10 mirror. A page JSON's source date is last
        # population change metadata, not a freshness date for conflict checks.
        priority = {
            DIRECT_TRANSPORT: 3,
            WEBSITE_TRANSPORT: 2,
            MIRROR_TRANSPORT: 1,
        }
        selected = max(
            points,
            key=lambda point: (
                priority[str(point["transport"])],
                date.fromisoformat(str(point["effectiveDate"])),
            ),
            default=None,
        )
        if selected is None:
            rows.append({"gemrateId": gemrate_id, "tcg": candidate.get("tcg"), "status": "unavailable", "reason": "no_exact_current_population", "carriedForward": False})
            continue
        population = selected["populationPsa10"]
        effective_date = selected["effectiveDate"]
        transport = transport_labels[str(selected["transport"])]
        tracking_status = (
            "eligible" if population >= 1000
            else "pre_entry_radar" if population >= 971
            else "outside_radar"
        )
        rows.append(
            {
                "gemrateId": gemrate_id,
                "tcg": candidate.get("tcg"),
                "status": "resolved" if population >= 1000 else "below-threshold",
                "populationPsa10": population,
                "trackingStatus": tracking_status,
                "effectiveDate": effective_date,
                "currentTransport": transport,
                "historyStatus": "ready" if history_ready else "unavailable",
                "transportObservations": transport_observations,
                "carriedForward": False,
                **_resolved_identity_fields(candidate, tracking_status),
            }
        )
    counts = _summarize(
        rows,
        carried_forward=carried_forward,
        direct_current=direct_current,
        public_current=public_current,
        mirror_current=mirror_current,
        direct_history=direct_history,
    )
    candidate_input = [{"gemrateId": row["gemrateId"], "tcg": row.get("tcg")} for row in candidate_rows]
    classification_complete = counts["candidateCount"] == (
        counts["resolved"] + counts["belowThreshold"] + counts["unavailable"] + counts["review"]
    )
    ranking_promotable = (
        counts["unavailable"] == 0
        and counts["review"] == 0
        and counts["trackedIdentityUnresolved"] == 0
    )
    return {
        "schemaVersion": 2,
        "populationAuthority": "gemrate",
        "asOf": as_of.isoformat(),
        "candidateInputSha256": hashlib.sha256(_canonical_json(candidate_input).encode("utf-8")).hexdigest(),
        "resume": resume,
        "actualCounts": counts,
        "attempted": counts["attempted"],
        "succeeded": counts["resolved"] + counts["belowThreshold"],
        "failed": counts["unavailable"] + counts["review"],
        "partial": counts["unavailable"] > 0 or counts["review"] > 0,
        "classificationComplete": classification_complete,
        "retryCount": counts["unavailable"] + counts["review"],
        "rankingPromotable": ranking_promotable,
        "promotable": ranking_promotable,
        "candidates": rows,
    }


def run_candidate_backfill(
    candidates: Iterable[Mapping[str, Any]],
    *,
    direct_root: Path,
    public_root: Path,
    mirror_root: Path,
    as_of: date,
    checkpoint: Mapping[str, Any] | None = None,
    resume: bool = False,
    collect_public: bool = False,
    public_delay: float = 0.3,
    public_collector: Callable[..., Mapping[str, Any]] = collect_public_card_details,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Optionally repair missing exact public POP before classifying candidates.

    The public collector has no access to search-result population values.  It
    receives only IDs emitted by ``build_public_card_details_worklist``.
    Collection failure still yields a complete classification manifest: failed
    candidates are ``unavailable`` and remain in the next-run retry worklist.
    """

    candidate_rows = [dict(row) for row in candidates]
    worklist = build_public_card_details_worklist(
        candidate_rows,
        direct_root=direct_root,
        public_root=public_root,
        as_of=as_of,
    )
    collection: dict[str, Any] = {
        "enabled": collect_public,
        "attempted": 0,
        "succeeded": 0,
        "failed": 0,
        "cached": 0,
        "partial": False,
    }
    if collect_public and worklist["ids"]:
        try:
            result = public_collector(
                list(worklist["ids"]),
                cards_dir=public_root,
                delay=public_delay,
                # The worklist has already checked semantic validity.  Do not
                # let a corrupt or wrong-ID cache file suppress its repair.
                resume=False,
            )
            collection.update(dict(result))
        except RuntimeError:
            collection.update({
                "attempted": len(worklist["ids"]),
                "failed": len(worklist["ids"]),
                "partial": True,
                "error": "public_card_collection_failed",
            })
    resolved_candidates, identity_reviews = apply_public_receipt_identity_proposals(
        candidate_rows,
        public_root=public_root,
        direct_root=direct_root,
    )
    manifest = run_offline_backfill(
        resolved_candidates,
        direct_root=direct_root,
        public_root=public_root,
        mirror_root=mirror_root,
        as_of=as_of,
        checkpoint=checkpoint,
        resume=resume,
    )
    manifest["keylessPublicCollection"] = collection
    manifest["publicCardDetailsWorklist"] = {
        "candidateCount": worklist["candidateCount"],
        "worklistCount": worklist["worklistCount"],
        "candidateInputSha256": worklist["candidateInputSha256"],
    }
    manifest["publicReceiptIdentityResolution"] = {
        "candidateCount": len(candidate_rows),
        "confirmed": sum(str(row.get("identityStatus") or "") == "exact_confirmed" for row in resolved_candidates),
        "review": len(identity_reviews),
        "reviewQueue": identity_reviews,
    }
    return manifest, worklist


def _read_document(path: Path) -> Mapping[str, Any]:
    document = _load_json(path)
    if document is None:
        raise ValueError(f"invalid JSON document: {path}")
    return document


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill private GemRate current-population candidate states")
    parser.add_argument("--active-universe", type=Path, default=DEFAULT_UNIVERSE)
    parser.add_argument("--one-piece-candidates", type=Path, default=DEFAULT_ONE_PIECE)
    parser.add_argument("--crosswalk", type=Path, default=DEFAULT_CROSSWALK)
    parser.add_argument(
        "--receipt-mappings",
        type=Path,
        default=DEFAULT_RECEIPT_MAPPINGS,
        help="private durable exact GemRate receipt mappings; conflicting opaque-ID rebinding fails closed",
    )
    parser.add_argument("--direct-root", type=Path, default=DEFAULT_DIRECT)
    parser.add_argument("--public-root", type=Path, default=DEFAULT_PUBLIC)
    parser.add_argument("--mirror-root", type=Path, help="explicit immutable G10 payload override for a controlled replay")
    parser.add_argument("--freeze-manifest", type=Path, default=DEFAULT_FREEZE_MANIFEST)
    parser.add_argument("--landing-root", type=Path, default=DEFAULT_LANDING)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--collect-public", action="store_true", help="repair missing exact public card-details POP via Playwright")
    parser.add_argument("--public-delay", type=float, default=0.3)
    parser.add_argument("--require-ranking-ready", action="store_true", help="fail if any candidate is unavailable or in review")
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    args = parser.parse_args()
    receipt_mappings = _load_json(args.receipt_mappings)
    crosswalk = merge_receipt_mappings(
        _read_document(args.crosswalk),
        receipt_mappings,
    )
    roster = build_candidate_roster(
        _read_document(args.active_universe),
        _read_document(args.one_piece_candidates),
        crosswalk,
    )
    checkpoint_path = args.out / "checkpoint.json"
    checkpoint = _read_document(checkpoint_path) if args.resume and checkpoint_path.is_file() else None
    mirror_root = args.mirror_root or resolve_immutable_mirror_root(args.freeze_manifest, args.landing_root)
    manifest, worklist = run_candidate_backfill(
        roster,
        direct_root=args.direct_root,
        public_root=args.public_root,
        mirror_root=mirror_root,
        as_of=args.as_of,
        checkpoint=checkpoint,
        resume=args.resume,
        collect_public=args.collect_public,
        public_delay=args.public_delay,
    )
    manifest["receiptIdentityMappings"] = persist_receipt_identity_mappings(
        manifest["candidates"], path=args.receipt_mappings,
    )
    _atomic_write_json(args.out / "public-card-details-worklist.json", worklist)
    _atomic_write_text(
        args.out / "public-card-details-ids.txt",
        "".join(f"{gemrate_id}\n" for gemrate_id in worklist["ids"]),
    )
    for name in ("manifest.json", "checkpoint.json"):
        _atomic_write_json(args.out / name, manifest)
    print(json.dumps({
        "actualCounts": manifest["actualCounts"],
        "classificationComplete": manifest["classificationComplete"],
        "rankingPromotable": manifest["rankingPromotable"],
        "retryCount": manifest["retryCount"],
    }, sort_keys=True))
    return 1 if args.require_ranking_ready and not manifest["rankingPromotable"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
