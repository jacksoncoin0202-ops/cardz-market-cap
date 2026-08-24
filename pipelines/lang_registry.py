"""Canonical card-language registry shared by pipeline projections."""
from __future__ import annotations


SUPPORTED_CARD_LANGUAGES = frozenset({"en", "ja", "ko", "zhCN", "zhTW"})


def supported_card_language(value: object) -> str | None:
    language = str(value or "")
    return language if language in SUPPORTED_CARD_LANGUAGES else None
