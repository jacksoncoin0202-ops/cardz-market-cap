#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CardZ Marketcap daily promo pack — data + gates. Does not post.

This is NOT the auto-update chain. Do not call it from live.confirmed.
Default: write a brief, print heatmap URLs, assert the pack. Zero send.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import unicodedata
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path, PureWindowsPath
from typing import Any, Mapping, Sequence
from urllib.parse import urlencode, urlparse

ROOT = Path(__file__).resolve().parents[1]
LIVE = "https://app.cardzmarketcap.com"
PUBLIC_LINK = "https://cardzmarketcap.com"
HEATMAP_TILES = 40  # 圖用 Top 40；數字永遠報 TCG Top 100 排名
HEATMAP_ATTEMPTS = 3  # 斷線重試；同一 generation pack 有齊檔就 skip，唔由零 GET
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)
DAILY_PERIOD = "7d"
FORBIDDEN_PERIODS = frozenset({"90d", "180d", "365d"})
TOP_N = 3
SCOPES = ("pokemon", "one-piece", "all")
SCOPE_PATH = {"pokemon": "/pokemon", "one-piece": "/one-piece", "all": "/"}

# Platform hard caps. Fail-closed: X Premium 25k exists, CARDZ 帳未核對就當 280。
# Sources: docs.x.com/fundamentals/counting-characters (280 weighted; CJK/emoji=2; t.co URL=23);
# Instagram caption 2200; Threads post 500 (2026); WhatsApp Cloud API text 4096.
CHANNEL_LIMIT = {
    "x.com-en": {"max": 280, "count": "x"},
    "x.com-zh": {"max": 280, "count": "x"},
    "instagram-en": {"max": 2200, "count": "chars"},
    "instagram-zh": {"max": 2200, "count": "chars"},
    "threads-en": {"max": 500, "count": "chars"},
    "threads-zh": {"max": 500, "count": "chars"},
    "whatsapp-ptcg": {"max": 4096, "count": "chars"},
    "whatsapp-tcg-4": {"max": 4096, "count": "chars"},
    "whatsapp-op": {"max": 4096, "count": "chars"},
    "fork-zh": {"max": 500, "count": "chars"},
    "site-zh": {"max": 2000, "count": "chars"},
}
URL_RE = re.compile(r"https?://[^\s]+", re.I)

# fork-zh / site / WhatsApp groups = Traditional. X Chinese = Simplified only.
CHANNEL_SCRIPT = {
    "x.com-en": "en",
    "x.com-zh": "zh-Hans",
    "instagram-en": "en",
    "instagram-zh": "zh-Hant",
    "threads-en": "en",
    "threads-zh": "zh-Hant",
    "whatsapp-ptcg": "zh-Hant",
    "whatsapp-tcg-4": "zh-Hant",
    "whatsapp-op": "zh-Hant",
    "fork-zh": "zh-Hant",
    "site-zh": "zh-Hant",
}

HOST_FOR_CHANNEL = {
    "x.com-en": "x.com",
    "x.com-zh": "x.com",
    "instagram-en": "instagram.com",
    "instagram-zh": "instagram.com",
    "threads-en": "threads.net",
    "threads-zh": "threads.net",
    "whatsapp-ptcg": "web.whatsapp.com",
    "whatsapp-tcg-4": "web.whatsapp.com",
    "whatsapp-op": "web.whatsapp.com",
    "site-zh": "app.cardzmarketcap.com",
    "fork-zh": "app.cardzmarketcap.com",
}

LEAK_RE = re.compile(
    rb"C:\\Users|jackson0202|127\.0\.0\.1|localhost|Telegram Desktop",
    re.I,
)
# Characters that exist in Simplified and not in Traditional.
SIMPLIFIED_ONLY = set(
    "这们为会过还时国对与来发从无关门问间长东车钱买卖头实际"
    "总产业广说让给边远进运达选样点张亿据吗条请认组现经个涨额汇"
)

CDP_JSON = "http://127.0.0.1:9222/json"

# Freshness gate. A promo post that quotes a two-day-old board lies about
# "today's" movers, so the pack refuses to build instead of shipping stale data.
# 26h (not 24h) leaves room for a late bake without opening a whole extra day.
PROMO_MAX_LIVE_LAG_HOURS_DEFAULT = 26.0
PROMO_MIN_BAKE_AGE_HOURS_DEFAULT = 0.5
JST = timezone(timedelta(hours=9))
RECEIPT_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]+")


class PromoError(RuntimeError):
    pass


class PromoStaleLive(PromoError):
    """Live payload older than PROMO_MAX_LIVE_LAG_HOURS. CLI exit code 3."""


class PromoBakeCooling(PromoError):
    """Live bake is less than 30 minutes old and is not ready for promotion."""


def promo_runtime_dir() -> Path:
    """data/runtime/promo, or PROMO_RUNTIME_DIR when tests/ops redirect it."""
    override = os.environ.get("PROMO_RUNTIME_DIR", "").strip()
    if override:
        return Path(override)
    return ROOT / "data" / "runtime" / "promo"


def max_live_lag_hours() -> float:
    raw = os.environ.get("PROMO_MAX_LIVE_LAG_HOURS", "").strip()
    if not raw:
        return PROMO_MAX_LIVE_LAG_HOURS_DEFAULT
    try:
        return float(raw)
    except ValueError as error:
        raise PromoError(f"PROMO_MAX_LIVE_LAG_HOURS={raw!r} is not a number") from error


