#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Score one leftover product page against leftover + owner catalog rows.

Pure on the scoring path: no MySQL, no CDP. The only writer is
`cmd_release_and_claim`, which deletes the owner's identity row for that
external id when the verdict is kick_owner, then leaves leftover to
`operator_bind` so the judge still runs.

Verdicts (exactly one):
  kick_owner  leftover passes, owner fails
  keep_owner  owner passes, leftover fails  (reprint stealing the booster page)
  keep_all    both pass  (never coin-flip)
  neither     both fail
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import rebuild_036 as R  # noqa: E402
import rebuild_036_reverify as RR  # noqa: E402
import op_identity_rules  # noqa: E402

VERDICTS = ("kick_owner", "keep_owner", "keep_all", "neither")
RULING_DIR = ROOT / "data" / "runtime" / "operator" / "rulings"


def page_passes_row(
    identity: Mapping[str, Any],
    row: Mapping[str, Any],
    *,
    source_code: str = "pricecharting",
) -> tuple[bool, list[str]]:
    """Does this page prove this catalog row? Same gates as pc-identity-reverify."""

    reasons: list[str] = []
    if not identity:
        return False, ["page_identity_missing"]
    pseudo_fp = {
        "cardNumber": identity.get("collector") or "",
        "derivedLanguage": identity.get("language") or "",
        "setName": identity.get("setText") or "",
    }
    conflicts = list(R._fingerprint_variant_conflicts(pseudo_fp, row))
    via_number_set = RR._pc_number_set_explaining(pseudo_fp, row, conflicts)
    if via_number_set:
        conflicts = []
    # SNK leftover-token compare spells `[JTG EN 184]` as set:['jtg'] vs
    # catalog Journey Together. Same supersede as S7 / snk-identity-reverify:
    # the bracket designation's own set claim is the one function
    # `snk_claim_set_agrees`. Do not parse SET EN here.
    if source_code == "snkrdunk":
        heading = str(identity.get("heading") or "")
        set_text = str(identity.get("setText") or "")
        claim = R._snk_collector_claim(heading, set_text, "")
        if R.snk_claim_set_agrees(claim, row, heading, set_text):
            conflicts = [
                c for c in conflicts
                if not c.startswith("set:") and not c.startswith("set_code:")
            ]
    page_tcg = str(identity.get("tcg") or "")
    row_tcg = str(row.get("tcg_code") or "")
    if page_tcg and row_tcg and page_tcg != row_tcg:
        conflicts.append(f"tcg:{page_tcg}!={row_tcg}")
    heading_n = R._norm_text(str(identity.get("heading") or ""))
    name_n = R._norm_text(str(row.get("canonical_name") or ""))
    if "gengar" in name_n and "gengar" not in heading_n:
        conflicts.append("character:gengar")
    if conflicts:
        return False, conflicts

    listing_text = (
        str(identity.get("setText") or ""),
        str(identity.get("canonicalUrl") or ""),
        str(identity.get("heading") or ""),
    )
    if row_tcg == "one-piece":
        if RR._pc_print_belongs_to_number_set(
            via_number_set, str(identity.get("parallel") or ""), row,
        ):
            return False, [
                "product_mismatch:number_set_own_print:"
                f"[{identity.get('parallel')}]:{via_number_set}"
            ]
        char_ok, char_why = op_identity_rules.character_agrees(
            str(row.get("canonical_name") or ""), *listing_text,
        )
        if not char_ok:
            return False, [char_why]
        same_product, why = False, "product_mismatch:no_set_name"
        our_parallel = (
            RR._pc_spc_metal_parallel(row, *listing_text)
            or str(row.get("fp_parallel") or row.get("parallel_code") or "")
        )
        if RR._pc_bracket_names_our_product(str(identity.get("parallel") or ""), row):
            same_product, why = True, ""
        else:
            for candidate_set in R.pc_product_candidate_sets(row, via_number_set):
                same_product, why = op_identity_rules.product_agrees(
                    op_identity_rules.our_product_text(row, candidate_set),
                    our_parallel, *listing_text,
                )
                if same_product:
                    break
        if not same_product:
            return False, [str(why)]

    if source_code == "pricecharting":
        page_parallel = str(identity.get("parallel") or "")
        if not RR._pc_print_signature_ok(page_parallel, row):
            if not RR._pc_unbracketed_own_set_print(
                via_number_set, page_parallel, row,
            ):
                return False, [
                    "print_signature_mismatch:"
                    f"page=[{identity.get('parallel')}] printing={row.get('printing_code')}"
                ]
    return True, reasons


