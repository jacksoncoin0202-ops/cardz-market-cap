#!/usr/bin/env python3
"""Capture one SNK product page as exact identity evidence.

The command is read-only by default.  ``--write`` creates one pending
``market_identity_review_queue`` row; the existing ``identity_review.py``
command remains the only authority that can accept the binding.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from failure_ledger import record_failure, record_resolution  # noqa: E402


SOURCE_CODE = "snkrdunk"
REASON_CODE = "snk_page_exact_identity_review"
OUTPUT_ROOT = ROOT / "data/runtime/private-reports/snk-page-identity"


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _plain_text(value: str) -> str:
    without_tags = re.sub(r"<[^>]+>", " ", value)
    return " ".join(html.unescape(without_tags).split())


def _first_tag(body: str, tag: str) -> str:
    match = re.search(rf"<{tag}\b[^>]*>(.*?)</{tag}>", body, re.I | re.S)
    return _plain_text(match.group(1)) if match else ""


def _normalized_words(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    value = re.sub(r"[^0-9a-z\u3040-\u30ff\u3400-\u9fff]+", " ", value)
    return " ".join(value.split())


def _collector_key(value: str) -> str:
    return re.sub(r"[^0-9a-z/]+", "", unicodedata.normalize("NFKC", value).casefold())


def parse_page(snk_id: int, body: str) -> dict[str, Any]:
    h1 = _first_tag(body, "h1")
    title = _first_tag(body, "title")
    if not h1:
        raise ValueError("snk_page_h1_missing")
    urls = sorted(
        set(
            html.unescape(url)
            for url in re.findall(
                r"https://cdn\.snkrdunk\.com/upload_bg_removed/[^\"'<> ]+",
                body,
                re.I,
            )
        )
    )
    if not urls:
        raise ValueError("snk_page_clean_image_missing")
    image_url = urls[0]
    parsed = urlsplit(image_url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "cdn.snkrdunk.com"
        or not parsed.path.startswith("/upload_bg_removed/")
    ):
        raise ValueError("snk_page_image_host_invalid")
    return {
        "snkId": int(snk_id),
        "pageUrl": f"https://snkrdunk.com/apparels/{int(snk_id)}/sales-histories",
        "h1": h1,
        "title": title,
        "imageUrl": image_url,
    }


def validate_page(
    page: Mapping[str, Any],
    *,
    expected_collector: str,
    expected_language: str,
    required_tokens: Sequence[str],
) -> None:
    visible = f"{page.get('h1') or ''} {page.get('title') or ''}"
    collector_key = _collector_key(expected_collector)
    if collector_key.isdigit():
        raise ValueError(f"snk_page_expected_collector_incomplete:{expected_collector}")
    if collector_key not in _collector_key(visible):
        raise ValueError(f"snk_page_collector_mismatch:{expected_collector}")
    language = expected_language.casefold()
    english_marker = bool(
        re.search(r"(?:^|[^A-Z0-9])EN(?:[^A-Z0-9]|$)", visible.upper())
        or "英語版" in visible
    )
    if language == "en" and not english_marker:
        raise ValueError("snk_page_language_mismatch:expected_en")
    if language == "ja" and english_marker:
        raise ValueError("snk_page_language_mismatch:expected_ja")
    normalized_visible = _normalized_words(visible)
    for token in required_tokens:
        normalized_token = _normalized_words(token)
        if not normalized_token or normalized_token not in normalized_visible:
            raise ValueError(f"snk_page_required_token_missing:{token}")


def fetch_page(snk_id: int) -> dict[str, Any]:
    url = f"https://snkrdunk.com/apparels/{int(snk_id)}/sales-histories"
    response = requests.get(
        url,
        timeout=30,
        headers={"User-Agent": "Mozilla/5.0 CardzMarketCap/1.0"},
    )
    response.raise_for_status()
    return parse_page(snk_id, response.text)


def load_variant(cursor: Any, variant_id: int, *, lock: bool) -> dict[str, Any]:
    suffix = " FOR UPDATE" if lock else ""
    cursor.execute(
        f"""
        SELECT id, opaque_id, tcg_code, card_language, canonical_name,
               set_name, collector_number, identity_status
        FROM catalog_variant
        WHERE id=%s{suffix}
        """,
        (variant_id,),
    )
    row = cursor.fetchone()
    if row is None:
        raise ValueError(f"catalog_variant_missing:{variant_id}")
    if str(row["identity_status"]) == "alias":
        raise ValueError(f"catalog_variant_is_alias:{variant_id}")
    return dict(row)


def assert_source_owner(cursor: Any, *, snk_id: int, variant_id: int) -> str:
    cursor.execute(
        """
        SELECT variant_id
        FROM catalog_source_identity
        WHERE source_code=%s AND external_entity_id=%s
        FOR UPDATE
        """,
        (SOURCE_CODE, str(snk_id)),
    )
    owner = cursor.fetchone()
    if owner is None:
        return "unowned"
    owner_id = int(owner["variant_id"])
    if owner_id != variant_id:
        raise ValueError(f"snk_identity_owned_by_other_variant:{owner_id}")
    return "already_owned"


def enqueue_review(
    connection: Any,
    *,
    snk_id: int,
    variant_id: int,
    evidence_sha256: str,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    run_key = sha256(
        {
            "pipeline": "snk_page_identity",
            "snkId": snk_id,
            "variantId": variant_id,
            "evidenceSha256": evidence_sha256,
        }
    )
    with connection.cursor() as cursor:
        load_variant(cursor, variant_id, lock=True)
        owner_status = assert_source_owner(
            cursor,
            snk_id=snk_id,
            variant_id=variant_id,
        )
        if owner_status == "already_owned":
            connection.rollback()
            return {"ownerStatus": owner_status, "reviewId": None, "runId": None}
        cursor.execute("SELECT id FROM market_ingest_run WHERE run_key=%s", (run_key,))
        run = cursor.fetchone()
        if run is None:
            cursor.execute(
                """
                INSERT INTO market_ingest_run
                    (run_key,source_code,ingest_mode,effective_at,payload_sha256,
                     manifest_sha256,status,observed_count,accepted_count,
                     quarantined_count,rejected_count,started_at,completed_at)
                VALUES (%s,%s,'backfill',%s,%s,%s,'complete',1,0,1,0,%s,%s)
                """,
                (
                    run_key,
                    SOURCE_CODE,
                    now,
                    evidence_sha256,
                    evidence_sha256,
                    now,
                    now,
                ),
            )
            run_id = int(cursor.lastrowid)
        else:
            run_id = int(run["id"])
        cursor.execute(
            """
            INSERT IGNORE INTO market_identity_review_queue
                (run_id,source_code,external_entity_id,reason_code,evidence_sha256,status)
            VALUES (%s,%s,%s,%s,%s,'pending')
            """,
            (run_id, SOURCE_CODE, str(snk_id), REASON_CODE, evidence_sha256),
        )
        cursor.execute(
            """
            SELECT id,status,resolved_variant_id
            FROM market_identity_review_queue
            WHERE source_code=%s AND external_entity_id=%s
              AND reason_code=%s AND evidence_sha256=%s
            """,
            (SOURCE_CODE, str(snk_id), REASON_CODE, evidence_sha256),
        )
        review = cursor.fetchone()
        if review is None:
            raise RuntimeError("snk_identity_review_enqueue_failed")
    connection.commit()
    return {
        "ownerStatus": owner_status,
        "reviewId": int(review["id"]),
        "reviewStatus": str(review["status"]),
        "resolvedVariantId": review["resolved_variant_id"],
        "runId": run_id,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snk-id", type=int, required=True)
    parser.add_argument("--variant-id", type=int, required=True)
    parser.add_argument("--expected-collector", required=True)
    parser.add_argument("--expected-language", choices=["en", "ja"], required=True)
    parser.add_argument("--require-token", action="append", default=[])
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    item_key = f"snk:{args.snk_id}:variant:{args.variant_id}"
    try:
        page = fetch_page(args.snk_id)
        validate_page(
            page,
            expected_collector=args.expected_collector,
            expected_language=args.expected_language,
            required_tokens=args.require_token,
        )
        from snk_image_ingest import db

        connection = db()
        try:
            with connection.cursor() as cursor:
                variant = load_variant(cursor, args.variant_id, lock=False)
            connection.rollback()
            evidence = {
                "schemaVersion": 1,
                "source": SOURCE_CODE,
                "page": page,
                "variant": variant,
                "expectations": {
                    "collector": args.expected_collector,
                    "language": args.expected_language,
                    "requiredTokens": list(args.require_token),
                },
            }
            evidence_hash = sha256(evidence)
            queue = (
                enqueue_review(
                    connection,
                    snk_id=args.snk_id,
                    variant_id=args.variant_id,
                    evidence_sha256=evidence_hash,
                )
                if args.write
                else {
                    "ownerStatus": "not_checked_for_update",
                    "reviewId": None,
                    "runId": None,
                }
            )
        finally:
            connection.close()
        destination = (
            OUTPUT_ROOT
            / f"snk-{args.snk_id}-variant-{args.variant_id}-{evidence_hash[:12]}"
            / ("queued.json" if args.write else "dry-run.json")
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        report = {
            "write": args.write,
            "evidenceSha256": evidence_hash,
            "evidence": evidence,
            "queue": queue,
            "receipt": str(destination),
        }
        destination.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        record_resolution(
            source=SOURCE_CODE,
            stage="page_identity",
            script=Path(__file__),
            item_key=item_key,
            resolution="page_identity_evidence_verified",
            context={"variantId": args.variant_id, "snkId": args.snk_id},
            evidence_paths=[destination],
        )
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except Exception as error:
        failure_path = record_failure(
            source=SOURCE_CODE,
            stage="page_identity",
            script=Path(__file__),
            item_key=item_key,
            reason_code=type(error).__name__.casefold(),
            message=str(error),
            retryable=True,
            url=f"https://snkrdunk.com/apparels/{args.snk_id}/sales-histories",
            context={
                "variantId": args.variant_id,
                "snkId": args.snk_id,
                "expectedCollector": args.expected_collector,
                "expectedLanguage": args.expected_language,
                "requiredTokens": args.require_token,
            },
            next_action="agent_review",
            error_type=type(error).__name__,
        )
        print(
            json.dumps(
                {
                    "error": str(error),
                    "failureReceipt": str(failure_path) if failure_path else None,
                },
                ensure_ascii=False,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
