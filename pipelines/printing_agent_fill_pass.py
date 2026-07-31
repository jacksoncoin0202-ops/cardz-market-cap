#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Agent-assisted printing field fill for open review worksheets.

Rules:
  - Freeze real private evidence under private-source-map (allowed roots).
  - evidenceType = vision_verified_source_field only when parse is explicit in source text.
  - Never infer edition / parallel / finish from a title, rarity, or downloaded image.
  - editionCode / parallelCode / finishCode remain unresolved until a separate
    field-level human/vision receipt is supplied.
  - Skip seal when any of 7 fields incomplete or bindings not exact pair.
  - Does not materialize DB.

Usage:
  python -X utf8 pipelines\\printing_agent_fill_pass.py \\
    --batch data\\runtime\\private-source-map\\printing-review-batches\\batch_XXX --write
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from printing_review_batch import cmd_seal_worksheet  # noqa: E402
from card_identity import normalize_language  # noqa: E402

EVIDENCE_ROOT = ROOT / "data" / "runtime" / "private-source-map" / "printing-evidence"
G10_SNK_ROOTS = (
    ROOT / "integrations" / "grade10" / "data" / "cards" / "snkrdunk",
    ROOT.parent / "grade10-scraper" / "data" / "cards" / "snkrdunk",
)
GEMRATE_CARDS = ROOT / "data" / "private" / "gemrate" / "cards"

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def freeze_json(obj: Any, dest: Path) -> tuple[str, str]:
    dest.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    raw = body.encode("utf-8")
    digest = sha256_bytes(raw)
    dest.write_bytes(raw)
    rel = str(dest.relative_to(ROOT)).replace("\\", "/")
    return rel, digest


def freeze_file(src: Path, dest: Path) -> tuple[str, str]:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    raw = dest.read_bytes()
    digest = sha256_bytes(raw)
    rel = str(dest.relative_to(ROOT)).replace("\\", "/")
    return rel, digest


def fetch_snk_master(snk_id: str) -> dict[str, Any] | None:
    try:
        from snkrdunk_bulk import SnkrdunkApi

        return SnkrdunkApi().get_master(int(snk_id))
    except Exception as e:
        return {"_error": str(e)}


def fill_field(
    field: str,
    *,
    raw: str,
    source_code: str,
    external_id: str,
    path: str,
    sha: str,
    note: str,
    normalized: str | None = None,
) -> dict[str, Any]:
    return {
        "field": field,
        "rawValue": raw,
        "normalizedValue": normalized if normalized is not None else raw,
        "evidenceType": "vision_verified_source_field",
        "extractorVersion": "agent-vision-snk-title-v1",
        "sourceCode": source_code,
        "externalEntityId": str(external_id),
        "sourceReceiptPath": path,
        "sourceReceiptSha256": sha,
        "status": "filled",
        "notes": note,
    }


def explicit_source_language(master: dict[str, Any]) -> tuple[str, str] | None:
    """Accept only a source-declared language field; never infer from title or set."""
    raw = str(master.get("language") or "").strip()
    normalized = normalize_language(raw)
    return (raw, normalized) if raw and normalized else None


def find_g10_asset_info(snk_id: str) -> Path | None:
    for root in G10_SNK_ROOTS:
        path = root / str(snk_id) / "asset_info.json"
        if path.is_file():
            return path
    return None


def explicit_g10_language(asset_info: dict[str, Any]) -> tuple[str, str] | None:
    """Accept only the structured language field bound to this exact SNK id."""
    raw = str(asset_info.get("language") or asset_info.get("lang") or "").strip()
    normalized = normalize_language(raw)
    return (raw, normalized) if raw and normalized else None


