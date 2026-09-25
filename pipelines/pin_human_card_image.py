#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Human card-image replacement: the one execution point for a rejected image.

DADDY 2026-09-26: the right card comes first (same card, number, parallel,
language).  Wrong-card, JP-on-EN, overlay and non-card-front images are
rejected and must be replaced; a SAMPLE-watermarked image of the right card
stays up and is replaced only when a clean image of the right card exists
(supersedes 09-25 "SAMPLE is always rejected").  Human reject is hard
authority.  A rejection
and its replacement are one decision, so they are one transaction here: a
rejection alone would drop the card to /card-placeholder.svg (sha "0"*64),
which fails the public asset sync and with it the bake.

Revived from archive/pipelines/pin_human_card_image.py (the 2026-08-11 single
pin, left there as history).  What changed and why:
  * manifest mode, many cards per run, one transaction per card;
  * dry-run is the default and opens a READ ONLY transaction; --apply writes;
  * --apply holds the orchestrator lease (operator_control.operator_e2e_lease,
    refused while the chain runs) and the chain's DB-writer lease
    (collect_control._db_writer_lease), in the order collect takes them;
  * the image is rendered exactly like the SNK EN / PC lanes
    (normalize_card_canvas -> webp q92 -> rebuild_036._materialize_trio for the
    _200/_600 derivatives), no extra rounded-corner step;
  * the result is re-read through the FE predicate (rebuild_036._fe_image_state,
    the mirror of live-db-snapshot.ts) and the card is rolled back unless the
    page would now show exactly the new sha.

Per card, --apply writes (and nothing else):
  market_image_rejection_registry  old sha, human_review_rejected:<reason>
  market_image_asset               the new content (raw_front)
  market_image_qc                  human_or_vision_confirmed / human-pin-v1
  market_canonical_image_acceptance  accepted_by='human', supersedes the head
  operator_binding_freeze          other accepted image freezes -> 'rejected'
                                   (status only), source 'human' -> accepted
No auto lane undoes this: the SNK EN lane and rebuild_036 image-bind never
supersede an acceptance they do not own / that is human, and neither
re-vouches registry content (scripts/test_pin_human_card_image.py).

Manifest: a JSON array of
  {"variantId": 123, "oldSha256": "<64 hex>", "newImagePath": "front.png",
   "sourceUrl": "...", "sourceLineage": "...",
   "rejectionReason": "sample_watermark|overlay|not_card_front|wrong_printing",
   "reviewNote": "..."}
A relative newImagePath is relative to the manifest file.  Nothing is fetched.

Run:
  python -X utf8 pipelines/pin_human_card_image.py manifest.json          # plan
  python -X utf8 pipelines/pin_human_card_image.py manifest.json --apply  # write
Exit: 0 all planned/applied, 1 a card refused or failed, 2 manifest/image refused.
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import rebuild_036  # noqa: E402
from qualified_pool_operator import db, load_env  # noqa: E402
from rebuild_036 import HUMAN_IMAGE_ACCEPTED_BY, canonical_json, sha256_bytes  # noqa: E402

CONTRACT = "human-card-image-replacement-v1"
LEASE_OWNER = "pin-human-card-image"
FREEZE_SOURCE_CODE = "human"
QC_VERSION = "human-pin-v1"
REJECTION_REASONS = frozenset({
    "sample_watermark", "overlay", "not_card_front", "wrong_printing",
})
MANIFEST_FIELDS = (
    "variantId", "oldSha256", "newImagePath", "sourceUrl", "sourceLineage",
    "rejectionReason", "reviewNote",
)
TRANSFORM = {
    "contract": "human-replacement-image-transform-v1",
    "canvas": {"width": 429, "height": 600},
    "alphaPreserved": True,
    "roundedCorners": False,
    "encoder": {"format": "webp", "quality": 92},
}
# The DB keeps hashes of the lineage/evidence documents; the documents
# themselves (source URL, lineage, review note) are kept here, one file per run.
RECEIPT_DIR = ROOT / "data" / "runtime" / "operator" / "image-replacements"
HEX64 = re.compile(r"^[0-9a-f]{64}$")

