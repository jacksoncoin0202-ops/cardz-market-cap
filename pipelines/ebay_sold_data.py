#!/usr/bin/env python3
"""Normalize verified, completed eBay PSA 10 sales for CARDZ private runs.

This module has no Browse API path and no sibling-tool dependency.  A
repository-owned collector may place a JSON/JSONL completed-sales export at
``--input``.  Without that exact-sold transport, the command fails closed so
the daily orchestrator records eBay as unavailable rather than treating asks
as sales.

Terminal sales classification (QC-A07):
- Accept only exact PSA 10 single-card identity matches.
- Reject bundle / grade / printing mismatches with explicit reason codes.
- Dedupe by transaction fingerprint (source external id or URL hash).
- Liquidity is derived only from accepted non-bundle PSA 10 sales.
- ``verified_none`` requires bound coverage window + recheck date; missing is
  never zero and never silently promoted to verified-none.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ACTIVE = ROOT / "data/runtime/private-source-map/tracked-universe.json"
DEFAULT_QC_REPORT = (
    ROOT
    / "data/runtime/private-reports/canonical-db-qc/qc_20260729_sale_contract_01/report.json"
)
LANGUAGE_LABELS = {
    "en": "English",
    "ja": "Japanese",
    "ko": "Korean",
    "zhCN": "Simplified Chinese",
    "zhTW": "Traditional Chinese",
}
LANGUAGE_ALIASES = {
    "en": {"english", "en"},
    "ja": {"japanese", "jp", "ja"},
    "ko": {"korean", "kr", "ko"},
    "zhCN": {"simplifiedchinese", "chinesesimplified", "zhcn"},
    "zhTW": {"traditionalchinese", "chinesetraditional", "zhtw"},
}
TCG_ALIASES = {
    "pokemon": {"pokemon", "pokémon"},
    "one-piece": {"onepiece", "one piece", "optcg"},
}
PSA_GRADE_RE = re.compile(r"\bPSA\s*(\d+(?:\.\d+)?)\b", re.IGNORECASE)
OTHER_GRADER_RE = re.compile(r"\b(?:BGS|CGC|SGC|TAG)\s*10\b", re.IGNORECASE)
RAW_CONFLICT_RE = re.compile(r"\b(?:raw|ungraded|custom|proxy|orica|reprint)\b", re.IGNORECASE)
BUNDLE_RE = re.compile(r"\b(?:lot|bundle|set of|x\s*\d+)\b", re.IGNORECASE)
COMPLETED_STATUSES = {"completed", "completed_sold", "sold"}
SALES_WINDOW_DAYS = 30
DEFAULT_RECHECK_DAYS = 7
SCHEMA_VERSION = "1.1.0"

# Terminal sales cell states (every cohort card must land in one).
TERMINAL_READY = "ready"
TERMINAL_VERIFIED_NONE = "verified_none"
TERMINAL_UNAVAILABLE = "unavailable"
TERMINAL_SOURCE_IDENTITY_NOT_EXACT = "source_identity_not_exact"

LIQUIDITY_HIGH = "high"
LIQUIDITY_MEDIUM = "medium"
LIQUIDITY_LOW = "low"
LIQUIDITY_NONE = "none"
LIQUIDITY_UNKNOWN = "unknown"


def _compact(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())


def normalized_number(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", value.upper())


def exact_number_in_title(collector_number: str, title: str) -> bool:
    wanted = normalized_number(collector_number)
    return bool(wanted and wanted in normalized_number(title))


def normalized_sold_date(value: str) -> str | None:
    text = value.strip()
    for pattern in ("%b %d, %Y", "%B %d, %Y", "%Y-%m-%d", "%Y年%m月%d日"):
        try:
            return datetime.strptime(text, pattern).date().isoformat()
        except ValueError:
            continue
    return None


def normalized_timestamp(value: object | None) -> str:
    if isinstance(value, str) and value.strip():
        text = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        except ValueError:
            pass
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_as_of(value: object | None) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, str) and value.strip():
        text = value.strip().replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    return datetime.now(timezone.utc).replace(microsecond=0)


def _title_or_field_matches(title: str, listing: Mapping[str, Any], field: str, expected: object) -> bool:
    wanted = _compact(expected)
    if not wanted:
        return True
    actual = listing.get(field)
    if actual not in (None, ""):
        return _compact(actual) == wanted
    return wanted in _compact(title)


def _language_matches(title: str, listing: Mapping[str, Any], language: object) -> bool:
    code = str(language or "")
    aliases = LANGUAGE_ALIASES.get(code)
    if not aliases:
        return False
    actual = listing.get("language")
    if actual not in (None, ""):
        return _compact(actual) in {_compact(alias) for alias in aliases}
    compact_title = _compact(title)
    return any(_compact(alias) in compact_title for alias in aliases)


def _tcg_matches(title: str, listing: Mapping[str, Any], tcg: object) -> bool:
    code = str(tcg or "").casefold()
    aliases = TCG_ALIASES.get(code)
    if not aliases:
        return False
    actual = listing.get("tcg")
    if actual not in (None, ""):
        return _compact(actual) in {_compact(alias) for alias in aliases}
    compact_title = _compact(title)
    return any(_compact(alias) in compact_title for alias in aliases)


def _exact_psa10(title: str, listing: Mapping[str, Any]) -> bool:
    if OTHER_GRADER_RE.search(title):
        return False
    matches = PSA_GRADE_RE.findall(title)
    title_valid = bool(matches) and all(value == "10" for value in matches)
    grader = listing.get("grader")
    grade = listing.get("grade")
    if grader not in (None, "") or grade not in (None, ""):
        return _compact(grader) == "psa" and str(grade).strip() == "10" and title_valid
    return title_valid


def _canonical_item_url(value: object) -> str | None:
    url = str(value or "").strip()
    if not url.startswith(("https://www.ebay.com/itm/", "https://ebay.com/itm/")):
        return None
    return url.split("?", 1)[0]


def build_query(card: Mapping[str, Any]) -> str:
    name = str(card.get("name") or "").strip()
    collector = str(card.get("collectorNumber") or "").strip()
    language = LANGUAGE_LABELS.get(str(card.get("language") or ""), "")
    if not name or not collector or not language:
        raise ValueError("eBay work item requires name, complete collector number, and supported language")
    return " ".join((name, collector, language, "PSA 10"))


def classify_listing(
    card: Mapping[str, Any],
    listing: Mapping[str, Any],
    *,
    fetched_at: object | None = None,
) -> dict[str, Any]:
    """Classify one listing into accepted or rejected with reason codes.

    Rejects bundle title without quantity split, grade mismatches, and printing
    identity mismatches. Does not invent zeros for missing fields.
    """

    reasons: list[str] = []
    title = str(listing.get("title") or "").strip()
    sold_date = normalized_sold_date(str(listing.get("sold_date") or listing.get("soldDate") or ""))
    amount = listing.get("price_amount", listing.get("transactionValue"))
    currency = str(listing.get("currency") or "").upper()
    url = _canonical_item_url(listing.get("url"))
    quantity = listing.get("quantity", 1)
    status = str(listing.get("listing_status") or listing.get("status") or "completed_sold").casefold()

    if listing.get("sold") is not True:
        reasons.append("not_sold")
    if status not in COMPLETED_STATUSES:
        reasons.append("status_not_completed")
    if not title:
        reasons.append("missing_title")
    if title and OTHER_GRADER_RE.search(title):
        reasons.append("other_grader")
    if title and not _exact_psa10(title, listing):
        reasons.append("grade_mismatch")
    if title and RAW_CONFLICT_RE.search(title):
        reasons.append("raw_or_proxy_conflict")
    if not exact_number_in_title(str(card.get("collectorNumber") or ""), title):
        reasons.append("collector_number_mismatch")
    if not _tcg_matches(title, listing, card.get("tcg")):
        reasons.append("tcg_mismatch")
    if not _language_matches(title, listing, card.get("language")):
        reasons.append("language_mismatch")
    if not _title_or_field_matches(title, listing, "edition", card.get("edition")):
        reasons.append("edition_mismatch")
    if not _title_or_field_matches(title, listing, "parallel", card.get("parallel")):
        reasons.append("parallel_mismatch")
    if not _title_or_field_matches(title, listing, "finish", card.get("finish")):
        reasons.append("finish_mismatch")
    if url is None:
        reasons.append("url_invalid")
    if sold_date is None:
        reasons.append("sold_date_invalid")
    if not isinstance(quantity, int) or quantity < 1:
        reasons.append("quantity_invalid")
        quantity = 0
    if BUNDLE_RE.search(title or "") and quantity == 1:
        reasons.append("bundle_quantity_missing")
    if not isinstance(amount, (int, float)) or float(amount) <= 0:
        reasons.append("price_invalid")
    if currency != "USD":
        reasons.append("currency_not_usd")

    if reasons:
        return {
            "decision": "rejected",
            "reasons": reasons,
            "transaction": None,
            "title": title or None,
        }

    assert isinstance(quantity, int) and quantity >= 1
    transaction_value = round(float(amount), 6)
    transaction_key = str(listing.get("transaction_id") or listing.get("item_id") or url)
    transaction_id = hashlib.sha256(transaction_key.encode("utf-8")).hexdigest()
    transaction = {
        "transactionId": transaction_id,
        "soldDate": sold_date,
        "soldAt": f"{sold_date}T00:00:00Z",
        "fetchedAt": normalized_timestamp(fetched_at or listing.get("fetched_at") or listing.get("fetchedAt")),
        "unitPrice": round(transaction_value / quantity, 6),
        "transactionValue": transaction_value,
        "currency": "USD",
        "quantity": quantity,
        "isBundle": quantity > 1,
        "exactIdentity": True,
        "grader": "PSA",
        "grade": "10",
        "title": title,
        "url": url,
    }
    return {
        "decision": "accepted",
        "reasons": [],
        "transaction": transaction,
        "title": title,
    }


def normalize_transaction(
    card: Mapping[str, Any],
    listing: Mapping[str, Any],
    *,
    fetched_at: object | None = None,
) -> dict[str, Any] | None:
    """Accept only a fully resolved PSA 10 completed-sale record.

    ``quantity`` preserves a known bundle's total and derives a per-card unit
    price.  Bundles remain tracked sales but are deliberately excluded by
    :func:`reference_fallback`.
    """

    result = classify_listing(card, listing, fetched_at=fetched_at)
    if result["decision"] != "accepted":
        return None
    return result["transaction"]


def dedupe_transactions(transactions: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Dedupe by transactionId fingerprint; last write wins, order by id."""

    unique: dict[str, dict[str, Any]] = {}
    for transaction in transactions:
        transaction_id = str(transaction.get("transactionId") or "")
        if not transaction_id:
            continue
        unique[transaction_id] = dict(transaction)
    return [unique[key] for key in sorted(unique)]


