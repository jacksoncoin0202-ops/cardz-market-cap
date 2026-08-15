#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Qualify sealed candidates against the live product bar — independently.

Live bar (MODEL.md §12), sealed-only:
  identity frozen + ≥1 source frozen + image frozen + usable price.

This never touches PSA10 pass / promote / GitHub live.
Rejects junk binds. Accepts only high-confidence source matches.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from sealed_discover_lib import (  # noqa: E402
    WAVE1_RE,
    WAVE2_RE,
    console_key,
    score_pc_console,
    score_snk_box,
    tokens,
)
from sealed_image_harvest import html_is_sku_product  # noqa: E402
from sealed_operator import _freeze  # noqa: E402
from sealed_runtime import HTML_DIR, OUT_DIR, db, load_env, utc_now  # noqa: E402

SNK_ACCEPT = 0.50
PC_ACCEPT = 1.0
CLOTHING_RE = (
    "diesel", "fear of god", "polo", "nike", "adidas", "uniqlo", "supreme",
    "t-shirt", "hoodie", "sneaker", "apparels:t-",
)
ACTOR = "sealed-live-qualify"
STANDARD_KINDS = {"booster-box", "high-class-box"}
# URL-identity consoles already walked; not fuzzy inventory.
EXACT_PC_CONSOLE = {
    ("optcg", "en", "PRB-01"): "one-piece-premium-booster",
    ("optcg", "en", "PRB-02"): "one-piece-premium-booster-2",
    ("optcg", "jp", "PRB-01"): "one-piece-japanese-premium-booster",
    ("optcg", "jp", "PRB-02"): "one-piece-japanese-premium-booster-2",
    ("ptcg", "en", "CIN"): "pokemon-crimson-invasion",
    ("ptcg", "en", "ME04"): "pokemon-chaos-rising",
    ("ptcg", "jp", "ADV1"): "pokemon-japanese-ex-ruby-&-sapphire-expansion-pack",
    ("ptcg", "en", "FLI"): "pokemon-forbidden-light",
    ("ptcg", "en", "LTR"): "pokemon-legendary-treasures",
    ("ptcg", "en", "NVI"): "pokemon-noble-victories",
    ("ptcg", "en", "PGO"): "pokemon-pokemon-go",
    ("ptcg", "en", "SV01"): "pokemon-scarlet-&-violet",
    ("ptcg", "en", "SUM"): "pokemon-sun-&-moon",
    ("ptcg", "en", "SWSH01"): "pokemon-sword-&-shield",
    ("ptcg", "jp", "jp4"): "pokemon-japanese-rocket-gang",
    ("ptcg", "jp", "SM3H"): "pokemon-japanese-battle-rainbow",
    ("ptcg", "jp", "S10b"): "pokemon-japanese-go",
    ("ptcg", "jp", "SV1S"): "pokemon-japanese-scarlet-ex",
    ("ptcg", "jp", "SV1V"): "pokemon-japanese-violet-ex",
}


def _note(raw: str | None) -> dict[str, Any]:
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def classify_snk(sku: dict, bind: dict) -> dict[str, Any]:
    kind = str(sku.get("product_kind") or "")
    if kind not in STANDARD_KINDS:
        return {"decision": "hold", "reason": "non_standard_box_needs_human", "name": ""}
    note = _note(bind.get("note"))
    name = str(note.get("snkName") or "")
    localized = str(note.get("snkLocalized") or "")
    combined = f"{name} {localized} {bind.get('external_entity_id') or ''}".lower()
    if any(token in combined for token in CLOTHING_RE):
        return {"decision": "reject", "reason": "clothing_or_streetwear", "name": name[:80]}
    if re.search(r"(elite trainer|\betb\b|booster bundle|プロモ|1枚|シングル)", name, re.I):
        return {"decision": "reject", "reason": "not_booster_box", "name": name[:80]}
    if not name and not localized:
        return {"decision": "hold", "reason": "no_snk_name", "name": ""}
    score, why = score_snk_box(sku, name, localized)
    item = {"score": score, "why": why, "name": name[:80]}
    if why in {"game_mismatch", "wave_mismatch"}:
        item.update({"decision": "reject", "reason": why})
        return item
    if why == "lang_mismatch":
        if re.search(r"(japanese|日版|日本語)", name, re.I) and str(sku.get("lang")) == "en":
            item.update({"decision": "reject", "reason": "snk_jp_on_en_sku"})
            return item
        score_en, why_en = score_snk_box(sku, name, "")
        if why_en == "ok" and score_en >= SNK_ACCEPT and not re.search(r"(japanese|日版|日本語)", name, re.I):
            item.update({"score": score_en, "why": why_en, "decision": "accept", "reason": f"snk_en_name_{score_en}"})
            wave = str(sku.get("print_wave") or "std")
            if wave in {"wave1", "wave2"} and not WAVE1_RE.search(name) and not WAVE2_RE.search(name):
                item.update({"decision": "hold", "reason": "snk_wave_unmarked"})
            return item
        item.update({"decision": "hold", "reason": "snk_localized_lang_unclear"})
        return item
    if why == "not_box":
        if re.search(r"(booster box|display box|booster pack.*box|ブースター)", name, re.I):
            own = tokens(f"{sku.get('name_en') or ''} {sku.get('name_jp') or ''}")
            overlap = (len(own & tokens(name)) / len(own)) if own else 0.0
            if overlap >= SNK_ACCEPT:
                item.update({"score": round(overlap, 2), "decision": "accept", "reason": f"snk_box_keyword_{overlap:.2f}"})
                return item
            item.update({"decision": "hold", "reason": "snk_box_keyword_but_scorer_rejected"})
        else:
            item.update({"decision": "reject", "reason": "not_box"})
        return item
    if why == "ok" and score >= SNK_ACCEPT:
        wave = str(sku.get("print_wave") or "std")
        hay = f"{name} {localized}"
        if wave in {"wave1", "wave2"} and not WAVE1_RE.search(hay) and not WAVE2_RE.search(hay):
            item.update({"decision": "hold", "reason": "snk_wave_unmarked"})
            return item
        item.update({"decision": "accept", "reason": f"snk_overlap_{score}"})
        return item
    item.update({"decision": "hold", "reason": why or "low_overlap"})
    return item