HEAD_ACCEPTANCE_SQL = (
    "SELECT ca.id, ca.lineage_sha256, ca.accepted_by"
    " FROM market_canonical_image_acceptance ca"
    " WHERE ca.variant_id=%s"
    "  AND NOT EXISTS (SELECT 1 FROM market_canonical_image_acceptance newer"
    "                  WHERE newer.supersedes_acceptance_id=ca.id)"
    " ORDER BY ca.accepted_at DESC, ca.id DESC LIMIT 1"
)


class ManifestError(ValueError):
    pass


class ApplyError(RuntimeError):
    pass


def load_manifest(path: Path) -> list[dict[str, Any]]:
    """Every problem at once; a manifest with any bad row is refused whole."""

    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"manifest unreadable: {exc}") from exc
    if not isinstance(document, list) or not document:
        raise ManifestError("manifest must be a non-empty JSON array of rows")
    problems: list[str] = []
    rows: list[dict[str, Any]] = []
    seen: set[int] = set()
    for index, raw in enumerate(document):
        where = f"row {index}"
        if not isinstance(raw, dict):
            problems.append(f"{where}: not an object")
            continue
        missing = [field for field in MANIFEST_FIELDS if field not in raw]
        if missing:
            problems.append(f"{where}: missing {missing}")
            continue
        variant = raw["variantId"]
        if isinstance(variant, bool) or not isinstance(variant, int) or variant <= 0:
            problems.append(f"{where}: variantId must be a positive integer")
            continue
        where = f"row {index} (variant {variant})"
        if variant in seen:
            problems.append(f"{where}: variant listed twice")
        seen.add(variant)
        if not isinstance(raw["oldSha256"], str) or not HEX64.fullmatch(raw["oldSha256"]):
            problems.append(f"{where}: oldSha256 must be 64 lowercase hex")
        if raw["rejectionReason"] not in REJECTION_REASONS:
            problems.append(
                f"{where}: rejectionReason must be one of {sorted(REJECTION_REASONS)}"
            )
        for field in ("newImagePath", "sourceUrl", "sourceLineage", "reviewNote"):
            if not isinstance(raw[field], str) or not raw[field].strip():
                problems.append(f"{where}: {field} must be a non-empty string")
        if isinstance(raw["newImagePath"], str) and raw["newImagePath"].strip():
            image_path = Path(raw["newImagePath"])
            if not image_path.is_absolute():
                image_path = Path(path).resolve().parent / image_path
            # No replacement, no run: rejecting without one takes the card down.
            if not image_path.is_file():
                problems.append(f"{where}: replacement image not found: {image_path}")
            row = dict(raw)
            row["newImagePath"] = str(image_path)
            rows.append(row)
    if problems:
        raise ManifestError("; ".join(problems))
    return rows


def render_replacement(raw: bytes) -> dict[str, Any]:
    """Same canvas and encoder as the SNK EN and PC lanes (no rounded corners)."""

    import g10_public_snapshot as g10
    from PIL import Image
    from native_image_resolver import normalize_card_canvas

    if not raw:
        raise ValueError("replacement image is empty")
    with Image.open(io.BytesIO(raw)) as opened:
        normalized = normalize_card_canvas(opened.copy())
    content = g10._save_webp(normalized, 92)
    return {
        "content": content,
        "contentSha256": sha256_bytes(content),
        "sourceSha256": sha256_bytes(raw),
        "width": normalized.width,
        "height": normalized.height,
        "transformSha256": sha256_bytes(canonical_json(TRANSFORM)),
    }


def read_card(conn, variant_id: int, old_sha: str) -> dict[str, Any]:
    """What the FE shows now, and what a replacement would supersede."""

    state = rebuild_036._fe_image_state(conn, [variant_id]).get(variant_id)
    rejected = rebuild_036._image_rejections(conn, [variant_id]).get(variant_id, set())
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM market_image_asset"
            " WHERE variant_id=%s AND image_kind='raw_front' AND content_sha256=%s"
            " LIMIT 1",
            (variant_id, old_sha),
        )
        old_asset = cur.fetchone()
        cur.execute(HEAD_ACCEPTANCE_SQL, (variant_id,))
        head = cur.fetchone()
    return {
        "published": str(state["contentSha256"]) if state else None,
        "rejected": set(rejected),
        "oldAssetId": int(old_asset["id"]) if old_asset else None,
        "headAcceptanceId": int(head["id"]) if head else None,
        "headAcceptedBy": str(head["accepted_by"]) if head else None,
    }


