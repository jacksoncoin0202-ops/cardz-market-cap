#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Operator corroboration: PC page PSA 10 pop ≈ GemRate pop + PSA 10 price.

DADDY 方法論（2026-08-13）：GemRate POP 係真理；PC 頁 `VGPC.pop_data.psa[-1]`
係 PSA 10 人口，只會滯後唔會超前。唯一候選同時有正數 PSA 10 價 → promote exact。

唔放寬 product_agrees／print-signature gate。紅名單 skip。
pid 已被另一張卡 exact 佔住 → skip。strict view 唔收貨就 rollback 嗰張。

用法：
  python -X utf8 pipelines/adjudicate_pc_pop_corroboration_20260813.py --dry-run
  python -X utf8 pipelines/adjudicate_pc_pop_corroboration_20260813.py --fetch-missing --dry-run
  python -X utf8 pipelines/adjudicate_pc_pop_corroboration_20260813.py --write
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))
sys.path.insert(0, str(ROOT / "scripts"))

from pricecharting_page_parse import extract_vgpc, parse_product_html  # noqa: E402
import snk_identity_discover as D  # noqa: E402
from rebuild_036 import (  # noqa: E402
    DAILY_CREDENTIALS_ENV,
    EVIDENCE_TYPE_PROVIDER_PAGE,
    NOT_A_REJECTION_VERDICT_SQL,
    REJECTION_RED_LIST_KEY,
    REJECTION_VERDICT_ACTIONS,
    _pc_map_url_by_product,
    _pc_page_identity,
    _pc_page_product_id,
    canonical_json,
    connect,
    pc_capture_for_product,
    sha256_bytes,
    sha256_file,
)
from stamp_red_sheet_quarantine import red_variant_ids  # noqa: E402

PAGES_DIR = ROOT / "data" / "private" / "pricecharting_session" / "html" / "full900"
REPLAY_DIR = ROOT / "data" / "private" / "pricecharting_session" / "html" / "replay-036_20260808T084217Z"
RECEIPT = ROOT / "data" / "runtime" / "operator" / "audit" / "pc_pop_corroboration_20260813.json"

# PC census 每月更新，滯後 OK；超前超過 10 張當錯卡／事故。
_POP_AHEAD_SLACK = 10
_POP_LAG_ABS = 30
_POP_LAG_RATIO = 0.15


def pop_close(pc_pop: int, gemrate_pop: int) -> bool:
    if pc_pop <= 0 or gemrate_pop <= 0:
        return False
    if pc_pop > gemrate_pop + _POP_AHEAD_SLACK:
        return False
    floor = max(0, gemrate_pop - max(_POP_LAG_ABS, int(gemrate_pop * _POP_LAG_RATIO)))
    return pc_pop >= floor


def _self_check() -> None:
    # v2185 Charizard V SWSH050: captured PC 50342 vs GemRate 50927.
    assert pop_close(50342, 50927), "known close pair must pass"
    assert not pop_close(1000, 50927), "far-behind must fail"
    assert not pop_close(60000, 50927), "ahead of GemRate must fail"
    assert pop_close(20, 25), "small-pop abs slack"
    assert not pop_close(0, 100), "zero pc pop must fail"
    assert _collector_agrees("215", "215/203"), "PC drops the denominator"
    assert _collector_agrees("SWSH050", "050"), "promo prefix"


def _collector_core(value: str) -> str:
    text = re.sub(r"\s+", "", str(value or "")).casefold()
    if not text:
        return ""
    text = text.split("/", 1)[0]
    return re.sub(r"[^a-z0-9]", "", text)


def _collector_agrees(page: str, catalog: str) -> bool:
    a = _collector_core(page)
    b = _collector_core(catalog)
    if not a or not b:
        return True
    if a == b or a.endswith(b) or b.endswith(a):
        return True
    da = re.sub(r"[^0-9]", "", a).lstrip("0")
    db = re.sub(r"[^0-9]", "", b).lstrip("0")
    return bool(da and da == db)


