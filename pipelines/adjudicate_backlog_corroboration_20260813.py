#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Operator corroboration for identity_ambiguous backlog (2026-08-13).

DADDY 方法論：GemRate POP 係真理；SNK／PC 用「差唔多嘅數量 + 差唔多嘅價」證明
同一張卡。SNK 公開 API／HTML **冇** PSA POP 欄（2026-08-13 實測），所以呢度用
更穩嘅辨識詞：

  1. rule_candidate 已經過 → 直接 promote（腳本本來就證到）
  2. GemRate 名含 Master Ball，SNK 名含 マスターボール/Master Ball，
     collector + character + language 一致 → promote
     （SNK 寫「ミラー」、GemRate 寫「Reverse Holo」，同一張卡；
      Master Ball 本身就係同號普通版嘅辨識詞，POP 會差一個數量級）

唔放寬 product_agrees／mirror gate。紅名單 skip。唯一候選先綁。
strict view 唔收貨就 rollback 嗰張。

用法：
  python -X utf8 pipelines/adjudicate_backlog_corroboration_20260813.py --dry-run
  python -X utf8 pipelines/adjudicate_backlog_corroboration_20260813.py --write
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))
sys.path.insert(0, str(ROOT / "scripts"))

import snk_identity_discover as D  # noqa: E402
from rebuild_036 import (  # noqa: E402
    DAILY_CREDENTIALS_ENV,
    card_name_without_treatment,
    canonical_json,
    connect,
    sha256_bytes,
    sha256_file,
)
from stamp_red_sheet_quarantine import red_variant_ids  # noqa: E402

GENERATION = "036_20260808T084217Z"
ITEMS_DIR = ROOT / "data" / "runtime" / "rebuild-036" / "snk-corroboration-20260813" / "items"
RECEIPT = ROOT / "data" / "runtime" / "operator" / "audit" / "backlog_corroboration_20260813.json"
LOCAL_SNK = ROOT / "data" / "private" / "snk" / f"rebuild-{GENERATION}" / "items"

_MASTER_BALL_RE = re.compile(r"master\s*ball|マスターボール", re.I)
_NAME_NUM_RE = re.compile(r"[A-Za-z0-9]+")


def _collector_core(value: str) -> str:
    text = re.sub(r"\s+", "", str(value or "")).casefold()
    if not text:
        return ""
    text = text.split("/", 1)[0]
    if "-" in text:
        text = text.rsplit("-", 1)[1]
    return str(int(text)) if text.isdigit() else text


def _load_local_payload(item_id: str) -> dict[str, Any] | None:
    path = LOCAL_SNK / f"{item_id}.json"
    if not path.is_file():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    if "source_payload" in raw:
        return raw
    return {
        "item_id": int(item_id) if str(item_id).isdigit() else item_id,
        "source_payload": {"master": raw.get("master") or raw},
        "product_number": raw.get("productNumber") or (raw.get("master") or {}).get("productNumber"),
        "quantity_variant_id": raw.get("quantityVariantId"),
        "image_url": raw.get("imageUrl"),
        "fetched_at": raw.get("fetchedAt"),
        "condition_filter": raw.get("conditionFilter"),
    }


def _master_ball_corroborates(row: dict[str, Any], payload: dict[str, Any]) -> tuple[bool, str]:
    gemrate_blob = " ".join((
        str(row.get("psa_name") or ""),
        str(row.get("fp_parallel") or ""),
        str(row.get("parallel_code") or ""),
    ))
    if not _MASTER_BALL_RE.search(gemrate_blob):
        return False, "gemrate_not_master_ball"
    master = (payload.get("source_payload") or {}).get("master") or {}
    snk_blob = " ".join((
        str(master.get("name") or ""),
        str(master.get("localizedName") or ""),
    ))
    if not _MASTER_BALL_RE.search(snk_blob):
        return False, "snk_not_master_ball"
    if not payload.get("quantity_variant_id"):
        return False, "no_psa10_1card_variant"
    same_character, why = D.character_agrees(
        row.get("fp_name") or "",
        str(master.get("name") or ""),
        str(master.get("localizedName") or ""),
    )
    if not same_character:
        return False, why
    language = D.R._snk_language(str(master.get("name") or ""), str(master.get("localizedName") or ""))
    ours_lang = str(row.get("p_card_language") or row.get("card_language") or "")
    if language and ours_lang and language != ours_lang:
        return False, f"language:{language}!={ours_lang}"
    claim = D.R._snk_collector_claim(
        str(master.get("name") or ""),
        str(master.get("localizedName") or ""),
        str(payload.get("product_number") or ""),
    )
    ours_num = str(row.get("p_collector_number") or row.get("collector_number") or "")
    if _collector_core(D.R._snk_claim_number(claim)) != _collector_core(ours_num):
        return False, f"collector:{claim}!={ours_num}"
    return True, "master_ball+collector+character"


