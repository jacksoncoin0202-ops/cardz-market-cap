#!/usr/bin/env python3
"""Normalize verified, completed eBay PSA 10 sales for CARDZ private runs.

This module has no Browse API path and no sibling-tool dependency.  A
repository-owned collector may place a JSON/JSONL completed-sales export at
``--input``.  Without that exact-sold transport, the command fails closed so
the daily orchestrator records eBay as unavailable rather than treating asks
as sales.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ACTIVE = ROOT / "data/runtime/private-source-map/tracked-universe.json"
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

    title = str(listing.get("title") or "").strip()
    sold_date = normalized_sold_date(str(listing.get("sold_date") or listing.get("soldDate") or ""))
    amount = listing.get("price_amount", listing.get("transactionValue"))
    currency = str(listing.get("currency") or "").upper()
    url = _canonical_item_url(listing.get("url"))
    quantity = listing.get("quantity", 1)
    status = str(listing.get("listing_status") or listing.get("status") or "completed_sold").casefold()
    if not isinstance(quantity, int) or quantity < 1:
        return None
    if (
        listing.get("sold") is not True
        or status not in COMPLETED_STATUSES
        or not title
        or not _exact_psa10(title, listing)
        or RAW_CONFLICT_RE.search(title)
        or not exact_number_in_title(str(card.get("collectorNumber") or ""), title)
        or not _tcg_matches(title, listing, card.get("tcg"))
        or not _language_matches(title, listing, card.get("language"))
        or not _title_or_field_matches(title, listing, "edition", card.get("edition"))
        or not _title_or_field_matches(title, listing, "parallel", card.get("parallel"))
        or not _title_or_field_matches(title, listing, "finish", card.get("finish"))
        or url is None
        or sold_date is None
        or not isinstance(amount, (int, float))
        or float(amount) <= 0
        or currency != "USD"
        or (BUNDLE_RE.search(title) and quantity == 1)
    ):
        return None
    transaction_value = round(float(amount), 6)
    transaction_key = str(listing.get("transaction_id") or listing.get("item_id") or url)
    transaction_id = hashlib.sha256(transaction_key.encode("utf-8")).hexdigest()
    return {
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


def collect_from_input(cards: Iterable[Mapping[str, Any]], rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
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
        transactions: dict[str, dict[str, Any]] = {}
        for listing in listings if isinstance(listings, list) else []:
            if isinstance(listing, Mapping):
                normalized = normalize_transaction(card, listing, fetched_at=fetched_at)
                if normalized is not None:
                    transactions[normalized["transactionId"]] = normalized
        ordered = [transactions[key] for key in sorted(transactions)]
        collected.append(
            {
                "schemaVersion": "1.0.0",
                "sourceCode": source_ref[0],
                "externalEntityId": source_ref[1],
                "pokedexId": card.get("pokedexId"),
                "status": "available" if ordered else "unavailable",
                "coverage": "partial" if ordered else "unavailable",
                "transactions": ordered,
                "referenceFallback": reference_fallback(ordered, datetime.now(timezone.utc)),
                "fetchedAt": normalized_timestamp(fetched_at),
            }
        )
    return collected


def atomic_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.next")
    with temporary.open("w", encoding="utf-8", newline="\n") as output:
        for row in rows:
            output.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Normalize repository-owned exact eBay PSA 10 completed-sale evidence")
    parser.add_argument("--active-universe", type=Path, default=DEFAULT_ACTIVE)
    parser.add_argument("--input", type=Path, default=(Path(os.environ["CARDZ_EBAY_SOLD_INPUT"]) if os.environ.get("CARDZ_EBAY_SOLD_INPUT") else None))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    if args.input is None or not args.input.is_file():
        raise RuntimeError("eBay sold adapter unavailable: no repository-owned completed-sale input is configured")
    document = json.loads(args.active_universe.read_text(encoding="utf-8"))
    cards = [card for card in document.get("cards", []) if isinstance(card, Mapping)]
    if args.limit:
        cards = cards[: args.limit]
    output = collect_from_input(cards, _read_json_or_jsonl(args.input))
    output.sort(key=lambda row: (str(row.get("sourceCode")), str(row.get("externalEntityId"))))
    atomic_jsonl(args.out.resolve(), output)
    print(json.dumps({"cards": len(output), "available": sum(row["status"] == "available" for row in output), "transactions": sum(len(row["transactions"]) for row in output), "output": str(args.out.resolve())}, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None
