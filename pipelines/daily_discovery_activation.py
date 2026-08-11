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

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable, Mapping

import rebuild_036 as R


STATE_CONTRACT = "cardz-036-daily-discovery-state-v1"
STATE_PATH = R.ROOT / "data" / "runtime" / "rebuild-036" / "daily-discovery-state.json"
LANES = frozenset({"http", "browser"})


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _normalise_ids(values: Iterable[Any], field: str) -> list[int]:
    ids = [int(value) for value in values]
    if any(value <= 0 for value in ids) or len(ids) != len(set(ids)):
        raise SystemExit(f"daily-discover ABORT: {field} must contain unique positive IDs")
    return sorted(ids)


def _lane_for_language(language: Any) -> str:
    return "browser" if str(language or "").strip().lower() == "en" else "http"


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
) -> None:
    import snk_identity_discover as snk

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
    ))
    if code != 0:
        raise SystemExit(f"daily-discover ABORT: HTTP discovery exited {code}")


def _run_browser_discovery(
    generation: str, rows: list[Mapping[str, Any]],
    credentials_env: Path | None,
) -> None:
    import pc_identity_discover as pc

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
    pending = set(state["pendingActivationIds"])

    recovered: dict[str, list[int]] | None = None
    if pending:
        still_pending = sorted(pending - set(snapshot["universeIds"]))
        if still_pending:
            _run_activation(generation)
            snapshot = _runtime_snapshot(credentials_env)
        recovered = {
            "requested": sorted(pending),
            "activated": sorted(pending & set(snapshot["universeIds"])),
            "qualifiedPending": sorted(pending - set(snapshot["universeIds"])),
        }
        known &= {int(row["variant_id"]) for row in snapshot["gapRows"]}
        _write_state(state_path, generation, known, [], "recovered-pending-activation")

    plan = plan_gap_delta(known, snapshot["gapRows"], lane)
    target_ids = plan["targetIds"]
    target_set = set(target_ids)
    if target_ids:
        rows = [
            row for row in snapshot["gapRows"]
            if int(row["variant_id"]) in target_set
        ]
        if lane == "http":
            _run_http_discovery(generation, target_ids, credentials_env)
        else:
            _run_browser_discovery(generation, rows, credentials_env)

        after_discovery = _runtime_snapshot(credentials_env)
        after_gap_ids = {int(row["variant_id"]) for row in after_discovery["gapRows"]}
        resolved = sorted(target_set - after_gap_ids)
        if resolved:
            known &= after_gap_ids
            _write_state(
                state_path, generation, known, resolved,
                f"{lane}-binding-landed-awaiting-activation",
            )
            _run_activation(generation)
            snapshot = _runtime_snapshot(credentials_env)
            universe_ids = set(snapshot["universeIds"])
            activation = {
                "requested": resolved,
                "activated": sorted(set(resolved) & universe_ids),
                "qualifiedPending": sorted(set(resolved) - universe_ids),
            }
            known &= {int(row["variant_id"]) for row in snapshot["gapRows"]}
            _write_state(
                state_path, generation, known, [], f"{lane}-activation-complete"
            )
        else:
            snapshot = after_discovery
            activation = {"requested": [], "activated": [], "qualifiedPending": []}
    else:
        activation = {"requested": [], "activated": [], "qualifiedPending": []}

    final_plan = plan_gap_delta(known, snapshot["gapRows"], lane)
    unresolved = final_plan["newIds"]
    if unresolved:
        known &= set(final_plan["currentIds"])
        _write_state(state_path, generation, known, [], f"{lane}-unresolved")
        print(json.dumps({
            "dailyDiscovery": "blocked",
            "generation": generation,
            "lane": lane,
            "targeted": target_ids,
            "unresolved": unresolved,
            "deferredToOtherLane": final_plan["deferredIds"],
            "activation": activation,
            "recovered": recovered,
        }, ensure_ascii=False))
        return 1

    current_ids = final_plan["currentIds"]
    _write_state(state_path, generation, current_ids, [], f"{lane}-complete")
    print(json.dumps({
        "dailyDiscovery": "complete",
        "generation": generation,
        "lane": lane,
        "knownGaps": len(current_ids),
        "targeted": target_ids,
        "activation": activation,
        "recovered": recovered,
    }, ensure_ascii=False))
    return 0
