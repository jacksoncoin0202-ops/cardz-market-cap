#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Copy the 034 audit sheet's red ruling onto the rows it governs.

Thirteen cards on that sheet were read by a human and refused. psa_identity_repair
enforces the ruling through a `red_ids` set built from the sheet manifest, and
rejects every non-gemrate binding on those cards -- but it wrote nothing onto the
rows, so a row governed by a human decision looked exactly like a row caught in
collateral quarantine. On 2026-08-09 a reverify pass that had learned to
reconsider collateral promoted seven of the thirteen. validator034's red13
invariant caught it before activation, which is one gate later than it should
have been noticed.

This puts the ruling where every lane already looks: R.REJECTION_RED_LIST_KEY on
the binding's own evidence, which R.rejection_is_verdict and
R.NOT_A_REJECTION_VERDICT_SQL both honour. Cards refused by a human stop
depending on a set literal in one pipeline.

Idempotent: a row already carrying the stamp is skipped, so the nested
previousEvidence never grows a second layer. Run it again after any restore.

    python -X utf8 scripts/stamp_red_sheet_quarantine.py [--write]

Writes need the rebuild credentials while the database is frozen; pass
--credentials-env to override.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "pipelines"))

import psa_identity_repair as REPAIR  # noqa: E402
import rebuild_036 as R  # noqa: E402
import validate_psa_identity_repair as V  # noqa: E402

REASON = (
    "034 audit sheet red row: this card was read and refused; every non-gemrate"
    " binding stays quarantined and its market data stays out of the world"
)


RELEASE_036 = ROOT / "data" / "editorial" / "red-sheet-036-release.json"


def _release_lists() -> tuple[set[int], set[int]] | None:
    """Return the later ruling when it carries the complete red-sheet state.

    New release documents record both sides of the ruling.  That makes the
    document self-contained after the machine-private 034 audit is retired.
    Older release documents only named released ids and still need the audit.
    """

    if not RELEASE_036.is_file():
        return None
    payload = json.loads(RELEASE_036.read_text(encoding="utf-8-sig"))
    if payload.get("contract") != "red-sheet-036-release-v1":
        raise SystemExit(f"red-sheet-036-release contract invalid: {RELEASE_036}")
    if "stillRedVariantIds" not in payload:
        return None
    released = {int(x) for x in payload.get("releasedVariantIds") or []}
    still_red = {int(x) for x in payload.get("stillRedVariantIds") or []}
    overlap = released & still_red
    if overlap:
        raise SystemExit(
            "red-sheet-036-release lists overlap: " + ",".join(map(str, sorted(overlap)))
        )
    historical = released | still_red
    if len(historical) != 13:
        raise SystemExit(
            f"expected 13 red variants in later ruling, got {len(historical)}: "
            f"{sorted(historical)}"
        )
    return released, still_red


def red_variant_ids() -> list[int]:
    """The same thirteen ids validator034 and psa_identity_repair derive.

    Prefer the private audit and sheet manifest while they exist.  Once that
    machine-private evidence is retired, the later self-contained 036 ruling
    preserves the same historical set without making daily runs depend on an
    ignored runtime file.
    """

    audit_path = V.OUT_034 / "audit.json"
    if not audit_path.is_file():
        release_lists = _release_lists()
        if release_lists is not None:
            released, still_red = release_lists
            return sorted(released | still_red)

    audit = V.load_audit(audit_path)
    sheet = V.load_sheet_manifest()
    by_old_name: dict[str, list[dict]] = {}
    for row in audit["rows"]:
        by_old_name.setdefault(str(row["oldCanonicalName"]), []).append(row)
    ids = {
        int(by_old_name[sheet["oldCanonicalNames"][sheet_row - 5]][0]["variantId"])
        for sheet_row in sheet["redSheetRows"]
    }
    if len(ids) != 13:
        raise SystemExit(f"expected 13 red variants, derived {len(ids)}: {sorted(ids)}")
    return sorted(ids)