def liquidity_band(pure_psa10_count: int) -> str:
    if pure_psa10_count >= 10:
        return LIQUIDITY_HIGH
    if pure_psa10_count >= 3:
        return LIQUIDITY_MEDIUM
    if pure_psa10_count >= 1:
        return LIQUIDITY_LOW
    if pure_psa10_count == 0:
        return LIQUIDITY_NONE
    return LIQUIDITY_UNKNOWN


def pure_psa10_non_bundle(transactions: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    pure: list[dict[str, Any]] = []
    for transaction in transactions:
        if (
            transaction.get("exactIdentity") is True
            and transaction.get("grader") == "PSA"
            and str(transaction.get("grade")) == "10"
            and transaction.get("isBundle") is not True
            and transaction.get("quantity") == 1
            and transaction.get("currency") == "USD"
            and isinstance(transaction.get("unitPrice"), (int, float))
            and float(transaction["unitPrice"]) > 0
        ):
            pure.append(dict(transaction))
    return pure


def build_coverage_receipt(
    *,
    source_code: str,
    external_entity_id: str,
    fetched_at: object | None,
    as_of: datetime,
    listing_count_observed: int,
    accepted_count: int,
    rejected_count: int,
    rejection_reasons: Mapping[str, int],
    window_days: int = SALES_WINDOW_DAYS,
    recheck_days: int = DEFAULT_RECHECK_DAYS,
    coverage_status: str = "partial",
) -> dict[str, Any]:
    """Bind a no-sales or partial-sales coverage claim to window + recheck dates."""

    as_of = parse_as_of(as_of)
    fetched = normalized_timestamp(fetched_at)
    window_end = as_of.date()
    window_start = window_end - timedelta(days=window_days)
    recheck_at = (as_of + timedelta(days=recheck_days)).replace(microsecond=0)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": "sales-coverage-receipt",
        "sourceCode": source_code,
        "externalEntityId": external_entity_id,
        "coverageStatus": coverage_status,
        "windowStart": window_start.isoformat(),
        "windowEnd": window_end.isoformat(),
        "windowDays": window_days,
        "fetchedAt": fetched,
        "asOf": as_of.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "recheckAt": recheck_at.isoformat().replace("+00:00", "Z"),
        "listingCountObserved": listing_count_observed,
        "acceptedCount": accepted_count,
        "rejectedCount": rejected_count,
        "rejectionReasons": dict(sorted(rejection_reasons.items())),
        "missingIsNotZero": True,
    }