def classify_pc(sku: dict, bind: dict) -> dict[str, Any]:
    kind = str(sku.get("product_kind") or "")
    if kind not in STANDARD_KINDS:
        return {"decision": "hold", "reason": "non_standard_box_needs_human"}
    url = str(bind.get("canonical_url") or "")
    ext = str(bind.get("external_entity_id") or "")
    path = urlparse(url).path if url else f"/game/{ext}"
    parts = [p for p in path.strip("/").split("/") if p]
    console = parts[1] if len(parts) >= 2 and parts[0] == "game" else (ext.split("/")[0] if ext else "")
    product = parts[2] if len(parts) >= 3 else (ext.split("/")[1] if "/" in ext else "")
    score = score_pc_console(sku, console)
    set_norm = re.sub(r"[^a-z0-9]", "", str(sku.get("set_code") or "")).lower()
    hay = re.sub(r"[^a-z0-9]", "", f"{console}{product}")
    if set_norm and len(set_norm) >= 3 and set_norm in hay:
        score = max(score, 3.0)
    want_console = EXACT_PC_CONSOLE.get(
        (str(sku.get("game") or ""), str(sku.get("lang") or ""), str(sku.get("set_code") or ""))
    )
    if want_console and console_key(console) == console_key(want_console):
        score = max(score, 2.0)
    item = {"score": score, "url": url or ext, "console": console, "product": product}
    game = str(sku.get("game") or "")
    lang = str(sku.get("lang") or "")
    if game == "optcg" and not console.startswith("one-piece"):
        item.update({"decision": "reject", "reason": "pc_wrong_game"})
        return item
    if game == "ptcg" and not console.startswith("pokemon"):
        item.update({"decision": "reject", "reason": "pc_wrong_game"})
        return item
    if "korean" in console and lang != "ko":
        item.update({"decision": "reject", "reason": "pc_korean_on_non_ko"})
        return item
    if (lang == "jp") != ("japanese" in console):
        item.update({"decision": "reject", "reason": "pc_lang_mismatch"})
        return item
    if "double-pack" in product or product.startswith("half-"):
        item.update({"decision": "reject", "reason": f"not_box_slug:{product}"})
        return item
    boxish = (
        product in {"booster-box", "sealed-booster", "display-box", "high-class-booster-box", "base-set-booster-box"}
        or "booster-box" in product
        or "premium-booster" in product
        or "high-class" in product
    )
    if product and not boxish:
        item.update({"decision": "reject", "reason": f"not_box_slug:{product}"})
        return item
    check_url = url or f"https://www.pricecharting.com/game/{ext}"
    digest = hashlib.sha256(check_url.encode("utf-8")).hexdigest()[:10]
    html_path = HTML_DIR / f"{sku['id']}_{digest}.html"
    if html_path.is_file():
        title_ok = html_is_sku_product(
            html_path.read_text(encoding="utf-8", errors="replace"), sku
        )
        if not title_ok and score < PC_ACCEPT:
            item.update({"decision": "hold", "reason": "html_title_not_sku"})
            return item
    if score >= PC_ACCEPT:
        item.update({"decision": "accept", "reason": f"pc_score_{score}"})
        return item
    item.update({"decision": "hold", "reason": "pc_score_low"})
    return item


