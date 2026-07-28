#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""半自動身份導航：清錯 + 低門檻召回 + 二次 check + 記 registry。

原則（用戶 2026-07-29 · 見 docs/RECALL_VERIFY_OPS.md）:
  - 明顯錯一定清
  - Recall 門檻可以極低（海量撈）；入 DB 前必須腳本 Verify
  - QC 主體係腳本；AI agent 只編排／殘渣
  - 通過先 mark DB（只做一次；之後增量跟 registry）

命令:
  python -X utf8 pipelines/semi_auto_identity.py clean --write
  python -X utf8 pipelines/semi_auto_identity.py match-snk --write
  python -X utf8 pipelines/semi_auto_identity.py match-ebay --write
  python -X utf8 pipelines/semi_auto_identity.py run --write
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MAP = ROOT / "data/runtime/private-source-map"
HARVEST = ROOT / "data/private/snkrdunk_brute/snkrdunk_all.jsonl"
REGISTRY = MAP / "liquidity-source-registry.jsonl"
IDENTITY_LEDGER = MAP / "semi-auto-identity-ledger.jsonl"
G10_DEFAULT = ROOT.parent / "grade10-scraper" / "data" / "cards"
REPORT_DIR = MAP / "qualified-pool-reports"

STOP = {
    "the", "and", "with", "ex", "gx", "vmax", "vstar", "card", "pokemon", "one", "piece",
    "full", "art", "alternate", "illustration", "secret", "rare", "ultra", "holo",
    "special", "promo", "edition", "booster", "pack", "japanese", "english", "premium",
    "anniversary", "collection", "game", "mirror", "monster", "ball", "master",
    "sar", "sr", "rr", "chr", "ur", "ar", "sec", "l", "leader",
    "mega",  # too common prefix — require species token after
}


def load_env() -> None:
    env = ROOT / "data/runtime/config/backend.env"
    if not env.is_file():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().replace("\r", ""))
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
        autocommit=False,
    )


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def norm_alnum(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (value or "").upper())


def tokens(value: str) -> set[str]:
    return {
        t
        for t in re.findall(r"[a-z0-9]+", (value or "").casefold())
        if len(t) > 2 and t not in STOP
    }


def species_token(name: str) -> str | None:
    """First real species/character token (skip rarity noise)."""
    for t in re.findall(r"[a-z0-9]+", (name or "").casefold()):
        if len(t) > 2 and t not in STOP:
            return t
    return None


def collector_forms(raw: str) -> set[str]:
    out: set[str] = set()
    s = (raw or "").strip()
    if not s:
        return out
    out.add(norm_alnum(s))
    m = re.match(r"^([A-Za-z]{1,5})[-\s]?(\d{1,4})([A-Za-z]?)$", s)
    if m:
        p, n, suf = m.group(1).upper(), m.group(2), (m.group(3) or "").upper()
        for nf in {n, n.lstrip("0") or n, n.zfill(3)}:
            out.add(f"{p}{nf}{suf}")
            out.add(norm_alnum(f"{p}-{nf}{suf}"))
    if s.isdigit() or re.fullmatch(r"0*\d+", s):
        n = s.lstrip("0") or s
        out.add(n)
        out.add(n.zfill(3))
    out.discard("")
    return out


def extract_snk_keys(name: str, product_number: str) -> set[str]:
    out: set[str] = set()
    name = name or ""
    pn = product_number or ""
    for m in re.finditer(r"\[([^\]\s]+)\s+([0-9A-Za-z]{1,6}(?:/[0-9A-Za-z]{1,6})?)\]", name):
        out |= collector_forms(m.group(2).split("/")[0])
        out.add(norm_alnum(m.group(1) + m.group(2).split("/")[0]))
    for m in re.finditer(r"\[([A-Za-z]{1,5}\d{0,3}[- ]?\d{1,4}[A-Za-z]?)\]", name):
        out |= collector_forms(m.group(1))
    if pn:
        out |= collector_forms(pn)
        parts = pn.split("-")
        if len(parts) >= 2:
            out.add(norm_alnum(parts[-1]))
            out.add(norm_alnum(parts[-2] + parts[-1]))
    return {x for x in out if x}