def _catalog_pop(row: Mapping[str, Any]) -> int:
    try:
        return int(row.get("pop") or row.get("latest_psa10_population") or 0)
    except (TypeError, ValueError):
        return 0


def _pc_pop_too_far(pc_pop: int, catalog_pop: int) -> bool:
    """Page census cannot be this catalog card: leftover-5 POP-first.

    PC lags; 10× behind GemRate is not lag, it is a different card. v339
    Mega Gengar MA 39082 on a page whose census is 963 (Incorrect Texture
    ~1410) is the measured case.
    """

    return pc_pop > 0 and catalog_pop > 0 and pc_pop * 10 < catalog_pop


def adjudicate(
    identity: Mapping[str, Any],
    leftover_row: Mapping[str, Any],
    owner_row: Mapping[str, Any],
    *,
    source_code: str = "pricecharting",
    pc_pop: int | None = None,
) -> dict[str, Any]:
    leftover_ok, leftover_gates = page_passes_row(
        identity, leftover_row, source_code=source_code,
    )
    owner_ok, owner_gates = page_passes_row(
        identity, owner_row, source_code=source_code,
    )
    leftover_gates = list(leftover_gates)
    owner_gates = list(owner_gates)
    census = int(pc_pop or 0)
    if census:
        leftover_pop = _catalog_pop(leftover_row)
        owner_pop = _catalog_pop(owner_row)
        if leftover_ok and _pc_pop_too_far(census, leftover_pop):
            leftover_ok = False
            leftover_gates.append(
                f"pop_mismatch:pc={census},catalog={leftover_pop}"
            )
        if owner_ok and _pc_pop_too_far(census, owner_pop):
            owner_ok = False
            owner_gates.append(
                f"pop_mismatch:pc={census},catalog={owner_pop}"
            )
    if leftover_ok and not owner_ok:
        verdict = "kick_owner"
    elif owner_ok and not leftover_ok:
        verdict = "keep_owner"
    elif leftover_ok and owner_ok:
        verdict = "keep_all"
    else:
        verdict = "neither"
    return {
        "verdict": verdict,
        "leftoverOk": leftover_ok,
        "ownerOk": owner_ok,
        "leftoverGates": leftover_gates,
        "ownerGates": owner_gates,
        "gate": (
            "reprint_page_belongs_to_owner" if verdict == "keep_owner"
            else "catalog_duplicate" if verdict == "keep_all"
            else verdict
        ),
    }


