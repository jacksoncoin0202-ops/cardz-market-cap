#!/usr/bin/env python3
"""Build the exact private CARDZ source crosswalk from the frozen G10 universe.

This script never searches by card name.  The current G10 card directory already
contains the exact GemRate identity used for each population document and, for
native SNK rows, the exact apparel id.  The resulting files live under
``data/runtime`` and are private pipeline inputs, never public contract fields.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from g10_ingest import iter_constituents, private_source_ref, read_json, storage_source


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "integrations" / "grade10" / "data"
DEFAULT_OUT = ROOT / "data" / "runtime" / "private-source-map" / "source-crosswalk.json"
GEMRATE_ID = re.compile(r"(?:gemrate_id=|/card/)([0-9a-f]{40})(?:\b|$)", re.IGNORECASE)
ONE_PIECE_NUMBER = re.compile(r"^(?:OP|ST|EB|P|PRB|DON)\d{0,2}-\d{3,4}$", re.IGNORECASE)
LANGUAGE_ALIASES = {
    "en": "en",
    "english": "en",
    "ja": "ja",
    "jp": "ja",
    "japanese": "ja",
    "ko": "ko",
    "kr": "ko",
    "korean": "ko",
    "zhcn": "zhCN",
    "cn": "zhCN",
    "zh-hans": "zhCN",
    "simplified chinese": "zhCN",
    "zhtw": "zhTW",
    "tw": "zhTW",
    "zh-hant": "zhTW",
    "traditional chinese": "zhTW",
}

# These tokens are only used when they are explicit in an exact GemRate card
# receipt or its retained discovery evidence.  They intentionally do not try
# to infer a language from a card name, a collector-number suffix, or a market
# default: all of those have caused silent cross-language misidentification.
LANGUAGE_EVIDENCE_TOKENS = {
    "japanese": "ja",
    "english": "en",
    "korean": "ko",
    "traditional chinese": "zhTW",
    "simplified chinese": "zhCN",
}


def canonical_language(value: Any) -> str:
    return LANGUAGE_ALIASES.get(str(value or "").strip().casefold(), "")


def language_from_exact_evidence(values: list[object]) -> tuple[str, list[str]]:
    """Resolve one language only from explicit retained source phrases.

    A receipt/candidate can name more than one language in a messy description.
    That is a review condition, not a reason to pick the first match.  Direct
    compact language values (``ja``, ``English``) are accepted through the
    existing alias map; prose is limited to the unambiguous tokens above.
    """

    matches: set[str] = set()
    for value in values:
        direct = canonical_language(value)
        if direct:
            matches.add(direct)
            continue
        text = " ".join(str(value or "").split()).casefold()
        if not text:
            continue
        for phrase, language in LANGUAGE_EVIDENCE_TOKENS.items():
            if phrase in text:
                matches.add(language)
    ordered = sorted(matches)
    return (ordered[0] if len(ordered) == 1 else "", ordered)


def normalized_identity_part(value: Any) -> str:
    """Normalize an already-supplied identity value without inventing one."""

    return " ".join(str(value or "").strip().split()).casefold()


_COLLECTOR_EVIDENCE_CODE = re.compile(r"^[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)?$")
_COLLECTOR_EVIDENCE_LANGUAGE_TOKENS = {"EN", "JA", "JP", "KO", "KR", "CN", "TW", "ZH"}


def normalized_collector_number_evidence(value: Any) -> str:
    """Return a comparison-only form for a small set of explicit formats.

    This deliberately is not a canonical collector-number formatter.  It only
    prevents duplicate GemRate/G10 evidence from entering review when both
    sides already state the same numeric components and one explicit set code.
    Bare numbers, multiple codes, and unrecognised language tokens stay
    unresolved so callers cannot manufacture a printing identity from them.
    """

    raw = " ".join(str(value or "").strip().upper().split())
    if not raw:
        return ""

    slash_parts = re.split(r"\s*/\s*", raw)
    if len(slash_parts) == 2:
        left, right = slash_parts
        if left.isdigit() and right.isdigit():
            return f"numeric:{int(left)}/{int(right)}"
        return _normalized_explicit_code_number(left, right)
    if len(slash_parts) != 1:
        return ""

    tokens = raw.split()
    # ``SVP EN 085`` is explicit code + explicit language token + number.  EN
    # is comparison-only metadata here, never a derived canonical language.
    if len(tokens) == 3 and tokens[1] == "EN":
        tokens = [tokens[0], tokens[2]]
    if len(tokens) != 2:
        return ""
    return _normalized_explicit_code_number(tokens[0], tokens[1])


def _normalized_explicit_code_number(left: str, right: str) -> str:
    """Normalize an already explicit code/number pair, otherwise return empty."""

    if left.isdigit() and _is_unambiguous_collector_code(right):
        return f"code:{right}:{int(left)}"
    if right.isdigit() and _is_unambiguous_collector_code(left):
        return f"code:{left}:{int(right)}"
    return ""


def _is_unambiguous_collector_code(value: str) -> bool:
    # Language tokens are not set codes.  Single-letter codes are also left
    # alone: a comparison helper must prefer a review over a false merge.
    return (
        value not in _COLLECTOR_EVIDENCE_LANGUAGE_TOKENS
        and len(value.replace("-", "")) >= 2
        and bool(_COLLECTOR_EVIDENCE_CODE.fullmatch(value))
    )


def same_collector_number_evidence(left: Any, right: Any) -> bool:
    """Compare explicit evidence without changing either source value."""

    if normalized_identity_part(left) == normalized_identity_part(right):
        return True
    left_normalized = normalized_collector_number_evidence(left)
    right_normalized = normalized_collector_number_evidence(right)
    return bool(left_normalized and right_normalized and left_normalized == right_normalized)


def complete_collector_number(value: Any) -> bool:
    collector = str(value or "").strip().upper()
    if ONE_PIECE_NUMBER.fullmatch(collector):
        return True
    if "/" not in collector:
        return False
    left, right = collector.split("/", 1)
    if not re.search(r"\d", left):
        return False
    if right.isdigit():
        return int(right) > 0
    if re.search(r"\d", right):
        return True
    # Explicit Pokémon promo namespaces use denominators such as S-P, SM-P,
    # SVP, and MEP. A generic one-letter/token denominator is not enough
    # evidence for an exact printing.
    return bool(
        re.fullmatch(r"[A-Z0-9]{1,8}-P", right)
        or re.fullmatch(r"[A-Z]{2,6}P", right)
    )


def canonical_printing_key(
    *,
    market: str,
    language: str,
    set_name: str,
    collector_number: str,
    edition: str,
    parallel: str,
    finish: str,
) -> str:
    """Return the exact canonical printing key from supplied identity fields.

    Callers must have already verified the complete collector number and the
    physical card language.  The order matches ``card_identity.printing_key7``:
    TCG, language, set, collector number, edition, parallel, finish.
    """

    canonical_lang = canonical_language(language)
    if not canonical_lang:
        raise ValueError("canonical_printing_key_requires_language")
    return "|".join(
        (
            normalized_identity_part(market),
            canonical_lang,
            normalized_identity_part(set_name),
            normalized_identity_part(collector_number),
            normalized_identity_part(edition),
            normalized_identity_part(parallel),
            normalized_identity_part(finish),
        )
    )


def build_gemrate_receipt_identity_proposal(
    candidate: Mapping[str, Any],
    receipt: Mapping[str, Any],
) -> dict[str, Any]:
    """Turn one exact public GemRate card receipt into a deterministic proposal.

    This is deliberately a crosswalk proposal, not a name-search mapper.  The
    opaque GemRate ID must agree, the settled public-card route must be marked
    verified, and every supplied discovery field must agree with the receipt.
    Edition and finish are required as explicit candidate evidence because the
    public current-population receipt does not state them.  A missing or
    conflicting field is preserved as a review reason; no suffix, finish, or
    language is manufactured.
    """

    gemrate_id = str(candidate.get("gemrateId") or "").strip().casefold()
    received_id = str(receipt.get("gemrate_id") or receipt.get("gemrateId") or "").strip().casefold()
    page = receipt.get("publicCardPage")
    page_identity = page.get("identity") if isinstance(page, Mapping) else None
    if not isinstance(page_identity, Mapping):
        page_identity = {}
    reasons: list[str] = []
    if not re.fullmatch(r"[0-9a-f]{40}", gemrate_id) or received_id != gemrate_id:
        reasons.append("gemrate_id_mismatch")
    canonical_url = str(page.get("canonicalUrl") or "") if isinstance(page, Mapping) else ""
    if not isinstance(page, Mapping) or not page.get("routeVerified") or not canonical_url.startswith("https://www.gemrate.com/card/"):
        reasons.append("gemrate_route_unverified")

    tcg = str(candidate.get("tcg") or candidate.get("market") or "").strip().casefold()
    if tcg not in {"pokemon", "one-piece"}:
        reasons.append("tcg_missing_or_unsupported")

    def text(value: object) -> str:
        return " ".join(str(value or "").split()).strip()

    receipt_set = text(page_identity.get("set_name"))
    receipt_number = text(page_identity.get("card_number"))
    receipt_parallel = text(page_identity.get("parallel"))
    candidate_set = text(candidate.get("setEvidence") or candidate.get("setName"))
    candidate_number = text(candidate.get("collectorNumberEvidence") or candidate.get("collectorNumber"))
    candidate_parallel = text(candidate.get("parallelEvidence") or candidate.get("parallel"))
    if not receipt_set:
        reasons.append("receipt_set_missing")
    if candidate_set and normalized_identity_part(candidate_set) != normalized_identity_part(receipt_set):
        reasons.append("set_conflict")
    if not complete_collector_number(receipt_number):
        reasons.append("collector_number_incomplete")
    # A short discovery number must not be reconciled to the receipt by adding
    # a prefix/suffix.  The human review queue receives both values instead.
    if candidate_number and (
        not complete_collector_number(candidate_number)
        or normalized_identity_part(candidate_number) != normalized_identity_part(receipt_number)
    ):
        reasons.append("collector_number_conflict")
    if candidate_parallel and normalized_identity_part(candidate_parallel) != normalized_identity_part(receipt_parallel):
        reasons.append("parallel_conflict")

    language, language_matches = language_from_exact_evidence([
        candidate.get("languageEvidence"),
        candidate.get("descriptionEvidence"),
        candidate.get("setEvidence"),
        candidate.get("language"),
        receipt.get("language"),
        page_identity.get("language"),
    ])
    if not language:
        reasons.append("language_missing_or_conflicting")

    # GemRate's current receipt has no edition/finish fields.  They can only
    # enter an automatically confirmed printing when a retained, explicit
    # candidate record says what they are.  Empty strings are allowed when the
    # source record explicitly carries the field (base/standard variants).
    has_edition = "editionEvidence" in candidate or "edition" in candidate
    has_finish = "finishEvidence" in candidate or "finish" in candidate
    edition = text(candidate.get("editionEvidence") if "editionEvidence" in candidate else candidate.get("edition"))
    finish = text(candidate.get("finishEvidence") if "finishEvidence" in candidate else candidate.get("finish"))
    if not has_edition:
        reasons.append("edition_unavailable")
    if not has_finish:
        reasons.append("finish_unavailable")

    identity = {
        "tcg": tcg,
        "language": language,
        "setName": receipt_set,
        "collectorNumber": receipt_number,
        "edition": edition,
        "parallel": receipt_parallel,
        "finish": finish,
    }
    proposal = {
        "gemrateId": gemrate_id,
        "identityStatus": "exact_confirmed" if not reasons else "review",
        "canonicalIdentity": identity if not reasons else None,
        "canonicalSource": {
            "sourceCode": "gemrate_public_card_page",
            "externalId": gemrate_id,
            "storageScope": "gemrate_public_card_page",
            "snkItemId": None,
        },
        "canonicalPrintingKey": canonical_printing_key(
            market=tcg,
            language=language,
            set_name=receipt_set,
            collector_number=receipt_number,
            edition=edition,
            parallel=receipt_parallel,
            finish=finish,
        ) if not reasons else None,
        "identityEvidence": {
            "receipt": {
                "canonicalUrl": canonical_url,
                "year": text(page_identity.get("year")),
                "setName": receipt_set,
                "collectorNumber": receipt_number,
                "parallel": receipt_parallel,
            },
            "candidate": {
                "nameEvidence": text(candidate.get("nameEvidence")),
                "setEvidence": candidate_set,
                "collectorNumberEvidence": candidate_number,
                "parallelEvidence": candidate_parallel,
                "languageEvidence": text(candidate.get("languageEvidence")),
                "editionEvidencePresent": has_edition,
                "finishEvidencePresent": has_finish,
            },
            "languageEvidenceMatches": language_matches,
        },
        "reviewReasons": sorted(set(reasons)),
    }
    return proposal


def build_gemrate_direct_identity_proposal(
    candidate: Mapping[str, Any],
    receipt: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Build an exact identity proposal from a verified direct GemRate receipt.

    The caller owns the filesystem proof that ``sourcePointer`` addresses the
    retained payload.  This pure function proves the receipt/payload binding
    and treats parsed descriptions solely as conflict evidence.  It never
    fills an identity field from a description, a suffix, or a search result.
    """

    gemrate_id = str(candidate.get("gemrateId") or "").strip().casefold()
    reasons: list[str] = []
    if not re.fullmatch(r"[0-9a-f]{40}", gemrate_id):
        reasons.append("gemrate_id_mismatch")
    requested = str(receipt.get("requestedGemrateId") or "").strip().casefold()
    entity = str(receipt.get("entityGemrateId") or "").strip().casefold()
    if requested != gemrate_id or entity != gemrate_id:
        reasons.append("direct_requested_entity_id_mismatch")
    data = payload.get("data") if isinstance(payload, Mapping) else None
    if not isinstance(data, Mapping) or str(data.get("gemrate_id") or "").strip().casefold() != gemrate_id:
        reasons.append("direct_payload_entity_id_mismatch")
    digest = str(receipt.get("payloadSha256") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", digest) or hashlib.sha256(stable_json(payload)).hexdigest() != digest:
        reasons.append("direct_payload_sha256_mismatch")

    def text(value: object) -> str:
        return " ".join(str(value or "").split()).strip()

    tcg = text(candidate.get("tcg") or candidate.get("market")).casefold()
    candidate_set = text(candidate.get("setEvidence") if "setEvidence" in candidate else candidate.get("setName"))
    candidate_number = text(candidate.get("collectorNumberEvidence") if "collectorNumberEvidence" in candidate else candidate.get("collectorNumber"))
    has_parallel = "parallelEvidence" in candidate or "parallel" in candidate
    parallel = text(candidate.get("parallelEvidence") if "parallelEvidence" in candidate else candidate.get("parallel"))
    has_edition = "editionEvidence" in candidate or "edition" in candidate
    edition = text(candidate.get("editionEvidence") if "editionEvidence" in candidate else candidate.get("edition"))
    has_finish = "finishEvidence" in candidate or "finish" in candidate
    finish = text(candidate.get("finishEvidence") if "finishEvidence" in candidate else candidate.get("finish"))
    language, language_matches = language_from_exact_evidence([
        candidate.get("languageEvidence"), candidate.get("descriptionEvidence"),
        candidate.get("setEvidence"), candidate.get("language"),
    ])
    if not language:
        reasons.append("language_missing_or_conflicting")
    if tcg not in {"pokemon", "one-piece"}:
        reasons.append("tcg_missing_or_unsupported")
    if not candidate_set:
        reasons.append("set_missing")
    if not complete_collector_number(candidate_number):
        reasons.append("collector_number_incomplete")
    if not has_edition:
        reasons.append("edition_unavailable")
    if not has_parallel:
        reasons.append("parallel_unavailable")
    if not has_finish:
        reasons.append("finish_unavailable")

    descriptions: list[Mapping[str, Any]] = []
    for description in [receipt.get("parsedDescription"), data.get("parsed_description") if isinstance(data, Mapping) else None]:
        if isinstance(description, Mapping):
            descriptions.append(description)
    graders = receipt.get("graders")
    if isinstance(graders, Mapping):
        for member in graders.values():
            if isinstance(member, Mapping) and isinstance(member.get("parsedDescription"), Mapping):
                descriptions.append(member["parsedDescription"])
    fields = {
        "set": (candidate_set, ("set", "setName", "set_name")),
        "collector_number": (candidate_number, ("cardNumber", "card_number", "collectorNumber", "collector_number")),
        "edition": (edition, ("edition",)),
        "parallel": (parallel, ("parallel", "variant")),
        "finish": (finish, ("finish",)),
    }
    for label, (expected, names) in fields.items():
        for description in descriptions:
            actual = next((text(description.get(name)) for name in names if text(description.get(name))), "")
            if not actual:
                continue
            if normalized_identity_part(actual) != normalized_identity_part(expected):
                reasons.append(f"direct_{label}_conflict")

    identity = {
        "tcg": tcg, "language": language, "setName": candidate_set,
        "collectorNumber": candidate_number,
        "edition": edition, "parallel": parallel, "finish": finish,
    }
    return {
        "gemrateId": gemrate_id,
        "identityStatus": "exact_confirmed" if not reasons else "review",
        "canonicalIdentity": identity if not reasons else None,
        "canonicalSource": {
            "sourceCode": "gemrate_direct", "externalId": gemrate_id,
            "storageScope": "gemrate_direct", "snkItemId": None,
        },
        "canonicalPrintingKey": canonical_printing_key(
            market=tcg, language=language, set_name=candidate_set,
            collector_number=candidate_number,
            edition=edition, parallel=parallel, finish=finish,
        ) if not reasons else None,
        "identityEvidence": {
            "directReceipt": {
                "requestedGemrateId": requested, "entityGemrateId": entity,
                "payloadSha256": digest, "parsedDescription": receipt.get("parsedDescription"),
            },
            "candidate": {
                "setEvidence": candidate_set, "collectorNumberEvidence": candidate_number,
                "languageEvidence": text(candidate.get("languageEvidence")),
                "editionEvidencePresent": has_edition, "parallelEvidencePresent": has_parallel,
                "finishEvidencePresent": has_finish,
            },
            "languageEvidenceMatches": language_matches,
        },
        "reviewReasons": sorted(set(reasons)),
    }


def review_entry(card: Mapping[str, Any]) -> dict[str, Any]:
    reasons = sorted({str(reason) for reason in card.get("reviewReasons", []) if str(reason)})
    evidence = {
        "market": card.get("market"),
        "setName": card.get("setName"),
        "collectorNumberRaw": card.get("collectorNumberRaw"),
        "language": card.get("language"),
        "sourceLanguage": card.get("sourceLanguage"),
        "edition": card.get("edition"),
        "parallel": card.get("parallel"),
        "finish": card.get("finish"),
    }
    review_key = stable_json(
        {
            "sourceCode": card.get("canonicalSourceCode"),
            "externalId": card.get("canonicalExternalId"),
            "reasons": reasons,
            "evidence": evidence,
        }
    )
    return {
        "reviewId": hashlib.sha256(review_key).hexdigest()[:24],
        "sourceCode": card.get("canonicalSourceCode"),
        "externalId": card.get("canonicalExternalId"),
        "market": card.get("market"),
        "reasons": reasons,
        "evidence": evidence,
    }


def iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def stable_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.next")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def extract_gemrate_id(document: Mapping[str, Any]) -> str | None:
    source = str(document.get("source") or "")
    match = GEMRATE_ID.search(source)
    return match.group(1).lower() if match else None


def _deduped_rows(source_root: Path) -> list[tuple[str, Mapping[str, Any]]]:
    chosen: dict[tuple[str, str], tuple[int, str, Mapping[str, Any]]] = {}
    priority = {"ptcg": 2, "ptcg100": 1, "opcg": 2}
    for market, row in iter_constituents(source_root):
        source_ref = private_source_ref(row)
        if source_ref is None:
            continue
        rank = priority.get(market, 0)
        current = chosen.get(source_ref)
        if current is None or rank > current[0]:
            chosen[source_ref] = (rank, market, row)
    return [(market, row) for _, market, row in chosen.values()]


def build_crosswalk(source_root: Path, generated_at: datetime | None = None) -> dict[str, Any]:
    generated_at = generated_at or datetime.now(timezone.utc)
    cards: list[dict[str, Any]] = []
    missing_population: list[str] = []
    missing_gemrate: list[str] = []

    for market, row in _deduped_rows(source_root):
        source_ref = private_source_ref(row)
        if source_ref is None:
            continue
        source_code, external_id = source_ref
        storage_code = storage_source(source_code)
        card_dir = source_root / "cards" / storage_code / external_id
        population_path = card_dir / "populations.json"
        asset_path = card_dir / "asset_info.json"
        population: Mapping[str, Any] = {}
        asset: Mapping[str, Any] = {}
        if population_path.is_file():
            loaded = read_json(population_path)
            if isinstance(loaded, Mapping):
                population = loaded
        else:
            missing_population.append(f"{source_code}:{external_id}")
        if asset_path.is_file():
            loaded = read_json(asset_path)
            if isinstance(loaded, Mapping):
                asset = loaded
        gemrate_id = extract_gemrate_id(population)
        if gemrate_id is None:
            missing_gemrate.append(f"{source_code}:{external_id}")

        card_market = "one-piece" if market == "opcg" else "pokemon"
        collector = str(asset.get("cardId") or "").strip()
        set_name = str(asset.get("setName") or row.get("setName") or "").strip()
        edition = str(asset.get("edition") or row.get("edition") or "").strip()
        parallel = str(asset.get("parallel") or row.get("parallel") or "").strip()
        finish = str(asset.get("finish") or row.get("finish") or "").strip()
        language, language_matches = language_from_exact_evidence(
            [asset.get("language"), row.get("language"), row.get("lang")]
        )
        reasons: list[str] = []
        if not asset:
            reasons.append("asset_identity_missing")
        if not complete_collector_number(collector):
            reasons.append("collector_number_incomplete")
        if not set_name:
            reasons.append("set_missing")
        if not language:
            reasons.append("language_missing_or_conflicting")
        card = {
            "canonicalSourceCode": source_code,
            "canonicalExternalId": external_id,
            "storageScope": storage_code,
            "market": card_market,
            "gemrateId": gemrate_id,
            "snkItemId": int(external_id) if source_code == "snkrdunk" and external_id.isdigit() else None,
            "name": str(asset.get("cardName") or row.get("name") or "").strip(),
            "collectorNumberRaw": collector,
            "setName": set_name,
            "language": language,
            "sourceLanguage": str(asset.get("language") or "").strip(),
            "languageEvidenceMatches": language_matches,
            "edition": edition,
            "parallel": parallel,
            "finish": finish,
            "canonicalPrintingKey": None,
            "identityStatus": "review" if reasons else "confirmed",
            "reviewReasons": sorted(set(reasons)),
        }
        if not reasons:
            card["canonicalPrintingKey"] = canonical_printing_key(
                market=card_market,
                language=language,
                set_name=set_name,
                collector_number=collector,
                edition=edition,
                parallel=parallel,
                finish=finish,
            )
        cards.append(card)

    cards.sort(key=lambda card: (card["market"], card["canonicalSourceCode"], card["canonicalExternalId"]))
    keys: dict[str, list[dict[str, Any]]] = {}
    for card in cards:
        key = card.get("canonicalPrintingKey")
        if isinstance(key, str) and key:
            keys.setdefault(key, []).append(card)
    for duplicates in keys.values():
        if len(duplicates) < 2:
            continue
        for card in duplicates:
            card["identityStatus"] = "review"
            card["canonicalPrintingKey"] = None
            card["reviewReasons"] = sorted(set(card["reviewReasons"] + ["duplicate_canonical_printing_key"]))
    review_queue = [review_entry(card) for card in cards if card["identityStatus"] == "review"]
    review_queue.sort(key=lambda row: (str(row["sourceCode"]), str(row["externalId"]), str(row["reviewId"])))
    payload_hash = hashlib.sha256(stable_json(cards)).hexdigest()
    return {
        "schemaVersion": "2.0.0",
        "generatedAt": iso_utc(generated_at),
        "payloadSha256": payload_hash,
        "counts": {
            "cards": len(cards),
            "pokemon": sum(card["market"] == "pokemon" for card in cards),
            "onePiece": sum(card["market"] == "one-piece" for card in cards),
            "gemrateExact": sum(bool(card["gemrateId"]) for card in cards),
            "snkExact": sum(card["snkItemId"] is not None for card in cards),
            "missingPopulationDocument": len(missing_population),
            "missingGemrateId": len(missing_gemrate),
            "identityConfirmed": sum(card["identityStatus"] == "confirmed" for card in cards),
            "identityReview": len(review_queue),
        },
        "gaps": {
            "missingPopulationDocument": missing_population,
            "missingGemrateId": missing_gemrate,
        },
        "cards": cards,
        "identityReviewQueue": review_queue,
    }


def write_crosswalk(document: Mapping[str, Any], output: Path, gemrate_ids: Path, snk_ids: Path) -> None:
    atomic_write(output, json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8"))
    cards = document.get("cards") if isinstance(document, Mapping) else None
    if not isinstance(cards, list):
        raise ValueError("crosswalk cards are missing")
    gemrate = sorted({str(card["gemrateId"]) for card in cards if isinstance(card, Mapping) and card.get("gemrateId")})
    snk = sorted({int(card["snkItemId"]) for card in cards if isinstance(card, Mapping) and isinstance(card.get("snkItemId"), int)})
    atomic_write(gemrate_ids, ("\n".join(gemrate) + "\n").encode("ascii"))
    atomic_write(snk_ids, ("\n".join(str(value) for value in snk) + "\n").encode("ascii"))


def self_test() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        rows = {
            "rows": [
                {
                    "name": "Pikachu #001",
                    "lang": "JP",
                    "url": "https://private.invalid/research/card/snkrdunk/123",
                },
                {
                    "name": "Luffy #OP01-001",
                    "lang": "JP",
                    "url": "https://private.invalid/research/card/ebay/opaque",
                },
            ]
        }
        for market in ("ptcg", "opcg"):
            target = root / "index" / market / "constituents.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps({"rows": [rows["rows"][0 if market == "ptcg" else 1]]}), encoding="utf-8")
        gids = ["a" * 40, "b" * 40]
        for scope, external, gid in (("snkrdunk", "123", gids[0]), ("altxyz", "opaque", gids[1])):
            card = root / "cards" / scope / external
            card.mkdir(parents=True)
            (card / "populations.json").write_text(
                json.dumps({"source": f"https://example.invalid/search?gemrate_id={gid}"}), encoding="utf-8"
            )
            (card / "asset_info.json").write_text(
                json.dumps({"cardName": external, "cardId": "001/S-P", "language": "jp", "setName": "Fixture"}), encoding="utf-8"
            )
        first = build_crosswalk(root, datetime(2026, 7, 23, tzinfo=timezone.utc))
        second = build_crosswalk(root, datetime(2026, 7, 23, tzinfo=timezone.utc))
        return {
            "counts": first["counts"],
            "hashStable": first["payloadSha256"] == second["payloadSha256"],
            "snkId": first["cards"][1]["snkItemId"] if first["cards"][1]["market"] == "pokemon" else first["cards"][0]["snkItemId"],
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build exact private GemRate/SNK crosswalk from G10")
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--gemrate-ids-out", type=Path)
    parser.add_argument("--snk-ids-out", type=Path)
    parser.add_argument("--expect-cards", type=int, default=600)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(self_test(), sort_keys=True))
        return 0
    source_root = args.source_root.resolve()
    if not source_root.is_dir():
        raise SystemExit(f"source root does not exist: {source_root}")
    document = build_crosswalk(source_root)
    if document["counts"]["cards"] != args.expect_cards:
        raise SystemExit(
            f"crosswalk completeness failed: {document['counts']['cards']} != {args.expect_cards}"
        )
    if document["counts"]["gemrateExact"] != args.expect_cards:
        raise SystemExit(
            f"exact GemRate coverage failed: {document['counts']['gemrateExact']} != {args.expect_cards}"
        )
    output = args.out.resolve()
    gemrate_ids = (args.gemrate_ids_out or output.with_name("gemrate-ids.txt")).resolve()
    snk_ids = (args.snk_ids_out or output.with_name("snk-ids.txt")).resolve()
    write_crosswalk(document, output, gemrate_ids, snk_ids)
    print(json.dumps({"status": "ready", "output": str(output), **document["counts"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
