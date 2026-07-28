#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bind GemRate PSA10 watchlist to SNK item ids using harvest inventory.

Matching = multi-form collector keys + set-code hints + name tokens (fail-closed).
Writes:
  - catalog_source_identity (snkrdunk / exact)
  - data/runtime/private-source-map/qualified-940-snk-ids.txt
  - data/runtime/private-source-map/snk-bind-report.json

Then:
  python -X utf8 pipelines/snk_market_data.py --ids-file ... --condition trading_card_single_psa10 --out ...
  python -X utf8 pipelines/bind_snk_watchlist.py ingest-harvest-prices
  python -X utf8 pipelines/ingest_snk_trades_sales.py --harvest ...
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MAP = ROOT / "data/runtime/private-source-map"
HARVEST = ROOT / "data/private/snkrdunk_brute/snkrdunk_all.jsonl"
IDS_OUT = MAP / "qualified-940-snk-ids.txt"
REPORT = MAP / "snk-bind-report.json"

STOP = {
    "the", "and", "with", "ex", "gx", "vmax", "vstar", "secret", "promo", "full",
    "art", "alternate", "illustration", "rare", "ultra", "holo", "pokemon", "card",
    "one", "piece", "japanese", "english", "edition",
}

# set_name / set tokens → SNK bracket set codes (uppercase, no hyphen)
SET_NAME_HINTS: list[tuple[re.Pattern[str], list[str]]] = [
    (re.compile(r"\b151\b", re.I), ["SV2A"]),
    (re.compile(r"terastal\s*festival", re.I), ["SV8A"]),
    (re.compile(r"crown\s*zenith", re.I), ["S12A", "S12A"]),
    (re.compile(r"evolving\s*skies", re.I), ["S6A", "S7R", "S7D"]),
    (re.compile(r"brilliant\s*stars", re.I), ["S9", "S9A"]),
    (re.compile(r"silver\s*tempest", re.I), ["S12", "S12A"]),
    (re.compile(r"lost\s*origin", re.I), ["S11", "S11A"]),
    (re.compile(r"vivid\s*voltage", re.I), ["S4", "S4A"]),
    (re.compile(r"chilling\s*reign", re.I), ["S5I", "S5A", "S5R"]),
    (re.compile(r"destined\s*rivals", re.I), ["SV9", "SV9A"]),
    (re.compile(r"surging\s*sparks", re.I), ["SV8", "SV8A"]),
    (re.compile(r"prismatic\s*evolutions", re.I), ["SV8A", "SV8"]),
    (re.compile(r"journey\s*together", re.I), ["SV9", "SV9A"]),
    (re.compile(r"temporal\s*forces", re.I), ["SV5", "SV5A"]),
    (re.compile(r"paldea\s*evolved", re.I), ["SV2", "SV2A"]),
    (re.compile(r"paldean\s*fates", re.I), ["SV4A", "SV4"]),
    (re.compile(r"obsidian\s*flames", re.I), ["SV3", "SV3A"]),
    (re.compile(r"paradox\s*rift", re.I), ["SV4", "SV4A"]),
    (re.compile(r"twilight\s*masquerade", re.I), ["SV6", "SV6A"]),
    (re.compile(r"shrouded\s*fable", re.I), ["SV6A"]),
    (re.compile(r"stellar\s*crown", re.I), ["SV7", "SV7A"]),
    (re.compile(r"mega\s*evolution", re.I), ["M1", "M1A", "M2", "M2A", "M3", "M4"]),
    (re.compile(r"phantasmal\s*flames", re.I), ["M2", "M2A"]),
    (re.compile(r"triplet\s*beat|tripletbeat", re.I), ["SV1A"]),
    (re.compile(r"eevee\s*heroes", re.I), ["S6A"]),
    (re.compile(r"vstar\s*universe", re.I), ["S12A"]),
    (re.compile(r"vmax\s*climax", re.I), ["S8B"]),
    (re.compile(r"25th\s*anniversary", re.I), ["S8A", "S8A-P", "S8AP"]),
    (re.compile(r"pokemon\s*go", re.I), ["S10B"]),
    (re.compile(r"celebrations", re.I), ["S8A", "S8A-P"]),
]