def decide(row: Mapping[str, Any], rendered: Mapping[str, Any],
           facts: Mapping[str, Any]) -> tuple[str, str]:
    """('replace'|'skip'|'refuse', reason).  Pure, so it is tested directly."""

    old_sha = str(row["oldSha256"])
    new_sha = str(rendered["contentSha256"])
    if new_sha == old_sha:
        return "refuse", "replacement_identical_to_rejected"
    if new_sha in facts["rejected"]:
        return "refuse", "replacement_is_human_rejected"
    if facts["published"] == new_sha:
        return "skip", "already_applied"
    if facts["oldAssetId"] is None:
        return "refuse", "old_sha_not_an_asset_of_variant"
    # Stale manifest guard: never overwrite an image someone changed since the
    # review.  None = the card shows no image now; replacing it only helps.
    if facts["published"] not in (old_sha, None):
        return "refuse", f"stale_manifest:published={facts['published']}"
    return "replace", "ok"


def card_documents(row: Mapping[str, Any], rendered: Mapping[str, Any], *,
                   run_id: str) -> dict[str, Any]:
    variant_id = int(row["variantId"])
    new_sha = str(rendered["contentSha256"])
    # Scoped by variant and run: uq_canonical_image_lineage is global, and the
    # same scan may legitimately be pinned twice (another variant, a re-pin).
    lineage = {
        "contract": CONTRACT,
        "variantId": variant_id,
        "reviewRunId": run_id,
        "replacesContentSha256": row["oldSha256"],
        "sourceUrl": row["sourceUrl"],
        "sourceLineage": row["sourceLineage"],
        "sourceBytesSha256": rendered["sourceSha256"],
        "transformSha256": rendered["transformSha256"],
        "contentSha256": new_sha,
    }
    lineage_sha = sha256_bytes(canonical_json(lineage))
    evidence = {
        "contract": "canonical-human-image-acceptance-v1",
        "lineageSha256": lineage_sha,
        "imageContentSha256": new_sha,
        "qcVersion": QC_VERSION,
        "rejectionReason": row["rejectionReason"],
        "reviewNote": row["reviewNote"],
    }
    decision = {
        "contract": "human-image-rejection-v1",
        "variantId": variant_id,
        "contentSha256": row["oldSha256"],
        "rejectionReason": row["rejectionReason"],
        "reviewNote": row["reviewNote"],
        "replacementContentSha256": new_sha,
        "reviewRunId": run_id,
    }
    return {
        "lineage": lineage,
        "lineageSha256": lineage_sha,
        "evidence": evidence,
        "evidenceSha256": sha256_bytes(canonical_json(evidence)),
        "decision": decision,
        "decisionSha256": sha256_bytes(canonical_json(decision)),
        "assetSourceVersionSha256": sha256_bytes(canonical_json({
            "sourceBytesSha256": rendered["sourceSha256"],
            "transformSha256": rendered["transformSha256"],
        })),
    }


def _write_public_trio(sha: str, content: bytes) -> str | None:
    """{sha}.webp + _200 + _600 under data/public/market-assets (content-addressed)."""

    return rebuild_036._materialize_trio(sha, content)


def verify_published(conn, variant_id: int, new_sha: str) -> None:
    """Read back through the FE predicate, inside the card's own transaction."""

    state = rebuild_036._fe_image_state(conn, [variant_id]).get(variant_id)
    got = str(state["contentSha256"]) if state else None
    if got != new_sha:
        raise ApplyError(f"variant {variant_id}: FE would show {got}, not {new_sha}")
    with conn.cursor() as cur:
        cur.execute(
            "SELECT source_code FROM operator_binding_freeze"
            " WHERE variant_id=%s AND freeze_kind='image' AND acceptance_status='accepted'",
            (variant_id,),
        )
        sources = sorted(str(item["source_code"]) for item in cur.fetchall())
    if sources != [FREEZE_SOURCE_CODE]:
        raise ApplyError(f"variant {variant_id}: accepted image freezes are {sources}")


