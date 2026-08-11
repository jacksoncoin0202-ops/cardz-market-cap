#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CARDZ stock/incr collector control plane (Polaris era).

Two operator modes only:
  stock  - residual full pulls for gap ids
  incr   - cursor/due exact-id deltas

Does not revive archived run_daily factory. Display authority stays in operator_control.latest_prices.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from qualified_pool_operator import db, load_env  # noqa: E402
from operator_control import CHECKPOINT_SLA_HOURS, current_universe  # noqa: E402
from native_image_resolver import ProcessedSnkDefaultImage, process_snk_default_image  # noqa: E402
from snkrdunk_bulk import (  # noqa: E402
    SNK_EN_PRODUCT_PAGE_CONTRACT,
    SnkrdunkApiPool,
    exact_en_product_url,
    master_default_image,
)

OUT_DIR = ROOT / "data" / "runtime" / "operator" / "collect"
REGISTRY_PATH = OUT_DIR / "collect_registry.jsonl"
LAST_INCR = OUT_DIR / "last_incr.json"
LAST_STOCK = OUT_DIR / "last_stock.json"
LAST_STATUS = OUT_DIR / "last_status.json"
PC_REFRESH_REPORT = OUT_DIR / "pc_cdp_refresh_report.json"
PC_MAP = ROOT / "data/runtime/private-source-map/c11_pc_ebay_map_full900.jsonl"
WINDOWS_PY = ROOT / ".venv-backend-windows/Scripts/python.exe"
CHECKPOINT_ADAPTERS = (
    "gemrate_pop",
    "snk_trades",
    "snk_price",
    "snk_en_image",
    "pc_ebay_sales",
    "en_price_ref",
)
COLLECT_LEASE_CONTRACT = "mysql_advisory_adapter_lease_v1"
# Per-item failure streaks and per-item success receipts live beside the other
# collect runtime state. The MySQL stream checkpoint remains the resume
# authority; these files carry the per-item evidence between daily runs.
QUARANTINE_PATH = OUT_DIR / "collect_quarantine.json"
ITEM_CHECKPOINT_PATH = OUT_DIR / "collect_item_checkpoints.json"
QUARANTINE_CONTRACT = "collect_item_quarantine_v1"
ITEM_CHECKPOINT_CONTRACT = "collect_item_checkpoint_v1"
QUARANTINE_THRESHOLD = 3  # consecutive failed runs before an item is skipped
PY = sys.executable
# Reporting freshness and the acceptance gate must quote the same number, so
# take it from the gate rather than keeping a second copy that can drift.
SLA_HOURS = CHECKPOINT_SLA_HOURS
# "fresh enough to publish" and "old enough to re-collect" are different
# questions, and answering both with SLA_HOURS is what broke the chain: the
# browser lane runs 09:30 JST, the acceptance gate runs again 03:30 JST, 18h
# later. A stream skipped at 09:30 for being "only" 24h old is 42h old at that
# night's gate -- past SLA -- so daily-accept raises and the board stops
# updating. Every stream failed on its second night. Refresh has to come due
# before one more lane interval can carry a stream past the gate.
LANE_INTERVAL_HOURS = 24
REFRESH_DUE_HOURS = SLA_HOURS - LANE_INTERVAL_HOURS
CARDZ_CDP_PORT = int(os.environ.get("CARDZ_CDP_PORT", "9333"))
# 9222 is the Codex browser profile. Attaching there drives somebody else's
# logged-in Chrome, and the ban on it lived only in AGENTS.md while this knob
# sat here happily accepting it from the environment.
if CARDZ_CDP_PORT == 9222:
    raise RuntimeError(
        "CARDZ_CDP_PORT=9222 is the Codex browser profile and must never be driven. "
        "PriceCharting uses 9333 (headed Chrome)."
    )