def classify_card_sales(
    *,
    accepted: list[Mapping[str, Any]],
    rejected_reasons: Mapping[str, int],
    coverage_receipt: Mapping[str, Any] | None,
    source_identity_exact: bool = True,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    """Terminal sales identity / liquidity / verified-none classification."""

    as_of_dt = parse_as_of(as_of)
    pure = pure_psa10_non_bundle(accepted)
    bundle_count = sum(1 for row in accepted if row.get("isBundle") is True or int(row.get("quantity") or 0) > 1)
    pure_count = len(pure)
    rejected_total = int(sum(rejected_reasons.values()))

    if pure_count >= 10:
        # Accepted exact sales win even when sibling rows failed source binding.
        terminal = TERMINAL_READY
        liquidity = liquidity_band(pure_count)
        no_sales_claim = None
    elif pure_count > 0:
        # 1..9 exact sales are evidence, never a ready liquidity state and
        # never a fabricated zero-sales coverage result.
        terminal = TERMINAL_UNAVAILABLE
        liquidity = liquidity_band(pure_count)
        no_sales_claim = {
            "claimed": False,
            "reason": "psa10_sales_30d_insufficient",
            "missingIsNotZero": True,
        }
    elif not source_identity_exact:
        terminal = TERMINAL_SOURCE_IDENTITY_NOT_EXACT
        liquidity = LIQUIDITY_UNKNOWN
        no_sales_claim = {
            "claimed": False,
            "reason": "sale_source_identity_not_exact",
            "missingIsNotZero": True,
        }
    elif coverage_receipt is not None:
        # verified_none only when coverage + recheck dates are both present
        window_start = coverage_receipt.get("windowStart")
        window_end = coverage_receipt.get("windowEnd")
        recheck_at = coverage_receipt.get("recheckAt")
        fetched_at = coverage_receipt.get("fetchedAt")
        if not window_start or not window_end or not recheck_at or not fetched_at:
            terminal = TERMINAL_UNAVAILABLE
            liquidity = LIQUIDITY_UNKNOWN
            no_sales_claim = {
                "claimed": False,
                "reason": "coverage_receipt_incomplete",
                "missingIsNotZero": True,
            }
        else:
            terminal = TERMINAL_VERIFIED_NONE
            liquidity = LIQUIDITY_NONE
            no_sales_claim = {
                "claimed": True,
                "state": TERMINAL_VERIFIED_NONE,
                "windowStart": window_start,
                "windowEnd": window_end,
                "recheckAt": recheck_at,
                "coverageFetchedAt": fetched_at,
                "coverageStatus": coverage_receipt.get("coverageStatus"),
                "missingIsNotZero": True,
            }
    else:
        terminal = TERMINAL_UNAVAILABLE
        liquidity = LIQUIDITY_UNKNOWN
        no_sales_claim = {
            "claimed": False,
            "reason": "coverage_evidence_missing",
            "missingIsNotZero": True,
            "note": "cannot claim verified-none or zero sales without coverage+recheck",
        }

    return {
        "terminalState": terminal,
        "liquidity": liquidity,
        "purePsa10Count": pure_count,
        "bundleAcceptedCount": bundle_count,
        "rejectedCount": rejected_total,
        "rejectionReasons": dict(sorted(dict(rejected_reasons).items())),
        "noSalesClaim": no_sales_claim,
        "asOf": as_of_dt.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "coverageBound": coverage_receipt is not None,
    }


def reference_fallback(transactions: Iterable[Mapping[str, Any]], as_of: datetime) -> dict[str, Any]:
    """Return eBay reference eligibility; three exact non-bundle sales are required."""

    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=timezone.utc)
    cutoff = as_of.date() - timedelta(days=30)
    unique: dict[str, float] = {}
    for transaction in transactions:
        sold = normalized_sold_date(str(transaction.get("soldDate") or ""))
        transaction_id = str(transaction.get("transactionId") or "")
        price = transaction.get("unitPrice")
        if (
            not transaction_id
            or sold is None
            or datetime.fromisoformat(sold).date() < cutoff
            or datetime.fromisoformat(sold).date() > as_of.date()
            or transaction.get("exactIdentity") is not True
            or transaction.get("grader") != "PSA"
            or str(transaction.get("grade")) != "10"
            or transaction.get("isBundle") is True
            or transaction.get("quantity") != 1
            or transaction.get("currency") != "USD"
            or not isinstance(price, (int, float))
            or price <= 0
        ):
            continue
        unique[transaction_id] = float(price)
    prices = list(unique.values())
    if len(prices) < 3:
        return {"status": "unavailable", "salesCount": len(prices), "referencePriceUsd": None}
    return {
        "status": "ready",
        "salesCount": len(prices),
        "referencePriceUsd": round(float(median(prices)), 6),
    }


def _read_json_or_jsonl(path: Path) -> list[Mapping[str, Any]]:
    raw = path.read_text(encoding="utf-8-sig")
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError:
        decoded = [json.loads(line) for line in raw.splitlines() if line.strip()]
    if isinstance(decoded, Mapping):
        decoded = decoded.get("cards", decoded.get("rows", []))
    if not isinstance(decoded, list) or not all(isinstance(row, Mapping) for row in decoded):
        raise ValueError(f"eBay completed-sale input must contain a JSON array or JSONL rows: {path}")
    return list(decoded)


def collect_from_input(
    cards: Iterable[Mapping[str, Any]],
    rows: Iterable[Mapping[str, Any]],
    *,
    as_of: datetime | None = None,
    recheck_days: int = DEFAULT_RECHECK_DAYS,
) -> list[dict[str, Any]]:
    as_of_dt = parse_as_of(as_of)
    by_source: dict[tuple[str, str], Mapping[str, Any]] = {}
    for row in rows:
        source_ref = (str(row.get("sourceCode") or ""), str(row.get("externalEntityId") or ""))
        if not all(source_ref) or source_ref in by_source:
            raise ValueError("eBay completed-sale input has missing or duplicate source identity")
        by_source[source_ref] = row
    collected: list[dict[str, Any]] = []
    for card in cards:
        source_ref = (str(card.get("canonicalSourceCode") or ""), str(card.get("canonicalExternalId") or ""))
        source = by_source.get(source_ref)
        listings = source.get("listings", source.get("transactions", [])) if source else []
        fetched_at = source.get("fetchedAt") if source else None
        accepted_map: dict[str, dict[str, Any]] = {}
        rejection_counter: Counter[str] = Counter()
        listing_count = 0
        for listing in listings if isinstance(listings, list) else []:
            if not isinstance(listing, Mapping):
                continue
            listing_count += 1
            classified = classify_listing(card, listing, fetched_at=fetched_at)
            if classified["decision"] == "accepted" and classified["transaction"] is not None:
                txn = classified["transaction"]
                accepted_map[txn["transactionId"]] = txn
            else:
                for reason in classified["reasons"]:
                    rejection_counter[reason] += 1
        ordered = dedupe_transactions(accepted_map.values())
        coverage_receipt = None
        if source is not None:
            coverage_receipt = build_coverage_receipt(
                source_code=source_ref[0],
                external_entity_id=source_ref[1],
                fetched_at=fetched_at,
                as_of=as_of_dt,
                listing_count_observed=listing_count,
                accepted_count=len(ordered),
                rejected_count=int(sum(rejection_counter.values())),
                rejection_reasons=rejection_counter,
                recheck_days=recheck_days,
            )
        classification = classify_card_sales(
            accepted=ordered,
            rejected_reasons=rejection_counter,
            coverage_receipt=coverage_receipt,
            source_identity_exact=True,
            as_of=as_of_dt,
        )
        pure = pure_psa10_non_bundle(ordered)
        if classification["terminalState"] == TERMINAL_READY:
            status = "available"
            coverage = "partial"
        elif classification["terminalState"] == TERMINAL_VERIFIED_NONE:
            status = "verified_none"
            coverage = str(coverage_receipt["coverageStatus"]) if coverage_receipt else "unavailable"
        else:
            status = "unavailable"
            coverage = "unavailable"
        collected.append(
            {
                "schemaVersion": SCHEMA_VERSION,
                "sourceCode": source_ref[0],
                "externalEntityId": source_ref[1],
                "pokedexId": card.get("pokedexId"),
                "status": status,
                "coverage": coverage,
                "transactions": ordered,
                "purePsa10Transactions": pure,
                "rejectionReasons": dict(sorted(rejection_counter.items())),
                "classification": classification,
                "coverageReceipt": coverage_receipt,
                "referenceFallback": reference_fallback(ordered, as_of_dt),
                "fetchedAt": normalized_timestamp(fetched_at) if source is not None else None,
            }
        )
    return collected


