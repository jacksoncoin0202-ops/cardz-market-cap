#!/usr/bin/env python3
"""Local-only, resumable VLM image-review receipts; never writes the DB.

The only eligible inputs are Tier A assets: current raw_front, source_id_exact,
one aligned source pointer, and a unique content hash.  A receipt is merely a
vision observation.  A later validator/materializer must decide whether it can
become a public semantic-QC promotion.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from pathlib import Path
from io import BytesIO
from typing import Any, Callable, Iterator, Mapping, Sequence

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipelines.failure_ledger import record_failure, record_resolution
from pipelines.image_geometry_qc import inspect_path as inspect_image_geometry

REPORT_ROOT = ROOT / "data" / "runtime" / "private-reports" / "canonical-db-qc"
OUTPUT_DIR = ROOT / "data" / "runtime" / "private-reports" / "local-vlm-image-review"
MODEL = "qwen3.5-ov:9b"
PIPELINE_VERSION = "local-vlm-card-review-v3-geometry-gated-bottom-crop"
OLLAMA = "http://127.0.0.1:11434"
SHA = re.compile(r"^[0-9a-f]{64}$")
FULL_PROMPT = (
    "Inspect this trading-card front independently. Return ONLY a JSON object with "
    "cardName, collectorNumber, language, finish, edition, parallel. Use lowercase "
    "language code en/ja when readable; use unknown for any field not visibly certain."
)
CROP_PROMPT = (
    "This is an enlarged lower section of one trading card. Read ONLY the printed "
    "collector number and language. Return ONLY JSON: "
    '{"collectorNumber": string|"unknown", "language": "en"|"ja"|"unknown"}. '
    "Do not guess."
)
PROMPTS = {"full": FULL_PROMPT, "collectorCrop": CROP_PROMPT}


def load_db_env() -> None:
    path = ROOT / "data" / "runtime" / "config" / "backend.env"
    for line in path.read_text(encoding="utf-8").splitlines() if path.is_file() else []:
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def latest_report_path(root: Path = REPORT_ROOT) -> Path:
    reports = sorted(
        root.glob("*/report.json"),
        key=lambda path: (path.stat().st_mtime_ns, path.as_posix()),
        reverse=True,
    )
    if not reports:
        raise RuntimeError(f"canonical_qc_report_missing:{root}")
    return reports[0]


def canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def normalise_number(value: Any) -> str:
    return re.sub(r"[^0-9a-z]", "", str(value or "").casefold()).lstrip("0")


def parse_json_object(text: str) -> dict[str, Any] | None:
    text = str(text or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def validate_observation(observed: Mapping[str, Any] | None, expected: Mapping[str, Any]) -> tuple[str, list[str]]:
    if observed is None:
        return "reject", ["response_not_json_object"]
    reasons: list[str] = []
    expected_number = expected.get("collectorNumber", expected.get("collector_number"))
    expected_language = expected.get("cardLanguage", expected.get("card_language"))
    if normalise_number(observed.get("collectorNumber")) != normalise_number(expected_number):
        reasons.append("collector_number_not_confirmed")
    language = str(observed.get("language") or "unknown").casefold()
    if language not in {str(expected_language or "").casefold(), "unknown"}:
        reasons.append("language_conflict")
    # Unknown visual printing fields are honest and allowed; conflicts are not.
    for source, expected_key in (("finish", "finishCode"), ("edition", "editionCode"), ("parallel", "parallelCode")):
        value = str(observed.get(source) or "unknown").casefold()
        want = str(expected.get(expected_key) or "").casefold()
        if value != "unknown" and want and value != want:
            reasons.append(f"{source}_conflict")
    return ("vision_confirm_candidate" if not reasons else "reject"), reasons


def make_receipt(row: Mapping[str, Any], model_digest: str, prompts: Mapping[str, str], raw_responses: Mapping[str, str]) -> dict[str, Any]:
    observed = parse_json_object(raw_responses.get("full") or "")
    expected = {
        "cardName": row["card_name"], "collectorNumber": row["collector_number"],
        "cardLanguage": row["card_language"], "finishCode": row["finish_code"],
        "editionCode": row["edition_code"], "parallelCode": row["parallel_code"],
    }
    decision, reasons = validate_observation(observed, expected)
    crop_observed = parse_json_object(raw_responses.get("collectorCrop") or "")
    crop_decision, crop_reasons = validate_observation(crop_observed, expected)
    if crop_decision == "vision_confirm_candidate":
        observed, decision, reasons = crop_observed, crop_decision, crop_reasons
    receipt: dict[str, Any] = {
        "schemaVersion": 1, "type": "local_vlm_image_review", "pipelineVersion": PIPELINE_VERSION, "assetId": int(row["asset_id"]),
        "variantId": int(row["variant_id"]), "contentSha256": row["content_sha256"],
        "sourceVersionSha256": row["source_version_sha256"], "tier": "A",
        "model": MODEL, "modelDigest": model_digest, "promptSha256": sha256_bytes(canonical_bytes(prompts)),
        "rawResponseSha256": sha256_bytes(canonical_bytes(raw_responses)), "expected": expected,
        "observed": observed or {}, "decision": decision, "reasons": reasons,
    }
    receipt["reviewKey"] = sha256_bytes(canonical_bytes({
        "assetId": receipt["assetId"], "contentSha256": receipt["contentSha256"],
        "sourceVersionSha256": receipt["sourceVersionSha256"], "pipelineVersion": PIPELINE_VERSION, "model": MODEL,
        "modelDigest": model_digest, "promptSha256": receipt["promptSha256"],
    }))
    receipt["receiptSha256"] = sha256_bytes(canonical_bytes(receipt))
    return receipt


def asset_path(row: Mapping[str, Any]) -> Path:
    key = str(row["private_object_key"])
    paths = [ROOT / key, ROOT / "data" / "runtime" / "private-landing" / key,
             ROOT / "data" / "public" / "market-assets" / f"{row['content_sha256']}.webp",
             ROOT / "apps" / "web" / "public" / "market-assets" / f"{row['content_sha256']}.webp"]
    for path in paths:
        if path.is_file() and sha256_bytes(path.read_bytes()) == str(row["content_sha256"]):
            return path
    raise RuntimeError(f"unreadable_or_hash_mismatch:{row['asset_id']}")


def ollama_model_digest() -> str:
    with urllib.request.urlopen(f"{OLLAMA}/api/tags", timeout=10) as response:
        models = json.load(response).get("models") or []
    for item in models:
        if item.get("name") == MODEL:
            digest = str(item.get("digest") or "")
            if SHA.fullmatch(digest):
                return digest
    raise RuntimeError(f"local_vlm_model_missing:{MODEL}")


def vlm_call(image: bytes, prompt: str) -> str:
    payload = {"model": MODEL, "prompt": prompt, "images": [base64.b64encode(image).decode()], "stream": False, "think": False, "options": {"temperature": 0, "num_predict": 180}}
    request = urllib.request.Request(f"{OLLAMA}/api/generate", data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.load(response).get("response") or ""


def bottom_crop(image: bytes) -> bytes:
    with Image.open(BytesIO(image)) as source:
        source = source.convert("RGB")
        width, height = source.size
        # Bottom-right is the common collector-number area; cap output at 1600px
        # so a 1600px source never expands into a VRAM-hostile 6400px request.
        crop = source.crop((int(width * 0.55), int(height * 0.55), width, height))
        scale = min(4.0, 1600.0 / max(crop.size))
        crop = crop.resize((max(1, int(crop.width * scale)), max(1, int(crop.height * scale))))
        output = BytesIO(); crop.save(output, format="JPEG", quality=95)
        return output.getvalue()


def review(row: Mapping[str, Any], model_digest: str) -> dict[str, Any]:
    path = asset_path(row)
    geometry = inspect_image_geometry(path)
    if geometry["status"] != "passed":
        raise ValueError(
            "image_canvas_geometry_invalid:" + ",".join(geometry["reasons"])
        )
    image = path.read_bytes()
    return make_receipt(
        row,
        model_digest,
        PROMPTS,
        {
            "full": vlm_call(image, FULL_PROMPT),
            "collectorCrop": vlm_call(bottom_crop(image), CROP_PROMPT),
        },
    )


def tier_a_rows(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    import pymysql
    variant_ids = [int(card["variantId"]) for card in report.get("cards") or []]
    if not variant_ids:
        return []
    connection = pymysql.connect(host="127.0.0.1", port=int(os.environ.get("CARDZ_DB_PORT", "3308")), user=os.environ.get("CARDZ_DB_USER", "cardz"), password=os.environ.get("CARDZ_DB_PASSWORD", ""), database="cardz_market_cap", charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor)
    marks = ",".join(["%s"] * len(variant_ids))
    try:
        with connection.cursor() as cursor:
            cursor.execute(f"""
                SELECT a.id asset_id,a.variant_id,a.content_sha256,a.source_version_sha256,a.private_object_key,
                       v.canonical_name card_name,v.collector_number,v.card_language,v.tcg_code,
                       pn.edition_code,pn.parallel_code,pn.finish_code
                FROM market_image_asset a
                JOIN catalog_variant v ON v.id=a.variant_id
                LEFT JOIN catalog_printing_identity pn ON pn.variant_id=a.variant_id
                JOIN market_image_qc q ON q.image_asset_id=a.id
                JOIN (SELECT image_asset_id,MAX(id) qid FROM market_image_qc GROUP BY image_asset_id) qq ON qq.image_asset_id=q.image_asset_id AND qq.qid=q.id
                JOIN (SELECT variant_id,MAX(CONCAT(DATE_FORMAT(captured_at,'%%Y%%m%%d%%H%%i%%s%%f'),LPAD(id,20,'0'))) k FROM market_image_asset WHERE image_kind='raw_front' GROUP BY variant_id) latest ON latest.variant_id=a.variant_id AND latest.k=CONCAT(DATE_FORMAT(a.captured_at,'%%Y%%m%%d%%H%%i%%s%%f'),LPAD(a.id,20,'0'))
                WHERE a.variant_id IN ({marks}) AND a.image_kind='raw_front' AND q.semantic_match_status='source_id_exact'
                  AND (SELECT COUNT(DISTINCT p.source_path) FROM market_image_source_pointer p WHERE p.variant_id=a.variant_id AND p.image_kind='raw_front')=1
                  AND EXISTS (SELECT 1 FROM market_image_source_pointer p WHERE p.variant_id=a.variant_id AND p.image_kind='raw_front' AND p.source_version_sha256=a.source_version_sha256)
                ORDER BY a.variant_id
            """, tuple(variant_ids))
            return list(cursor.fetchall())
    finally:
        connection.close()


def existing_receipts(path: Path) -> set[str]:
    if not path.is_file(): return set()
    values = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        try: values.add(str(json.loads(line).get("reviewKey") or ""))
        except json.JSONDecodeError: raise RuntimeError(f"invalid_receipt_jsonl:{path}")
    return values


def review_key(row: Mapping[str, Any], model_digest: str) -> str:
    return sha256_bytes(canonical_bytes({
        "assetId": int(row["asset_id"]),
        "contentSha256": row["content_sha256"],
        "sourceVersionSha256": row["source_version_sha256"],
        "pipelineVersion": PIPELINE_VERSION,
        "model": MODEL,
        "modelDigest": model_digest,
        "promptSha256": sha256_bytes(canonical_bytes(PROMPTS)),
    }))


def failure_item_key(row: Mapping[str, Any]) -> str:
    return (
        f"variant:{int(row['variant_id'])}:asset:{int(row['asset_id'])}:"
        f"{str(row['content_sha256'])}"
    )


def prioritize_rows(
    rows: Sequence[Mapping[str, Any]],
    priority_tcg: str | None,
) -> list[Mapping[str, Any]]:
    priority = str(priority_tcg or "").strip().casefold()
    return sorted(
        rows,
        key=lambda row: (
            0 if priority and str(row.get("tcg_code") or "").casefold() == priority else 1,
            int(row["variant_id"]),
            int(row["asset_id"]),
        ),
    )


@contextmanager
def exclusive_output(path: Path) -> Iterator[None]:
    """Make one coordinator the sole owner of a receipt JSONL."""

    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_name(f"{path.name}.lock")
    try:
        descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise RuntimeError(f"local_vlm_output_locked:{lock}") from exc
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(f"{os.getpid()}\n")
        yield
    finally:
        lock.unlink(missing_ok=True)


def review_rows(
    rows: Sequence[Mapping[str, Any]],
    model_digest: str,
    *,
    workers: int,
    job: Callable[[Mapping[str, Any], str], dict[str, Any]] = review,
    record_failure_fn: Callable[..., Any] = record_failure,
    record_resolution_fn: Callable[..., Any] = record_resolution,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Review independently; one failed card must not discard successful receipts."""

    receipts: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(job, row, model_digest): row for row in rows}
        for future in as_completed(futures):
            row = futures[future]
            item_key = failure_item_key(row)
            run_id = review_key(row, model_digest)
            context = {
                "variantId": int(row["variant_id"]),
                "assetId": int(row["asset_id"]),
                "contentSha256": str(row["content_sha256"]),
                "sourceVersionSha256": str(row["source_version_sha256"]),
                "modelDigest": model_digest,
            }
            try:
                receipt = future.result()
            except Exception as exc:
                error_type = type(exc).__name__
                record_failure_fn(
                    source="local_vlm",
                    stage="image_review",
                    script=Path(__file__),
                    item_key=item_key,
                    reason_code="local_vlm_review_failed",
                    message=f"{error_type}: {exc}",
                    retryable=True,
                    run_id=run_id,
                    context=context,
                    next_action="retry_local_vlm_image_review",
                    error_type=error_type,
                )
                failures.append({"itemKey": item_key, "errorType": error_type})
                continue
            receipts.append(receipt)
            record_resolution_fn(
                source="local_vlm",
                stage="image_review",
                script=Path(__file__),
                item_key=item_key,
                run_id=run_id,
                resolution="vision_observation_recorded",
                context=context,
            )
    receipts.sort(key=lambda row: (int(row["variantId"]), int(row["assetId"])))
    failures.sort(key=lambda row: row["itemKey"])
    return receipts, failures


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=None)
    parser.add_argument("--limit", type=int, default=3, help="0 means all Tier A; default is 3")
    parser.add_argument("--workers", type=int, default=1, choices=(1, 2))
    parser.add_argument(
        "--priority-tcg",
        default="one-piece",
        help="review this TCG first; default: one-piece",
    )
    parser.add_argument("--output", type=Path, default=OUTPUT_DIR / "tier-a.jsonl")
    args = parser.parse_args(argv)
    load_db_env()
    report_path = args.report or latest_report_path()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    with exclusive_output(args.output):
        all_rows = tier_a_rows(report)
        digest = ollama_model_digest()
        done = existing_receipts(args.output)
        rows = prioritize_rows(
            [row for row in all_rows if review_key(row, digest) not in done],
            args.priority_tcg,
        )
        if args.limit > 0:
            rows = rows[:args.limit]
        receipts, failures = review_rows(rows, digest, workers=args.workers)
        if receipts:
            with args.output.open("a", encoding="utf-8") as handle:
                for receipt in receipts:
                    handle.write(json.dumps(receipt, ensure_ascii=False, sort_keys=True) + "\n")
    print(json.dumps({
        "tierACandidates": len(all_rows),
        "reviewed": len(receipts) + len(failures),
        "written": len(receipts),
        "confirmCandidates": sum(r["decision"] == "vision_confirm_candidate" for r in receipts),
        "rejected": sum(r["decision"] == "reject" for r in receipts),
        "failed": len(failures),
        "priorityTcg": args.priority_tcg,
        "report": str(report_path),
        "output": str(args.output),
    }, indent=2))
    return 0


if __name__ == "__main__": raise SystemExit(main())