def apply_card(conn, row: Mapping[str, Any], rendered: Mapping[str, Any],
               facts: Mapping[str, Any], *, run_id: str, now: datetime) -> dict[str, Any]:
    """One card, one transaction; rolled back unless the FE shows the new sha."""

    variant_id = int(row["variantId"])
    old_sha = str(row["oldSha256"])
    new_sha = str(rendered["contentSha256"])
    docs = card_documents(row, rendered, run_id=run_id)
    # Files first, like the lanes: a DB row must never name bytes that are not
    # on disk.  Content-addressed, so a rolled-back card leaves only an
    # unreferenced file, never a changed one.
    failure = _write_public_trio(new_sha, rendered["content"])
    if failure:
        conn.rollback()
        raise ApplyError(f"variant {variant_id}: public webp trio: {failure}")
    note = (
        f"human replacement ({row['rejectionReason']}): {row['reviewNote']}"
        f" | source: {row['sourceUrl']}"
    )[:1000]
    try:
        with conn.cursor() as cur:
            # First verdict wins: a later run never rewrites why it was rejected.
            cur.execute(
                "INSERT INTO market_image_rejection_registry"
                " (variant_id,content_sha256,first_image_asset_id,rejection_reason,"
                "  decision_code_sha256,review_run_id,rejected_at)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s)"
                " ON DUPLICATE KEY UPDATE variant_id=variant_id",
                (variant_id, old_sha, facts["oldAssetId"],
                 f"human_review_rejected:{row['rejectionReason']}",
                 docs["decisionSha256"], run_id, now),
            )
            cur.execute(
                "INSERT INTO market_image_asset"
                " (variant_id,image_kind,content_sha256,private_object_key,mime_type,"
                "  width_px,height_px,source_version_sha256,captured_at)"
                " VALUES (%s,'raw_front',%s,%s,'image/webp',%s,%s,%s,%s)"
                " ON DUPLICATE KEY UPDATE"
                "  private_object_key=VALUES(private_object_key),mime_type=VALUES(mime_type),"
                "  width_px=VALUES(width_px),height_px=VALUES(height_px),"
                "  source_version_sha256=VALUES(source_version_sha256),"
                "  captured_at=GREATEST(captured_at,VALUES(captured_at))",
                (variant_id, new_sha, f"data/public/market-assets/{new_sha}.webp",
                 rendered["width"], rendered["height"],
                 docs["assetSourceVersionSha256"], now),
            )
            cur.execute(
                "SELECT id FROM market_image_asset"
                " WHERE variant_id=%s AND image_kind='raw_front' AND content_sha256=%s"
                " LIMIT 1",
                (variant_id, new_sha),
            )
            asset = cur.fetchone()
            if not asset:
                raise ApplyError(f"variant {variant_id}: asset row missing after upsert")
            asset_id = int(asset["id"])
            cur.execute(
                "INSERT INTO market_image_qc"
                " (image_asset_id,semantic_match_status,card_number_match,language_match,"
                "  tcg_match,raw_front_confirmed,public_allowed,rejection_reason,"
                "  checked_at,qc_version)"
                " VALUES (%s,'human_or_vision_confirmed',1,1,1,1,1,NULL,%s,%s)"
                " ON DUPLICATE KEY UPDATE"
                "  semantic_match_status='human_or_vision_confirmed',card_number_match=1,"
                "  language_match=1,tcg_match=1,raw_front_confirmed=1,"
                "  public_allowed=1,rejection_reason=NULL,checked_at=VALUES(checked_at)",
                (asset_id, now, QC_VERSION),
            )
            # Fallback branch of the projection view: no storefront lineage,
            # lineage == fallback version, path free of 'snkrdunk'.  Written
            # by image_lane_policy (the only writer of the table); it
            # supersedes the head it reads in this transaction, and a human
            # lane is held only by the registry, which decide() already
            # refused -- a hold here raises and the card rolls back.
            from image_lane_policy import accept_canonical_image

            acceptance_id = accept_canonical_image(
                cur,
                variant_id=variant_id,
                image_asset_id=asset_id,
                content_sha256=new_sha,
                lineage_sha256=docs["lineageSha256"],
                evidence_sha256=docs["evidenceSha256"],
                accepted_by=HUMAN_IMAGE_ACCEPTED_BY,
                accepted_at=now,
                fallback_source_path=f"human-designated:{run_id}:{variant_id}",
                fallback_source_version_sha256=docs["lineageSha256"],
                fallback_source_observed_at=now,
                raise_on_hold=True,
            ).acceptance_id
            # One accepted image freeze per card: the FE fallback takes the
            # first row it meets, so a second accepted row is not a pin.
            cur.execute(
                "UPDATE operator_binding_freeze"
                " SET acceptance_status='rejected',actor=%s,note=%s"
                " WHERE variant_id=%s AND freeze_kind='image'"
                "  AND acceptance_status='accepted' AND source_code<>%s",
                (f"pin_human_card_image:{run_id}",
                 f"superseded by human replacement {run_id}",
                 variant_id, FREEZE_SOURCE_CODE),
            )
            demoted = int(cur.rowcount or 0)
            cur.execute(
                "INSERT INTO operator_binding_freeze"
                " (variant_id,freeze_kind,source_code,external_entity_id,content_sha256,"
                "  canonical_image_acceptance_id,accepted_lineage_sha256,acceptance_status,"
                "  actor,evidence_sha256,note,accepted_at)"
                " VALUES (%s,'image',%s,%s,%s,%s,%s,'accepted',%s,%s,%s,%s)"
                " ON DUPLICATE KEY UPDATE"
                "  external_entity_id=VALUES(external_entity_id),"
                "  content_sha256=VALUES(content_sha256),"
                "  canonical_image_acceptance_id=VALUES(canonical_image_acceptance_id),"
                "  accepted_lineage_sha256=VALUES(accepted_lineage_sha256),"
                "  acceptance_status='accepted',actor=VALUES(actor),"
                "  evidence_sha256=VALUES(evidence_sha256),note=VALUES(note),"
                "  accepted_at=VALUES(accepted_at)",
                (variant_id, FREEZE_SOURCE_CODE, run_id, new_sha, acceptance_id,
                 docs["lineageSha256"], HUMAN_IMAGE_ACCEPTED_BY,
                 docs["evidenceSha256"], note, now),
            )
        verify_published(conn, variant_id, new_sha)
    except Exception:
        conn.rollback()
        raise
    conn.commit()
    return {
        "variantId": variant_id,
        "action": "replaced",
        "oldSha256": old_sha,
        "newSha256": new_sha,
        "assetId": asset_id,
        "acceptanceId": acceptance_id,
        "supersededAcceptanceId": facts["headAcceptanceId"],
        "demotedFreezeRows": demoted,
        **{key: docs[key] for key in ("lineage", "lineageSha256", "evidence",
                                      "evidenceSha256", "decision", "decisionSha256")},
    }