def load_env() -> None:
    env = ROOT / "data/runtime/config/backend.env"
    if not env.is_file():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())
    os.environ.setdefault("CARDZ_DB_HOST", "127.0.0.1")


def db():
    import pymysql

    load_env()
    return pymysql.connect(
        host=os.environ.get("CARDZ_DB_HOST", "127.0.0.1"),
        port=int(os.environ.get("CARDZ_DB_PORT", "3308")),
        user=os.environ["CARDZ_DB_USER"],
        password=os.environ["CARDZ_DB_PASSWORD"],
        database=os.environ["CARDZ_DB_NAME"],
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )


def norm_alnum(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (value or "").upper())


def name_tokens(value: str) -> set[str]:
    return {
        t
        for t in re.findall(r"[a-z0-9]+", (value or "").casefold())
        if len(t) > 2 and t not in STOP
    }


def collector_key_forms(raw: str) -> set[str]:
    """All lookup keys for a collector number — 單打 / 連打 / 空格 / 前導零."""
    out: set[str] = set()
    s = (raw or "").strip()
    if not s:
        return out

    out.add(norm_alnum(s))
    out.add(s.upper())
    out.add(s.replace(" ", "").upper())
    out.add(s.replace("-", "").upper())
    out.add(s.replace("/", "").upper())

    # split forms: OP01-016 / OP01 016 / ST10-010
    m = re.match(r"^([A-Za-z]{1,5})[-\s]?(\d{1,4})([A-Za-z]?)$", s)
    if m:
        prefix, num, suf = m.group(1).upper(), m.group(2), (m.group(3) or "").upper()
        for nform in {num, num.lstrip("0") or num, num.zfill(3), num.zfill(2)}:
            out.add(f"{prefix}{nform}{suf}")
            out.add(f"{prefix}-{nform}{suf}")
            out.add(f"{prefix} {nform}{suf}")
            out.add(f"{prefix}{nform}")
            out.add(norm_alnum(f"{prefix}-{nform}{suf}"))

    # TG23 / GG44 / SV107
    m2 = re.match(r"^([A-Za-z]{1,4})(\d{1,4})([A-Za-z]?)$", s)
    if m2:
        prefix, num, suf = m2.group(1).upper(), m2.group(2), (m2.group(3) or "").upper()
        for nform in {num, num.lstrip("0") or num, num.zfill(3)}:
            out.add(f"{prefix}{nform}{suf}")
            out.add(f"{prefix}-{nform}{suf}")
            out.add(f"{prefix} {nform}{suf}")

    # pure digits / fraction 173/165
    if "/" in s:
        left, _, right = s.partition("/")
        out.add(norm_alnum(left))
        out.add(norm_alnum(s))
        if left.strip().isdigit():
            n = left.strip()
            out.add(n)
            out.add(n.lstrip("0") or n)
            out.add(n.zfill(3))

    if s.isdigit() or re.fullmatch(r"0*\d+", s):
        n = s.lstrip("0") or s
        out.add(n)
        out.add(n.zfill(2))
        out.add(n.zfill(3))
        out.add(n.zfill(4))
        out.add(s)

    # trailing number after hyphen
    if "-" in s:
        tail = s.rsplit("-", 1)[-1]
        out |= collector_key_forms(tail) if tail != s else set()

    out.discard("")
    return {norm_alnum(x) if re.search(r"[A-Za-z]", x) else x for x in out if x}