def parse_iso_utc(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def live_lag_hours(generated_at: Any, *, now: datetime | None = None) -> float | None:
    parsed = parse_iso_utc(generated_at)
    if parsed is None:
        return None
    ref = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return (ref - parsed).total_seconds() / 3600.0


def business_date_jst(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).astimezone(JST).date().isoformat()


def assert_live_fresh(
    generated_at: Any,
    *,
    allow_stale: bool = False,
    now: datetime | None = None,
    max_lag_hours: float | None = None,
    min_lag_hours: float = PROMO_MIN_BAKE_AGE_HOURS_DEFAULT,
) -> tuple[float | None, float]:
    """Fail closed outside the 30-minute-to-26-hour promotion window."""
    limit = max_live_lag_hours() if max_lag_hours is None else float(max_lag_hours)
    lag = live_lag_hours(generated_at, now=now)
    if lag is None:
        if allow_stale:
            return None, limit
        raise PromoStaleLive(
            f"live generatedAt {str(generated_at or '')!r} missing/unparseable — "
            "refusing to promo (use --allow-stale)"
        )
    if lag < float(min_lag_hours):
        raise PromoBakeCooling(
            f"live generatedAt {generated_at} is only {lag:.2f}h old < "
            f"{float(min_lag_hours):.2f}h — wait 30 minutes before promo"
        )
    if lag > limit and not allow_stale:
        raise PromoStaleLive(
            f"live generatedAt {generated_at} is {lag:.2f}h old > {limit:.2f}h "
            "(PROMO_MAX_LIVE_LAG_HOURS) — refusing to promo (use --allow-stale)"
        )
    return lag, limit


def write_action_receipt(
    *,
    business_date: str,
    destination: str,
    dry_run: bool,
    fill_only: bool,
    posted: bool,
    text: str,
    live_generated_at: Any,
    lag_hours: float | None,
    outcome: str,
    error: str | None = None,
    runtime_dir: Path | None = None,
    now: datetime | None = None,
) -> Path:
    """One receipt per build and per post attempt — dry-run included."""
    base = Path(runtime_dir) if runtime_dir is not None else promo_runtime_dir()
    dest_dir = base / "receipts"
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime(
        "%Y%m%dT%H%M%S%fZ"
    )
    slug = RECEIPT_SLUG_RE.sub("_", str(destination)) or "unknown"
    path = dest_dir / f"{business_date}_{slug}_{stamp}.json"
    payload = {
        "business_date": business_date,
        "destination": str(destination),
        "dry_run": bool(dry_run),
        "fill_only": bool(fill_only),
        "posted": bool(posted),
        "text_sha256": hashlib.sha256((text or "").encode("utf-8")).hexdigest(),
        "live_generated_at": str(live_generated_at or ""),
        "lag_hours": None if lag_hours is None else round(float(lag_hours), 4),
        "outcome": str(outcome),
        "error": None if error is None else str(error),
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return path


def metric_pct(card: Mapping[str, Any], period: str) -> float | None:
    window = (card.get("windows") or {}).get(period) or {}
    metric = window.get("changePct") or {}
    if metric.get("status") not in ("ready", "stale"):
        return None
    value = metric.get("value")
    if value is None:
        return None
    return float(value)


def collector_number(card: Mapping[str, Any]) -> str:
    return str(card.get("collectorNumber") or card.get("collector_number") or "").strip()


def set_name_en(card: Mapping[str, Any]) -> str:
    value = card.get("setName") or card.get("set_name") or ""
    if isinstance(value, Mapping):
        return str(value.get("en") or "").strip()
    return str(value).strip()


def card_subject(card: Mapping[str, Any]) -> str:
    """Same peel as FE `cardSubject`: drop year + set.en + trailing collector number."""
    raw = str(card.get("officialName") or (card.get("name") or {}).get("en") or "").strip()
    if not raw:
        return ""
    text = re.sub(r"^\d{4}\s+", "", raw)
    set_en = set_name_en(card)
    if set_en and text.lower().startswith(set_en.lower()):
        text = text[len(set_en) :].strip()
    number = collector_number(card)
    if number and text.lower().endswith(number.lower()):
        text = text[: -len(number)].strip()
    return text.strip() or raw


def short_name(card: Mapping[str, Any], locale: str) -> str:
    names = card.get("name") or {}
    official = str(card.get("officialName") or "")
    if locale == "zh-Hans":
        local = str(names.get("zh-CN") or names.get("zh-TW") or names.get("en") or card.get("id") or "")
    elif locale.startswith("zh"):
        local = str(names.get("zh-TW") or names.get("en") or card.get("id") or "")
    else:
        local = str(names.get("en") or official or card.get("id") or "")
    # EN officialName 係 PSA 全串；社交要用主體，唔好整年＋set 名。
    if locale == "en" or local == official or re.match(r"^\d{4}\s", local):
        peeled = card_subject(card)
        if peeled:
            local = peeled
    number = collector_number(card)
    if number and number.lower() not in local.lower():
        local = f"{local} {number}".strip()
    return local


def x_weighted_length(text: str) -> int:
    """X 280 budget. NFC; URL=23; Latin=1; CJK/emoji/other=2. docs.x.com counting-characters."""
    text = unicodedata.normalize("NFC", text)
    total = 0
    pos = 0
    for match in URL_RE.finditer(text):
        total += _x_span_weight(text[pos : match.start()])
        total += 23
        pos = match.end()
    total += _x_span_weight(text[pos:])
    return total


def _x_span_weight(span: str) -> int:
    weight = 0
    for char in span:
        code = ord(char)
        if code in {0x200D, 0xFE0E, 0xFE0F}:
            continue
        weight += 1 if code <= 0x024F else 2
    return weight


def copy_length(text: str, mode: str) -> int:
    if mode == "x":
        return x_weighted_length(text)
    return len(unicodedata.normalize("NFC", text))


def channel_over_limit(channel: str, text: str) -> str | None:
    spec = CHANNEL_LIMIT.get(channel)
    if not spec:
        return None
    used = copy_length(text, str(spec["count"]))
    limit = int(spec["max"])
    if used > limit:
        return f"{channel}: {used} > {limit} ({spec['count']})"
    return None


def movers_from_cards(
    cards: Sequence[Mapping[str, Any]],
    *,
    period: str = DAILY_PERIOD,
    top_n: int = TOP_N,
    locale: str = "en",
) -> dict[str, Any]:
    if period in FORBIDDEN_PERIODS:
        raise PromoError(f"daily promo refuses period={period}; use {DAILY_PERIOD} or 7d")
    ranked: list[dict[str, Any]] = []
    for card in cards:
        rank = card.get("viewRank") or card.get("rank")
        try:
            rank_n = int(rank)
        except (TypeError, ValueError):
            continue
        if rank_n < 1 or rank_n > 100:
            continue
        pct = metric_pct(card, period)
        if pct is None or pct == 0:
            continue
        ranked.append(
            {
                "id": card.get("id"),
                "rank": rank_n,
                "name": short_name(card, locale),
                "nameEn": short_name(card, "en"),
                "nameZhHant": short_name(card, "zh-Hant"),
                "nameZhHans": short_name(card, "zh-Hans"),
                "number": collector_number(card),
                "tcg": card.get("tcg"),
                "changePct": round(pct, 4),
            }
        )
    up = sorted((row for row in ranked if row["changePct"] > 0), key=lambda r: r["changePct"], reverse=True)[:top_n]
    down = sorted((row for row in ranked if row["changePct"] < 0), key=lambda r: r["changePct"])[:top_n]
    return {"period": period, "up": up, "down": down, "scored": len(ranked)}


def heatmap_url(scope: str, period: str = DAILY_PERIOD) -> str:
    path = SCOPE_PATH.get(scope, "/")
    return f"{LIVE}{path}?period={period}"


SCRIPT_TO_OG_LANG = {"en": "en", "zh-Hant": "zh-TW", "zh-Hans": "zh-CN"}


def heatmap_og_url(
    scope: str = "all",
    period: str = DAILY_PERIOD,
    show: int = HEATMAP_TILES,
    *,
    fmt: str = "post",
    theme: str = "dark",
    updown: str = "green-up",
    lang: str = "en",
) -> str:
    """GET /api/og/heatmap — matches FE heatmapOgSearch. No browser, no :3900."""
    if period in FORBIDDEN_PERIODS:
        raise PromoError(f"daily promo refuses period={period}; use {DAILY_PERIOD}")
    query = {
        "period": period,
        "show": str(show),
        "scope": scope,
        "format": fmt,
        "theme": theme,
        "updown": updown,
        "lang": lang,
    }
    return f"{LIVE}/api/og/heatmap?{urlencode(query)}"


def heatmap_og_url_for_channel(channel: str, period: str = DAILY_PERIOD) -> str:
    script = CHANNEL_SCRIPT.get(channel)
    board = CHANNEL_BOARD.get(channel)
    fmt = CHANNEL_FORMAT.get(channel)
    if not script or not board or not fmt:
        raise PromoError(f"unknown channel {channel}")
    return heatmap_og_url(board, period, fmt=fmt, lang=SCRIPT_TO_OG_LANG[script])


def format_pct(value: float) -> str:
    sign = "+" if value > 0 else "−"
    return f"{sign}{abs(value):.2f}%"


def mover_name(row: Mapping[str, Any], script: str) -> str:
    if script == "zh-Hans":
        name = str(row.get("nameZhHans") or row.get("nameEn") or row.get("id"))
    elif script == "zh-Hant":
        name = str(row.get("nameZhHant") or row.get("nameEn") or row.get("id"))
    else:
        name = str(row.get("nameEn") or row.get("id"))
    number = str(row.get("number") or "").strip()
    if number and number.lower() not in name.lower():
        name = f"{name} {number}".strip()
    return name


BOARD_HEAD = {
    "all": {"en": "TCG Top 100", "zh-Hant": "TCG Top 100", "zh-Hans": "TCG Top 100"},
    "pokemon": {"en": "Pokémon Top 100", "zh-Hant": "寶可夢 Top 100", "zh-Hans": "宝可梦 Top 100"},
    "one-piece": {"en": "One Piece Top 100", "zh-Hant": "海賊王 Top 100", "zh-Hans": "海贼王 Top 100"},
}


def _render_n(movers: Mapping[str, Any], *, board: str, script: str, top_n: int) -> str:
    """No commentary. Rank + localized name + number + pct + lantern. Link last."""
    period = str(movers.get("period") or DAILY_PERIOD).upper()
    head = f"{BOARD_HEAD[board][script]} · {period}"
    lines = [head, ""]
    up = list(movers.get("up") or [])[:top_n]
    down = list(movers.get("down") or [])[:top_n]
    for row in up:
        lines.append(f"🟢 #{row['rank']} {mover_name(row, script)}  {format_pct(float(row['changePct']))}")
    if up and down:
        lines.append("")
    for row in down:
        lines.append(f"🔴 #{row['rank']} {mover_name(row, script)}  {format_pct(float(row['changePct']))}")
    lines.append("")
    lines.append(PUBLIC_LINK)
    return "\n".join(lines) + "\n"


def render_copy(
    movers: Mapping[str, Any],
    *,
    board: str,
    script: str,
    channel: str | None = None,
) -> str:
    """Max 3 up / 3 down. Shrink 3→2→1 if the channel cap would reject the post."""
    for top_n in (TOP_N, 2, 1):
        text = _render_n(movers, board=board, script=script, top_n=top_n)
        if channel is None or channel_over_limit(channel, text) is None:
            return text
    raise PromoError(channel_over_limit(channel, text) or f"{channel}: copy over limit")


CHANNEL_BOARD = {
    "x.com-en": "all",
    "x.com-zh": "all",
    "instagram-en": "all",
    "instagram-zh": "all",
    "threads-en": "all",
    "threads-zh": "all",
    "whatsapp-ptcg": "pokemon",
    "whatsapp-tcg-4": "all",
    "whatsapp-op": "one-piece",
    "fork-zh": "all",
    "site-zh": "all",
}
CHANNEL_FORMAT = {
    channel: ("square" if channel.startswith("instagram-") else "post")
    for channel in CHANNEL_SCRIPT
}
if not (
    CHANNEL_LIMIT.keys()
    == CHANNEL_SCRIPT.keys()
    == HOST_FOR_CHANNEL.keys()
    == CHANNEL_BOARD.keys()
    == CHANNEL_FORMAT.keys()
):
    raise RuntimeError("CHANNEL_* tables out of sync")


def generation_id(value: Any) -> str:
    if isinstance(value, Mapping):
        return str(value.get("id") or "")
    return str(value or "")


def brief_from_payload(
    health: Mapping[str, Any],
    scoped: Mapping[str, Mapping[str, Any]],
    *,
    period: str = DAILY_PERIOD,
    allow_stale: bool = False,
    now: datetime | None = None,
    max_lag_hours: float | None = None,
) -> dict[str, Any]:
    generation = generation_id(health.get("generation"))
    generated_at = str(health.get("generatedAt") or "")
    if not generation:
        raise PromoError("health payload missing generation")
    lag_hours, lag_limit = assert_live_fresh(
        generated_at,
        allow_stale=allow_stale,
        now=now,
        max_lag_hours=max_lag_hours,
    )
    boards: dict[str, Any] = {}
    for scope, payload in scoped.items():
        payload_gen = generation_id(payload.get("generation"))
        if payload_gen and payload_gen != generation:
            raise PromoError(f"{scope} generation {payload_gen} != health {generation}")
        boards[scope] = {
            "count": len(payload.get("cards") or []),
            "universe": 100,
            "heatmapTiles": HEATMAP_TILES,
            "movers": movers_from_cards(payload.get("cards") or [], period=period),
            "heatmapUrl": heatmap_og_url(scope, period),
            "pageUrl": heatmap_url(scope, period),
        }
    copies = {
        channel: render_copy(
            boards[CHANNEL_BOARD[channel]]["movers"],
            board=CHANNEL_BOARD[channel],
            script=script,
            channel=channel,
        )
        for channel, script in CHANNEL_SCRIPT.items()
        if CHANNEL_BOARD[channel] in boards
    }
    channel_heatmaps = {
        channel: heatmap_og_url_for_channel(channel, period)
        for channel, board in CHANNEL_BOARD.items()
        if board in boards
    }
    return {
        "product": "CardZ Marketcap",
        "generation": generation,
        "generatedAt": generated_at,
        "lagHours": None if lag_hours is None else round(lag_hours, 4),
        "minBakeAgeHours": PROMO_MIN_BAKE_AGE_HOURS_DEFAULT,
        "maxLagHours": lag_limit,
        "allowStale": bool(allow_stale),
        "period": period,
        "liveUrl": LIVE,
        "publicLink": PUBLIC_LINK,
        "heatmapTiles": HEATMAP_TILES,
        "boards": boards,
        "copies": copies,
        "channelHeatmaps": channel_heatmaps,
        "post": False,
    }


def fetch_bytes(url: str, *, attempts: int = HEATMAP_ATTEMPTS) -> tuple[bytes, Mapping[str, str]]:
    last: PromoError | None = None
    for _ in range(max(1, attempts)):
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "image/jpeg,image/png,*/*"})
        try:
            with urllib.request.urlopen(req, timeout=120) as response:
                headers = {k.lower(): v for k, v in response.headers.items()}
                return response.read(), headers
        except urllib.error.HTTPError as error:
            last = PromoError(f"heatmap HTTP {error.code} {url}")
        except urllib.error.URLError as error:
            last = PromoError(f"heatmap fetch failed {url}: {error}")
    raise last or PromoError(f"heatmap fetch failed {url}")


