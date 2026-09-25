"""CARDZ CDP identity — Python side of scripts/ensure_chrome_cdp.ps1.

A 200 on /json/version is not Windows Chrome. Reasons must stay in
sync with Get-CdpRejectReason in ensure_chrome_cdp.ps1.
Headed Windows only (CF tool needs a real window). Headless / WSL / Linux
are rejected.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any

CODEX_BROWSER_PORT = 9222
DEFAULT_PORT = 9333
_LOOPBACK_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def reject_reason(version: dict[str, Any] | None) -> str | None:
    if version is None:
        return "no-listener"
    blob = f"{version.get('Browser') or ''} {version.get('User-Agent') or ''}"
    if re.search(r"Linux|X11|WSL", blob, re.I):
        return "linux-or-wsl"
    if "Windows NT" not in blob:
        return "not-windows"
    if re.search(r"Headless", blob, re.I):
        return "headless"
    return None


def fetch_version(
    port: int = DEFAULT_PORT,
    timeout: float = 2.0,
    *,
    host: str = "127.0.0.1",
) -> dict[str, Any] | None:
    if int(port) == CODEX_BROWSER_PORT:
        raise RuntimeError(
            "CARDZ must never attach to CDP 9222 (Codex browser profile). Use 9333."
        )
    try:
        with _LOOPBACK_OPENER.open(
            f"http://{host}:{int(port)}/json/version", timeout=timeout
        ) as response:
            payload = json.load(response)
        if isinstance(payload, dict):
            return payload
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None
    return None


def fetch_targets(
    port: int = DEFAULT_PORT,
    timeout: float = 5.0,
    *,
    host: str = "127.0.0.1",
) -> list[Any] | None:
    """Return /json/list or None when the DevTools HTTP target list is jammed."""

    if int(port) == CODEX_BROWSER_PORT:
        raise RuntimeError(
            "CARDZ must never attach to CDP 9222 (Codex browser profile). Use 9333."
        )
    try:
        with _LOOPBACK_OPENER.open(
            f"http://{host}:{int(port)}/json/list", timeout=timeout
        ) as response:
            payload = json.load(response)
        if isinstance(payload, list):
            return payload
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None
    return None


def require_headed_windows(
    port: int = DEFAULT_PORT, *, host: str = "127.0.0.1"
) -> dict[str, Any]:
    version = fetch_version(port, host=host)
    reason = reject_reason(version)
    if reason:
        raise RuntimeError(
            f"CDP identity rejected port={port} reason={reason}; "
            "run scripts/ensure_chrome_cdp.ps1 -Port 9333"
        )
    return version


def require_session_ready(
    port: int = DEFAULT_PORT, *, host: str = "127.0.0.1"
) -> dict[str, Any]:
    """Identity plus a live target list. Version-only 200 is not a usable session."""

    version = require_headed_windows(port, host=host)
    if fetch_targets(port, host=host) is None:
        raise RuntimeError(
            f"CDP identity rejected port={port} reason=jammed-targets; "
            "run scripts/ensure_chrome_cdp.ps1 -Port 9333"
        )
    return version
