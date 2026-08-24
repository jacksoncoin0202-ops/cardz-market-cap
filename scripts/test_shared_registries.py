#!/usr/bin/env python3
"""Shared Python language/window registries and one due-time predicate."""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import active_universe  # noqa: E402
import daily_discovery_activation as discovery  # noqa: E402
import db_runtime  # noqa: E402
import g10_ingest  # noqa: E402
import lang_registry  # noqa: E402
import market_source_sync  # noqa: E402
import tag_daily_capture  # noqa: E402
import window_registry  # noqa: E402


assert lang_registry.SUPPORTED_CARD_LANGUAGES == {
    "en", "ja", "ko", "zhCN", "zhTW",
}
assert active_universe.SUPPORTED_CARD_LANGUAGES is lang_registry.SUPPORTED_CARD_LANGUAGES
assert db_runtime.SUPPORTED_CARD_LANGUAGES is lang_registry.SUPPORTED_CARD_LANGUAGES
assert market_source_sync.SUPPORTED_CARD_LANGUAGES is lang_registry.SUPPORTED_CARD_LANGUAGES
assert tag_daily_capture.SUPPORTED_LANGUAGES is lang_registry.SUPPORTED_CARD_LANGUAGES
assert lang_registry.supported_card_language("zhTW") == "zhTW"
assert lang_registry.supported_card_language("th") is None

assert window_registry.WINDOW_DAYS == {"1d": 1, "7d": 7, "30d": 30}
assert g10_ingest.WINDOW_DAYS is window_registry.WINDOW_DAYS
assert db_runtime.WINDOW_DAYS is window_registry.WINDOW_DAYS

now = datetime(2026, 8, 25, 3, 0, 0)
future = {
    "variant_id": 1,
    "discovery_status": "source_not_found",
    "language": "ja",
    "next_due_at": "2026-08-25T03:00:01Z",
}
due = dict(future, variant_id=2, next_due_at="2026-08-25T03:00:00Z")
plan = discovery.plan_ledger_retry_targets([], [future, due], "http", now=now)
assert plan["targetIds"] == [2], plan
source = (ROOT / "pipelines" / "daily_discovery_activation.py").read_text(encoding="utf-8")
planner = source[source.index("def plan_ledger_retry_targets"):source.index("def _load_ledger_rows")]
assert "_row_due(raw, clock)" in planner
assert "str(quarantine_until) > str(clock)" not in planner
print("POSITIVE_OK Python language/window registries and due-time predicate have one authority")
