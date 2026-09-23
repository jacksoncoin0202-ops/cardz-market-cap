#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared sealed discovery helpers.

SNK: existing harvest (`snkrdunk_all.jsonl`) is the first authority — the
public master/history contract is already documented in snkrdunk_bulk.py.
PC: console listing pages are the first authority; product HTML is VGPC
(pricecharting_page_parse). Search URLs are bootstrap only.
"""
from __future__ import annotations

import hashlib
import json
import re
from html import unescape
from typing import Any
from urllib.parse import quote, unquote, urlparse

from sealed_runtime import sha

GENERIC_TOKENS = {
    "pokemon", "card", "game", "one", "piece", "box", "pack", "booster",
    "expansion", "enhanced", "high", "class", "scarlet", "violet", "display",
    "ポケモン", "カード", "ゲーム", "ボックス", "パック", "拡張", "強化",
    "ハイクラス", "ブースター", "ワンピース", "english", "japanese",
}

BOX_NAME_RE = re.compile(r"(BOX|ボックス|Booster Box|booster box|Display)", re.I)
REJECT_BOX_RE = re.compile(
    r"(PSA\s*\d|BGS|CGC|ARS|1枚|シングル|エリートトレーナー|ETB|ケース|カートン|"
    r"スペシャルBOX|Special Box|Pokemon Center|ポケモンセンター|"
    r"デッキ|スターター|collection box|コレクター|封入特典|プロモ|"
    r"\[[A-Z0-9]{1,8}-\d+\])",
    re.I,
)
QUOTE_JP = re.compile(r"「([^」]{2,40})」")
QUOTE_EN = re.compile(r'"([^"]{2,60})"')
EN_MARK_RE = re.compile(r"\bEN\b|english|英語", re.I)
JP_MARK_RE = re.compile(r"japanese|日版|日本語", re.I)
WAVE1_RE = re.compile(r"初版|1st|wave\s*1|\bw1\b", re.I)
WAVE2_RE = re.compile(r"再販|reprint|wave\s*2|\bw2\b|unlimited", re.I)
SNK_SPELLINGS = ("trading-cards", "apparel-groups", "apparels")
SNK_ITEM_RE = re.compile(r"^(?:%s):(\d+)$" % "|".join(SNK_SPELLINGS))
PC_COVER_RE = re.compile(
    r'<div[^>]*class="[^"]*\bcover\b[^"]*"[^>]*>.*?<img[^>]+src=["\']([^"\']+)["\']',
    re.I | re.S,
)
PC_STORAGE_RE = re.compile(
    r"https://storage\.googleapis\.com/images\.pricecharting\.com/([a-z0-9]+)/(\d+)\.jpg",
    re.I,
)
PC_GAME_HREF_RE = re.compile(
    r'href="(?:https://www\.pricecharting\.com)?(/game/[^"?#]+)"',
    re.I,
)
NOT_BOX_SLUG = ("etb", "elite-trainer", "bundle", "blister", "tin", "case", "pack-art", "single")
BOX_SLUG = ("booster-box", "sealed-booster", "display-box", "high-class")

GAME_EN = {"optcg": "ONE PIECE Card Game", "ptcg": "Pokémon TCG"}
GAME_JA = {"optcg": "ONE PIECEカードゲーム", "ptcg": "ポケモンカードゲーム"}
KIND_EN = {"booster-box": "Booster Box", "high-class-box": "High Class Box"}
KIND_JA = {"booster-box": "ブースターパック", "high-class-box": "ハイクラスパック"}
WAVE_EN = {"wave1": "1st Edition", "wave2": "Reprint", "1st": "1st Edition", "unlimited": "Unlimited"}
WAVE_JA = {"wave1": "初版", "wave2": "再販", "1st": "初版", "unlimited": "アンリミテッド"}


def compact_note(payload: dict[str, Any], limit: int = 500) -> str:
    raw = json.dumps(payload, ensure_ascii=False, default=str)
    if len(raw) <= limit:
        return raw
    trimmed = dict(payload)
    for key in ("snkLocalized", "snkName", "pcName", "imageUrl"):
        if key in trimmed and isinstance(trimmed[key], str):
            trimmed[key] = trimmed[key][:80]
    raw = json.dumps(trimmed, ensure_ascii=False, default=str)
    return raw[:limit]


def tokens(text: str) -> set[str]:
    parts = re.split(r"[^a-z0-9\u3040-\u30ff\u3400-\u9fff]+", (text or "").lower())
    return {p for p in parts if len(p) >= 3 and p not in GENERIC_TOKENS}


def name_overlap(left: str, right: str) -> float:
    a = tokens(left)
    if not a:
        return 0.0
    return round(len(a & tokens(right)) / len(a), 2)


def game_of(name: str) -> str:
    low = (name or "").lower()
    if "one piece" in low or "ワンピース" in (name or ""):
        return "optcg"
    if "pokemon" in low or "ポケモン" in (name or ""):
        return "ptcg"
    return ""


def lang_of(name: str) -> str:
    if EN_MARK_RE.search(name or ""):
        return "en"
    if JP_MARK_RE.search(name or ""):
        return "jp"
    if re.search(r"[\u3040-\u30ff\u3400-\u9fff]", name or ""):
        return "jp"
    return "unknown"


def norm_game(value: str) -> str:
    return str(value or "").strip().lower()


def norm_lang(value: str) -> str:
    return str(value or "").strip().lower()


def is_kept_box_name(name: str) -> bool:
    if not BOX_NAME_RE.search(name or ""):
        return False
    return not REJECT_BOX_RE.search(name or "")


def score_snk_box(sku: dict, box_name: str, box_localized: str) -> tuple[float, str]:
    combined = f"{box_name} {box_localized}".strip()
    if not is_kept_box_name(combined):
        return 0.0, "not_box"
    guessed_game = game_of(combined)
    if guessed_game and guessed_game != norm_game(sku.get("game")):
        return 0.0, "game_mismatch"
    guessed_lang = lang_of(combined)
    sku_lang = norm_lang(sku.get("lang"))
    if guessed_lang != "unknown" and guessed_lang != sku_lang:
        return 0.0, "lang_mismatch"
    own = tokens(f"{sku.get('name_en') or ''} {sku.get('name_jp') or ''}")
    if not own:
        return 0.0, "no_sku_tokens"
    overlap = len(own & tokens(combined)) / len(own)
    quotes = QUOTE_JP.findall(combined) + QUOTE_EN.findall(combined)
    if any(
        q and (q in (sku.get("name_jp") or "") or q.lower() in (sku.get("name_en") or "").lower())
        for q in quotes
    ):
        overlap = max(overlap, 0.85)
    wave = sku.get("print_wave") or "std"
    if wave == "wave1" and WAVE2_RE.search(combined) and not WAVE1_RE.search(combined):
        return 0.0, "wave_mismatch"
    if wave == "wave2" and WAVE1_RE.search(combined) and not WAVE2_RE.search(combined):
        return 0.0, "wave_mismatch"
    if overlap < 0.34:
        return round(overlap, 2), "low_overlap"
    return round(overlap, 2), "ok"


def same_item_ids(source: str, external_id: str) -> tuple[str, ...]:
    """Every external id that names the same source item. SNK's adapter drops the trading-cards:/apparel-groups:/
    apparels: prefix and fetches /v1/apparels/{itemId}, so the three spellings are one box (2026-09-23: OP-01 EN
    held trading-cards:136031, the JP box OP-01 JP holds as apparels:136031)."""
    m = SNK_ITEM_RE.match(external_id) if source == "snkrdunk" else None
    return tuple(f"{prefix}:{m.group(1)}" for prefix in SNK_SPELLINGS) if m else (external_id,)


def insert_candidate_bind(
    cur,
    *,
    source: str,
    external_id: str,
    sealed_id: int,
    url: str,
    note: str,
    origin: str,
) -> str:
    """Insert a resolved candidate. Never take an item another SKU holds under any spelling, never reopen a
    rejected one."""
    ids = same_item_ids(source, external_id)
    cur.execute(
        f"""
        SELECT sealed_id, external_entity_id, match_status FROM catalog_sealed_source_identity
        WHERE source_code=%s AND external_entity_id IN ({",".join(["%s"] * len(ids))})
        """,
        (source, *ids),
    )
    rows = list(cur.fetchall())
    mine = [r for r in rows if int(r["sealed_id"]) == sealed_id]
    # another SKU's live row is a conflict; its rejected row only when it sits on this exact id (the key is taken)
    held = [r for r in rows if int(r["sealed_id"]) != sealed_id
            and (r["match_status"] != "rejected" or r["external_entity_id"] == external_id)]
    if held:
        return "conflict_exact" if any(r["match_status"] == "exact" for r in held) else "conflict_other"
    if any(r["match_status"] == "rejected" for r in mine):
        # A reject is a decision, not a cache entry: rediscovering the same item must not reopen it
        # (2026-09-23 the weekly SNK search flipped BW1B's rejected DIESEL T-shirt back to candidate).
        return "already_rejected"
    if any(r["match_status"] == "exact" for r in mine):
        # An accept is a decision too: finding the item again must not demote it. 2026-09-23 a scan's PC inventory
        # ingest set 193 accepted PC binds back to candidate and overwrote the notes that named their products.
        return "already_exact"
    if mine and not any(r["external_entity_id"] == external_id for r in mine):
        # this SKU already holds the item under another spelling
        return "already_bound"
    if mine:
        cur.execute(
            """
            UPDATE catalog_sealed_source_identity
            SET resolved=1, match_status='candidate', canonical_url=%s, note=%s
            WHERE source_code=%s AND external_entity_id=%s
            """,
            ((url or "")[:600] or None, note[:500], source, external_id),
        )
        return "updated"
    cur.execute(
        """
        SELECT external_entity_id, match_status FROM catalog_sealed_source_identity
        WHERE sealed_id=%s AND source_code=%s AND match_status<>'rejected'
        """,
        (sealed_id, source),
    )
    owned = cur.fetchone()
    if owned:
        return "already_exact" if owned["match_status"] == "exact" else "already_bound"
    cur.execute(
        """
        INSERT INTO catalog_sealed_source_identity
          (source_code, external_entity_id, sealed_id, canonical_url, match_status,
           resolved, evidence_sha256, note)
        VALUES (%s,%s,%s,%s,'candidate',1,%s,%s)
        """,
        (
            source,
            external_id[:191],
            sealed_id,
            (url or "")[:600] or None,
            sha({"sku": sealed_id, "source": source, "ext": external_id, "origin": origin}),
            note[:500],
        ),
    )
    return "inserted"


def unbound_skus(cur, source: str) -> list[dict]:
    cur.execute(
        """
        SELECT p.id, p.sku_id, p.slug, p.game, p.lang, p.group_code, p.set_code,
               p.name_en, p.name_jp, p.print_wave, p.product_kind, p.status
        FROM catalog_sealed_product p
        WHERE p.status <> 'no-box'
          AND NOT EXISTS (
            SELECT 1 FROM catalog_sealed_source_identity i
            WHERE i.sealed_id=p.id AND i.source_code=%s AND i.match_status<>'rejected'
          )
        ORDER BY p.id
        """,
        (source,),
    )
    return [dict(r) for r in cur.fetchall()]


def pc_cover_urls(html: str) -> list[str]:
    found: list[str] = []
    match = PC_COVER_RE.search(html or "")
    if match:
        found.append(unescape(match.group(1)))
    found.extend(m.group(0) for m in PC_STORAGE_RE.finditer(html or ""))
    expanded: list[str] = []
    seen: set[str] = set()
    for url in found:
        for candidate in expand_pc_image_sizes(url):
            if candidate not in seen:
                seen.add(candidate)
                expanded.append(candidate)
    return expanded


def expand_pc_image_sizes(url: str) -> list[str]:
    match = re.search(
        r"(https://storage\.googleapis\.com/images\.pricecharting\.com/[a-z0-9]+/)(\d+)(\.jpg)",
        url,
        re.I,
    )
    if not match:
        return [url]
    base, ext = match.group(1), match.group(3)
    return [f"{base}{size}{ext}" for size in (1600, 960, 240)]


PC_CONSOLE_BODY_RE = re.compile(
    r'href="(?:https://www\.pricecharting\.com)?(/console/(?:one-piece|pokemon)[^"#?]+)"'
    r"(?:[^>]*>)([^<]+)",
    re.I,
)
PC_ROW_SPLIT_RE = re.compile(r'<tr id="product-(\d+)"', re.I)
PC_TITLE_RE = re.compile(
    r'<td class="title"[^>]*>\s*<a href="((?:https://www\.pricecharting\.com)?/game/[^"]+)"[^>]*>(.*?)</a>',
    re.I | re.S,
)
PC_PRICE_RE = re.compile(
    r'<td class="price numeric used_price">\s*<span class="js-price">([^<]*)</span>',
    re.I | re.S,
)
PC_OWN_RE = re.compile(r"You own:\s*\d+\s*/\s*(\d+)", re.I)
PC_CANONICAL_CONSOLE_RE = re.compile(
    r'<link rel="canonical" href="https://www\.pricecharting\.com/console/([^"?#]+)"',
    re.I,
)
SKIP_PC_SET_RE = re.compile(
    r"starter-deck|start-deck|ultra-deck|promo|carddass|mcdonald|vending|old-maid|topsun|gift-box",
    re.I,
)
PC_BOX_TITLE_RE = re.compile(
    r"\b(booster box|display box|high[- ]class(?: pack)? box|enhanced booster box|half booster box)\b",
    re.I,
)
PC_BOX_SLUG_RE = re.compile(r"(booster-box|display-box|high-class|sealed-booster)", re.I)


def _strip_pc_text(text: str) -> str:
    return re.sub(r"<[^>]+>", "", unescape(text or "")).replace("\xa0", " ").strip()


def classify_pc_console(slug: str) -> str:
    low = unquote(slug or "").lower()
    if SKIP_PC_SET_RE.search(low):
        return "skip"
    return "jp-set" if "japanese" in low else "en-set"


def classify_pc_row(title: str, slug: str) -> str:
    blob = f"{title} {slug}"
    if PC_BOX_TITLE_RE.search(blob) or PC_BOX_SLUG_RE.search(slug):
        if "etb" in slug or "elite-trainer" in slug:
            return "etb"
        if "bundle" in slug and "booster-box" not in slug:
            return "bundle"
        return "box"
    if re.search(r"\b(etb|elite trainer|blister|tin|case)\b", blob, re.I):
        return "other-sealed"
    return "card"


def parse_pc_category_consoles(html: str) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for match in PC_CONSOLE_BODY_RE.finditer(html or ""):
        path = unquote(match.group(1).split("?")[0]).rstrip("/")
        slug = path.split("/console/", 1)[-1]
        if slug in seen:
            continue
        seen.add(slug)
        out.append(
            {
                "slug": slug,
                "url": "https://www.pricecharting.com" + path,
                "title": _strip_pc_text(match.group(2)),
                "kind": classify_pc_console(slug),
                "game": "optcg" if slug.startswith("one-piece") else "ptcg",
                "lang": "jp" if "japanese" in slug.lower() else "en",
            }
        )
    return out


def parse_pc_games_table(html: str) -> list[dict]:
    rows: list[dict] = []
    parts = PC_ROW_SPLIT_RE.split(html or "")
    for i in range(1, len(parts), 2):
        pid = parts[i]
        chunk = parts[i + 1].split("</tr>", 1)[0]
        title_m = PC_TITLE_RE.search(chunk)
        if not title_m:
            continue
        href = title_m.group(1).split("?")[0]
        if href.startswith("/"):
            href = "https://www.pricecharting.com" + href
        path_parts = href.rstrip("/").split("/")
        slug = path_parts[-1]
        console = unquote(path_parts[-2]) if len(path_parts) >= 2 else ""
        title = _strip_pc_text(title_m.group(2))
        price_m = PC_PRICE_RE.search(chunk)
        ungraded = (price_m.group(1) if price_m else "").strip()
        rows.append(
            {
                "pid": pid,
                "href": href,
                "console": console,
                "slug": slug,
                "title": title,
                "kind": classify_pc_row(title, slug),
                "ungraded": ungraded,
                "hasPrice": bool(re.search(r"[\d.]", ungraded)),
            }
        )
    return rows


def is_pc_console_page(html: str, slug: str) -> bool:
    if "games_table" not in (html or "") or "just a moment" in (html or "").lower():
        return False
    match = PC_CANONICAL_CONSOLE_RE.search(html or "")
    return bool(match and unquote(match.group(1)).rstrip("/") == slug)


def pc_own_count(html: str) -> int | None:
    match = PC_OWN_RE.search(html or "")
    return int(match.group(1)) if match else None


def pc_game_links(html: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for match in PC_GAME_HREF_RE.finditer(html or ""):
        path = match.group(1).split("?")[0].rstrip("/")
        if path in seen:
            continue
        seen.add(path)
        out.append("https://www.pricecharting.com" + path)
    return out


def is_pc_box_url(url: str) -> bool:
    slug = urlparse(url).path.rsplit("/", 1)[-1].lower()
    if any(token in slug for token in NOT_BOX_SLUG):
        return False
    return any(token in slug for token in BOX_SLUG)


def pc_slug_variants(console_slug: str, print_wave: str) -> list[str]:
    console = console_slug.strip("/").split("/")[-1]
    first = ["booster-box", "sealed-booster-box", "display-box"]
    if print_wave in ("wave1", "1st"):
        first = ["booster-box-1st-edition", "booster-box"] + first
    elif print_wave in ("wave2", "unlimited"):
        first = ["booster-box-unlimited", "booster-box"] + first
    seen: set[str] = set()
    urls: list[str] = []
    for suffix in first:
        url = f"https://www.pricecharting.com/game/{console}/{suffix}"
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def console_slug_from_url(url: str) -> str | None:
    match = re.search(r"pricecharting\.com/console/([^?#]+)", url or "", re.I)
    if not match:
        return None
    return match.group(1).strip("/")


def grammar_full_name_en(product: dict) -> str:
    game = norm_game(product.get("game"))
    parts = [
        GAME_EN.get(game, "TCG"),
        KIND_EN.get(str(product.get("product_kind") or ""), "Booster Box"),
        str(product.get("name_en") or "").strip(),
        "EN" if norm_lang(product.get("lang")) == "en" else "Japanese",
        "Box",
    ]
    wave = WAVE_EN.get(str(product.get("print_wave") or ""), "")
    if wave:
        parts.append(wave)
    return " ".join(p for p in parts if p)


def grammar_full_name_ja(product: dict) -> str:
    game = norm_game(product.get("game"))
    name = str(product.get("name_jp") or product.get("name_en") or "").strip()
    parts = [
        GAME_JA.get(game, "TCG"),
        KIND_JA.get(str(product.get("product_kind") or ""), "ブースターパック"),
        name,
        "BOX",
    ]
    wave = WAVE_JA.get(str(product.get("print_wave") or ""), "")
    if wave:
        parts.append(wave)
    return " ".join(p for p in parts if p)


def yahoo_jp_query(name_jp: str | None, name_en: str | None, print_wave: str, game: str = "") -> str:
    # quote brackets are not words a seller types: SM1+ 強化拡張パック「サン&ムーン」 searches as 強化拡張パック サン&ムーン
    core = " ".join(re.sub(r"[「」『』]", " ", name_jp or name_en or "").split())
    wave = ""
    if print_wave == "wave1":
        wave = "初版"
    elif print_wave == "wave2":
        wave = "再販"
    guessed = game_of(f"{core} {name_en or ''}") or norm_game(game)
    if "optcg" in guessed:
        guessed = "optcg"
    elif "ptcg" in guessed:
        guessed = "ptcg"
    prefix = "ワンピースカード" if guessed == "optcg" else "ポケモンカード"
    return " ".join(b for b in (prefix, core, wave, "BOX") if b)


def yahoo_closedsearch_url(query: str) -> str:
    encoded = quote(query)
    return f"https://auctions.yahoo.co.jp/closedsearch/closedsearch?p={encoded}&va={encoded}"


def yahoo_query_needs_rewrite(url: str, lang: str) -> bool:
    if lang != "jp" or "auctions.yahoo.co.jp" not in (url or ""):
        return False
    return bool(re.search(r"Wave\+|reprint|English|Booster", url, re.I))


SKIP_PC_CONSOLE_RE = re.compile(r"korean|chinese", re.I)
SKIP_PC_BOX_SLUG_RE = re.compile(
    r"build|jumbo|half-booster|prerelease|deluxe|series-|blister|elite-trainer|etb|bundle",
    re.I,
)


def parse_pc_usd(text: str) -> float | None:
    match = re.search(r"[\d,.]+", text or "")
    if not match:
        return None
    try:
        value = float(match.group(0).replace(",", ""))
    except ValueError:
        return None
    return value if value > 0 else None


def console_key(value: str) -> str:
    return unescape(unquote(value or "")).replace("&amp;", "&").strip("/").lower()


def score_pc_console(sku: dict, console_slug: str, console_title: str = "") -> float:
    slug = console_key(console_slug)
    if not slug or SKIP_PC_CONSOLE_RE.search(slug):
        return 0.0
    game = norm_game(sku.get("game"))
    if game == "optcg" and not slug.startswith("one-piece"):
        return 0.0
    if game == "ptcg" and not slug.startswith("pokemon"):
        return 0.0
    jp_console = "japanese" in slug
    if (norm_lang(sku.get("lang")) == "jp") != jp_console:
        return 0.0
    set_code = re.sub(r"[^a-z0-9]", "", str(sku.get("set_code") or "").lower())
    hay = re.sub(r"[^a-z0-9]", "", slug + " " + (console_title or ""))
    score = name_overlap(str(sku.get("name_en") or ""), slug.replace("-", " ") + " " + console_title) * 2
    if set_code and len(set_code) >= 3 and set_code in hay:
        score += 3.0
    return round(score, 2)


def pick_pc_inventory_box(sku: dict, boxes: list[dict]) -> dict | None:
    usable = []
    for box in boxes:
        slug = str(box.get("slug") or "")
        if SKIP_PC_BOX_SLUG_RE.search(slug):
            continue
        if str(sku.get("product_kind") or "") == "high-class-box":
            if "high-class" not in slug and "high class" not in str(box.get("title") or "").lower():
                continue
        elif "high-class" in slug:
            continue
        usable.append(box)
    if not usable:
        return None
    wave = str(sku.get("print_wave") or "std")
    ranked: list[tuple[int, dict]] = []
    for box in usable:
        slug = str(box.get("slug") or "")
        score = 0
        if wave in ("wave1", "1st") and ("1st" in slug or "blue-bottom" in slug):
            score += 5
        if wave in ("wave2", "unlimited") and any(tok in slug for tok in ("unlimited", "reprint", "white")):
            score += 5
        if slug == "booster-box":
            score += 3 if wave in ("std", "") else 1
        if slug.startswith("booster-box-") and score == 0:
            score += 1
        ranked.append((score, box))
    ranked.sort(key=lambda item: -item[0])
    if ranked[0][0] > 0:
        return ranked[0][1]
    return usable[0] if len(usable) == 1 else None


def match_pc_inventory(sku: dict, boxes_by_console: dict[str, list[dict]], hint_url: str = "") -> dict | None:
    hint = console_slug_from_url(hint_url) or ""
    if "/game/" in (hint_url or "") and not hint:
        path = urlparse(hint_url).path.strip("/").split("/")
        if len(path) >= 2 and path[0] == "game":
            hint = path[1]
    hint = console_key(hint)
    if hint and hint in boxes_by_console and score_pc_console(sku, hint) > 0:
        picked = pick_pc_inventory_box(sku, boxes_by_console[hint])
        if picked:
            return picked
    scored: list[tuple[float, str]] = []
    for slug, boxes in boxes_by_console.items():
        title = str((boxes[0] or {}).get("consoleTitle") or "")
        score = score_pc_console(sku, slug, title)
        if score >= 2.0:
            scored.append((score, slug))
    scored.sort(reverse=True)
    for _, slug in scored[:3]:
        picked = pick_pc_inventory_box(sku, boxes_by_console[slug])
        if picked:
            return picked
    return None


def accept_commands(source: str) -> list[str]:
    """Where a discover receipt points next: one SKU at a time, after looking at the item. Never a bulk accept:
    2026-09-23 every sealed source freeze in the DB came from one unlooked-at bulk accept, JP boxes on EN SKUs among them."""
    return [f"$PY -X utf8 pipelines/sealed_daily.py accept-binding --sku <sku> --kind source --source-code {source}"]


def evidence_sha(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