def set_codes_from_text(*parts: str) -> set[str]:
    text = " ".join(p for p in parts if p)
    codes: set[str] = set()
    if not text:
        return codes

    for pat, mapped in SET_NAME_HINTS:
        if pat.search(text):
            codes.update(norm_alnum(c) for c in mapped)

    # OP-13 / OP13 / One Piece ... OP-05
    for m in re.finditer(r"\bOP[-\s]?(\d{1,2})\b", text, re.I):
        codes.add(f"OP{int(m.group(1)):02d}")
        codes.add(f"OP{m.group(1)}")
    for m in re.finditer(r"\bST[-\s]?(\d{1,2})\b", text, re.I):
        codes.add(f"ST{int(m.group(1)):02d}")
    for m in re.finditer(r"\bEB[-\s]?(\d{1,2})\b", text, re.I):
        codes.add(f"EB{int(m.group(1)):02d}")
    for m in re.finditer(r"\bPRB[-\s]?(\d{1,2})\b", text, re.I):
        codes.add(f"PRB{int(m.group(1)):02d}")

    # SV2a / s8b / M2a style tokens
    for m in re.finditer(r"\b([A-Z]{1,4}\d{1,2}[A-Z]?)\b", text, re.I):
        codes.add(norm_alnum(m.group(1)))

    codes.discard("")
    return codes


def composite_keys(set_codes: set[str], num_keys: set[str]) -> set[str]:
    """SET+NUM composites: SV2A173, OP01116, …"""
    out: set[str] = set()
    pure_nums = {k for k in num_keys if k.isdigit() or re.fullmatch(r"0*\d+", k)}
    for sc in set_codes:
        for nk in num_keys | pure_nums:
            if not nk:
                continue
            out.add(norm_alnum(f"{sc}{nk}"))
            # if nk already starts with set, skip double
    return out


def extract_keys_from_snk(name: str, product_number: str) -> set[str]:
    """Index keys for one SNK harvest row — 多格式."""
    out: set[str] = set()
    name = name or ""
    product_number = product_number or ""

    # [SV2a 173/165] spaced set + num
    for m in re.finditer(
        r"\[([^\]\s]+)\s+([0-9A-Za-z]{1,6}(?:/[0-9A-Za-z]{1,6})?)\]", name
    ):
        set_raw, num_raw = m.group(1), m.group(2)
        out |= collector_key_forms(num_raw)
        out |= collector_key_forms(f"{set_raw}-{num_raw.split('/')[0]}")
        out.add(norm_alnum(set_raw + num_raw.split("/")[0]))
        out.add(norm_alnum(set_raw))
        lead = num_raw.split("/", 1)[0]
        out.add(norm_alnum(f"{set_raw}{lead}"))

    # [OP07-051] / [ST10-010] single token in brackets
    for m in re.finditer(r"\[([A-Za-z]{1,5}\d{0,3}[- ]?\d{1,4}[A-Za-z]?)\]", name):
        out |= collector_key_forms(m.group(1))

    # product_number tails: pkmn-tcg-SV1a-080 / OP07-051 / OPC-TCG-...
    if product_number:
        out |= collector_key_forms(product_number)
        parts = product_number.split("-")
        if parts:
            out.add(norm_alnum(parts[-1]))
            if len(parts) >= 2:
                out.add(norm_alnum(parts[-2] + parts[-1]))
                out |= collector_key_forms(f"{parts[-2]}-{parts[-1]}")
            # pkmn-tcg-SV1a-080 → SV1A080
            if len(parts) >= 3:
                out.add(norm_alnum(parts[-2] + parts[-1]))

    # bare OP01-016 / TG23 in free text
    for m in re.finditer(
        r"\b([A-Z]{1,5}\d{1,3}[- ]?\d{0,4}[A-Z]?|\d{1,3}/\d{2,3}|\d{2,4})\b",
        name,
        re.I,
    ):
        out |= collector_key_forms(m.group(1))

    out.discard("")
    return out


