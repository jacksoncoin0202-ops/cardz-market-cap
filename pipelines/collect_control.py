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
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from qualified_pool_operator import db, load_env  # noqa: E402
from operator_control import current_universe  # noqa: E402

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
    "pc_ebay_sales",
    "en_price_ref",
)
PY = sys.executable
SLA_HOURS = 36


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
) -> str:
    if not has_stock:
        return "stock"
    if checkpoint is None:
        return "incr"
    last_success = checkpoint.get("last_effective_at")
    age = _age_hours(_parse_datetime(last_success))
    return "incr" if age is None or age > SLA_HOURS else "ok"


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


def ensure_cdp(port: int = 9222) -> dict[str, Any]:
    """Best-effort CDP ensure on Windows host script; non-fatal if unavailable."""
    ps1 = ROOT / "scripts" / "ensure_chrome_cdp.ps1"
    if not ps1.exists():
        return {"ok": False, "reason": "ensure_chrome_cdp.ps1 missing"}
    # From WSL, call powershell.exe if present
    pwsh = "powershell.exe"
    cmd = [pwsh, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1), "-Port", str(port)]
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
        SELECT variant_id, source_code, external_entity_id, match_status
        FROM catalog_source_identity
        WHERE variant_id IN ({ph})
          AND match_status IN ('exact','confirmed','attached')
        """,
        vids,
    )
    binds: dict[int, dict[str, str]] = {}
    for r in cur.fetchall():
        binds.setdefault(int(r["variant_id"]), {})[str(r["source_code"])] = str(r["external_entity_id"])

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
                "pricecharting": b.get("pricecharting"),
                "ebay": b.get("ebay"),
                "gemrate": b.get("gemrate"),
            },
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
            },
            "popMax": pop.get(vid).isoformat(sep=" ") if hasattr(pop.get(vid), "isoformat") else pop.get(vid),
            "_snkSaleMax": snk_sale_max,
            "_ebaySaleMax": ebay_sale_max,
            "_snkPriceMax": snk_price_max,
            "_enPriceMax": en_price_max,
            "_popMax": pop.get(vid),
        }
        rows.append(row)
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
        )
        needs.append({
            "adapter": "snk_trades",
            "modeNeeded": mode,
            "externalId": ids["snkrdunk"],
            "transport": "http_curl",
            "polarRole": "ja_sales_primary" if lang == "ja" else "cross_market_sales",
        })
        pmode = _poll_mode(
            has_stock=bool(row["prices"]["snkAny"]),
            observed_at=row.get("_snkPriceMax"),
            checkpoint=_checkpoint_for(checkpoints, "snk_price", vid, ids["snkrdunk"]),
        )
        needs.append({
            "adapter": "snk_price",
            "modeNeeded": pmode,
            "externalId": ids["snkrdunk"],
            "transport": "http_curl",
            "polarRole": "ja_price_primary" if lang == "ja" else "cross_market_price",
        })
    elif lang != "en":
        needs.append({"adapter": "bind_snk", "modeNeeded": "bind", "externalId": None, "transport": None, "polarRole": "ja_identity"})

    if lang == "en":
        pc_external = str(ids.get("pricecharting") or "").strip()
        pc_exact_product = pc_external if pc_external.isdigit() else None
        if not pc_exact_product and not ids.get("snkrdunk"):
            needs.append({"adapter": "bind_pc_or_ebay", "modeNeeded": "bind", "externalId": None, "transport": "cdp_9222", "polarRole": "en_identity"})
        elif pc_exact_product:
            external = pc_exact_product
            smode = _poll_mode(
                has_stock=bool(row["sales"]["ebayAny"]),
                observed_at=row.get("_ebaySaleMax"),
                checkpoint=_checkpoint_for(checkpoints, "pc_ebay_sales", vid, external),
            )
            needs.append({
                "adapter": "pc_ebay_sales",
                "modeNeeded": smode,
                "externalId": external,
                "transport": "cdp_9222",
                "polarRole": "en_sales_primary",
            })
            pmode = _poll_mode(
                has_stock=bool(row["prices"]["enAny"]),
                observed_at=row.get("_enPriceMax"),
                checkpoint=_checkpoint_for(checkpoints, "en_price_ref", vid, external),
            )
            needs.append({
                "adapter": "en_price_ref",
                "modeNeeded": pmode,
                "externalId": external,
                "transport": "cdp_9222",
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
    for name, sql in queries:
        cur.execute(sql)
        r = cur.fetchone()
        m = r["m"] if isinstance(r, dict) else r[0]
        n = int((r["n"] if isinstance(r, dict) else r[1]) or 0)
        age = _age_hours(m)
        out["streams"][name] = {
            "maxAt": m.isoformat(sep=" ") if hasattr(m, "isoformat") else m,
            "rows": n,
            "ageHours": None if age is None else round(age, 2),
            "slaOk": age is not None and age <= SLA_HOURS,
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
        fresh = freshness_summary(cur, reg)
        report = {
            "action": "status",
            "asOf": utc_now(),
            "counts": counts,
            "freshness": fresh,
            "registryPath": str(REGISTRY_PATH) if rebuild_registry else None,
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
                ON DUPLICATE KEY UPDATE
                    run_id=VALUES(run_id), external_entity_id=VALUES(external_entity_id),
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
        return {"runId": run_id, "inserted": len(resolved), "checkpointed": checkpointed}
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
    selected = _unique_items(items, limit)
    report: dict[str, Any] = {
        "adapter": "gemrate_pop",
        "mode": mode,
        "due": len(items),
        "processed": len(selected),
        "ok": True,
    }
    if not selected:
        report["note"] = "no exact GemRate IDs due"
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
    report["run"] = _run(command, timeout=max(5400, 10 * len(selected)), dry_run=False)
    if report["run"].get("exit") != 0:
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
    if not manifest.get("promoted") or not manifest.get("promotable"):
        report.update({"ok": False, "error": "gemrate_manifest_not_promotable", "manifest": str(manifest_path), "checkpointed": 0})
        return report
    try:
        write = _ingest_gemrate_manifest(manifest, selected)
    except Exception as exc:  # noqa: BLE001
        report.update({"ok": False, "error": f"gemrate_ingest:{type(exc).__name__}:{exc}", "checkpointed": 0})
        return report
    report.update({"manifest": str(manifest_path), **write})
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
) -> dict[str, Any]:
    try:
        selected, ids = _snk_selection(items, limit)
    except Exception as exc:  # noqa: BLE001
        return {
            "adapter": adapter,
            "mode": mode,
            "due": len(items),
            "processed": 0,
            "checkpointed": 0,
            "ok": False,
            "error": f"snk_selection:{type(exc).__name__}:{exc}",
        }
    report: dict[str, Any] = {
        "adapter": adapter,
        "mode": mode,
        "due": len(items),
        "processed": len(selected),
        "checkpointed": 0,
        "ok": True,
    }
    if not selected:
        report["note"] = "no exact SNK IDs due"
        return report
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    ids_path = OUT_DIR / f"{adapter}_ids_{mode}_{stamp}.txt"
    harvest_path = OUT_DIR / f"{adapter}_harvest_{mode}_{stamp}.jsonl"
    ids_path.write_text("\n".join(str(value) for value in ids) + "\n", encoding="ascii")
    report.update({"idsPath": str(ids_path), "harvestPath": str(harvest_path)})
    if dry_run:
        report.update({"dryRun": True, "note": "exact IDs selected; network and DB writes not executed"})
        return report

    started_at = datetime.now(timezone.utc).replace(tzinfo=None)
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
    report["harvest"] = _run(
        harvest_cmd,
        timeout=max(180, 8 * len(ids)),
        dry_run=False,
    )
    if report["harvest"].get("exit") != 0 or not harvest_path.is_file():
        report.update({"ok": False, "error": "snk_harvest_failed"})
        return report
    try:
        raw_rows, payload_sha = _validate_snk_harvest(harvest_path, ids)
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
        timeout=max(180, 5 * len(ids)),
        dry_run=False,
    )
    if report["ingest"].get("exit") != 0:
        report.update({"ok": False, "error": f"{adapter}_ingest_failed"})
        return report
    if adapter == "snk_price":
        try:
            ingest_summary = json.loads(ingest_report_path.read_text(encoding="utf-8-sig"))
            ingest_contract = _validate_snk_price_ingest(ingest_summary, ids)
        except Exception as exc:  # noqa: BLE001
            report.update({"ok": False, "error": f"snk_price_ingest_contract:{type(exc).__name__}:{exc}"})
            return report
        report.update(ingest_contract)
    try:
        checkpoint = record_successful_poll(
            adapter=adapter,
            mode=mode,
            items=selected,
            payload={"harvest": raw_rows, "ingest": report["ingest"]},
            started_at=started_at,
            payload_sha_by_external=payload_sha,
        )
    except Exception as exc:  # noqa: BLE001
        report.update({"ok": False, "error": f"checkpoint:{type(exc).__name__}:{exc}"})
        return report
    report.update(checkpoint)
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
) -> dict[str, Any]:
    return _run_snk_adapter(
        items,
        adapter="snk_trades",
        mode=mode,
        limit=limit,
        dry_run=dry_run,
        delay=delay,
        workers=workers,
    )


def run_snk_price(
    items: list[dict[str, Any]],
    *,
    mode: str,
    limit: int | None,
    dry_run: bool,
    delay: float,
    workers: int = 24,
) -> dict[str, Any]:
    return _run_snk_adapter(
        items,
        adapter="snk_price",
        mode=mode,
        limit=limit,
        dry_run=dry_run,
        delay=delay,
        workers=workers,
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
    by_variant = {int(row.get("variant_id") or 0): row for row in rows}
    missing = sorted(requested - set(by_variant))
    if missing or len(by_variant) != len(requested):
        raise RuntimeError(f"canonical PC map missing active variants: {missing[:20]}")
    ordered = [by_variant[int(item["variantId"])] for item in items]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"pc_map_{label}_{mode}.jsonl"
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in ordered),
        encoding="utf-8",
    )
    return path, ordered


def refresh_pc_pages(
    items: list[dict[str, Any]],
    *,
    mode: str,
    dry_run: bool,
    resume_report: Path | None,
    sleep_seconds: float,
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
        ]
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
        checkpoint = record_successful_poll(
            adapter="pc_ebay_sales",
            mode=mode,
            items=selected,
            payload=ingest,
            started_at=started_at,
            payload_sha_by_external=_pc_checkpoint_hashes(selected, refresh),
        )
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
        checkpoint = record_successful_poll(
            adapter="en_price_ref",
            mode=mode,
            items=selected,
            payload=plan,
            started_at=started_at,
            payload_sha_by_external=_pc_checkpoint_hashes(selected, refresh),
        )
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
    if "snk_trades" in requested:
        results.append(
            run_snk_trades(
                due_by_adapter["snk_trades"],
                mode=mode,
                limit=limit,
                dry_run=dry_run,
                delay=delay,
                workers=workers,
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
            )
        )

    pc_items_by_variant: dict[int, dict[str, Any]] = {}
    for adapter in ("pc_ebay_sales", "en_price_ref"):
        for item in selected_by_adapter.get(adapter, []):
            pc_items_by_variant.setdefault(int(item["variantId"]), item)
    pc_refresh = refresh_pc_pages(
        list(pc_items_by_variant.values()),
        mode=mode,
        dry_run=dry_run,
        resume_report=pc_resume_report,
        sleep_seconds=pc_sleep,
    )
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
        if int(by_adapter.get(adapter, {}).get("processed") or 0)
        != len(due_by_adapter[adapter])
    ]
    ok = not failed and not truncated
    report = {
        "action": mode,
        "asOf": utc_now(),
        "ok": ok,
        "dryRun": dry_run,
        "limit": limit,
        "requestedAdapters": requested,
        "failedAdapters": failed,
        "truncatedAdapters": truncated,
        "preStatusCounts": status.get("counts"),
        "pcRefresh": pc_refresh,
        "results": results,
        "browserBootstrapRequested": bool(ensure_browser),
        "notes": [
            "All requested active exact-ID adapters are fail-closed.",
            "A successful source poll advances only that adapter and stream checkpoint.",
            "PC/eBay and EN price reference share one fresh CDP page acquisition.",
        ],
    }
    load_env()
    conn = db()
    cur = conn.cursor()
    try:
        report["postFreshness"] = freshness_summary(cur, reg)
    finally:
        conn.close()
    output = LAST_STOCK if mode == "stock" else LAST_INCR
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return report


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
        p.add_argument("--adapter", action="append", default=[], help="all|gemrate_pop|snk_trades|snk_price|pc_ebay_sales|en_price_ref (repeatable)")
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
