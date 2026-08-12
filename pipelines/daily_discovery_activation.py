#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bridge newly discovered 036 catalog cards into the existing FE03 universe.

The daily acceptance command deliberately re-ranks one immutable universe; it
does not rewrite membership.  This coordinator closes the missing edge before
daily-accept:

1. compare the exact current no-binding variant IDs with the last acknowledged
   set in shared runtime state;
2. send only newly missing IDs to the transport-appropriate discovery lane;
3. leave ambiguous identities unacknowledged and fail closed;
4. when an exact binding lands, reuse the existing 036 E2E from
   identity-resolve through atomic activation.

No second universe writer lives here.  The state file is a cursor, not an
identity ruling: an ID enters ``knownGapIds`` only during explicit bootstrap or
after it was already in the previous cursor.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable, Mapping

import rebuild_036 as R
import discovery_ledger as DL


STATE_CONTRACT = "cardz-036-daily-discovery-state-v1"
STATE_PATH = R.ROOT / "data" / "runtime" / "rebuild-036" / "daily-discovery-state.json"
LANES = frozenset({"http", "browser"})
DAILY_PROVIDER_LIMIT = 40
SAME_EVIDENCE_QUARANTINE_AFTER = 3
DISCOVERY_REPORT_DIR = R.ROOT / "data" / "runtime" / "operator" / "collect"


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _normalise_ids(values: Iterable[Any], field: str) -> list[int]:
    ids = [int(value) for value in values]
    if any(value <= 0 for value in ids) or len(ids) != len(set(ids)):
        raise SystemExit(f"daily-discover ABORT: {field} must contain unique positive IDs")
    return sorted(ids)


def _lane_for_language(language: Any) -> str:
    return "browser" if str(language or "").strip().lower() == "en" else "http"


def evidence_sha256(document: Mapping[str, Any]) -> str:
    return hashlib.sha256(R.canonical_json(document)).hexdigest()


def attempt_lifecycle(
    previous_evidence: str | None,
    previous_consecutive: int,
    current_evidence: str,
    *,
    success: bool = False,
    terminal: bool = False,
) -> dict[str, Any]:
    """Pure scheduling rule for one real provider/activation attempt."""

    if len(current_evidence) != 64:
        raise ValueError("discovery evidence digest must be sha256")
    if success:
        return {
            "consecutive": 0,
            "nextDueHours": 168,
            "quarantineHours": None,
        }
    consecutive = (
        max(0, int(previous_consecutive)) + 1
        if previous_evidence == current_evidence
        else 1
    )
    if terminal:
        return {
            "consecutive": consecutive,
            "nextDueHours": None,
            "quarantineHours": 24 * 3650,
        }
    quarantined = consecutive >= SAME_EVIDENCE_QUARANTINE_AFTER
    return {
        "consecutive": consecutive,
        "nextDueHours": 168 if quarantined else 24,
        "quarantineHours": 168 if quarantined else None,
    }


def plan_gap_delta(
    known_gap_ids: Iterable[int], gap_rows: Iterable[Mapping[str, Any]], lane: str,
) -> dict[str, Any]:
    """Pure delta planner used by the live command and deterministic tests."""

    if lane not in LANES:
        raise ValueError(f"unsupported discovery lane: {lane}")
    known = set(_normalise_ids(known_gap_ids, "knownGapIds"))
    row_by_id: dict[int, Mapping[str, Any]] = {}
    for row in gap_rows:
        variant_id = int(row["variant_id"])
        if variant_id in row_by_id:
            raise SystemExit(
                f"daily-discover ABORT: duplicate discovery row for variant {variant_id}"
            )
        row_by_id[variant_id] = row
    current = set(row_by_id)
    new_ids = sorted(current - known)
    targets = [
        variant_id for variant_id in new_ids
        if _lane_for_language(row_by_id[variant_id].get("language")) == lane
    ]
    deferred = [variant_id for variant_id in new_ids if variant_id not in set(targets)]
    return {
        "newIds": new_ids,
        "targetIds": targets,
        "deferredIds": deferred,
        "resolvedKnownIds": sorted(known - current),
        "currentIds": sorted(current),
    }