SNK_EN_ASSET_DIR = ROOT / "data" / "runtime" / "operator" / "snk-en-assets"
SNK_EN_SOURCE_CODE = "snkrdunk"
SNK_EN_FREEZE_SOURCE_CODE = "snkrdunk_en"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class SnkEnPrepared:
    variant_id: int
    external_id: str
    product_url: str
    product_page_payload_sha256: str
    product_page_observed_at: datetime
    master_payload_sha256: str
    default_image_url: str
    default_image_url_sha256: str
    downloaded_bytes_sha256: str
    image: ProcessedSnkDefaultImage
    identity_evidence_sha256: str
    source_observed_at: datetime
    captured_at: datetime
    master_reused: bool
    bytes_reused: bool
    downloaded: bool


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _text_sha256(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def _bytes_sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _parse_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _utc_naive(value: datetime | None = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is not None:
        current = current.astimezone(timezone.utc).replace(tzinfo=None)
    return current


def _stream_key(variant_id: int, external_id: Any) -> str:
    return f"{int(variant_id)}:{str(external_id or '').strip()}"[:100]


def _select_items(items: list[dict[str, Any]], limit: int | None) -> list[dict[str, Any]]:
    if limit is None or limit <= 0:
        return list(items)
    return list(items[:limit])


def _unique_items(items: list[dict[str, Any]], limit: int | None) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen: set[int] = set()
    for item in items:
        variant_id = int(item["variantId"])
        if variant_id in seen:
            continue
        seen.add(variant_id)
        selected.append(item)
        if limit is not None and limit > 0 and len(selected) >= limit:
            break
    return selected


def _jsonl_rows(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]


def _last_json_object(text: str) -> dict[str, Any] | None:
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    for line in reversed(lines):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def _windows_path(path: Path) -> str:
    if sys.platform == "win32":
        return str(path)
    result = subprocess.run(
        ["wslpath", "-w", str(path)],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return result.stdout.strip()


def _age_hours(dt: datetime | None) -> float | None:
    if dt is None:
        return None
    if getattr(dt, "tzinfo", None) is not None:
        dt = dt.replace(tzinfo=None)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    return (now - dt).total_seconds() / 3600.0


def _run(cmd: list[str], *, timeout: int, dry_run: bool) -> dict[str, Any]:
    item = {
        "cmd": cmd,
        "timeout": timeout,
        "dryRun": dry_run,
        "startedAt": utc_now(),
    }
    if dry_run:
        item.update({"exit": 0, "skipped": True, "note": "dry-run; not executed"})
        return item
    try:
        r = subprocess.run(
            cmd,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        item.update(
            {
                "exit": r.returncode,
                "stdoutTail": (r.stdout or "")[-4000:],
                "stderrTail": (r.stderr or "")[-2000:],
            }
        )
    except subprocess.TimeoutExpired as exc:
        item.update({"exit": 124, "error": f"timeout:{exc}"})
    except Exception as exc:  # noqa: BLE001
        item.update({"exit": 1, "error": f"{type(exc).__name__}:{exc}"})
    item["finishedAt"] = utc_now()
    return item


def load_checkpoints(cur) -> dict[tuple[str, str], dict[str, Any]]:
    placeholders = ",".join(["%s"] * len(CHECKPOINT_ADAPTERS))
    cur.execute(
        f"""
        SELECT source_code, stream_key, last_effective_at, last_payload_sha256,
               last_run_id, updated_at
        FROM market_ingest_checkpoint
        WHERE source_code IN ({placeholders})
        """,
        CHECKPOINT_ADAPTERS,
    )
    return {
        (str(row["source_code"]), str(row["stream_key"])): dict(row)
        for row in cur.fetchall()
    }


def _checkpoint_for(
    checkpoints: dict[tuple[str, str], dict[str, Any]],
    adapter: str,
    variant_id: int,
    external_id: Any,
) -> dict[str, Any] | None:
    return checkpoints.get((adapter, _stream_key(variant_id, external_id)))


def _poll_mode(
    *,
    has_stock: bool,
    observed_at: datetime | None,
    checkpoint: dict[str, Any] | None,
    empty_poll_is_complete: bool = False,
) -> str:
    if not has_stock:
        if not empty_poll_is_complete or checkpoint is None:
            return "stock"
        age = _age_hours(_parse_datetime(checkpoint.get("last_effective_at")))
        return "incr" if age is None or age > REFRESH_DUE_HOURS else "ok"
    if checkpoint is None:
        return "incr"
    last_success = checkpoint.get("last_effective_at")
    age = _age_hours(_parse_datetime(last_success))
    return "incr" if age is None or age > REFRESH_DUE_HOURS else "ok"


def _insert_control_run(
    cur,
    *,
    adapter: str,
    mode: str,
    items: list[dict[str, Any]],
    payload: Any,
    started_at: datetime,
    completed_at: datetime,
) -> int:
    payload_sha = _sha256(payload)
    run_key = _sha256(
        {
            "adapter": adapter,
            "mode": mode,
            "startedAt": started_at.isoformat(),
            "payloadSha256": payload_sha,
            "streams": [
                _stream_key(int(item["variantId"]), item.get("externalId"))
                for item in items
            ],
        }
    )
    cur.execute(
        """
        INSERT INTO market_ingest_run
            (run_key, source_code, ingest_mode, effective_at, payload_sha256,
             manifest_sha256, status, observed_count, accepted_count,
             quarantined_count, rejected_count, started_at, completed_at)
        VALUES (%s,%s,%s,%s,%s,%s,'completed',%s,%s,0,0,%s,%s)
        ON DUPLICATE KEY UPDATE id=LAST_INSERT_ID(id), status='completed',
            observed_count=VALUES(observed_count), accepted_count=VALUES(accepted_count),
            completed_at=VALUES(completed_at)
        """,
        (
            run_key,
            adapter,
            "incremental" if mode == "incr" else "stock",
            completed_at,
            payload_sha,
            payload_sha,
            len(items),
            len(items),
            started_at,
            completed_at,
        ),
    )
    return int(cur.lastrowid)


def _upsert_checkpoints(
    cur,
    *,
    adapter: str,
    items: list[dict[str, Any]],
    run_id: int,
    completed_at: datetime,
    payload_sha_by_external: dict[str, str] | None = None,
) -> int:
    payload_sha_by_external = payload_sha_by_external or {}
    count = 0
    for item in items:
        external = str(item.get("externalId") or "")
        payload_sha = payload_sha_by_external.get(external) or _sha256(
            {
                "adapter": adapter,
                "variantId": int(item["variantId"]),
                "externalId": external,
                "completedAt": completed_at.isoformat(),
            }
        )
        cur.execute(
            """
            INSERT INTO market_ingest_checkpoint
                (source_code, stream_key, last_effective_at, last_payload_sha256,
                 last_run_id, updated_at)
            VALUES (%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                last_effective_at=VALUES(last_effective_at),
                last_payload_sha256=VALUES(last_payload_sha256),
                last_run_id=VALUES(last_run_id),
                updated_at=VALUES(updated_at)
            """,
            (
                adapter,
                _stream_key(int(item["variantId"]), external),
                completed_at,
                payload_sha,
                run_id,
                completed_at,
            ),
        )
        count += 1
    return count


def record_successful_poll(
    *,
    adapter: str,
    mode: str,
    items: list[dict[str, Any]],
    payload: Any,
    started_at: datetime,
    payload_sha_by_external: dict[str, str] | None = None,
    completed_at: datetime | None = None,
) -> dict[str, Any]:
    if not items:
        return {"runId": None, "checkpointed": 0}
    completed_at = completed_at or datetime.now(timezone.utc)
    if completed_at.tzinfo is not None:
        completed_at = completed_at.astimezone(timezone.utc).replace(tzinfo=None)
    if started_at.tzinfo is not None:
        started_at = started_at.astimezone(timezone.utc).replace(tzinfo=None)
    conn = db()
    try:
        cur = conn.cursor()
        run_id = _insert_control_run(
            cur,
            adapter=adapter,
            mode=mode,
            items=items,
            payload=payload,
            started_at=started_at,
            completed_at=completed_at,
        )
        checkpointed = _upsert_checkpoints(
            cur,
            adapter=adapter,
            items=items,
            run_id=run_id,
            completed_at=completed_at,
            payload_sha_by_external=payload_sha_by_external,
        )
        conn.commit()
        return {"runId": run_id, "checkpointed": checkpointed}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    """House pattern: durable tmp write + os.replace, never a partial file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.next")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _load_runtime_state(path: Path, contract: str) -> dict[str, Any]:
    if not path.is_file():
        return {"contract": contract, "updatedAt": None, "adapters": {}}
    try:
        state = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        state = None
    if not isinstance(state, dict) or state.get("contract") != contract:
        # A corrupt or foreign state file must not silently veto collection.
        return {"contract": contract, "updatedAt": None, "adapters": {}}
    state.setdefault("adapters", {})
    return state


def _quarantined_streams(adapter: str) -> dict[str, dict[str, Any]]:
    state = _load_runtime_state(QUARANTINE_PATH, QUARANTINE_CONTRACT)
    entries = state["adapters"].get(adapter) or {}
    return {
        stream: entry
        for stream, entry in entries.items()
        if int(entry.get("consecutiveFailures") or 0) >= QUARANTINE_THRESHOLD
    }


def _partition_quarantined(
    adapter: str, items: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split due items into pollable and quarantined; quarantined are reported, never dropped."""
    quarantined_streams = _quarantined_streams(adapter)
    active: list[dict[str, Any]] = []
    quarantined: list[dict[str, Any]] = []
    for item in items:
        stream = _stream_key(int(item["variantId"]), item.get("externalId"))
        entry = quarantined_streams.get(stream)
        if entry is None:
            active.append(item)
            continue
        quarantined.append(
            {
                "variantId": int(item["variantId"]),
                "externalId": str(item.get("externalId") or ""),
                "reason": entry.get("reason"),
                "consecutiveFailures": int(entry.get("consecutiveFailures") or 0),
                "lastAttempt": entry.get("lastAttempt"),
            }
        )
    return active, quarantined


def _record_item_outcomes(
    adapter: str,
    *,
    succeeded: list[dict[str, Any]],
    failed: list[dict[str, Any]],
) -> None:
    """Advance per-item failure streaks; one success clears the item's entry."""
    if not succeeded and not failed:
        return
    now = utc_now()
    state = _load_runtime_state(QUARANTINE_PATH, QUARANTINE_CONTRACT)
    entries = dict(state["adapters"].get(adapter) or {})
    for item in succeeded:
        entries.pop(_stream_key(int(item["variantId"]), item.get("externalId")), None)
    for row in failed:
        stream = _stream_key(int(row["variantId"]), row.get("externalId"))
        previous = entries.get(stream) or {}
        entries[stream] = {
            "variantId": int(row["variantId"]),
            "externalId": str(row.get("externalId") or ""),
            "reason": str(row.get("error") or row.get("reason") or "unknown"),
            "consecutiveFailures": int(previous.get("consecutiveFailures") or 0) + 1,
            "lastAttempt": now,
        }
    state["adapters"][adapter] = entries
    state["updatedAt"] = now
    _write_json_atomic(QUARANTINE_PATH, state)


def _persist_item_checkpoints(
    adapter: str,
    *,
    mode: str,
    items: list[dict[str, Any]],
    payload_sha_by_external: Mapping[str, str] | None = None,
    run_id: int | None = None,
) -> None:
    """Mirror per-item success into the runtime checkpoint JSON as it lands.

    The DB stream checkpoint stays the resume authority; this file is the
    incrementally-updated per-item receipt that survives a later lane abort.
    """
    if not items:
        return
    now = utc_now()
    payload_sha_by_external = payload_sha_by_external or {}
    state = _load_runtime_state(ITEM_CHECKPOINT_PATH, ITEM_CHECKPOINT_CONTRACT)
    entries = dict(state["adapters"].get(adapter) or {})
    for item in items:
        external = str(item.get("externalId") or "")
        entries[_stream_key(int(item["variantId"]), external)] = {
            "variantId": int(item["variantId"]),
            "externalId": external,
            "mode": mode,
            "lastSuccessAt": now,
            "payloadSha256": payload_sha_by_external.get(external),
            "runId": run_id,
        }
    state["adapters"][adapter] = entries
    state["updatedAt"] = now
    _write_json_atomic(ITEM_CHECKPOINT_PATH, state)


def ensure_cdp(port: int = 9333) -> dict[str, Any]:
    """Ensure the one dedicated CARDZ CDP session before the leased PC run."""
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/json/version", timeout=2
        ) as response:
            payload = json.load(response)
        if response.status == 200 and payload.get("webSocketDebuggerUrl"):
            return {"ok": True, "port": port, "reused": True}
    except Exception:  # noqa: BLE001
        pass
    ps1 = ROOT / "scripts" / "ensure_chrome_cdp.ps1"
    if not ps1.exists():
        return {"ok": False, "reason": "ensure_chrome_cdp.ps1 missing"}
    # From WSL, call powershell.exe if present
    pwsh = "powershell.exe"
    cmd = [
        pwsh,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        _windows_path(ps1),
        "-Port",
        str(port),
    ]
    try:
        r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=90)
        return {
            "ok": r.returncode == 0,
            "exit": r.returncode,
            "stdoutTail": (r.stdout or "")[-1500:],
            "stderrTail": (r.stderr or "")[-1000:],
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}:{exc}"}


def _migration_029_ready(cur) -> bool:
    cur.execute(
        "SELECT COUNT(*) AS n FROM cardz_schema_version WHERE version_code='029'"
    )
    row = cur.fetchone()
    return int((row.get("n") if isinstance(row, dict) else row[0]) or 0) == 1


def _require_migration_029(cur) -> None:
    if not _migration_029_ready(cur):
        raise RuntimeError(
            "snk_en_image requires completed migration 029; table existence alone is not readiness"
        )


def load_universe_rows(cur) -> list[dict[str, Any]]:
    u = current_universe(cur)
    vids = u["variantIds"]
    if not vids:
        return []
    ph = ",".join(["%s"] * len(vids))
    cur.execute(
        f"""
        SELECT v.id AS variant_id, v.opaque_id, v.card_language, v.canonical_name, v.collector_number
        FROM catalog_variant v
        WHERE v.id IN ({ph})
        """,
        vids,
    )
    base = {int(r["variant_id"]): dict(r) for r in cur.fetchall()}

    cur.execute(
        f"""
        SELECT variant_id, source_code, external_entity_id, match_status,
               evidence_sha256, bind_evidence_json
        FROM catalog_source_identity
        WHERE variant_id IN ({ph})
          AND match_status = 'exact'
        """,
        vids,
    )
    binds: dict[int, dict[str, str]] = {}
    snk_en_identity: dict[int, dict[str, Any]] = {}
    exact_snk_ids_by_variant: dict[int, set[str]] = {}
    for r in cur.fetchall():
        variant_id = int(r["variant_id"])
        source_code = str(r["source_code"])
        external_id = str(r["external_entity_id"])
        binds.setdefault(variant_id, {})[source_code] = external_id
        if source_code in {"snk", "snkrdunk", "snkrdunk_en"}:
            exact_snk_ids_by_variant.setdefault(variant_id, set()).add(external_id)
        if source_code == SNK_EN_SOURCE_CODE:
            previous = snk_en_identity.get(variant_id)
            if previous and str(previous["externalId"]) != external_id:
                raise RuntimeError(
                    f"multiple exact snkrdunk IDs for active variant {variant_id}"
                )
            snk_en_identity[variant_id] = {
                "externalId": external_id,
                "evidenceSha256": str(r.get("evidence_sha256") or ""),
            }
    multiple_exact_snk = {
        variant_id: sorted(external_ids)
        for variant_id, external_ids in exact_snk_ids_by_variant.items()
        if len(external_ids) > 1
    }
    if multiple_exact_snk:
        raise RuntimeError(f"multiple exact SNK IDs in active universe: {multiple_exact_snk}")

    # snk_market_data ingests kline only for pairs in operator_strict_source_identity
    # (load_exact_snk_item_to_variant), which is stricter than match_status='exact'
    # above: the 037 view also demands 036 provider-native evidence and a matching
    # capture receipt. A pre-036 binding that S7 declined to restamp (soft parallel
    # mismatch) keeps status 'exact' and so used to land in the snk_price poll list,
    # where the ingest then skipped it as no_exact_identity -- and that one skip
    # failed the adapter contract, throwing away the checkpoints of every card that
    # HAD been ingested in the same batch. Poll what the ingest can accept.
    cur.execute(
        f"""
        SELECT variant_id, external_entity_id
        FROM operator_strict_source_identity
        WHERE variant_id IN ({ph})
          AND source_code = 'snkrdunk'
        """,
        vids,
    )
    strict_snk_by_variant: dict[int, str] = {}
    for r in cur.fetchall():
        strict_snk_by_variant[int(r["variant_id"])] = str(r["external_entity_id"])

    snk_en_accepted: dict[int, dict[str, Any]] = {}
    if _migration_029_ready(cur):
        cur.execute(
            f"""
            SELECT variant_id, canonical_image_snk_item_id,
                   canonical_image_source_observed_at
            FROM operator_canonical_image_projection
            WHERE variant_id IN ({ph})
            """,
            vids,
        )
        for r in cur.fetchall():
            snk_en_accepted[int(r["variant_id"])] = {
                "externalId": str(r["canonical_image_snk_item_id"]),
                "observedAt": r["canonical_image_source_observed_at"],
            }

    cur.execute(
        f"""
        SELECT variant_id, source_code, MAX(sold_at) AS max_sold, COUNT(*) AS n
        FROM market_sale_observation
        WHERE variant_id IN ({ph})
        GROUP BY variant_id, source_code
        """,
        vids,
    )
    sales: dict[int, dict[str, dict[str, Any]]] = {}
    for r in cur.fetchall():
        sales.setdefault(int(r["variant_id"]), {})[str(r["source_code"])] = {
            "max": r["max_sold"],
            "n": int(r["n"] or 0),
        }

    cur.execute(
        f"""
        SELECT variant_id, source_code, MAX(effective_at) AS max_eff, COUNT(*) AS n
        FROM market_price_observation
        WHERE variant_id IN ({ph})
        GROUP BY variant_id, source_code
        """,
        vids,
    )
    prices: dict[int, dict[str, dict[str, Any]]] = {}
    for r in cur.fetchall():
        prices.setdefault(int(r["variant_id"]), {})[str(r["source_code"])] = {
            "max": r["max_eff"],
            "n": int(r["n"] or 0),
        }

    # An arbitrary legacy PriceCharting/eBay row is not proof that the current
    # EN PSA10 reference contract has been materialized.  Registry readiness
    # must use the same explicit PriceCharting field and exact identity gate as
    # the canonical projections; otherwise a fresh checkpoint can hide a
    # missing ``pc_psa10_current_price_v1`` observation indefinitely.
    cur.execute(
        f"""
        SELECT p.variant_id, MAX(p.effective_at) AS max_eff
        FROM market_price_observation p
        INNER JOIN market_source_observation so ON so.id=p.source_observation_id
          AND so.source_code='pricecharting'
          AND so.external_entity_id=p.source_external_entity_id
          AND so.payload_sha256=p.payload_sha256
          AND so.observed_date=p.observed_date
        INNER JOIN catalog_source_identity si ON si.variant_id=p.variant_id
          AND si.source_code='pricecharting'
          AND si.external_entity_id=p.source_external_entity_id
          AND LOWER(si.match_status)='exact'
        WHERE p.variant_id IN ({ph})
          AND p.source_code='pricecharting'
          AND p.metric_status='ready' AND p.price_usd>0 AND p.source_priority=95
          AND so.observation_kind='psa10_price_guide'
          AND JSON_UNQUOTE(JSON_EXTRACT(so.payload_json,'$.contract'))='pc_psa10_current_price_v1'
          AND JSON_UNQUOTE(JSON_EXTRACT(so.payload_json,'$.source'))='pricecharting'
          AND JSON_UNQUOTE(JSON_EXTRACT(so.payload_json,'$.method'))='pricecharting_explicit_psa10_field_v1'
          AND JSON_UNQUOTE(JSON_EXTRACT(so.payload_json,'$.field'))='VGPC.chart_data.manualonly.last'
          AND JSON_UNQUOTE(JSON_EXTRACT(so.payload_json,'$.sourceUrl')) LIKE 'https://www.pricecharting.com/%%'
          AND p.source_external_entity_id REGEXP '^[0-9]+$'
        GROUP BY p.variant_id
        """,
        vids,
    )
    explicit_pc_prices = {
        int(r["variant_id"]): r["max_eff"] for r in cur.fetchall()
    }

    cur.execute(
        f"""
        SELECT variant_id, MAX(effective_at) AS max_eff
        FROM market_grader_population_observation
        WHERE variant_id IN ({ph}) AND source_code='gemrate'
        GROUP BY variant_id
        """,
        vids,
    )
    pop = {int(r["variant_id"]): r["max_eff"] for r in cur.fetchall()}

    member_by_variant = {
        int(member["variant_id"]): dict(member) for member in u.get("members", [])
    }
    rows = []
    for vid, meta in base.items():
        lang = str(meta.get("card_language") or "").lower()
        b = binds.get(vid) or {}
        s = sales.get(vid) or {}
        p = prices.get(vid) or {}
        snk_sale_max = None
        for sc in ("snkrdunk", "snk", "snk_psa10"):
            if sc in s and s[sc]["max"] is not None:
                if snk_sale_max is None or s[sc]["max"] > snk_sale_max:
                    snk_sale_max = s[sc]["max"]
        ebay_sale_max = (s.get("ebay") or {}).get("max")
        snk_price_max = None
        for sc in ("snk_psa10", "snkrdunk", "snk"):
            if sc in p and p[sc]["max"] is not None:
                if snk_price_max is None or p[sc]["max"] > snk_price_max:
                    snk_price_max = p[sc]["max"]
        en_price_max = None
        for sc in ("ebay", "pricecharting"):
            if sc in p and p[sc]["max"] is not None:
                if en_price_max is None or p[sc]["max"] > en_price_max:
                    en_price_max = p[sc]["max"]

        row = {
            "variantId": vid,
            "opaqueId": meta.get("opaque_id"),
            "lang": lang,
            "name": meta.get("canonical_name"),
            "collector": meta.get("collector_number"),
            "ids": {
                "snkrdunk": b.get("snkrdunk") or b.get("snk"),
                "snkrdunkStrict": strict_snk_by_variant.get(vid),
                "snkrdunkEn": (snk_en_identity.get(vid) or {}).get("externalId"),
                "pricecharting": b.get("pricecharting"),
                "ebay": b.get("ebay"),
                "gemrate": b.get("gemrate"),
            },
            "snkEnIdentityEvidenceSha256": (
                snk_en_identity.get(vid) or {}
            ).get("evidenceSha256"),
            "snkEnAccepted": snk_en_accepted.get(vid),
            "activeRank": (member_by_variant.get(vid) or {}).get("market_rank"),
            "activeRole": (member_by_variant.get(vid) or {}).get("member_role"),
            "activeSegment": (member_by_variant.get(vid) or {}).get("segment_code"),
            "sales": {
                "snkMax": snk_sale_max.isoformat(sep=" ") if hasattr(snk_sale_max, "isoformat") else snk_sale_max,
                "ebayMax": ebay_sale_max.isoformat(sep=" ") if hasattr(ebay_sale_max, "isoformat") else ebay_sale_max,
                "snkAny": snk_sale_max is not None,
                "ebayAny": ebay_sale_max is not None,
            },
            "prices": {
                "snkMax": snk_price_max.isoformat(sep=" ") if hasattr(snk_price_max, "isoformat") else snk_price_max,
                "enMax": en_price_max.isoformat(sep=" ") if hasattr(en_price_max, "isoformat") else en_price_max,
                "snkAny": snk_price_max is not None,
                "enAny": en_price_max is not None,
                "enExplicitPc": explicit_pc_prices.get(vid) is not None,
            },
            "popMax": pop.get(vid).isoformat(sep=" ") if hasattr(pop.get(vid), "isoformat") else pop.get(vid),
            "_snkSaleMax": snk_sale_max,
            "_ebaySaleMax": ebay_sale_max,
            "_snkPriceMax": snk_price_max,
            "_enPriceMax": en_price_max,
            "_enExplicitPcMax": explicit_pc_prices.get(vid),
            "_popMax": pop.get(vid),
        }
        rows.append(row)
    rows.sort(
        key=lambda row: (
            int(row.get("activeRank") or 4294967295),
            int(row["variantId"]),
        )
    )
    return rows


def classify_needs(
    row: dict[str, Any],
    checkpoints: dict[tuple[str, str], dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Return adapter need rows for registry."""
    checkpoints = checkpoints or {}
    needs = []
    lang = row["lang"]
    ids = row["ids"]
    vid = int(row["variantId"])

    # gemrate pop
    if ids.get("gemrate"):
        mode = _poll_mode(
            has_stock=row.get("_popMax") is not None,
            observed_at=row.get("_popMax"),
            checkpoint=_checkpoint_for(checkpoints, "gemrate_pop", vid, ids["gemrate"]),
        )
        needs.append({"adapter": "gemrate_pop", "modeNeeded": mode, "externalId": ids["gemrate"], "transport": "http_curl", "polarRole": "pop"})

    # SNK is an exact market authority whenever the card has an exact SNK ID;
    # card language does not invalidate that binding (the active cohort includes
    # English-language Pokemon and One Piece cards traded on SNK).
    if ids.get("snkrdunk"):
        mode = _poll_mode(
            has_stock=bool(row["sales"]["snkAny"]),
            observed_at=row.get("_snkSaleMax"),
            checkpoint=_checkpoint_for(checkpoints, "snk_trades", vid, ids["snkrdunk"]),
            empty_poll_is_complete=True,
        )
        needs.append({
            "adapter": "snk_trades",
            "modeNeeded": mode,
            "externalId": ids["snkrdunk"],
            "transport": "http_curl",
            "polarRole": "ja_sales_primary" if lang == "ja" else "cross_market_sales",
        })
        # Only the strict pair is pollable for price: the kline ingest resolves
        # identity through operator_strict_source_identity and skips anything
        # else, and one such skip fails the whole adapter contract. Cards held
        # back here are counted in status as snkPriceIdentityNotStrict, never
        # dropped quietly.
        if ids.get("snkrdunkStrict"):
            pmode = _poll_mode(
                has_stock=bool(row["prices"]["snkAny"]),
                observed_at=row.get("_snkPriceMax"),
                checkpoint=_checkpoint_for(
                    checkpoints, "snk_price", vid, ids["snkrdunkStrict"]
                ),
                empty_poll_is_complete=True,
            )
            needs.append({
                "adapter": "snk_price",
                "modeNeeded": pmode,
                "externalId": ids["snkrdunkStrict"],
                "transport": "http_curl",
                "polarRole": "ja_price_primary" if lang == "ja" else "cross_market_price",
            })
    elif lang != "en":
        needs.append({"adapter": "bind_snk", "modeNeeded": "bind", "externalId": None, "transport": None, "polarRole": "ja_identity"})

    # SNK EN default-image authority is independent of card language.  It is
    # admitted only from the exact `snkrdunk` catalog binding; legacy `snk`
    # aliases and generic CDN pointers are deliberately ineligible.
    snk_en_external = str(ids.get("snkrdunkEn") or "").strip()
    if snk_en_external:
        accepted = row.get("snkEnAccepted") or {}
        has_stock = str(accepted.get("externalId") or "") == snk_en_external
        mode = _poll_mode(
            has_stock=has_stock,
            observed_at=accepted.get("observedAt") if has_stock else None,
            checkpoint=_checkpoint_for(
                checkpoints,
                "snk_en_image",
                vid,
                snk_en_external,
            ),
        )
        needs.append(
            {
                "adapter": "snk_en_image",
                "modeNeeded": mode,
                "externalId": snk_en_external,
                "identityEvidenceSha256": row.get(
                    "snkEnIdentityEvidenceSha256"
                ),
                "transport": "http_requests_no_browser",
                "polarRole": "canonical_en_default_image",
            }
        )

    if lang == "en":
        pc_external = str(ids.get("pricecharting") or "").strip()
        pc_exact_product = pc_external if pc_external.isdigit() else None
        if not pc_exact_product and not ids.get("snkrdunk"):
            needs.append({"adapter": "bind_pc_or_ebay", "modeNeeded": "bind", "externalId": None, "transport": "cdp_9333", "polarRole": "en_identity"})
        elif pc_exact_product:
            external = pc_exact_product
            smode = _poll_mode(
                has_stock=bool(row["sales"]["ebayAny"]),
                observed_at=row.get("_ebaySaleMax"),
                checkpoint=_checkpoint_for(checkpoints, "pc_ebay_sales", vid, external),
                empty_poll_is_complete=True,
            )
            needs.append({
                "adapter": "pc_ebay_sales",
                "modeNeeded": smode,
                "externalId": external,
                "transport": "cdp_9333",
                "polarRole": "en_sales_primary",
            })
            pmode = _poll_mode(
                has_stock=bool(row["prices"]["enExplicitPc"]),
                observed_at=row.get("_enExplicitPcMax"),
                checkpoint=_checkpoint_for(checkpoints, "en_price_ref", vid, external),
            )
            needs.append({
                "adapter": "en_price_ref",
                "modeNeeded": pmode,
                "externalId": external,
                "transport": "cdp_9333",
                "polarRole": "en_price_fallback",
            })
    return needs


def build_registry(
    rows: list[dict[str, Any]],
    checkpoints: dict[tuple[str, str], dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    reg = []
    for row in rows:
        for need in classify_needs(row, checkpoints):
            reg.append({
                "variantId": row["variantId"],
                "opaqueId": row["opaqueId"],
                "lang": row["lang"],
                "name": row["name"],
                **need,
                "builtAt": utc_now(),
            })
    return reg


def write_registry(reg: list[dict[str, Any]]) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with REGISTRY_PATH.open("w", encoding="utf-8") as f:
        for item in reg:
            f.write(json.dumps(item, ensure_ascii=False, default=str) + "\n")
    return REGISTRY_PATH


def freshness_summary(
    cur, registry: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    out = {"asOf": utc_now(), "slaHours": SLA_HOURS, "streams": {}, "polls": {}}
    queries = [
        ("snk_sales", "SELECT MAX(sold_at) m, COUNT(*) n FROM market_sale_observation WHERE source_code IN ('snkrdunk','snk','snk_psa10')"),
        ("ebay_sales", "SELECT MAX(sold_at) m, COUNT(*) n FROM market_sale_observation WHERE source_code='ebay'"),
        ("snk_price", "SELECT MAX(effective_at) m, COUNT(*) n FROM market_price_observation WHERE source_code IN ('snk_psa10','snkrdunk','snk')"),
        ("ebay_price", "SELECT MAX(effective_at) m, COUNT(*) n FROM market_price_observation WHERE source_code='ebay'"),
        ("pc_price", "SELECT MAX(effective_at) m, COUNT(*) n FROM market_price_observation WHERE source_code='pricecharting'"),
        ("gemrate_pop", "SELECT MAX(effective_at) m, COUNT(*) n FROM market_grader_population_observation WHERE source_code='gemrate'"),
    ]
    if _migration_029_ready(cur):
        queries.append(
            (
                "snk_en_image",
                "SELECT MAX(canonical_image_source_observed_at) m, COUNT(*) n "
                "FROM operator_canonical_image_projection",
            )
        )
    for name, sql in queries:
        cur.execute(sql)
        r = cur.fetchone()
        m = r["m"] if isinstance(r, dict) else r[0]
        n = int((r["n"] if isinstance(r, dict) else r[1]) or 0)
        age = _age_hours(m)
        # A negative age means the newest row is stamped in the FUTURE, which is
        # not freshness -- it is a stamping defect. SNK daily bars carry
        # effective_at = observed_date 23:59:59 (snk_market_data.py:1303), so
        # this stream reads -19.33 h right now and used to report slaOk=1. A
        # dead feed can hide behind that for a whole extra day. Say what is true:
        # ok requires the stamp to be in the past AND inside the SLA.
        future = age is not None and age < 0
        out["streams"][name] = {
            "maxAt": m.isoformat(sep=" ") if hasattr(m, "isoformat") else m,
            "rows": n,
            "ageHours": None if age is None else round(age, 2),
            "futureStamped": future,
            "slaOk": age is not None and 0 <= age <= SLA_HOURS,
        }
    checkpoints = load_checkpoints(cur)
    for adapter in CHECKPOINT_ADAPTERS:
        expected = [
            item
            for item in (registry or [])
            if item.get("adapter") == adapter and item.get("externalId") is not None
        ]
        active = [
            _checkpoint_for(
                checkpoints,
                adapter,
                int(item["variantId"]),
                item.get("externalId"),
            )
            for item in expected
        ]
        active = [row for row in active if row is not None]
        if registry is None:
            active = [row for (source, _), row in checkpoints.items() if source == adapter]
            expected_count = len(active)
        else:
            expected_count = len(expected)
        times = [_parse_datetime(row.get("last_effective_at")) for row in active]
        times = [value for value in times if value is not None]
        oldest = min(times) if times else None
        newest = max(times) if times else None
        oldest_age = _age_hours(_parse_datetime(oldest))
        out["polls"][adapter] = {
            "expectedStreams": expected_count,
            "checkpointedStreams": len(active),
            "missingStreams": max(0, expected_count - len(active)),
            "oldestSuccessAt": oldest.isoformat(sep=" ") if hasattr(oldest, "isoformat") else oldest,
            "newestSuccessAt": newest.isoformat(sep=" ") if hasattr(newest, "isoformat") else newest,
            "oldestAgeHours": None if oldest_age is None else round(oldest_age, 2),
            "slaOk": (
                expected_count > 0
                and len(active) == expected_count
                and oldest_age is not None
                and oldest_age <= SLA_HOURS
            ),
        }
    return out


def cmd_status(*, rebuild_registry: bool = True) -> dict[str, Any]:
    load_env()
    conn = db()
    cur = conn.cursor()
    try:
        rows = load_universe_rows(cur)
        checkpoints = load_checkpoints(cur)
        reg = build_registry(rows, checkpoints) if rebuild_registry else []
        if rebuild_registry:
            write_registry(reg)
        counts = {
            "universe": len(rows),
            "registryRows": len(reg),
            "byAdapterMode": {},
            "stockDue": 0,
            "incrDue": 0,
            "bindDue": 0,
            "ok": 0,
        }
        for item in reg:
            key = f"{item['adapter']}:{item['modeNeeded']}"
            counts["byAdapterMode"][key] = counts["byAdapterMode"].get(key, 0) + 1
            mode = item["modeNeeded"]
            if mode == "stock":
                counts["stockDue"] += 1
            elif mode == "incr":
                counts["incrDue"] += 1
            elif mode == "bind":
                counts["bindDue"] += 1
            elif mode == "ok":
                counts["ok"] += 1
        # A card with an exact SNK binding whose pair is not in the strict view
        # cannot be kline-ingested, so it is not in the poll list above. Say so
        # here: a gap that only shows up as an absence is a gap nobody reads.
        not_strict = sorted(
            int(row["variantId"])
            for row in rows
            if (row.get("ids") or {}).get("snkrdunk")
            and not (row.get("ids") or {}).get("snkrdunkStrict")
        )
        counts["snkPriceIdentityNotStrict"] = len(not_strict)
        fresh = freshness_summary(cur, reg)
        report = {
            "action": "status",
            "asOf": utc_now(),
            "counts": counts,
            "freshness": fresh,
            "registryPath": str(REGISTRY_PATH) if rebuild_registry else None,
            "snkPriceIdentityNotStrict": not_strict,
            "notes": [
                "stockDue = residual full pulls",
                "incrDue = stale beyond SLA",
                "bindDue = missing identity before collect",
                "Polaris display is separate (operator_control.latest_prices)",
            ],
        }
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        LAST_STATUS.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        return report
    finally:
        conn.close()


def _due(reg: list[dict[str, Any]], adapter: str, modes: set[str]) -> list[dict[str, Any]]:
    return [r for r in reg if r.get("adapter") == adapter and r.get("modeNeeded") in modes]


def _snk_ids(items: list[dict[str, Any]], limit: int | None) -> list[int]:
    ids = []
    seen = set()
    for it in items:
        raw = it.get("externalId")
        if raw is None:
            continue
        try:
            n = int(str(raw).strip())
        except ValueError:
            continue
        if n in seen:
            continue
        seen.add(n)
        ids.append(n)
        if limit is not None and len(ids) >= limit:
            break
    return ids


def _ingest_gemrate_manifest(
    manifest: dict[str, Any],
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    resolved = list(manifest.get("resolved") or [])
    item_by_external = {str(item.get("externalId") or ""): item for item in items}
    resolved = [row for row in resolved if str(row.get("gemrateId") or "") in item_by_external]
    if len(resolved) != len(item_by_external):
        missing = sorted(set(item_by_external) - {str(row.get("gemrateId") or "") for row in resolved})
        raise RuntimeError(f"GemRate manifest is incomplete for {len(missing)} active IDs")

    fetched_at = _parse_datetime(manifest.get("fetchedAt")) or datetime.now(timezone.utc)
    fetched_naive = fetched_at.astimezone(timezone.utc).replace(tzinfo=None)
    manifest_sha = _sha256(manifest)
    run_key = _sha256({"source": "gemrate", "manifestSha256": manifest_sha})
    conn = db()
    payload_sha_by_external: dict[str, str] = {}
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO market_ingest_run
                (run_key, source_code, ingest_mode, effective_at, payload_sha256,
                 manifest_sha256, status, observed_count, accepted_count,
                 quarantined_count, rejected_count, started_at, completed_at)
            VALUES (%s,'gemrate','incremental',%s,%s,%s,'completed',%s,%s,0,0,%s,%s)
            ON DUPLICATE KEY UPDATE id=LAST_INSERT_ID(id), status='completed',
                observed_count=VALUES(observed_count), accepted_count=VALUES(accepted_count),
                completed_at=VALUES(completed_at)
            """,
            (
                run_key,
                fetched_naive,
                manifest_sha,
                manifest_sha,
                len(resolved),
                len(resolved),
                fetched_naive,
                fetched_naive,
            ),
        )
        run_id = int(cur.lastrowid)
        for row in resolved:
            external = str(row["gemrateId"])
            item = item_by_external[external]
            effective_date = str(row["effectiveDate"])[:10]
            effective_at = f"{effective_date} 00:00:00"
            population = int(row["populationPsa10"])
            payload = {
                "grader": "PSA",
                "grade": "10",
                "population": population,
                "transport": row.get("transport"),
                "effectiveDateSource": row.get("effectiveDateSource"),
                "fetchedAt": manifest.get("fetchedAt"),
            }
            payload_sha = _sha256(payload)
            payload_sha_by_external[external] = payload_sha
            cur.execute(
                """
                INSERT INTO market_source_observation
                    (run_id, source_code, external_entity_id, observation_kind,
                     effective_at, observed_date, payload_sha256, payload_json, observed_at)
                VALUES (%s,'gemrate',%s,'grader_population_psa10',%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE run_id=VALUES(run_id), observed_at=VALUES(observed_at)
                """,
                (
                    run_id,
                    external,
                    effective_at,
                    effective_date,
                    payload_sha,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                    fetched_naive,
                ),
            )
            cur.execute(
                """
                INSERT INTO market_grader_population_observation
                    (run_id, variant_id, source_code, external_entity_id, grader_code,
                     top_grade_label, total_population, top_grade_population, estimated,
                     effective_at, observed_date, payload_sha256)
                VALUES (%s,%s,'gemrate',%s,'PSA','10',NULL,%s,0,%s,%s,%s)
                -- top_grade_label and estimated sit outside uq_market_grader_population
                -- (variant_id, grader_code, source_code, observed_date), so a row an
                -- undecomposed 'top' lane landed first for the same day keeps that label
                -- unless we restate it here -- and the population acceptance lane reads
                -- the label, not the number. Restating is what makes this row mean PSA 10.
                ON DUPLICATE KEY UPDATE
                    run_id=VALUES(run_id), external_entity_id=VALUES(external_entity_id),
                    top_grade_label=VALUES(top_grade_label), estimated=VALUES(estimated),
                    top_grade_population=GREATEST(top_grade_population,VALUES(top_grade_population)),
                    effective_at=VALUES(effective_at), payload_sha256=VALUES(payload_sha256)
                """,
                (
                    run_id,
                    int(item["variantId"]),
                    external,
                    population,
                    effective_at,
                    effective_date,
                    payload_sha,
                ),
            )
        checkpointed = _upsert_checkpoints(
            cur,
            adapter="gemrate_pop",
            items=items,
            run_id=run_id,
            completed_at=fetched_naive,
            payload_sha_by_external=payload_sha_by_external,
        )
        conn.commit()
        return {
            "runId": run_id,
            "inserted": len(resolved),
            "checkpointed": checkpointed,
            "payloadShaByExternal": payload_sha_by_external,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def run_gemrate_pop(
    items: list[dict[str, Any]],
    *,
    mode: str,
    limit: int | None,
    dry_run: bool,
) -> dict[str, Any]:
    active, quarantined = _partition_quarantined("gemrate_pop", items)
    selected = _unique_items(active, limit)
    report: dict[str, Any] = {
        "adapter": "gemrate_pop",
        "mode": mode,
        "due": len(items),
        "processed": len(selected),
        "quarantined": len(quarantined),
        "quarantinedItems": quarantined,
        "failed": 0,
        "failedItems": [],
        "ok": True,
    }
    if not selected:
        report["note"] = (
            "all due GemRate IDs are quarantined" if quarantined else "no exact GemRate IDs due"
        )
        return report
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ids_path = OUT_DIR / f"gemrate_ids_{mode}.txt"
    ids = [str(item["externalId"]) for item in selected]
    ids_path.write_text("\n".join(ids) + "\n", encoding="utf-8")
    if dry_run:
        report.update({"dryRun": True, "checkpointed": 0})
        return report

    before = set((ROOT / "data/private/gemrate/runs").glob("daily_*/manifest.json"))
    command = [
        PY,
        "-X",
        "utf8",
        "pipelines/gemrate_source.py",
        "daily",
        "--ids-file",
        str(ids_path),
        "--speed",
        "fast",
        "--website-budget-seconds",
        "5400",
    ]
    # The kill timer must leave headroom above the child's website budget
    # (5400s): the child also runs the direct-API leg, the mirror pass and two
    # grader-volume browser launches. Aliasing the two guaranteed a SIGKILL
    # exactly when the website budget was actually needed.
    report["run"] = _run(command, timeout=max(2 * 5400, 10 * len(selected)), dry_run=False)
    # Exit 1 is the child's declared-partial signal (some IDs unresolved); the
    # manifest still carries every resolved row, so per-item ingest continues.
    exit_code = report["run"].get("exit")
    if exit_code not in (0, 1):
        report.update({"ok": False, "error": "gemrate_daily_failed", "checkpointed": 0})
        return report
    after = set((ROOT / "data/private/gemrate/runs").glob("daily_*/manifest.json"))
    candidates = sorted(after - before, key=lambda path: path.stat().st_mtime, reverse=True)
    if not candidates:
        candidates = sorted(after, key=lambda path: path.stat().st_mtime, reverse=True)
    if not candidates:
        report.update({"ok": False, "error": "gemrate_manifest_missing", "checkpointed": 0})
        return report
    manifest_path = candidates[0]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("mismatches"):
        # Direct/mirror disagreement is a source-integrity failure, never a
        # per-item one; nothing may advance from a mismatched manifest.
        report.update({"ok": False, "error": "gemrate_manifest_mismatch", "manifest": str(manifest_path), "checkpointed": 0})
        return report
    if exit_code != 0 and not manifest.get("partial"):
        # A non-zero exit without a declared partial manifest is an infra
        # failure; per-item streaks must not advance on it.
        report.update({"ok": False, "error": "gemrate_daily_failed", "manifest": str(manifest_path), "checkpointed": 0})
        return report
    if exit_code == 0 and not (manifest.get("promoted") and manifest.get("promotable")):
        report.update({"ok": False, "error": "gemrate_manifest_not_promotable", "manifest": str(manifest_path), "checkpointed": 0})
        return report

    # Per-item split: only manifest-resolved IDs advance; unresolved IDs fail
    # individually and feed the quarantine streak instead of the whole lane.
    resolved_ids = {str(row.get("gemrateId") or "") for row in (manifest.get("resolved") or [])}
    identity_failed = {
        str(row.get("gemrateId") or ""): str(row.get("reason") or "identity_receipt_failed")
        for row in (manifest.get("directIdentityReceiptFailures") or [])
    }
    unresolved_reasons = {
        str(row.get("gemrateId") or ""): str(row.get("reason") or "unresolved")
        for row in (manifest.get("unresolved") or [])
    }
    ok_items: list[dict[str, Any]] = []
    failed_items: list[dict[str, Any]] = []
    for item in selected:
        external = str(item.get("externalId") or "")
        if external in resolved_ids and external not in identity_failed:
            ok_items.append(item)
            continue
        failed_items.append(
            {
                "variantId": int(item["variantId"]),
                "externalId": external,
                "error": identity_failed.get(external)
                or unresolved_reasons.get(external)
                or "gemrate_manifest_missing_id",
            }
        )
    if failed_items:
        # Per-item source failures advance the quarantine streak immediately so
        # they persist even if the ingest step below fails for other reasons.
        _record_item_outcomes("gemrate_pop", succeeded=[], failed=failed_items)
    write: dict[str, Any] = {"runId": None, "inserted": 0, "checkpointed": 0}
    if ok_items:
        try:
            write = _ingest_gemrate_manifest(manifest, ok_items)
        except Exception as exc:  # noqa: BLE001
            report.update({"ok": False, "error": f"gemrate_ingest:{type(exc).__name__}:{exc}", "checkpointed": 0})
            return report
        payload_sha_by_external = write.pop("payloadShaByExternal", None) or {}
        _persist_item_checkpoints(
            "gemrate_pop",
            mode=mode,
            items=ok_items,
            payload_sha_by_external=payload_sha_by_external,
            run_id=write.get("runId"),
        )
        _record_item_outcomes("gemrate_pop", succeeded=ok_items, failed=[])
    report.update({"manifest": str(manifest_path), **write})
    report["failed"] = len(failed_items)
    report["failedItems"] = failed_items
    if failed_items:
        report.update({"ok": False, "error": "gemrate_partial_items_failed"})
    return report


def _snk_selection(
    items: list[dict[str, Any]], limit: int | None
) -> tuple[list[dict[str, Any]], list[int]]:
    selected = _unique_items(items, limit)
    ids: list[int] = []
    seen: set[int] = set()
    for item in selected:
        try:
            external_id = int(str(item.get("externalId") or "").strip())
        except ValueError as exc:
            raise RuntimeError(f"invalid SNK external ID for variant {item['variantId']}") from exc
        if external_id in seen:
            raise RuntimeError(f"duplicate SNK external ID in active cohort: {external_id}")
        seen.add(external_id)
        ids.append(external_id)
    return selected, ids


def _validate_snk_harvest(
    harvest_path: Path, requested_ids: list[int]
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    rows = _jsonl_rows(harvest_path)
    by_id: dict[int, dict[str, Any]] = {}
    for row in rows:
        item_id = row.get("item_id")
        if not isinstance(item_id, int) or item_id in by_id or row.get("error"):
            raise RuntimeError("SNK harvest contains invalid, duplicate, or error rows")
        by_id[item_id] = row
    if set(by_id) != set(requested_ids):
        missing = sorted(set(requested_ids) - set(by_id))
        extra = sorted(set(by_id) - set(requested_ids))
        raise RuntimeError(f"SNK harvest exact-ID mismatch missing={missing[:10]} extra={extra[:10]}")
    return rows, {str(item_id): _sha256(by_id[item_id]) for item_id in requested_ids}


def _partition_snk_harvest(
    harvest_path: Path, requested_ids: list[int]
) -> tuple[dict[int, dict[str, Any]], dict[int, str]]:
    """Split one harvest into per-item rows and per-item failure reasons.

    A duplicate or foreign row is still a whole-harvest contract violation; a
    per-row source error or a missing row only fails that one item.
    """
    requested = set(requested_ids)
    rows_by_id: dict[int, dict[str, Any]] = {}
    failed_by_id: dict[int, str] = {}
    for row in _jsonl_rows(harvest_path):
        item_id = row.get("item_id")
        if not isinstance(item_id, int) or item_id not in requested:
            raise RuntimeError(f"SNK harvest contains an invalid or foreign row: {item_id!r}")
        if item_id in rows_by_id or item_id in failed_by_id:
            raise RuntimeError(f"SNK harvest contains a duplicate row: {item_id}")
        error = row.get("error")
        if error:
            failed_by_id[item_id] = f"snk_harvest_row_error:{str(error)[:200]}"
        else:
            rows_by_id[item_id] = row
    for item_id in requested - set(rows_by_id) - set(failed_by_id):
        failed_by_id[item_id] = "snk_harvest_row_missing"
    return rows_by_id, failed_by_id


def _snk_harvest_once(
    ids: list[int],
    *,
    label: str,
    mode: str,
    delay: float,
    workers: int,
) -> dict[str, Any]:
    """Run the SNKRDUNK harvest child exactly once for one exact-ID list."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    ids_path = OUT_DIR / f"{label}_ids_{mode}_{stamp}.txt"
    harvest_path = OUT_DIR / f"{label}_harvest_{mode}_{stamp}.jsonl"
    ids_path.write_text("\n".join(str(value) for value in ids) + "\n", encoding="ascii")
    harvest_cmd = [
        PY,
        "-X",
        "utf8",
        "pipelines/snk_market_data.py",
        "--ids-file",
        str(ids_path),
        "--out",
        str(harvest_path),
        "--delay",
        str(delay),
        "--workers",
        str(max(1, int(workers))),
        "--condition",
        "trading_card_single_psa10",
    ]
    result: dict[str, Any] = {
        "ok": True,
        "idsPath": str(ids_path),
        "harvestPath": str(harvest_path),
        "requestedIds": list(ids),
        "run": _run(harvest_cmd, timeout=max(180, 8 * len(ids)), dry_run=False),
    }
    if result["run"].get("exit") != 0 or not harvest_path.is_file():
        result.update({"ok": False, "error": "snk_harvest_failed"})
        return result
    try:
        result["rowsById"], result["failedById"] = _partition_snk_harvest(harvest_path, ids)
    except Exception as exc:  # noqa: BLE001
        result.update({"ok": False, "error": f"snk_harvest_contract:{type(exc).__name__}:{exc}"})
    return result


def _validate_snk_price_ingest(
    ingest_summary: dict[str, Any], requested_ids: list[int]
) -> dict[str, Any]:
    """Treat an exact-ID poll with no kline as a successful, checkpointable poll."""
    requested = set(requested_ids)
    accepted = {int(value) for value in (ingest_summary.get("acceptedItemIds") or [])}
    skipped = list(ingest_summary.get("skipped") or [])
    empty = {
        int(row["itemId"])
        for row in skipped
        if str(row.get("reason") or "") == "empty_kline" and row.get("itemId") is not None
    }
    other_skips = [row for row in skipped if str(row.get("reason") or "") != "empty_kline"]
    hard_skip_keys = (
        "skippedAllowlist",
        "skippedCondition",
        "skippedErrorRow",
        "skippedNoExactIdentity",
    )
    hard_skip_counts = {
        key: int(ingest_summary.get(key) or 0)
        for key in hard_skip_keys
    }
    if accepted & empty:
        raise RuntimeError("SNK price ingest accepted/empty sets overlap")
    if other_skips or any(hard_skip_counts.values()):
        raise RuntimeError(
            f"SNK price ingest contains non-empty-kline skips: "
            f"rows={len(other_skips)} counters={hard_skip_counts}"
        )
    if int(ingest_summary.get("skippedEmptyKline") or 0) != len(empty):
        raise RuntimeError("SNK price empty-kline counter does not match skipped rows")
    polled = accepted | empty
    if polled != requested:
        missing = sorted(requested - polled)
        extra = sorted(polled - requested)
        raise RuntimeError(
            f"SNK price ingest exact-ID mismatch missing={missing[:10]} extra={extra[:10]}"
        )
    return {
        "accepted": len(accepted),
        "emptyKline": len(empty),
        "polled": len(polled),
    }


def _run_snk_adapter(
    items: list[dict[str, Any]],
    *,
    adapter: str,
    mode: str,
    limit: int | None,
    dry_run: bool,
    delay: float,
    workers: int,
    shared_harvest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    active, quarantined = _partition_quarantined(adapter, items)
    try:
        selected, ids = _snk_selection(active, limit)
    except Exception as exc:  # noqa: BLE001
        return {
            "adapter": adapter,
            "mode": mode,
            "due": len(items),
            "processed": 0,
            "checkpointed": 0,
            "quarantined": len(quarantined),
            "quarantinedItems": quarantined,
            "ok": False,
            "error": f"snk_selection:{type(exc).__name__}:{exc}",
        }
    report: dict[str, Any] = {
        "adapter": adapter,
        "mode": mode,
        "due": len(items),
        "processed": len(selected),
        "checkpointed": 0,
        "quarantined": len(quarantined),
        "quarantinedItems": quarantined,
        "failed": 0,
        "failedItems": [],
        "ok": True,
    }
    if not selected:
        report["note"] = (
            "all due SNK IDs are quarantined" if quarantined else "no exact SNK IDs due"
        )
        return report
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    ids_path = OUT_DIR / f"{adapter}_ids_{mode}_{stamp}.txt"
    harvest_path = OUT_DIR / f"{adapter}_harvest_{mode}_{stamp}.jsonl"
    if dry_run:
        ids_path.write_text("\n".join(str(value) for value in ids) + "\n", encoding="ascii")
        report.update({"idsPath": str(ids_path), "harvestPath": str(harvest_path)})
        report.update({"dryRun": True, "note": "exact IDs selected; network and DB writes not executed"})
        return report

    started_at = datetime.now(timezone.utc).replace(tzinfo=None)
    if shared_harvest is not None and set(ids) <= set(shared_harvest.get("requestedIds") or []):
        # One collect run performs one SNKRDUNK harvest; both ingest consumers
        # (trades and price) read the same immutable rows instead of refetching.
        if not shared_harvest.get("ok"):
            report.update(
                {
                    "harvest": shared_harvest.get("run"),
                    "ok": False,
                    "error": str(shared_harvest.get("error") or "snk_harvest_failed"),
                }
            )
            return report
        rows_by_id = {
            item_id: shared_harvest["rowsById"][item_id]
            for item_id in ids
            if item_id in shared_harvest["rowsById"]
        }
        failed_by_id = {
            item_id: shared_harvest["failedById"][item_id]
            for item_id in ids
            if item_id in shared_harvest["failedById"]
        }
        shared_run = shared_harvest.get("run") or {}
        report["harvest"] = {
            "exit": 0,
            "reusedSharedHarvest": True,
            "sharedHarvestPath": shared_harvest.get("harvestPath"),
            "startedAt": shared_run.get("startedAt"),
            "finishedAt": shared_run.get("finishedAt"),
        }
        shared_started = _parse_datetime(shared_run.get("startedAt"))
        if shared_started is not None:
            started_at = _utc_naive(shared_started)
    else:
        harvest = _snk_harvest_once(
            ids, label=f"{adapter}_raw", mode=mode, delay=delay, workers=workers
        )
        report["harvest"] = harvest.get("run")
        if not harvest.get("ok"):
            report.update({"ok": False, "error": str(harvest.get("error") or "snk_harvest_failed")})
            return report
        rows_by_id = harvest["rowsById"]
        failed_by_id = harvest["failedById"]

    ok_items: list[dict[str, Any]] = []
    ok_ids: list[int] = []
    failed_items: list[dict[str, Any]] = []
    for item, external_id in zip(selected, ids):
        if external_id in rows_by_id:
            ok_items.append(item)
            ok_ids.append(external_id)
            continue
        failed_items.append(
            {
                "variantId": int(item["variantId"]),
                "externalId": str(item.get("externalId") or ""),
                "error": failed_by_id.get(external_id, "snk_harvest_row_missing"),
            }
        )
    report["failed"] = len(failed_items)
    report["failedItems"] = failed_items
    if failed_items:
        # Per-item source failures advance the quarantine streak immediately so
        # they persist even if the ingest step below fails for other reasons.
        _record_item_outcomes(adapter, succeeded=[], failed=failed_items)
    if not ok_items:
        report.update({"ok": False, "error": f"{adapter}_all_items_failed"})
        return report

    # The ingest consumer only ever sees the validated exact-ID subset; the raw
    # harvest (including error rows) stays immutable at its own path.
    ids_path.write_text("\n".join(str(value) for value in ok_ids) + "\n", encoding="ascii")
    harvest_path.write_text(
        "".join(
            json.dumps(rows_by_id[item_id], ensure_ascii=False, sort_keys=True) + "\n"
            for item_id in ok_ids
        ),
        encoding="utf-8",
    )
    report.update({"idsPath": str(ids_path), "harvestPath": str(harvest_path)})
    try:
        raw_rows, payload_sha = _validate_snk_harvest(harvest_path, ok_ids)
    except Exception as exc:  # noqa: BLE001
        report.update({"ok": False, "error": f"snk_harvest_contract:{type(exc).__name__}:{exc}"})
        return report

    if adapter == "snk_trades":
        ingest_cmd = [
            PY,
            "-X",
            "utf8",
            "pipelines/ingest_snk_trades_sales.py",
            "--harvest",
            str(harvest_path),
        ]
    else:
        ingest_report_path = OUT_DIR / f"snk_price_ingest_{mode}_{stamp}.json"
        ingest_cmd = [
            PY,
            "-X",
            "utf8",
            "pipelines/snk_market_data.py",
            "--ingest-jsonl",
            str(harvest_path),
            "--condition",
            "trading_card_single_psa10",
            "--out",
            str(ingest_report_path),
        ]
    report["ingest"] = _run(
        ingest_cmd,
        timeout=max(180, 5 * len(ok_ids)),
        dry_run=False,
    )
    if report["ingest"].get("exit") != 0:
        report.update({"ok": False, "error": f"{adapter}_ingest_failed"})
        return report
    if adapter == "snk_price":
        try:
            ingest_summary = json.loads(ingest_report_path.read_text(encoding="utf-8-sig"))
            ingest_contract = _validate_snk_price_ingest(ingest_summary, ok_ids)
        except Exception as exc:  # noqa: BLE001
            report.update({"ok": False, "error": f"snk_price_ingest_contract:{type(exc).__name__}:{exc}"})
            return report
        report.update(ingest_contract)
    try:
        checkpoint = record_successful_poll(
            adapter=adapter,
            mode=mode,
            items=ok_items,
            payload={"harvest": raw_rows, "ingest": report["ingest"]},
            started_at=started_at,
            payload_sha_by_external=payload_sha,
        )
    except Exception as exc:  # noqa: BLE001
        report.update({"ok": False, "error": f"checkpoint:{type(exc).__name__}:{exc}"})
        return report
    _persist_item_checkpoints(
        adapter,
        mode=mode,
        items=ok_items,
        payload_sha_by_external=payload_sha,
        run_id=checkpoint.get("runId"),
    )
    _record_item_outcomes(adapter, succeeded=ok_items, failed=[])
    report.update(checkpoint)
    if failed_items:
        report.update({"ok": False, "error": f"{adapter}_partial_items_failed"})
    return report


def _snk_en_harvest_files() -> list[Path]:
    candidates = [
        *OUT_DIR.glob("snk_*harvest*.jsonl"),
        *OUT_DIR.glob("binding_delta_snk_harvest.jsonl"),
        *(ROOT / "data" / "runtime" / "operator").glob(
            "snk-global-market*.jsonl"
        ),
    ]
    return sorted(
        {path.resolve() for path in candidates if path.is_file()},
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )


def _local_snk_master_cache(
    external_ids: set[str],
) -> dict[str, tuple[dict[str, Any], datetime, Path]]:
    """Index already-landed exact masters, newest immutable payload first."""

    found: dict[str, tuple[dict[str, Any], datetime, Path]] = {}
    for path in _snk_en_harvest_files():
        with path.open(encoding="utf-8-sig") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict):
                    continue
                external_id = str(row.get("item_id") or "").strip()
                if (
                    external_id not in external_ids
                    or row.get("error")
                ):
                    continue
                source_payload = row.get("source_payload")
                master = (
                    source_payload.get("master")
                    if isinstance(source_payload, Mapping)
                    else None
                )
                if not isinstance(master, Mapping):
                    continue
                try:
                    master_default_image(master, int(external_id))
                except (TypeError, ValueError):
                    continue
                observed_at = _parse_datetime(row.get("fetched_at"))
                if observed_at is None:
                    continue
                observed_at = _utc_naive(observed_at)
                previous = found.get(external_id)
                if previous is not None and previous[1] >= observed_at:
                    continue
                found[external_id] = (
                    dict(master),
                    observed_at,
                    path,
                )
    return found


def _local_snk_default_bytes(
    external_ids: set[str],
) -> dict[tuple[str, str], Path]:
    """Index exact URL-bound G10 bytes; a filename or SNK ID alone is not proof."""

    root = ROOT / "data" / "runtime" / "private-landing" / "g10"
    if not root.is_dir():
        return {}
    provider_roots = sorted(
        (path for path in root.rglob("snkrdunk") if path.parent.name == "cards"),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )
    found: dict[tuple[str, str], Path] = {}
    for provider_root in provider_roots:
        payload_root = provider_root.parents[1]
        image_root = payload_root / "images"
        for external_id in external_ids:
            asset_info = provider_root / external_id / "asset_info.json"
            if not asset_info.is_file():
                continue
            try:
                payload = json.loads(asset_info.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError):
                continue
            query = payload.get("assetQueryId")
            if (
                not isinstance(query, Mapping)
                or str(query.get("source") or "") != "snkrdunk"
                or str(query.get("id") or "") != external_id
            ):
                continue
            url = str(payload.get("image") or "").strip()
            if not url or (external_id, url) in found:
                continue
            paths = sorted(image_root.glob(f"snkrdunk_{external_id}.*"))
            if paths:
                found[(external_id, url)] = paths[0]
    return found


def _resolve_object_path(value: Any) -> Path:
    path = Path(str(value or ""))
    return path if path.is_absolute() else ROOT / path


def _load_existing_snk_en_acceptances(
    cur, items: list[dict[str, Any]]
) -> dict[int, dict[str, Any]]:
    if not items:
        return {}
    variant_ids = [int(item["variantId"]) for item in items]
    placeholders = ",".join(["%s"] * len(variant_ids))
    cur.execute(
        f"""
        SELECT ca.variant_id,ca.id AS acceptance_id,ca.evidence_sha256,
               l.id AS lineage_id,l.exact_item_id,l.product_url,
               l.master_payload_sha256,l.default_image_url,
               l.default_image_url_sha256,l.downloaded_bytes_sha256,
               l.processed_content_sha256,l.transform_sha256,
               l.identity_evidence_sha256,l.lineage_sha256,
               l.source_observed_at,
               page.product_page_payload_sha256,
               page.product_page_observed_at,page.authority_sha256,
               a.id AS image_asset_id,
               a.private_object_key,a.content_sha256,a.mime_type,
               q.semantic_match_status,q.card_number_match,q.language_match,
               q.tcg_match,q.raw_front_confirmed,q.public_allowed
        FROM market_canonical_image_acceptance ca
        INNER JOIN market_snk_en_storefront_lineage l
          ON l.id=ca.storefront_lineage_id AND l.variant_id=ca.variant_id
         AND l.lineage_sha256=ca.lineage_sha256
        INNER JOIN market_snk_en_product_page_authority page
          ON page.storefront_lineage_id=l.id
         AND page.variant_id=l.variant_id
         AND page.exact_item_id=l.exact_item_id
         AND page.product_url=l.product_url
         AND page.default_image_url=l.default_image_url
         AND page.master_payload_sha256=l.master_payload_sha256
         AND page.identity_evidence_sha256=l.identity_evidence_sha256
        INNER JOIN market_image_asset a
          ON a.id=ca.image_asset_id AND a.id=l.processed_image_asset_id
         AND a.variant_id=ca.variant_id
         AND a.content_sha256=l.processed_content_sha256
        INNER JOIN market_image_qc q
          ON q.image_asset_id=a.id
         AND q.id=(SELECT q2.id FROM market_image_qc q2
                   WHERE q2.image_asset_id=a.id
                   ORDER BY q2.checked_at DESC,q2.id DESC LIMIT 1)
        WHERE ca.variant_id IN ({placeholders})
          AND l.source_code='snkrdunk' AND l.storefront_code='en'
          AND NOT EXISTS (
            SELECT 1 FROM market_canonical_image_acceptance newer
            WHERE newer.supersedes_acceptance_id=ca.id
          )
        """,
        variant_ids,
    )
    return {int(row["variant_id"]): dict(row) for row in cur.fetchall()}


def _existing_snk_en_is_reusable(
    item: Mapping[str, Any], row: Mapping[str, Any]
) -> bool:
    external_id = str(item.get("externalId") or "").strip()
    product_url = exact_en_product_url(int(external_id))
    content_sha = str(row.get("content_sha256") or "")
    object_path = _resolve_object_path(row.get("private_object_key"))
    if (
        str(row.get("exact_item_id") or "") != external_id
        or str(row.get("product_url") or "") != product_url
        or str(row.get("default_image_url_sha256") or "")
        != _text_sha256(str(row.get("default_image_url") or ""))
        or str(row.get("processed_content_sha256") or "") != content_sha
        or str(row.get("mime_type") or "") != "image/webp"
        or str(row.get("identity_evidence_sha256") or "")
        != str(item.get("identityEvidenceSha256") or "")
        or str(row.get("semantic_match_status") or "")
        not in {"accepted_freeze", "human_or_vision_confirmed"}
        or not all(
            int(row.get(field) or 0) == 1
            for field in (
                "card_number_match",
                "language_match",
                "tcg_match",
                "raw_front_confirmed",
                "public_allowed",
            )
        )
        or not all(
            SHA256_RE.fullmatch(str(row.get(field) or ""))
            for field in (
                "master_payload_sha256",
                "downloaded_bytes_sha256",
                "processed_content_sha256",
                "transform_sha256",
                "identity_evidence_sha256",
                "lineage_sha256",
                "evidence_sha256",
                "product_page_payload_sha256",
                "authority_sha256",
            )
        )
        or row.get("product_page_observed_at") is None
        or not object_path.is_file()
    ):
        return False
    try:
        return _bytes_sha256(object_path.read_bytes()) == content_sha
    except OSError:
        return False


def _prepare_snk_en_target(
    item: Mapping[str, Any],
    *,
    api_pool: SnkrdunkApiPool,
    master_cache: Mapping[str, tuple[dict[str, Any], datetime, Path]],
    bytes_cache: Mapping[tuple[str, str], Path],
) -> SnkEnPrepared:
    variant_id = int(item["variantId"])
    external_id = str(item.get("externalId") or "").strip()
    if not external_id.isdigit() or int(external_id) <= 0:
        raise ValueError(f"invalid exact SNK external ID: {external_id}")
    identity_evidence = str(item.get("identityEvidenceSha256") or "")
    if not SHA256_RE.fullmatch(identity_evidence):
        raise ValueError(
            f"exact snkrdunk binding lacks hash-bound identity evidence: {external_id}"
        )

    cached = master_cache.get(external_id)
    if cached is None:
        master = api_pool.get_master(int(external_id))
        source_observed_at = _utc_naive()
        master_reused = False
    else:
        master, source_observed_at, _ = cached
        master_reused = True
    default_url = master_default_image(master, int(external_id))
    page_authority = api_pool.get_en_product_page_authority(
        int(external_id), default_url
    )
    if (
        page_authority.get("contract") != SNK_EN_PRODUCT_PAGE_CONTRACT
        or page_authority.get("productUrl")
        != exact_en_product_url(int(external_id))
        or page_authority.get("finalUrl") != page_authority.get("productUrl")
        or int(page_authority.get("httpStatus") or 0) != 200
        or page_authority.get("defaultImageUrl") != default_url
        or not SHA256_RE.fullmatch(
            str(page_authority.get("productPagePayloadSha256") or "")
        )
    ):
        raise RuntimeError(f"incomplete SNK EN product-page authority: {external_id}")
    product_page_observed_at = _utc_naive()
    local_bytes_path = bytes_cache.get((external_id, default_url))
    if local_bytes_path is not None:
        raw = local_bytes_path.read_bytes()
        captured_at = _utc_naive(source_observed_at)
        bytes_reused = True
        downloaded = False
    else:
        raw = api_pool.get_bytes(default_url)
        captured_at = _utc_naive()
        bytes_reused = False
        downloaded = True
    processed = process_snk_default_image(raw)
    return SnkEnPrepared(
        variant_id=variant_id,
        external_id=external_id,
        product_url=exact_en_product_url(int(external_id)),
        product_page_payload_sha256=str(
            page_authority["productPagePayloadSha256"]
        ),
        product_page_observed_at=product_page_observed_at,
        master_payload_sha256=_sha256(master),
        default_image_url=default_url,
        default_image_url_sha256=_text_sha256(default_url),
        downloaded_bytes_sha256=_bytes_sha256(raw),
        image=processed,
        identity_evidence_sha256=identity_evidence,
        source_observed_at=max(
            _utc_naive(source_observed_at), captured_at, product_page_observed_at
        ),
        captured_at=captured_at,
        master_reused=master_reused,
        bytes_reused=bytes_reused,
        downloaded=downloaded,
    )


def _write_snk_en_asset(image: ProcessedSnkDefaultImage) -> tuple[Path, str]:
    SNK_EN_ASSET_DIR.mkdir(parents=True, exist_ok=True)
    path = SNK_EN_ASSET_DIR / f"{image.content_sha256}.webp"
    relative = path.relative_to(ROOT).as_posix()
    if path.is_file():
        if _bytes_sha256(path.read_bytes()) != image.content_sha256:
            raise RuntimeError(f"content-addressed SNK EN asset hash mismatch: {path}")
        return path, relative
    temporary = path.with_name(f".{path.name}.{os.getpid()}.next")
    temporary.write_bytes(image.content)
    if _bytes_sha256(temporary.read_bytes()) != image.content_sha256:
        temporary.unlink(missing_ok=True)
        raise RuntimeError(f"SNK EN asset write hash mismatch: {path}")
    os.replace(temporary, path)
    return path, relative


def _upsert_snk_en_freeze(
    cur,
    *,
    variant_id: int,
    external_id: str,
    content_sha256: str,
    acceptance_id: int,
    lineage_sha256: str,
    evidence_sha256: str,
    accepted_at: datetime,
) -> None:
    cur.execute(
        """
        INSERT INTO operator_binding_freeze
          (variant_id,freeze_kind,source_code,external_entity_id,
           content_sha256,canonical_image_acceptance_id,
           accepted_lineage_sha256,acceptance_status,actor,
           evidence_sha256,note,accepted_at)
        VALUES (%s,'image',%s,%s,%s,%s,%s,'accepted',%s,%s,%s,%s)
        ON DUPLICATE KEY UPDATE
          external_entity_id=VALUES(external_entity_id),
          content_sha256=VALUES(content_sha256),
          canonical_image_acceptance_id=VALUES(canonical_image_acceptance_id),
          accepted_lineage_sha256=VALUES(accepted_lineage_sha256),
          acceptance_status='accepted',actor=VALUES(actor),
          evidence_sha256=VALUES(evidence_sha256),note=VALUES(note),
          accepted_at=VALUES(accepted_at)
        """,
        (
            variant_id,
            SNK_EN_FREEZE_SOURCE_CODE,
            external_id,
            content_sha256,
            acceptance_id,
            lineage_sha256,
            "collect_control:snk_en_image",
            evidence_sha256,
            "exact SNK EN storefront default image",
            accepted_at,
        ),
    )


def _assert_exact_snk_en_binding(cur, item: Mapping[str, Any]) -> str:
    variant_id = int(item["variantId"])
    external_id = str(item.get("externalId") or "").strip()
    cur.execute(
        """
        SELECT external_entity_id
        FROM catalog_source_identity
        WHERE variant_id=%s
          AND source_code IN ('snk','snkrdunk','snkrdunk_en')
          AND LOWER(match_status)='exact'
        LOCK IN SHARE MODE
        """,
        (variant_id,),
    )
    exact_ids = {
        str(binding.get("external_entity_id") or "").strip()
        for binding in cur.fetchall()
    }
    if exact_ids != {external_id}:
        raise RuntimeError(
            f"multiple or changed exact SNK IDs before commit: {variant_id}:{sorted(exact_ids)}"
        )
    cur.execute(
        """
        SELECT evidence_sha256
        FROM catalog_source_identity
        WHERE variant_id=%s AND source_code='snkrdunk'
          AND external_entity_id=%s AND LOWER(match_status)='exact'
        LIMIT 1
        LOCK IN SHARE MODE
        """,
        (variant_id, external_id),
    )
    row = cur.fetchone()
    evidence_sha = str((row or {}).get("evidence_sha256") or "")
    if (
        not SHA256_RE.fullmatch(evidence_sha)
        or evidence_sha != str(item.get("identityEvidenceSha256") or "")
    ):
        raise RuntimeError(
            f"exact snkrdunk binding changed before commit: {variant_id}:{external_id}"
        )
    return evidence_sha


def _checkpoint_snk_en_item(
    cur,
    *,
    item: dict[str, Any],
    mode: str,
    started_at: datetime,
    completed_at: datetime,
    lineage_sha256: str,
    payload: Mapping[str, Any],
) -> int:
    run_id = _insert_control_run(
        cur,
        adapter="snk_en_image",
        mode=mode,
        items=[item],
        payload=dict(payload),
        started_at=started_at,
        completed_at=completed_at,
    )
    checkpointed = _upsert_checkpoints(
        cur,
        adapter="snk_en_image",
        items=[item],
        run_id=run_id,
        completed_at=completed_at,
        payload_sha_by_external={str(item["externalId"]): lineage_sha256},
    )
    if checkpointed != 1:
        raise RuntimeError(f"SNK EN checkpoint did not advance exactly once: {item['externalId']}")
    return run_id


def _persist_reused_snk_en(
    cur,
    *,
    item: dict[str, Any],
    row: Mapping[str, Any],
    mode: str,
    started_at: datetime,
    completed_at: datetime,
) -> dict[str, Any]:
    _assert_exact_snk_en_binding(cur, item)
    lineage_sha = str(row["lineage_sha256"])
    evidence_sha = str(row["evidence_sha256"])
    _upsert_snk_en_freeze(
        cur,
        variant_id=int(item["variantId"]),
        external_id=str(item["externalId"]),
        content_sha256=str(row["content_sha256"]),
        acceptance_id=int(row["acceptance_id"]),
        lineage_sha256=lineage_sha,
        evidence_sha256=evidence_sha,
        accepted_at=completed_at,
    )
    run_id = _checkpoint_snk_en_item(
        cur,
        item=item,
        mode=mode,
        started_at=started_at,
        completed_at=completed_at,
        lineage_sha256=lineage_sha,
        payload={
            "contract": "snk-en-storefront-lineage-v1",
            "reusedAcceptanceId": int(row["acceptance_id"]),
            "lineageSha256": lineage_sha,
        },
    )
    return {"inserted": 0, "runId": run_id, "lineageSha256": lineage_sha}


def _persist_prepared_snk_en(
    cur,
    *,
    item: dict[str, Any],
    prepared: SnkEnPrepared,
    mode: str,
    started_at: datetime,
    completed_at: datetime,
) -> dict[str, Any]:
    _assert_exact_snk_en_binding(cur, item)
    _, private_object_key = _write_snk_en_asset(prepared.image)
    asset_source_version = _sha256(
        {
            "masterPayloadSha256": prepared.master_payload_sha256,
            "downloadedBytesSha256": prepared.downloaded_bytes_sha256,
            "transformSha256": prepared.image.transform_sha256,
        }
    )
    cur.execute(
        """
        INSERT INTO market_image_asset
          (variant_id,image_kind,content_sha256,private_object_key,mime_type,
           width_px,height_px,source_version_sha256,captured_at)
        VALUES (%s,'raw_front',%s,%s,%s,%s,%s,%s,%s)
        ON DUPLICATE KEY UPDATE
          private_object_key=VALUES(private_object_key),mime_type=VALUES(mime_type),
          width_px=VALUES(width_px),height_px=VALUES(height_px),
          source_version_sha256=VALUES(source_version_sha256),
          captured_at=GREATEST(captured_at,VALUES(captured_at))
        """,
        (
            prepared.variant_id,
            prepared.image.content_sha256,
            private_object_key,
            prepared.image.mime_type,
            prepared.image.width,
            prepared.image.height,
            asset_source_version,
            prepared.captured_at,
        ),
    )
    cur.execute(
        """
        SELECT id FROM market_image_asset
        WHERE variant_id=%s AND image_kind='raw_front' AND content_sha256=%s
        LIMIT 1
        """,
        (prepared.variant_id, prepared.image.content_sha256),
    )
    asset = cur.fetchone()
    if not asset:
        raise RuntimeError(f"SNK EN asset row missing: {prepared.external_id}")
    asset_id = int(asset["id"])
    cur.execute(
        """
        INSERT INTO market_image_source_pointer
          (variant_id,image_kind,remote_url_sha256,source_path,
           source_version_sha256,public_allowed,observed_at)
        VALUES (%s,'raw_front',%s,%s,%s,1,%s)
        ON DUPLICATE KEY UPDATE
          remote_url_sha256=VALUES(remote_url_sha256),
          source_path=VALUES(source_path),public_allowed=1,
          observed_at=GREATEST(observed_at,VALUES(observed_at))
        """,
        (
            prepared.variant_id,
            prepared.default_image_url_sha256,
            prepared.default_image_url,
            asset_source_version,
            prepared.source_observed_at,
        ),
    )
    cur.execute(
        """
        INSERT INTO market_image_qc
          (image_asset_id,semantic_match_status,card_number_match,language_match,
           tcg_match,raw_front_confirmed,public_allowed,rejection_reason,
           checked_at,qc_version)
        VALUES (%s,'accepted_freeze',1,1,1,1,1,NULL,%s,%s)
        ON DUPLICATE KEY UPDATE
          semantic_match_status='accepted_freeze',card_number_match=1,
          language_match=1,tcg_match=1,raw_front_confirmed=1,
          public_allowed=1,rejection_reason=NULL,checked_at=VALUES(checked_at)
        """,
        (asset_id, completed_at, prepared.image.qc_version),
    )
    lineage_payload = {
        "contract": "snk-en-storefront-lineage-v1",
        "variantId": prepared.variant_id,
        "sourceCode": SNK_EN_SOURCE_CODE,
        "storefront": "en",
        "exactItemId": prepared.external_id,
        "productUrl": prepared.product_url,
        "productPagePayloadSha256": prepared.product_page_payload_sha256,
        "productPageObservedAt": prepared.product_page_observed_at.isoformat(
            timespec="microseconds"
        ),
        "masterPayloadSha256": prepared.master_payload_sha256,
        "defaultImageUrl": prepared.default_image_url,
        "defaultImageUrlSha256": prepared.default_image_url_sha256,
        "downloadedBytesSha256": prepared.downloaded_bytes_sha256,
        "processedContentSha256": prepared.image.content_sha256,
        "transformSha256": prepared.image.transform_sha256,
        "identityEvidenceSha256": prepared.identity_evidence_sha256,
        "sourceObservedAt": prepared.source_observed_at.isoformat(timespec="microseconds"),
    }
    lineage_sha = _sha256(lineage_payload)
    cur.execute(
        """
        INSERT IGNORE INTO market_snk_en_storefront_lineage
          (variant_id,source_code,storefront_code,exact_item_id,product_url,
           master_payload_sha256,default_image_url,default_image_url_sha256,
           downloaded_bytes_sha256,processed_image_asset_id,
           processed_content_sha256,transform_sha256,transform_json,
           identity_evidence_sha256,lineage_sha256,source_observed_at)
        VALUES (%s,'snkrdunk','en',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        (
            prepared.variant_id,
            prepared.external_id,
            prepared.product_url,
            prepared.master_payload_sha256,
            prepared.default_image_url,
            prepared.default_image_url_sha256,
            prepared.downloaded_bytes_sha256,
            asset_id,
            prepared.image.content_sha256,
            prepared.image.transform_sha256,
            json.dumps(
                prepared.image.transform,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            prepared.identity_evidence_sha256,
            lineage_sha,
            prepared.source_observed_at,
        ),
    )
    lineage_inserted = int(cur.rowcount or 0) == 1
    cur.execute(
        "SELECT id FROM market_snk_en_storefront_lineage WHERE lineage_sha256=%s",
        (lineage_sha,),
    )
    lineage = cur.fetchone()
    if not lineage:
        raise RuntimeError(f"SNK EN lineage row missing: {prepared.external_id}")
    lineage_id = int(lineage["id"])
    authority_payload = {
        "contract": SNK_EN_PRODUCT_PAGE_CONTRACT,
        "variantId": prepared.variant_id,
        "exactItemId": prepared.external_id,
        "productUrl": prepared.product_url,
        "finalUrl": prepared.product_url,
        "httpStatus": 200,
        "productPagePayloadSha256": prepared.product_page_payload_sha256,
        "productPageObservedAt": prepared.product_page_observed_at.isoformat(
            timespec="microseconds"
        ),
        "defaultImageUrl": prepared.default_image_url,
        "masterPayloadSha256": prepared.master_payload_sha256,
        "identityEvidenceSha256": prepared.identity_evidence_sha256,
        "storefrontLineageSha256": lineage_sha,
    }
    authority_sha = _sha256(authority_payload)
    cur.execute(
        """
        INSERT IGNORE INTO market_snk_en_product_page_authority
          (storefront_lineage_id,variant_id,exact_item_id,product_url,final_url,
           http_status,product_page_payload_sha256,product_page_observed_at,
           default_image_url,master_payload_sha256,identity_evidence_sha256,
           authority_sha256)
        VALUES (%s,%s,%s,%s,%s,200,%s,%s,%s,%s,%s,%s)
        """,
        (
            lineage_id,
            prepared.variant_id,
            prepared.external_id,
            prepared.product_url,
            prepared.product_url,
            prepared.product_page_payload_sha256,
            prepared.product_page_observed_at,
            prepared.default_image_url,
            prepared.master_payload_sha256,
            prepared.identity_evidence_sha256,
            authority_sha,
        ),
    )
    cur.execute(
        """
        SELECT id FROM market_snk_en_product_page_authority
        WHERE storefront_lineage_id=%s AND authority_sha256=%s
        """,
        (lineage_id, authority_sha),
    )
    if cur.fetchone() is None:
        raise RuntimeError(
            f"SNK EN product-page authority row missing: {prepared.external_id}"
        )
    acceptance_evidence = _sha256(
        {
            "contract": "canonical-snk-en-image-acceptance-v1",
            "lineageSha256": lineage_sha,
            "imageContentSha256": prepared.image.content_sha256,
            "qcVersion": prepared.image.qc_version,
        }
    )
    cur.execute(
        """
        SELECT ca.id,ca.lineage_sha256
        FROM market_canonical_image_acceptance ca
        WHERE ca.variant_id=%s
          AND NOT EXISTS (SELECT 1 FROM market_canonical_image_acceptance newer
                          WHERE newer.supersedes_acceptance_id=ca.id)
        ORDER BY ca.accepted_at DESC,ca.id DESC LIMIT 1
        """,
        (prepared.variant_id,),
    )
    current = cur.fetchone()
    if current and str(current["lineage_sha256"]) == lineage_sha:
        acceptance_id = int(current["id"])
        acceptance_inserted = False
    else:
        cur.execute(
            """
            INSERT INTO market_canonical_image_acceptance
              (variant_id,storefront_lineage_id,image_asset_id,lineage_sha256,
               evidence_sha256,accepted_by,accepted_at,supersedes_acceptance_id)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                prepared.variant_id,
                lineage_id,
                asset_id,
                lineage_sha,
                acceptance_evidence,
                "collect_control:snk_en_image",
                completed_at,
                int(current["id"]) if current else None,
            ),
        )
        acceptance_id = int(cur.lastrowid)
        acceptance_inserted = True
    _upsert_snk_en_freeze(
        cur,
        variant_id=prepared.variant_id,
        external_id=prepared.external_id,
        content_sha256=prepared.image.content_sha256,
        acceptance_id=acceptance_id,
        lineage_sha256=lineage_sha,
        evidence_sha256=acceptance_evidence,
        accepted_at=completed_at,
    )
    run_id = _checkpoint_snk_en_item(
        cur,
        item=item,
        mode=mode,
        started_at=started_at,
        completed_at=completed_at,
        lineage_sha256=lineage_sha,
        payload={
            **lineage_payload,
            "lineageSha256": lineage_sha,
            "productPageAuthoritySha256": authority_sha,
            "acceptanceId": acceptance_id,
            "acceptanceEvidenceSha256": acceptance_evidence,
        },
    )
    return {
        "inserted": int(lineage_inserted or acceptance_inserted),
        "runId": run_id,
        "lineageSha256": lineage_sha,
    }


def run_snk_en_image(
    items: list[dict[str, Any]],
    *,
    mode: str,
    limit: int | None,
    dry_run: bool,
    delay: float,
    workers: int,
) -> dict[str, Any]:
    try:
        selected, _ = _snk_selection(items, limit)
    except Exception as exc:  # noqa: BLE001
        return {
            "adapter": "snk_en_image",
            "mode": mode,
            "due": len(items),
            "processed": 0,
            "inserted": 0,
            "checkpoint": 0,
            "checkpointed": 0,
            "reused": 0,
            "downloaded": 0,
            "failed": len(items),
            "ok": False,
            "error": f"snk_en_selection:{type(exc).__name__}:{exc}",
        }
    report: dict[str, Any] = {
        "adapter": "snk_en_image",
        "mode": mode,
        "due": len(items),
        "processed": len(selected),
        "inserted": 0,
        "checkpoint": 0,
        "checkpointed": 0,
        "reused": 0,
        "reusedAccepted": 0,
        "reusedMaster": 0,
        "reusedBytes": 0,
        "downloaded": 0,
        "downloadedMaster": 0,
        "failed": 0,
        "failedItems": [],
        "ok": True,
        "targetScope": "current_active_universe_all_exact_snkrdunk",
        "storefront": "en",
        "browserUsed": False,
    }
    if not selected:
        report["freshness"] = {
            "slaHours": SLA_HOURS,
            "oldestSuccessAt": None,
            "newestSuccessAt": None,
            "slaOk": True,
        }
        return report
    if dry_run:
        report.update(
            {
                "dryRun": True,
                "note": "exact active SNK IDs selected; network, asset and DB writes not executed",
                "freshness": {
                    "slaHours": SLA_HOURS,
                    "oldestSuccessAt": None,
                    "newestSuccessAt": None,
                    "slaOk": False,
                },
            }
        )
        return report

    load_env()
    writer = db()
    success_times: list[datetime] = []
    completed_variants: set[int] = set()
    try:
        cur = writer.cursor()
        _require_migration_029(cur)
        # A due SNK EN image poll always performs the exact product-page GET.
        # Existing accepted lineage is used by classify_needs while fresh, not
        # as a substitute for a newly due storefront observation.
        reusable: dict[int, dict[str, Any]] = {}
        writer.commit()
        missing = [
            item for item in selected if int(item["variantId"]) not in reusable
        ]
        external_ids = {str(item["externalId"]) for item in missing}
        master_cache = _local_snk_master_cache(external_ids)
        bytes_cache = _local_snk_default_bytes(external_ids)
        http_workers = max(1, min(int(workers), len(missing) or 1, 12))
        report["httpWorkers"] = http_workers
        api_pool = SnkrdunkApiPool(
            workers=http_workers,
            delay=max(0.0, float(delay)),
            retries=1,
        )
        futures: dict[int, Future[SnkEnPrepared]] = {}
        executor = ThreadPoolExecutor(
            max_workers=http_workers,
            thread_name_prefix="snk-en-image",
        )
        try:
            for item in missing:
                futures[int(item["variantId"])] = executor.submit(
                    _prepare_snk_en_target,
                    item,
                    api_pool=api_pool,
                    master_cache=master_cache,
                    bytes_cache=bytes_cache,
                )
            for item in selected:
                variant_id = int(item["variantId"])
                started_at = _utc_naive()
                try:
                    if variant_id in reusable:
                        completed_at = _utc_naive()
                        report["reusedAccepted"] += 1
                        report["reused"] += 1
                        write = _persist_reused_snk_en(
                            cur,
                            item=item,
                            row=reusable[variant_id],
                            mode=mode,
                            started_at=started_at,
                            completed_at=completed_at,
                        )
                    else:
                        prepared = futures[variant_id].result()
                        completed_at = _utc_naive()
                        report["reusedMaster"] += int(prepared.master_reused)
                        report["reusedBytes"] += int(prepared.bytes_reused)
                        report["reused"] += int(
                            prepared.master_reused or prepared.bytes_reused
                        )
                        report["downloaded"] += int(prepared.downloaded)
                        report["downloadedMaster"] += int(
                            not prepared.master_reused
                        )
                        write = _persist_prepared_snk_en(
                            cur,
                            item=item,
                            prepared=prepared,
                            mode=mode,
                            started_at=started_at,
                            completed_at=completed_at,
                        )
                    writer.commit()
                    report["inserted"] += int(write["inserted"])
                    report["checkpointed"] += 1
                    success_times.append(completed_at)
                    completed_variants.add(variant_id)
                except Exception as exc:  # noqa: BLE001
                    writer.rollback()
                    report["failedItems"].append(
                        {
                            "variantId": variant_id,
                            "externalId": str(item.get("externalId") or ""),
                            "error": f"{type(exc).__name__}:{exc}",
                        }
                    )
        finally:
            executor.shutdown(wait=True, cancel_futures=False)
    except Exception as exc:  # noqa: BLE001
        writer.rollback()
        already_failed = {
            int(row["variantId"]) for row in report["failedItems"]
        }
        for item in selected:
            variant_id = int(item["variantId"])
            if variant_id in completed_variants or variant_id in already_failed:
                continue
            report["failedItems"].append(
                {
                    "variantId": variant_id,
                    "externalId": str(item.get("externalId") or ""),
                    "error": f"{type(exc).__name__}:{exc}",
                }
            )
    finally:
        writer.close()

    report["failed"] = len(report["failedItems"])
    report["checkpoint"] = report["checkpointed"]
    report["ok"] = (
        report["failed"] == 0
        and report["checkpointed"] == report["processed"]
    )
    report["freshness"] = {
        "slaHours": SLA_HOURS,
        "oldestSuccessAt": (
            min(success_times).isoformat(sep=" ") if success_times else None
        ),
        "newestSuccessAt": (
            max(success_times).isoformat(sep=" ") if success_times else None
        ),
        "slaOk": bool(report["ok"] and success_times),
    }
    if not report["ok"]:
        report["error"] = "one_or_more_exact_snk_en_targets_failed"
    return report


def _command_arg_path(command: list[Any], flag: str) -> Path:
    values = [str(value) for value in command]
    try:
        return Path(values[values.index(flag) + 1])
    except (ValueError, IndexError) as exc:
        raise RuntimeError(f"receipt command is missing {flag}") from exc


def cmd_commit_snk_price_receipt(*, receipt_path: Path) -> dict[str, Any]:
    """Commit checkpoints from the already-completed exact-ID SNK price receipt.

    This command never performs a network request or a second ingest. It exists so a
    controller-contract repair can resume from durable harvest/ingest evidence.
    """
    receipt = json.loads(receipt_path.read_text(encoding="utf-8-sig"))
    matches = [
        row
        for row in (receipt.get("results") or [])
        if row.get("adapter") == "snk_price"
    ]
    if len(matches) != 1:
        raise RuntimeError(f"expected one SNK price result in receipt, found {len(matches)}")
    row = matches[0]
    harvest_path = Path(str(row.get("harvestPath") or ""))
    ingest_path = _command_arg_path((row.get("ingest") or {}).get("cmd") or [], "--out")
    if (row.get("harvest") or {}).get("exit") != 0:
        raise RuntimeError("receipt harvest did not exit zero")
    if (row.get("ingest") or {}).get("exit") != 0:
        raise RuntimeError("receipt ingest did not exit zero")
    if not harvest_path.is_file() or not ingest_path.is_file():
        raise RuntimeError("receipt harvest or ingest artifact is missing")

    requested_ids = [
        int(line.strip())
        for line in Path(str(row.get("idsPath") or "")).read_text(encoding="ascii").splitlines()
        if line.strip()
    ]
    if len(requested_ids) != len(set(requested_ids)):
        raise RuntimeError("receipt requested IDs are duplicated")
    raw_rows, payload_sha = _validate_snk_harvest(harvest_path, requested_ids)
    ingest_summary = json.loads(ingest_path.read_text(encoding="utf-8-sig"))
    ingest_contract = _validate_snk_price_ingest(ingest_summary, requested_ids)

    registry = _jsonl_rows(REGISTRY_PATH)
    by_external = {
        str(item.get("externalId") or ""): item
        for item in registry
        if item.get("adapter") == "snk_price"
    }
    requested_text = {str(value) for value in requested_ids}
    if set(by_external) != requested_text:
        missing = sorted(requested_text - set(by_external))
        extra = sorted(set(by_external) - requested_text)
        raise RuntimeError(
            f"current active SNK registry differs from receipt missing={missing[:10]} extra={extra[:10]}"
        )
    items = [by_external[str(value)] for value in requested_ids]
    started_at = _parse_datetime((row.get("harvest") or {}).get("startedAt"))
    completed_at = _parse_datetime((row.get("ingest") or {}).get("finishedAt"))
    if started_at is None or completed_at is None or completed_at < started_at:
        raise RuntimeError("receipt timestamps are missing or invalid")
    checkpoint = record_successful_poll(
        adapter="snk_price",
        mode=str(row.get("mode") or "incr"),
        items=items,
        payload={
            "resumeContract": "snk_price_empty_kline_checkpoint_v1",
            "receiptSha256": hashlib.sha256(receipt_path.read_bytes()).hexdigest(),
            "harvestSha256": hashlib.sha256(harvest_path.read_bytes()).hexdigest(),
            "ingestSha256": hashlib.sha256(ingest_path.read_bytes()).hexdigest(),
            "harvestRows": len(raw_rows),
            **ingest_contract,
        },
        started_at=started_at,
        completed_at=completed_at,
        payload_sha_by_external=payload_sha,
    )
    result = {
        "action": "commit-snk-price-receipt",
        "ok": True,
        "receipt": str(receipt_path),
        "harvest": str(harvest_path),
        "ingest": str(ingest_path),
        **ingest_contract,
        **checkpoint,
        "lastEffectiveAt": completed_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "networkRequests": 0,
        "ingestRuns": 0,
    }
    output = OUT_DIR / "snk_price_checkpoint_resume.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    result["output"] = str(output)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def cmd_commit_snk_binding_delta(
    *,
    harvest_path: Path,
    variant_ids: list[int],
) -> dict[str, Any]:
    """Ingest and checkpoint exact new bindings from one already-completed SNK harvest.

    No network request is made here.  The broad source harvest may contain other IDs;
    only the explicitly named active variants are admitted into the delta receipt.
    """
    wanted_variants = list(dict.fromkeys(int(value) for value in variant_ids))
    if not wanted_variants or any(value <= 0 for value in wanted_variants):
        raise RuntimeError("at least one positive --variant-id is required")
    if not harvest_path.is_file():
        raise RuntimeError(f"completed SNK harvest is missing: {harvest_path}")

    registry = _jsonl_rows(REGISTRY_PATH)
    items_by_adapter: dict[str, dict[int, dict[str, Any]]] = {}
    for adapter in ("snk_trades", "snk_price"):
        items_by_adapter[adapter] = {
            int(row["variantId"]): row
            for row in registry
            if row.get("adapter") == adapter
            and int(row.get("variantId") or 0) in wanted_variants
            and str(row.get("externalId") or "").isdigit()
        }
        missing = sorted(set(wanted_variants) - set(items_by_adapter[adapter]))
        if missing:
            raise RuntimeError(f"active registry missing exact {adapter} variants: {missing}")

    external_by_variant = {
        variant_id: str(items_by_adapter["snk_trades"][variant_id]["externalId"])
        for variant_id in wanted_variants
    }
    for variant_id in wanted_variants:
        if str(items_by_adapter["snk_price"][variant_id]["externalId"]) != external_by_variant[variant_id]:
            raise RuntimeError(f"SNK adapter identity mismatch for variant {variant_id}")
    wanted_external = set(external_by_variant.values())

    broad_rows = [
        json.loads(line)
        for line in harvest_path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]
    selected_rows = [
        row for row in broad_rows if str(row.get("item_id") or "") in wanted_external
    ]
    selected_ids = [int(external_by_variant[variant_id]) for variant_id in wanted_variants]
    if len(selected_rows) != len(wanted_external):
        found = sorted(str(row.get("item_id") or "") for row in selected_rows)
        raise RuntimeError(
            f"completed SNK harvest does not contain each exact delta ID wanted={sorted(wanted_external)} found={found}"
        )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    subset_path = OUT_DIR / "binding_delta_snk_harvest.jsonl"
    subset_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in selected_rows),
        encoding="utf-8",
    )
    validated_rows, payload_sha = _validate_snk_harvest(subset_path, selected_ids)
    timestamps = [
        value
        for value in (_parse_datetime(row.get("fetched_at")) for row in selected_rows)
        if value is not None
    ]
    if len(timestamps) != len(selected_rows):
        raise RuntimeError("SNK delta harvest has missing fetched_at timestamps")
    started_at = min(timestamps)
    completed_at = max(timestamps)

    results: list[dict[str, Any]] = []
    for adapter in ("snk_trades", "snk_price"):
        items = [items_by_adapter[adapter][variant_id] for variant_id in wanted_variants]
        result: dict[str, Any] = {
            "adapter": adapter,
            "mode": "incr",
            "due": len(items),
            "processed": len(items),
            "checkpointed": 0,
            "ok": False,
        }
        if adapter == "snk_trades":
            ingest_report_path = None
            ingest_cmd = [
                PY,
                "-X",
                "utf8",
                "pipelines/ingest_snk_trades_sales.py",
                "--harvest",
                str(subset_path),
            ]
        else:
            ingest_report_path = OUT_DIR / "binding_delta_snk_price_ingest.json"
            ingest_cmd = [
                PY,
                "-X",
                "utf8",
                "pipelines/snk_market_data.py",
                "--ingest-jsonl",
                str(subset_path),
                "--condition",
                "trading_card_single_psa10",
                "--out",
                str(ingest_report_path),
            ]
        result["ingest"] = _run(ingest_cmd, timeout=180, dry_run=False)
        if result["ingest"].get("exit") != 0:
            result["error"] = f"{adapter}_delta_ingest_failed"
            results.append(result)
            break
        if adapter == "snk_price":
            ingest_summary = json.loads(ingest_report_path.read_text(encoding="utf-8-sig"))
            result.update(_validate_snk_price_ingest(ingest_summary, selected_ids))
        checkpoint = record_successful_poll(
            adapter=adapter,
            mode="incr",
            items=items,
            payload={
                "contract": "snk_post_binding_delta_v1",
                "sourceHarvestSha256": hashlib.sha256(harvest_path.read_bytes()).hexdigest(),
                "subsetHarvestSha256": hashlib.sha256(subset_path.read_bytes()).hexdigest(),
                "sourceHarvestRows": len(broad_rows),
                "rows": validated_rows,
                "ingest": result["ingest"],
            },
            started_at=started_at,
            completed_at=completed_at,
            payload_sha_by_external=payload_sha,
        )
        result.update(checkpoint)
        result["ok"] = int(result.get("checkpointed") or 0) == len(items)
        results.append(result)
        if not result["ok"]:
            break

    ok = len(results) == 2 and all(row.get("ok") is True for row in results)
    receipt = {
        "action": "commit-snk-binding-delta",
        "asOf": utc_now(),
        "ok": ok,
        "requestedAdapters": ["snk_trades", "snk_price"],
        "variantIds": wanted_variants,
        "externalIds": selected_ids,
        "sourceHarvest": str(harvest_path),
        "sourceHarvestSha256": hashlib.sha256(harvest_path.read_bytes()).hexdigest(),
        "sourceHarvestRows": len(broad_rows),
        "subsetHarvest": str(subset_path),
        "networkRequests": 0,
        "sourceHarvestNetworkRequests": len(broad_rows),
        "results": results,
    }
    output = OUT_DIR / "binding_delta_snk_refresh.json"
    output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return receipt


def run_snk_trades(
    items: list[dict[str, Any]],
    *,
    mode: str,
    limit: int | None,
    dry_run: bool,
    delay: float,
    workers: int = 24,
    shared_harvest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _run_snk_adapter(
        items,
        adapter="snk_trades",
        mode=mode,
        limit=limit,
        dry_run=dry_run,
        delay=delay,
        workers=workers,
        shared_harvest=shared_harvest,
    )


def run_snk_price(
    items: list[dict[str, Any]],
    *,
    mode: str,
    limit: int | None,
    dry_run: bool,
    delay: float,
    workers: int = 24,
    shared_harvest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _run_snk_adapter(
        items,
        adapter="snk_price",
        mode=mode,
        limit=limit,
        dry_run=dry_run,
        delay=delay,
        workers=workers,
        shared_harvest=shared_harvest,
    )


def _pc_subset_map(
    items: list[dict[str, Any]], *, mode: str, label: str
) -> tuple[Path, list[dict[str, Any]]]:
    if not PC_MAP.is_file():
        raise RuntimeError(f"canonical PC map is missing: {PC_MAP}")
    requested = {int(item["variantId"]) for item in items}
    rows = [
        row
        for row in _jsonl_rows(PC_MAP)
        if int(row.get("variant_id") or 0) in requested
    ]
    rows_by_variant: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        rows_by_variant.setdefault(int(row.get("variant_id") or 0), []).append(row)
    duplicates = sorted(
        variant_id for variant_id, matches in rows_by_variant.items() if len(matches) != 1
    )
    if duplicates:
        raise RuntimeError(f"canonical PC map has duplicate active variants: {duplicates[:20]}")
    by_variant = {
        variant_id: matches[0] for variant_id, matches in rows_by_variant.items()
    }
    missing = sorted(requested - set(by_variant))
    if missing or len(by_variant) != len(requested):
        raise RuntimeError(f"canonical PC map missing active variants: {missing[:20]}")
    external_id_mismatches = []
    for item in items:
        variant_id = int(item["variantId"])
        requested_external_id = str(item.get("externalId") or "").strip()
        mapped_external_id = str(by_variant[variant_id].get("pc_product_id") or "").strip()
        if not requested_external_id or requested_external_id != mapped_external_id:
            external_id_mismatches.append(variant_id)
    if external_id_mismatches:
        raise RuntimeError(
            "canonical PC map external ID mismatch for active variants: "
            f"{external_id_mismatches[:20]}"
        )
    ordered = [by_variant[int(item["variantId"])] for item in items]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"pc_map_{label}_{mode}.jsonl"
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in ordered),
        encoding="utf-8",
    )
    return path, ordered


def partition_local_pc_stock_pages(
    items: list[dict[str, Any]], *, mode: str, dry_run: bool
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Use exact saved PC evidence as the primary missing-contract input.

    Only ``stock`` rows are eligible. Incremental rows still require a real
    source refresh. The saved page must match the canonical URL, product id and
    explicit PSA10 field before Chrome can be skipped.
    """

    selected = _unique_items(items, None)
    empty_report = {
        "adapter": "pc_local_stock_replay",
        "mode": mode,
        "processed": 0,
        "ok": True,
        "cursorAdvanced": False,
        "payloadShaByVariant": {},
        "rows": [],
        "networkReasons": {},
    }
    if dry_run or not selected:
        return [], selected, empty_report
    _, map_rows = _pc_subset_map(selected, mode=mode, label="local-stock")
    from pc_psa10_price_derivation import validate_pc_psa10

    map_by_variant = {int(row["variant_id"]): row for row in map_rows}
    replayed: list[dict[str, Any]] = []
    network: list[dict[str, Any]] = []
    payload_sha_by_variant: dict[str, str] = {}
    evidence_times: list[datetime] = []
    evidence_rows: list[dict[str, Any]] = []
    network_reasons: dict[str, str] = {}
    for item in selected:
        variant_id = int(item["variantId"])
        if str(item.get("modeNeeded") or "") != "stock":
            network.append(item)
            network_reasons[str(variant_id)] = "incremental_refresh_due"
            continue
        row = map_by_variant[variant_id]
        exact_price, reason = validate_pc_psa10(row)
        if exact_price is None:
            network.append(item)
            network_reasons[str(variant_id)] = reason
            continue
        html_path = ROOT / str(row.get("html_path") or row.get("htmlPath") or "")
        modified_at = datetime.fromtimestamp(html_path.stat().st_mtime, timezone.utc)
        if (_age_hours(modified_at) or 0) > SLA_HOURS:
            network.append(item)
            network_reasons[str(variant_id)] = "local_exact_html_exceeds_36h_sla"
            continue
        html_bytes = html_path.read_bytes()
        replayed.append(item)
        payload_sha_by_variant[str(variant_id)] = hashlib.sha256(html_bytes).hexdigest()
        evidence_times.append(modified_at)
        evidence_rows.append(
            {
                "variantId": variant_id,
                "externalId": str(row.get("pc_product_id") or ""),
                "sourceUrl": str(row.get("pc_url") or ""),
                "htmlPath": str(html_path),
                "htmlSha256": payload_sha_by_variant[str(variant_id)],
                "explicitField": exact_price.get("field"),
                "sourceObservedDate": exact_price.get("observed_date"),
                "localArtifactModifiedAt": modified_at.isoformat().replace(
                    "+00:00", "Z"
                ),
                "reason": reason,
            }
        )
    report = {
        "adapter": "pc_local_stock_replay",
        "mode": mode,
        "processed": len(replayed),
        "ok": True,
        "cursorAdvanced": False,
        "payloadShaByVariant": payload_sha_by_variant,
        "evidenceAsOf": (
            max(evidence_times).isoformat().replace("+00:00", "Z")
            if evidence_times
            else None
        ),
        "evidenceFreshnessFloor": (
            min(evidence_times).isoformat().replace("+00:00", "Z")
            if evidence_times
            else None
        ),
        "rows": evidence_rows,
        "networkReasons": network_reasons,
    }
    return replayed, network, report


def refresh_pc_pages(
    items: list[dict[str, Any]],
    *,
    mode: str,
    dry_run: bool,
    resume_report: Path | None,
    sleep_seconds: float,
    cdp_already_ensured: bool,
) -> dict[str, Any]:
    selected = _unique_items(items, None)
    report: dict[str, Any] = {
        "adapter": "pc_cdp_fresh_pages",
        "mode": mode,
        "processed": len(selected),
        "ok": True,
        "payloadShaByVariant": {},
    }
    if not selected:
        report["note"] = "no exact PC variants due"
        return report
    try:
        _, map_rows = _pc_subset_map(selected, mode=mode, label="refresh")
    except Exception as exc:  # noqa: BLE001
        report.update({"ok": False, "error": f"pc_map:{type(exc).__name__}:{exc}"})
        return report
    ids_path = OUT_DIR / f"pc_variant_ids_{mode}.txt"
    ids_path.write_text(
        "\n".join(str(item["variantId"]) for item in selected) + "\n",
        encoding="ascii",
    )
    report["variantIdsPath"] = str(ids_path)
    if dry_run:
        report.update({"dryRun": True, "note": "exact PC variants selected; CDP was not executed"})
        return report
    if not WINDOWS_PY.is_file():
        report.update({"ok": False, "error": f"Windows backend Python missing: {WINDOWS_PY}"})
        return report

    started_at = datetime.now(timezone.utc)
    try:
        cmd = [
            str(WINDOWS_PY),
            "-X",
            "utf8",
            _windows_path(ROOT / "pipelines/pc_cdp_sold_refresh_win.py"),
            "--variant-ids-file",
            _windows_path(ids_path),
            "--limit",
            "0",
            "--no-ingest",
            "--sleep",
            str(max(0.0, float(sleep_seconds))),
            "--cdp-port",
            str(CARDZ_CDP_PORT),
        ]
        if cdp_already_ensured:
            cmd.append("--cdp-already-ensured")
        if resume_report is not None:
            cmd.extend(["--resume-report", _windows_path(resume_report)])
    except Exception as exc:  # noqa: BLE001
        report.update({"ok": False, "error": f"windows_path:{type(exc).__name__}:{exc}"})
        return report
    report["run"] = _run(cmd, timeout=max(600, 45 * len(selected)), dry_run=False)
    if report["run"].get("exit") != 0 or not PC_REFRESH_REPORT.is_file():
        report.update({"ok": False, "error": "pc_cdp_refresh_failed"})
        return report
    try:
        source_report = json.loads(PC_REFRESH_REPORT.read_text(encoding="utf-8-sig"))
        report_time = _parse_datetime(source_report.get("asOf"))
        if report_time is None or report_time < started_at:
            raise RuntimeError("PC refresh report is stale")
        expected = len(selected)
        if not (
            int(source_report.get("batch") or 0) == expected
            and int(source_report.get("ok") or 0) == expected
            and int(source_report.get("fail") or 0) == 0
            and int(source_report.get("cf") or 0) == 0
            and not source_report.get("missingRequestedVariantIds")
        ):
            raise RuntimeError("PC refresh report is incomplete")
        payload_sha_by_variant: dict[str, str] = {}
        for row in map_rows:
            html_path = ROOT / str(row.get("html_path") or row.get("htmlPath") or "")
            if not html_path.is_file():
                raise RuntimeError(f"refreshed PC HTML missing for variant {row.get('variant_id')}")
            payload_sha_by_variant[str(int(row["variant_id"]))] = hashlib.sha256(
                html_path.read_bytes()
            ).hexdigest()
    except Exception as exc:  # noqa: BLE001
        report.update({"ok": False, "error": f"pc_refresh_contract:{type(exc).__name__}:{exc}"})
        return report
    report.update(
        {
            "sourceReport": source_report,
            "payloadShaByVariant": payload_sha_by_variant,
        }
    )
    return report


def _pc_checkpoint_hashes(
    items: list[dict[str, Any]], refresh: dict[str, Any]
) -> dict[str, str]:
    by_variant = refresh.get("payloadShaByVariant") or {}
    return {
        str(item.get("externalId") or ""): str(by_variant[str(int(item["variantId"]))])
        for item in items
    }


def run_pc_ebay_sales(
    items: list[dict[str, Any]],
    *,
    mode: str,
    dry_run: bool,
    refresh: dict[str, Any],
) -> dict[str, Any]:
    selected = _unique_items(items, None)
    report: dict[str, Any] = {
        "adapter": "pc_ebay_sales",
        "mode": mode,
        "due": len(items),
        "processed": len(selected),
        "checkpointed": 0,
        "ok": True,
    }
    if not selected:
        report["note"] = "no exact PC/eBay sales variants due"
        return report
    if not refresh.get("ok"):
        report.update({"ok": False, "error": "fresh_pc_pages_unavailable"})
        return report
    try:
        map_path, _ = _pc_subset_map(selected, mode=mode, label="sales")
    except Exception as exc:  # noqa: BLE001
        report.update({"ok": False, "error": f"pc_map:{type(exc).__name__}:{exc}"})
        return report
    if dry_run:
        report.update({"dryRun": True, "map": str(map_path)})
        return report

    started_at = datetime.now(timezone.utc).replace(tzinfo=None)
    ingest_report_path = OUT_DIR / f"pc_ebay_sales_ingest_{mode}.json"
    command = [
        PY,
        "-X",
        "utf8",
        "pipelines/c11_pc_sold_ingest.py",
        "--map",
        str(map_path),
        "--report",
        str(ingest_report_path),
        "--write",
    ]
    report["run"] = _run(command, timeout=max(1200, 10 * len(selected)), dry_run=False)
    if report["run"].get("exit") != 0 or not ingest_report_path.is_file():
        report.update({"ok": False, "error": "pc_ebay_sales_ingest_failed"})
        return report
    try:
        ingest = json.loads(ingest_report_path.read_text(encoding="utf-8-sig"))
        stats = ingest.get("stats") or {}
        if not (
            int(ingest.get("mapReadyHigh") or 0) == len(selected)
            and int(ingest.get("mapExistingVariant") or 0) == len(selected)
            and int(ingest.get("mapExactProductGateRejected") or 0) == 0
            and not ingest.get("missingVariantIds")
            and int(stats.get("cards_no_html") or 0) == 0
            and int(stats.get("cards_parse_fail") or 0) == 0
        ):
            raise RuntimeError("PC/eBay ingest report is incomplete")
        local_replay_ids = {
            int(value) for value in (refresh.get("localReplayVariantIds") or [])
        }
        replay_floor = _parse_datetime(
            (refresh.get("localStockReplay") or {}).get("evidenceFreshnessFloor")
        )
        checkpoint = record_successful_poll(
            adapter="pc_ebay_sales",
            mode=mode,
            items=selected,
            payload=ingest,
            started_at=started_at,
            payload_sha_by_external=_pc_checkpoint_hashes(selected, refresh),
            # A stock replay is valid source evidence, but its checkpoint must
            # retain the artifact's real age instead of pretending it was
            # fetched at the time of this control-plane run.
            completed_at=replay_floor if local_replay_ids else None,
        )
        checkpoint["cursorAdvanced"] = True
        checkpoint["reused"] = len(local_replay_ids)
    except Exception as exc:  # noqa: BLE001
        report.update({"ok": False, "error": f"pc_ebay_contract:{type(exc).__name__}:{exc}"})
        return report
    report.update({"map": str(map_path), "ingestReport": str(ingest_report_path), **checkpoint})
    return report


def run_en_price_ref(
    items: list[dict[str, Any]],
    *,
    mode: str,
    dry_run: bool,
    refresh: dict[str, Any],
) -> dict[str, Any]:
    selected = _unique_items(items, None)
    report: dict[str, Any] = {
        "adapter": "en_price_ref",
        "mode": mode,
        "due": len(items),
        "processed": len(selected),
        "checkpointed": 0,
        "ok": True,
    }
    if not selected:
        report["note"] = "no exact EN price-reference variants due"
        return report
    if not refresh.get("ok"):
        report.update({"ok": False, "error": "fresh_pc_pages_unavailable"})
        return report
    try:
        map_path, _ = _pc_subset_map(selected, mode=mode, label="price")
    except Exception as exc:  # noqa: BLE001
        report.update({"ok": False, "error": f"pc_map:{type(exc).__name__}:{exc}"})
        return report
    if dry_run:
        report.update({"dryRun": True, "map": str(map_path)})
        return report

    started_at = datetime.now(timezone.utc).replace(tzinfo=None)
    plan_path = OUT_DIR / f"pc_price_plan_{mode}.json"
    derive_cmd = [
        PY,
        "-X",
        "utf8",
        "pipelines/pc_psa10_price_derivation.py",
        "--map",
        str(map_path),
        "--out",
        str(plan_path),
        "--sources",
        "pricecharting",
    ]
    report["derive"] = _run(derive_cmd, timeout=max(1200, 10 * len(selected)), dry_run=False)
    if report["derive"].get("exit") != 0 or not plan_path.is_file():
        report.update({"ok": False, "error": "en_price_plan_failed"})
        return report
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8-sig"))
        planned_variants = {int(row["variantId"]) for row in (plan.get("rows") or [])}
        expected_variants = {int(item["variantId"]) for item in selected}
        if not (
            plan.get("contract") == "pc_psa10_current_price_v1"
            and int(plan.get("exactBindings") or 0) == len(selected)
            and int(plan.get("rejectedBindings") or 0) == 0
            and planned_variants == expected_variants
            and isinstance(plan.get("planSha256"), str)
            and len(plan["planSha256"]) == 64
        ):
            raise RuntimeError("EN price plan is incomplete for active variants")
    except Exception as exc:  # noqa: BLE001
        report.update({"ok": False, "error": f"en_price_plan_contract:{type(exc).__name__}:{exc}"})
        return report
    materialize_cmd = [
        PY,
        "-X",
        "utf8",
        "pipelines/pc_psa10_price_materialize.py",
        "--plan",
        str(plan_path),
        "--plan-sha256",
        str(plan["planSha256"]),
        "--map",
        str(map_path),
        "--write",
    ]
    report["materialize"] = _run(
        materialize_cmd,
        timeout=max(1200, 10 * len(selected)),
        dry_run=False,
    )
    if report["materialize"].get("exit") != 0:
        report.update({"ok": False, "error": "en_price_materialize_failed"})
        return report
    try:
        local_replay_ids = {
            int(value) for value in (refresh.get("localReplayVariantIds") or [])
        }
        replay_floor = _parse_datetime(
            (refresh.get("localStockReplay") or {}).get("evidenceFreshnessFloor")
        )
        checkpoint = record_successful_poll(
            adapter="en_price_ref",
            mode=mode,
            items=selected,
            payload=plan,
            started_at=started_at,
            payload_sha_by_external=_pc_checkpoint_hashes(selected, refresh),
            completed_at=replay_floor if local_replay_ids else None,
        )
        checkpoint["cursorAdvanced"] = True
        checkpoint["reused"] = len(local_replay_ids)
    except Exception as exc:  # noqa: BLE001
        report.update({"ok": False, "error": f"checkpoint:{type(exc).__name__}:{exc}"})
        return report
    report.update({"map": str(map_path), "plan": str(plan_path), **checkpoint})
    return report


def _requested_adapters(adapters: list[str]) -> list[str]:
    allowed = list(CHECKPOINT_ADAPTERS)
    wanted = set(adapters)
    unknown = wanted - set(allowed) - {"all"}
    if unknown:
        raise ValueError(f"unknown adapters: {sorted(unknown)}")
    return allowed if "all" in wanted else [adapter for adapter in allowed if adapter in wanted]


def _acquire_adapter_leases(adapters: list[str]):
    """Hold one MySQL advisory lease per adapter for the complete collection run."""
    load_env()
    conn = db()
    cur = conn.cursor()
    acquired: list[str] = []
    try:
        lease_roles = set(adapters)
        if lease_roles.intersection({"pc_ebay_sales", "en_price_ref"}):
            lease_roles.add("pc_cdp")
        for adapter in sorted(lease_roles):
            lock_name = f"cardz:collect:{adapter}"[:64]
            cur.execute("SELECT GET_LOCK(%s, 0) AS acquired", (lock_name,))
            row = cur.fetchone()
            value = row.get("acquired") if isinstance(row, dict) else row[0]
            if int(value or 0) != 1:
                raise RuntimeError(
                    f"adapter lease already held: {adapter}; stop the duplicate collector/Chrome runner"
                )
            acquired.append(lock_name)
        return conn, acquired
    except Exception:
        for lock_name in reversed(acquired):
            cur.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))
        conn.close()
        raise


def _release_adapter_leases(conn, lock_names: list[str]) -> None:
    try:
        cur = conn.cursor()
        for lock_name in reversed(lock_names):
            cur.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))
    finally:
        conn.close()


def _collect_mode_impl(
    *,
    mode: str,
    adapters: list[str],
    limit: int | None,
    dry_run: bool,
    delay: float,
    workers: int,
    ensure_browser: bool,
    pc_resume_report: Path | None,
    pc_sleep: float,
    variant_ids: list[int] | None = None,
) -> dict[str, Any]:
    status = cmd_status(rebuild_registry=True)
    reg = _jsonl_rows(REGISTRY_PATH)
    requested = _requested_adapters(adapters)
    modes = {"stock"} if mode == "stock" else {"incr", "stock"}
    explicit_variants = set(int(value) for value in (variant_ids or []))
    due_by_adapter = {
        adapter: sorted(
            (
                [
                    row
                    for row in reg
                    if row.get("adapter") == adapter
                    and int(row.get("variantId") or 0) in explicit_variants
                    and str(row.get("externalId") or "").strip()
                ]
                if explicit_variants
                else _due(reg, adapter, modes)
            ),
            key=lambda row: 0 if row["modeNeeded"] == "incr" else 1,
        )
        for adapter in requested
    }
    if explicit_variants:
        for adapter, rows in due_by_adapter.items():
            found = {int(row["variantId"]) for row in rows}
            missing = sorted(explicit_variants - found)
            if missing:
                raise RuntimeError(f"explicit {adapter} variants are not exact-bound: {missing}")
    selected_by_adapter = {
        adapter: _unique_items(rows, limit)
        for adapter, rows in due_by_adapter.items()
    }
    results: list[dict[str, Any]] = []

    if "gemrate_pop" in requested:
        results.append(
            run_gemrate_pop(
                due_by_adapter["gemrate_pop"],
                mode=mode,
                limit=limit,
                dry_run=dry_run,
            )
        )
    # One SNKRDUNK harvest per collect run: when both SNK ingest consumers are
    # requested, the union of their due exact IDs is fetched once and each lane
    # ingests from the same immutable rows (previously each lane refetched).
    snk_shared_harvest: dict[str, Any] | None = None
    if not dry_run and "snk_trades" in requested and "snk_price" in requested:
        union_ids: list[int] = []
        seen_ids: set[int] = set()
        for adapter in ("snk_trades", "snk_price"):
            lane_active, _ = _partition_quarantined(adapter, due_by_adapter[adapter])
            try:
                _, lane_ids = _snk_selection(lane_active, limit)
            except Exception:  # noqa: BLE001
                # Selection errors belong to the lane report; fall back to
                # per-lane harvesting so the lane fails with its own error.
                union_ids = []
                break
            for external_id in lane_ids:
                if external_id not in seen_ids:
                    seen_ids.add(external_id)
                    union_ids.append(external_id)
        if union_ids:
            snk_shared_harvest = _snk_harvest_once(
                union_ids,
                label="snk_shared",
                mode=mode,
                delay=delay,
                workers=workers,
            )
    if "snk_trades" in requested:
        results.append(
            run_snk_trades(
                due_by_adapter["snk_trades"],
                mode=mode,
                limit=limit,
                dry_run=dry_run,
                delay=delay,
                workers=workers,
                shared_harvest=snk_shared_harvest,
            )
        )
    if "snk_price" in requested:
        results.append(
            run_snk_price(
                due_by_adapter["snk_price"],
                mode=mode,
                limit=limit,
                dry_run=dry_run,
                delay=delay,
                workers=workers,
                shared_harvest=snk_shared_harvest,
            )
        )
    if "snk_en_image" in requested:
        results.append(
            run_snk_en_image(
                due_by_adapter["snk_en_image"],
                mode=mode,
                limit=limit,
                dry_run=dry_run,
                delay=delay,
                workers=workers,
            )
        )

    pc_items_by_variant: dict[int, dict[str, Any]] = {}
    for adapter in ("pc_ebay_sales", "en_price_ref"):
        for item in selected_by_adapter.get(adapter, []):
            variant_id = int(item["variantId"])
            current = pc_items_by_variant.get(variant_id)
            if current is None:
                pc_items_by_variant[variant_id] = dict(item)
            elif item.get("modeNeeded") == "incr":
                # One fresh page serves both PC consumers. If either consumer
                # is incremental, this variant must take the network lane.
                pc_items_by_variant[variant_id] = {
                    **current,
                    "modeNeeded": "incr",
                }
    browser_bootstrap: dict[str, Any] = {
        "requested": bool(ensure_browser),
        "needed": bool(pc_items_by_variant),
        "ok": True,
    }
    cdp_already_ensured = False
    all_pc_items = list(pc_items_by_variant.values())
    local_pc_items, network_pc_items, local_pc_report = partition_local_pc_stock_pages(
        all_pc_items, mode=mode, dry_run=dry_run
    )
    if local_pc_items and not network_pc_items:
        browser_bootstrap = {
            "requested": bool(ensure_browser),
            "needed": False,
            "ok": True,
            "reason": "all exact missing contracts replayed from local immutable HTML",
        }
    if network_pc_items:
        if ensure_browser and not dry_run:
            browser_bootstrap = {
                "requested": True,
                "needed": True,
                **ensure_cdp(CARDZ_CDP_PORT),
            }
            if not bool(browser_bootstrap.get("ok")):
                raise RuntimeError(
                    "the singleton CARDZ CDP session could not be started; no PC adapter ran"
                )
            cdp_already_ensured = True
        network_pc_refresh = refresh_pc_pages(
            network_pc_items,
            mode=mode,
            dry_run=dry_run,
            resume_report=pc_resume_report,
            sleep_seconds=pc_sleep,
            cdp_already_ensured=cdp_already_ensured,
        )
    else:
        network_pc_refresh = {
            "adapter": "pc_cdp_fresh_pages",
            "mode": mode,
            "processed": 0,
            "ok": True,
            "payloadShaByVariant": {},
            "note": "no exact PC variants require network refresh",
        }
    pc_refresh = {
        "adapter": "pc_page_acquisition",
        "mode": mode,
        "processed": len(all_pc_items),
        "ok": bool(local_pc_report.get("ok")) and bool(network_pc_refresh.get("ok")),
        "localReplay": bool(all_pc_items) and not network_pc_items,
        "localReplayVariantIds": sorted(
            int(item["variantId"]) for item in local_pc_items
        ),
        "networkVariantIds": sorted(
            int(item["variantId"]) for item in network_pc_items
        ),
        "payloadShaByVariant": {
            **(local_pc_report.get("payloadShaByVariant") or {}),
            **(network_pc_refresh.get("payloadShaByVariant") or {}),
        },
        "localStockReplay": local_pc_report,
        "networkRefresh": network_pc_refresh,
    }
    if "pc_ebay_sales" in requested:
        results.append(
            run_pc_ebay_sales(
                selected_by_adapter["pc_ebay_sales"],
                mode=mode,
                dry_run=dry_run,
                refresh=pc_refresh,
            )
        )
    if "en_price_ref" in requested:
        results.append(
            run_en_price_ref(
                selected_by_adapter["en_price_ref"],
                mode=mode,
                dry_run=dry_run,
                refresh=pc_refresh,
            )
        )

    by_adapter = {str(result.get("adapter")): result for result in results}
    failed = [
        adapter
        for adapter in requested
        if adapter not in by_adapter or not bool(by_adapter[adapter].get("ok"))
    ]
    truncated = [
        adapter
        for adapter in requested
        # Quarantined items are known-partial skips, not truncation.
        if int(by_adapter.get(adapter, {}).get("processed") or 0)
        + int(by_adapter.get(adapter, {}).get("quarantined") or 0)
        != len(due_by_adapter[adapter])
    ]
    ok = not failed and not truncated
    processed_count = sum(int(result.get("processed") or 0) for result in results)
    inserted_count = sum(int(result.get("inserted") or 0) for result in results)
    checkpointed_count = sum(
        int(result.get("checkpointed") or 0) for result in results
    )
    reused_count = sum(int(result.get("reused") or 0) for result in results)
    downloaded_count = sum(
        int(result.get("downloaded") or 0) for result in results
    )
    failed_count = sum(
        int(result.get("failed") or (0 if result.get("ok") else 1))
        for result in results
    )
    quarantined_count = sum(
        int(result.get("quarantined") or 0) for result in results
    )
    snk_shared_summary = None
    if snk_shared_harvest is not None:
        snk_shared_summary = {
            "ok": bool(snk_shared_harvest.get("ok")),
            "idsPath": snk_shared_harvest.get("idsPath"),
            "harvestPath": snk_shared_harvest.get("harvestPath"),
            "requestedIds": len(snk_shared_harvest.get("requestedIds") or []),
            "rows": len(snk_shared_harvest.get("rowsById") or {}),
            "failedRows": len(snk_shared_harvest.get("failedById") or {}),
            "error": snk_shared_harvest.get("error"),
        }
    report = {
        "action": mode,
        "asOf": utc_now(),
        "ok": ok,
        "dryRun": dry_run,
        "limit": limit,
        "requestedAdapters": requested,
        "failedAdapters": failed,
        "truncatedAdapters": truncated,
        "processed": processed_count,
        "inserted": inserted_count,
        "checkpoint": checkpointed_count,
        "checkpointed": checkpointed_count,
        "reused": reused_count,
        "downloaded": downloaded_count,
        "failed": failed_count,
        "quarantined": quarantined_count,
        "snkSharedHarvest": snk_shared_summary,
        "preStatusCounts": status.get("counts"),
        "pcRefresh": pc_refresh,
        "browserBootstrap": browser_bootstrap,
        "results": results,
        "browserBootstrapRequested": bool(ensure_browser),
        "leaseContract": COLLECT_LEASE_CONTRACT,
        "notes": [
            "All requested active exact-ID adapters are fail-closed.",
            "A successful source poll advances only that adapter and stream checkpoint.",
            "SNK EN image collection is exact-ID HTTP only and never opens Chrome.",
            "Missing PC contracts replay strict local HTML first; incremental PC/eBay and EN price reference share one fresh CDP page acquisition.",
            "SNK trades and price ingest one shared exact-ID harvest per run; the remote source is fetched once.",
            "Quarantined items (3+ consecutive failed runs) are skipped, reported per lane, and count as known-partial, never lane failure.",
        ],
    }
    load_env()
    conn = db()
    cur = conn.cursor()
    try:
        report["postFreshness"] = freshness_summary(cur, reg)
        report["freshness"] = report["postFreshness"]
    finally:
        conn.close()
    output = LAST_STOCK if mode == "stock" else LAST_INCR
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return report


def _collect_mode(
    *,
    mode: str,
    adapters: list[str],
    limit: int | None,
    dry_run: bool,
    delay: float,
    workers: int,
    ensure_browser: bool,
    pc_resume_report: Path | None,
    pc_sleep: float,
    variant_ids: list[int] | None = None,
) -> dict[str, Any]:
    requested = _requested_adapters(adapters)
    lease_conn, lock_names = _acquire_adapter_leases(requested)
    try:
        return _collect_mode_impl(
            mode=mode,
            adapters=adapters,
            limit=limit,
            dry_run=dry_run,
            delay=delay,
            workers=workers,
            ensure_browser=ensure_browser,
            pc_resume_report=pc_resume_report,
            pc_sleep=pc_sleep,
            variant_ids=variant_ids,
        )
    finally:
        _release_adapter_leases(lease_conn, lock_names)


def cmd_stock(
    *,
    adapters: list[str],
    limit: int | None,
    dry_run: bool,
    delay: float,
    workers: int,
    ensure_browser: bool,
    pc_resume_report: Path | None,
    pc_sleep: float,
    variant_ids: list[int] | None = None,
) -> dict[str, Any]:
    return _collect_mode(
        mode="stock",
        adapters=adapters,
        limit=limit,
        dry_run=dry_run,
        delay=delay,
        workers=workers,
        ensure_browser=ensure_browser,
        pc_resume_report=pc_resume_report,
        pc_sleep=pc_sleep,
        variant_ids=variant_ids,
    )


def cmd_incr(
    *,
    adapters: list[str],
    limit: int | None,
    dry_run: bool,
    delay: float,
    workers: int,
    ensure_browser: bool,
    pc_resume_report: Path | None,
    pc_sleep: float,
    variant_ids: list[int] | None = None,
) -> dict[str, Any]:
    return _collect_mode(
        mode="incr",
        adapters=adapters,
        limit=limit,
        dry_run=dry_run,
        delay=delay,
        workers=workers,
        ensure_browser=ensure_browser,
        pc_resume_report=pc_resume_report,
        pc_sleep=pc_sleep,
        variant_ids=variant_ids,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="CARDZ stock/incr collect control plane")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_status = sub.add_parser("status", help="freshness + due registry")
    p_status.add_argument("--no-rebuild", action="store_true")

    def add_common(p):
        p.add_argument("--adapter", action="append", default=[], help="all|gemrate_pop|snk_trades|snk_price|snk_en_image|pc_ebay_sales|en_price_ref (repeatable)")
        p.add_argument("--limit", type=int, default=None, help="max exact ids per network adapter")
        p.add_argument("--dry-run", action="store_true")
        p.add_argument("--delay", type=float, default=0.0, help="SNK per-worker delay; 0 = max concurrent")
        p.add_argument("--workers", type=int, default=24, help="SNK concurrent workers (API, not CDP)")
        p.add_argument("--ensure-browser", action="store_true", help="ensure the dedicated single CARDZ CDP session before PC path")
        p.add_argument("--pc-resume-report", type=Path, help="reuse successful exact-ID pages from one strict PC receipt")
        p.add_argument("--pc-sleep", type=float, default=4.0, help="seconds between serial PC page loads")
        p.add_argument("--variant-id", action="append", type=int, default=[], help="force exact active variant only (repeatable)")

    p_stock = sub.add_parser("stock", help="residual full pulls only")
    add_common(p_stock)
    p_incr = sub.add_parser("incr", help="daily deltas for due exact ids")
    add_common(p_incr)
    p_resume_snk = sub.add_parser(
        "commit-snk-price-receipt",
        help="checkpoint an already successful SNK exact-ID harvest/ingest; no network",
    )
    p_resume_snk.add_argument("--receipt", type=Path, default=LAST_INCR)
    p_commit_delta = sub.add_parser(
        "commit-snk-binding-delta",
        help="ingest exact variants from one already-completed SNK harvest; no network",
    )
    p_commit_delta.add_argument("--harvest", type=Path, required=True)
    p_commit_delta.add_argument("--variant-id", action="append", type=int, required=True)

    args = parser.parse_args()
    adapters = args.adapter if getattr(args, "adapter", None) else []
    if not adapters:
        adapters = ["all"]

    report = None
    if args.cmd == "status":
        cmd_status(rebuild_registry=not args.no_rebuild)
    elif args.cmd == "stock":
        report = cmd_stock(adapters=adapters, limit=args.limit, dry_run=args.dry_run, delay=args.delay, workers=args.workers, ensure_browser=args.ensure_browser, pc_resume_report=args.pc_resume_report, pc_sleep=args.pc_sleep, variant_ids=args.variant_id)
    elif args.cmd == "incr":
        report = cmd_incr(adapters=adapters, limit=args.limit, dry_run=args.dry_run, delay=args.delay, workers=args.workers, ensure_browser=args.ensure_browser, pc_resume_report=args.pc_resume_report, pc_sleep=args.pc_sleep, variant_ids=args.variant_id)
    elif args.cmd == "commit-snk-price-receipt":
        report = cmd_commit_snk_price_receipt(receipt_path=args.receipt)
    elif args.cmd == "commit-snk-binding-delta":
        report = cmd_commit_snk_binding_delta(
            harvest_path=args.harvest.resolve(),
            variant_ids=args.variant_id,
        )
    else:
        raise SystemExit(f"unknown command: {args.cmd}")
    return 0 if report is None or report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
