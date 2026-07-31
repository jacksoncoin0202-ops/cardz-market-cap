#!/usr/bin/env python3
"""Canonical card identity: language + opaque_id + printing key.

Single source of truth for backend identity math. UI does not depend on this.

## Language codes (closed set)

  en | ja | ko | zhCN | zhTW

## opaque_id

  cmc_ + sha256(tcg | language | set_name | collector.normalized | name)[:24]
  (parts joined with \\x1f, each casefolded)

Language is **required** for a production identity. Empty language is forbidden
in new hashes (post-018 architecture).

## printing key

  sha256 of 7 parts joined with "|":
  tcg | language | set_name | collector | edition | parallel | finish
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Mapping

CANONICAL_LANGUAGES = frozenset({"en", "ja", "ko", "zhCN", "zhTW"})

_LANGUAGE_ALIASES = {
    "en": "en",
    "eng": "en",
    "english": "en",
    "ja": "ja",
    "jp": "ja",
    "jpn": "ja",
    "japanese": "ja",
    "ko": "ko",
    "kr": "ko",
    "korean": "ko",
    "zhcn": "zhCN",
    "zh-cn": "zhCN",
    "zh-hans": "zhCN",
    "simplified-chinese": "zhCN",
    "zhtw": "zhTW",
    "zh-tw": "zhTW",
    "zh-hant": "zhTW",
    "traditional-chinese": "zhTW",
}


def normalize_language(value: Any) -> str | None:
    """Return canonical language or None. Never invent."""
    if value is None:
        return None
    key = str(value).strip().casefold().replace("_", "-")
    if not key:
        return None
    if str(value).strip() in CANONICAL_LANGUAGES:
        return str(value).strip()
    return _LANGUAGE_ALIASES.get(key)


def require_language(value: Any) -> str:
    lang = normalize_language(value)
    if lang is None:
        raise ValueError(f"unsupported or missing card_language: {value!r}")
    return lang


def normalize_collector_token(value: Any) -> str:
    """Minimal collector normalize for opaque_id (casefold, strip spaces)."""
    return re.sub(r"\s+", "", str(value or "")).casefold()


def opaque_id(
    tcg: str,
    language: str,
    set_name: str,
    collector_normalized: str,
    name: str,
) -> str:
    """Stable public id. Language required (empty string rejected).

    Join matches historical ``g10_public_snapshot.opaque_id``:
    each part strip + casefold, then ``\\x1f``-join, sha256[:24].
    Canonical language codes casefold as: en/ja/ko/zhcn/zhtw inside the hash.
    """
    lang = require_language(language)
    parts = [
        str(tcg or "").strip().casefold(),
        lang.casefold(),
        str(set_name or "").strip().casefold(),
        str(collector_normalized or "").strip().casefold(),
        str(name or "").strip().casefold(),
    ]
    if not parts[0] or not parts[2] or not parts[4]:
        raise ValueError("opaque_id requires tcg, set_name, name")
    joined = "\x1f".join(parts)
    return f"cmc_{hashlib.sha256(joined.encode('utf-8')).hexdigest()[:24]}"


def opaque_id_from_row(row: Mapping[str, Any], *, collector_normalized: str | None = None) -> str:
    coll = collector_normalized
    if coll is None:
        # Prefer g10 normalize_collector when available (set-aware denominators)
        try:
            from g10_public_snapshot import normalize_collector

            coll_obj = normalize_collector(
                row.get("collector_number"), None, row.get("set_name")
            )
            coll = coll_obj.normalized
        except Exception:
            coll = normalize_collector_token(row.get("collector_number"))
    return opaque_id(
        str(row.get("tcg_code") or ""),
        str(row.get("card_language") or ""),
        str(row.get("set_name") or ""),
        coll,
        str(row.get("canonical_name") or row.get("name") or ""),
    )


def opaque_id_duplicate_salt(base_opaque_id: str, variant_id: int) -> str:
    """When two rows share identity fields, only one may own the clean opaque_id."""
    digest = hashlib.sha256(f"{base_opaque_id}|variant:{int(variant_id)}".encode()).hexdigest()[:24]
    return f"cmc_{digest}"


PrintingKey7 = tuple[str, str, str, str, str, str, str]


def printing_key7(
    tcg: Any,
    language: Any,
    set_name: Any,
    collector_number: Any,
    edition_code: Any = "",
    parallel_code: Any = "",
    finish_code: Any = "",
) -> PrintingKey7:
    lang = normalize_language(language) or ""
    return (
        str(tcg or "").strip().casefold(),
        lang,  # canonical casing if known else ""
        str(set_name or "").strip().casefold(),
        str(collector_number or "").strip().casefold(),
        str(edition_code or "").strip().casefold(),
        str(parallel_code or "").strip().casefold(),
        str(finish_code or "").strip().casefold(),
    )


def printing_key7_sha256(key: PrintingKey7) -> str:
    # language segment uses canonical form as stored (en/ja/…), others casefold
    return hashlib.sha256("|".join(key).encode("utf-8")).hexdigest()


def printing_key7_from_row(row: Mapping[str, Any]) -> PrintingKey7:
    return printing_key7(
        row.get("tcg_code") or row.get("printing_tcg_code"),
        row.get("card_language") or row.get("printing_card_language"),
        row.get("set_name") or row.get("printing_set_name"),
        row.get("collector_number") or row.get("printing_collector_number"),
        row.get("edition_code"),
        row.get("parallel_code"),
        row.get("finish_code"),
    )


def printing_identity_sha256_from_row(row: Mapping[str, Any]) -> str:
    return printing_key7_sha256(printing_key7_from_row(row))