def psa10_pop(html: str) -> int | None:
    vgpc = extract_vgpc(html)
    pop = vgpc.get("pop_data")
    if not isinstance(pop, dict):
        return None
    psa = pop.get("psa")
    if not isinstance(psa, list) or len(psa) not in (10, 11):
        return None
    try:
        value = int(psa[-1])
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def psa10_price_usd(html: str, url: str) -> float | None:
    parsed = parse_product_html(html, source_url=url)
    if not parsed.get("ok"):
        return None
    history = (parsed.get("psa10") or {}).get("history") or {}
    if history.get("label") != "PSA 10":
        return None
    price = history.get("last_usd")
    try:
        value = float(price)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _url_from_evidence(raw: str) -> str:
    try:
        ev = json.loads(raw or "{}")
    except ValueError:
        return ""
    if not isinstance(ev, dict):
        return ""
    evidence = ev.get("evidence") if isinstance(ev.get("evidence"), dict) else {}
    nested = evidence.get("evidence") if isinstance(evidence.get("evidence"), dict) else {}
    for obj in (nested, evidence, ev):
        if not isinstance(obj, dict):
            continue
        for key in ("canonicalUrl", "url", "pc_url"):
            val = str(obj.get(key) or "")
            if "pricecharting.com/game/" in val:
                return val
    return ""


def _find_html(vid: int, pid: str) -> Path | None:
    mapped = pc_capture_for_product(PAGES_DIR, vid, pid)
    best: Path | None = None
    candidates: list[Path] = []
    if mapped is not None:
        candidates.append(mapped)
    for folder in (PAGES_DIR, REPLAY_DIR):
        if not folder.is_dir():
            continue
        candidates.extend(folder.glob(f"{vid}_*.html"))
        candidates.extend(folder.glob(f"{pid}_*.html"))
        candidates.extend(folder.glob(f"*_{pid}.html"))
    seen: set[Path] = set()
    for path in candidates:
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        html = path.read_text(encoding="utf-8", errors="replace")
        if _pc_page_product_id(html) != str(pid):
            continue
        if _pc_page_identity(html)[0] is None:
            continue
        if psa10_pop(html) is not None:
            return path
        if best is None:
            best = path
    return best


def _load_cards(cur) -> dict[int, dict[str, Any]]:
    cur.execute(
        """
        SELECT l.variant_id,
               COALESCE(psa.psa_description,
                 JSON_UNQUOTE(JSON_EXTRACT(rm.detail_json,'$.fingerprint.description'))) AS psa_name,
               rm.latest_psa10_population AS pop,
               v.tcg_code, v.card_language, v.set_name, v.collector_number,
               v.canonical_name, v.set_code AS v_set_code, v.printing_code AS v_printing_code,
               p.parallel_code, p.printing_code, p.canonical_printing_sha256,
               p.tcg_code AS p_tcg_code, p.card_language AS p_card_language,
               p.set_code AS p_set_code, p.collector_number AS p_collector_number,
               p.edition_code AS p_edition_code, p.finish_code AS p_finish_code
        FROM market_identity_discovery_ledger l
        JOIN catalog_variant v ON v.id=l.variant_id
        LEFT JOIN catalog_printing_identity p ON p.variant_id=v.id
        LEFT JOIN catalog_rebuild_member rm ON rm.variant_id=l.variant_id
          AND rm.generation_id=(SELECT generation_id FROM catalog_rebuild_member ORDER BY computed_at DESC LIMIT 1)
        LEFT JOIN catalog_psa_identity_acceptance psa ON psa.variant_id=l.variant_id
          AND NOT EXISTS (SELECT 1 FROM catalog_psa_identity_acceptance n WHERE n.supersedes_acceptance_id=psa.id)
        WHERE (l.discovery_status='identity_ambiguous'
           OR l.blocker_code IN ('manual_review','multiple_exact_bindings'))
          AND NOT EXISTS (
            SELECT 1 FROM catalog_source_identity x
            WHERE x.variant_id=l.variant_id AND x.source_code='pricecharting'
              AND x.match_status='exact'
          )
        """
    )
    cards: dict[int, dict[str, Any]] = {}
    for r in cur.fetchall():
        cards[int(r["variant_id"])] = dict(r)
    return cards