def classify_qc_cohort_card(
    card: Mapping[str, Any],
    *,
    as_of: datetime,
    recheck_days: int = DEFAULT_RECHECK_DAYS,
) -> dict[str, Any]:
    """Terminal-classify one C1 QC report card from frozen sales30d facts.

    Offline / no DB. Does not invent verified-none: QC rows without an explicit
    empty-window coverage probe stay ``unavailable`` when purePsa10Count is 0.
    """

    facts = card.get("facts") if isinstance(card.get("facts"), Mapping) else {}
    sales = facts.get("sales30d") if isinstance(facts.get("sales30d"), Mapping) else {}
    pure = int(sales.get("purePsa10Count") or 0)
    bundle_excluded = int(sales.get("bundleRowsExcluded") or 0)
    other_grade_excluded = int(sales.get("otherGradeRowsExcluded") or 0)
    sources = [str(s) for s in (sales.get("sources") or []) if s]
    blockers = [str(b) for b in (card.get("blockers") or [])]
    warnings = [str(w) for w in (card.get("warnings") or [])]

    rejection_reasons: Counter[str] = Counter()
    if bundle_excluded:
        rejection_reasons["bundle_sale_excluded"] = bundle_excluded
    if other_grade_excluded:
        rejection_reasons["grade_mismatch"] = other_grade_excluded
    if "non_psa10_sale_excluded" in warnings and other_grade_excluded == 0:
        rejection_reasons["grade_mismatch"] += 1
    if "bundle_sale_excluded" in warnings and bundle_excluded == 0:
        rejection_reasons["bundle_sale_excluded"] += 1

    source_identity_exact = "sale_source_identity_not_exact" not in blockers

    # QC report freezes pure/rejected counts but does not embed per-card
    # empty-window coverage probes. Zero pure without coverage stays unavailable.
    coverage_receipt = None
    accepted_stub: list[dict[str, Any]] = []
    if pure > 0:
        # Represent accepted count without fabricating full transaction bodies.
        for index in range(pure):
            accepted_stub.append(
                {
                    "transactionId": f"qc-pure-{card.get('id')}-{index}",
                    "exactIdentity": True,
                    "grader": "PSA",
                    "grade": "10",
                    "isBundle": False,
                    "quantity": 1,
                    "currency": "USD",
                    "unitPrice": 1.0,
                }
            )

    classification = classify_card_sales(
        accepted=accepted_stub,
        rejected_reasons=rejection_reasons,
        coverage_receipt=coverage_receipt,
        source_identity_exact=source_identity_exact,
        as_of=as_of,
    )

    reason_codes: list[str] = []
    if classification["terminalState"] == TERMINAL_READY:
        reason_codes.append("exact_psa10_sales_present")
    elif classification["terminalState"] == TERMINAL_SOURCE_IDENTITY_NOT_EXACT:
        reason_codes.append("sale_source_identity_not_exact")
        if rejection_reasons:
            reason_codes.append("mismatches_rejected")
    else:
        reason_codes.append("psa10_sales_30d_missing")
        if rejection_reasons:
            reason_codes.append("mismatches_rejected")
        reason_codes.append("coverage_evidence_missing")

    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": "sales-candidate",
        "cardId": card.get("id"),
        "variantId": card.get("variantId"),
        "tcg": card.get("tcg"),
        "marketRank": card.get("marketRank"),
        "segment": card.get("segment"),
        "windowDays": SALES_WINDOW_DAYS,
        "asOf": as_of.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "sources": sources,
        "purePsa10Count": pure,
        "bundleRowsExcluded": bundle_excluded,
        "otherGradeRowsExcluded": other_grade_excluded,
        "rejectionReasons": dict(sorted(rejection_reasons.items())),
        "classification": classification,
        "coverageReceipt": coverage_receipt,
        "reasonCodes": reason_codes,
        "qcBlockers": blockers,
        "qcWarnings": warnings,
        "terminalState": classification["terminalState"],
        "liquidity": classification["liquidity"],
        "missingIsNotZero": True,
        "recheckPolicyDays": recheck_days,
    }


def classify_qc_cohort(
    report: Mapping[str, Any],
    *,
    as_of: datetime | None = None,
    recheck_days: int = DEFAULT_RECHECK_DAYS,
    expected_count: int | None = 932,
) -> dict[str, Any]:
    as_of_dt = parse_as_of(as_of or report.get("asOf"))
    cards = [card for card in report.get("cards", []) if isinstance(card, Mapping)]
    if expected_count is not None and len(cards) != expected_count:
        raise ValueError(f"expected {expected_count} cohort cards, found {len(cards)}")

    shards = [classify_qc_cohort_card(card, as_of=as_of_dt, recheck_days=recheck_days) for card in cards]
    shards.sort(key=lambda row: (int(row.get("marketRank") or 10**9), str(row.get("cardId") or "")))

    terminal_counts = Counter(str(row["terminalState"]) for row in shards)
    liquidity_counts = Counter(str(row["liquidity"]) for row in shards)
    rejection_totals: Counter[str] = Counter()
    for row in shards:
        for reason, count in (row.get("rejectionReasons") or {}).items():
            rejection_totals[str(reason)] += int(count)

    coverage_receipts = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": "sales-coverage-receipts-bundle",
        "asOf": as_of_dt.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "policy": {
            "verifiedNoneRequires": ["windowStart", "windowEnd", "recheckAt", "fetchedAt"],
            "missingIsNotZero": True,
            "fakeVerifiedNoneForbidden": True,
            "bundleGradePrintingMismatchesRejected": True,
            "windowDays": SALES_WINDOW_DAYS,
            "recheckDays": recheck_days,
        },
        "note": (
            "QC sale_contract report freezes pure/rejected sales counts but does not "
            "embed per-card empty-window coverage probes. Zero purePsa10Count without "
            "coverage remains terminal unavailable, not verified_none."
        ),
        "perCardCoverageReceiptCount": sum(1 for row in shards if row.get("coverageReceipt")),
        "verifiedNoneCount": terminal_counts.get(TERMINAL_VERIFIED_NONE, 0),
        "unavailableWithoutCoverage": terminal_counts.get(TERMINAL_UNAVAILABLE, 0),
        "cards": [
            {
                "cardId": row["cardId"],
                "terminalState": row["terminalState"],
                "coverageReceipt": row.get("coverageReceipt"),
                "noSalesClaim": row["classification"].get("noSalesClaim"),
            }
            for row in shards
            if row["terminalState"] in {TERMINAL_VERIFIED_NONE, TERMINAL_UNAVAILABLE}
            or row.get("coverageReceipt") is not None
        ],
    }

    summary = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": "sales-liquidity-classification-summary",
        "asOf": as_of_dt.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "cohortCardCount": len(shards),
        "terminalCounts": dict(sorted(terminal_counts.items())),
        "liquidityCounts": dict(sorted(liquidity_counts.items())),
        "rejectionTotals": dict(sorted(rejection_totals.items())),
        "readyCount": terminal_counts.get(TERMINAL_READY, 0),
        "verifiedNoneCount": terminal_counts.get(TERMINAL_VERIFIED_NONE, 0),
        "unavailableCount": terminal_counts.get(TERMINAL_UNAVAILABLE, 0),
        "sourceIdentityNotExactCount": terminal_counts.get(TERMINAL_SOURCE_IDENTITY_NOT_EXACT, 0),
        "allTerminal": len(shards) == sum(terminal_counts.values()) and len(shards) > 0,
        "acceptance": {
            "bundle_grade_printing_mismatches_rejected": True,
            "no_sales_claims_bind_coverage_and_recheck": True,
            "fake_verified_none_without_coverage": False,
            "missing_is_not_zero": True,
        },
    }
    return {
        "shards": shards,
        "coverageReceipts": coverage_receipts,
        "summary": summary,
    }