def reusable_heatmap(path: Path) -> bool:
    """Same-generation resume: real JPEG/PNG already on disk, no leak."""
    if not path.is_file() or path.stat().st_size < 8000:
        return False
    raw = path.read_bytes()
    if raw[:3] != b"\xff\xd8\xff" and raw[:8] != b"\x89PNG\r\n\x1a\n":
        return False
    return not scan_leak(raw)


def write_receipt(pack: Path, payload: Mapping[str, Any]) -> Path:
    dest = pack / "receipt.json"
    dest.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return dest


def read_receipt(pack: Path) -> dict[str, Any]:
    path = pack / "receipt.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def download_heatmap(
    brief: Mapping[str, Any],
    pack: Path,
    scope: str = "all",
    *,
    fmt: str = "post",
    lang: str = "en",
    theme: str = "dark",
    updown: str = "green-up",
) -> Path:
    generation = str(brief.get("generation") or "")
    period = str(brief.get("period") or DAILY_PERIOD)
    dest_jpg = pack / f"heatmap-{scope}-{period}-{fmt}-{lang}.jpg"
    dest_png = pack / f"heatmap-{scope}-{period}-{fmt}-{lang}.png"
    if reusable_heatmap(dest_jpg):
        return dest_jpg
    if reusable_heatmap(dest_png):
        return dest_png
    url = heatmap_og_url(scope, period, fmt=fmt, theme=theme, updown=updown, lang=lang)
    body, headers = fetch_bytes(url)
    remote_gen = headers.get("x-og-generation") or ""
    if remote_gen and generation and remote_gen != generation:
        raise PromoError(f"heatmap generation {remote_gen} != live {generation}")
    if len(body) < 8000:
        raise PromoError(f"heatmap too small ({len(body)} bytes) — not a real image")
    ctype = headers.get("content-type") or ""
    ext = ".jpg" if "jpeg" in ctype else ".png" if "png" in ctype else ".jpg"
    if body[:3] == b"\xff\xd8\xff":
        ext = ".jpg"
    elif body[:8] == b"\x89PNG\r\n\x1a\n":
        ext = ".png"
    dest = pack / f"heatmap-{scope}-{period}-{fmt}-{lang}{ext}"
    dest.write_bytes(body)
    leaks = scan_leak(body)
    if leaks:
        dest.unlink(missing_ok=True)
        raise PromoError(f"heatmap leak {leaks}")
    return dest