def _score_page(
    row: dict[str, Any], pid: str, status: str, html: str, html_path: Path, url: str
) -> dict[str, Any]:
    identity, why = _pc_page_identity(html)
    if identity is None:
        return {"ext": pid, "status": status, "ok": False, "why": f"page_identity:{why}"}
    page_pid = _pc_page_product_id(html)
    if page_pid != str(pid):
        return {"ext": pid, "status": status, "ok": False, "why": f"page_pid:{page_pid}!={pid}"}
    pc_pop = psa10_pop(html)
    if pc_pop is None:
        return {"ext": pid, "status": status, "ok": False, "why": "no_psa10_pop"}
    gemrate_pop = int(row.get("pop") or 0)
    if not pop_close(pc_pop, gemrate_pop):
        return {
            "ext": pid, "status": status, "ok": False,
            "why": f"pop_mismatch:pc={pc_pop},gemrate={gemrate_pop}",
            "pc_pop": pc_pop, "price": None,
        }
    price = psa10_price_usd(html, url or identity["canonicalUrl"])
    if price is None:
        return {
            "ext": pid, "status": status, "ok": False,
            "why": f"no_psa10_price:pc_pop={pc_pop}",
            "pc_pop": pc_pop,
        }
    ours_tcg = str(row.get("p_tcg_code") or row.get("tcg_code") or "")
    if identity["tcg"] and ours_tcg and identity["tcg"] != ours_tcg:
        return {"ext": pid, "status": status, "ok": False, "why": f"tcg:{identity['tcg']}!={ours_tcg}"}
    ours_lang = str(row.get("p_card_language") or row.get("card_language") or "")
    if identity["language"] and ours_lang and identity["language"] != ours_lang:
        return {"ext": pid, "status": status, "ok": False, "why": f"language:{identity['language']}!={ours_lang}"}
    ours_num = str(row.get("p_collector_number") or row.get("collector_number") or "")
    if not _collector_agrees(identity["collector"], ours_num):
        return {
            "ext": pid, "status": status, "ok": False,
            "why": f"collector:{identity['collector']}!={ours_num}",
        }
    return {
        "ext": pid,
        "status": status,
        "ok": True,
        "why": "pc_psa10_pop+price",
        "pc_pop": pc_pop,
        "gemrate_pop": gemrate_pop,
        "price_usd": price,
        "identity": identity,
        "html_path": html_path,
        "url": url or identity["canonicalUrl"],
    }