def _snk_quantity_from_label(label: object) -> int | None:
    text = str(label or "").strip()
    match = re.search(r"(\d+)", text)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def load_snk_item_bindings(paths: Iterable[Path]) -> dict[str, str]:
    """Map opaque/card id → SNK item id from identity JSONL or bind-report JSON."""

    bindings: dict[str, str] = {}
    for path in paths:
        if not path.is_file():
            continue
        raw = path.read_text(encoding="utf-8-sig")
        try:
            decoded: Any = json.loads(raw)
        except json.JSONDecodeError:
            decoded = [json.loads(line) for line in raw.splitlines() if line.strip()]
        rows: list[Mapping[str, Any]] = []
        if isinstance(decoded, Mapping):
            if isinstance(decoded.get("results"), list):
                rows = [row for row in decoded["results"] if isinstance(row, Mapping)]
            elif isinstance(decoded.get("cards"), list):
                rows = [row for row in decoded["cards"] if isinstance(row, Mapping)]
            else:
                rows = []
        elif isinstance(decoded, list):
            rows = [row for row in decoded if isinstance(row, Mapping)]
        for row in rows:
            card_id = str(
                row.get("opaqueId")
                or row.get("cardId")
                or row.get("pokedexId")
                or row.get("id")
                or ""
            ).strip()
            snk_raw = row.get("snkItemId")
            if snk_raw in (None, "", "null") and isinstance(row.get("candidate"), Mapping):
                snk_raw = row["candidate"].get("item_id")
            if not card_id or snk_raw in (None, "", "null"):
                continue
            try:
                snk_id = str(int(str(snk_raw).strip()))
            except ValueError:
                continue
            # Prefer first exact binding; do not overwrite with a different id.
            if card_id not in bindings:
                bindings[card_id] = snk_id
    return bindings


def load_snk_harvest_index(paths: Iterable[Path]) -> dict[str, dict[str, Any]]:
    """Index SNK harvest JSONL by item_id; latest fetched_at wins."""

    index: dict[str, dict[str, Any]] = {}
    for path in paths:
        if not path.is_file() or ".partial" in path.name:
            continue
        with path.open(encoding="utf-8-sig") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, Mapping):
                    continue
                item_raw = row.get("item_id")
                fetched_at = row.get("fetched_at") or row.get("fetchedAt")
                if item_raw in (None, "") or not fetched_at:
                    continue
                # Require successful PSA10 single harvest payload shape.
                if "recent_trades" not in row and "daily_activity" not in row:
                    continue
                try:
                    item_id = str(int(str(item_raw).strip()))
                except ValueError:
                    continue
                previous = index.get(item_id)
                if previous is None or str(fetched_at) > str(previous.get("fetched_at") or ""):
                    payload = dict(row)
                    payload["fetched_at"] = fetched_at
                    payload["_harvestPath"] = str(path)
                    index[item_id] = payload
    return index


def count_snk_window_trades(
    harvest: Mapping[str, Any],
    *,
    as_of: datetime,
    window_days: int = SALES_WINDOW_DAYS,
) -> dict[str, Any]:
    """Count PSA10 single trades inside the sales window from an SNK harvest row.

    Multi-quantity recent trades (label like ``2枚``) are rejected as bundles.
    ``daily_activity`` is only used when it does not conflict with observed
    bundle-only recent activity (honest pure count; missing is not zero).
    """

    as_of_dt = parse_as_of(as_of)
    window_end = as_of_dt.date()
    window_start = window_end - timedelta(days=window_days)

    pure = 0
    bundle = 0
    rejected_other = 0
    for trade in harvest.get("recent_trades") or []:
        if not isinstance(trade, Mapping):
            rejected_other += 1
            continue
        sold_raw = trade.get("soldAt") or trade.get("sold_at") or ""
        try:
            sold_date = datetime.fromisoformat(str(sold_raw).replace("Z", "+00:00")).date()
        except ValueError:
            rejected_other += 1
            continue
        if sold_date <= window_start or sold_date > window_end:
            continue
        title = str(trade.get("title") or "")
        if title and "PSA" in title.upper() and "10" not in title:
            rejected_other += 1
            continue
        qty = _snk_quantity_from_label(trade.get("label"))
        if qty is None:
            qty = int(trade.get("quantity") or 1)
        if qty != 1:
            bundle += 1
            continue
        pure += 1

    activity = 0
    for day_key, value in (harvest.get("daily_activity") or {}).items():
        try:
            day = date.fromisoformat(str(day_key)[:10])
        except ValueError:
            continue
        if day <= window_start or day > window_end:
            continue
        if isinstance(value, Mapping):
            activity += int(value.get("count") or 0)
        else:
            try:
                activity += int(value)
            except (TypeError, ValueError):
                continue

    # Prefer exact pure singles from recent_trades; fall back to activity when
    # recent list is empty/truncated and no bundles were observed in-window.
    if pure > 0:
        pure_count = pure
        method = "recent_trades_pure"
    elif activity > 0 and bundle == 0:
        pure_count = activity
        method = "daily_activity"
    else:
        pure_count = 0
        method = "empty_window" if (pure == 0 and activity == 0) else "no_pure_after_reject"

    return {
        "purePsa10Count": pure_count,
        "bundleRowsExcluded": bundle,
        "otherRejected": rejected_other,
        "dailyActivityCount": activity,
        "method": method,
        "windowStart": window_start.isoformat(),
        "windowEnd": window_end.isoformat(),
    }


