"""Shared market-window durations for Python ingestion and replay lanes."""
from __future__ import annotations


WINDOW_DAYS = {"1d": 1, "7d": 7, "30d": 30}
WINDOW_CODES = tuple(WINDOW_DAYS)