def _promote_one(cur, vid: int, row: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
    pid = str(decision["ext"])
    html_path: Path = decision["html_path"]
    identity: dict[str, Any] = decision["identity"]
    digest = sha256_file(html_path)
    rel = html_path.resolve().relative_to(ROOT.resolve()).as_posix()
    captured_at = datetime.fromtimestamp(
        html_path.stat().st_mtime, tz=timezone.utc
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    evidence = {
        "providerClaims": {
            "tcgCode": identity["tcg"],
            "cardLanguage": identity["language"],
            "collectorNumber": identity["collector"],
            "setCode": "",
            "printingCode": "",
            "parallelCode": identity["parallel"],
        },
        "evidence": {
            "type": EVIDENCE_TYPE_PROVIDER_PAGE,
            "sha256": digest,
            "path": rel,
            "canonicalUrl": identity["canonicalUrl"],
            "pageHeading": identity["heading"],
            "capturedAt": captured_at,
            "generation": "pc_pop_corroboration_20260813",
        },
        "adjudication": {
            "action": "promote-pc-pop-corroboration",
            "adjudicatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "adjudicatedBy": "adjudicate_pc_pop_corroboration_20260813",
            "reason": (
                f"PC VGPC.pop_data.psa[-1]={decision['pc_pop']} ≈ GemRate "
                f"{decision['gemrate_pop']}; PSA 10 price ${decision['price_usd']:.2f}"
            ),
            "gemratePsaName": row.get("psa_name"),
            "gemratePop": decision["gemrate_pop"],
            "pcPop": decision["pc_pop"],
            "pcPsa10Usd": decision["price_usd"],
        },
    }
    evidence_sha = sha256_bytes(canonical_json(evidence))
    ident = D.bound_ident(row)
    cur.execute(
        """INSERT INTO catalog_provider_capture_receipt
             (source_code, external_entity_id, capture_sha256, capture_path,
              captured_at, generation_id, parser_version)
           VALUES ('pricecharting', %s, %s, %s, %s, %s, %s)
           ON DUPLICATE KEY UPDATE capture_sha256=VALUES(capture_sha256),
             capture_path=VALUES(capture_path), captured_at=VALUES(captured_at),
             generation_id=VALUES(generation_id), parser_version=VALUES(parser_version)""",
        (
            pid, digest, rel[:500],
            datetime.strptime(captured_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc),
            "pc_pop_corroboration_20260813", "pc_pop_corroboration_v1",
        ),
    )
    cur.execute(
        """INSERT INTO catalog_source_identity
             (source_code, external_entity_id, variant_id, match_status, evidence_sha256,
              source_product_number, bind_evidence_json, bound_tcg_code, bound_card_language,
              bound_collector_number, bound_set_code, bound_printing_code, bound_parallel_code,
              bound_edition_code, bound_finish_code)
           VALUES ('pricecharting', %s, %s, 'exact', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
           ON DUPLICATE KEY UPDATE variant_id=VALUES(variant_id), match_status='exact',
             evidence_sha256=VALUES(evidence_sha256),
             source_product_number=VALUES(source_product_number),
             bind_evidence_json=VALUES(bind_evidence_json),
             bound_tcg_code=VALUES(bound_tcg_code),
             bound_card_language=VALUES(bound_card_language),
             bound_collector_number=VALUES(bound_collector_number),
             bound_set_code=VALUES(bound_set_code),
             bound_printing_code=VALUES(bound_printing_code),
             bound_parallel_code=VALUES(bound_parallel_code),
             bound_edition_code=VALUES(bound_edition_code),
             bound_finish_code=VALUES(bound_finish_code)""",
        (
            pid, vid, evidence_sha, str(identity["collector"])[:96],
            json.dumps(evidence, ensure_ascii=False),
            ident["tcg"], ident["lang"], ident["collector"], ident["set_code"],
            ident["printing"], ident["parallel"], ident["edition"], ident["finish"],
        ),
    )
    cur.execute(
        """SELECT COUNT(*) n FROM operator_strict_source_identity
           WHERE source_code='pricecharting' AND external_entity_id=%s AND variant_id=%s""",
        (pid, vid),
    )
    strict_n = int(cur.fetchone()["n"])
    if strict_n != 1:
        raise RuntimeError(f"v{vid} pc:{pid} strict view rows={strict_n}")
    return {
        "variant_id": vid,
        "pid": pid,
        "why": decision["why"],
        "pc_pop": decision["pc_pop"],
        "gemrate_pop": decision["gemrate_pop"],
        "price_usd": decision["price_usd"],
        "psa_name": row.get("psa_name"),
        "strictRows": strict_n,
    }


def _fetch_missing(
    missing: list[tuple[int, str, str]],
) -> dict[str, int]:
    import pricecharting_cf_session as cf_session

    counts = {"attempted": 0, "fetched": 0, "failed": 0, "no_url": 0}
    PAGES_DIR.mkdir(parents=True, exist_ok=True)
    for vid, pid, url in missing:
        if not url:
            counts["no_url"] += 1
            continue
        counts["attempted"] += 1
        out = PAGES_DIR / f"{vid}_{pid}.html"
        code = cf_session.cmd_fetch(url, out, timeout_s=90)
        if code != 0 or not out.is_file():
            counts["failed"] += 1
            continue
        body = out.read_text(encoding="utf-8", errors="replace")
        if _pc_page_product_id(body) != pid:
            out.unlink(missing_ok=True)
            counts["failed"] += 1
            continue
        counts["fetched"] += 1
        time.sleep(2.0)
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--write", action="store_true")
    parser.add_argument("--fetch-missing", action="store_true")
    args = parser.parse_args()
    _self_check()

    red = set(red_variant_ids())
    conn = connect(DAILY_CREDENTIALS_ENV)
    cur = conn.cursor()
    cards = _load_cards(cur)
    if not cards:
        print(json.dumps({"counts": {"cards": 0}}))
        conn.close()
        return 0

    cur.execute(
        """SELECT variant_id, external_entity_id, match_status, bind_evidence_json
           FROM catalog_source_identity
           WHERE source_code='pricecharting' AND variant_id IN ({})
        """.format(",".join(["%s"] * len(cards))),
        list(cards),
    )
    pc_by: dict[int, list[dict[str, Any]]] = {vid: [] for vid in cards}
    for r in cur.fetchall():
        pc_by[int(r["variant_id"])].append({
            "ext": str(r["external_entity_id"]),
            "status": r["match_status"],
            "url": _url_from_evidence(r["bind_evidence_json"] or ""),
            "evidence": r["bind_evidence_json"] or "",
        })

    cur.execute(
        """SELECT external_entity_id, variant_id FROM catalog_source_identity
           WHERE source_code='pricecharting' AND match_status='exact'"""
    )
    exact_owner = {str(r["external_entity_id"]): int(r["variant_id"]) for r in cur.fetchall()}
    urls_by_pid = _pc_map_url_by_product()

    missing_fetch: list[tuple[int, str, str]] = []
    scored_cache: dict[tuple[int, str], dict[str, Any]] = {}

    def evaluate(vid: int, cand: dict[str, Any]) -> dict[str, Any]:
        pid = cand["ext"]
        key = (vid, pid)
        if key in scored_cache:
            return scored_cache[key]
        owner = exact_owner.get(pid)
        if owner is not None and owner != vid:
            scored_cache[key] = {
                "ext": pid, "status": cand["status"], "ok": False,
                "why": f"pid_owned_by_v{owner}",
            }
            return scored_cache[key]
        ev = {}
        try:
            ev = json.loads(cand["evidence"] or "{}")
        except ValueError:
            ev = {}
        action = str(ev.get("action") or "")
        if action in REJECTION_VERDICT_ACTIONS:
            scored_cache[key] = {
                "ext": pid, "status": cand["status"], "ok": False,
                "why": f"reasoned_rejection:{action}",
            }
            return scored_cache[key]
        if str(ev.get(REJECTION_RED_LIST_KEY) or "").lower() == "true":
            scored_cache[key] = {
                "ext": pid, "status": cand["status"], "ok": False,
                "why": "redListed",
            }
            return scored_cache[key]
        html_path = _find_html(vid, pid)
        url = cand["url"] or urls_by_pid.get(pid, "")
        if html_path is None:
            missing_fetch.append((vid, pid, url))
            scored_cache[key] = {
                "ext": pid, "status": cand["status"], "ok": False,
                "why": "no_html", "url": url,
            }
            return scored_cache[key]
        if not url:
            ident, _ = _pc_page_identity(html_path.read_text(encoding="utf-8", errors="replace"))
            url = (ident or {}).get("canonicalUrl") or ""
        html = html_path.read_text(encoding="utf-8", errors="replace")
        if psa10_pop(html) is None:
            missing_fetch.append((vid, pid, url or cand["url"] or urls_by_pid.get(pid, "")))
            scored_cache[key] = {
                "ext": pid, "status": cand["status"], "ok": False,
                "why": "no_psa10_pop", "url": url, "html": str(html_path),
            }
            return scored_cache[key]
        scored_cache[key] = _score_page(cards[vid], pid, cand["status"], html, html_path, url)
        return scored_cache[key]

    for vid in cards:
        for cand in pc_by.get(vid, []):
            evaluate(vid, cand)

    fetch_counts: dict[str, int] | None = None
    if args.fetch_missing and missing_fetch:
        # unique by pid
        seen_pid: set[str] = set()
        uniq: list[tuple[int, str, str]] = []
        for item in missing_fetch:
            if item[1] in seen_pid:
                continue
            seen_pid.add(item[1])
            uniq.append(item)
        print(json.dumps({"phase": "pc-fetch-missing", "n": len(uniq)}, ensure_ascii=False), flush=True)
        fetch_counts = _fetch_missing(uniq)
        print(json.dumps({"phase": "pc-fetch-missing", "counts": fetch_counts}, ensure_ascii=False), flush=True)
        scored_cache.clear()
        missing_fetch.clear()
        for vid in cards:
            for cand in pc_by.get(vid, []):
                evaluate(vid, cand)

    promotable: list[tuple[int, dict[str, Any], dict[str, Any]]] = []
    held: list[dict[str, Any]] = []
    for vid, row in sorted(cards.items(), key=lambda kv: -(kv[1].get("pop") or 0)):
        if vid in red:
            held.append({"variant_id": vid, "why": "red_sheet", "psa_name": row.get("psa_name"),
                         "pop": row.get("pop")})
            continue
        cands = pc_by.get(vid, [])
        if not cands:
            held.append({"variant_id": vid, "why": "no_pc_candidate", "pop": row.get("pop"),
                         "psa_name": row.get("psa_name")})
            continue
        decisions = [evaluate(vid, cand) for cand in cands]
        ok = [d for d in decisions if d.get("ok")]
        if len(ok) == 1:
            promotable.append((vid, row, ok[0]))
        elif len(ok) > 1:
            held.append({
                "variant_id": vid, "why": "ambiguous_ok",
                "items": [d["ext"] for d in ok], "psa_name": row.get("psa_name"),
                "pop": row.get("pop"),
            })
        else:
            held.append({
                "variant_id": vid, "why": "unsolved",
                "pop": row.get("pop"), "psa_name": row.get("psa_name"),
                "fails": [{"ext": d["ext"], "status": d.get("status"), "why": d.get("why"),
                           "pc_pop": d.get("pc_pop")} for d in decisions],
            })

    plan = {
        "promotable": [
            {
                "variant_id": vid, "pid": d["ext"], "why": d["why"],
                "pop": row.get("pop"), "pc_pop": d.get("pc_pop"),
                "price_usd": d.get("price_usd"), "psa_name": row.get("psa_name"),
                "heading": (d.get("identity") or {}).get("heading"),
            }
            for vid, row, d in promotable
        ],
        "held": held,
        "fetch": fetch_counts,
        "counts": {
            "cards": len(cards),
            "promotable": len(promotable),
            "held": len(held),
            "red_sheet": sum(1 for h in held if h["why"] == "red_sheet"),
            "no_pc_candidate": sum(1 for h in held if h["why"] == "no_pc_candidate"),
            "unsolved": sum(1 for h in held if h["why"] == "unsolved"),
            "ambiguous_ok": sum(1 for h in held if h["why"] == "ambiguous_ok"),
        },
    }
    print(json.dumps({"counts": plan["counts"], "promotable": plan["promotable"][:20],
                      "promotableMore": max(0, len(plan["promotable"]) - 20)},
                     ensure_ascii=False, indent=1))
    if args.dry_run:
        RECEIPT.parent.mkdir(parents=True, exist_ok=True)
        RECEIPT.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
        print("dry-run receipt:", RECEIPT)
        conn.close()
        return 0

    written = []
    failed = []
    for vid, row, decision in promotable:
        try:
            written.append(_promote_one(cur, vid, row, decision))
            conn.commit()
        except Exception as exc:
            conn.rollback()
            failed.append({
                "variant_id": vid, "pid": decision["ext"],
                "error": f"{type(exc).__name__}: {exc}",
            })
    out = {**plan, "written": written, "failed": failed}
    RECEIPT.parent.mkdir(parents=True, exist_ok=True)
    RECEIPT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"written": len(written), "failed": failed}, ensure_ascii=False, indent=1))
    print("receipt:", RECEIPT)
    conn.close()
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