def process_worksheet(path: Path, *, write: bool) -> dict[str, Any]:
    ws = json.loads(path.read_text(encoding="utf-8"))
    if ws.get("status") == "sealed":
        return {"variantId": ws.get("variantId"), "status": "already_sealed"}
    if not ws.get("bindingReady"):
        return {
            "variantId": ws.get("variantId"),
            "status": "skip_bindings",
            "reason": "need exact gemrate+snk pair first",
        }

    # A dry run is a local metadata check only.  Do not fetch or freeze source
    # evidence, download media, mutate worksheets, or write an output receipt.
    if not write:
        return {
            "variantId": ws.get("variantId"),
            "status": "would_fetch_and_fill",
            "bindingReady": True,
        }

    bindings = {b["sourceCode"]: b["externalEntityId"] for b in ws.get("sourceBindings") or []}
    snk_id = bindings.get("snkrdunk")
    gem_id = bindings.get("gemrate")
    if not snk_id or not gem_id:
        return {"variantId": ws.get("variantId"), "status": "skip_bindings"}

    # Freeze SNK master
    master = fetch_snk_master(str(snk_id))
    if not master or master.get("_error"):
        return {
            "variantId": ws.get("variantId"),
            "status": "skip_snk_fetch",
            "reason": (master or {}).get("_error"),
        }
    snk_path, snk_sha = freeze_json(
        {
            "type": "snk_master_freeze",
            "snkId": snk_id,
            "fetchedAt": utc_now(),
            "master": master,
        },
        EVIDENCE_ROOT / "snk" / str(snk_id) / "master.json",
    )

    # Optional G10 asset_info
    g10_rel = g10_sha = None
    g10_data: dict[str, Any] | None = None
    g10_src = find_g10_asset_info(str(snk_id))
    if g10_src is not None:
        try:
            g10_data = json.loads(g10_src.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            g10_data = None
        g10_rel, g10_sha = freeze_file(
            g10_src, EVIDENCE_ROOT / "g10" / str(snk_id) / "asset_info.json"
        )

    # Optional gemrate identity
    gem_rel = gem_sha = None
    gem_id_path = GEMRATE_CARDS / str(gem_id) / "identity.receipt.json"
    if gem_id_path.is_file():
        gem_rel, gem_sha = freeze_file(
            gem_id_path, EVIDENCE_ROOT / "gemrate" / str(gem_id) / "identity.receipt.json"
        )

    title = str(master.get("name") or "")
    # Catalog base fields need an exact SNK binding receipt before sealing.
    fields = ws["fields"]
    for base_field in ("tcgCode", "setName", "collectorNumber"):
        val = fields[base_field].get("normalizedValue") or fields[base_field].get("rawValue")
        if not val:
            return {
                "variantId": ws.get("variantId"),
                "status": "skip_base_missing",
                "field": base_field,
            }
        fields[base_field] = fill_field(
            base_field,
            raw=val,
            source_code="snkrdunk",
            external_id=snk_id,
            path=snk_path,
            sha=snk_sha,
            note=f"catalog base corroborated by SNK master name={title!r}",
        )

    missing = []
    declared_language = explicit_source_language(master)
    language_path = snk_path
    language_sha = snk_sha
    language_note = "explicit language field in exact SNK master"
    if declared_language is None and g10_data is not None and g10_rel and g10_sha:
        declared_language = explicit_g10_language(g10_data)
        language_path = g10_rel
        language_sha = g10_sha
        language_note = "explicit language field in exact SNK-bound G10 asset_info"
    draft_language = str(
        fields.get("cardLanguage", {}).get("normalizedValue")
        or fields.get("cardLanguage", {}).get("rawValue")
        or ""
    ).strip()
    if declared_language is None or normalize_language(draft_language) != declared_language[1]:
        missing.append("cardLanguage")
    else:
        fields["cardLanguage"] = fill_field(
            "cardLanguage",
            raw=declared_language[0],
            normalized=declared_language[1],
            source_code="snkrdunk",
            external_id=snk_id,
            path=language_path,
            sha=language_sha,
            note=language_note,
        )
    # These are printing facts, not safe title/rarity defaults.  A separate
    # human/vision step must create one field-level evidence receipt per value
    # and invoke ``seal-worksheet`` directly.
    missing.extend(("editionCode", "parallelCode", "finishCode"))

    ws["fields"] = fields
    ws["agentPass"] = {
        "at": utc_now(),
        "snkEvidence": snk_path,
        "g10Evidence": g10_rel,
        "gemrateEvidence": gem_rel,
        "automaticPrintingFieldInference": "disabled",
        "missingFields": missing,
        "snkTitle": title,
    }

    if write:
        path.write_text(json.dumps(ws, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if missing:
        return {
            "variantId": ws.get("variantId"),
            "status": "incomplete",
            "missingFields": missing,
            "title": title,
            "bindingReady": True,
        }

    # seal
    if not write:
        return {
            "variantId": ws.get("variantId"),
            "status": "would_seal",
            "title": title,
        }
    try:
        sealed = cmd_seal_worksheet(
            worksheet_path=path, actor="agent-vision", write=True
        )
        return {
            "variantId": ws.get("variantId"),
            "status": "sealed",
            "seal": sealed,
            "title": title,
        }
    except SystemExit as e:
        return {
            "variantId": ws.get("variantId"),
            "status": "seal_failed",
            "reason": str(e),
            "title": title,
        }
    except Exception as e:
        return {
            "variantId": ws.get("variantId"),
            "status": "seal_error",
            "reason": str(e)[:300],
            "title": title,
        }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=Path, required=True)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    batch = args.batch
    if not batch.is_absolute():
        batch = ROOT / batch
    idx_path = batch / "index.json"
    if not idx_path.is_file():
        raise SystemExit(f"missing {idx_path}")
    index = json.loads(idx_path.read_text(encoding="utf-8"))
    results = []
    for i, row in enumerate(index.get("worksheets") or []):
        if args.limit and i >= args.limit:
            break
        rel = row.get("path") or f"worksheets/variant_{row['variantId']}.json"
        wp = batch / rel
        if not wp.is_file():
            results.append({"variantId": row.get("variantId"), "status": "missing_file"})
            continue
        results.append(process_worksheet(wp, write=args.write))

    summary = {
        "write": args.write,
        "batch": str(batch),
        "n": len(results),
        "byStatus": {},
        "results": results,
    }
    for r in results:
        st = r.get("status") or "?"
        summary["byStatus"][st] = summary["byStatus"].get(st, 0) + 1
    print(json.dumps({k: summary[k] for k in summary if k != "results"}, indent=2))
    if args.write:
        out = batch / f"agent_fill_pass_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
        out.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("detail", out)
    # print incomplete reasons
    for r in results:
        if r.get("status") in ("incomplete", "seal_failed", "skip_bindings"):
            print(
                f"  vid={r.get('variantId')} {r.get('status')} "
                f"missing={r.get('missingFields')} title={r.get('title')!r} reason={r.get('reason')}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