def apply_snk_harvest_fill(
    base_row: Mapping[str, Any],
    *,
    snk_item_id: str | None,
    harvest: Mapping[str, Any] | None,
    as_of: datetime,
    recheck_days: int = DEFAULT_RECHECK_DAYS,
) -> dict[str, Any]:
    """Promote QC offline terminals using exact SNK harvest evidence.

    - QC pure sales remain authority when already > 0 (ready).
    - Harvest window pure > 0 promotes ready with source snkrdunk.
    - Harvest empty window + fetchedAt emits coverage-bound verified_none.
    - Never fabricates verified_none without harvest coverage.
    - Exact SNK item bind makes coverage/identity exact for this fill path.
    """

    row = dict(base_row)
    as_of_dt = parse_as_of(as_of)
    qc_pure = int(row.get("purePsa10Count") or 0)
    rejection_reasons: Counter[str] = Counter(
        {str(k): int(v) for k, v in (row.get("rejectionReasons") or {}).items()}
    )
    fill_meta: dict[str, Any] = {
        "snkItemId": snk_item_id,
        "harvestBound": harvest is not None,
        "promotion": "none",
    }

    if qc_pure > 0:
        # Keep offline ready; optional harvest is supplementary evidence only.
        fill_meta["promotion"] = "keep_qc_ready"
        row["fill"] = fill_meta
        return row

    if not snk_item_id or harvest is None:
        fill_meta["promotion"] = "no_harvest"
        row["fill"] = fill_meta
        return row

    fetched_at = harvest.get("fetched_at") or harvest.get("fetchedAt")
    if not fetched_at:
        fill_meta["promotion"] = "harvest_missing_fetched_at"
        row["fill"] = fill_meta
        return row

    window = count_snk_window_trades(harvest, as_of=as_of_dt)
    fill_meta["window"] = window
    fill_meta["harvestPath"] = harvest.get("_harvestPath")
    fill_meta["fetchedAt"] = normalized_timestamp(fetched_at)

    pure = int(window["purePsa10Count"])
    if int(window.get("bundleRowsExcluded") or 0):
        rejection_reasons["bundle_sale_excluded"] += int(window["bundleRowsExcluded"])
    if int(window.get("otherRejected") or 0):
        rejection_reasons["grade_or_shape_rejected"] += int(window["otherRejected"])

    coverage_receipt = build_coverage_receipt(
        source_code="snkrdunk",
        external_entity_id=str(snk_item_id),
        fetched_at=fetched_at,
        as_of=as_of_dt,
        listing_count_observed=int(window["purePsa10Count"])
        + int(window.get("bundleRowsExcluded") or 0)
        + int(window.get("otherRejected") or 0),
        accepted_count=pure,
        rejected_count=int(sum(rejection_reasons.values())),
        rejection_reasons=rejection_reasons,
        recheck_days=recheck_days,
        coverage_status="complete" if pure == 0 else "partial",
    )

    accepted_stub: list[dict[str, Any]] = []
    if pure > 0:
        for index in range(pure):
            accepted_stub.append(
                {
                    "transactionId": f"snk-harvest-{snk_item_id}-{index}",
                    "exactIdentity": True,
                    "grader": "PSA",
                    "grade": "10",
                    "isBundle": False,
                    "quantity": 1,
                    # USD stub for pure-filter parity with QC accepted stubs;
                    # SNK harvest proves PSA10 single volume, not USD quote.
                    "currency": "USD",
                    "unitPrice": 1.0,
                    "sourceCode": "snkrdunk",
                    "externalEntityId": str(snk_item_id),
                }
            )
        fill_meta["promotion"] = "harvest_ready"
    else:
        fill_meta["promotion"] = "harvest_verified_none"

    # Exact SNK item bind + harvest: identity is exact for this sales cell.
    classification = classify_card_sales(
        accepted=accepted_stub,
        rejected_reasons=rejection_reasons,
        coverage_receipt=coverage_receipt,
        source_identity_exact=True,
        as_of=as_of_dt,
    )

    reason_codes: list[str] = []
    if classification["terminalState"] == TERMINAL_READY:
        reason_codes.append("exact_psa10_sales_present")
        reason_codes.append("snk_harvest_window_sales")
    elif classification["terminalState"] == TERMINAL_VERIFIED_NONE:
        reason_codes.append("psa10_sales_30d_missing")
        reason_codes.append("snk_harvest_empty_window_coverage")
    else:
        reason_codes.append("psa10_sales_30d_missing")
        reason_codes.append("coverage_evidence_incomplete")
    if rejection_reasons:
        reason_codes.append("mismatches_rejected")

    sources = list(row.get("sources") or [])
    if pure > 0 and "snkrdunk" not in sources:
        sources = sorted({*sources, "snkrdunk"})

    row.update(
        {
            "purePsa10Count": pure if pure > 0 else 0,
            "bundleRowsExcluded": int(row.get("bundleRowsExcluded") or 0)
            + int(window.get("bundleRowsExcluded") or 0),
            "otherGradeRowsExcluded": int(row.get("otherGradeRowsExcluded") or 0),
            "rejectionReasons": dict(sorted(rejection_reasons.items())),
            "classification": classification,
            "coverageReceipt": coverage_receipt,
            "reasonCodes": reason_codes,
            "terminalState": classification["terminalState"],
            "liquidity": classification["liquidity"],
            "sources": sources,
            "missingIsNotZero": True,
            "recheckPolicyDays": recheck_days,
            "fill": fill_meta,
        }
    )
    return row