def plan_ledger_retry_targets(
    gap_rows: Iterable[Mapping[str, Any]],
    ledger_rows: Iterable[Mapping[str, Any]],
    lane: str,
    *,
    limit: int = 40,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Rotate unresolved ledger cards into the daily discovery targets.

    Old known gaps and inactive unresolved cards must not sit outside the cursor
    forever. New gaps still win; this only fills residual capacity up to limit.
    Respects quarantine_until and next_due_at when present (044).
    """
    if lane not in LANES:
        raise ValueError(f"unsupported discovery lane: {lane}")
    if limit < 0:
        raise ValueError("limit must be non-negative")
    clock = now or datetime.now(timezone.utc).replace(tzinfo=None)
    gap_by_id = {int(row["variant_id"]): row for row in gap_rows}
    retry_statuses = {
        "source_not_found",
        "identity_ambiguous",
        "inactive_unresolved",
    }
    candidates: list[tuple[str, str, int, Mapping[str, Any]]] = []
    for raw in ledger_rows:
        variant_id = int(raw["variant_id"])
        status = str(raw.get("discovery_status") or "")
        if status not in retry_statuses:
            continue
        if str(raw.get("blocker_code") or "") == "multiple_exact_bindings":
            # More provider calls cannot safely choose between two exact owners.
            continue
        quarantine_until = raw.get("quarantine_until")
        if quarantine_until is not None and str(quarantine_until) > str(clock):
            continue
        next_due = raw.get("next_due_at")
        if next_due is not None and str(next_due) > str(clock):
            continue
        if variant_id in gap_by_id:
            row = gap_by_id[variant_id]
        else:
            language = str(raw.get("card_language") or raw.get("language") or "")
            row = {
                "variant_id": variant_id,
                "language": language,
                "tcg": str(raw.get("tcg") or "pokemon"),
            }
        if _lane_for_language(row.get("language")) != lane:
            continue
        due_key = str(next_due or raw.get("last_reviewed_at") or "")
        reviewed = str(raw.get("last_reviewed_at") or "")
        candidates.append((due_key, reviewed, variant_id, row))
    candidates.sort(key=lambda item: (item[0], item[1], item[2]))
    selected = candidates[:limit]
    target_ids = [variant_id for _, _, variant_id, _ in selected]
    return {
        "targetIds": target_ids,
        "targetRows": [row for _, _, _, row in selected],
        "candidateCount": len(candidates),
        "limit": limit,
    }


def _load_ledger_rows(credentials_env: Path | None) -> list[dict[str, Any]]:
    conn = R.connect(credentials_env or R.DAILY_CREDENTIALS_ENV)
    try:
        with conn.cursor() as cur:
            DL.rebuild_ledger(cur)
            cur.execute(
                """
                SELECT l.variant_id, l.catalog_status, l.discovery_status,
                       l.detail_json, l.last_reviewed_at,
                       l.attempt_count, l.last_attempt_at, l.last_outcome,
                       l.next_due_at, l.quarantine_until, l.blocker_code,
                       l.last_evidence_sha256, l.consecutive_same_evidence_count,
                       pi.card_language AS language, pi.tcg_code AS tcg
                FROM market_identity_discovery_ledger l
                LEFT JOIN catalog_printing_identity pi ON pi.variant_id=l.variant_id
                INNER JOIN catalog_rebuild_member rm
                  ON rm.variant_id=l.variant_id
                 AND rm.generation_id=(
                   SELECT generation_id FROM catalog_rebuild_member
                   ORDER BY computed_at DESC LIMIT 1)
                WHERE rm.cohort<>'non_qualified'
                  AND rm.latest_psa10_population>=%s
                ORDER BY COALESCE(l.next_due_at, l.last_reviewed_at) ASC, l.variant_id ASC
                """,
                (R.DISCOVERY_GAP_POP,),
            )
            rows = [dict(row) for row in cur.fetchall()]
        conn.commit()
        return rows
    finally:
        conn.close()

def _runtime_snapshot(credentials_env: Path | None) -> dict[str, Any]:
    conn = R.connect(credentials_env or R.DAILY_CREDENTIALS_ENV)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT generation_id FROM catalog_rebuild_member"
                " ORDER BY computed_at DESC LIMIT 1"
            )
            latest = cur.fetchone()
            if not latest:
                raise SystemExit("daily-discover ABORT: no rebuild generation exists")
            generation = str(latest["generation_id"])
            cur.execute(
                "SELECT generation_id FROM cardz_rebuild_generation"
                " WHERE activated_at IS NOT NULL ORDER BY activated_at DESC LIMIT 1"
            )
            active = cur.fetchone()
            if not active:
                raise SystemExit("daily-discover ABORT: no activated generation exists")
            active_generation = str(active["generation_id"])
            if generation != active_generation:
                raise SystemExit(
                    "daily-discover ABORT: latest catalog generation"
                    f" {generation} is not the activated generation {active_generation}"
                )
            gap_rows = R._discovery_gap_rows(cur)
            row_generations = {str(row["generation_id"]) for row in gap_rows}
            if row_generations - {generation}:
                raise SystemExit(
                    "daily-discover ABORT: gap rows span the wrong generation:"
                    f" {sorted(row_generations)}"
                )
            cur.execute(
                "SELECT m.variant_id FROM market_universe_member m"
                " INNER JOIN market_universe_lock l ON l.id=m.universe_lock_id"
                " WHERE l.is_current=1"
            )
            universe_ids = sorted({int(row["variant_id"]) for row in cur.fetchall()})
        return {
            "generation": generation,
            "gapRows": gap_rows,
            "universeIds": universe_ids,
        }
    finally:
        conn.close()


def _load_state(path: Path, generation: str) -> dict[str, Any]:
    if not path.is_file():
        raise SystemExit(
            "daily-discover ABORT: state is not initialized; run"
            " operator_control.py daily-discover-activate --initialize once"
        )
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"daily-discover ABORT: unreadable state: {error}") from error
    if state.get("contract") != STATE_CONTRACT:
        raise SystemExit("daily-discover ABORT: unsupported state contract")
    if str(state.get("generation") or "") != generation:
        raise SystemExit(
            "daily-discover ABORT: state generation"
            f" {state.get('generation')} does not match {generation}"
        )
    known = _normalise_ids(state.get("knownGapIds") or [], "knownGapIds")
    pending = _normalise_ids(
        state.get("pendingActivationIds") or [], "pendingActivationIds"
    )
    if set(known) & set(pending):
        raise SystemExit("daily-discover ABORT: known and pending IDs overlap")
    return {**state, "knownGapIds": known, "pendingActivationIds": pending}


def _write_state(
    path: Path, generation: str, known_ids: Iterable[int],
    pending_ids: Iterable[int], action: str,
) -> dict[str, Any]:
    state = {
        "contract": STATE_CONTRACT,
        "generation": generation,
        "knownGapIds": _normalise_ids(known_ids, "knownGapIds"),
        "pendingActivationIds": _normalise_ids(
            pending_ids, "pendingActivationIds"
        ),
        "lastAction": action,
        "updatedAt": _utc_now(),
    }
    if set(state["knownGapIds"]) & set(state["pendingActivationIds"]):
        raise SystemExit("daily-discover ABORT: refusing overlapping state")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(R.canonical_json(state) + b"\n")
    os.replace(temporary, path)
    return state


def _run_http_discovery(
    generation: str, variant_ids: list[int], credentials_env: Path | None,
) -> list[dict[str, Any]]:
    import snk_identity_discover as snk

    reports: list[dict[str, Any]] = []
    code = snk.cmd_snk_identity_discover(SimpleNamespace(
        write=True,
        allow_repoint=False,
        generation=generation,
        tcg="",
        min_pop=R.DISCOVERY_GAP_POP,
        limit=0,
        per_card=20,
        delay=0.8,
        workers=8,
        credentials_env=credentials_env,
        variant_ids=variant_ids,
        report_sink=reports,
    ))
    if code != 0:
        raise SystemExit(f"daily-discover ABORT: HTTP discovery exited {code}")
    return reports


def _run_browser_discovery(
    generation: str, rows: list[Mapping[str, Any]],
    credentials_env: Path | None,
) -> list[dict[str, Any]]:
    import pc_identity_discover as pc

    reports: list[dict[str, Any]] = []
    by_tcg: dict[str, list[int]] = {}
    for row in rows:
        tcg = str(row["tcg"])
        if tcg not in pc.CATEGORY_BY_TCG:
            raise SystemExit(f"daily-discover ABORT: PC has no category for {tcg}")
        by_tcg.setdefault(tcg, []).append(int(row["variant_id"]))
    for tcg, variant_ids in sorted(by_tcg.items()):
        code = pc.cmd_pc_identity_discover(SimpleNamespace(
            write=True,
            generation=generation,
            tcg=tcg,
            language="en",
            min_pop=R.DISCOVERY_GAP_POP,
            limit=0,
            delay=1.5,
            allow_repoint=False,
            timeout=90,
            no_fetch=False,
            credentials_env=credentials_env,
            variant_ids=variant_ids,
            report_sink=reports,
        ))
        if code != 0:
            raise SystemExit(f"daily-discover ABORT: PC discovery exited {code}")

    target_ids = sorted({int(row["variant_id"]) for row in rows})
    code = R.cmd_pc_identity_reverify(SimpleNamespace(
        write=True,
        pages_dir=None,
        fetch_missing=False,
        map=None,
        credentials_env=credentials_env,
        variant_ids=target_ids,
    ))
    if code != 0:
        raise SystemExit(f"daily-discover ABORT: PC reverify exited {code}")
    return reports


def _prime_new_actives(variant_ids: Iterable[int]) -> None:
    """新 exact binding 落地之後、activation 之前，先集齊價/成交/pop 同 canonical map。

    2026-08-13 v134/v315 事故：discovery 落咗 binding，但 canonical PC map
    （c11_pc_ebay_map_full900.jsonl）冇人 consolidate，S8 price-materialize 讀
    stale map 出唔到 quote，S12 product_ready gap 卡死（missingPrice），成條
    夜鏈 abort。當晚人手橋接嘅次序就係呢度固化嘅次序：
      1. scoped collect incr（cmd_incr 自己會 rebuild registry —— registry 係
         由 DB 現任 exact binding 投影出嚟，包埋新卡；跟住攞 full900 頁、
         ingest 價/成交、寫 checkpoint —— daily-accept 嘅 checkpoint gate
         都係嗰晚缺呢啲先紅）；
      2. consolidate_pc_map --write（fail-closed：registry 有活躍 PC binding
         而搵唔到 verified transport row 會 raise）。
    兩步任一死 → SystemExit → state keep 住 pendingActivationIds，下一輪
    coordinator 由 recovery_ids 重試，唔會靜靜跳過。
    """
    ids = {int(value) for value in variant_ids}
    if not ids:
        return
    import collect_control as CC

    # cmd_incr 對 explicit variant 係 fail-closed：張卡喺請求嘅 adapter 冇
    # exact binding 就 raise。新卡好少五條 lane 齊（PC-only / SNK-only 好常見），
    # 所以先 rebuild registry（DB 現任 exact binding 嘅投影），再由 registry
    # 推導每個 adapter 實際有邊啲目標卡，逐 adapter 開 scoped run。
    CC.cmd_status(rebuild_registry=True)
    variants_by_adapter: dict[str, list[int]] = {}
    for row in CC._jsonl_rows(CC.REGISTRY_PATH):
        vid = int(row.get("variantId") or 0)
        if vid in ids:
            variants_by_adapter.setdefault(str(row["adapter"]), []).append(vid)
    for adapter in sorted(variants_by_adapter):
        report = CC.cmd_incr(
            adapters=[adapter],
            limit=None,
            dry_run=False,
            delay=1.0,
            workers=1,
            # PC 頁一定要 CDP 9333 headed Chrome；SNK/GemRate 係 http，
            # 唔開瀏覽器。
            ensure_browser=adapter in {"pc_ebay_sales", "en_price_ref"},
            pc_resume_report=None,
            pc_sleep=None,
            pc_workers=None,
            variant_ids=sorted(variants_by_adapter[adapter]),
        )
        if not report.get("ok"):
            raise SystemExit(
                "daily-discover ABORT: scoped collect"
                f" adapter={adapter} variants={sorted(variants_by_adapter[adapter])} failed"
            )
    import subprocess
    import sys

    consolidate = subprocess.run(
        [sys.executable, "-X", "utf8",
         str(R.ROOT / "pipelines" / "consolidate_pc_map.py"), "--write"],
        cwd=str(R.ROOT), capture_output=True, text=True, encoding="utf-8",
    )
    if consolidate.returncode != 0:
        raise SystemExit(
            "daily-discover ABORT: consolidate_pc_map failed before activation:\n"
            + (consolidate.stderr or consolidate.stdout or "")[-2000:]
        )


def _run_activation(generation: str) -> None:
    # Discovery uses the long-lived backend account. Rebuild stages must not:
    # cmd_freeze creates the short-lived rebuild account described by
    # rebuild.env, and passing backend.env through would make the frozen writer
    # try to run S2-S11 with credentials that intentionally lost DML.
    code = R.cmd_e2e(SimpleNamespace(
        generation=generation,
        invalidate_from="identity-resolve",
        credentials_env=None,
        freshness_hours=72.0,
        skip_bake=True,
    ))
    if code != 0:
        raise SystemExit(f"daily-discover ABORT: 036 activation exited {code}")




def _datetime_value(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    text = str(value).strip().replace("T", " ").removesuffix("Z")
    return datetime.fromisoformat(text).replace(tzinfo=None)


def _row_due(row: Mapping[str, Any], now: datetime) -> bool:
    quarantine = _datetime_value(row.get("quarantine_until"))
    next_due = _datetime_value(row.get("next_due_at"))
    return (quarantine is None or quarantine <= now) and (
        next_due is None or next_due <= now
    )


def _json_value(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if value is None:
        return None
    try:
        return json.loads(str(value))
    except json.JSONDecodeError:
        return str(value)


def _attempt_evidence(
    *,
    variant_id: int,
    lane: str,
    provider_reports: list[Mapping[str, Any]],
    ledger_row: Mapping[str, Any],
    activation_attempted: bool,
    active: bool,
) -> dict[str, Any]:
    provider_entries: list[dict[str, Any]] = []
    for report in provider_reports:
        proposals = [
            item for item in report.get("proposals", [])
            if int(item.get("variant_id") or 0) == variant_id
        ]
        held = [
            item for item in report.get("held", [])
            if int(item.get("variant_id") or 0) == variant_id
        ]
        if proposals or held:
            provider_entries.append({
                "provider": "pricecharting"
                if report.get("pcIdentityDiscover") else "snkrdunk",
                "proposals": proposals,
                "held": held,
            })
    return {
        "contract": "cardz-discovery-attempt-evidence-v1",
        "variantId": variant_id,
        "lane": lane,
        "providerEvidence": provider_entries,
        "postState": {
            "catalogStatus": ledger_row.get("catalog_status"),
            "discoveryStatus": ledger_row.get("discovery_status"),
            "blockerCode": ledger_row.get("blocker_code"),
            "detail": _json_value(ledger_row.get("detail_json")),
            "active": active,
        },
        "activationAttempted": activation_attempted,
    }


def merge_provider_targets(
    new_ids: Iterable[int], retry_ids: Iterable[int], *, limit: int,
) -> list[int]:
    """New gaps win, but no lane may exceed its provider request budget."""

    if limit < 0:
        raise ValueError("limit must be non-negative")
    merged: list[int] = []
    for raw in (*list(new_ids), *list(retry_ids)):
        variant_id = int(raw)
        if variant_id > 0 and variant_id not in merged:
            merged.append(variant_id)
        if len(merged) >= limit:
            break
    return merged


def _record_ledger_attempts(
    credentials_env: Path | None,
    records: list[Mapping[str, Any]],
    previous_rows: Mapping[int, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Persist actual evidence and advance retry/quarantine deterministically."""

    if not records:
        return []
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    receipts: list[dict[str, Any]] = []
    conn = R.connect(credentials_env or R.DAILY_CREDENTIALS_ENV)
    try:
        with conn.cursor() as cur:
            for record in records:
                variant_id = int(record["variant_id"])
                digest = str(record["evidence_sha256"])
                previous = previous_rows.get(variant_id) or {}
                lifecycle = attempt_lifecycle(
                    str(previous.get("last_evidence_sha256") or "") or None,
                    int(previous.get("consecutive_same_evidence_count") or 0),
                    digest,
                    success=bool(record.get("success")),
                    terminal=bool(record.get("terminal")),
                )
                next_due = (
                    now + timedelta(hours=int(lifecycle["nextDueHours"]))
                    if lifecycle["nextDueHours"] is not None else None
                )
                quarantine = (
                    now + timedelta(hours=int(lifecycle["quarantineHours"]))
                    if lifecycle["quarantineHours"] is not None else None
                )
                cur.execute(
                    """
                    UPDATE market_identity_discovery_ledger
                    SET attempt_count=attempt_count+1,
                        last_attempt_at=%s,
                        last_outcome=%s,
                        last_evidence_sha256=%s,
                        consecutive_same_evidence_count=%s,
                        next_due_at=%s,
                        quarantine_until=%s,
                        last_reviewed_at=%s,
                        updated_at=%s
                    WHERE variant_id=%s
                    """,
                    (
                        now, str(record["outcome"]), digest,
                        int(lifecycle["consecutive"]), next_due, quarantine,
                        now, now, variant_id,
                    ),
                )
                if cur.rowcount != 1:
                    raise RuntimeError(
                        f"discovery attempt ledger row missing for variant {variant_id}"
                    )
                receipts.append({
                    "variantId": variant_id,
                    "outcome": str(record["outcome"]),
                    "evidenceSha256": digest,
                    "consecutiveSameEvidence": int(lifecycle["consecutive"]),
                    "nextDueAt": next_due.isoformat(sep=" ") if next_due else None,
                    "quarantineUntil": quarantine.isoformat(sep=" ") if quarantine else None,
                })
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return receipts


def _write_attempt_report(document: Mapping[str, Any]) -> Path:
    DISCOVERY_REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = DISCOVERY_REPORT_DIR / f"daily-discovery-{document['lane']}-{stamp}.json"
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(R.canonical_json(document) + b"\n")
    os.replace(temporary, path)
    return path


def cmd_daily_discover_activate(args: Any) -> int:
    state_path = Path(getattr(args, "state_path", None) or STATE_PATH)
    credentials_env = getattr(args, "credentials_env", None)
    snapshot = _runtime_snapshot(credentials_env)
    generation = snapshot["generation"]
    current_ids = [int(row["variant_id"]) for row in snapshot["gapRows"]]

    if bool(getattr(args, "initialize", False)):
        if state_path.exists():
            existing = _load_state(state_path, generation)
            if (
                existing["knownGapIds"] == sorted(current_ids)
                and not existing["pendingActivationIds"]
            ):
                print(json.dumps({
                    "dailyDiscovery": "already-initialized",
                    "generation": generation,
                    "knownGaps": len(current_ids),
                }, ensure_ascii=False))
                return 0
            raise SystemExit(
                "daily-discover ABORT: initialized state differs from current DB;"
                " refusing to rewrite the cursor"
            )
        state = _write_state(
            state_path, generation, current_ids, [], "explicit-initialize"
        )
        print(json.dumps({
            "dailyDiscovery": "initialized",
            "generation": generation,
            "knownGaps": len(state["knownGapIds"]),
            "state": state_path.relative_to(R.ROOT).as_posix(),
        }, ensure_ascii=False))
        return 0

    lane = str(getattr(args, "lane", "") or "")
    if lane not in LANES:
        raise SystemExit("daily-discover ABORT: --lane http|browser is required")
    state = _load_state(state_path, generation)
    known = set(state["knownGapIds"])
    recovery_ids = set(state["pendingActivationIds"])

    plan = plan_gap_delta(known, snapshot["gapRows"], lane)
    ledger_rows = _load_ledger_rows(credentials_env)
    ledger_by_id = {int(row["variant_id"]): row for row in ledger_rows}
    ledger_plan = plan_ledger_retry_targets(
        snapshot["gapRows"], ledger_rows, lane, limit=DAILY_PROVIDER_LIMIT,
    )
    target_ids = merge_provider_targets(
        plan["targetIds"], ledger_plan["targetIds"],
        limit=DAILY_PROVIDER_LIMIT,
    )

    clock = datetime.now(timezone.utc).replace(tzinfo=None)
    inactive_exact_ids = {
        int(row["variant_id"]) for row in ledger_rows
        if str(row.get("discovery_status") or "") == "inactive_exact"
        and _lane_for_language(row.get("language")) == lane
        and _row_due(row, clock)
    }
    multiple_exact_ids = {
        int(row["variant_id"]) for row in ledger_rows
        if str(row.get("blocker_code") or "") == "multiple_exact_bindings"
        and _lane_for_language(row.get("language")) == lane
        and _row_due(row, clock)
    }

    target_set = set(target_ids)
    provider_reports: list[dict[str, Any]] = []
    if target_ids:
        gap_rows_by_id = {
            int(row["variant_id"]): row for row in snapshot["gapRows"]
        }
        ledger_rows_by_id = {
            int(row["variant_id"]): row for row in ledger_plan["targetRows"]
        }
        rows = []
        for variant_id in target_ids:
            row = gap_rows_by_id.get(variant_id) or ledger_rows_by_id.get(variant_id)
            if row is None:
                raise SystemExit(
                    f"daily-discover ABORT: missing discovery row for variant {variant_id}"
                )
            rows.append(row)
        if lane == "http":
            provider_reports = _run_http_discovery(
                generation, target_ids, credentials_env,
            )
        else:
            provider_reports = _run_browser_discovery(
                generation, rows, credentials_env,
            )

    after_discovery = _runtime_snapshot(credentials_env)
    after_gap_ids = {
        int(row["variant_id"]) for row in after_discovery["gapRows"]
    }
    binding_landed = target_set - after_gap_ids
    activation_ids = (
        inactive_exact_ids | binding_landed | recovery_ids
    ) - set(after_discovery["universeIds"])
    activation_attempted = bool(activation_ids)
    if activation_attempted:
        pending_ids = sorted(activation_ids)
        _write_state(
            state_path, generation,
            known & after_gap_ids,
            pending_ids,
            f"{lane}-awaiting-atomic-activation",
        )
        _prime_new_actives(pending_ids)
        _run_activation(generation)
        snapshot = _runtime_snapshot(credentials_env)
    else:
        snapshot = after_discovery

    universe_ids = set(snapshot["universeIds"])
    ledger_after = _load_ledger_rows(credentials_env)
    ledger_after_by_id = {int(row["variant_id"]): row for row in ledger_after}
    attempted_ids = target_set | inactive_exact_ids | multiple_exact_ids | recovery_ids
    records: list[dict[str, Any]] = []
    evidence_documents: dict[int, dict[str, Any]] = {}
    for variant_id in sorted(attempted_ids):
        row = ledger_after_by_id.get(variant_id)
        if row is None:
            raise RuntimeError(f"discovery ledger lost variant {variant_id}")
        active = variant_id in universe_ids
        terminal = variant_id in multiple_exact_ids
        if terminal:
            outcome = "quarantined_multiple_exact"
        elif active:
            outcome = "activated"
        elif str(row.get("discovery_status") or "") == "inactive_exact":
            outcome = "activation_pending"
        else:
            outcome = "still_unresolved"
        document = _attempt_evidence(
            variant_id=variant_id,
            lane=lane,
            provider_reports=provider_reports if variant_id in target_set else [],
            ledger_row=row,
            activation_attempted=activation_attempted and variant_id in activation_ids,
            active=active,
        )
        digest = evidence_sha256(document)
        evidence_documents[variant_id] = document
        records.append({
            "variant_id": variant_id,
            "outcome": outcome,
            "evidence_sha256": digest,
            "success": active,
            "terminal": terminal,
        })
    attempts = _record_ledger_attempts(
        credentials_env, records, ledger_by_id,
    )

    final_gap_ids = {
        int(row["variant_id"]) for row in snapshot["gapRows"]
    }
    _write_state(
        state_path, generation, final_gap_ids, [], f"{lane}-complete",
    )
    activated = sorted(attempted_ids & universe_ids)
    pending_activation = sorted(
        variant_id for variant_id in attempted_ids - universe_ids
        if str((ledger_after_by_id.get(variant_id) or {}).get("discovery_status") or "")
        == "inactive_exact"
    )
    report = {
        "dailyDiscovery": "complete",
        "generation": generation,
        "lane": lane,
        "knownGaps": len(final_gap_ids),
        "providerLimit": DAILY_PROVIDER_LIMIT,
        "targeted": target_ids,
        "ledgerRetry": ledger_plan,
        "remainingNewForLane": plan["targetIds"][len(target_ids):],
        "deferredToOtherLane": plan["deferredIds"],
        "multipleExactQuarantined": sorted(multiple_exact_ids),
        "activation": {
            "requested": sorted(activation_ids),
            "activated": activated,
            "qualifiedPending": pending_activation,
            "recovered": sorted(recovery_ids),
        },
        "attempts": attempts,
        "evidence": {
            str(variant_id): evidence_documents[variant_id]
            for variant_id in sorted(evidence_documents)
        },
    }
    report_path = _write_attempt_report(report)
    report["reportPath"] = report_path.relative_to(R.ROOT).as_posix()
    print(json.dumps(report, ensure_ascii=False, default=str))
    return 0