def heatmap_receipt_ref(path: Path) -> str:
    """Filename only. Absolute Windows paths put the operator username in receipt.json."""
    raw = str(path)
    return PureWindowsPath(raw).name if "\\" in raw else Path(raw).name


def fetch_json(url: str) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as error:
        raise PromoError(f"live fetch failed {url}: {error}") from error


def build_live_brief(
    period: str = DAILY_PERIOD,
    *,
    allow_stale: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    health = fetch_json(f"{LIVE}/api/health")
    scoped: dict[str, Any] = {}
    for scope in SCOPES:
        scoped[scope] = fetch_json(f"{LIVE}/api/v1/market?scope={scope}&pageSize=100")
    return brief_from_payload(
        health, scoped, period=period, allow_stale=allow_stale, now=now
    )


def scan_leak(raw: bytes) -> list[str]:
    hits = sorted({match.group(0).decode("latin-1") for match in LEAK_RE.finditer(raw)})
    return hits


def scan_simplified(text: str) -> list[str]:
    return sorted({ch for ch in text if ch in SIMPLIFIED_ONLY})


def assert_text(channel: str, text: str) -> None:
    script = CHANNEL_SCRIPT.get(channel)
    if script is None:
        raise PromoError(f"unknown channel {channel}")
    leaks = scan_leak(text.encode("utf-8"))
    if leaks:
        raise PromoError(f"{channel}: leak {leaks}")
    if "localhost" in text.lower() or "127.0.0.1" in text:
        raise PromoError(f"{channel}: localhost URL is not publishable")
    if script == "zh-Hant":
        hits = scan_simplified(text)
        if hits:
            raise PromoError(f"{channel}: Simplified chars {hits} — zh-Hant only (x.com-zh is the Hans lane)")
    over = channel_over_limit(channel, text)
    if over:
        raise PromoError(over)


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def jst_date_of(iso_text: str) -> str:
    raw = str(iso_text or "").strip()
    if not raw:
        raise PromoError("missing timestamp")
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(JST).date().isoformat()


def assert_box_matches_business_date(health: Mapping[str, Any], business_date: str) -> None:
    """Refuse promo when live /box is a previous JST day (old bake, old picture)."""
    box = health.get("box") if isinstance(health.get("box"), Mapping) else {}
    as_of = str(box.get("asOf") or "")
    if not as_of:
        raise PromoError("live health has no box.asOf — not a complete bake")
    box_day = jst_date_of(as_of)
    if box_day != str(business_date):
        raise PromoError(
            f"box asOf {as_of} is JST {box_day}, not business date {business_date} — old bake, do not promo"
        )


def assert_heatmap_not_repeat(
    pack_dir: Path, *, filename: str = "heatmap-all-7d-post-en.jpg"
) -> None:
    """Refuse a pack whose TCG heatmap is byte-identical to an older generation pack."""
    current = pack_dir / filename
    if not current.is_file():
        return
    cur_sha = _sha256_file(current)
    parent = pack_dir.parent
    if not parent.is_dir():
        return
    for other in parent.iterdir():
        if not other.is_dir() or other == pack_dir:
            continue
        jpg = other / filename
        if not jpg.is_file():
            continue
        other_sha = _sha256_file(jpg)
        if other_sha == cur_sha:
            raise PromoError(
                f"heatmap {filename} sha256 matches previous pack {other.name} — old picture, do not promo"
            )


def assert_pack(pack_dir: Path, *, expect_generation: str | None = None) -> dict[str, Any]:
    if not pack_dir.is_dir():
        raise PromoError(f"pack dir missing: {pack_dir}")
    brief_path = pack_dir / "brief.json"
    if not brief_path.is_file():
        raise PromoError("pack missing brief.json")
    brief = json.loads(brief_path.read_text(encoding="utf-8"))
    if brief.get("period") in FORBIDDEN_PERIODS:
        raise PromoError(f"brief period {brief.get('period')} is not a daily window")
    if expect_generation and brief.get("generation") != expect_generation:
        raise PromoError(
            f"generation {brief.get('generation')} != live {expect_generation}"
        )
    problems: list[str] = []
    for path in sorted(pack_dir.rglob("*")):
        if not path.is_file():
            continue
        raw = path.read_bytes()
        leaks = scan_leak(raw)
        if leaks:
            problems.append(f"{path.name}: leak {leaks}")
        if path.suffix.lower() in {".md", ".txt", ".json"}:
            channel = path.stem  # e.g. x.com-zh.txt
            if channel in CHANNEL_SCRIPT:
                try:
                    assert_text(channel, raw.decode("utf-8", "replace"))
                except PromoError as error:
                    problems.append(str(error))
    if problems:
        raise PromoError(" ; ".join(problems))
    assert_heatmap_not_repeat(pack_dir)
    return {"ok": True, "generation": brief.get("generation"), "files": sum(1 for _ in pack_dir.rglob("*") if _.is_file())}


def _cdp_host_aliases(host: str) -> tuple[str, ...]:
    """Threads moved www.threads.net → www.threads.com; both name the same tab."""

    folded = str(host or "").casefold()
    if folded in {"threads.net", "threads.com"}:
        return ("threads.net", "threads.com")
    return (folded,) if folded else ()


def hostname_matches_cdp_host(hostname: str, host: str) -> bool:
    """Exact/suffix match. Substring would steal accountscenter.threads.com."""

    folded = str(hostname or "").casefold()
    if not folded or folded.startswith("accountscenter."):
        return False
    for alias in _cdp_host_aliases(host):
        if folded == alias or folded.endswith("." + alias):
            return True
    return False


def _threads_tab_rank(url: str) -> int:
    parsed = urlparse(str(url or ""))
    path = parsed.path or "/"
    if path in {"/", ""}:
        return 0
    return 1


def pick_cdp_tab(pages: Sequence[Mapping[str, Any]], host: str) -> dict[str, Any]:
    """Reuse the existing 9222 tab for this host. Never open a retry tab.

    pages: Chrome /json list (type, url, id, title).
    """
    matches: list[Mapping[str, Any]] = []
    for page in pages:
        if str(page.get("type") or "page") not in {"page", "tab"}:
            continue
        parsed = urlparse(str(page.get("url") or ""))
        hostname = str(parsed.hostname or "").casefold()
        if hostname_matches_cdp_host(hostname, host):
            matches.append(page)
    if matches:
        matches.sort(key=lambda page: _threads_tab_rank(str(page.get("url") or "")))
        chosen = matches[0]
        extras = [str(page.get("id")) for page in matches[1:]]
        return {
            "action": "reuse",
            "id": chosen.get("id"),
            "url": chosen.get("url"),
            "extraSameHost": extras,
            "openNew": False,
        }
    return {"action": "open_once", "host": host, "openNew": True, "extraSameHost": []}


def list_cdp_pages(endpoint: str = CDP_JSON) -> list[dict[str, Any]]:
    req = urllib.request.Request(endpoint, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=3) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, list):
        raise PromoError("9222 /json did not return a list")
    return payload