def _load_variant_score_row(conn: Any, variant_id: int) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT v.id AS variant_id, v.tcg_code, v.card_language, v.set_name,"
            " v.collector_number, v.canonical_name, v.set_code AS v_set_code,"
            " v.printing_code AS v_printing_code,"
            " (SELECT JSON_UNQUOTE(JSON_EXTRACT(rm.detail_json,"
            "         '$.fingerprint.parallel'))"
            "    FROM catalog_rebuild_member rm WHERE rm.variant_id=v.id"
            "   ORDER BY rm.computed_at DESC LIMIT 1) AS fp_parallel,"
            " p.parallel_code, p.printing_code,"
            " (SELECT rm.latest_psa10_population FROM catalog_rebuild_member rm"
            "   WHERE rm.variant_id=v.id"
            "   ORDER BY rm.computed_at DESC LIMIT 1) AS pop"
            " FROM catalog_variant v"
            " LEFT JOIN catalog_printing_identity p ON p.variant_id=v.id"
            " WHERE v.id=%s",
            (int(variant_id),),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def cmd_release_and_claim(
    *,
    source_code: str,
    owner_variant_id: int,
    claimant_variant_id: int,
    external_id: str,
    actor: str,
    write: bool = False,
    conn: Any = None,
    html: str | None = None,
    identity: Mapping[str, Any] | None = None,
    leftover_row: Mapping[str, Any] | None = None,
    owner_row: Mapping[str, Any] | None = None,
    red_ids: list[int] | None = None,
) -> int:
    """Release owner pid only on kick_owner. Dry-run unless --write.

    Does not write leftover identity: leftover still goes through operator_bind.
    """

    if source_code not in ("pricecharting", "snkrdunk"):
        raise SystemExit(f"--source-code must be pricecharting or snkrdunk, not {source_code}")
    external_id = str(external_id or "").strip()
    if not external_id:
        raise SystemExit("--external-id is required")
    if int(owner_variant_id) == int(claimant_variant_id):
        raise SystemExit("owner and claimant must be different variants")
    owns_conn = conn is None
    if owns_conn:
        conn = R.connect(R.DAILY_CREDENTIALS_ENV)
    ruled_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT variant_id, source_code, external_entity_id, match_status,"
                " bind_evidence_json FROM catalog_source_identity"
                " WHERE source_code=%s AND external_entity_id=%s",
                (source_code, external_id),
            )
            owner_ident = cur.fetchone()
            cur.execute(
                "SELECT capture_sha256, capture_path, captured_at"
                " FROM catalog_provider_capture_receipt"
                " WHERE source_code=%s AND external_entity_id=%s"
                " ORDER BY captured_at DESC LIMIT 1",
                (source_code, external_id),
            )
            receipt = cur.fetchone()
        if not owner_ident:
            print(json.dumps({
                "command": "release-and-claim",
                "verdict": "neither",
                "refused": "no_identity_row",
                "externalEntityId": external_id,
            }, ensure_ascii=False))
            return 1
        if int(owner_ident["variant_id"]) != int(owner_variant_id):
            print(
                f"release-and-claim REFUSED: {source_code}/{external_id} belongs to"
                f" variant {owner_ident['variant_id']}, not {owner_variant_id}"
            )
            return 1
        if not receipt:
            # Operator-supplied identity is the page: SNK leftover hunt has a
            # listing title but no catalog_provider_capture_receipt yet.
            # Capture is written later by operator_bind.
            if identity is not None:
                receipt = {
                    "capture_sha256": "identity-supplied",
                    "capture_path": "",
                    "captured_at": None,
                }
            else:
                # The page can already sit in full900 as `{claimant}_{pid}.html`
                # (v2045 1st Anniversary) while the rejected owner row never
                # wrote a catalog_provider_capture_receipt. The file is the
                # capture; refusing here left a zombie extra pid in place.
                pages_dir = (
                    ROOT / "data" / "private" / "pricecharting_session"
                    / "html" / "full900"
                )
                html_path = None
                if source_code == "pricecharting" and pages_dir.is_dir():
                    html_path = (
                        R.pc_capture_for_product(
                            pages_dir, int(claimant_variant_id), external_id,
                        )
                        or R.pc_capture_for_product(
                            pages_dir, int(owner_variant_id), external_id,
                        )
                    )
                if html_path is None or not html_path.is_file():
                    print("release-and-claim REFUSED: no catalog_provider_capture_receipt;"
                          " nothing was captured, so there is nothing to release")
                    return 1
                receipt = {
                    "capture_sha256": R.sha256_file(html_path),
                    "capture_path": str(html_path),
                    "captured_at": None,
                }

        if red_ids is None:
            red_ids = list(R.red_listed_variants())
        red = set(int(x) for x in red_ids)
        if int(owner_variant_id) in red or int(claimant_variant_id) in red:
            print(json.dumps({
                "command": "release-and-claim",
                "verdict": "keep_owner",
                "refused": "red_listed",
                "ownerVariantId": int(owner_variant_id),
                "claimantVariantId": int(claimant_variant_id),
            }, ensure_ascii=False, indent=2))
            return 1
        standing = R.operator_ruling(owner_ident.get("bind_evidence_json"))
        # A ruling protects the card's accepted identity, not a second pid the
        # same variant already replaced (v107 zombie 8506470). A ruling whose
        # action is a rejection of THIS pid (v2054 reject-wrong-printing-source
        # on 8506784: "page is variant 267's") is the opposite of keep_owner.
        with conn.cursor() as cur:
            cur.execute(
                "SELECT external_entity_id FROM catalog_source_identity"
                " WHERE source_code=%s AND variant_id=%s"
                " AND LOWER(match_status)='exact'"
                " AND external_entity_id<>%s",
                (source_code, int(owner_variant_id), external_id),
            )
            other_exact = list(cur.fetchall() or [])
        rejects_this_pid = R.rejection_is_verdict(
            owner_ident.get("bind_evidence_json")
        )
        if standing and not other_exact and not rejects_this_pid:
            print(json.dumps({
                "command": "release-and-claim",
                "verdict": "keep_owner",
                "refused": "standing_operator_ruling",
                "ruling": standing[:160],
            }, ensure_ascii=False, indent=2))
            return 1

        if identity is None:
            page_html = html
            if page_html is None:
                cap = Path(str(receipt["capture_path"]))
                if not cap.is_file():
                    print("release-and-claim REFUSED: capture_path missing on disk")
                    return 1
                page_html = cap.read_text(encoding="utf-8", errors="replace")
            if source_code == "pricecharting":
                parsed, why = R._pc_page_identity(page_html)
                if parsed is None:
                    print(f"release-and-claim REFUSED: page_parse_failed {why}")
                    return 1
                identity = parsed
            else:
                identity = {
                    "collector": "", "language": "", "setText": page_html[:200],
                    "parallel": "", "tcg": "", "heading": "", "canonicalUrl": "",
                }

        leftover = leftover_row or _load_variant_score_row(conn, int(claimant_variant_id))
        owner = owner_row or _load_variant_score_row(conn, int(owner_variant_id))
        if leftover is None or owner is None:
            print("release-and-claim REFUSED: catalog_variant missing")
            return 1

        census = None
        if source_code == "pricecharting":
            from adjudicate_pc_pop_corroboration_20260813 import psa10_pop
            page_html = html
            if page_html is None:
                cap = Path(str(receipt["capture_path"]))
                if cap.is_file():
                    page_html = cap.read_text(encoding="utf-8", errors="replace")
            if page_html:
                census = psa10_pop(page_html)

        score = adjudicate(
            identity, leftover, owner, source_code=source_code, pc_pop=census,
        )
        record = {
            "command": "release-and-claim",
            "ruledAt": ruled_at,
            "actor": actor,
            "sourceCode": source_code,
            "ownerVariantId": int(owner_variant_id),
            "claimantVariantId": int(claimant_variant_id),
            "externalEntityId": external_id,
            "ownerMatchStatus": str(owner_ident.get("match_status") or ""),
            "verdict": score["verdict"],
            "gate": score["gate"],
            "leftoverGates": score["leftoverGates"],
            "ownerGates": score["ownerGates"],
            "captureReceipt": {
                "sha256": str(receipt.get("capture_sha256") or ""),
                "path": str(receipt.get("capture_path") or ""),
            },
            "applied": False,
            "write": bool(write),
        }
        if score["verdict"] != "kick_owner":
            print(json.dumps({**record, "dryRun": not write}, ensure_ascii=False, indent=2))
            print(
                f"release-and-claim {score['verdict']}: not releasing"
                f" {source_code}/{external_id}"
            )
            return 0
        if not write:
            print(json.dumps({**record, "dryRun": True}, ensure_ascii=False, indent=2))
            print("release-and-claim DRY-RUN: nothing written. re-run with --write")
            return 0

        RULING_DIR.mkdir(parents=True, exist_ok=True)
        stamp = ruled_at.replace(":", "").replace("-", "").replace(".", "")
        path = RULING_DIR / (
            f"release-{stamp}-v{int(owner_variant_id)}-{source_code}-{external_id}.json"
        )
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM catalog_source_identity"
                " WHERE source_code=%s AND external_entity_id=%s AND variant_id=%s",
                (source_code, external_id, int(owner_variant_id)),
            )
            changed = int(cur.rowcount)
        record["rowcount"] = changed
        if changed != 1:
            conn.rollback()
            path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            print(
                f"release-and-claim ABORT: DELETE changed {changed} rows, expected 1"
            )
            return 2
        conn.commit()
        record["applied"] = True
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(record, ensure_ascii=False, indent=2))
        print(f"release-and-claim WROTE kick {source_code}/{external_id} v{owner_variant_id}")
        print(f"  receipt: {path}")
        return 0
    finally:
        if owns_conn:
            conn.close()
