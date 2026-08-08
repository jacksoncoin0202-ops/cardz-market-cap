"""036 identity-first rebuild orchestrator (PLAN 036 §6.0).

Not an entry point: invoked only through operator_control.py subcommands
rebuild-036 / rebuild-036-activate / rebuild-036-unfreeze. Checkpoints live in
cardz_rebuild_checkpoint (the thing being rebuilt IS the database, so per-stage
atomicity comes free). The linear run covers S0..S11 only; S12 activation is a
separate subcommand, and S13/S14 run only via an explicit --stage after
activation has been proven.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import pymysql

ROOT = Path(__file__).resolve().parents[1]
GENERATION_RE = re.compile(r"^036_\d{8}T\d{6}Z$")
REBUILD_STAGE_COMPLETE = "complete"
DEFAULT_CREDENTIALS_ENV = ROOT / "data" / "runtime" / "config" / "rebuild.env"
RESTORE_PROOF = ROOT / "data" / "runtime" / "rebuild-036" / "restore-proof-rowcounts-20260808.json"
PRUNE_ALLOWLIST = ROOT / "data" / "policy" / "prune-order-allowlist.json"
FREEZE_PROOF_SCRIPT = ROOT / "scripts" / "prove_writer_freeze.py"
MYSQL_CONTAINER = "cardz-market-cap-db-1"
POLICY = {"minPop": 1000, "planVersion": "036"}

MIGRATION_036_FILES = frozenset({
    "036_catalog_provider_capture_receipt.mysql.sql",
    "036_market_gemrate_psa10_observation_v2.mysql.sql",
    "036_operator_latest_gemrate_psa10.mysql.sql",
    "036_price_observation_add_first_run_id.mysql.sql",
    "036_price_observation_add_last_run_id.mysql.sql",
    "036_price_observation_add_restamp_count.mysql.sql",
    "036_price_observation_backfill_first_run_id.mysql.sql",
    "036_rebuild_bookkeeping.mysql.sql",
})

# Variant-linked tables invisible to the information_schema FK closure. S10
# derives its candidate set as (FK closure from catalog_variant) | this set and
# two-way checks the result against data/policy/prune-order-allowlist.json.
MANUAL_NO_FK_TABLES = frozenset({
    "operator_binding_freeze",
    "catalog_variant_remap",
    "market_gemrate_psa10_observation_v2",
    "market_image_decision_archive",
    "catalog_rebuild_member",
    "catalog_population_identity_incident",
})

NEVER_DELETE_TABLES = frozenset({"market_raw_payload_object", "market_source_observation"})

# PLAN §6.1 input source 3: the old checkout's raw caches feed the worklist and
# the card capture cache is merged copy-if-absent. That folder also owns the
# MySQL compose file, so it must exist; treat absence as an environment fault.
OLD_CHECKOUT_ROOT = Path("C:/Users/jackson0202/Documents/Playground/cardz-market-cap")


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def connect(credentials_env: Path) -> pymysql.connections.Connection:
    env = load_env_file(credentials_env)
    return pymysql.connect(
        host=env.get("CARDZ_DB_HOST", "127.0.0.1"),
        port=int(env.get("CARDZ_DB_PORT", "3308")),
        user=env["CARDZ_DB_USER"],
        password=env["CARDZ_DB_PASSWORD"],
        database=env.get("CARDZ_DB_NAME", "cardz_market_cap"),
        charset="utf8mb4",
        autocommit=False,
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=10,
    )


# ---------------------------------------------------------------------------
# Stage implementations. Each returns {"input_sha256", "output_sha256",
# "counts"}; input_fn is recomputed on resume to detect upstream drift.
# ---------------------------------------------------------------------------

def _run_freeze_proof() -> None:
    """Call site for scripts/prove_writer_freeze.py (S0; S12/S13 have their own)."""
    result = subprocess.run(
        [sys.executable, "-X", "utf8", str(FREEZE_PROOF_SCRIPT)],
        cwd=str(ROOT), capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise SystemExit(
            "S0 ABORT: writer freeze is not in force.\n"
            f"{result.stdout.strip()}\n{result.stderr.strip()}"
        )


def stage_preflight(ctx: SimpleNamespace) -> dict[str, Any]:
    counts: dict[str, Any] = {}

    _run_freeze_proof()
    counts["writer_freeze"] = "proven_1142"

    allowlist = json.loads(PRUNE_ALLOWLIST.read_text(encoding="utf-8"))
    tables = allowlist.get("tables")
    if not isinstance(tables, dict) or not tables:
        raise SystemExit("S0 ABORT: prune allowlist has no tables")
    for name, entry in tables.items():
        if entry.get("deleteMode") not in {"delete", "nullify", "never"}:
            raise SystemExit(f"S0 ABORT: prune allowlist deleteMode invalid: {name}")
        if not entry.get("reviewedAt") or not entry.get("reviewedBy"):
            raise SystemExit(f"S0 ABORT: prune allowlist entry unreviewed: {name}")
    for name in NEVER_DELETE_TABLES | MANUAL_NO_FK_TABLES:
        if name not in tables:
            raise SystemExit(f"S0 ABORT: prune allowlist missing required table: {name}")
    for name in NEVER_DELETE_TABLES:
        if tables[name]["deleteMode"] != "never":
            raise SystemExit(f"S0 ABORT: {name} must be deleteMode never")
    counts["allowlist_tables"] = len(tables)

    # Verbose CSV and whole-row matching: four known tasks run cardz scripts
    # without "cardz" in the task name (PC-FULL-900 shard cmds, pc_s2_keepalive).
    schtasks = subprocess.run(
        ["schtasks", "/query", "/v", "/fo", "CSV"], capture_output=True, text=True,
    )
    if schtasks.returncode != 0:
        raise SystemExit("S0 ABORT: schtasks query failed")
    enabled = []
    matched_names: set[str] = set()
    for row in csv.DictReader(io.StringIO(schtasks.stdout)):
        if not any("cardz" in str(value).lower() for value in row.values()):
            continue
        name = row.get("TaskName", "?")
        matched_names.add(name)
        state = (row.get("Scheduled Task State") or row.get("Status") or "").strip().lower()
        if state != "disabled":
            enabled.append(f"{name}={state or 'unknown'}")
    if enabled:
        raise SystemExit(f"S0 ABORT: cardz-linked scheduled tasks not Disabled: {sorted(set(enabled))}")
    counts["cardz_tasks_all_disabled"] = len(matched_names)

    proof = json.loads(RESTORE_PROOF.read_text(encoding="utf-8"))
    dump_file = Path(proof["dumpFile"])
    if not dump_file.is_file():
        raise SystemExit(f"S0 ABORT: backup dump missing: {dump_file}")
    actual = sha256_file(dump_file)
    if actual != proof["dumpSha256"]:
        raise SystemExit("S0 ABORT: backup dump sha mismatch against restore proof")
    counts["backup_sha_verified"] = True

    with ctx.conn.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) AS n FROM market_ingest_run WHERE status='running'")
        stale = int(cursor.fetchone()["n"])
    if stale:
        raise SystemExit(f"S0 ABORT: {stale} market_ingest_run rows still 'running'")
    counts["stale_running_runs"] = 0

    return {"input_sha256": None, "output_sha256": None, "counts": counts}


def _migration_input_sha(_: SimpleNamespace) -> str:
    entries = []
    for name in sorted(MIGRATION_036_FILES):
        path = ROOT / "pipelines" / "migrations" / name
        entries.append([name, sha256_bytes(path.read_bytes())])
    return sha256_bytes(canonical_json(entries))


def stage_migrate(ctx: SimpleNamespace) -> dict[str, Any]:
    from db_runtime import migrate

    report = migrate(ctx.conn, ROOT / "pipelines" / "migrations", only=set(MIGRATION_036_FILES))
    with ctx.conn.cursor() as cursor:
        cursor.execute(
            "SELECT migration_file, content_sha256 FROM cardz_migration_ledger "
            "WHERE migration_file LIKE '036%%' ORDER BY migration_file"
        )
        rows = [[row["migration_file"], row["content_sha256"]] for row in cursor.fetchall()]
    if len(rows) != len(MIGRATION_036_FILES):
        raise SystemExit(f"S1 ABORT: expected {len(MIGRATION_036_FILES)} ledgered 036 files, found {len(rows)}")
    return {
        "input_sha256": _migration_input_sha(ctx),
        "output_sha256": sha256_bytes(canonical_json(rows)),
        "counts": report,
    }


def _jsonl_psa_ids(path: Path) -> set[str]:
    """Extract the GemRate id space from brute-harvest rowData (field: psa_id)."""

    ids: set[str] = set()
    if not path.is_file():
        return ids
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            value = row.get("psa_id")
            if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value):
                ids.add(value)
    return ids


def _db_gemrate_ids(conn) -> tuple[set[str], int]:
    """Sweep every base-table column that can hold a GemRate id (§6.1 source 2).

    Covers current / rejected / historical ids and the 625 zero-observation
    variants' candidates: all of those live in gemrate-id columns or in
    catalog_source_identity(source_code='gemrate').
    """

    ids: set[str] = set()
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT c.table_name, c.column_name FROM information_schema.columns c"
            " JOIN information_schema.tables t ON t.table_schema=c.table_schema"
            "  AND t.table_name=c.table_name AND t.table_type='BASE TABLE'"
            " WHERE c.table_schema=DATABASE() AND c.column_name LIKE '%%gemrate%%id%%'"
        )
        columns = [(row["TABLE_NAME"], row["COLUMN_NAME"]) for row in cursor.fetchall()]
        for table, column in columns:
            cursor.execute(
                f"SELECT DISTINCT `{column}` AS gid FROM `{table}`"
                f" WHERE `{column}` REGEXP '^[0-9a-f]{{40}}$'"
            )
            ids.update(row["gid"] for row in cursor.fetchall())
        cursor.execute(
            "SELECT DISTINCT external_entity_id AS gid FROM catalog_source_identity"
            " WHERE source_code='gemrate' AND external_entity_id REGEXP '^[0-9a-f]{40}$'"
        )
        ids.update(row["gid"] for row in cursor.fetchall())
    return ids, len(columns)


def _manifest_ids(runs_root: Path) -> set[str]:
    ids: set[str] = set()
    if not runs_root.is_dir():
        return ids
    for manifest in runs_root.glob("*/manifest.json"):
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for receipt in data.get("failureReceipts") or []:
            value = str(receipt.get("gemrateId") or "")
            if re.fullmatch(r"[0-9a-f]{40}", value):
                ids.add(value)
    return ids


def _card_dir_ids(cards_dir: Path) -> set[str]:
    if not cards_dir.is_dir():
        return set()
    return {
        entry.name for entry in cards_dir.iterdir()
        if entry.is_dir() and re.fullmatch(r"[0-9a-f]{40}", entry.name)
    }


def stage_discover(ctx: SimpleNamespace) -> dict[str, Any]:
    """S2 (§6.1): full GemRate landing. Files only — zero DB writes by design.

    No filtering by POP / language / printing / existing variant (D5). Raw
    payloads land in the sha-deduplicated private cache, so old raw files are
    never overwritten; completion means every worklist id has a capture or an
    honest failure receipt. Browser-level crashes fail the stage instead.
    """

    import shutil
    from datetime import datetime, timezone

    import gemrate_source

    if not OLD_CHECKOUT_ROOT.is_dir():
        raise SystemExit(f"S2 ABORT: old checkout missing: {OLD_CHECKOUT_ROOT}")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = gemrate_source.OUT_DIR / "runs" / ctx.generation
    run_dir.mkdir(parents=True, exist_ok=True)

    # 1) Merge the old checkout's capture cache, copy-if-absent (source 3).
    old_cards = OLD_CHECKOUT_ROOT / "data" / "private" / "gemrate" / "cards"
    cards_dir = gemrate_source.CARDS_DIR
    cards_dir.mkdir(parents=True, exist_ok=True)
    merged = skipped_existing = 0
    for entry in sorted(old_cards.iterdir()) if old_cards.is_dir() else []:
        if not entry.is_dir() or not re.fullmatch(r"[0-9a-f]{40}", entry.name):
            continue
        target = cards_dir / entry.name
        if target.exists():
            skipped_existing += 1
            continue
        shutil.copytree(entry, target)
        merged += 1

    # 2) Fresh provider catalog via brute harvest (source 1). Reuse the output
    #    already on disk when retrying so a crash in the dump leg does not
    #    re-crawl the whole set list.
    brute_dir = ROOT / "data" / "private" / "gemrate_brute"
    brute_all = brute_dir / "all_cards.jsonl"
    brute_reused = brute_all.is_file()
    if not brute_reused:
        harvest = subprocess.run(
            [sys.executable, "-X", "utf8", str(ROOT / "pipelines" / "gemrate_brute_harvest.py"),
             "--all-sets"],
            cwd=str(ROOT), capture_output=True, text=True,
        )
        (run_dir / f"brute-harvest-{stamp}.log").write_text(
            (harvest.stdout or "") + "\n--- stderr ---\n" + (harvest.stderr or ""),
            encoding="utf-8",
        )
        if harvest.returncode != 0 or not brute_all.is_file():
            raise SystemExit(f"S2 ABORT: brute harvest failed rc={harvest.returncode}")

    # 3) Worklist = union of every id source; no filtering (D5).
    sources: dict[str, set[str]] = {}
    sources["brute_fresh"] = _jsonl_psa_ids(brute_all)
    sources["brute_old_checkout"] = _jsonl_psa_ids(
        OLD_CHECKOUT_ROOT / "data" / "private" / "gemrate_brute" / "all_cards.jsonl"
    )
    sources["ids_file"] = {
        gid for gid in gemrate_source.load_ids(None)
        if re.fullmatch(r"[0-9a-f]{40}", gid)
    }
    sources["db"], db_columns = _db_gemrate_ids(ctx.conn)
    sources["cards_cache_new"] = _card_dir_ids(cards_dir)
    sources["cards_cache_old"] = _card_dir_ids(old_cards)
    sources["manifests_new"] = _manifest_ids(gemrate_source.OUT_DIR / "runs")
    sources["manifests_old"] = _manifest_ids(
        OLD_CHECKOUT_ROOT / "data" / "private" / "gemrate" / "runs"
    )
    worklist = sorted(set().union(*sources.values()))
    if len(worklist) < 3000:
        raise SystemExit(f"S2 ABORT: worklist implausibly small ({len(worklist)})")
    worklist_path = run_dir / "worklist.txt"
    worklist_path.write_text("\n".join(worklist) + "\n", encoding="utf-8")
    gemrate_source._save(run_dir / "worklist-provenance.json", {
        "generation": ctx.generation,
        "builtAt": stamp,
        "bruteReused": brute_reused,
        "dbGemrateIdColumns": db_columns,
        "cacheMerge": {"copied": merged, "skippedExisting": skipped_existing},
        "sourceCounts": {name: len(values) for name, values in sorted(sources.items())},
        "worklistCount": len(worklist),
        "worklistSha256": sha256_file(worklist_path),
    })

    # 4) Keep pipelines/gemrate_ids.txt the running union (append-only).
    added_to_ids_file = gemrate_source.append_ids(worklist)

    # 5) The long leg: exact public card pages, strict resume, 429 ladder.
    result = gemrate_source.collect_public_card_details(
        worklist, cards_dir=cards_dir, delay=0.3, resume=True, chunk_size=200,
    )
    reasons: dict[str, int] = {}
    for receipt in result.get("failureReceipts") or []:
        key = str(receipt.get("reason") or "unknown")
        reasons[key] = reasons.get(key, 0) + 1
    manifest_path = run_dir / f"public-card-dump-manifest-{stamp}.json"
    gemrate_source._save(manifest_path, {
        "schemaVersion": "1.1.0",
        "runId": f"public_{stamp}",
        "generation": ctx.generation,
        "runStatus": "complete" if result["promotable"] else "partial",
        **result,
    })
    if result.get("error"):
        raise SystemExit(f"S2 ABORT: browser-level failure: {result['error']}")

    counts = {
        "worklist": len(worklist),
        "cacheMergedFromOldCheckout": merged,
        "idsFileAppended": added_to_ids_file,
        "cached": result["cached"],
        "attempted": result["attempted"],
        "succeeded": result["succeeded"],
        "failed": result["failed"],
        "failureReasons": reasons,
        "bruteReused": brute_reused,
    }
    return {
        "input_sha256": None,
        "output_sha256": sha256_file(manifest_path),
        "counts": counts,
    }


# name -> (fn, input_fn, always_run). fn None = designed but not yet built in
# this working copy; the runner stops there instead of faking progress.
LINEAR_STAGES: list[tuple[str, Callable | None, Callable | None, bool]] = [
    ("preflight", stage_preflight, None, True),
    ("migrate", stage_migrate, _migration_input_sha, False),
    ("discover", stage_discover, None, False),
    ("pop-land", None, None, False),
    ("identity-resolve", None, None, False),
    ("bind", None, None, False),
    ("pc-replay", None, None, False),
    ("snk-refresh", None, None, False),
    ("price-materialize", None, None, False),
    ("image-bind", None, None, False),
    ("prune-plan", None, None, False),
    ("validate", None, None, False),
]
POST_ACTIVATION_STAGES: list[tuple[str, Callable | None, Callable | None, bool]] = [
    ("prune-apply", None, None, False),
    ("canary", None, None, False),
]
ALL_STAGES = LINEAR_STAGES + POST_ACTIVATION_STAGES
STAGE_NAMES = [name for name, _, _, _ in ALL_STAGES]


# ---------------------------------------------------------------------------
# Checkpoint machinery
# ---------------------------------------------------------------------------

def _ensure_generation(conn, generation: str) -> None:
    policy_sha = sha256_bytes(canonical_json(POLICY))
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT policy_sha256, min_pop FROM cardz_rebuild_generation WHERE generation_id=%s",
            (generation,),
        )
        row = cursor.fetchone()
        if row is None:
            cursor.execute(
                "INSERT INTO cardz_rebuild_generation (generation_id, created_at, policy_sha256, min_pop)"
                " VALUES (%s, UTC_TIMESTAMP(6), %s, %s)",
                (generation, policy_sha, POLICY["minPop"]),
            )
            conn.commit()
            return
        if row["policy_sha256"] != policy_sha or int(row["min_pop"]) != POLICY["minPop"]:
            raise SystemExit(
                f"ABORT: generation {generation} exists with a different policy"
                f" (recorded {row['policy_sha256'][:12]}.., min_pop {row['min_pop']})"
            )


def _checkpoints(conn, generation: str) -> dict[str, dict[str, Any]]:
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT stage, status, attempt, input_sha256, output_sha256, error_code"
            " FROM cardz_rebuild_checkpoint WHERE generation_id=%s",
            (generation,),
        )
        return {row["stage"]: row for row in cursor.fetchall()}


def _stage_begin(conn, generation: str, stage: str) -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            "INSERT INTO cardz_rebuild_checkpoint (generation_id, stage, status, attempt, started_at)"
            " VALUES (%s, %s, 'running', 1, UTC_TIMESTAMP(6))"
            " ON DUPLICATE KEY UPDATE status='running', attempt=attempt+1,"
            " started_at=UTC_TIMESTAMP(6), finished_at=NULL, error_code=NULL",
            (generation, stage),
        )
    conn.commit()


def _stage_finish(conn, generation: str, stage: str, result: dict[str, Any]) -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            "UPDATE cardz_rebuild_checkpoint SET status=%s, input_sha256=%s, output_sha256=%s,"
            " counts_json=%s, finished_at=UTC_TIMESTAMP(6) WHERE generation_id=%s AND stage=%s",
            (
                REBUILD_STAGE_COMPLETE,
                result.get("input_sha256"),
                result.get("output_sha256"),
                json.dumps(result.get("counts") or {}, ensure_ascii=False, sort_keys=True),
                generation,
                stage,
            ),
        )
    conn.commit()


def _stage_failed(conn, generation: str, stage: str, error_code: str) -> None:
    conn.rollback()
    with conn.cursor() as cursor:
        cursor.execute(
            "UPDATE cardz_rebuild_checkpoint SET status='failed', error_code=%s,"
            " finished_at=UTC_TIMESTAMP(6) WHERE generation_id=%s AND stage=%s",
            (error_code[:64], generation, stage),
        )
    conn.commit()


def _run_stage(ctx: SimpleNamespace, name: str, fn: Callable, *, forced: bool) -> None:
    print(json.dumps({"phase": "stage-start", "stage": name, "forced": forced}, ensure_ascii=False), flush=True)
    _stage_begin(ctx.conn, ctx.generation, name)
    try:
        result = fn(ctx)
    except SystemExit as error:
        _stage_failed(ctx.conn, ctx.generation, name, str(error)[:64] or "systemexit")
        raise
    except Exception as error:  # noqa: BLE001 - checkpoint then surface
        _stage_failed(ctx.conn, ctx.generation, name, f"{type(error).__name__}: {error}"[:64])
        raise
    _stage_finish(ctx.conn, ctx.generation, name, result)
    print(
        json.dumps(
            {"phase": "stage-complete", "stage": name, "counts": result.get("counts") or {}},
            ensure_ascii=False, default=str,
        ),
        flush=True,
    )


def _dry_run(ctx: SimpleNamespace) -> int:
    checkpoints = _checkpoints(ctx.conn, ctx.generation)
    plan = []
    blocked = False
    for name, fn, input_fn, always_run in ALL_STAGES:
        record = checkpoints.get(name)
        status = record["status"] if record else "pending"
        post = (name, fn, input_fn, always_run) in POST_ACTIVATION_STAGES
        if post:
            decision = "explicit --stage only (after activation)"
        elif blocked:
            decision = "blocked upstream"
        elif always_run:
            decision = "run (always)"
        elif status == REBUILD_STAGE_COMPLETE:
            drift = ""
            if input_fn is not None and record and record.get("input_sha256"):
                current = input_fn(ctx)
                if current != record["input_sha256"]:
                    drift = " INPUT DRIFT -> needs --invalidate-from"
                    blocked = True
            decision = f"skip (complete){drift}"
        elif status == "failed":
            decision = "blocked: failed, needs --force-stage"
            blocked = True
        elif fn is None:
            decision = "STOP: not implemented in this build"
            blocked = True
        else:
            decision = "run"
        plan.append({"stage": name, "status": status, "attempt": record["attempt"] if record else 0, "decision": decision})
    print(json.dumps({"generation": ctx.generation, "dryRun": True, "plan": plan}, ensure_ascii=False, indent=1))
    return 0


def cmd_rebuild(args: argparse.Namespace) -> int:
    generation = args.generation
    if not GENERATION_RE.match(generation):
        raise SystemExit(f"--generation must match {GENERATION_RE.pattern}")
    for flag in ("stage", "invalidate_from"):
        value = getattr(args, flag, None)
        if value and value not in STAGE_NAMES:
            raise SystemExit(f"unknown stage '{value}'; stages: {', '.join(STAGE_NAMES)}")

    credentials = args.credentials_env or DEFAULT_CREDENTIALS_ENV
    conn = connect(credentials)
    ctx = SimpleNamespace(conn=conn, root=ROOT, generation=generation, args=args)
    try:
        if args.dry_run:
            return _dry_run(ctx)

        _ensure_generation(conn, generation)

        if args.invalidate_from:
            start = STAGE_NAMES.index(args.invalidate_from)
            targets = STAGE_NAMES[start:]
            with conn.cursor() as cursor:
                cursor.execute(
                    "UPDATE cardz_rebuild_checkpoint SET status='pending', input_sha256=NULL,"
                    " output_sha256=NULL, error_code=NULL"
                    f" WHERE generation_id=%s AND stage IN ({','.join(['%s'] * len(targets))})",
                    (generation, *targets),
                )
                invalidated = cursor.rowcount
            conn.commit()
            print(json.dumps({"invalidatedFrom": args.invalidate_from, "checkpointRows": invalidated}))
            return 0

        if args.stage:
            return _run_single_stage(ctx)

        return _run_linear(ctx)
    finally:
        conn.close()


def _run_single_stage(ctx: SimpleNamespace) -> int:
    args = ctx.args
    entry = next(item for item in ALL_STAGES if item[0] == args.stage)
    name, fn, _input_fn, always_run = entry
    if fn is None:
        raise SystemExit(f"stage '{name}' is not implemented in this build")
    if entry in POST_ACTIVATION_STAGES:
        _assert_activated(ctx)
        _run_freeze_proof()
    record = _checkpoints(ctx.conn, ctx.generation).get(name)
    status = record["status"] if record else "pending"
    if status == "failed" and not args.force_stage:
        raise SystemExit(f"stage '{name}' previously failed ({record.get('error_code')}); rerun needs --force-stage")
    if status == REBUILD_STAGE_COMPLETE and not always_run and not args.force_stage:
        raise SystemExit(f"stage '{name}' is already complete; rerun needs --force-stage")
    _run_stage(ctx, name, fn, forced=args.force_stage)
    return 0


def _run_linear(ctx: SimpleNamespace) -> int:
    args = ctx.args
    checkpoints = _checkpoints(ctx.conn, ctx.generation)
    for name, fn, input_fn, always_run in LINEAR_STAGES:
        record = checkpoints.get(name)
        status = record["status"] if record else "pending"
        if status == REBUILD_STAGE_COMPLETE and not always_run:
            if input_fn is not None and record.get("input_sha256"):
                current = input_fn(ctx)
                if current != record["input_sha256"]:
                    raise SystemExit(
                        f"ABORT: stage '{name}' input drifted (recorded"
                        f" {record['input_sha256'][:12]}.., now {current[:12]}..)."
                        f" Rerun with --invalidate-from {name} to invalidate it and downstream."
                    )
            print(json.dumps({"phase": "stage-skip", "stage": name, "status": "complete"}), flush=True)
            continue
        if status == "failed" and not args.force_stage:
            raise SystemExit(
                f"ABORT: stage '{name}' previously failed ({record.get('error_code')});"
                " rerun needs --force-stage"
            )
        if fn is None:
            print(
                json.dumps({"phase": "stop", "stage": name, "reason": "not implemented in this build"}),
                flush=True,
            )
            return 3
        _run_stage(ctx, name, fn, forced=False)
    print(json.dumps({"phase": "linear-complete", "through": LINEAR_STAGES[-1][0]}), flush=True)
    return 0


def _assert_activated(ctx: SimpleNamespace) -> None:
    with ctx.conn.cursor() as cursor:
        cursor.execute(
            "SELECT activated_at, activation_receipt_sha256 FROM cardz_rebuild_generation"
            " WHERE generation_id=%s",
            (ctx.generation,),
        )
        row = cursor.fetchone()
        if row is None or row["activated_at"] is None or not row["activation_receipt_sha256"]:
            raise SystemExit(
                f"ABORT: generation {ctx.generation} is not activated; post-activation stages refused"
            )
        cursor.execute(
            "SELECT passed FROM cardz_rebuild_validation_receipt"
            " WHERE generation_id=%s AND receipt_sha256=%s",
            (ctx.generation, row["activation_receipt_sha256"]),
        )
        receipt = cursor.fetchone()
        if receipt is None or int(receipt["passed"]) != 1:
            raise SystemExit("ABORT: activation receipt row missing or not passed")


def cmd_activate(args: argparse.Namespace) -> int:
    generation = args.generation
    if not GENERATION_RE.match(generation):
        raise SystemExit(f"--generation must match {GENERATION_RE.pattern}")
    _run_freeze_proof()
    credentials = args.credentials_env or DEFAULT_CREDENTIALS_ENV
    conn = connect(credentials)
    try:
        checkpoints = _checkpoints(conn, generation)
        incomplete = [
            name for name, _, _, _ in LINEAR_STAGES
            if (checkpoints.get(name) or {}).get("status") != REBUILD_STAGE_COMPLETE
        ]
        if incomplete:
            raise SystemExit(f"ABORT: stages not complete: {incomplete}")
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT passed FROM cardz_rebuild_validation_receipt"
                " WHERE generation_id=%s AND receipt_sha256=%s",
                (generation, args.receipt_sha256),
            )
            receipt = cursor.fetchone()
        if receipt is None or int(receipt["passed"]) != 1:
            raise SystemExit("ABORT: validation receipt missing or not passed for that sha")
        # §6.7: re-run the validator in-process and refuse unless the recomputed
        # receipt sha equals the CLI sha. The validator lands with S11.
        raise SystemExit("ABORT: S12 validator re-run is not implemented in this build; activation refused")
    finally:
        conn.close()


UNFREEZE_SQL = (
    "GRANT ALL PRIVILEGES ON `cardz\\_market\\_cap`.* TO 'cardz'@'%';\n"
    "DROP USER IF EXISTS 'cardz_rebuild'@'%';\n"
    "FLUSH PRIVILEGES;\n"
    "SHOW GRANTS FOR 'cardz'@'%';\n"
    "SELECT COUNT(*) AS rebuild_users FROM mysql.user WHERE user='cardz_rebuild';\n"
)


def cmd_unfreeze(args: argparse.Namespace) -> int:
    """Teardown from freeze-receipt-20260808.md: restore cardz's pre-freeze ALL
    grant and drop the short-lived rebuild user. Runs as container root; the
    password expands inside the container shell and never appears here."""
    if not args.confirm:
        raise SystemExit("rebuild-036-unfreeze is destructive to the freeze; pass --confirm")
    result = subprocess.run(
        [
            "docker", "exec", "-i", MYSQL_CONTAINER,
            "sh", "-lc", 'exec mysql -uroot -p"$MYSQL_ROOT_PASSWORD" --batch cardz_market_cap',
        ],
        input=UNFREEZE_SQL, capture_output=True, text=True,
    )
    stdout = result.stdout.strip()
    if result.returncode != 0:
        raise SystemExit(f"unfreeze failed (exit {result.returncode}): {result.stderr.strip()[:500]}")
    if "GRANT ALL PRIVILEGES ON `cardz\\_market\\_cap`.* TO `cardz`@`%`" not in stdout.replace('"', "`"):
        raise SystemExit(f"unfreeze verification failed: cardz grant not ALL. Output:\n{stdout}")
    if not re.search(r"rebuild_users\n0\b", stdout.replace("\r", "")):
        raise SystemExit(f"unfreeze verification failed: cardz_rebuild still exists. Output:\n{stdout}")
    print(json.dumps({"unfrozen": True, "cardzGrant": "ALL", "rebuildUserDropped": True}))
    return 0