def run(*, apply: bool) -> int:
    load_env()
    conn = db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT p.id, p.sku_id, p.slug, p.game, p.lang, p.group_code, p.set_code,
               p.name_en, p.name_jp, p.print_wave, p.product_kind, p.status
        FROM catalog_sealed_product p
        WHERE p.status <> 'no-box'
        ORDER BY p.id
        """
    )
    products = [dict(r) for r in cur.fetchall()]
    by_id = {int(p["id"]): p for p in products}
    cur.execute(
        """
        SELECT sealed_id, source_code, external_entity_id, canonical_url, note, match_status, resolved
        FROM catalog_sealed_source_identity
        WHERE match_status='candidate'
        """
    )
    decisions: list[dict[str, Any]] = []
    for row in cur.fetchall():
        sku = by_id.get(int(row["sealed_id"]))
        if not sku:
            continue
        bind = dict(row)
        if bind["source_code"] == "snkrdunk":
            verdict = classify_snk(sku, bind)
        elif bind["source_code"] == "pricecharting":
            verdict = classify_pc(sku, bind)
        else:
            verdict = {"decision": "hold", "reason": "unknown_source"}
        decisions.append({
            "sku": sku["sku_id"],
            "slug": sku["slug"],
            "group": sku["group_code"],
            "source": bind["source_code"],
            "ext": bind["external_entity_id"],
            **verdict,
        })

    counts = Counter(d["decision"] for d in decisions)
    by_source = Counter((d["source"], d["decision"]) for d in decisions)
    applied = {"rejected": 0, "sourceFrozen": 0, "identityFrozen": 0, "imageFrozen": 0}
    if apply:
        for item in decisions:
            if item["decision"] != "reject":
                continue
            cur.execute(
                """
                UPDATE catalog_sealed_source_identity
                SET match_status='rejected', resolved=1, note=%s
                WHERE source_code=%s AND external_entity_id=%s AND match_status='candidate'
                """,
                (f"live-qualify:{item.get('reason')}", item["source"], item["ext"]),
            )
            applied["rejected"] += cur.rowcount
        for item in decisions:
            if item["decision"] != "accept":
                continue
            sku = next(p for p in products if p["sku_id"] == item["sku"])
            sealed_id = int(sku["id"])
            cur.execute(
                """
                UPDATE catalog_sealed_source_identity
                SET match_status='exact', resolved=1
                WHERE sealed_id=%s AND source_code=%s AND external_entity_id=%s
                """,
                (sealed_id, item["source"], item["ext"]),
            )
            _freeze(
                cur, sealed_id, "source", item["source"], item["ext"], ACTOR,
                f"live-qualify {item.get('reason')}",
            )
            applied["sourceFrozen"] += 1
        for sku in products:
            _freeze(cur, int(sku["id"]), "identity", "", sku["sku_id"], ACTOR, "catalog identity locked")
            applied["identityFrozen"] += 1
        cur.execute(
            """
            SELECT a.sealed_id, a.content_sha256
            FROM market_sealed_image_asset a
            JOIN (
              SELECT sealed_id, MAX(captured_at) AS captured_at
              FROM market_sealed_image_asset WHERE image_kind='box_front' GROUP BY sealed_id
            ) latest ON latest.sealed_id=a.sealed_id AND latest.captured_at=a.captured_at
            WHERE a.image_kind='box_front'
            """
        )
        for row in cur.fetchall():
            _freeze(cur, int(row["sealed_id"]), "image", "", str(row["content_sha256"]), ACTOR, "harvested box_front")
            applied["imageFrozen"] += 1
        conn.commit()
    else:
        conn.rollback()
    conn.close()

    receipt = {
        "asOf": utc_now(),
        "action": "sealed-live-qualify",
        "mode": "apply" if apply else "dry-run",
        "thresholds": {"snkOverlap": SNK_ACCEPT, "pcConsole": PC_ACCEPT},
        "counts": dict(counts),
        "bySourceDecision": {f"{src}:{dec}": n for (src, dec), n in sorted(by_source.items())},
        "applied": applied if apply else None,
        "rejectSample": [d for d in decisions if d["decision"] == "reject"][:25],
        "holdSample": [d for d in decisions if d["decision"] == "hold"][:25],
        "acceptSample": [d for d in decisions if d["decision"] == "accept"][:25],
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "live-qualify-receipt.json"
    out.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False, indent=2, default=str))
    print(f"wrote {out}")
    return 0


def main_from_args(*, apply: bool) -> int:
    return run(apply=apply)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="reject junk + freeze accept/identity/image")
    args = ap.parse_args()
    return run(apply=args.apply)


if __name__ == "__main__":
    raise SystemExit(main())