def released_red_variant_ids() -> set[int]:
    """036 operator release: subset of red_variant_ids() that a later ruling unbound.

    The 034 sheet still derives thirteen. Cards a 036 operator identified
    (shape 21 TR/SP/AA) are subtracted here so stamp/repair/validator only
    quarantine what is still refused.
    """

    release_lists = _release_lists()
    if release_lists is not None:
        return release_lists[0]
    if not RELEASE_036.is_file():
        return set()
    payload = json.loads(RELEASE_036.read_text(encoding="utf-8-sig"))
    if payload.get("contract") != "red-sheet-036-release-v1":
        raise SystemExit(f"red-sheet-036-release contract invalid: {RELEASE_036}")
    allowed = set(red_variant_ids())
    released = {int(x) for x in payload.get("releasedVariantIds") or []}
    return released & allowed


def active_red_variant_ids() -> list[int]:
    release_lists = _release_lists()
    if release_lists is not None:
        return sorted(release_lists[1])
    return sorted(set(red_variant_ids()) - released_red_variant_ids())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--credentials-env", dest="credentials_env", default="")
    args = parser.parse_args(argv)

    historical = red_variant_ids()
    red_ids = active_red_variant_ids()
    credentials = (
        Path(args.credentials_env) if args.credentials_env
        else (R.DEFAULT_CREDENTIALS_ENV if args.write else R.DAILY_CREDENTIALS_ENV)
    )
    report: dict[str, object] = {
        "historicalRedVariantIds": historical,
        "redVariantIds": red_ids,
        "releasedRedVariantIds": sorted(released_red_variant_ids()),
        "write": bool(args.write),
    }
    if not red_ids:
        print(json.dumps(report, ensure_ascii=False, indent=1, default=str))
        return 0
    marks = ",".join(["%s"] * len(red_ids))
    conn = R.connect(credentials)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT variant_id, source_code, external_entity_id, match_status"
                " FROM catalog_source_identity"
                f" WHERE variant_id IN ({marks}) AND source_code<>'gemrate'"
                f"   AND {R.NOT_A_REJECTION_VERDICT_SQL}"
                " ORDER BY variant_id, source_code",
                tuple(red_ids),
            )
            unstamped = cur.fetchall()
        report["unstamped"] = [
            {
                "variantId": int(r["variant_id"]),
                "sourceCode": str(r["source_code"]),
                "externalEntityId": str(r["external_entity_id"]),
                "matchStatus": str(r["match_status"]),
            }
            for r in unstamped
        ]
        report["wouldRevokeExact"] = sum(
            1 for r in unstamped if str(r["match_status"]) == "exact"
        )

        if args.write and unstamped:
            try:
                with conn.cursor() as cur:
                    stamped = 0
                    quarantined: dict[str, int] = {}
                    for variant_id in red_ids:
                        # bind_evidence_json is assigned before match_status so it
                        # records the status this statement is about to replace.
                        cur.execute(
                            """UPDATE catalog_source_identity
                                  SET bind_evidence_json=JSON_OBJECT(
                                        'contract', %s,
                                        'action', 'quarantine-unresolved-variant-identity',
                                        %s, TRUE,
                                        'reasonCode', 'red_sheet_row',
                                        'reason', %s,
                                        'quarantinedAt', UTC_TIMESTAMP(),
                                        'previousMatchStatus', match_status,
                                        'previousEvidence', bind_evidence_json),
                                      match_status='rejected'
                                WHERE variant_id=%s AND source_code<>'gemrate'
                                  AND """
                            + R.NOT_A_REJECTION_VERDICT_SQL,
                            (
                                REPAIR.CONTRACT, R.REJECTION_RED_LIST_KEY,
                                REASON, variant_id,
                            ),
                        )
                        stamped += int(cur.rowcount)
                        # Identity alone does not undo a promotion: the stages that
                        # ran after it wrote prices, sales and image pointers, and
                        # validator034 reads those, not the binding.
                        for key, value in REPAIR._quarantine_variant(
                            cur, variant_id, REASON,
                        ).items():
                            quarantined[key] = quarantined.get(key, 0) + int(value)
                conn.commit()
                report["stamped"] = stamped
                report["quarantined"] = quarantined
            except Exception:
                conn.rollback()
                raise
    finally:
        conn.close()
    print(json.dumps(report, ensure_ascii=False, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