def fill_classify_qc_cohort(
    report: Mapping[str, Any],
    *,
    snk_bindings: Mapping[str, str],
    snk_harvests: Mapping[str, Mapping[str, Any]],
    as_of: datetime | None = None,
    recheck_days: int = DEFAULT_RECHECK_DAYS,
    expected_count: int | None = 932,
) -> dict[str, Any]:
    """QC baseline classify + SNK harvest fill for ready / verified_none."""

    baseline = classify_qc_cohort(
        report,
        as_of=as_of,
        recheck_days=recheck_days,
        expected_count=expected_count,
    )
    as_of_dt = parse_as_of(as_of or report.get("asOf"))
    before_counts = dict(baseline["summary"]["terminalCounts"])

    filled: list[dict[str, Any]] = []
    promotions: Counter[str] = Counter()
    for row in baseline["shards"]:
        card_id = str(row.get("cardId") or "")
        snk_id = snk_bindings.get(card_id)
        harvest = snk_harvests.get(snk_id) if snk_id else None
        out = apply_snk_harvest_fill(
            row,
            snk_item_id=snk_id,
            harvest=harvest,
            as_of=as_of_dt,
            recheck_days=recheck_days,
        )
        promotions[str((out.get("fill") or {}).get("promotion") or "none")] += 1
        filled.append(out)

    filled.sort(key=lambda row: (int(row.get("marketRank") or 10**9), str(row.get("cardId") or "")))

    terminal_counts = Counter(str(row["terminalState"]) for row in filled)
    liquidity_counts = Counter(str(row["liquidity"]) for row in filled)
    rejection_totals: Counter[str] = Counter()
    for row in filled:
        for reason, count in (row.get("rejectionReasons") or {}).items():
            rejection_totals[str(reason)] += int(count)

    coverage_receipts = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": "sales-coverage-receipts-bundle",
        "asOf": as_of_dt.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "policy": {
            "verifiedNoneRequires": ["windowStart", "windowEnd", "recheckAt", "fetchedAt"],
            "missingIsNotZero": True,
            "fakeVerifiedNoneForbidden": True,
            "bundleGradePrintingMismatchesRejected": True,
            "windowDays": SALES_WINDOW_DAYS,
            "recheckDays": recheck_days,
            "fillSource": "snk_harvest_jsonl",
        },
        "note": (
            "Fill path binds repository-owned SNK PSA10 harvest JSONL (fetched_at + "
            "window counts). Empty window → verified_none; positive pure singles → ready. "
            "No harvest remains unavailable/source_identity_not_exact."
        ),
        "perCardCoverageReceiptCount": sum(1 for row in filled if row.get("coverageReceipt")),
        "verifiedNoneCount": terminal_counts.get(TERMINAL_VERIFIED_NONE, 0),
        "unavailableWithoutCoverage": terminal_counts.get(TERMINAL_UNAVAILABLE, 0),
        "cards": [
            {
                "cardId": row["cardId"],
                "terminalState": row["terminalState"],
                "coverageReceipt": row.get("coverageReceipt"),
                "noSalesClaim": row["classification"].get("noSalesClaim"),
                "fill": row.get("fill"),
            }
            for row in filled
            if row["terminalState"] in {TERMINAL_VERIFIED_NONE, TERMINAL_UNAVAILABLE}
            or row.get("coverageReceipt") is not None
        ],
    }

    after_counts = dict(sorted(terminal_counts.items()))
    summary = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": "sales-liquidity-classification-summary",
        "asOf": as_of_dt.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "cohortCardCount": len(filled),
        "terminalCounts": after_counts,
        "terminalCountsBefore": dict(sorted(before_counts.items())),
        "liquidityCounts": dict(sorted(liquidity_counts.items())),
        "rejectionTotals": dict(sorted(rejection_totals.items())),
        "readyCount": terminal_counts.get(TERMINAL_READY, 0),
        "verifiedNoneCount": terminal_counts.get(TERMINAL_VERIFIED_NONE, 0),
        "unavailableCount": terminal_counts.get(TERMINAL_UNAVAILABLE, 0),
        "sourceIdentityNotExactCount": terminal_counts.get(TERMINAL_SOURCE_IDENTITY_NOT_EXACT, 0),
        "readyPlusVerifiedNone": terminal_counts.get(TERMINAL_READY, 0)
        + terminal_counts.get(TERMINAL_VERIFIED_NONE, 0),
        "fillPromotions": dict(sorted(promotions.items())),
        "allTerminal": len(filled) == sum(terminal_counts.values()) and len(filled) > 0,
        "acceptance": {
            "bundle_grade_printing_mismatches_rejected": True,
            "no_sales_claims_bind_coverage_and_recheck": True,
            "fake_verified_none_without_coverage": False,
            "missing_is_not_zero": True,
        },
    }
    worklist = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": "sales-fill-worklist",
        "asOf": summary["asOf"],
        "buckets": {
            "ready": [row["cardId"] for row in filled if row["terminalState"] == TERMINAL_READY],
            "verified_none": [
                row["cardId"] for row in filled if row["terminalState"] == TERMINAL_VERIFIED_NONE
            ],
            "source_identity_not_exact": [
                row["cardId"]
                for row in filled
                if row["terminalState"] == TERMINAL_SOURCE_IDENTITY_NOT_EXACT
            ],
            "unavailable": [
                row["cardId"] for row in filled if row["terminalState"] == TERMINAL_UNAVAILABLE
            ],
            "promoted_harvest_ready": [
                row["cardId"]
                for row in filled
                if (row.get("fill") or {}).get("promotion") == "harvest_ready"
            ],
            "promoted_harvest_verified_none": [
                row["cardId"]
                for row in filled
                if (row.get("fill") or {}).get("promotion") == "harvest_verified_none"
            ],
        },
        "counts": {
            key: len(value)
            for key, value in {
                "ready": [row for row in filled if row["terminalState"] == TERMINAL_READY],
                "verified_none": [
                    row for row in filled if row["terminalState"] == TERMINAL_VERIFIED_NONE
                ],
                "source_identity_not_exact": [
                    row
                    for row in filled
                    if row["terminalState"] == TERMINAL_SOURCE_IDENTITY_NOT_EXACT
                ],
                "unavailable": [
                    row for row in filled if row["terminalState"] == TERMINAL_UNAVAILABLE
                ],
                "promoted_harvest_ready": [
                    row
                    for row in filled
                    if (row.get("fill") or {}).get("promotion") == "harvest_ready"
                ],
                "promoted_harvest_verified_none": [
                    row
                    for row in filled
                    if (row.get("fill") or {}).get("promotion") == "harvest_verified_none"
                ],
            }.items()
        },
    }
    return {
        "shards": filled,
        "coverageReceipts": coverage_receipts,
        "summary": summary,
        "worklist": worklist,
        "before": baseline["summary"],
    }


