# -*- coding: utf-8 -*-
"""Detect exclusive / 専売 products for ranking demotion (not QC hard-fail).

POLICY_EXCLUSIVE_RANK_IGNORE.md — DADDY 2026-07-30.
"""
from __future__ import annotations

import re
from typing import Any, Mapping

_EXCLUSIVE_RE = re.compile(
    r"exclusive|獨家|専売|限定店|exclusive\s+collaboration|exclusive\s+promo",
    re.IGNORECASE,
)


def is_exclusive_product(
    name: str | None = None,
    set_name: str | None = None,
    **_: Any,
) -> bool:
    blob = f"{name or ''} {set_name or ''}"
    return bool(_EXCLUSIVE_RE.search(blob))


def is_exclusive_row(row: Mapping[str, Any]) -> bool:
    return is_exclusive_product(
        name=str(row.get("canonical_name") or row.get("name") or row.get("cardName") or ""),
        set_name=str(row.get("set_name") or row.get("setName") or ""),
    )