def cmd_brief(args: argparse.Namespace) -> int:
    brief = build_live_brief(period=args.period, allow_stale=bool(getattr(args, "allow_stale", False)))
    out = Path(args.out) if args.out else promo_runtime_dir() / brief["generation"] / "brief.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(brief, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    pack = out.parent
    business_date = business_date_jst()
    for channel, text in (brief.get("copies") or {}).items():
        (pack / f"{channel}.txt").write_text(text, encoding="utf-8", newline="\n")
        assert_text(channel, text)
        write_action_receipt(
            business_date=business_date,
            destination=channel,
            dry_run=True,
            fill_only=False,
            posted=False,
            text=text,
            live_generated_at=brief.get("generatedAt"),
            lag_hours=brief.get("lagHours"),
            outcome="built",
        )
    heatmaps: dict[str, str] = {}
    errors: list[dict[str, str]] = []
    for channel, url in (brief.get("channelHeatmaps") or {}).items():
        board = CHANNEL_BOARD[channel]
        script = CHANNEL_SCRIPT[channel]
        lang = SCRIPT_TO_OG_LANG[script]
        fmt = CHANNEL_FORMAT[channel]
        key = f"{board}-{fmt}-{lang}"
        if key in heatmaps:
            continue
        try:
            heatmaps[key] = heatmap_receipt_ref(
                download_heatmap(brief, pack, board, fmt=fmt, lang=lang)
            )
        except PromoError as error:
            errors.append({"stage": "heatmap", "key": key, "error": str(error)})
            print(f"PROMO_HEATMAP {key} {error}", file=sys.stderr)
    write_receipt(
        pack,
        {
            "generation": brief["generation"],
            "period": brief["period"],
            "post": False,
            "stage": "asserted" if not errors else "heatmaps-partial",
            "heatmaps": heatmaps,
            "errors": errors,
        },
    )
    print(json.dumps({
        "wrote": str(out),
        "generation": brief["generation"],
        "period": brief["period"],
        "heatmap": heatmaps.get("all-post-en"),
        "heatmaps": heatmaps,
        "heatmapUrl": brief["boards"].get("all", {}).get("heatmapUrl") or brief.get("channelHeatmaps", {}).get("x.com-en"),
        "channelHeatmaps": brief.get("channelHeatmaps"),
        "errors": errors,
    }, ensure_ascii=False))
    print("post=false")
    tcg = brief.get("copies", {}).get("x.com-en", "")
    if tcg:
        print("--- x.com-en ---")
        print(tcg, end="")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    pack = Path(args.pack)
    receipt = read_receipt(pack)
    if not receipt:
        raise PromoError(f"no receipt.json in {pack}")
    print(json.dumps(receipt, ensure_ascii=False))
    return 0