def load_snk_index() -> tuple[dict[str, list[dict[str, Any]]], dict[int, dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_id: dict[int, dict[str, Any]] = {}
    if not HARVEST.is_file():
        raise SystemExit(f"missing harvest: {HARVEST}")
    with HARVEST.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            item_id = row.get("item_id")
            if not isinstance(item_id, int):
                continue
            name = str(row.get("name") or "")
            pn = str(row.get("product_number") or "")
            keys = extract_keys_from_snk(name, pn)
            set_codes = set_codes_from_text(name, pn)
            rec = {
                "item_id": item_id,
                "name": row.get("name"),
                "product_number": row.get("product_number"),
                "tokens": name_tokens(name),
                "set_codes": set_codes,
                "keys": keys,
                "psa10_min": None,
                "image_url": row.get("image_url"),
            }
            for chip in row.get("chips") or []:
                if isinstance(chip, dict) and chip.get("filter_condition_id") == "psa_10":
                    price = chip.get("used_min_price")
                    if isinstance(price, (int, float)) and price > 0:
                        rec["psa10_min"] = float(price)
            by_id[item_id] = rec
            for n in keys:
                index[n].append(rec)
            # also index pure composites already in keys
    return index, by_id


def load_watchlist(conn) -> list[dict[str, Any]]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT w.variant_id, w.gemrate_id, w.card_name, w.set_name, w.collector_number,
               w.psa10_population, v.opaque_id
        FROM market_gemrate_psa10_watchlist w
        JOIN catalog_variant v ON v.id = w.variant_id
        ORDER BY w.psa10_population DESC
        """
    )
    return list(cur.fetchall())


def already_bound(conn) -> dict[int, str]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT variant_id, external_entity_id FROM catalog_source_identity
        WHERE source_code IN ('snkrdunk','snk')
        """
    )
    out: dict[int, str] = {}
    for r in cur.fetchall():
        out[int(r["variant_id"])] = str(r["external_entity_id"])
    return out


def score_candidate(watch: dict[str, Any], cand: dict[str, Any], watch_sets: set[str]) -> int:
    wtokens = name_tokens(str(watch.get("card_name") or ""))
    score = 0
    hits = wtokens & cand["tokens"]
    score += 30 * len(hits)
    raw = re.findall(r"[a-z0-9]+", str(watch.get("card_name") or "").casefold())
    if raw and raw[0] in cand["tokens"]:
        score += 50
    # set code overlap is decisive for pure-number collectors
    cand_sets = cand.get("set_codes") or set()
    set_hits = watch_sets & cand_sets
    if set_hits:
        score += 80
    # weaker: set code appears in product_number / name alnum
    cand_blob = norm_alnum(str(cand.get("product_number") or "") + str(cand.get("name") or ""))
    for sc in watch_sets:
        if sc and sc in cand_blob:
            score += 40
            break
    if cand.get("psa10_min"):
        score += 5
    return score


def bind(*, write: bool, min_score: int = 50) -> dict[str, Any]:
    index, _by_id = load_snk_index()
    conn = db()
    watch = load_watchlist(conn)
    bound = already_bound(conn)
    results = []
    new_binds = []

    for w in watch:
        vid = int(w["variant_id"])
        collector_raw = str(w.get("collector_number") or "")
        set_name = str(w.get("set_name") or "")
        card_name = str(w.get("card_name") or "")

        num_keys = collector_key_forms(collector_raw)
        watch_sets = set_codes_from_text(set_name, collector_raw, card_name)
        # if collector already embeds set (OP01-016), extract
        watch_sets |= set_codes_from_text(collector_raw)
        keys = set(num_keys)
        keys |= composite_keys(watch_sets, num_keys)
        # spaced / hyphen display forms kept via collector_key_forms

        cands: list[dict[str, Any]] = []
        seen_ids: set[int] = set()
        for k in keys:
            if not k:
                continue
            # try both raw and norm_alnum
            for probe in {k, norm_alnum(k)}:
                for c in index.get(probe, []):
                    if c["item_id"] in seen_ids:
                        continue
                    seen_ids.add(c["item_id"])
                    sc = score_candidate(w, c, watch_sets)
                    cands.append({**c, "score": sc, "matchKey": probe})

        cands.sort(key=lambda r: (-r["score"], r["item_id"]))
        best = cands[0] if cands else None
        auto = best is not None and best["score"] >= min_score

        # pure digit collector without set hit → require stronger name evidence
        pure_digit = bool(re.fullmatch(r"0*\d+", collector_raw.strip()))
        if auto and pure_digit and best:
            set_ok = bool(watch_sets & (best.get("set_codes") or set()))
            blob = norm_alnum(str(best.get("product_number") or "") + str(best.get("name") or ""))
            set_in_blob = any(sc in blob for sc in watch_sets if sc)
            name_ok = best["score"] >= 80 and (
                bool(name_tokens(card_name) & best["tokens"])
            )
            if not set_ok and not set_in_blob:
                # no set confirmation → only auto if very strong name + score
                if not (name_ok and best["score"] >= 110):
                    auto = False

        if auto and len(cands) > 1 and cands[1]["item_id"] != best["item_id"]:
            if cands[1]["score"] >= best["score"] and cands[1]["score"] >= min_score:
                auto = False
            elif cands[1]["score"] >= best["score"] - 5 and cands[1]["score"] >= 80:
                auto = False

        cand_public = None
        if best:
            cand_public = {
                "item_id": best["item_id"],
                "name": best.get("name"),
                "product_number": best.get("product_number"),
                "score": best.get("score"),
                "matchKey": best.get("matchKey"),
                "psa10_min": best.get("psa10_min"),
                "image_url": best.get("image_url"),
            }
        row = {
            "variantId": vid,
            "opaqueId": w.get("opaque_id"),
            "name": w.get("card_name"),
            "collectorNumber": w.get("collector_number"),
            "setName": set_name,
            "watchSets": sorted(watch_sets),
            "keyCount": len(keys),
            "alreadyBound": vid in bound,
            "snkItemId": best["item_id"] if auto else (int(bound[vid]) if vid in bound and str(bound[vid]).isdigit() else None),
            "score": best["score"] if best else 0,
            "candidate": cand_public,
            "needsReview": not auto and best is not None and vid not in bound,
            "noCandidate": best is None and vid not in bound,
        }
        # if already bound keep id
        if vid in bound and not row["snkItemId"]:
            try:
                row["snkItemId"] = int(bound[vid])
            except (TypeError, ValueError):
                row["snkItemId"] = bound[vid]
        results.append(row)
        if auto and vid not in bound:
            new_binds.append(row)

    written = 0
    if write and new_binds:
        cur = conn.cursor()
        for row in new_binds:
            item_id = str(row["snkItemId"])
            evidence = hashlib.sha256(f"snkrdunk:{item_id}:{row['variantId']}".encode()).hexdigest()
            cur.execute(
                """
                INSERT INTO catalog_source_identity
                    (source_code, external_entity_id, variant_id, match_status, evidence_sha256)
                VALUES ('snkrdunk', %s, %s, 'exact', %s)
                ON DUPLICATE KEY UPDATE
                    variant_id=VALUES(variant_id),
                    match_status='exact',
                    evidence_sha256=VALUES(evidence_sha256),
                    updated_at=CURRENT_TIMESTAMP
                """,
                (item_id, row["variantId"], evidence),
            )
            written += 1
        conn.commit()

    id_set: set[int] = set()
    for row in results:
        if row.get("snkItemId"):
            try:
                id_set.add(int(row["snkItemId"]))
            except (TypeError, ValueError):
                pass
    cur = conn.cursor()
    cur.execute(
        """
        SELECT external_entity_id FROM catalog_source_identity
        WHERE source_code IN ('snkrdunk','snk')
          AND variant_id IN (SELECT variant_id FROM market_gemrate_psa10_watchlist)
        """
    )
    for r in cur.fetchall():
        try:
            id_set.add(int(r["external_entity_id"]))
        except (TypeError, ValueError):
            continue
    conn.close()

    MAP.mkdir(parents=True, exist_ok=True)
    IDS_OUT.write_text(
        "\n".join(str(i) for i in sorted(id_set)) + ("\n" if id_set else ""),
        encoding="utf-8",
    )
    summary = {
        "watch": len(watch),
        "alreadyBound": sum(1 for r in results if r["alreadyBound"]),
        "autoNew": len(new_binds),
        "autoTotalWithId": sum(1 for r in results if r.get("snkItemId")),
        "needsReview": sum(1 for r in results if r.get("needsReview")),
        "noCandidate": sum(1 for r in results if r.get("noCandidate")),
        "writtenIdentities": written,
        "snkIdsFile": str(IDS_OUT),
        "uniqueSnkIds": len(id_set),
        "write": write,
        "indexKeys": len(index),
    }
    REPORT.write_text(
        json.dumps({"summary": summary, "results": results}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, sort_keys=True))
    return summary


def ingest_harvest_prices(*, dry_run: bool = False) -> dict[str, Any]:
    """Write latest SNK PSA10 chip prices from harvest into market_price_observation."""

    load_env()
    psa: dict[int, float] = {}
    with HARVEST.open(encoding="utf-8") as fh:
        for line in fh:
            row = json.loads(line)
            item_id = row.get("item_id")
            if not isinstance(item_id, int):
                continue
            for chip in row.get("chips") or []:
                if isinstance(chip, dict) and chip.get("filter_condition_id") == "psa_10":
                    price = chip.get("used_min_price")
                    if isinstance(price, (int, float)) and price > 0:
                        psa[item_id] = float(price)

    conn = db()
    cur = conn.cursor()
    jpy_per_usd = 150.0
    cur.execute(
        """
        SELECT rate FROM market_fx_rate_observation
        WHERE base_currency='USD' AND quote_currency='JPY'
        ORDER BY effective_date DESC, id DESC
        LIMIT 1
        """
    )
    fx_row = cur.fetchone()
    if fx_row and fx_row.get("rate") is not None:
        jpy_per_usd = float(fx_row["rate"])
    print(json.dumps({"fx_jpy_per_usd": jpy_per_usd}, sort_keys=True))

    cur.execute(
        """
        SELECT s.variant_id, s.external_entity_id
        FROM catalog_source_identity s
        JOIN market_gemrate_psa10_watchlist w ON w.variant_id = s.variant_id
        WHERE s.source_code IN ('snkrdunk','snk')
        """
    )
    pairs = []
    for r in cur.fetchall():
        try:
            item_id = int(r["external_entity_id"])
        except (TypeError, ValueError):
            continue
        if item_id in psa:
            pairs.append((int(r["variant_id"]), item_id, psa[item_id]))

    if dry_run:
        print(json.dumps({"dryRun": True, "pairs": len(pairs)}, sort_keys=True))
        conn.close()
        return {"pairs": len(pairs)}

    effective = datetime.now(timezone.utc).replace(tzinfo=None)
    today = date.today().isoformat()
    run_key = f"snk_harvest_chip_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    seed = hashlib.sha256(run_key.encode()).hexdigest()
    cur.execute(
        """
        INSERT INTO market_ingest_run
            (run_key, source_code, ingest_mode, effective_at, payload_sha256, manifest_sha256,
             status, observed_count, accepted_count, quarantined_count, rejected_count, started_at)
        VALUES (%s, 'snk_psa10', 'incremental', %s, %s, %s, 'running', 0, 0, 0, 0, %s)
        """,
        (run_key, effective, seed, seed, effective),
    )
    run_id = cur.lastrowid
    written = 0
    for variant_id, item_id, price_jpy in pairs:
        price_usd = round(price_jpy / jpy_per_usd, 6)
        payload = {"source": "snk_harvest_chip", "itemId": item_id, "priceJpy": price_jpy, "priceUsd": price_usd}
        ph = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        cur.execute(
            """
            INSERT INTO market_price_observation
                (run_id, variant_id, source_code, observed_date, effective_at, price_usd,
                 native_price, native_currency, source_priority, metric_status, payload_sha256)
            VALUES (%s, %s, 'snk_psa10', %s, %s, %s, %s, 'JPY', 50, 'ready', %s)
            ON DUPLICATE KEY UPDATE
                run_id=VALUES(run_id), effective_at=VALUES(effective_at), price_usd=VALUES(price_usd),
                native_price=VALUES(native_price), native_currency=VALUES(native_currency),
                source_priority=VALUES(source_priority), metric_status=VALUES(metric_status),
                payload_sha256=VALUES(payload_sha256)
            """,
            (run_id, variant_id, today, effective, price_usd, price_jpy, ph),
        )
        written += 1
    cur.execute(
        """
        UPDATE market_ingest_run
        SET status='complete', observed_count=%s, accepted_count=%s, completed_at=%s
        WHERE id=%s
        """,
        (written, written, effective, run_id),
    )
    conn.commit()
    conn.close()
    summary = {"runId": run_id, "priceRows": written, "pairs": len(pairs), "jpyPerUsd": jpy_per_usd}
    print(json.dumps(summary, sort_keys=True))
    return summary


def accept_high_review(*, write: bool, min_score: int = 200) -> dict[str, Any]:
    """Semi-auto: accept needsReview rows with high score + first-name match.

    Intended for score≥200 OP exact / set-confirmed candidates left in review
    due to near-ties or pure-digit caution.
    """

    report_path = REPORT
    if not report_path.is_file():
        # rebuild dry report first
        bind(write=False, min_score=50)
    data = json.loads(report_path.read_text(encoding="utf-8"))
    results = data.get("results") or []
    conn = db()
    bound = already_bound(conn)
    accepted: list[dict[str, Any]] = []
    for row in results:
        vid = int(row["variantId"])
        if vid in bound:
            continue
        if not row.get("needsReview"):
            continue
        score = int(row.get("score") or 0)
        if score < min_score:
            continue
        cand = row.get("candidate") or {}
        item_id = cand.get("item_id")
        if not item_id:
            continue
        wname = str(row.get("name") or "")
        cname = str(cand.get("name") or "")
        first = next(
            (t for t in re.findall(r"[a-z0-9]+", wname.casefold()) if len(t) > 2 and t not in STOP),
            None,
        )
        if not first or first not in cname.casefold():
            continue
        # collector must appear in candidate (blocks 127 Sharpedo → Rayquaza 127)
        collector = str(row.get("collectorNumber") or "")
        ckeys = collector_key_forms(collector)
        cand_blob = norm_alnum(cname + str(cand.get("product_number") or ""))
        col_ok = any(
            (k and (k in cand_blob or k in norm_alnum(cname)))
            for k in ckeys
            if len(k) >= 2
        )
        if not col_ok:
            continue
        # pure digits: require set hint in candidate too
        if re.fullmatch(r"0*\d+", collector.strip() or ""):
            wsets = set(row.get("watchSets") or []) or set_codes_from_text(
                str(row.get("setName") or ""), collector, wname
            )
            if wsets and not any(sc in cand_blob for sc in wsets if len(sc) >= 3):
                continue
        accepted.append(
            {
                "variantId": vid,
                "snkItemId": int(item_id),
                "score": score,
                "name": wname,
                "candidate": cname[:80],
            }
        )

    written = 0
    if write and accepted:
        cur = conn.cursor()
        for row in accepted:
            item_id = str(row["snkItemId"])
            evidence = hashlib.sha256(
                f"snkrdunk:{item_id}:{row['variantId']}:accept_high".encode()
            ).hexdigest()
            cur.execute(
                """
                INSERT INTO catalog_source_identity
                    (source_code, external_entity_id, variant_id, match_status, evidence_sha256)
                VALUES ('snkrdunk', %s, %s, 'exact', %s)
                ON DUPLICATE KEY UPDATE
                    variant_id=VALUES(variant_id),
                    match_status='exact',
                    evidence_sha256=VALUES(evidence_sha256),
                    updated_at=CURRENT_TIMESTAMP
                """,
                (item_id, row["variantId"], evidence),
            )
            written += 1
        conn.commit()
        # refresh ids file
        bind(write=False, min_score=50)

    conn.close()
    summary = {
        "minScore": min_score,
        "accepted": len(accepted),
        "writtenIdentities": written,
        "write": write,
        "sample": accepted[:20],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return summary


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_bind = sub.add_parser("bind")
    p_bind.add_argument("--write", action="store_true")
    p_bind.add_argument("--min-score", type=int, default=50)
    p_acc = sub.add_parser("accept-high")
    p_acc.add_argument("--write", action="store_true")
    p_acc.add_argument("--min-score", type=int, default=200)
    p_ing = sub.add_parser("ingest-harvest-prices")
    p_ing.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if args.cmd == "bind":
        bind(write=args.write, min_score=args.min_score)
    elif args.cmd == "accept-high":
        accept_high_review(write=args.write, min_score=args.min_score)
    elif args.cmd == "ingest-harvest-prices":
        ingest_harvest_prices(dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