def _classify(row: dict[str, Any], ext: str, status: str, payload: dict[str, Any] | None) -> dict[str, Any]:
    if payload is None:
        return {"ext": ext, "status": status, "ok": False, "why": "no_local_capture"}
    ok, why, facts = D.rule_candidate(row, payload)
    if ok:
        return {"ext": ext, "status": status, "ok": True, "why": "rule_candidate", "facts": facts, "payload": payload}
    mb_ok, mb_why = _master_ball_corroborates(row, payload)
    if mb_ok:
        master = (payload.get("source_payload") or {}).get("master") or {}
        facts = {
            "master": master,
            "masterName": master.get("name"),
            "localized": master.get("localizedName"),
            "productNumber": payload.get("product_number"),
            "claim": D.R._snk_collector_claim(
                str(master.get("name") or ""),
                str(master.get("localizedName") or ""),
                str(payload.get("product_number") or ""),
            ),
            "language": D.R._snk_language(str(master.get("name") or ""), str(master.get("localizedName") or "")),
            "tcg": D.R._snk_tcg(str(master.get("name") or ""), str(master.get("localizedName") or "")),
            "quantityVariantId": payload.get("quantity_variant_id"),
            "imageUrl": payload.get("image_url"),
            "fetchedAt": payload.get("fetched_at"),
            "conditionFilter": payload.get("condition_filter"),
            "snkParallel": "master-ball",
        }
        return {"ext": ext, "status": status, "ok": True, "why": mb_why, "facts": facts, "payload": payload}
    return {"ext": ext, "status": status, "ok": False, "why": why}


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
               p.edition_code AS p_edition_code, p.finish_code AS p_finish_code,
               JSON_UNQUOTE(JSON_EXTRACT(rm.detail_json,'$.fingerprint.name')) AS fp_name,
               JSON_UNQUOTE(JSON_EXTRACT(rm.detail_json,'$.fingerprint.parallel')) AS fp_parallel,
               JSON_UNQUOTE(JSON_EXTRACT(rm.detail_json,'$.fingerprint.cardNumber')) AS fp_number
        FROM market_identity_discovery_ledger l
        JOIN catalog_variant v ON v.id=l.variant_id
        LEFT JOIN catalog_printing_identity p ON p.variant_id=v.id
        LEFT JOIN catalog_rebuild_member rm ON rm.variant_id=l.variant_id
          AND rm.generation_id=(SELECT generation_id FROM catalog_rebuild_member ORDER BY computed_at DESC LIMIT 1)
        LEFT JOIN catalog_psa_identity_acceptance psa ON psa.variant_id=l.variant_id
          AND NOT EXISTS (SELECT 1 FROM catalog_psa_identity_acceptance n WHERE n.supersedes_acceptance_id=psa.id)
        WHERE l.discovery_status='identity_ambiguous'
           OR l.blocker_code IN ('manual_review','multiple_exact_bindings')
        """
    )
    cards: dict[int, dict[str, Any]] = {}
    for r in cur.fetchall():
        row = dict(r)
        row["fp_name"] = card_name_without_treatment(row.get("fp_name") or "")
        cards[int(row["variant_id"])] = row
    return cards


def _promote_one(cur, vid: int, row: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
    item_id = int(decision["ext"])
    facts = decision["facts"]
    ITEMS_DIR.mkdir(parents=True, exist_ok=True)
    evidence, evidence_sha = D.build_evidence(item_id, facts, ITEMS_DIR)
    evidence["adjudication"] = {
        "action": "promote-backlog-corroboration",
        "adjudicatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "adjudicatedBy": "adjudicate_backlog_corroboration_20260813",
        "reason": decision["why"],
        "gemratePsaName": row.get("psa_name"),
        "gemratePop": row.get("pop"),
    }
    digest = evidence["evidence"]["sha256"]
    rel = evidence["evidence"]["path"]
    fetched = str(facts.get("fetchedAt") or "")
    captured_at = (
        datetime.strptime(fetched, "%Y-%m-%dT%H:%M:%S%z") if fetched
        else datetime.now(timezone.utc)
    )
    parser_version = "snkmd_" + sha256_file(ROOT / "pipelines" / "snk_market_data.py")[:12]
    cur.execute(
        """INSERT INTO catalog_provider_capture_receipt
             (source_code, external_entity_id, capture_sha256, capture_path,
              captured_at, generation_id, parser_version)
           VALUES ('snkrdunk', %s, %s, %s, %s, %s, %s)
           ON DUPLICATE KEY UPDATE capture_sha256=VALUES(capture_sha256),
             capture_path=VALUES(capture_path), captured_at=VALUES(captured_at),
             generation_id=VALUES(generation_id), parser_version=VALUES(parser_version)""",
        (str(item_id), digest, rel[:500], captured_at, GENERATION, parser_version[:64]),
    )
    ident = D.bound_ident(row)
    cur.execute(
        """INSERT INTO catalog_source_identity
             (source_code, external_entity_id, variant_id, match_status, evidence_sha256,
              source_product_number, bind_evidence_json, bound_tcg_code, bound_card_language,
              bound_collector_number, bound_set_code, bound_printing_code, bound_parallel_code,
              bound_edition_code, bound_finish_code)
           VALUES ('snkrdunk', %s, %s, 'exact', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
            str(item_id), vid, evidence_sha, str(facts.get("claim") or ident["collector"]),
            json.dumps(evidence, ensure_ascii=False),
            ident["tcg"], ident["lang"], ident["collector"], ident["set_code"],
            ident["printing"], ident["parallel"], ident["edition"], ident["finish"],
        ),
    )
    cur.execute(
        """SELECT COUNT(*) n FROM operator_strict_source_identity
           WHERE source_code='snkrdunk' AND external_entity_id=%s AND variant_id=%s""",
        (str(item_id), vid),
    )
    strict_n = int(cur.fetchone()["n"])
    if strict_n != 1:
        raise RuntimeError(f"v{vid} snk:{item_id} strict view rows={strict_n}")
    return {
        "variant_id": vid,
        "item": item_id,
        "why": decision["why"],
        "pop": row.get("pop"),
        "psa_name": row.get("psa_name"),
        "strictRows": strict_n,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--write", action="store_true")
    args = parser.parse_args()

    red = set(red_variant_ids())
    conn = connect(DAILY_CREDENTIALS_ENV)
    cur = conn.cursor()
    cards = _load_cards(cur)
    cur.execute(
        """SELECT variant_id, external_entity_id, match_status
           FROM catalog_source_identity
           WHERE source_code='snkrdunk' AND variant_id IN ({})
        """.format(",".join(["%s"] * len(cards))),
        list(cards),
    )
    snk_by: dict[int, list[tuple[str, str]]] = {vid: [] for vid in cards}
    for r in cur.fetchall():
        snk_by[int(r["variant_id"])].append((str(r["external_entity_id"]), r["match_status"]))

    promotable: list[tuple[int, dict[str, Any], dict[str, Any]]] = []
    held: list[dict[str, Any]] = []
    for vid, row in sorted(cards.items(), key=lambda kv: -(kv[1].get("pop") or 0)):
        if vid in red:
            held.append({"variant_id": vid, "why": "red_sheet", "psa_name": row.get("psa_name")})
            continue
        cands = [c for c in snk_by.get(vid, []) if c[1] in ("manual_review", "rejected")]
        if not cands:
            held.append({"variant_id": vid, "why": "no_snk_candidate", "pop": row.get("pop"),
                         "psa_name": row.get("psa_name")})
            continue
        decisions = [
            _classify(row, ext, status, _load_local_payload(ext))
            for ext, status in cands
        ]
        ok = [d for d in decisions if d["ok"]]
        if len(ok) == 1:
            promotable.append((vid, row, ok[0]))
        elif len(ok) > 1:
            held.append({"variant_id": vid, "why": "ambiguous_ok",
                         "items": [d["ext"] for d in ok], "psa_name": row.get("psa_name")})
        else:
            held.append({
                "variant_id": vid, "why": "unsolved",
                "pop": row.get("pop"), "psa_name": row.get("psa_name"),
                "fails": [{"ext": d["ext"], "status": d["status"], "why": d["why"]} for d in decisions],
            })

    plan = {
        "promotable": [
            {"variant_id": vid, "item": d["ext"], "why": d["why"],
             "pop": row.get("pop"), "psa_name": row.get("psa_name")}
            for vid, row, d in promotable
        ],
        "held": held,
        "counts": {
            "cards": len(cards),
            "promotable": len(promotable),
            "held": len(held),
            "red_sheet": sum(1 for h in held if h["why"] == "red_sheet"),
            "no_snk_candidate": sum(1 for h in held if h["why"] == "no_snk_candidate"),
            "unsolved": sum(1 for h in held if h["why"] == "unsolved"),
        },
    }
    print(json.dumps({"counts": plan["counts"], "promotable": plan["promotable"]},
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
            failed.append({"variant_id": vid, "item": decision["ext"], "error": f"{type(exc).__name__}: {exc}"})
    out = {**plan, "written": written, "failed": failed}
    RECEIPT.parent.mkdir(parents=True, exist_ok=True)
    RECEIPT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"written": len(written), "failed": failed}, ensure_ascii=False, indent=1))
    print("receipt:", RECEIPT)
    conn.close()
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