def cmd_assert(args: argparse.Namespace) -> int:
    expect = args.generation
    if args.require_live:
        health = fetch_json(f"{LIVE}/api/health")
        expect = generation_id(health.get("generation"))
        assert_box_matches_business_date(health, business_date_jst())
    result = assert_pack(Path(args.pack), expect_generation=expect)
    print(json.dumps(result, ensure_ascii=False))
    return 0


def cmd_heatmap(args: argparse.Namespace) -> int:
    brief = build_live_brief(allow_stale=bool(getattr(args, "allow_stale", False)))
    pack = Path(args.out) if args.out else promo_runtime_dir() / brief["generation"]
    pack.mkdir(parents=True, exist_ok=True)
    (pack / "brief.json").write_text(
        json.dumps(brief, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    path = download_heatmap(
        brief,
        pack,
        args.scope,
        fmt=args.fmt,
        lang=args.lang,
        theme=args.theme,
        updown=args.updown,
    )
    url = heatmap_og_url(
        args.scope,
        brief["period"],
        fmt=args.fmt,
        theme=args.theme,
        updown=args.updown,
        lang=args.lang,
    )
    print(json.dumps({"heatmap": str(path), "generation": brief["generation"], "url": url}, ensure_ascii=False))
    return 0


def cmd_pick_tab(args: argparse.Namespace) -> int:
    host = HOST_FOR_CHANNEL.get(args.channel, args.host)
    if not host:
        raise PromoError("need --channel or --host")
    pages = list_cdp_pages()
    decision = pick_cdp_tab(pages, host)
    print(json.dumps(decision, ensure_ascii=False))
    if decision["action"] == "reuse":
        extras = decision.get("extraSameHost") or []
        if extras:
            print(
                "same-host extras exist; reuse the first, do not open another tab",
                file=sys.stderr,
            )
    return 0


def cmd_self_test() -> int:
    # Negative fixtures must fire. Absence of a raise is the bug.
    cards = [
        {
            "id": "up1",
            "viewRank": 2,
            "tcg": "Pokémon",
            "name": {"en": "Up One", "zh-TW": "升一"},
            "windows": {
                "1d": {"changePct": {"value": 12.5, "status": "ready"}},
                "7d": {"changePct": {"value": 12.5, "status": "ready"}},
            },
        },
        {
            "id": "down1",
            "viewRank": 5,
            "tcg": "Pokémon",
            "name": {"en": "Down One", "zh-TW": "跌一"},
            "windows": {
                "1d": {"changePct": {"value": -8.2, "status": "ready"}},
                "7d": {"changePct": {"value": -8.2, "status": "ready"}},
            },
        },
        {
            "id": "flat",
            "viewRank": 1,
            "tcg": "Pokémon",
            "name": {"en": "Flat"},
            "windows": {"1d": {"changePct": {"value": 0, "status": "ready"}}},
        },
        {
            "id": "missing",
            "viewRank": 9,
            "name": {"en": "Grey"},
            "windows": {"1d": {"changePct": {"value": None, "status": "accumulating"}}},
        },
    ]
    movers = movers_from_cards(cards)
    assert movers["up"][0]["id"] == "up1", movers
    assert movers["down"][0]["id"] == "down1", movers
    assert len(movers["up"]) == 1 and len(movers["down"]) == 1

    fired = 0
    try:
        movers_from_cards(cards, period="180d")
    except PromoError:
        fired += 1
    else:
        raise SystemExit("180d period did not fire")

    try:
        assert_text("fork-zh", "这张卡升了")
    except PromoError as error:
        fired += 1
        if "Simplified" not in str(error):
            raise SystemExit(f"wrong simplified error: {error}")
    else:
        raise SystemExit("simplified zh-Hant did not fire")

    try:
        assert_text("whatsapp-ptcg", "C:\\Users\\jackson0202\\Downloads\\x.png")
    except PromoError as error:
        fired += 1
        if "leak" not in str(error):
            raise SystemExit(f"wrong leak error: {error}")
    else:
        raise SystemExit("path leak did not fire")

    try:
        assert_text("x.com-en", "see http://localhost:3800/pokemon")
    except PromoError:
        fired += 1
    else:
        raise SystemExit("localhost URL did not fire")

    pages = [
        {"id": "a", "type": "page", "url": "https://x.com/home"},
        {"id": "b", "type": "page", "url": "https://x.com/compose/post"},
        {"id": "c", "type": "page", "url": "https://app.cardzmarketcap.com/pokemon?period=1d"},
    ]
    x_tab = pick_cdp_tab(pages, "x.com")
    assert x_tab["action"] == "reuse" and x_tab["id"] == "a" and x_tab["openNew"] is False, x_tab
    assert x_tab["extraSameHost"] == ["b"], x_tab
    fresh = pick_cdp_tab(pages, "threads.net")
    assert fresh["action"] == "open_once" and fresh["openNew"] is True, fresh
    com_tab = pick_cdp_tab(
        list(pages) + [{"id": "t1", "type": "page", "url": "https://www.threads.com/@cardz.game"}],
        "threads.net",
    )
    assert com_tab["action"] == "reuse" and com_tab["id"] == "t1" and com_tab["openNew"] is False, com_tab
    fired += 3

    copy = render_copy(movers, board="all", script="zh-Hant")
    assert "🟢 #2 升一" in copy and "🔴 #5 跌一" in copy, copy
    assert PUBLIC_LINK in copy
    assert "因為" not in copy and "今日行情" not in copy
    fired += 1

    assert x_weighted_length("A") == 1
    assert x_weighted_length("皮") == 2
    assert x_weighted_length("🟢") == 2
    assert x_weighted_length(PUBLIC_LINK) == 23, x_weighted_length(PUBLIC_LINK)
    try:
        assert_text("x.com-en", "a" * 281)
    except PromoError as error:
        fired += 1
        if "280" not in str(error):
            raise SystemExit(f"wrong length error: {error}")
    else:
        raise SystemExit("x.com-en 281 ascii did not fire")

    long_en = {
        "id": "long",
        "officialName": "2015 Pokemon Japanese XY Promo Pretend Magikarp Pikachu Holo 150/XY-P",
        "setName": {"en": "Pokemon Japanese XY Promo"},
        "collectorNumber": "150/XY-P",
        "name": {"en": "2015 Pokemon Japanese XY Promo Pretend Magikarp Pikachu Holo 150/XY-P"},
    }
    peeled = short_name(long_en, "en")
    assert "Pretend Magikarp" in peeled and "150/XY-P" in peeled, peeled
    assert "2015" not in peeled, peeled
    luffy = {
        "id": "luffy",
        "name": {"zh-TW": "蒙其・D・魯夫", "en": "Monkey D. Luffy"},
        "collectorNumber": "OP05-060",
    }
    assert "OP05-060" in short_name(luffy, "zh-Hant")
    fired += 1

    og = heatmap_og_url("pokemon")
    assert "period=7d" in og and "scope=pokemon" in og and "lang=en" in og and "updown=green-up" in og, og
    assert "format=post" in og and "theme=dark" in og
    zh = heatmap_og_url_for_channel("threads-zh")
    assert "lang=zh-TW" in zh and "scope=all" in zh, zh
    hans = heatmap_og_url_for_channel("x.com-zh")
    assert "lang=zh-CN" in hans, hans
    ptcg = heatmap_og_url_for_channel("whatsapp-ptcg")
    assert "scope=pokemon" in ptcg and "lang=zh-TW" in ptcg, ptcg
    tcg4 = heatmap_og_url_for_channel("whatsapp-tcg-4")
    assert "scope=all" in tcg4 and "lang=zh-TW" in tcg4, tcg4
    assert tcg4 != ptcg, tcg4
    op = heatmap_og_url_for_channel("whatsapp-op")
    assert "scope=one-piece" in op, op
    red = heatmap_og_url("all", updown="red-up", fmt="status", lang="zh-TW")
    assert "updown=red-up" in red and "format=status" in red and "lang=zh-TW" in red, red
    try:
        heatmap_og_url("all", "180d")
    except PromoError:
        fired += 1
    else:
        raise SystemExit("180d heatmap URL did not fire")
    fired += 1

    print(json.dumps({"ok": True, "fired": fired}))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="CardZ Marketcap promo pack (no post)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    brief = sub.add_parser("brief")
    brief.add_argument("--period", default=DAILY_PERIOD)
    brief.add_argument("--out")
    brief.add_argument(
        "--allow-stale",
        action="store_true",
        help=f"skip the {PROMO_MAX_LIVE_LAG_HOURS_DEFAULT}h live-freshness gate",
    )
    ass = sub.add_parser("assert")
    ass.add_argument("pack")
    ass.add_argument("--generation")
    ass.add_argument("--require-live", action="store_true")
    tab = sub.add_parser("pick-tab")
    tab.add_argument("--channel")
    tab.add_argument("--host")
    heat = sub.add_parser("heatmap")
    heat.add_argument("--scope", default="all")
    heat.add_argument("--format", default="post", dest="fmt")
    heat.add_argument("--lang", default="en")
    heat.add_argument("--theme", default="dark")
    heat.add_argument("--updown", default="green-up")
    heat.add_argument("--out")
    heat.add_argument("--allow-stale", action="store_true")
    st = sub.add_parser("status")
    st.add_argument("pack")
    sub.add_parser("self-test")
    args = parser.parse_args()
    try:
        if args.cmd == "brief":
            return cmd_brief(args)
        if args.cmd == "assert":
            return cmd_assert(args)
        if args.cmd == "pick-tab":
            return cmd_pick_tab(args)
        if args.cmd == "heatmap":
            return cmd_heatmap(args)
        if args.cmd == "status":
            return cmd_status(args)
        if args.cmd == "self-test":
            return cmd_self_test()
    except PromoStaleLive as error:
        print(f"PROMO_STALE_LIVE {error}", file=sys.stderr)
        return 3
    except PromoError as error:
        print(f"PROMO_FAIL {error}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