@contextmanager
def _chain_leases():
    """Same two leases, same order, as a mutating collect command."""

    from collect_control import _db_writer_lease
    from operator_control import operator_e2e_lease

    with operator_e2e_lease(LEASE_OWNER), _db_writer_lease():
        yield


def _write_receipt(run_id: str, document: Mapping[str, Any]) -> Path:
    RECEIPT_DIR.mkdir(parents=True, exist_ok=True)
    path = RECEIPT_DIR / f"{run_id}.json"
    temporary = path.with_name(f".{path.name}.next")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
    return path


def _plan_line(row: Mapping[str, Any], rendered: Mapping[str, Any],
               facts: Mapping[str, Any], action: str, reason: str) -> dict[str, Any]:
    return {
        "variantId": int(row["variantId"]),
        "action": action,
        "reason": reason,
        "oldSha256": row["oldSha256"],
        "newSha256": rendered["contentSha256"],
        "published": facts["published"],
        "supersedesAcceptanceId": facts["headAcceptanceId"],
        "supersedesAcceptedBy": facts["headAcceptedBy"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Replace human-rejected card images from a manifest (dry-run by default)"
    )
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--apply", action="store_true",
                        help="write; without it the run opens a READ ONLY transaction")
    args = parser.parse_args(argv)

    try:
        rows = load_manifest(args.manifest)
        cards = []
        for row in rows:
            try:
                rendered = render_replacement(Path(row["newImagePath"]).read_bytes())
            except Exception as exc:  # noqa: BLE001 - any undecodable file refuses the run
                raise ManifestError(
                    f"variant {row['variantId']}: replacement image unusable: {exc}"
                ) from exc
            cards.append((row, rendered))
    except ManifestError as exc:
        print(json.dumps({"refused": str(exc)}, ensure_ascii=False, indent=2))
        return 2

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    run_id = f"human-image-replacement-{now.strftime('%Y%m%dT%H%M%S%fZ')}"
    manifest_sha = sha256_bytes(Path(args.manifest).read_bytes())

    if not args.apply:
        load_env()
        conn = db()
        try:
            with conn.cursor() as cur:
                cur.execute("SET SESSION TRANSACTION READ ONLY")
                cur.execute("START TRANSACTION READ ONLY")
            plan = []
            for row, rendered in cards:
                facts = read_card(conn, int(row["variantId"]), str(row["oldSha256"]))
                plan.append(_plan_line(row, rendered, facts, *decide(row, rendered, facts)))
            conn.rollback()
        finally:
            conn.close()
        print(json.dumps({"mode": "dry-run", "manifestSha256": manifest_sha,
                          "cards": plan}, ensure_ascii=False, indent=2))
        return 1 if any(item["action"] == "refuse" for item in plan) else 0

    results: list[dict[str, Any]] = []
    receipt = {
        "contract": f"{CONTRACT}-receipt",
        "reviewRunId": run_id,
        "manifest": str(args.manifest),
        "manifestSha256": manifest_sha,
        "startedAt": now.isoformat(timespec="microseconds"),
        "cards": results,
    }
    failed = False
    with _chain_leases():
        load_env()
        conn = db()
        try:
            # Whole manifest first: any refusal stops the run before the first write.
            plan = []
            for row, rendered in cards:
                facts = read_card(conn, int(row["variantId"]), str(row["oldSha256"]))
                plan.append(_plan_line(row, rendered, facts, *decide(row, rendered, facts)))
            conn.rollback()
            if any(item["action"] == "refuse" for item in plan):
                print(json.dumps({"mode": "apply", "refused": plan},
                                 ensure_ascii=False, indent=2))
                return 1
            for row, rendered in cards:
                # Re-read inside this card's transaction: the plan above is
                # only a pre-check, the write acts on what is there now.
                facts = read_card(conn, int(row["variantId"]), str(row["oldSha256"]))
                action, reason = decide(row, rendered, facts)
                if action != "replace":
                    conn.rollback()
                    results.append(_plan_line(row, rendered, facts, action, reason))
                    if action == "refuse":
                        failed = True
                        break
                    continue
                try:
                    results.append(apply_card(conn, row, rendered, facts,
                                              run_id=run_id, now=now))
                except Exception as exc:  # noqa: BLE001 - reported, run stops
                    results.append({"variantId": int(row["variantId"]),
                                    "action": "failed", "reason": str(exc)[:500]})
                    failed = True
                    break
                finally:
                    _write_receipt(run_id, receipt)
        finally:
            conn.close()
    _write_receipt(run_id, receipt)
    print(json.dumps({"mode": "apply", "reviewRunId": run_id, "cards": [
        {key: item.get(key) for key in ("variantId", "action", "reason",
                                        "oldSha256", "newSha256", "acceptanceId")}
        for item in results
    ]}, ensure_ascii=False, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