def ledger_append(rows: list[dict[str, Any]]) -> None:
    IDENTITY_LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with IDENTITY_LEDGER.open("a", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")


def load_registry() -> dict[int, dict[str, Any]]:
    by: dict[int, dict[str, Any]] = {}
    if not REGISTRY.is_file():
        return by
    for line in REGISTRY.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            by[int(r["variantId"])] = r
    return by


def save_registry(by: dict[int, dict[str, Any]]) -> None:
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    with REGISTRY.open("w", encoding="utf-8") as fh:
        for vid in sorted(by):
            fh.write(json.dumps(by[vid], ensure_ascii=False, default=str) + "\n")


def upsert_identity(cur, source_code: str, external_id: str, variant_id: int, tag: str) -> None:
    evidence = hashlib.sha256(
        f"{source_code}:{external_id}:{variant_id}:{tag}".encode()
    ).hexdigest()
    cur.execute(
        """
        INSERT INTO catalog_source_identity
            (source_code, external_entity_id, variant_id, match_status, evidence_sha256)
        VALUES (%s, %s, %s, 'exact', %s)
        ON DUPLICATE KEY UPDATE
            variant_id=VALUES(variant_id),
            match_status='exact',
            evidence_sha256=VALUES(evidence_sha256),
            updated_at=CURRENT_TIMESTAMP
        """,
        (source_code, str(external_id), variant_id, evidence),
    )


def load_harvest_index() -> tuple[dict[int, dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    by_id: dict[int, dict[str, Any]] = {}
    index: dict[str, list[dict[str, Any]]] = defaultdict(list)
    if not HARVEST.is_file():
        return by_id, index
    with HARVEST.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            item_id = row.get("item_id")
            if not isinstance(item_id, int):
                continue
            if row.get("error"):
                continue
            name = str(row.get("name") or "")
            pn = str(row.get("product_number") or "")
            rec = {
                "item_id": item_id,
                "name": name,
                "product_number": pn,
                "tokens": tokens(name),
                "species": species_token(name),
                "keys": extract_snk_keys(name, pn),
            }
            by_id[item_id] = rec
            for k in rec["keys"]:
                index[k].append(rec)
    return by_id, index


def watchlist(cur) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT w.variant_id, w.gemrate_id, w.card_name, w.set_name, w.collector_number,
               w.psa10_population
        FROM market_gemrate_psa10_watchlist w
        ORDER BY w.psa10_population DESC
        """
    )
    return list(cur.fetchall())


def species_hit(sp: str, cand_name: str) -> bool:
    """Token-only species match — 'mew' must NOT hit 'mewtwo'."""
    if not sp:
        return False
    return sp in tokens(cand_name)


def op_name_hit(watch_name: str, cand_name: str) -> bool:
    """One Piece: require all major name tokens (monkey + luffy)."""
    wt = tokens(watch_name)
    ct = tokens(cand_name)
    if not wt:
        return False
    # if watch has 2+ tokens, require ≥2 hit or all if only 2
    hits = wt & ct
    if len(wt) >= 2:
        return len(hits) >= min(2, len(wt))
    return bool(hits)


def set_hints(set_name: str, collector: str, card_name: str) -> set[str]:
    """Light set codes from set_name / collector for pure-digit safety."""
    text = f"{set_name or ''} {collector or ''} {card_name or ''}"
    codes: set[str] = set()
    for m in re.finditer(r"\bOP[-\s]?(\d{1,2})\b", text, re.I):
        codes.add(f"OP{int(m.group(1)):02d}")
    for m in re.finditer(r"\bST[-\s]?(\d{1,2})\b", text, re.I):
        codes.add(f"ST{int(m.group(1)):02d}")
    for m in re.finditer(r"\b([A-Z]{1,4}\d{1,2}[A-Z]?)\b", text, re.I):
        codes.add(norm_alnum(m.group(1)))
    aliases = [
        (r"\b151\b", ["SV2A"]),
        (r"terastal\s*festival", ["SV8A"]),
        (r"prismatic\s*evolutions", ["SV8A", "SV8"]),
        (r"black\s*flame|ruler of the black", ["SV3"]),
        (r"blue\s*sky\s*stream", ["S7R"]),
        (r"vstar\s*universe", ["S12A"]),
        (r"mega\s*dream|phantasmal", ["M2A", "M2"]),
        (r"inferno", ["M2"]),
        (r"twilight\s*masquerade", ["SV6", "SV6A"]),
        (r"crown\s*zenith", ["S12A"]),
        (r"evolving\s*skies", ["S6A", "S7R"]),
        (r"celebrations", ["S8A", "S8AP", "S8A-P"]),
        (r"pokemon\s*go", ["S10B"]),
        (r"paldea\s*evolved", ["SV2", "SV2A"]),
        (r"obsidian\s*flames", ["SV3", "SV3A"]),
        (r"temporal\s*forces", ["SV5", "SV5A"]),
        (r"stellar\s*crown", ["SV7", "SV7A"]),
        (r"surging\s*sparks", ["SV8", "SV8A"]),
        (r"destined\s*rivals|journey\s*together", ["SV9", "SV9A"]),
        (r"paldean\s*fates", ["SV4A", "SV4"]),
        (r"paradox\s*rift", ["SV4", "SV4A"]),
        (r"shrouded\s*fable", ["SV6A"]),
        (r"brilliant\s*stars", ["S9", "S9A"]),
        (r"silver\s*tempest", ["S12", "S12A"]),
        (r"lost\s*origin", ["S11", "S11A"]),
        (r"chilling\s*reign", ["S5I", "S5A", "S5R"]),
        (r"vivid\s*voltage", ["S4", "S4A"]),
        (r"fusion\s*strike", ["S8", "S8B"]),
        (r"battle\s*styles", ["S5R", "S5I"]),
        (r"astral\s*radiance", ["S10", "S10A"]),
        (r"sword\s*and\s*shield:\s*base|sword\s*&\s*shield\s*base", ["S1H", "S1W", "S1A"]),
        (r"one\s*piece|op-\d", []),  # OP codes extracted above
    ]
    for pat, vals in aliases:
        if re.search(pat, text, re.I):
            codes.update(vals)
    codes.discard("")
    return codes


def score_recall(
    watch_name: str,
    watch_col: str,
    cand_name: str,
    cand_keys: set[str],
    *,
    product_number: str = "",
) -> tuple[int, str]:
    """Low-threshold recall: collector hit + any species/name signal.

    Used only to *find* candidates; must pass verify_pair before DB write.
    """
    forms = collector_forms(watch_col)
    blob = norm_alnum((cand_name or "") + " " + (product_number or ""))
    col_hit = any(f and (f in blob or f in cand_keys) for f in forms if len(f) >= 2)
    if not col_hit and forms:
        # allow pure short digit at recall only
        col_hit = any(f and f in blob for f in forms if f.isdigit())
    if not col_hit:
        return 0, "recall_no_collector"

    sp = species_token(watch_name)
    ct = tokens(cand_name)
    name_hit = bool(sp and sp in ct) or bool(tokens(watch_name) & ct)
    if not name_hit:
        # OP codes can recall on collector alone
        if re.search(r"[A-Za-z]{2,}\d", watch_col or ""):
            return 40, "recall_collector_only_op"
        return 0, "recall_no_name"

    score = 50
    if sp and sp in ct:
        score += 20
    score += min(20, 5 * len(tokens(watch_name) & ct))
    if re.search(r"[A-Za-z]{2,}\d", watch_col or "") and norm_alnum(watch_col) in blob:
        score += 30
    return score, "recall_ok"


def verify_pair(
    watch_name: str,
    watch_col: str,
    cand_name: str,
    cand_keys: set[str],
    *,
    watch_set: str = "",
    product_number: str = "",
) -> tuple[bool, str, int]:
    """Second pass — hard gate. Only True gets written to DB."""
    sp = species_token(watch_name)
    cname = cand_name or ""
    blob = norm_alnum(cname + " " + (product_number or ""))
    forms = collector_forms(watch_col)

    # collector hard
    col_hit = any(f and (f in blob or f in cand_keys) for f in forms if len(f) >= 2)
    if not col_hit:
        col_hit = bool(forms & {norm_alnum(k) for k in cand_keys if len(norm_alnum(k)) >= 2})
    if not col_hit:
        return False, "verify_collector", 0

    # name hard
    is_op = bool(re.search(r"one\s*piece|\bOP-?\d", watch_set or "", re.I)) or bool(
        re.match(r"^(OP|ST|EB|PRB)", (watch_col or "").upper())
    )
    if is_op:
        if not op_name_hit(watch_name, cname):
            return False, "verify_op_name", 0
    else:
        if not sp or not species_hit(sp, cname):
            return False, "verify_species", 0

    pure_digit = bool(re.fullmatch(r"0*\d+", (watch_col or "").strip()))
    if pure_digit:
        if len((watch_col or "").lstrip("0") or "0") <= 1:
            return False, "verify_col_too_short", 0
        hints = set_hints(watch_set, watch_col, watch_name)
        if not hints:
            return False, "verify_no_set_hint", 0
        if not any(h in blob for h in hints if len(h) >= 2):
            return False, "verify_set", 0

    wn = (watch_name or "").casefold()
    cn = cname.casefold()
    for marker in ("vmax", "vstar"):
        if marker in wn and marker not in cn:
            return False, "verify_rarity", 0
    if re.search(r"\bgx\b", wn) and not re.search(r"\bgx\b", cn):
        return False, "verify_rarity", 0

    # reject obvious non-card product lines
    if re.search(r"\b(sleeve|playmat|box|deck|booster pack)\b", cn) and "ex" not in cn:
        if "card game" in cn and "[" not in cname:
            return False, "verify_not_single", 0

    score = 100
    if re.search(r"[A-Za-z]{2,}\d", watch_col or "") and norm_alnum(watch_col) in blob:
        score += 50
    extra = len(tokens(watch_name) & tokens(cname))
    score += min(40, 10 * max(0, extra - 1))
    return True, "verify_ok", score


def score_pair(
    watch_name: str,
    watch_col: str,
    cand_name: str,
    cand_keys: set[str],
    *,
    watch_set: str = "",
    product_number: str = "",
) -> tuple[int, str]:
    """Legacy single-score: verify-only (for clean())."""
    ok, reason, score = verify_pair(
        watch_name,
        watch_col,
        cand_name,
        cand_keys,
        watch_set=watch_set,
        product_number=product_number,
    )
    return (score if ok else 0), reason


def cmd_clean(*, write: bool) -> dict[str, Any]:
    """Delete snkrdunk/ebay identities that fail species+collector gate."""
    by_id, _ = load_harvest_index()
    conn = db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT s.source_code, s.external_entity_id, s.variant_id,
               w.card_name, w.collector_number, w.set_name
        FROM catalog_source_identity s
        JOIN market_gemrate_psa10_watchlist w ON w.variant_id = s.variant_id
        WHERE s.source_code IN ('snkrdunk', 'snk')
        """
    )
    rows = list(cur.fetchall())
    bad: list[dict[str, Any]] = []
    good = 0
    for r in rows:
        try:
            item_id = int(r["external_entity_id"])
        except (TypeError, ValueError):
            bad.append({**r, "reason": "non_int_id"})
            continue
        h = by_id.get(item_id)
        if not h:
            # keep if harvest missing (don't mass-delete G10-only snk ids without proof)
            good += 1
            continue
        sc, reason = score_pair(
            str(r["card_name"] or ""),
            str(r["collector_number"] or ""),
            h["name"],
            h["keys"],
            watch_set=str(r.get("set_name") or ""),
            product_number=str(h.get("product_number") or ""),
        )
        if sc < 100:
            bad.append(
                {
                    "variantId": int(r["variant_id"]),
                    "source": r["source_code"],
                    "externalId": str(r["external_entity_id"]),
                    "watchName": r["card_name"],
                    "collector": r["collector_number"],
                    "candName": h["name"][:80],
                    "reason": reason,
                    "score": sc,
                }
            )
        else:
            good += 1

    deleted = 0
    if write and bad:
        for b in bad:
            cur.execute(
                """
                DELETE FROM catalog_source_identity
                WHERE source_code=%s AND external_entity_id=%s AND variant_id=%s
                """,
                (b["source"], b["externalId"], b["variantId"]),
            )
            deleted += int(cur.rowcount or 0)
        conn.commit()
        ledger_append(
            [
                {
                    "action": "clean_delete",
                    "at": utc_now(),
                    **b,
                    "script": "pipelines/semi_auto_identity.py#clean",
                }
                for b in bad
            ]
        )

    conn.close()
    summary = {
        "write": write,
        "checked": len(rows),
        "good": good,
        "bad": len(bad),
        "deleted": deleted,
        "sampleBad": bad[:25],
    }
    _write_report("clean", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return summary


def cmd_match_snk(*, write: bool, min_score: int = 50, recall_min: int = 40) -> dict[str, Any]:
    """Recall low → verify hard → only verified marks DB.

    min_score kept for CLI compat (verify uses fixed gate; recall_min controls haul size).
    """
    by_id, index = load_harvest_index()
    conn = db()
    cur = conn.cursor()
    watch = watchlist(cur)
    cur.execute(
        """
        SELECT variant_id, external_entity_id FROM catalog_source_identity
        WHERE source_code IN ('snkrdunk', 'snk')
        """
    )
    bound = {int(r["variant_id"]): str(r["external_entity_id"]) for r in cur.fetchall()}

    accepted: list[dict[str, Any]] = []
    rejected_verify: list[dict[str, Any]] = []
    recall_count = 0
    for w in watch:
        vid = int(w["variant_id"])
        if vid in bound:
            continue
        col = str(w.get("collector_number") or "")
        name = str(w.get("card_name") or "")
        set_name = str(w.get("set_name") or "")
        forms = collector_forms(col)
        # --- stage 1: low-threshold recall ---
        recalled: list[dict[str, Any]] = []
        seen: set[int] = set()
        for f in forms:
            for rec in index.get(f, []) + index.get(norm_alnum(f), []):
                if rec["item_id"] in seen:
                    continue
                seen.add(rec["item_id"])
                sc, reason = score_recall(
                    name,
                    col,
                    rec["name"],
                    rec["keys"],
                    product_number=str(rec.get("product_number") or ""),
                )
                if sc >= recall_min:
                    recalled.append({**rec, "recallScore": sc, "recallReason": reason})
        if not recalled:
            continue
        recall_count += 1
        recalled.sort(key=lambda r: (-r["recallScore"], r["item_id"]))

        # --- stage 2: verify each (best first); take first unique verified ---
        verified: list[dict[str, Any]] = []
        for rec in recalled[:12]:
            ok, vreason, vscore = verify_pair(
                name,
                col,
                rec["name"],
                rec["keys"],
                watch_set=set_name,
                product_number=str(rec.get("product_number") or ""),
            )
            if ok:
                verified.append({**rec, "score": vscore, "verifyReason": vreason})
            else:
                rejected_verify.append(
                    {
                        "variantId": vid,
                        "watchName": name,
                        "collector": col,
                        "candName": (rec.get("name") or "")[:80],
                        "snkItemId": rec["item_id"],
                        "recallScore": rec["recallScore"],
                        "reject": vreason,
                    }
                )
        if not verified:
            continue
        verified.sort(key=lambda r: (-r["score"], r["item_id"]))
        best = verified[0]
        if len(verified) > 1 and verified[1]["item_id"] != best["item_id"]:
            if verified[1]["score"] >= best["score"] - 10:
                rejected_verify.append(
                    {
                        "variantId": vid,
                        "watchName": name,
                        "collector": col,
                        "reject": "verify_ambiguous",
                        "a": best["item_id"],
                        "b": verified[1]["item_id"],
                    }
                )
                continue
        accepted.append(
            {
                "variantId": vid,
                "snkItemId": best["item_id"],
                "score": best["score"],
                "recallScore": best.get("recallScore"),
                "watchName": name,
                "collector": col,
                "setName": set_name,
                "candName": best["name"][:100],
                "productNumber": best.get("product_number"),
                "pipeline": "recall→verify",
            }
        )

    written = 0
    reg = load_registry()
    if write and accepted:
        for a in accepted:
            upsert_identity(cur, "snkrdunk", str(a["snkItemId"]), a["variantId"], "semi_auto_snk")
            written += 1
            reg[a["variantId"]] = {
                **(reg.get(a["variantId"]) or {}),
                "variantId": a["variantId"],
                "preferredLiquiditySource": "snkrdunk",
                "snkItemId": a["snkItemId"],
                "script": "pipelines/semi_auto_identity.py#match-snk",
                "matchScore": a["score"],
                "updatedAt": utc_now(),
            }
        conn.commit()
        save_registry(reg)
        ledger_append(
            [
                {
                    "action": "match_snk",
                    "at": utc_now(),
                    **a,
                    "script": "pipelines/semi_auto_identity.py#match-snk",
                }
                for a in accepted
            ]
        )
        # refresh ids file
        cur.execute(
            """
            SELECT external_entity_id FROM catalog_source_identity
            WHERE source_code IN ('snkrdunk','snk')
              AND variant_id IN (SELECT variant_id FROM market_gemrate_psa10_watchlist)
            """
        )
        ids = sorted(
            {
                int(r["external_entity_id"])
                for r in cur.fetchall()
                if str(r["external_entity_id"]).isdigit()
            }
        )
        ids_path = MAP / "qualified-940-snk-ids.txt"
        ids_path.write_text("\n".join(map(str, ids)) + ("\n" if ids else ""), encoding="utf-8")

    conn.close()
    summary = {
        "write": write,
        "harvestCards": len(by_id),
        "cardsWithRecall": recall_count,
        "recallRejectedAfterVerify": len(rejected_verify),
        "accepted": len(accepted),
        "written": written,
        "sampleAccepted": accepted[:20],
        "sampleRejected": rejected_verify[:15],
        "note": "Low recall → hard verify → only verified marked in DB/ledger (one-time)",
    }
    _write_report("match_snk", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return summary


def _g10_altxyz_assets(g10_root: Path) -> list[dict[str, Any]]:
    """Load altxyz dirs with name/collector/gemrate from asset_info."""
    alt = g10_root / "altxyz"
    out: list[dict[str, Any]] = []
    if not alt.is_dir():
        return out
    gem_re = re.compile(r"gemrate_id=([0-9a-fA-F]{40})")
    for d in alt.iterdir():
        if not d.is_dir():
            continue
        info: dict[str, Any] = {"externalId": d.name, "dir": str(d)}
        for fname in ("asset_info.json", "meta.json", "populations.json"):
            p = d / fname
            if not p.is_file():
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            m = gem_re.search(text)
            if m:
                info["gemrateId"] = m.group(1).lower()
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                continue
            if not isinstance(data, dict):
                continue
            for k_src, k_dst in (
                ("cardName", "cardName"),
                ("name", "cardName"),
                ("card_name", "cardName"),
                ("setName", "setName"),
                ("set_name", "setName"),
                ("cardId", "collectorNumber"),
                ("collectorNumber", "collectorNumber"),
                ("collector_number", "collectorNumber"),
            ):
                if k_src in data and data[k_src] and k_dst not in info:
                    info[k_dst] = str(data[k_src])
            # nested
            for nest in ("asset", "card", "info"):
                sub = data.get(nest)
                if isinstance(sub, dict):
                    for k_src, k_dst in (
                        ("cardName", "cardName"),
                        ("name", "cardName"),
                        ("cardId", "collectorNumber"),
                        ("collectorNumber", "collectorNumber"),
                    ):
                        if k_src in sub and sub[k_src] and k_dst not in info:
                            info[k_dst] = str(sub[k_src])
        if info.get("cardName") or info.get("collectorNumber") or info.get("gemrateId"):
            out.append(info)
    return out


def cmd_match_ebay(*, write: bool, g10_root: Path | None = None, min_score: int = 100) -> dict[str, Any]:
    """Semi-auto eBay via G10 altxyz: gemrate first, else collector+name."""
    g10_root = g10_root or G10_DEFAULT
    assets = _g10_altxyz_assets(g10_root)
    conn = db()
    cur = conn.cursor()
    watch = watchlist(cur)
    by_gem = {(w.get("gemrate_id") or "").lower(): w for w in watch if w.get("gemrate_id")}
    # index watch by collector forms
    by_col: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for w in watch:
        for f in collector_forms(str(w.get("collector_number") or "")):
            by_col[f].append(w)

    cur.execute(
        "SELECT external_entity_id, variant_id FROM catalog_source_identity WHERE source_code='ebay'"
    )
    existing = {str(r["external_entity_id"]): int(r["variant_id"]) for r in cur.fetchall()}

    accepted: list[dict[str, Any]] = []
    for a in assets:
        ext = a["externalId"]
        # gemrate exact
        gem = (a.get("gemrateId") or "").lower()
        target = None
        method = None
        score = 0
        if gem and gem in by_gem:
            target = by_gem[gem]
            method = "gemrate"
            score = 200
        else:
            col = str(a.get("collectorNumber") or "")
            cname = str(a.get("cardName") or "")
            forms = collector_forms(col)
            keys = forms | extract_snk_keys(cname, col)
            # recall watch candidates by collector
            recalled_w: list[tuple[int, dict[str, Any], int]] = []
            seen_v: set[int] = set()
            for f in forms:
                for w in by_col.get(f, []):
                    vid = int(w["variant_id"])
                    if vid in seen_v:
                        continue
                    seen_v.add(vid)
                    rsc, _ = score_recall(
                        str(w.get("card_name") or ""),
                        str(w.get("collector_number") or ""),
                        cname,
                        keys,
                        product_number=col,
                    )
                    if rsc >= 40:
                        recalled_w.append((rsc, w, vid))
            recalled_w.sort(key=lambda t: -t[0])
            best = None
            best_sc = 0
            for rsc, w, vid in recalled_w[:12]:
                ok, vreason, vscore = verify_pair(
                    str(w.get("card_name") or ""),
                    str(w.get("collector_number") or ""),
                    cname,
                    keys,
                    watch_set=str(w.get("set_name") or ""),
                    product_number=col,
                )
                if ok and vscore > best_sc:
                    best_sc = vscore
                    best = w
            if best and best_sc >= min_score:
                target = best
                method = "collector_name_recall_verify"
                score = best_sc
        if not target:
            continue
        vid = int(target["variant_id"])
        if existing.get(ext) == vid:
            continue
        accepted.append(
            {
                "variantId": vid,
                "ebayExternalId": ext,
                "method": method,
                "score": score,
                "watchName": target.get("card_name"),
                "collector": target.get("collector_number"),
                "assetName": a.get("cardName"),
                "prevVariantId": existing.get(ext),
                "pipeline": "recall→verify",
            }
        )

    written = 0
    moved = 0
    reg = load_registry()
    if write and accepted:
        for a in accepted:
            upsert_identity(cur, "ebay", a["ebayExternalId"], a["variantId"], f"semi_auto_ebay_{a['method']}")
            written += 1
            if a.get("prevVariantId") and a["prevVariantId"] != a["variantId"]:
                cur.execute(
                    """
                    UPDATE market_sale_observation
                    SET variant_id=%s
                    WHERE source_code='ebay' AND external_entity_id=%s AND variant_id=%s
                    """,
                    (a["variantId"], a["ebayExternalId"], a["prevVariantId"]),
                )
                moved += int(cur.rowcount or 0)
            reg[a["variantId"]] = {
                **(reg.get(a["variantId"]) or {}),
                "variantId": a["variantId"],
                "preferredLiquiditySource": "ebay",
                "ebayExternalId": a["ebayExternalId"],
                "script": "pipelines/semi_auto_identity.py#match-ebay",
                "matchMethod": a["method"],
                "updatedAt": utc_now(),
            }
        conn.commit()
        save_registry(reg)
        ledger_append(
            [
                {
                    "action": "match_ebay",
                    "at": utc_now(),
                    **a,
                    "script": "pipelines/semi_auto_identity.py#match-ebay",
                }
                for a in accepted
            ]
        )

    cur.execute(
        """
        SELECT COUNT(DISTINCT w.variant_id) c
        FROM market_gemrate_psa10_watchlist w
        JOIN catalog_source_identity s ON s.variant_id=w.variant_id AND s.source_code='ebay'
        """
    )
    ebay_watch = int(cur.fetchone()["c"])
    conn.close()
    summary = {
        "write": write,
        "g10AltxyzScanned": len(assets),
        "accepted": len(accepted),
        "written": written,
        "salesMoved": moved,
        "ebayIdentityOnWatchlist": ebay_watch,
        "sample": accepted[:15],
    }
    _write_report("match_ebay", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return summary


def _write_report(name: str, summary: dict[str, Any]) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"semi_auto_{name}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("report", path)


def cmd_run(*, write: bool) -> dict[str, Any]:
    a = cmd_clean(write=write)
    b = cmd_match_snk(write=write)
    c = cmd_match_ebay(write=write)
    summary = {"clean": a, "matchSnk": b, "matchEbay": c}
    _write_report("run", {k: {kk: vv for kk, vv in v.items() if kk != "sample" and kk != "sampleBad"} for k, v in summary.items()})
    return summary


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("clean", "match-snk", "match-ebay", "run"):
        p = sub.add_parser(name)
        p.add_argument("--write", action="store_true")
        if name == "match-snk":
            p.add_argument("--min-score", type=int, default=100, help="verify floor (compat)")
            p.add_argument("--recall-min", type=int, default=40, help="low bar for candidate haul")
        if name == "match-ebay":
            p.add_argument("--min-score", type=int, default=100)
            p.add_argument("--g10-root", type=Path, default=G10_DEFAULT)
        if name == "run":
            p.add_argument("--recall-min", type=int, default=40)
    args = ap.parse_args()
    if args.cmd == "clean":
        cmd_clean(write=args.write)
    elif args.cmd == "match-snk":
        cmd_match_snk(write=args.write, min_score=args.min_score, recall_min=args.recall_min)
    elif args.cmd == "match-ebay":
        cmd_match_ebay(write=args.write, g10_root=args.g10_root, min_score=args.min_score)
    elif args.cmd == "run":
        cmd_clean(write=args.write)
        cmd_match_snk(write=args.write, recall_min=getattr(args, "recall_min", 40))
        cmd_match_ebay(write=args.write)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
