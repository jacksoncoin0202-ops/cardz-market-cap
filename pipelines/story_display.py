"""Visible story strip — same rules as apps/web/src/lib/story-display.ts.

Bake / live-db / gate must call this, not a second copy. Never strip `_`.
"""

from __future__ import annotations

import re

_ATX = re.compile(r"^\s{0,3}#{1,6}\s+", re.M)
_BULLET = re.compile(r"^\s{0,3}([-*+]|\d{1,9}[.)])\s+", re.M)


def strip_visible_markdown(text: str) -> str:
    cleaned = _ATX.sub("", text)
    cleaned = _BULLET.sub("", cleaned)
    return cleaned.replace("**", "").replace("*", "")


def format_story_for_display(text: str | None) -> str | None:
    if not isinstance(text, str):
        return None
    cleaned = re.sub(r"[ \t]+\n", "\n", strip_visible_markdown(text)).strip()
    return cleaned or None