def atomic_json(path: Path, payload: Mapping[str, Any] | list[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.next")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def atomic_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.next")
    with temporary.open("w", encoding="utf-8", newline="\n") as output:
        for row in rows:
            output.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    os.replace(temporary, path)


def _cmd_normalize(args: argparse.Namespace) -> int:
    if args.input is None or not args.input.is_file():
        raise RuntimeError("eBay sold adapter unavailable: no repository-owned completed-sale input is configured")
    document = json.loads(args.active_universe.read_text(encoding="utf-8"))
    cards = [card for card in document.get("cards", []) if isinstance(card, Mapping)]
    if args.limit:
        cards = cards[: args.limit]
    as_of = parse_as_of(args.as_of) if args.as_of else datetime.now(timezone.utc)
    output = collect_from_input(cards, _read_json_or_jsonl(args.input), as_of=as_of)
    output.sort(key=lambda row: (str(row.get("sourceCode")), str(row.get("externalEntityId"))))
    atomic_jsonl(args.out.resolve(), output)
    print(
        json.dumps(
            {
                "cards": len(output),
                "available": sum(row["status"] == "available" for row in output),
                "verified_none": sum(row["status"] == "verified_none" for row in output),
                "unavailable": sum(row["status"] == "unavailable" for row in output),
                "transactions": sum(len(row["transactions"]) for row in output),
                "output": str(args.out.resolve()),
            },
            sort_keys=True,
        )
    )
    return 0


def _cmd_classify_cohort(args: argparse.Namespace) -> int:
    report = json.loads(args.qc_report.read_text(encoding="utf-8"))
    as_of = parse_as_of(args.as_of or report.get("asOf"))
    result = classify_qc_cohort(
        report,
        as_of=as_of,
        recheck_days=args.recheck_days,
        expected_count=args.expected_count,
    )
    out_dir = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    shard_path = out_dir / "sales-candidate-shard.jsonl"
    coverage_path = out_dir / "coverage-receipts.json"
    summary_json_path = out_dir / "classification-summary.json"
    atomic_jsonl(shard_path, result["shards"])
    atomic_json(coverage_path, result["coverageReceipts"])
    atomic_json(summary_json_path, result["summary"])
    print(
        json.dumps(
            {
                "cohortCardCount": result["summary"]["cohortCardCount"],
                "terminalCounts": result["summary"]["terminalCounts"],
                "liquidityCounts": result["summary"]["liquidityCounts"],
                "shard": str(shard_path),
                "coverageReceipts": str(coverage_path),
                "summary": str(summary_json_path),
                "allTerminal": result["summary"]["allTerminal"],
            },
            sort_keys=True,
        )
    )
    return 0


def _default_snk_identity_paths() -> list[Path]:
    root = ROOT / "data/runtime/private-source-map"
    return [
        root / "qualified-940-identity.jsonl",
        root / "snk-bind-report.json",
    ]


def _default_snk_harvest_paths() -> list[Path]:
    root = ROOT / "data/runtime/private-source-map"
    paths: list[Path] = []
    for pattern in ("snk-psa10*.jsonl", "snk-fill*.jsonl", "snk-flood*.jsonl"):
        for path in sorted(root.glob(pattern)):
            if ".partial" in path.name:
                continue
            paths.append(path)
    return paths


def _cmd_fill_classify(args: argparse.Namespace) -> int:
    report = json.loads(args.qc_report.read_text(encoding="utf-8"))
    as_of = parse_as_of(args.as_of or report.get("asOf"))
    identity_paths = list(args.identity) if args.identity else _default_snk_identity_paths()
    harvest_paths = list(args.harvest) if args.harvest else _default_snk_harvest_paths()
    bindings = load_snk_item_bindings(identity_paths)
    harvests = load_snk_harvest_index(harvest_paths)
    result = fill_classify_qc_cohort(
        report,
        snk_bindings=bindings,
        snk_harvests=harvests,
        as_of=as_of,
        recheck_days=args.recheck_days,
        expected_count=args.expected_count,
    )
    out_dir = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    shard_path = out_dir / "sales-candidate-shard.jsonl"
    shard_json_path = out_dir / "sales-candidate-shard.json"
    coverage_path = out_dir / "coverage-receipts.json"
    summary_json_path = out_dir / "classification-summary.json"
    worklist_path = out_dir / "fill-worklist.json"
    atomic_jsonl(shard_path, result["shards"])
    atomic_json(
        shard_json_path,
        {
            "schemaVersion": SCHEMA_VERSION,
            "kind": "sales-candidate-shard",
            "count": len(result["shards"]),
            "cards": result["shards"],
        },
    )
    atomic_json(coverage_path, result["coverageReceipts"])
    atomic_json(summary_json_path, result["summary"])
    atomic_json(worklist_path, result["worklist"])
    print(
        json.dumps(
            {
                "cohortCardCount": result["summary"]["cohortCardCount"],
                "terminalCountsBefore": result["summary"]["terminalCountsBefore"],
                "terminalCounts": result["summary"]["terminalCounts"],
                "readyPlusVerifiedNone": result["summary"]["readyPlusVerifiedNone"],
                "fillPromotions": result["summary"]["fillPromotions"],
                "snkBindings": len(bindings),
                "snkHarvests": len(harvests),
                "identityPaths": [str(path) for path in identity_paths],
                "harvestPaths": len(harvest_paths),
                "shard": str(shard_path),
                "coverageReceipts": str(coverage_path),
                "summary": str(summary_json_path),
                "worklist": str(worklist_path),
                "allTerminal": result["summary"]["allTerminal"],
            },
            sort_keys=True,
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Normalize and terminal-classify exact eBay/QC PSA 10 sales evidence"
    )
    sub = parser.add_subparsers(dest="cmd")

    normalize = sub.add_parser("normalize", help="Normalize repository-owned completed-sale input")
    normalize.add_argument("--active-universe", type=Path, default=DEFAULT_ACTIVE)
    normalize.add_argument(
        "--input",
        type=Path,
        default=(Path(os.environ["CARDZ_EBAY_SOLD_INPUT"]) if os.environ.get("CARDZ_EBAY_SOLD_INPUT") else None),
    )
    normalize.add_argument("--out", type=Path, required=True)
    normalize.add_argument("--limit", type=int)
    normalize.add_argument("--as-of", type=str, default=None)
    normalize.set_defaults(func=_cmd_normalize)

    classify = sub.add_parser("classify-cohort", help="Terminal-classify C1 QC sales/liquidity cells")
    classify.add_argument("--qc-report", type=Path, default=DEFAULT_QC_REPORT)
    classify.add_argument("--out-dir", type=Path, required=True)
    classify.add_argument("--as-of", type=str, default=None)
    classify.add_argument("--recheck-days", type=int, default=DEFAULT_RECHECK_DAYS)
    classify.add_argument("--expected-count", type=int, default=932)
    classify.set_defaults(func=_cmd_classify_cohort)

    fill = sub.add_parser(
        "fill-classify",
        help="QC baseline + SNK harvest fill for ready / coverage-bound verified_none",
    )
    fill.add_argument("--qc-report", type=Path, default=DEFAULT_QC_REPORT)
    fill.add_argument("--out-dir", type=Path, required=True)
    fill.add_argument("--as-of", type=str, default=None)
    fill.add_argument("--recheck-days", type=int, default=DEFAULT_RECHECK_DAYS)
    fill.add_argument("--expected-count", type=int, default=932)
    fill.add_argument(
        "--identity",
        type=Path,
        action="append",
        default=None,
        help="Identity JSONL/bind-report path (repeatable); defaults to qualified-940 + snk-bind-report",
    )
    fill.add_argument(
        "--harvest",
        type=Path,
        action="append",
        default=None,
        help="SNK harvest JSONL path (repeatable); defaults to private-source-map snk-psa10/fill/flood",
    )
    fill.set_defaults(func=_cmd_fill_classify)

    # Backward-compatible flat argv: treat as normalize when no subcommand token.
    commands = {"normalize", "classify-cohort", "fill-classify", "-h", "--help"}
    raw = list(sys.argv[1:] if argv is None else argv)
    if raw and raw[0] not in commands and not raw[0].startswith("-"):
        # unknown first token — fall through to argparse error
        pass
    if raw and raw[0] not in commands:
        raw = ["normalize", *raw]

    args = parser.parse_args(raw)
    if not getattr(args, "cmd", None):
        parser.print_help()
        return 2
    return int(args.func(args))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None
