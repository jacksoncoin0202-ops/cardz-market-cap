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
import importlib
import inspect
import io
import json
import re
import socket
import subprocess
import sys
import time
from html import unescape as html_unescape
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Mapping

import pymysql

from identity_name import complete_collector_number, complete_collector_tail
from pc_sale_identity import pc_sale_fingerprint, pc_sale_price_text

ROOT = Path(__file__).resolve().parents[1]
GENERATION_RE = re.compile(r"^036_\d{8}T\d{6}Z$")
REBUILD_STAGE_COMPLETE = "complete"
DEFAULT_CREDENTIALS_ENV = ROOT / "data" / "runtime" / "config" / "rebuild.env"
# Nightly ops run as the long-lived runtime account: cardz_rebuild only exists
# inside a rebuild freeze window, so rebuild.env would fail at connect on any
# normal night. During a freeze the cardz account keeps SELECT but loses DML,
# which is exactly the inert-rollback behaviour cmd_daily_accept documents.
DAILY_CREDENTIALS_ENV = ROOT / "data" / "runtime" / "config" / "backend.env"
# Undated on purpose. This used to name the 2026-08-08 dump, so re-proving the
# backup meant writing a NEW file and leaving the gate reading the old one. The
# proof is a current fact about the database, not an archive entry; the dated
# 2026-08-08 file stays where it is as the Gate 0.2 receipt.
#
# To re-prove, take the dump and rewrite this file with its sha and its own
# `-- Dump completed on` line:
#   docker exec cardz-market-cap-db-1 sh -c 'mysqldump -u root
#     -p"$MYSQL_ROOT_PASSWORD" --single-transaction --routines --triggers
#     cardz_market_cap' > <dump>
RESTORE_PROOF = ROOT / "data" / "runtime" / "rebuild-036" / "restore-proof.json"
# Outside the repo: the dump is 1.5 GB and git has no business with it. Same
# directory the 2026-08-08 backup was taken into by hand.
BACKUP_DIR = Path.home() / "cardz-036-foundation-backup"
# Written by cmd_freeze, removed by cmd_unfreeze: which freeze window is open,
# stamped off the server's own clock. The restore proof names the window it was
# taken in, so S0 can tell "this dump covers the state you froze" from "this
# dump is from some earlier freeze". Without it the gate had only one question
# available -- is anything in the ledger newer than the dump -- and the answer
# is yes the moment the run's own first stage writes.
FREEZE_WINDOW = ROOT / "data" / "runtime" / "rebuild-036" / "freeze-window.json"
PRUNE_ALLOWLIST = ROOT / "data" / "policy" / "prune-order-allowlist.json"
FREEZE_PROOF_SCRIPT = ROOT / "scripts" / "prove_writer_freeze.py"
MYSQL_CONTAINER = "cardz-market-cap-db-1"
POLICY = {"minPop": 1000, "planVersion": "036"}
MAX_CURRENT_PRICE_AGE_DAYS = 30

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


def _stable_repr(value: Any) -> str:
    """repr() with the iteration order taken out.

    Plain repr() of a set or dict is hash-order dependent, and string hashing
    is seeded per process, so the same constant would fingerprint differently
    on every run and every stage would look drifted.
    """
    if isinstance(value, (set, frozenset)):
        return "{" + ", ".join(sorted(_stable_repr(item) for item in value)) + "}"
    if isinstance(value, dict):
        return "{" + ", ".join(
            f"{_stable_repr(key)}: {_stable_repr(val)}"
            for key, val in sorted(value.items(), key=lambda kv: repr(kv[0]))
        ) + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_stable_repr(item) for item in value) + "]"
    if isinstance(value, re.Pattern):
        return f"re:{value.pattern}"
    if isinstance(value, Path):
        return f"path:{value.as_posix()}"
    return repr(value)


def _referenced_globals(code: Any) -> set[str]:
    names = set(code.co_names)
    for const in code.co_consts:
        if isinstance(const, type(code)):
            names |= _referenced_globals(const)
    return names


_CODE_SHA_CACHE: dict[str, str] = {}


def _data_file_sha(path: Path) -> str | None:
    """A policy file's bytes are input, not decoration.

    data/policy/op-printed-codes.json decides which set codes a card may claim.
    Regenerating it for a new OP set changes the answer identity-resolve and
    bind produce without changing one byte of code, and hashing the Path
    recorded the name of the input instead of the input.
    """
    try:
        if path.is_file() and (ROOT / "data") in path.parents:
            return sha256_bytes(path.read_bytes())
    except OSError:
        return None
    return None


def _code_sha(*roots: Callable | None) -> str:
    """Fingerprint the code a stage actually runs.

    A checkpoint promises "same input, same result, so skip". Code is input.
    Both 2026-08-10 corrections -- reading the PSA population row instead of
    the cross-grader rollup, and dropping the printed collector code as a set
    signal -- landed as edits to helpers that identity-resolve and bind call.
    Because only table rows were hashed, the very next run printed stage-skip
    and shipped the old answer; the fix only landed because it was forced
    through by hand with --invalidate-from. That is a step that works exactly
    as long as somebody remembers it, so the hash learns about code instead.

    Scoped, not whole-file: walks the module-level names each root reaches,
    transitively, and hashes their source (functions, classes) or normalized
    repr (constants). Editing one lane must not demand a re-crawl of another.
    """
    key = "|".join(sorted(root.__name__ for root in roots if root is not None))
    cached = _CODE_SHA_CACHE.get(key)
    if cached is not None:
        return cached
    module = sys.modules[__name__]
    seen: set[str] = set()
    parts: list[str] = []
    queue = [root.__name__ for root in roots if root is not None]
    while queue:
        name = queue.pop()
        if name in seen:
            continue
        seen.add(name)
        obj = getattr(module, name, None)
        if obj is None:
            # A sibling pipelines module the stage reaches by a function-local
            # import. op_identity_rules imports this module at its top, so that
            # import cannot be hoisted, and getattr therefore answers None --
            # the entire One Piece rules module used to fingerprint as absent.
            # Editing printed_set_code moved nothing, and identity-resolve,
            # bind, pc-replay and snk-refresh all stage-skipped on the next run.
            sibling = ROOT / "pipelines" / f"{name}.py"
            if sibling.is_file():
                parts.append(f"{name}\n{sibling.read_text(encoding='utf-8')}")
                for attr, value in vars(importlib.import_module(name)).items():
                    if isinstance(value, Path):
                        digest = _data_file_sha(value)
                        if digest:
                            parts.append(f"{name}.{attr}#{digest}")
            continue
        if (inspect.isfunction(obj) or inspect.isclass(obj)) and getattr(obj, "__module__", None) == __name__:
            parts.append(f"{name}\n{inspect.getsource(obj)}")
            if inspect.isfunction(obj):
                queue.extend(_referenced_globals(obj.__code__))
        elif isinstance(obj, Path):
            digest = _data_file_sha(obj)
            parts.append(f"{name}={_stable_repr(obj)}" + (f"#{digest}" if digest else ""))
        elif isinstance(obj, (str, bytes, bool, int, float, tuple, list, dict, set, frozenset, re.Pattern)):
            parts.append(f"{name}={_stable_repr(obj)}")
    parts.sort()
    digest = sha256_bytes("\n".join(parts).encode("utf-8"))
    _CODE_SHA_CACHE[key] = digest
    return digest


def _stage_input_sha(name: str, ctx: SimpleNamespace) -> str | None:
    entry = next((item for item in ALL_STAGES if item[0] == name), None)
    if entry is None:
        return None
    _name, fn, input_fn, _always_run = entry
    if fn is None:
        return None
    return sha256_bytes(canonical_json({
        "data": input_fn(ctx) if input_fn is not None else None,
        "code": _code_sha(fn, input_fn),
    }))


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
    conn = pymysql.connect(
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
    # Docker Desktop's NAT proxy drops TCP mappings that stay silent for
    # minutes (long COUNT(*) on heavy views) — the client then hangs forever
    # in recv() on a half-open socket. Keepalive every 30s holds the mapping.
    sock = getattr(conn, "_sock", None)
    if sock is not None:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        if hasattr(socket, "SIO_KEEPALIVE_VALS"):  # Windows
            sock.ioctl(socket.SIO_KEEPALIVE_VALS, (1, 30_000, 10_000))
    # qualified_pool_operator.db() has asked this since the two-database episode;
    # the heaviest writer in the repo did not. A port number proves nothing --
    # the retired 3310 instance answered with the same schema name and the same
    # container-internal @@port -- so the marker tables are the identity test.
    _assert_canonical_db(conn, int(env.get("CARDZ_DB_PORT", "3308")))
    return conn


CANONICAL_DB_MARKER_TABLES = ("market_source_warehouse", "market_banned_source_policy")


def _assert_canonical_db(conn: pymysql.connections.Connection, port: int) -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT table_name FROM information_schema.tables"
            " WHERE table_schema = DATABASE() AND table_name IN (%s, %s)",
            CANONICAL_DB_MARKER_TABLES,
        )
        found = {str(next(iter(row.values()))) for row in cursor.fetchall()}
    missing = [name for name in CANONICAL_DB_MARKER_TABLES if name not in found]
    if missing:
        conn.close()
        raise SystemExit(
            f"ABORT: CARDZ_DB_PORT={port} is not the canonical New-Era database"
            f" (missing {', '.join(missing)}). Canonical is 3308."
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


def _dump_taken_at(proof: dict[str, Any], dump_file: Path) -> datetime:
    """When the backup was taken, read off the backup rather than the proof.

    The proof is a hand-written file and the time it claims decides whether a
    destructive stage may run, so the claim is checked against the bytes whose
    sha was just verified: mysqldump signs off with its own
    `-- Dump completed on ...` line and that line has to be in the file.

    Naive UTC. mysqldump writes the marker in the server's local time and the
    server runs UTC (`@@system_time_zone`, measured 2026-08-10), which is the
    same clock `UTC_TIMESTAMP(6)` stamps the checkpoint ledger with.
    """

    from datetime import datetime

    marker = str(proof.get("dumpCompletedMarker") or "")
    if not marker.startswith("-- Dump completed on "):
        raise SystemExit("S0 ABORT: restore proof has no dumpCompletedMarker")
    with dump_file.open("rb") as handle:
        handle.seek(max(0, dump_file.stat().st_size - 4096))
        tail = handle.read().decode("utf-8", "replace")
    if marker not in tail:
        raise SystemExit(
            f"S0 ABORT: the dump does not end with the proof's marker: {marker!r}"
        )
    stamp = " ".join(marker[len("-- Dump completed on "):].split())
    try:
        return datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        raise SystemExit(f"S0 ABORT: unreadable dump completion time: {stamp!r}")


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

    # Whole-task matching (name + path + actions): four known tasks run cardz
    # scripts without "cardz" in the task name (PC-FULL-900 shard cmds,
    # pc_s2_keepalive). schtasks CSV is unusable here: detached it emits the
    # OEM codepage AND localizes its column headers with the codepage, so any
    # header-keyed parse silently matches nothing. Get-ScheduledTask instead:
    # State is a .NET enum whose names are invariant English, and the console
    # output encoding is forced to UTF-8 explicitly.
    ps_script = (
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
        " Get-ScheduledTask | ForEach-Object { [pscustomobject]@{"
        " name=$_.TaskName; path=$_.TaskPath; state=[string]$_.State;"
        " actions=(($_.Actions | ForEach-Object {"
        " \"$($_.Execute) $($_.Arguments) $($_.WorkingDirectory)\" }) -join ' ')"
        " } } | ConvertTo-Json -Compress"
    )
    tasks_query = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
        capture_output=True,
    )
    if tasks_query.returncode != 0:
        raise SystemExit("S0 ABORT: Get-ScheduledTask query failed")
    try:
        task_rows = json.loads((tasks_query.stdout or b"").decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SystemExit(f"S0 ABORT: scheduled-task JSON unreadable: {error}")
    if isinstance(task_rows, dict):
        task_rows = [task_rows]
    enabled = []
    matched_names: set[str] = set()
    for row in task_rows:
        blob = " ".join(str(row.get(key) or "") for key in ("name", "path", "actions"))
        if "cardz" not in blob.lower():
            continue
        name = f"{row.get('path') or ''}{row.get('name') or '?'}"
        matched_names.add(name)
        state = str(row.get("state") or "").strip().lower()
        if state != "disabled":
            enabled.append(f"{name}={state or 'unknown'}")
    if enabled:
        raise SystemExit(f"S0 ABORT: cardz-linked scheduled tasks not Disabled: {sorted(set(enabled))}")
    # Fail closed on the match count too: Gate 0.3 receipt proved exactly 14
    # cardz-linked tasks exist, so seeing fewer means the scan itself is broken
    # (encoding, output shape), not that the tasks disappeared.
    if len(matched_names) < 14:
        raise SystemExit(
            f"S0 ABORT: cardz task scan matched only {len(matched_names)} tasks"
            " (Gate 0.3 baseline is 14); scheduler proof is not trustworthy"
        )
    counts["cardz_tasks_all_disabled"] = len(matched_names)

    proof = json.loads(RESTORE_PROOF.read_text(encoding="utf-8"))
    dump_file = Path(proof["dumpFile"])
    if not dump_file.is_file():
        raise SystemExit(f"S0 ABORT: backup dump missing: {dump_file}")
    actual = sha256_file(dump_file)
    if actual != proof["dumpSha256"]:
        raise SystemExit("S0 ABORT: backup dump sha mismatch against restore proof")
    counts["backup_sha_verified"] = True

    # A matching sha proves the file has not been modified, which is not the
    # same fact as it being a restore point. Measured 2026-08-10: the dump this
    # gate was protecting completed 2026-08-08 07:58 UTC while the newest row
    # in the checkpoint ledger was 15:26 UTC two days later -- restoring it
    # would have discarded every binding, every prune and two days of
    # collection, and the gate still answered `backup_sha_verified`. What has
    # to hold is that nothing the database records postdates the backup of it.
    # mysqldump writes this line last, so a proof can only quote it off a dump
    # that finished. A truncated file with a matching sha is still not a place
    # to go back to.
    taken_at = _dump_taken_at(proof, dump_file)

    # And the dump has to belong to the freeze that is open right now. A full
    # dump contains everything up to the moment it completed, so the only
    # question a restore point raises is what restoring it would throw away:
    # taken inside this window, the answer is this run's own work, which is
    # exactly what you would be undoing.
    #
    # Measured 2026-08-10: the dump this gate was protecting completed
    # 2026-08-08 07:58 UTC, two days and a full night of collection before the
    # freeze it was offered against, and the gate answered backup_sha_verified.
    # Asking instead whether the ledger held anything newer than the dump was
    # the first fix and it was unsatisfiable: a run's own stages write, so at
    # 17:34Z it stopped prune-apply and cited the activation prune-apply was
    # there to follow.
    window = json.loads(FREEZE_WINDOW.read_text(encoding="utf-8")) if FREEZE_WINDOW.is_file() else {}
    if not window.get("openedAt"):
        raise SystemExit(
            "S0 ABORT: no freeze window is open, so nothing here is a restore"
            f" point. Freeze first; {FREEZE_WINDOW.name} is written by the freeze."
        )
    if proof.get("freezeOpenedAt") != window["openedAt"]:
        raise SystemExit(
            f"S0 ABORT: the restore proof was taken in the freeze window opened"
            f" {proof.get('freezeOpenedAt')}, and the window open now was opened"
            f" {window['openedAt']}. Whatever ran between them would be lost."
            f" Take a fresh dump inside this window."
        )
    counts["backup_taken_at"] = f"{taken_at.isoformat()}Z"
    counts["freeze_opened_at"] = f"{window['openedAt']}Z"

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


def _parse_capture_observation(
    cards_dir: Path, gid: str, generation: str,
) -> tuple[dict[str, Any] | None, str]:
    """Deterministically re-parse one captured card page into a v2 row.

    Returns (row, "") or (None, skip_reason). Only rawStatus=='captured' with a
    sha-verified raw payload may land (§3.11f); dom_evidence_only has no
    replayable raw file and is skipped as still-pending.
    """

    card_dir = cards_dir / gid
    normalized_path = card_dir / "card_details.json"
    receipt_path = card_dir / "card_details.raw.receipt.json"
    if not normalized_path.is_file() or not receipt_path.is_file():
        return None, "no_capture"
    try:
        normalized = json.loads(normalized_path.read_text(encoding="utf-8"))
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, "unreadable_capture"
    if normalized.get("privateSourceReceipt") != receipt:
        return None, "receipt_mismatch"
    if receipt.get("rawStatus") != "captured":
        return None, "dom_evidence_only"
    pointer = receipt.get("sourcePointer")
    digest = str(receipt.get("contentSha256") or "")
    if not isinstance(pointer, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        return None, "receipt_incomplete"
    raw_path = (card_dir / pointer).resolve()
    try:
        if card_dir.resolve() not in raw_path.parents:
            return None, "raw_path_escape"
        if sha256_file(raw_path) != digest:
            return None, "raw_sha_mismatch"
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, "raw_unreadable"
    if normalized.get("gemrate_id") != gid:
        return None, "gid_mismatch"
    psa_row = next(
        (
            row for row in normalized.get("population_data") or []
            if isinstance(row, dict) and row.get("grader") == "psa"
        ),
        None,
    )
    if psa_row is None:
        return None, "no_psa_row"
    grades = psa_row.get("grades")
    g10 = grades.get("g10") if isinstance(grades, dict) else None
    if not isinstance(g10, int) or g10 < 0:
        return None, "no_psa10_population"
    # PSA's own total for this card, read from the raw row rather than added up
    # from the normalized grade dict. That dict is not a breakdown: both live
    # normalizers reduce the PSA row to {"g10": N}, so the old
    # `sum(grades.values()) if len(grades) > 1` wrote NULL for 16,754 of the
    # 16,834 captures, and for the 80 written by an older normalizer that kept
    # a full breakdown it understated 59 of them by up to 189 -- half grades,
    # qualifiers and the auto_/non_auto_ buckets are sibling fields on the PSA
    # row, not keys in the dict. card_total_grades is present on all 16,834 raw
    # PSA rows and is PSA-scoped, so it does not reintroduce the cross-grader
    # rollup (gid 0d446fc8…: sum 27,356, card_total_grades 27,545, Universal
    # rollup 33,683).
    raw_psa_row = next(
        (
            row for row in raw.get("population_data") or []
            if isinstance(row, dict) and str(row.get("grader") or "").lower() == "psa"
        ),
        None,
    )
    total = (raw_psa_row or {}).get("card_total_grades")
    if not isinstance(total, int) or total < 0:
        total = None
    fetched_at = str(receipt.get("fetchedAt") or "")
    if not fetched_at:
        return None, "no_fetched_at"
    page = normalized.get("publicCardPage") or {}
    observed = str(
        normalized.get("date") or page.get("sourceDate") or fetched_at[:10]
    )[:10]
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", observed):
        return None, "bad_observed_date"
    return {
        "gemrate_id": gid,
        "psa10_population": g10,
        "total_population": total,
        "effective_at": fetched_at.replace("T", " ").replace("Z", ""),
        "observed_date": observed,
        "capture_path": f"data/private/gemrate/cards/{gid}/{pointer}",
        "raw_payload_sha256": digest,
        "psa_row_sha256": sha256_bytes(canonical_json(psa_row)),
        "generation_id": generation,
    }, ""


def stage_popland(ctx: SimpleNamespace) -> dict[str, Any]:
    """S3 (§6.1/§7.2): deterministic re-parse of captures into the v2 table.

    One transaction end to end: the market_ingest_run row, every observation
    upsert, and the run completion update commit together, so a crash leaves
    neither a stale 'running' run (S0 gate) nor half a landing.
    """

    from datetime import datetime, timezone

    import gemrate_source

    run_dir = gemrate_source.OUT_DIR / "runs" / ctx.generation
    worklist_path = run_dir / "worklist.txt"
    if not worklist_path.is_file():
        raise SystemExit(f"S3 ABORT: worklist missing: {worklist_path}")
    worklist = [
        line.strip() for line in worklist_path.read_text(encoding="utf-8").splitlines()
        if re.fullmatch(r"[0-9a-f]{40}", line.strip())
    ]
    if not worklist:
        raise SystemExit("S3 ABORT: worklist empty")

    rows: list[dict[str, Any]] = []
    skip_reasons: dict[str, int] = {}
    for gid in worklist:
        row, reason = _parse_capture_observation(
            gemrate_source.CARDS_DIR, gid, ctx.generation,
        )
        if row is None:
            skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
            continue
        rows.append(row)
    if not rows:
        raise SystemExit("S3 ABORT: zero landable captures")

    landed_digest = sha256_bytes(canonical_json([
        [row["gemrate_id"], row["observed_date"], row["psa10_population"],
         row["raw_payload_sha256"], row["psa_row_sha256"]]
        for row in sorted(rows, key=lambda r: (r["gemrate_id"], r["observed_date"]))
    ]))
    now = datetime.now(timezone.utc)
    run_key = sha256_bytes(canonical_json(
        ["rebuild036-pop-land", ctx.generation, now.strftime("%Y%m%dT%H%M%S%fZ")]
    ))
    conn = ctx.conn
    conn.rollback()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO market_ingest_run (run_key, source_code, ingest_mode,"
                " effective_at, payload_sha256, manifest_sha256, status,"
                " observed_count, started_at)"
                " VALUES (%s, 'gemrate', 'rebuild', UTC_TIMESTAMP(6), %s, %s,"
                " 'running', %s, UTC_TIMESTAMP(6))",
                (run_key, landed_digest, sha256_file(worklist_path), len(rows)),
            )
            run_id = cursor.lastrowid
            insert_sql = (
                "INSERT INTO market_gemrate_psa10_observation_v2 (run_id, gemrate_id,"
                " variant_id, psa10_population, total_population, effective_at,"
                " observed_date, capture_path, raw_payload_sha256, psa_row_sha256,"
                " generation_id)"
                " VALUES (%s, %s, NULL, %s, %s, %s, %s, %s, %s, %s, %s)"
                " ON DUPLICATE KEY UPDATE"
                " run_id=VALUES(run_id), psa10_population=VALUES(psa10_population),"
                " total_population=VALUES(total_population),"
                " effective_at=VALUES(effective_at), capture_path=VALUES(capture_path),"
                " raw_payload_sha256=VALUES(raw_payload_sha256),"
                " psa_row_sha256=VALUES(psa_row_sha256),"
                " generation_id=VALUES(generation_id)"
            )
            for start in range(0, len(rows), 1000):
                batch = rows[start:start + 1000]
                cursor.executemany(insert_sql, [
                    (
                        run_id, row["gemrate_id"], row["psa10_population"],
                        row["total_population"], row["effective_at"],
                        row["observed_date"], row["capture_path"],
                        row["raw_payload_sha256"], row["psa_row_sha256"],
                        row["generation_id"],
                    )
                    for row in batch
                ])
            cursor.execute(
                "UPDATE market_ingest_run SET status='complete',"
                " accepted_count=%s, completed_at=UTC_TIMESTAMP(6) WHERE id=%s",
                (len(rows), run_id),
            )
            cursor.execute(
                "SELECT COUNT(*) AS n, COUNT(DISTINCT gemrate_id) AS g"
                " FROM market_gemrate_psa10_observation_v2 WHERE generation_id=%s",
                (ctx.generation,),
            )
            after = cursor.fetchone()
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    counts = {
        "worklist": len(worklist),
        "landed": len(rows),
        "skipped": skip_reasons,
        "runId": run_id,
        "tableRowsThisGeneration": int(after["n"]),
        "tableDistinctIdsThisGeneration": int(after["g"]),
    }
    return {
        "input_sha256": sha256_file(worklist_path),
        "output_sha256": landed_digest,
        "counts": counts,
    }


def _popland_input_sha(ctx: SimpleNamespace) -> str:
    """The captures the stage parses, not the ids it iterates over.

    This used to hash runs/<generation>/worklist.txt, a file of 40-hex ids
    written once by discover. The capture store those ids name is shared and
    generation-independent: `gemrate_source.py daily` promotes fresh
    card_details.json and receipts into the same CARDS_DIR, and a `daily_*` run
    can never become a generation (GENERATION_RE), so the runbook's gap-intake
    path reuses the active one. Populations moved and dom_evidence_only ids
    became captured while the recorded sha sat still, and pop-land printed
    stage-skip over the new data.

    rawStatus and contentSha256 are what the parse branches on. fetchedAt is
    deliberately left out: a refetch that returns the same page is not new
    input, and hashing it would re-run every downstream lane nightly.
    """

    import gemrate_source

    worklist_path = gemrate_source.OUT_DIR / "runs" / ctx.generation / "worklist.txt"
    if not worklist_path.is_file():
        return "worklist-missing"
    gids = [
        line.strip()
        for line in worklist_path.read_text(encoding="utf-8").splitlines()
        if re.fullmatch(r"[0-9a-f]{40}", line.strip())
    ]
    captures: list[list[str]] = []
    for gid in gids:
        receipt_path = gemrate_source.CARDS_DIR / gid / "card_details.raw.receipt.json"
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            captures.append([gid, "", ""])
            continue
        captures.append([
            gid,
            str(receipt.get("rawStatus") or ""),
            str(receipt.get("contentSha256") or ""),
        ])
    return sha256_bytes(canonical_json(sorted(captures)))


def _capture_fingerprint(cards_dir: Path, gid: str) -> tuple[dict[str, Any] | None, str]:
    """§6.2 identity fingerprint from the settled capture (raw payload + slug).

    Fails closed: any missing/unverifiable piece returns (None, reason) and the
    id becomes identity_pending instead of being guessed at.
    """

    card_dir = cards_dir / gid
    try:
        normalized = json.loads((card_dir / "card_details.json").read_text(encoding="utf-8"))
        receipt = json.loads((card_dir / "card_details.raw.receipt.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, "no_capture"
    if receipt.get("rawStatus") != "captured":
        return None, "dom_evidence_only"
    pointer = receipt.get("sourcePointer")
    if not isinstance(pointer, str):
        return None, "receipt_incomplete"
    try:
        raw = json.loads((card_dir / pointer).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, "raw_unreadable"
    page = normalized.get("publicCardPage") or {}
    canonical_url = str(page.get("canonicalUrl") or "")
    url_parts = [part for part in canonical_url.split("/") if part]
    settled_id = ""
    slug = ""
    if "card" in url_parts:
        card_index = url_parts.index("card")
        if len(url_parts) > card_index + 1:
            settled_id = url_parts[card_index + 1]
        if len(url_parts) > card_index + 2:
            slug = url_parts[card_index + 2]
    psa_rows = [
        row for row in raw.get("population_data") or []
        if isinstance(row, dict) and str(row.get("grader") or "").lower() == "psa"
    ]
    set_name = str(raw.get("set_name") or "")
    language = _language_from_set_name(set_name) or "en"
    # The top-level payload is GemRate's cross-grader "Universal" rollup, and its
    # description is generated from the merged view rather than copied from any
    # one grader: it renders "Sword & Shield" as "Sword and Shield", drops the
    # set code ("One Piece OP05-Awakening of the New Era" arrives as "One Piece
    # Awakening of the New Era"), and contradicts the payload's own parallel
    # field. The PSA row inside population_data carries the label PSA prints.
    # It reproduces all 751 rows of the 034 PSA acceptance authority; the rollup
    # reproduces 588. The two disagree on 4,459 of 16,834 captures, so this is
    # the name for a quarter of the shelf. No PSA row means no PSA name, and
    # psaRowCount already sends that id to identity_pending.
    description = str((psa_rows[0]["description"] if psa_rows else "") or "").strip()
    # The same argument as `description`, for the set. `setName` above is the
    # rollup's wording and stays the fingerprint's own claim; this is the label
    # PSA prints, and it is what the catalog's set_name is restated from in S5.
    # 1213 of the 1605 bound variants carry a set_name the PSA row disagrees
    # with -- hand-typed strings ('SVP EN "Van gogh exhibition"'), all-lowercase
    # rows, and rollup spellings ('Sword and Shield' for 'Sword & Shield') --
    # and _fingerprint_variant_conflicts compares provider set text against
    # that column, so the junk read as a set conflict. Measured 2026-08-11:
    # restating clears variant 1's PriceCharting hold and newly conflicts none
    # of the 997 replayable exact PC bindings.
    psa_set_name = str((psa_rows[0]["set_name"] if psa_rows else "") or "").strip()
    # The number PSA prints is the bare numerator ("110"), and the rollup copies
    # it, so `cardNumber` alone can never render "110/80". The denominator is
    # already sitting in the same payload: CGC/SGC/Beckett rows for the same
    # physical card carry the full form. Measured 2026-08-11: 1169 of 1605 bound
    # variants have no denominator, and 1108 of those are recoverable from a
    # sibling grader row with zero new scraping.
    #
    # This is a second, wider field on purpose. `cardNumber` feeds
    # _derive_print_fields -> _gemrate_printing_sha, and every printing_sha input
    # is frozen (see the S5 comment at the set_name restate). `cardNumberFull` is
    # display-only: it lands in catalog_variant.collector_number, which is not in
    # the hash, and _collector_core() strips the denominator before any identity
    # comparison, so widening it moves no match.
    psa_number = str((psa_rows[0].get("card_number") if psa_rows else "") or "")
    card_number_full = complete_collector_number(
        psa_number or raw.get("card_number"), raw.get("population_data")
    )
    return {
        "gemrateId": gid,
        "description": description,
        "name": str(raw.get("name") or ""),
        "year": str(raw.get("year") or ""),
        "setName": set_name,
        "psaSetName": psa_set_name,
        "cardNumber": str(raw.get("card_number") or ""),
        "cardNumberFull": card_number_full,
        "parallel": str(raw.get("parallel") or ""),
        "category": str(raw.get("category") or ""),
        "derivedLanguage": language,
        "canonicalUrl": canonical_url,
        "settledId": settled_id,
        "slug": slug,
        "psaRowCount": len(psa_rows),
        "rawSha256": str(receipt.get("contentSha256") or ""),
    }, ""


def _language_from_set_name(set_name: str) -> str:
    """Explicit language wording in a set name; '' when the name says nothing.

    "chinese" without a script stays "zh": ambiguous, downstream fails closed.
    """

    lowered = str(set_name or "").lower()
    if "simplified chinese" in lowered:
        return "zhCN"
    if "traditional chinese" in lowered:
        return "zhTW"
    if "japanese" in lowered:
        return "ja"
    if "chinese" in lowered:
        return "zh"
    if "korean" in lowered:
        return "ko"
    return ""


def _norm_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def _collector_core(value: str) -> str:
    """Comparable core of a collector number across vocabularies.

    GemRate stores the bare printed number ("016", "236"); the catalog stores
    display forms with a set prefix and/or denominator ("OP01-016", "236/187").
    Core = last dash segment, denominator dropped, numeric leading zeros folded.
    """

    text = _norm_text(value).replace(" ", "")
    if not text or text == "unknown":  # catalog placeholder, not a number
        return ""
    text = text.split("/", 1)[0]
    if "-" in text:
        text = text.rsplit("-", 1)[1]
    return str(int(text)) if text.isdigit() else text


_SET_CODE_RE = re.compile(r"\b(op\d{2}|eb\d{2}|st\d{2}|prb\d{2}|sv\d+[a-z]?|s\d+[a-z]?|swsh\d+|sm\d+[a-z]?|xy\d+[a-z]?)\b")
_ERA_PHRASES = (
    "scarlet and violet", "sword and shield", "sun and moon", "mega evolution",
    "black and white", "diamond and pearl", "heartgold and soulsilver",
)
_NOISE_TOKENS = {
    "pokemon", "one", "piece", "japanese", "japan", "english", "en", "jp", "ja",
    "booster", "pack", "the", "of", "and", "a", "an",
    # The abbreviation half of _ERA_PHRASES. GemRate writes the era short
    # ("Pokemon SM Black Star Promo") where the catalog writes it long ("2019
    # Sun and Moon Black Star Promo Power Partnership Tins"); the phrases above
    # already erase the long form, so leaving the short one as a signal made an
    # era the two sides spell differently look like two different sets. Only
    # the bare series marker -- _SET_CODE_RE keeps every numbered form (sm12a,
    # sv8a) as a code, and codes are compared separately.
    "sm", "swsh", "sv", "xy", "bw", "dp", "hgss",
}


def _set_signals(set_name: str) -> tuple[set[str], set[str]]:
    """(set codes, meaningful name tokens) a SET NAME exposes for comparison.

    A collector number used to be a second source of codes here. It is not a
    set signal: the number is printed on the card and names the set the card
    first appeared in, which for a promo or a reprint is a different product
    from the one the entity is -- the OP07 booster Luffy #109 bound onto the
    Promos lottery prize exactly that way. The conflict check stopped passing
    one in, but the parameter survived and _gemrate_bind_evidence still did,
    so 197 of 16,835 captures minted providerClaims.setCode out of a card
    number ('Pokemon Sword & Shield Shining Fates' + 'SV007' -> 'sv007', a
    code no provider ever claimed). Removing the parameter is what makes the
    inference unrepresentable rather than merely unused.
    """

    text = _norm_text(str(set_name or "").replace("&", " and "))
    codes = set(_SET_CODE_RE.findall(text))
    # GemRate's "<franchise> <CODE> <LANG>-<Name>" prefix: code is already
    # harvested above; strip it so the code token never pollutes name tokens.
    text = re.sub(r"\b([a-z0-9]{2,6})\s+(en|jp|ja)-", " ", text)
    base_text = text

    def _tok(value: str) -> set[str]:
        return {
            "promo" if token == "p" else token
            for token in re.split(r"[^a-z0-9]+", value)
            if token and token not in _NOISE_TOKENS and token not in codes
        }

    for phrase in _ERA_PHRASES:
        text = text.replace(phrase, " ")
    tokens = _tok(text)
    if not tokens:
        # A set name that is nothing but an era phrase ("Mega Evolution")
        # must still expose its words: emptying the signal here lets any
        # fingerprint sail through the set comparison unopposed — a Classic
        # deck Venusaur was adopted into the Mega Evolution set this way.
        tokens = _tok(base_text)
    return codes, tokens


def _fingerprint_variant_conflicts(
    fp: Mapping[str, Any], variant: Mapping[str, Any],
) -> list[str]:
    """Hard identity conflicts between a discovery fingerprint and a variant.

    Signal-level comparison, not string equality: the two vocabularies spell
    the same identity differently (era names vs set codes, bare vs prefixed
    collector numbers), so each signal is normalised to what both sides can
    express before being allowed to conflict. Parallel/printing wording stays
    soft evidence in detail_json.
    """

    conflicts: list[str] = []
    v_number = _collector_core(variant.get("collector_number") or "")
    f_number = _collector_core(fp.get("cardNumber") or "")
    if v_number and f_number and v_number != f_number:
        conflicts.append(f"collector_number:{f_number}!={v_number}")
    v_lang = str(variant.get("card_language") or "")
    if not v_lang:
        # Catalog rows minted before the language column was enforced carry
        # the language only in the set name ("One Piece Japanese OP11-…");
        # without this an en fingerprint sails into a ja variant unopposed.
        v_lang = _language_from_set_name(variant.get("set_name") or "")
    f_lang = str(fp.get("derivedLanguage") or "")
    if v_lang and f_lang:
        same = v_lang == f_lang or (v_lang.startswith("zh") and f_lang.startswith("zh"))
        if not same:
            conflicts.append(f"language:{f_lang}!={v_lang}")
    # Set names only. A collector number's prefix is the code PRINTED on the
    # card, which names the set the card first appeared in -- not the set the
    # entity is in. Reading it as a set signal is what bound the OP07 booster
    # Luffy #109 (pop 1919) onto the Promos serialized lottery prize (pop 301):
    # the variant's own set_name was the junk string 'Opened', its collector
    # number said OP07-109, the codes agreed, and the short-circuit below meant
    # the set names 'Promos' and '500 Years in the Future' were never compared.
    # op_identity_rules below is the proved path for a printed code and stays.
    # Replayed over all 1605 exact bindings: 32 stale conflicts clear, and the
    # only two that newly conflict are the two whose set_name is not a set name.
    f_codes, f_tokens = _set_signals(fp.get("setName") or "")
    v_codes, v_tokens = _set_signals(variant.get("set_name") or "")
    # Both codes a One Piece card can honestly answer with. _set_signals reads
    # the set GemRate SOLD it in; the card prints the set it FIRST appeared in,
    # and 88 of the 121 cards in the 2026-08-10 gap differ. A provider page for
    # the alternate-art Kaido out of the OP05 booster says op04 because the
    # card says op04, and every lane refused it here for printing the truth.
    # Only ever additive: printed_set_code returns a code proved off the
    # Limitless page for the product GemRate itself named, and only for the
    # cards a resolver could prove one for -- "" for everything else.
    import op_identity_rules  # deferred: it imports this module at its top

    # Both directions, because a reprint has two true answers and the catalog
    # carries exactly one of them: 48 rows had a blank set_code that the printed
    # code filled, and 21 already carried the printed code and needed the other
    # one -- GemRate said op10, catalog said op08, adding OP08 again changed
    # nothing (variants 19, 1214, 1228, measured 2026-08-10).
    vid = variant.get("variant_id") or variant.get("id")
    for code in (
        op_identity_rules.printed_set_code(vid),
        op_identity_rules.sold_in_set_code(vid),
    ):
        if code:
            v_codes = v_codes | {code.casefold()}
    if f_codes and v_codes and not (f_codes & v_codes):
        conflicts.append(f"set_code:{sorted(f_codes)}!={sorted(v_codes)}")

    def covered(small: set[str], large: set[str]) -> bool:
        # Prefix-tolerant token match absorbs catalog spelling drift
        # ("paldea" vs "paldean") without merging genuinely different words.
        return all(
            any(
                a == b or (min(len(a), len(b)) >= 4
                           and (a.startswith(b) or b.startswith(a)))
                for b in large
            )
            for a in small
        )

    # The set-code agreement above used to end the comparison here. That was
    # the same short-circuit that bound the OP07 booster Luffy onto the Promos
    # lottery prize, and dropping collector_number as a code source did not
    # retire it -- op_identity_rules re-supplies the printed code from the
    # policy file, and for a promo the printed code names the BOOSTER the card
    # first appeared in, not the product the entity is. Replayed 2026-08-11 over
    # every exact binding rebuilt from its own capture: 0 of 1605 gemrate change,
    # and 10 of 984 PriceCharting newly conflict -- 9 promo/collection variants
    # bound to a booster's page (1876, 1915, 1962, 1974, 2034, 2077, 2146, 2153,
    # 2172; v2077 was publishing a Gift Collection promo at the Romance Dawn
    # booster's price) plus v1427, the one genuine OP08/OP02 reprint whose second
    # true code belongs in its own set_code column.
    if f_tokens and v_tokens and not (
        covered(f_tokens, v_tokens) or covered(v_tokens, f_tokens)
    ):
        conflicts.append(f"set:{sorted(f_tokens)}!={sorted(v_tokens)}")
    return conflicts


def stage_identity_resolve(ctx: SimpleNamespace) -> dict[str, Any]:
    """S4 (§6.2/§6.3): decide cohorts and incidents; write bookkeeping only.

    This stage never writes catalog_variant / bindings — S5 executes decisions.
    Unresolvable ambiguity fails closed as identity_pending (blocks activation)
    instead of stopping to ask; every automated ruling lands as a closed
    incident row so the audit trail survives.
    """

    from datetime import datetime, timezone

    import gemrate_source

    conn = ctx.conn
    conn.rollback()
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT gemrate_id, psa10_population, observed_date, effective_at"
            " FROM operator_latest_gemrate_psa10"
        )
        latest = {row["gemrate_id"]: row for row in cursor.fetchall()}
        cursor.execute(
            "SELECT gemrate_id, MAX(psa10_population) AS peak"
            " FROM market_gemrate_psa10_observation_v2 GROUP BY gemrate_id"
        )
        peaks = {row["gemrate_id"]: int(row["peak"]) for row in cursor.fetchall()}
        cursor.execute(
            "SELECT external_entity_id AS gemrate_id, variant_id, match_status"
            " FROM catalog_source_identity WHERE source_code='gemrate'"
        )
        bindings = {row["gemrate_id"]: row for row in cursor.fetchall()}
        cursor.execute(
            "SELECT v.id, v.opaque_id, v.tcg_code, v.card_language, v.canonical_name,"
            " v.set_name, v.collector_number, v.identity_status,"
            " p.parallel_code, p.printing_code, p.canonical_printing_sha256"
            " FROM catalog_variant v"
            " LEFT JOIN catalog_printing_identity p ON p.variant_id = v.id"
        )
        variants = {int(row["id"]): row for row in cursor.fetchall()}
    variant_gids: dict[int, list[str]] = {}
    for gid, row in bindings.items():
        # A rejected binding is a recorded decision, not a live claim on the
        # variant: counting it here manufactures phantom mixed-printings
        # incidents that S5 (which only rules over live bindings) can never
        # close, so they stay open across every recompute.
        if row["match_status"] == "rejected":
            continue
        variant_gids.setdefault(int(row["variant_id"]), []).append(gid)

    min_pop = int(POLICY["minPop"])
    members: list[dict[str, Any]] = []
    incidents: list[dict[str, Any]] = []
    fingerprint_failures: dict[str, int] = {}
    for gid, obs in sorted(latest.items()):
        pop = int(obs["psa10_population"])
        fp, fp_reason = _capture_fingerprint(gemrate_source.CARDS_DIR, gid)
        pending_reasons: list[str] = []
        detail: dict[str, Any] = {"latestPop": pop, "observedDate": str(obs["observed_date"])}
        if fp is None:
            pending_reasons.append(f"fingerprint:{fp_reason}")
            fingerprint_failures[fp_reason] = fingerprint_failures.get(fp_reason, 0) + 1
        else:
            detail["fingerprint"] = fp
            if fp["psaRowCount"] != 1:
                pending_reasons.append(f"psa_rows:{fp['psaRowCount']}")
            if fp["settledId"] and fp["settledId"] != gid:
                pending_reasons.append("resettled")
                incidents.append({
                    "gemrate_id": gid, "variant_id": None,
                    "incident_kind": "requested_id_resettled",
                    "detail": {"settledId": fp["settledId"], "canonicalUrl": fp["canonicalUrl"]},
                    "resolution": None,
                })
        peak = peaks.get(gid, pop)
        if pop < peak:
            pending_reasons.append(f"pop_decrease:{peak}->{pop}")
            incidents.append({
                "gemrate_id": gid, "variant_id": None,
                "incident_kind": "pop_decrease_same_entity",
                "detail": {"peak": peak, "latest": pop},
                "resolution": None,
            })
        binding = bindings.get(gid)
        variant_id = int(binding["variant_id"]) if binding else None
        if binding and fp is not None:
            variant = variants.get(variant_id) or {}
            conflicts = _fingerprint_variant_conflicts(fp, variant)
            # 'accepted_binding_moved' is an incident about a LIVE binding
            # drifting. A rejected row is not live, and S5 has no path that
            # closes such an incident, so raising one every recompute would
            # accumulate noise nobody can clear. This is a statement about the
            # incident's meaning, not about the rejection being trustworthy --
            # most rejected rows are collateral quarantine that examined
            # nothing (see REJECTION_VERDICT_ACTIONS).
            if conflicts and binding["match_status"] != "rejected":
                pending_reasons.append("binding_conflict")
                incidents.append({
                    "gemrate_id": gid, "variant_id": variant_id,
                    "incident_kind": "accepted_binding_moved",
                    "detail": {"conflicts": conflicts, "matchStatus": binding["match_status"]},
                    "resolution": None,
                })
            detail["binding"] = {
                "variantId": variant_id,
                "matchStatus": binding["match_status"],
                "conflicts": conflicts,
            }
        cohort = "qualified_identity" if pop >= min_pop else "non_qualified"
        members.append({
            "gemrate_id": gid,
            "variant_id": variant_id,
            "pop": pop,
            "cohort": cohort,
            "identity_pending": 1 if (pending_reasons and cohort != "non_qualified") else 0,
            "detail": {**detail, "pendingReasons": pending_reasons},
        })

    # Variants carrying multiple current gemrate bindings (the 17 collapsed
    # cases): mixed printings unless every bound fingerprint agrees.
    for variant_id, gids in sorted(variant_gids.items()):
        if len(gids) < 2:
            continue
        prints = set()
        for gid in gids:
            fp, _ = _capture_fingerprint(gemrate_source.CARDS_DIR, gid)
            if fp is None:
                prints.add(f"unknown:{gid}")
            else:
                prints.add(_norm_text(
                    f"{fp['setName']}|{fp['cardNumber']}|{fp['parallel']}|{fp['derivedLanguage']}"
                ))
        if len(prints) > 1:
            for gid in gids:
                incidents.append({
                    "gemrate_id": gid, "variant_id": variant_id,
                    "incident_kind": "variant_mixed_printings",
                    "detail": {"boundIds": sorted(gids), "distinctPrints": sorted(prints)},
                    "resolution": None,
                })

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")
    member_digest = sha256_bytes(canonical_json([
        [m["gemrate_id"], m["cohort"], m["identity_pending"], m["pop"], m["variant_id"]]
        for m in members
    ]))
    try:
        with conn.cursor() as cursor:
            for start in range(0, len(members), 500):
                batch = members[start:start + 500]
                cursor.executemany(
                    "INSERT INTO catalog_rebuild_member (generation_id, gemrate_id,"
                    " variant_id, latest_psa10_population, cohort, identity_pending,"
                    " detail_json, computed_at)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
                    " ON DUPLICATE KEY UPDATE variant_id=VALUES(variant_id),"
                    " latest_psa10_population=VALUES(latest_psa10_population),"
                    " cohort=VALUES(cohort), identity_pending=VALUES(identity_pending),"
                    " detail_json=VALUES(detail_json), computed_at=VALUES(computed_at)",
                    [
                        (
                            ctx.generation, m["gemrate_id"], m["variant_id"], m["pop"],
                            m["cohort"], m["identity_pending"],
                            json.dumps(m["detail"], ensure_ascii=False, sort_keys=True),
                            now,
                        )
                        for m in batch
                    ],
                )
            for incident in incidents:
                cursor.execute(
                    "INSERT INTO catalog_population_identity_incident (generation_id,"
                    " gemrate_id, variant_id, incident_kind, detail_json, opened_at,"
                    " resolved_at, resolution)"
                    " VALUES (%s, %s, %s, %s, %s, %s, NULL, NULL)"
                    " ON DUPLICATE KEY UPDATE detail_json=VALUES(detail_json),"
                    " variant_id=VALUES(variant_id), resolved_at=NULL, resolution=NULL",
                    (
                        ctx.generation, incident["gemrate_id"], incident["variant_id"],
                        incident["incident_kind"],
                        json.dumps(incident["detail"], ensure_ascii=False, sort_keys=True),
                        now,
                    ),
                )
            # Reconcile: an open incident this recompute no longer detects is
            # closed (not deleted) so the audit trail keeps the false alarm.
            detected = {(i["gemrate_id"], i["incident_kind"]) for i in incidents}
            cursor.execute(
                "SELECT gemrate_id, incident_kind FROM catalog_population_identity_incident"
                " WHERE generation_id=%s AND resolved_at IS NULL",
                (ctx.generation,),
            )
            stale = [
                (row["gemrate_id"], row["incident_kind"]) for row in cursor.fetchall()
                if (row["gemrate_id"], row["incident_kind"]) not in detected
            ]
            for gid, kind in stale:
                cursor.execute(
                    "UPDATE catalog_population_identity_incident SET resolved_at=%s,"
                    " resolution='condition_cleared_on_recompute'"
                    " WHERE generation_id=%s AND gemrate_id=%s AND incident_kind=%s"
                    " AND resolved_at IS NULL",
                    (now, ctx.generation, gid, kind),
                )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    cohorts: dict[str, int] = {}
    pending_count = 0
    for m in members:
        cohorts[m["cohort"]] = cohorts.get(m["cohort"], 0) + 1
        pending_count += m["identity_pending"]
    counts = {
        "identities": len(members),
        "cohorts": cohorts,
        "identityPending": pending_count,
        "incidents": len(incidents),
        "fingerprintFailures": fingerprint_failures,
        "boundVariantsWithMultipleIds": sum(1 for g in variant_gids.values() if len(g) > 1),
    }
    return {
        "input_sha256": _identity_input_sha(ctx),
        "output_sha256": member_digest,
        "counts": counts,
    }


def _identity_input_sha(ctx: SimpleNamespace) -> str:
    with ctx.conn.cursor() as cursor:
        cursor.execute(
            "SELECT gemrate_id, observed_date, psa10_population, raw_payload_sha256"
            " FROM market_gemrate_psa10_observation_v2 ORDER BY gemrate_id, observed_date"
        )
        rows = [
            [row["gemrate_id"], str(row["observed_date"]), int(row["psa10_population"]),
             row["raw_payload_sha256"]]
            for row in cursor.fetchall()
        ]
    return sha256_bytes(canonical_json(rows))


# §6.4: strict provider-native evidence token for gemrate bindings. Deliberately
# NOT the legacy "provider_payload" (psa_identity_repair.py:426) so the two
# branches stay distinguishable.
EVIDENCE_TYPE_GEMRATE = "provider_native_psa_identity_and_population"
EVIDENCE_TYPE_PROVIDER_PAGE = "provider_native_product_page"


# match_status='rejected' means two different things, and only one of them is a
# ruling about the binding it sits on.
#
# A VERDICT is written by a decision contract that looked at this provider row
# and said no: apply_verified_source_bindings writes action='reject', and
# new_era_db_tidy writes action='reject-wrong-printing-source'. Both stamp their
# reasoning into bind_evidence_json. Those must never be overturned by a lane
# that simply likes the card better.
#
# A QUARANTINE is collateral. psa_identity_repair.py rejects EVERY non-gemrate
# binding on a variant whose GEMRATE identity would not resolve, and leaves the
# old evidence untouched -- so the row keeps whatever it said before, usually
# action='confirm'. Nothing examined the provider binding at all. On 2026-08-07
# 14:08 one such pass rejected 226 price-lane rows in a single minute; the
# gemrate identities were repaired the next morning and the collateral was
# never revisited, leaving 121 cards at PSA10 population >= 1000 looking
# permanently ruled out when no one had ever looked at them.
#
# The distinction has to be readable from the row, because a lane that cannot
# tell them apart must choose between overturning real verdicts and honouring
# fake ones. It gets both wrong.
REJECTION_VERDICT_ACTIONS = frozenset({
    "reject",
    "reject-wrong-printing-source",
    # A supersession is a ruling about which of two gemrate ids owns the
    # variant, not a doubt about the loser's page. Resurrecting one would put
    # two exact gemrate bindings on one variant, which is the invariant
    # validate counts (strictGemrateBindings == universe), so no reverify lane
    # may reconsider it. resolve_active_psa_identity used to leave these rows
    # carrying whatever they said before -- v1729 and v1730 sat at 'rejected'
    # still reading action='confirm', indistinguishable from the collateral
    # this constant exists to separate.
    "supersede-losing-gemrate-binding",
})

if any(not re.fullmatch(r"[a-z-]+", action) for action in REJECTION_VERDICT_ACTIONS):
    # These are interpolated into SQL below. Keeping them to letters and hyphens
    # is what makes that safe, so the constraint is enforced, not assumed.
    raise AssertionError("rejection verdict actions must be lowercase and hyphens")

# The third verdict is a ruling about the CARD, not about any one binding.
#
# The 034 audit sheet has thirteen red rows: cards a human read and refused, so
# every non-gemrate binding on them stays quarantined and their market data
# stays out of the world. psa_identity_repair enforces it via `red_ids`, and
# nothing about that reached the row -- the ruling lived in a spreadsheet and a
# set literal. Reconsidering quarantined rows promoted seven of the thirteen
# before validator034's red13 invariant caught it, which is exactly one gate
# more than should have been needed. So the ruling is copied onto every row it
# governs, under this key, and every lane reads it off the row like any other.
REJECTION_RED_LIST_KEY = "redListed"

# Drop into a WHERE to mean "this row is not a rejection anyone reasoned about".
# Built from the constants above so the SQL and the Python cannot drift apart.
NOT_A_REJECTION_VERDICT_SQL = (
    "COALESCE(JSON_UNQUOTE(JSON_EXTRACT(bind_evidence_json,'$.action')),'') NOT IN ("
    + ",".join(f"'{action}'" for action in sorted(REJECTION_VERDICT_ACTIONS))
    + ")"
    + " AND COALESCE(JSON_UNQUOTE(JSON_EXTRACT(bind_evidence_json,"
    + f"'$.{REJECTION_RED_LIST_KEY}')),'false') <> 'true'"
)


NUMBER_SET_CODE_RE = re.compile(r"^([A-Z]{2,4}\d{2})-")


# A catalog-majority map from set code to set name used to live here, and
# `set_names_a_card_could_carry` took it as a second source for the name a
# printed code carries. It could not be one. catalog_variant.set_name asserts
# which product a particular card was PULLED FROM; no count over such rows
# converts that into what a set code is NAMED, and the docstring's claim that
# the majority made it safe did not survive the data: of 156 (tcg, language,
# code) keys, 80 were decided by three rows or fewer and 12 had a top-two tie
# at one row each, so the winner was whatever the execution plan emitted.
#
# Measured 2026-08-11 on the live catalog: of the 24 one-piece/en codes, the 9
# that `limitless_product_name` does not cover -- EB01, EB02, ST01, ST10, ST13,
# ST14, ST16, ST18, ST21 -- fell through to this map, and all 9 answered with
# ANOTHER set's name (ST01 -> 'Awakening of the New Era', which is OP05;
# ST13 -> OP12; ST14 -> OP10; EB01/ST16/ST18 -> OP11's 'A Fist of Divine
# Speed'; EB02 -> 'One Piece Promos'). Not one was right. That name is handed
# to `product_agrees`, the only check separating a reprint from a different
# product once the number agrees, so it made an OP11 page acceptable for an
# ST18 card. A code with no proved product name now simply yields no extra
# name -- the honest state.
def set_names_a_card_could_carry(row: Mapping[str, Any]) -> list[str]:
    """The catalog's set name, plus the one this card's NUMBER names.

    One Piece reprints a card into a later product without renumbering it, so
    "OP09-Emperors in the New World Nami ... 106" and OP08-106 are the same
    card described from two ends. PriceCharting files it under the number's
    set, and a product check that only knows the catalog's set_name refuses its
    page for saying "Two Legends" where we said "Emperors".

    Two ways to learn the number's set, and the second exists because the first
    only works when the code is written INTO the number. Measured 2026-08-10:
    of the 121 One Piece cards with no price source, all but a handful carry a
    bare "044" and NUMBER_SET_CODE_RE matches none of them, so this returned
    one name and the reprint page was never read. `printed_set_code` reads the
    code off the Limitless page for the product GemRate itself named, which is
    where the bare number's prefix was recorded in the first place.

    Widening where we look, not what we accept: every name returned here is
    still put to `product_agrees`, and the number, character and print
    signature still have to match on whichever page answers.
    """

    names = [str(row.get("set_name") or "")]
    match = NUMBER_SET_CODE_RE.match(str(row.get("collector_number") or "").upper())
    codes = [match.group(1)] if match else []
    import op_identity_rules  # deferred: it imports this module at its top

    for extra in (
        op_identity_rules.printed_set_code(row.get("variant_id")),
        op_identity_rules.sold_in_set_code(row.get("variant_id")),
    ):
        if extra and extra not in codes:
            codes.append(extra)
    for code in codes:
        alt = op_identity_rules.limitless_product_name(code)
        if alt and alt not in names:
            names.append(alt)
    return [name for name in names if name]


def red_listed_variants() -> list[int]:
    """The thirteen cards a human read on the 034 audit sheet and refused.

    Derived, never copied: the sheet is the authority, and a correction to it
    has to reach every lane without anybody remembering which lanes exist. It
    lives here rather than in one lane because the failure it prevents is a
    NEW lane -- one that never heard of the list -- and a new lane imports this
    module before it imports any other.

    Fail-closed on purpose: this raises rather than returning a short list. On
    2026-08-09 the PriceCharting discovery lane proposed three red cards
    because it could not see the ruling, prices and sales went live for two of
    them, and validator034's red13 invariant was what noticed -- one gate later
    than it should have been. AGENTS.md rule 11.
    """

    sys.path.insert(0, str(ROOT / "scripts"))
    import stamp_red_sheet_quarantine as RED

    return RED.red_variant_ids()


def rejection_is_verdict(bind_evidence_json: Any) -> bool:
    """Did something actually rule against this binding, or is it collateral?

    Unreadable evidence counts as a verdict. A row whose reasoning cannot be
    parsed is not thereby proved harmless, and the cost of the two mistakes is
    not symmetric: honouring a stale quarantine leaves a card off the front end
    until someone re-runs discovery, while overturning a real rejection binds a
    card to the wrong product and prices it wrong.
    """

    if bind_evidence_json is None:
        return False
    claim = bind_evidence_json
    if isinstance(claim, (str, bytes)):
        try:
            claim = json.loads(claim)
        except (ValueError, TypeError):
            return True
    if not isinstance(claim, Mapping):
        return True
    if str(claim.get("action") or "") in REJECTION_VERDICT_ACTIONS:
        return True
    return bool(claim.get(REJECTION_RED_LIST_KEY))


def _gemrate_printing_sha(fields: Mapping[str, str]) -> str:
    """Same 10-field recipe as resolve_active_psa_identity.printing_sha (§3.5).

    Kept in lockstep by value, not import, so this module stays standalone."""

    identity = {
        key: str(fields.get(key) or "").strip().casefold()
        for key in (
            "tcg_code", "card_language", "set_name", "set_code", "collector_number",
            "printing_code", "rarity_code", "edition_code", "parallel_code", "finish_code",
        )
    }
    return sha256_bytes(canonical_json(identity))


def _derive_print_fields(fp: Mapping[str, Any]) -> tuple[dict[str, str] | None, str]:
    """Provider-native printing tuple for minting a new variant (D7).

    Fails closed: any underivable piece returns (None, reason) so the member
    stays identity_pending instead of minting a guessed identity."""

    from g10_public_snapshot import normalize_collector

    set_name = str(fp.get("setName") or "")
    lowered = set_name.casefold()
    if lowered.startswith("one piece"):
        tcg = "one-piece"
    elif lowered.startswith("pokemon"):
        tcg = "pokemon"
    else:
        return None, "tcg_underivable"
    language = str(fp.get("derivedLanguage") or "")
    if language == "zh":
        # Catalog vocabulary needs zhCN/zhTW; GemRate set names only say Chinese.
        return None, "language_ambiguous_zh"
    if not str(fp.get("description") or ""):
        return None, "description_missing"
    collector = normalize_collector(fp.get("cardNumber"))
    if not collector.display or collector.display == "Unknown":
        return None, "collector_unknown"
    return {
        "tcg_code": tcg,
        "card_language": language,
        "set_name": set_name,
        "set_code": "",
        "collector_number": collector.display,
        "printing_code": "",
        "rarity_code": "",
        "edition_code": "",
        "parallel_code": _norm_text(fp.get("parallel") or ""),
        "finish_code": "",
    }, ""


def _tcg_from_set(set_name: Any) -> str:
    lowered = str(set_name or "").casefold()
    if lowered.startswith("one piece"):
        return "one-piece"
    if lowered.startswith("pokemon"):
        return "pokemon"
    return ""  # honest unknown; column default is '' too


def _sig_tokens(value: str) -> set[str]:
    # Single-character tokens ("Monkey D. Luffy" → "d", possessive "s") carry
    # no discriminating power and punish spelling drift; drop them.
    return {
        token for token in re.split(r"[^a-z0-9]+", _norm_text(value))
        if len(token) > 1 and token not in _NOISE_TOKENS
    }


def _token_covered(small: set[str], large: set[str]) -> bool:
    # Prefix-tolerant coverage, same tolerance as the set-signal comparison.
    return all(
        any(
            a == b or (min(len(a), len(b)) >= 4 and (a.startswith(b) or b.startswith(a)))
            for b in large
        )
        for a in small
    )


def _name_agrees(fp_name: str, variant_name: str) -> bool:
    """Does the provider's card name appear in the variant's canonical name?

    Set + collector number + parallel do NOT pin a card: one-piece numbers
    several distinct alt-arts 118, so adoption must never place a Luffy on a
    Rayleigh variant. Empty either side: no signal, leave to other checks.
    """

    f_tokens = _sig_tokens(fp_name)
    v_tokens = _sig_tokens(variant_name)
    if not f_tokens or not v_tokens:
        return True
    return _token_covered(f_tokens, v_tokens)


_PRINT_PHRASES = (
    "red manga alternate art", "manga alternate art", "special alternate art",
    "wanted alternate art", "alternate art", "full art", "reverse foil",
    "reverse holo", "master ball", "1st edition", "sec",
)


def _print_phrases(text: str) -> frozenset[str]:
    """Print-treatment wording present in a name/parallel blob.

    GemRate's parallel field often says just "Base" while the treatment lives
    in the name ("Full Art/Pikachu-Reverse Foil"), and catalog rows may carry
    it only in canonical_name ("… Manga Alternate Art 061" with an empty
    parallel_code). Comparing extracted phrase sets keeps a Base fingerprint
    from adopting a phrase-marked variant and vice versa. Token-bounded so
    "sec" never matches inside "second".
    """

    blob = " " + " ".join(
        t for t in re.split(r"[^a-z0-9]+", _norm_text(text)) if t
    ) + " "
    return frozenset(p for p in _PRINT_PHRASES if f" {p} " in blob)


def card_name_without_treatment(name: str) -> str:
    """The character's name, with a treatment GemRate glued to the front removed.

    GemRate fingerprints arrive as "Full Art/Charizard GX" -- the parallel and
    the name in a single field. Read whole, that string is not a name, and
    everything downstream that treated it as one was wrong in both directions:
    it put "Full Art/" into PriceCharting search queries (which answered a
    Pokemon query with a One Piece product), it refused 43 correct product
    pages whose titles say "Charizard GX" and never say "Full Art", and it
    admitted "Double Full Heal #105" for "Full Art/M Pidgeot EX" because the
    similarity check found the word "full" in both.

    The treatment still has to be proven -- it just cannot be proven against a
    page title that never spells it out. That job belongs to the print-signature
    rules, which read _PRINT_PHRASES from the same vocabulary as this function.

    Only a known print phrase is dropped, so a card whose name genuinely
    contains a slash keeps it.
    """

    head, separator, tail = (name or "").partition("/")
    if separator and _norm_text(head).strip() in _PRINT_PHRASES:
        return tail.strip()
    return name


def _snk_treatment_mirror(master_name: str, localized: str) -> bool:
    """SNK titles carry the print treatment after the name colon
    ("Pikachu: Mirror [s8a 001/028]"). Only that slot may claim mirror —
    a card NAMED Mirror (e.g. "Mirror Energy") never triggers it.

    Only a BARE mirror slot counts: qualified treatments like
    "マスターボール ミラー" (151 Master Ball reverse) name a pattern parallel
    whose variant-side vocabulary never says mirror, so they must not
    hard-conflict on this axis."""

    for text in (master_name, localized):
        head = re.split(r"[\[（(]", text, 1)[0]
        parts = re.split(r"[:：]", head, 1)
        if len(parts) != 2:
            continue
        slot = parts[1].strip().strip("・･ 　")
        if slot == "ミラー" or re.fullmatch(r"(?i)mirror", slot):
            return True
    return False


def _same_listing(fp_a: Mapping[str, Any], fp_b: Mapping[str, Any]) -> bool:
    """Two provider listings describing the same physical card (dup ids).

    Mutual description coverage is required — one-way coverage would fold a
    "Pikachu-Reverse" listing into a "Pikachu Base" one.
    """

    a = _sig_tokens(str(fp_a.get("description") or ""))
    b = _sig_tokens(str(fp_b.get("description") or ""))
    return bool(a) and bool(b) and _token_covered(a, b) and _token_covered(b, a)


def _parallel_agrees(fp_parallel: str, variant_parallel: str) -> bool:
    fpp = _norm_text(fp_parallel)
    vpp = _norm_text(variant_parallel)
    if fpp == vpp:
        return True
    # A variant with no printing evidence + a Base fingerprint is the default
    # print of the same card; alt-art wordings never collapse into "".
    return {fpp, vpp} == {"", "base"} or (vpp == "" and fpp == "base")


def _print_signature_agrees(fp_parallel: str, variant: Mapping[str, Any]) -> bool:
    """Does the fingerprint's print wording describe this variant's print?

    printing_code is the stronger catalog evidence when present (a 1st Edition
    print is stored as printing_code='1st' with parallel_code left ''), so it
    takes precedence and the ""≡Base default-print collapse must not apply.
    """

    printing = _norm_text(variant.get("printing_code") or "")
    if printing:
        fpp = _norm_text(fp_parallel)
        return fpp == printing or fpp == f"{printing} edition"
    return _parallel_agrees(fp_parallel, variant.get("parallel_code") or "")


def _gemrate_bind_evidence(fp: Mapping[str, Any], generation: str) -> tuple[dict[str, Any], str]:
    """Contract shape for operator_strict_source_identity (037): providerClaims
    carry what the provider itself said; evidence ties the bind to a
    raw-verified v2 observation via rawPayloadSha256."""

    codes, _ = _set_signals(fp.get("setName") or "")
    evidence = {
        "providerClaims": {
            "tcgCode": _tcg_from_set(fp.get("setName")),
            "cardLanguage": str(fp.get("derivedLanguage") or ""),
            "collectorNumber": str(fp.get("cardNumber") or ""),
            "setCode": sorted(codes)[0] if codes else "",
            "printingCode": "",
            "parallelCode": str(fp.get("parallel") or ""),
        },
        "evidence": {
            "type": EVIDENCE_TYPE_GEMRATE,
            "rawPayloadSha256": fp["rawSha256"],
            "path": f"data/private/gemrate/cards/{fp['gemrateId']}/card_details.json",
            "canonicalUrl": fp["canonicalUrl"],
            "settledId": fp["settledId"],
            "generation": generation,
        },
    }
    return evidence, sha256_bytes(canonical_json(evidence))


# _capture_fingerprint reads this straight off the PSA population row, so it is
# the PSA label verbatim -- the same string line 1900 already writes as the
# canonical_name of a newly created variant. Variants created before that read
# was corrected still carry the Universal rollup wording, so the binding
# transaction restates it for every variant it settles.
_PSA_FULL_NAME_SQL = "JSON_UNQUOTE(JSON_EXTRACT(m.detail_json,'$.fingerprint.description'))"
_PSA_LANGUAGE_SQL = (
    "JSON_UNQUOTE(JSON_EXTRACT(m.detail_json,'$.fingerprint.derivedLanguage'))"
)
_PSA_SET_NAME_SQL = "JSON_UNQUOTE(JSON_EXTRACT(m.detail_json,'$.fingerprint.psaSetName'))"
# The PSA numerator widened with a denominator borrowed from a sibling grader row
# in the same capture -- see _capture_fingerprint. Display only; never a sha input.
_PSA_NUMBER_FULL_SQL = (
    "JSON_UNQUOTE(JSON_EXTRACT(m.detail_json,'$.fingerprint.cardNumberFull'))"
)


def stage_bind(ctx: SimpleNamespace) -> dict[str, Any]:
    """S5: the single execution/closure point for S4's identity decisions.

    Closure rules (all data-decided, never interactive):
    - alias: a resettled id whose settled entity is itself a member closes as
      alias_of_settled_entity and drops to non_qualified (rule 8: one owner).
    - pop_decrease closes as provider_correction_verified only when both the
      peak and latest raw payloads re-verify on disk and describe the same
      identity tuple and the entity did not resettle.
    - multi-bound variants: the owner is the unique zero-hard-conflict
      fingerprint (parallel wording breaks ties); losers re-home to their own
      printing-sha variant when qualified. No unique owner -> stays open.
    Anything un-rulable stays an open incident -> identity_pending -> the
    generation cannot activate (§6.3), which is the honest outcome.
    """

    from datetime import datetime, timezone

    import gemrate_source

    conn = ctx.conn
    conn.rollback()
    generation = ctx.generation
    min_pop = int(POLICY["minPop"])
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT gemrate_id, variant_id, latest_psa10_population, cohort,"
            " identity_pending, detail_json FROM catalog_rebuild_member"
            " WHERE generation_id=%s", (generation,),
        )
        members = {row["gemrate_id"]: row for row in cursor.fetchall()}
        cursor.execute(
            "SELECT gemrate_id, variant_id, incident_kind, resolved_at"
            " FROM catalog_population_identity_incident WHERE generation_id=%s",
            (generation,),
        )
        incident_rows = cursor.fetchall()
        cursor.execute(
            "SELECT external_entity_id AS gid, variant_id, match_status"
            " FROM catalog_source_identity WHERE source_code='gemrate'"
        )
        bindings = {row["gid"]: row for row in cursor.fetchall()}
        cursor.execute(
            "SELECT v.id, v.opaque_id, v.tcg_code, v.card_language, v.canonical_name,"
            " v.set_name, v.collector_number, v.identity_status, v.created_at,"
            " p.parallel_code, p.printing_code, p.canonical_printing_sha256,"
            " p.tcg_code AS p_tcg_code, p.card_language AS p_card_language,"
            " p.set_code AS p_set_code, p.collector_number AS p_collector_number,"
            " p.edition_code AS p_edition_code, p.finish_code AS p_finish_code"
            " FROM catalog_variant v"
            " LEFT JOIN catalog_printing_identity p ON p.variant_id = v.id"
        )
        variants = {int(row["id"]): dict(row) for row in cursor.fetchall()}

    fingerprints: dict[str, Mapping[str, Any] | None] = {}
    fp_reasons: dict[str, str] = {}
    for gid in members:
        fp, reason = _capture_fingerprint(gemrate_source.CARDS_DIR, gid)
        fingerprints[gid] = fp
        if fp is None:
            fp_reasons[gid] = reason

    open_incidents: dict[str, list[str]] = {}
    for row in incident_rows:
        if row["resolved_at"] is None:
            open_incidents.setdefault(row["gemrate_id"], []).append(row["incident_kind"])

    # Variants minted by this rebuild (vs the pre-rebuild catalog). A mint that
    # duplicated a pre-existing card — the adoption key used to compare raw
    # collector text, so "085" never matched the catalog's "085/SVP" — must not
    # act as an adoption target or a settled home; re-homing below migrates its
    # binding back to the original variant and retires the empty shell.
    gen_start = datetime.strptime(
        generation.split("_", 1)[1], "%Y%m%dT%H%M%SZ"
    ).replace(tzinfo=timezone.utc)
    rebuild_minted = {
        vid for vid, variant in variants.items()
        if variant.get("created_at") is not None
        and variant["created_at"].replace(tzinfo=timezone.utc) >= gen_start
    }

    # Bindings the 034 world formally accepted before this rebuild started:
    # authoritative reunion evidence. Set and parallel wording drift between
    # provider and catalog is tolerated for these — the acceptance itself is
    # the identity evidence; collector core, language and card name must still
    # agree so a re-settled listing cannot ride an old acceptance.
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT gemrate_id, variant_id FROM catalog_gemrate_provenance_acceptance"
            " WHERE accepted_at < %s ORDER BY accepted_at",
            (gen_start.replace(tzinfo=None),),
        )
        prior_home = {
            row["gemrate_id"]: int(row["variant_id"]) for row in cursor.fetchall()
        }

    printing_sha_owner: dict[str, int] = {}
    adoption_index: dict[tuple[str, str], list[int]] = {}
    for vid, variant in variants.items():
        sha = variant.get("canonical_printing_sha256")
        if sha:
            printing_sha_owner[sha] = vid
        core = _collector_core(variant.get("collector_number") or "")
        if not core or vid in rebuild_minted:
            continue  # no comparable number / rebuild shell: never an adoption target
        key = (str(variant.get("tcg_code") or "").casefold(), core)
        adoption_index.setdefault(key, []).append(vid)

    # Current exact gemrate owners per variant; maintained as decisions land so
    # later adoptions see the post-decision world.
    variant_exact_owner: dict[int, str] = {}
    for gid, row in bindings.items():
        if row["match_status"] == "exact":
            variant_exact_owner.setdefault(int(row["variant_id"]), gid)

    closures: list[tuple[str, str, str]] = []  # (gid, kind, resolution)
    new_incidents: list[dict[str, Any]] = []
    binding_updates: dict[str, dict[str, Any]] = {}  # gid -> final binding row intent
    variant_mints: dict[str, dict[str, Any]] = {}  # printing_sha -> mint spec
    member_updates: dict[str, dict[str, Any]] = {}
    pending_extra: dict[str, list[str]] = {}
    demoted: dict[str, str] = {}  # gid -> recorded non_qualified demotion reason
    counts = {
        "variantsCreated": 0, "variantsAdopted": 0, "bindingsUpgradedExact": 0,
        "bindingsRebound": 0, "bindingsRejected": 0, "aliasesClosed": 0,
        "incidentsClosed": 0, "incidentsStillOpen": 0, "newIncidents": 0,
        "ownershipUnresolved": 0, "duplicateMintsRebound": 0,
        "duplicateShellsRetired": 0, "priorAcceptanceRebound": 0,
        "duplicateListingsDemoted": 0, "printShaNameClashDemoted": 0,
        "localeSetNamesFollowed": 0, "localeEnSetNamesRestated": 0,
    }

    def member_pop(gid: str) -> int:
        row = members.get(gid)
        return int(row["latest_psa10_population"]) if row else 0

    def qualified(gid: str) -> bool:
        return member_pop(gid) >= min_pop

    # --- alias closure (rule 8) -------------------------------------------
    alias_of: dict[str, str] = {}
    for gid, fp in fingerprints.items():
        if fp is None:
            continue
        settled = fp["settledId"]
        if settled and settled != gid:
            if settled in members:
                alias_of[gid] = settled
                closures.append((gid, "requested_id_resettled", "alias_of_settled_entity"))
                counts["aliasesClosed"] += 1
                if gid in bindings and bindings[gid]["match_status"] != "rejected":
                    binding_updates[gid] = {
                        "action": "reject", "reason": "alias_of_settled_entity",
                    }
            # settled entity missing from members: incident stays open.
    # A rejected alias can no longer hold printing ownership; without this
    # purge the settled successor re-homing to its own print sees a ghost
    # "printing_already_owned" conflict from its retired predecessor.
    variant_exact_owner = {
        vid: gid for vid, gid in variant_exact_owner.items() if gid not in alias_of
    }

    # --- pop_decrease closure ---------------------------------------------
    popdec_gids = [
        gid for gid, kinds in open_incidents.items() if "pop_decrease_same_entity" in kinds
    ]
    if popdec_gids:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT gemrate_id, observed_date, psa10_population, capture_path,"
                " raw_payload_sha256 FROM market_gemrate_psa10_observation_v2"
                f" WHERE gemrate_id IN ({','.join(['%s'] * len(popdec_gids))})",
                tuple(popdec_gids),
            )
            series: dict[str, list[dict[str, Any]]] = {}
            for row in cursor.fetchall():
                series.setdefault(row["gemrate_id"], []).append(row)

    def _raw_identity_tuple(capture_path: str, expect_sha: str) -> tuple[str, ...] | None:
        path = ROOT / capture_path
        try:
            data = path.read_bytes()
        except OSError:
            return None
        if sha256_bytes(data) != expect_sha:
            return None
        try:
            raw = json.loads(data.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        return tuple(
            _norm_text(str(raw.get(field) or ""))
            for field in ("description", "set_name", "card_number", "parallel")
        )

    for gid in popdec_gids:
        if gid in alias_of:
            continue  # entity swapped; decrease is not a same-entity correction
        fp = fingerprints.get(gid)
        rows = sorted(series.get(gid, []), key=lambda r: str(r["observed_date"]))
        if fp is None or not rows or fp["settledId"] != gid:
            continue
        peak = max(rows, key=lambda r: (int(r["psa10_population"]), str(r["observed_date"])))
        latest = rows[-1]
        if int(latest["psa10_population"]) >= int(peak["psa10_population"]):
            closures.append((gid, "pop_decrease_same_entity", "series_recovered"))
            continue
        peak_identity = _raw_identity_tuple(peak["capture_path"], peak["raw_payload_sha256"])
        latest_identity = _raw_identity_tuple(latest["capture_path"], latest["raw_payload_sha256"])
        if peak_identity is not None and peak_identity == latest_identity:
            closures.append((gid, "pop_decrease_same_entity", "provider_correction_verified"))

    # --- ownership over multi-bound / conflicted variants ------------------
    variant_bound: dict[int, list[str]] = {}
    for gid, row in bindings.items():
        if row["match_status"] != "rejected" and gid not in alias_of:
            variant_bound.setdefault(int(row["variant_id"]), []).append(gid)

    def resolve_home(gid: str, fp: Mapping[str, Any]) -> None:
        """Re-home a qualified, evidence-complete gid to its printing variant."""

        fields, reason = _derive_print_fields(fp)
        if fields is None:
            if reason == "collector_unknown":
                # The provider page carries no printed collector number and the
                # product contract (product_ready) requires one, so this id can
                # never hold a real printing identity. The 034 world parked the
                # same cards as g10_* review placeholders outside the universe;
                # 036 records that parking as an explicit cohort demotion.
                demoted[gid] = "provider_carries_no_collector_number"
                if gid in bindings and bindings[gid]["match_status"] != "rejected":
                    binding_updates[gid] = {"action": "reject", "reason": demoted[gid]}
                for kind in open_incidents.get(gid, []):
                    closures.append((gid, kind, "non_qualified_collector_unknown"))
                return
            pending_extra.setdefault(gid, []).append(f"mint:{reason}")
            return
        # Rule R1: a binding the 034 world formally accepted is reunion
        # evidence stronger than wording comparison — set names and parallel
        # vocabularies drift between provider and catalog ("Special Alternate
        # Art" vs "sr-spc"), but the acceptance was made against this exact
        # provider identity. Collector core, language and card name must still
        # agree so a re-settled listing cannot ride an old acceptance.
        prior = prior_home.get(gid)
        if (
            prior is not None
            and prior in variants
            and prior not in rebuild_minted
            and variant_exact_owner.get(prior) in (None, gid)
        ):
            prior_variant = variants[prior]
            hard = [
                c for c in _fingerprint_variant_conflicts(fp, prior_variant)
                if not c.startswith("set:") and not c.startswith("set_code:")
            ]
            if not hard and _name_agrees(
                fp.get("name") or "", prior_variant.get("canonical_name") or ""
            ):
                binding_updates[gid] = {
                    "action": "bind", "variant_id": prior, "fp": fp,
                    "fields": fields, "adopted": True,
                }
                variant_exact_owner[prior] = gid
                counts["priorAcceptanceRebound"] += 1
                return
        sha = _gemrate_printing_sha(fields)
        target = printing_sha_owner.get(sha)
        if target is not None and target in rebuild_minted:
            # The sha lands on a variant this rebuild minted. If the mint
            # duplicated a pre-existing catalog card, that original is the home
            # (its sales/image/name lineage lives there); only a mint with no
            # pre-rebuild counterpart may keep the binding.
            key = (fields["tcg_code"], _collector_core(fields["collector_number"]))
            older = [
                vid for vid in adoption_index.get(key, [])
                if not _fingerprint_variant_conflicts(fp, variants[vid])
                and _parallel_agrees(fp["parallel"], variants[vid].get("parallel_code") or "")
                and _name_agrees(fp.get("name") or "",
                                 variants[vid].get("canonical_name") or "")
                and _print_phrases(
                    f"{fp.get('name') or ''} {fp.get('parallel') or ''}"
                ) == _print_phrases(
                    f"{variants[vid].get('canonical_name') or ''}"
                    f" {variants[vid].get('parallel_code') or ''}"
                    f" {variants[vid].get('printing_code') or ''}"
                )
            ]
            if len(older) > 1:
                anchored = [
                    vid for vid in older
                    if variants[vid].get("canonical_printing_sha256")
                ]
                if len(anchored) == 1:
                    older = anchored
            if len(older) == 1:
                target = older[0]
        if target is not None and not _name_agrees(
            fp.get("name") or "", variants[target].get("canonical_name") or ""
        ):
            # Same set/number/parallel but a different card name (one-piece
            # numbers several distinct alt-arts 118): §3.5's printing sha
            # cannot host both identities, so binding or minting here would
            # collide two cards into one variant. The incumbent keeps the
            # print; the newcomer is parked outside the universe with an
            # explicit, revisitable demotion instead of a dishonest merge.
            demoted[gid] = f"print_identity_unrepresentable_vs_variant_{target}"
            if gid in bindings and bindings[gid]["match_status"] != "rejected":
                binding_updates[gid] = {"action": "reject", "reason": demoted[gid]}
            for kind in open_incidents.get(gid, []):
                closures.append((gid, kind, "print_identity_unrepresentable"))
            counts["printShaNameClashDemoted"] += 1
            return
        if target is None:
            key = (fields["tcg_code"], _collector_core(fields["collector_number"]))
            candidates = []
            for vid in adoption_index.get(key, []):
                variant = variants[vid]
                if _fingerprint_variant_conflicts(fp, variant):
                    continue
                if not _parallel_agrees(fp["parallel"], variant.get("parallel_code") or ""):
                    continue
                # Set + number + parallel do not pin a card (one-piece numbers
                # several distinct alt-arts 118): the provider's card name must
                # appear in the canonical name or this is a different card.
                if not _name_agrees(fp.get("name") or "",
                                    variant.get("canonical_name") or ""):
                    continue
                # Rule R5: print-treatment wording must agree. A "Base 061"
                # fingerprint must not adopt the "Manga Alternate Art 061"
                # variant (same number, different print), and a Full Art
                # fingerprint must not tie with the plain print of the number.
                if _print_phrases(
                    f"{fp.get('name') or ''} {fp.get('parallel') or ''}"
                ) != _print_phrases(
                    f"{variant.get('canonical_name') or ''}"
                    f" {variant.get('parallel_code') or ''}"
                    f" {variant.get('printing_code') or ''}"
                ):
                    continue
                candidates.append(vid)
            if len(candidates) > 1:
                # A variant with no canonical_printing_sha256 can never become
                # product_ready — it is a parked placeholder (g10_* review
                # rows), not an identity anchor; a unique anchored candidate
                # outranks placeholders instead of tying with them.
                anchored = [
                    vid for vid in candidates
                    if variants[vid].get("canonical_printing_sha256")
                ]
                if len(anchored) == 1:
                    candidates = anchored
            if len(candidates) > 1:
                pending_extra.setdefault(gid, []).append("adoption_ambiguous")
                return
            if candidates:
                target = candidates[0]
        if target is not None:
            owner = variant_exact_owner.get(target)
            if owner and owner != gid:
                owner_fp = fingerprints.get(owner)
                if owner_fp is not None and _same_listing(fp, owner_fp):
                    # Rule R4: two live provider listings describing the same
                    # physical card (provider has not merged the ids yet).
                    # Mutual description coverage is required so a "Reverse"
                    # listing never folds into a "Base" one. The incumbent
                    # keeps the variant; the duplicate is parked outside the
                    # universe so the card is never double-counted.
                    demoted[gid] = f"duplicate_provider_listing_of_{owner}"
                    if gid in bindings and bindings[gid]["match_status"] != "rejected":
                        binding_updates[gid] = {"action": "reject", "reason": demoted[gid]}
                    for kind in open_incidents.get(gid, []):
                        closures.append((gid, kind, "duplicate_provider_listing"))
                    counts["duplicateListingsDemoted"] += 1
                    return
                new_incidents.append({
                    "gemrate_id": gid, "variant_id": target,
                    "incident_kind": "variant_mixed_printings",
                    "detail": {"reason": "printing_already_owned", "owner": owner},
                })
                pending_extra.setdefault(gid, []).append("printing_already_owned")
                return
            binding_updates[gid] = {
                "action": "bind", "variant_id": target, "fp": fp, "fields": fields,
                "adopted": True,
            }
            variant_exact_owner[target] = gid
            return
        spec = variant_mints.get(sha)
        if spec is not None:
            # Two live ids minting the same print in one run = rule 8 conflict.
            new_incidents.append({
                "gemrate_id": gid, "variant_id": None,
                "incident_kind": "variant_mixed_printings",
                "detail": {"reason": "same_print_two_entities", "peer": spec["gid"]},
            })
            pending_extra.setdefault(gid, []).append("same_print_two_entities")
            return
        variant_mints[sha] = {"gid": gid, "fields": fields, "fp": fp, "printing_sha": sha}
        binding_updates[gid] = {"action": "bind", "variant_id": None, "fp": fp,
                                "fields": fields, "mint_sha": sha, "adopted": False}

    for vid, gids in sorted(variant_bound.items()):
        variant = variants.get(vid)
        if variant is None:
            continue
        if vid in rebuild_minted:
            continue  # not a settled home; re-evaluated wholesale below
        contenders = []
        for gid in gids:
            if fingerprints.get(gid) is None and not qualified(gid):
                # Unverifiable non-qualified binding (no capture exists): give
                # it the same ruling the loser path would, instead of letting
                # it hold the whole contest open forever.
                binding_updates[gid] = {"action": "reject"}
                closures.append((gid, "accepted_binding_moved",
                                 "unverifiable_non_qualified_binding_rejected"))
                closures.append((gid, "variant_mixed_printings",
                                 "unverifiable_non_qualified_binding_rejected"))
                if variant_exact_owner.get(vid) == gid:
                    del variant_exact_owner[vid]
                continue
            contenders.append(gid)
        gids = contenders
        if not gids:
            continue
        with_fp = [gid for gid in gids if fingerprints.get(gid) is not None]
        if len(with_fp) < len(gids):
            counts["ownershipUnresolved"] += len(gids) > 1
            continue  # un-rulable without every fingerprint; incidents stay open
        zero = [
            gid for gid in with_fp
            if not _fingerprint_variant_conflicts(fingerprints[gid], variant)
        ]
        owner: str | None = None
        if len(zero) == 1:
            owner = zero[0]
        elif len(zero) > 1:
            para = [
                gid for gid in zero
                if _print_signature_agrees(fingerprints[gid]["parallel"], variant)
            ]
            if len(para) == 1:
                owner = para[0]
        if owner is None and len(gids) == 1 and not zero:
            # Sole binding whose fingerprint moved away: evict and re-home it.
            owner = ""
        if owner is None:
            if len(gids) > 1 and all(not qualified(gid) for gid in gids):
                # No owner is derivable and no contender can ever enter the
                # universe: each gets the loser-path ruling and the variant is
                # left unbound, exactly as piecemeal resolution would end up.
                for gid in gids:
                    binding_updates[gid] = {"action": "reject"}
                    closures.append((gid, "accepted_binding_moved",
                                     "non_qualified_binding_rejected"))
                    closures.append((gid, "variant_mixed_printings",
                                     "non_qualified_binding_rejected"))
                if variant_exact_owner.get(vid) in gids:
                    del variant_exact_owner[vid]
                continue
            if len(gids) > 1:
                counts["ownershipUnresolved"] += 1
            continue
        if owner:
            binding_updates.setdefault(owner, {
                "action": "bind", "variant_id": vid, "fp": fingerprints[owner],
                "fields": None, "adopted": False, "confirm": True,
            })
            variant_exact_owner[vid] = owner
            closures.append((owner, "variant_mixed_printings",
                             "confirmed_owner_by_provider_fingerprint"))
        for gid in gids:
            if gid == owner:
                continue
            if variant_exact_owner.get(vid) == gid:
                del variant_exact_owner[vid]
            if qualified(gid):
                resolve_home(gid, fingerprints[gid])
                if binding_updates.get(gid, {}).get("action") == "bind":
                    closures.append((gid, "accepted_binding_moved",
                                     "rebound_to_provider_native_variant"))
                    closures.append((gid, "variant_mixed_printings",
                                     "split_to_new_variant"))
            else:
                binding_updates[gid] = {"action": "reject"}
                closures.append((gid, "accepted_binding_moved",
                                 "non_qualified_binding_rejected"))
                closures.append((gid, "variant_mixed_printings",
                                 "non_qualified_binding_rejected"))

    # --- bindings living on rebuild-minted variants ------------------------
    # Every such binding re-resolves from provider evidence: a legit mint
    # re-finds itself via its printing sha (no-op rebind), while a mint that
    # duplicated a pre-existing catalog card migrates back to the original the
    # fixed collector-core adoption key now reaches.
    for vid in sorted(variant_bound):
        if vid not in rebuild_minted:
            continue
        for gid in variant_bound[vid]:
            fp = fingerprints.get(gid)
            if fp is None or not qualified(gid):
                continue  # unverifiable / out of universe: binding left as-is
            resolve_home(gid, fp)
            intent = binding_updates.get(gid)
            if intent and intent.get("action") == "bind" and intent.get("variant_id") != vid:
                counts["duplicateMintsRebound"] += 1
                if variant_exact_owner.get(vid) == gid:
                    del variant_exact_owner[vid]

    # --- unbound or rejected-bound qualified members -----------------------
    for gid, member in sorted(members.items()):
        if gid in alias_of or gid in binding_updates:
            continue
        binding = bindings.get(gid)
        if binding is not None and binding["match_status"] != "rejected":
            continue  # handled by ownership loop (or clean: confirmed below)
        if not qualified(gid):
            continue
        fp = fingerprints.get(gid)
        if fp is None or fp["psaRowCount"] != 1:
            continue  # already pending via fingerprint reasons
        resolve_home(gid, fp)
        if binding_updates.get(gid, {}).get("action") == "bind":
            # A successful re-home settles this id's moved/mixed incidents too.
            closures.append((gid, "accepted_binding_moved",
                             "rebound_to_provider_native_variant"))
            closures.append((gid, "variant_mixed_printings", "split_to_new_variant"))

    # --- write everything in one transaction -------------------------------
    now = datetime.now(timezone.utc)
    now_str = now.strftime("%Y-%m-%d %H:%M:%S.%f")
    minted_ids: dict[str, int] = {}
    try:
        with conn.cursor() as cursor:
            for sha, spec in sorted(variant_mints.items()):
                fields, fp = spec["fields"], spec["fp"]
                opaque = f"cmc_{sha[:24]}"  # D7: derived from the printing sha
                evidence, evidence_sha = _gemrate_bind_evidence(fp, generation)
                cursor.execute(
                    "INSERT INTO catalog_variant (opaque_id, tcg_code, card_language,"
                    " canonical_name, set_name, set_code, printing_code, rarity_code,"
                    " collector_number, identity_status)"
                    " VALUES (%s, %s, %s, %s, %s, '', '', '', %s, 'confirmed')"
                    " ON DUPLICATE KEY UPDATE id=LAST_INSERT_ID(id)",
                    (
                        opaque, fields["tcg_code"], fields["card_language"],
                        fp["description"], fields["set_name"], fields["collector_number"],
                    ),
                )
                vid = cursor.lastrowid
                cursor.execute(
                    "INSERT INTO catalog_printing_identity (variant_id, tcg_code,"
                    " card_language, set_name, set_code, printing_code, rarity_code,"
                    " collector_number, edition_code, parallel_code, finish_code,"
                    " canonical_printing_sha256, identity_status, evidence_sha256,"
                    " provenance_json, observed_at)"
                    " VALUES (%s, %s, %s, %s, '', '', '', %s, '', %s, '', %s,"
                    " 'confirmed', %s, %s, %s)"
                    " ON DUPLICATE KEY UPDATE evidence_sha256=VALUES(evidence_sha256),"
                    " provenance_json=VALUES(provenance_json)",
                    (
                        vid, fields["tcg_code"], fields["card_language"],
                        fields["set_name"], fields["collector_number"],
                        fields["parallel_code"], sha, evidence_sha,
                        json.dumps(evidence, ensure_ascii=False, sort_keys=True), now,
                    ),
                )
                minted_ids[sha] = vid
                counts["variantsCreated"] += 1

            for gid, intent in sorted(binding_updates.items()):
                if intent["action"] == "reject":
                    cursor.execute(
                        # S4 reasoned about this id; the row has to say so.
                        # Moving the status alone left the old evidence in
                        # place, so a decided rejection was unreadable next to
                        # collateral -- the exact confusion
                        # REJECTION_VERDICT_ACTIONS exists to end.
                        "UPDATE catalog_source_identity SET match_status='rejected',"
                        " bind_evidence_json=JSON_SET("
                        "   COALESCE(bind_evidence_json, JSON_OBJECT()),"
                        "   '$.previousAction',"
                        "     JSON_UNQUOTE(JSON_EXTRACT(bind_evidence_json,'$.action')),"
                        "   '$.action', 'reject',"
                        "   '$.reason', %s,"
                        "   '$.rejectedAt', UTC_TIMESTAMP())"
                        " WHERE source_code='gemrate' AND external_entity_id=%s"
                        " AND match_status<>'rejected'",
                        (str(intent.get("reason") or "identity_resolve_rejection"), gid),
                    )
                    counts["bindingsRejected"] += cursor.rowcount
                    continue
                vid = intent["variant_id"]
                if vid is None:
                    vid = minted_ids[intent["mint_sha"]]
                    intent["variant_id"] = vid
                fp = intent["fp"]
                evidence, evidence_sha = _gemrate_bind_evidence(fp, generation)
                variant_row = variants.get(vid) or {}
                fields = intent.get("fields")
                # bound_* mirror the printing identity the bind was validated
                # against (that is the strict view's equality contract); the
                # provider's own wording lives in providerClaims instead.
                if variant_row.get("canonical_printing_sha256"):
                    mirror = {
                        "tcg": variant_row.get("p_tcg_code") or "",
                        "language": variant_row.get("p_card_language") or "",
                        "set_code": variant_row.get("p_set_code") or "",
                        "collector": variant_row.get("p_collector_number") or "",
                        "printing": variant_row.get("printing_code") or "",
                        "parallel": variant_row.get("parallel_code") or "",
                        "edition": variant_row.get("p_edition_code") or "",
                        "finish": variant_row.get("p_finish_code") or "",
                    }
                else:
                    if fields is None:
                        fields, _reason = _derive_print_fields(fp)
                    if fields is not None and vid not in minted_ids.values():
                        # Existing variant with no printing identity: land a
                        # provider-native printing row so the bind can ever
                        # reach the strict projection.
                        cursor.execute(
                            "INSERT INTO catalog_printing_identity (variant_id,"
                            " tcg_code, card_language, set_name, set_code,"
                            " printing_code, rarity_code, collector_number,"
                            " edition_code, parallel_code, finish_code,"
                            " canonical_printing_sha256, identity_status,"
                            " evidence_sha256, provenance_json, observed_at)"
                            " VALUES (%s, %s, %s, %s, '', '', '', %s, '', %s, '',"
                            " %s, 'confirmed', %s, %s, %s)"
                            " ON DUPLICATE KEY UPDATE"
                            " evidence_sha256=VALUES(evidence_sha256),"
                            " provenance_json=VALUES(provenance_json)",
                            (
                                vid, fields["tcg_code"], fields["card_language"],
                                fields["set_name"], fields["collector_number"],
                                fields["parallel_code"],
                                _gemrate_printing_sha(fields), evidence_sha,
                                json.dumps(evidence, ensure_ascii=False, sort_keys=True),
                                now,
                            ),
                        )
                    if fields is None:
                        mirror = {"tcg": "", "language": "", "set_code": "",
                                  "collector": "", "printing": "", "parallel": "",
                                  "edition": "", "finish": ""}
                    else:
                        mirror = {
                            "tcg": fields["tcg_code"],
                            "language": fields["card_language"],
                            "set_code": fields["set_code"],
                            "collector": fields["collector_number"],
                            "printing": fields["printing_code"],
                            "parallel": fields["parallel_code"],
                            "edition": fields["edition_code"],
                            "finish": fields["finish_code"],
                        }
                cursor.execute(
                    "INSERT INTO catalog_source_identity (source_code,"
                    " external_entity_id, variant_id, match_status, evidence_sha256,"
                    " source_product_number, bound_set_code, bound_printing_code,"
                    " bind_evidence_json, bound_tcg_code, bound_card_language,"
                    " bound_collector_number, bound_edition_code, bound_parallel_code,"
                    " bound_finish_code)"
                    " VALUES ('gemrate', %s, %s, 'exact', %s, %s, %s, %s, %s, %s, %s,"
                    " %s, %s, %s, %s)"
                    " ON DUPLICATE KEY UPDATE variant_id=VALUES(variant_id),"
                    " match_status=VALUES(match_status),"
                    " evidence_sha256=VALUES(evidence_sha256),"
                    " source_product_number=VALUES(source_product_number),"
                    " bound_set_code=VALUES(bound_set_code),"
                    " bound_printing_code=VALUES(bound_printing_code),"
                    " bind_evidence_json=VALUES(bind_evidence_json),"
                    " bound_tcg_code=VALUES(bound_tcg_code),"
                    " bound_card_language=VALUES(bound_card_language),"
                    " bound_collector_number=VALUES(bound_collector_number),"
                    " bound_edition_code=VALUES(bound_edition_code),"
                    " bound_parallel_code=VALUES(bound_parallel_code),"
                    " bound_finish_code=VALUES(bound_finish_code)",
                    (
                        gid, vid, evidence_sha,
                        str(fp["cardNumber"] or "")[:64],
                        mirror["set_code"][:24], mirror["printing"][:24],
                        json.dumps(evidence, ensure_ascii=False, sort_keys=True),
                        mirror["tcg"][:32], mirror["language"][:8],
                        mirror["collector"][:96], mirror["edition"][:191],
                        mirror["parallel"][:64], mirror["finish"][:64],
                    ),
                )
                if intent.get("confirm"):
                    counts["bindingsUpgradedExact"] += 1
                else:
                    counts["bindingsRebound"] += 1
                if intent.get("adopted"):
                    counts["variantsAdopted"] += 1

            # Rebuild-minted variants left with no live binding are duplicate
            # shells: strip the printing identity (so no future sha lookup or
            # product-shape query can land on them) and park the row as
            # 'review', the same shape the catalog uses for other non-entities.
            if rebuild_minted:
                placeholders = ",".join(["%s"] * len(rebuild_minted))
                cursor.execute(
                    "SELECT DISTINCT variant_id FROM catalog_source_identity"
                    f" WHERE variant_id IN ({placeholders})"
                    " AND match_status <> 'rejected'",
                    tuple(sorted(rebuild_minted)),
                )
                still_referenced = {int(row["variant_id"]) for row in cursor.fetchall()}
                shells = sorted(
                    rebuild_minted - still_referenced - set(minted_ids.values())
                )
                for shell_vid in shells:
                    cursor.execute(
                        "DELETE FROM catalog_printing_identity WHERE variant_id=%s",
                        (shell_vid,),
                    )
                    cursor.execute(
                        "UPDATE catalog_variant SET identity_status='review'"
                        " WHERE id=%s",
                        (shell_vid,),
                    )
                counts["duplicateShellsRetired"] = len(shells)

            closed_keys = set()
            for gid, kind, resolution in closures:
                if (gid, kind) in closed_keys:
                    continue
                closed_keys.add((gid, kind))
                cursor.execute(
                    "UPDATE catalog_population_identity_incident"
                    " SET resolved_at=%s, resolution=%s"
                    " WHERE generation_id=%s AND gemrate_id=%s AND incident_kind=%s"
                    " AND resolved_at IS NULL",
                    (now_str, resolution, generation, gid, kind),
                )
                counts["incidentsClosed"] += cursor.rowcount
            for incident in new_incidents:
                cursor.execute(
                    "INSERT INTO catalog_population_identity_incident (generation_id,"
                    " gemrate_id, variant_id, incident_kind, detail_json, opened_at,"
                    " resolved_at, resolution)"
                    " VALUES (%s, %s, %s, %s, %s, %s, NULL, NULL)"
                    " ON DUPLICATE KEY UPDATE detail_json=VALUES(detail_json)",
                    (
                        generation, incident["gemrate_id"], incident["variant_id"],
                        incident["incident_kind"],
                        json.dumps(incident["detail"], ensure_ascii=False, sort_keys=True),
                        now_str,
                    ),
                )
                counts["newIncidents"] += 1

            cursor.execute(
                "SELECT gemrate_id, incident_kind FROM catalog_population_identity_incident"
                " WHERE generation_id=%s AND resolved_at IS NULL", (generation,),
            )
            still_open: dict[str, list[str]] = {}
            for row in cursor.fetchall():
                still_open.setdefault(row["gemrate_id"], []).append(row["incident_kind"])
            counts["incidentsStillOpen"] = sum(len(v) for v in still_open.values())

            pending_after = 0
            for gid, member in sorted(members.items()):
                detail = json.loads(member["detail_json"]) if member["detail_json"] else {}
                reasons: list[str] = []
                fp = fingerprints.get(gid)
                if fp is None:
                    reasons.append(f"fingerprint:{fp_reasons.get(gid, 'unknown')}")
                elif fp["psaRowCount"] != 1:
                    reasons.append(f"psa_rows:{fp['psaRowCount']}")
                reasons.extend(pending_extra.get(gid, []))
                reasons.extend(f"incident_open:{kind}" for kind in still_open.get(gid, []))
                cohort = member["cohort"]
                variant_id = member["variant_id"]
                if gid in alias_of:
                    cohort = "non_qualified"
                    detail["aliasOf"] = alias_of[gid]
                elif gid in demoted:
                    cohort = "non_qualified"
                    detail["demotedReason"] = demoted[gid]
                intent = binding_updates.get(gid)
                if intent and intent["action"] == "bind":
                    variant_id = intent["variant_id"]
                elif intent and intent["action"] == "reject":
                    variant_id = None
                identity_pending = 1 if (reasons and cohort != "non_qualified") else 0
                pending_after += identity_pending
                detail["s5"] = {
                    "pendingReasons": reasons,
                    "action": (intent or {}).get("action", "none"),
                    "adopted": bool((intent or {}).get("adopted")),
                }
                member_updates[gid] = {"variant_id": variant_id, "cohort": cohort,
                                       "identity_pending": identity_pending}
                cursor.execute(
                    "UPDATE catalog_rebuild_member SET variant_id=%s, cohort=%s,"
                    " identity_pending=%s, detail_json=%s, computed_at=%s"
                    " WHERE generation_id=%s AND gemrate_id=%s",
                    (
                        variant_id, cohort, identity_pending,
                        json.dumps(detail, ensure_ascii=False, sort_keys=True),
                        now_str, generation, gid,
                    ),
                )
            counts["identityPendingAfter"] = pending_after
            counts["demotedCollectorUnknown"] = len(demoted)

            # A variant's display name is the PSA label of the entity bound to
            # it, so it is settled here, in the transaction that settles the
            # binding -- not left to whatever wording seeded the row. Every
            # qualified variant holds exactly one exact gemrate binding, so the
            # join names one row per variant.
            #
            # It used to be one pure-SQL UPDATE copying the PSA description
            # verbatim, and that is exactly why the truncated names kept coming
            # back: the label PSA prints ENDS at the bare numerator ("... Special
            # Art Rare 110"), so every run of this transaction restored the short
            # form over whatever had been repaired since. Measured 2026-08-11: 71
            # of the published top 100 and 436 of the 1605 bound variants were
            # sitting on a truncated tail. The completion is Python because it
            # needs leading-zero folding -- `079` has to recognise `79/73` -- and
            # the version that tried it in SQL/regex appended instead, producing
            # "... Secret 079 79/73" on 79 rows and getting the whole function
            # deleted. identity_name.complete_collector_tail is the single
            # implementation, covered by scripts/test_identity_name.py.
            #
            # The number is restated first, in the same pass, because the name's
            # tail is built from it. It lands on catalog_variant.collector_number
            # only -- catalog_printing_identity.collector_number is a printing_sha
            # input and is frozen for the reason spelled out at the set_name
            # restate below. _collector_core() folds the denominator away before
            # any identity comparison, so widening the display column moves no
            # match.
            cursor.execute(
                "SELECT v.id AS variant_id, v.canonical_name, v.collector_number,"
                f" {_PSA_FULL_NAME_SQL} AS psa_description,"
                f" {_PSA_NUMBER_FULL_SQL} AS psa_number_full,"
                " p.collector_number AS printed_collector_number"
                " FROM catalog_variant v"
                " INNER JOIN catalog_source_identity s ON s.variant_id=v.id"
                "   AND s.source_code='gemrate' AND s.match_status='exact'"
                " INNER JOIN catalog_rebuild_member m ON m.generation_id=%s"
                "   AND m.variant_id=v.id AND m.gemrate_id=s.external_entity_id"
                " LEFT JOIN catalog_printing_identity p ON p.variant_id=v.id",
                (generation,),
            )
            counts["psaNamesRestated"] = 0
            counts["psaCollectorNumbersRestated"] = 0
            for row in cursor.fetchall():
                variant_id = int(row["variant_id"])
                collector = complete_collector_number(
                    row["psa_number_full"], (), row["printed_collector_number"]
                ) or str(row["collector_number"] or "")
                if collector and collector != str(row["collector_number"] or ""):
                    cursor.execute(
                        "UPDATE catalog_variant SET collector_number=%s WHERE id=%s",
                        (collector, variant_id),
                    )
                    counts["psaCollectorNumbersRestated"] += 1
                full_name = complete_collector_tail(row["psa_description"], collector)
                if full_name and full_name != str(row["canonical_name"] or ""):
                    cursor.execute(
                        "UPDATE catalog_variant SET canonical_name=%s WHERE id=%s",
                        (full_name, variant_id),
                    )
                    counts["psaNamesRestated"] += 1

            # And the set name off the same PSA row, for a reason the display
            # only half explains: set_name is not decoration, it is an INPUT to
            # _fingerprint_variant_conflicts, which refuses a provider binding
            # whose set text shares no token with this column. 1213 of the 1605
            # bound variants carried something the PSA row disagreed with, so
            # the comparison was run against seed wording rather than against
            # the label PSA prints -- variant 1's PriceCharting page says
            # 'Pokemon Promo' and the column said 'SVP EN "Van gogh exhibition"',
            # zero overlap, and the Van Gogh Pikachu published SNKRDUNK's $969
            # instead of PriceCharting's $2,900. Restated 2026-08-11 over every
            # exact binding: that hold clears and none of the 997 replayable
            # exact PC bindings newly conflicts. It runs AFTER the adjudication
            # above on purpose -- restating first would cement the set name of
            # a binding that same transaction is about to reject.
            cursor.execute(
                "UPDATE catalog_variant v"
                " INNER JOIN catalog_source_identity s ON s.variant_id=v.id"
                "   AND s.source_code='gemrate' AND s.match_status='exact'"
                " INNER JOIN catalog_rebuild_member m ON m.generation_id=%s"
                "   AND m.variant_id=v.id AND m.gemrate_id=s.external_entity_id"
                f" SET v.set_name={_PSA_SET_NAME_SQL}"
                f" WHERE COALESCE({_PSA_SET_NAME_SQL},'')<>''"
                f"   AND BINARY v.set_name<>BINARY {_PSA_SET_NAME_SQL}",
                (generation,),
            )
            counts["psaSetNamesRestated"] = cursor.rowcount

            # Same authority, same transaction, for the same reason. A NULL
            # card_language is the ABSENCE of a claim, and _fingerprint_variant_
            # conflicts gates the whole language comparison on `v_lang and
            # f_lang` -- so a blank column did not mean "unknown, refuse", it
            # meant a fingerprint of any language passed unopposed. Variant 1813
            # ('2024 One Piece English Version 1st Anniversary Set Nami Base
            # 016', card_language NULL) was bound exact to SNKRDUNK item 254304,
            # 'ナミ R (赤ナミ) [OP01-016]【中国語版】', and published that Chinese
            # printing's price as the English card's on 2026-08-10. Four of the
            # 1605 bound variants held a blank language. Replayed 2026-08-11:
            # those four fill (1813/1814 en, 1865/1866 ja), v1813 then raises
            # language:zh!=en and snk-refresh demotes it to manual_review, and
            # v1814 -- bound to SNK 287032, the 【英語版】 listing of the same
            # anniversary set -- stays clean. Nothing else in the 1605 moves.
            #
            # The keyword fallback on set_name stays: it covers UNBOUND
            # candidates, which identity-resolve also compares and which this
            # restatement by definition cannot reach.
            cursor.execute(
                "UPDATE catalog_variant v"
                " INNER JOIN catalog_source_identity s ON s.variant_id=v.id"
                "   AND s.source_code='gemrate' AND s.match_status='exact'"
                " INNER JOIN catalog_rebuild_member m ON m.generation_id=%s"
                "   AND m.variant_id=v.id AND m.gemrate_id=s.external_entity_id"
                f" SET v.card_language={_PSA_LANGUAGE_SQL}"
                f" WHERE COALESCE({_PSA_LANGUAGE_SQL},'')<>''"
                f"   AND COALESCE(v.card_language,'')<>{_PSA_LANGUAGE_SQL}",
                (generation,),
            )
            counts["psaLanguagesRestated"] = cursor.rowcount

            # The set name again, this time for the column the SITE reads.
            # catalog_variant.set_name above is the judgment input;
            # catalog_variant_locale.localized_set_name is what fills a card's
            # `sets` map, and restating only the judgment column left the Van
            # Gogh card ranked #1 on a PriceCharting binding the PSA set name
            # had just unblocked while still printing 'SVP EN "Van gogh
            # exhibition"' underneath it. 1285 of the 1286 published cards
            # carried an en locale row that disagreed with the PSA label.
            #
            # catalog_printing_identity.set_name is deliberately NOT restated
            # with it, though it disagrees on 1241 rows. That column is not
            # display text -- it is one of the ten casefolded inputs to
            # printing_sha(), and scripts/validate_psa_identity_repair.py
            # recomputes the hash from the live row and compares it to the
            # stored canonical_printing_sha256. Mirroring the PSA label into it
            # on 2026-08-11 turned completePrintingHashExact red and the table
            # had to be restored from the pre-run dump. Recomputing the sha
            # instead is not available either: it is UNIQUE, it is the owner
            # key bind uses to detect print collisions, and validator034's
            # red13 invariant pins specific historical hashes. Nothing renders
            # printingIdentity.setName -- the detail page shows editionCode,
            # setCode and finishCode -- so the fingerprint keeps its own
            # wording and the display reads the locale row.
            #
            # Non-English locales first, and only the ones that were never
            # translated. 568 of the 1286 published cards carry a real zhTW/ja
            # set translation from data/editorial/set-names.json; overwriting
            # those with an English label would be a regression dressed as a
            # fix. A locale row that still equals the English row verbatim is
            # a copy, not a translation, and follows the canonical label. This
            # must run BEFORE the 'en' restatement below, which is what makes
            # the copy detectable.
            cursor.execute(
                "UPDATE catalog_variant_locale t"
                " INNER JOIN catalog_variant_locale en ON en.variant_id=t.variant_id"
                "   AND en.locale_code='en'"
                " INNER JOIN catalog_variant v ON v.id=t.variant_id"
                " INNER JOIN catalog_rebuild_member m ON m.generation_id=%s"
                "   AND m.variant_id=v.id"
                " SET t.localized_set_name=v.set_name"
                " WHERE t.locale_code<>'en'"
                "   AND BINARY t.localized_set_name=BINARY en.localized_set_name"
                "   AND BINARY t.localized_set_name<>BINARY v.set_name",
                (generation,),
            )
            counts["localeSetNamesFollowed"] = cursor.rowcount

            # 'en' is not a translation -- it is the canonical English label,
            # and its authority is the column restated above.
            cursor.execute(
                "UPDATE catalog_variant_locale t"
                " INNER JOIN catalog_variant v ON v.id=t.variant_id"
                " INNER JOIN catalog_rebuild_member m ON m.generation_id=%s"
                "   AND m.variant_id=v.id"
                " SET t.localized_set_name=v.set_name"
                " WHERE t.locale_code='en'"
                "   AND BINARY COALESCE(t.localized_set_name,'')<>BINARY v.set_name",
                (generation,),
            )
            counts["localeEnSetNamesRestated"] = cursor.rowcount
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    output = sha256_bytes(canonical_json([
        [gid, member_updates[gid]["variant_id"], member_updates[gid]["cohort"],
         member_updates[gid]["identity_pending"]]
        for gid in sorted(member_updates)
    ]))
    return {"input_sha256": _bind_input_sha(ctx), "output_sha256": output, "counts": counts}


def _bind_input_sha(ctx: SimpleNamespace) -> str:
    """S5's input is S4's recorded output — never the tables S5 itself mutates,
    so a completed bind stage cannot self-invalidate on the next linear pass."""

    with ctx.conn.cursor() as cursor:
        cursor.execute(
            "SELECT output_sha256 FROM cardz_rebuild_checkpoint"
            " WHERE generation_id=%s AND stage='identity-resolve'",
            (ctx.generation,),
        )
        row = cursor.fetchone()
    return sha256_bytes(canonical_json(["bind-input", (row or {}).get("output_sha256")]))


STAGED_037_NAME = "037_strict_source_identity_provider_native.mysql.sql"


def _pc_replay_dir(generation: str) -> Path:
    return (ROOT / "data" / "private" / "pricecharting_session" / "html"
            / f"replay-{generation}")


def _pc_page_identity(html: str) -> tuple[dict[str, Any] | None, str]:
    """Provider-native identity signals from a replayed PC product page.

    Reads only what the page itself asserts: canonical URL (console slug ->
    tcg/language/set tokens, product slug -> collector fallback) and the h1
    heading (card number, parallel bracket, set text). Fails closed."""

    canonical = re.search(
        r'<link[^>]*rel="canonical"[^>]*href="([^"]+)"', html
    ) or re.search(r'<link[^>]*href="([^"]+)"[^>]*rel="canonical"', html)
    if not canonical:
        return None, "canonical_missing"
    url = canonical.group(1)
    parts = [part for part in url.split("/") if part]
    if "game" not in parts or len(parts) < parts.index("game") + 3:
        return None, "canonical_not_product"
    console_slug = parts[parts.index("game") + 1]
    product_slug = parts[parts.index("game") + 2]
    h1_match = re.search(
        r'<h1[^>]*id="product_name"[^>]*>(.*?)</h1>', html, re.DOTALL
    )
    if not h1_match:
        return None, "h1_missing"
    # Entities, then tags: the h1 of every "Scarlet &amp; Violet" set spells its
    # ampersand as an entity, and stripping tags alone leaves the word `amp` in
    # the heading. _set_signals turns "&" into the noise word "and" precisely
    # because an ampersand names no set, so `amp` is not a signal -- it is the
    # markup showing through, and it read as a token the catalog could never
    # match. 109 of the 143 hard_conflict refusals on 2026-08-10 carried it.
    # Unescape after the tags are gone so an entity-encoded angle bracket
    # cannot become a tag that the strip already walked past.
    heading = " ".join(
        html_unescape(re.sub(r"<[^>]+>", " ", h1_match.group(1))).split()
    )
    number_match = re.search(r"#\s*([A-Za-z0-9/.-]+)", heading)
    slug_tail = product_slug.rsplit("-", 1)[-1]
    collector = number_match.group(1) if number_match else ""
    if not collector and re.fullmatch(r"[0-9]+[a-z]?", slug_tail):
        collector = slug_tail
    if not collector:
        return None, "collector_missing"
    bracket = re.search(r"\[([^\]]+)\]", heading)
    console_tokens = console_slug.replace("-", " ")
    if console_slug.startswith("pokemon"):
        tcg = "pokemon"
    elif console_slug.startswith("one-piece"):
        tcg = "one-piece"
    else:
        tcg = ""
    language = "ja" if "japanese" in console_tokens.split() else "en"
    # The h1 states the set inside its own /console/ anchor. Carving it out of
    # the heading text instead needed a '#' before the card number to know where
    # the card's name ended, and One Piece headings have no '#' -- so the name
    # and the printed code stayed in setText ("Monkey.D.Luffy   OP05-119 One
    # Piece Awakening of the New Era") and conflicted with a catalog set name
    # that says the same set. Measured 2026-08-11: variants 24 and 107, the only
    # two of 984 exact PC bindings that conflicted for a reason that was ours.
    anchor = re.search(
        r'<a[^>]*href="/console/[^"]*"[^>]*>(.*?)</a>', h1_match.group(1), re.DOTALL
    )
    if anchor:
        set_text = " ".join(
            html_unescape(re.sub(r"<[^>]+>", " ", anchor.group(1))).split()
        )
    else:
        set_text = heading[number_match.end():] if number_match else heading
        set_text = re.sub(r"\[[^\]]*\]", " ", set_text).strip()
    if not set_text:
        set_text = console_tokens
    return {
        "canonicalUrl": url,
        "consoleSlug": console_slug,
        "productSlug": product_slug,
        "heading": heading,
        "collector": collector,
        "parallel": (bracket.group(1).strip() if bracket else ""),
        "tcg": tcg,
        "language": language,
        "setText": set_text,
    }, ""


# Tokens that mark a true within-number parallel (base and special share the
# collector number, so wording is the only discriminator). Catalog values
# WITHOUT any of these are rarity vocabulary (sir/ur/l/sr-…), which the PC
# page already encodes in the collector number itself.
_TRUE_PARALLEL_TOKENS = frozenset({
    "reverse", "holo", "holofoil", "foil", "ball", "1st", "first", "edition",
    "shadowless", "unlimited", "manga", "anniversary", "wanted", "alternate",
    "alt", "aa", "sp", "spc", "parallel", "p", "v2", "v3",
})


def _pc_rarity_only_parallel(value: str) -> bool:
    """True when a catalog parallel_code is rarity vocab, not a real parallel."""

    tokens = set(re.split(r"[^a-z0-9]+", value.casefold())) - {""}
    return bool(tokens) and not (tokens & _TRUE_PARALLEL_TOKENS)


def stage_pc_replay(ctx: SimpleNamespace) -> dict[str, Any]:
    """S6 (§6.4/§6.5): replay artifact -> receipts -> 037 -> PC rebind.

    The replay directory is an input artifact produced once by
    pc_cache_replay.py (copy-first, content-gated); this stage re-verifies
    every winner file against its sidecar sha, lands the capture receipts the
    037 view's EXISTS demands, promotes the staged 037 migration, and then
    re-derives PC bindings from what the replayed pages themselves assert.
    Bindings without a page (or with soft mismatches) keep their status and
    simply never reach the strict projection — honest, not silent."""

    from datetime import datetime, timezone

    conn = ctx.conn
    conn.rollback()
    generation = ctx.generation
    replay_dir = _pc_replay_dir(generation)
    manifest_path = replay_dir / "replay-manifest.json"
    if not manifest_path.is_file():
        raise SystemExit(
            f"replay artifact missing: {manifest_path} — run"
            f" pipelines/pc_cache_replay.py --generation {generation} first"
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    counts = {
        "pagesVerified": 0, "receiptsUpserted": 0, "migration037Applied": 0,
        "bindingsSeen": 0, "bindingsWithPage": 0, "restampedExact": 0,
        "upgradedFromReview": 0, "downgradedToReview": 0, "hardConflicts": 0,
        "parallelSoftMismatch": 0, "pageParseFailures": 0,
        "bindingsWithoutPage": 0, "pagesWithoutBinding": 0,
        "replayRejects": int(manifest.get("rejects") or 0),
    }

    # --- Phase A: re-verify every winner file against its sidecar ----------
    pages: dict[str, dict[str, Any]] = {}
    for sidecar_path in sorted(replay_dir.glob("*.json")):
        if sidecar_path.name.startswith("replay-"):
            continue
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
        html_path = sidecar_path.with_suffix(".html")
        digest = sha256_file(html_path)
        if digest != sidecar["captureSha256"]:
            raise AssertionError(
                f"replay file drifted from sidecar: {html_path.name}"
            )
        pages[str(sidecar["pcProductId"])] = {
            "htmlPath": html_path,
            "sha256": digest,
            "capturePath": str(sidecar["capturePath"]),
            "capturedAtUtc": str(sidecar["capturedAtUtc"]),
            "parserVersion": str(sidecar["parserVersion"]),
        }
        counts["pagesVerified"] += 1

    # --- Phase B: capture receipts (the strict view's EXISTS target) -------
    try:
        with conn.cursor() as cursor:
            for pid, page in sorted(pages.items()):
                captured_at = datetime.strptime(
                    page["capturedAtUtc"], "%Y-%m-%dT%H:%M:%SZ"
                ).replace(tzinfo=timezone.utc)
                cursor.execute(
                    "INSERT INTO catalog_provider_capture_receipt (source_code,"
                    " external_entity_id, capture_sha256, capture_path,"
                    " captured_at, generation_id, parser_version)"
                    " VALUES ('pricecharting', %s, %s, %s, %s, %s, %s)"
                    " ON DUPLICATE KEY UPDATE capture_path=VALUES(capture_path),"
                    " captured_at=VALUES(captured_at),"
                    " generation_id=VALUES(generation_id),"
                    " parser_version=VALUES(parser_version)",
                    (
                        pid, page["sha256"], page["capturePath"][:500],
                        captured_at, generation, page["parserVersion"][:64],
                    ),
                )
                counts["receiptsUpserted"] += 1
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    # --- Phase C: promote + apply the staged 037 view ----------------------
    staged = ROOT / "pipelines" / "migrations" / "staged" / STAGED_037_NAME
    live = ROOT / "pipelines" / "migrations" / STAGED_037_NAME
    if not live.exists():
        if not staged.exists():
            raise SystemExit(f"037 migration missing from both {live} and {staged}")
        staged.rename(live)
    from db_runtime import migrate

    migrate(conn, ROOT / "pipelines" / "migrations", only={STAGED_037_NAME})
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT 1 FROM cardz_schema_version WHERE version_code='037'"
        )
        if cursor.fetchone() is None:
            raise AssertionError("037 applied but schema version row missing")
    counts["migration037Applied"] = 1

    # --- Phase D: re-derive PC bindings from the replayed pages ------------
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT si.external_entity_id AS pid, si.variant_id, si.match_status,"
            " v.tcg_code, v.card_language, v.set_name, v.collector_number,"
            " v.set_code AS v_set_code, v.printing_code AS v_printing_code,"
            " p.parallel_code, p.printing_code, p.canonical_printing_sha256,"
            " p.tcg_code AS p_tcg_code, p.card_language AS p_card_language,"
            " p.set_code AS p_set_code, p.collector_number AS p_collector_number,"
            " p.edition_code AS p_edition_code, p.finish_code AS p_finish_code"
            " FROM catalog_source_identity si"
            " JOIN catalog_variant v ON v.id=si.variant_id"
            " LEFT JOIN catalog_printing_identity p ON p.variant_id=v.id"
            " WHERE si.source_code='pricecharting'"
        )
        pc_bindings = cursor.fetchall()

    results: list[tuple[str, str, str]] = []  # (pid, final_status, evidence_sha)
    updates: list[tuple[str, str, str | None, str | None, dict | None]] = []
    seen_pids: set[str] = set()
    for row in pc_bindings:
        counts["bindingsSeen"] += 1
        pid = str(row["pid"])
        seen_pids.add(pid)
        status = row["match_status"]
        page = pages.get(pid)
        if page is None:
            counts["bindingsWithoutPage"] += 1
            results.append((pid, status, ""))
            continue
        if status == "rejected":
            results.append((pid, status, ""))
            continue
        counts["bindingsWithPage"] += 1
        html = page["htmlPath"].read_text(encoding="utf-8", errors="replace")
        identity, reason = _pc_page_identity(html)
        if identity is None:
            counts["pageParseFailures"] += 1
            results.append((pid, status, ""))
            continue
        pseudo_fp = {
            "cardNumber": identity["collector"],
            "derivedLanguage": identity["language"],
            "setName": identity["setText"],
        }
        conflicts = _fingerprint_variant_conflicts(pseudo_fp, row)
        if identity["tcg"] and str(row["tcg_code"] or "") and \
                identity["tcg"] != str(row["tcg_code"]):
            conflicts.append(f"tcg:{identity['tcg']}!={row['tcg_code']}")
        if conflicts:
            counts["hardConflicts"] += 1
            if status == "exact":
                counts["downgradedToReview"] += 1
                updates.append((pid, "manual_review", None, None, None))
                results.append((pid, "manual_review", ""))
            else:
                results.append((pid, status, ""))
            continue
        # One rule, one implementation. This stage used to carry its own copy
        # of the bracket comparison, so tightening the shared one (2026-08-09,
        # bracket-less page vs a treated printing_code) left the stage that
        # STAMPS bindings exact still using the loose reading.
        if not _pc_print_signature_ok(identity["parallel"], row):
            counts["parallelSoftMismatch"] += 1
            if status == "exact":
                # The page is the authority for an exact binding. When it stops
                # proving one, leaving the row exact keeps publishing a price
                # nothing stands behind -- and keeps the product held against
                # the variant that could prove it.
                counts["downgradedToReview"] += 1
                updates.append((pid, "manual_review", None, None, None))
                results.append((pid, "manual_review", ""))
            else:
                results.append((pid, status, ""))
            continue
        evidence = {
            "providerClaims": {
                "tcgCode": identity["tcg"],
                "cardLanguage": identity["language"],
                "collectorNumber": identity["collector"],
                "setCode": "",
                "printingCode": "",
                "parallelCode": identity["parallel"],
            },
            "evidence": {
                "type": EVIDENCE_TYPE_PROVIDER_PAGE,
                "sha256": page["sha256"],
                "path": page["capturePath"],
                "canonicalUrl": identity["canonicalUrl"],
                "pageHeading": identity["heading"],
                "capturedAt": page["capturedAtUtc"],
                "generation": generation,
            },
        }
        evidence_sha = sha256_bytes(canonical_json(evidence))
        if row["canonical_printing_sha256"]:
            mirror = (
                str(row["p_tcg_code"] or ""), str(row["p_card_language"] or ""),
                str(row["p_set_code"] or ""), str(row["p_collector_number"] or ""),
                str(row["printing_code"] or ""), str(row["parallel_code"] or ""),
                str(row["p_edition_code"] or ""), str(row["p_finish_code"] or ""),
            )
        else:
            # No printing identity row: the strict view's INNER JOIN excludes
            # this bind regardless; mirror the variant's own claim honestly.
            mirror = (
                str(row["tcg_code"] or ""), str(row["card_language"] or ""),
                str(row["v_set_code"] or ""), str(row["collector_number"] or ""),
                str(row["v_printing_code"] or ""), "", "", "",
            )
        if status == "manual_review":
            counts["upgradedFromReview"] += 1
        counts["restampedExact"] += 1
        updates.append((pid, "exact", evidence_sha, identity["collector"],
                        {"evidence": evidence, "mirror": mirror}))
        results.append((pid, "exact", evidence_sha))

    for pid in sorted(set(pages) - seen_pids):
        counts["pagesWithoutBinding"] += 1

    try:
        with conn.cursor() as cursor:
            for pid, new_status, evidence_sha, product_number, extra in updates:
                if extra is None:
                    cursor.execute(
                        "UPDATE catalog_source_identity SET match_status=%s"
                        " WHERE source_code='pricecharting'"
                        " AND external_entity_id=%s",
                        (new_status, pid),
                    )
                    continue
                mirror = extra["mirror"]
                cursor.execute(
                    "UPDATE catalog_source_identity SET match_status='exact',"
                    " evidence_sha256=%s, source_product_number=%s,"
                    " bind_evidence_json=%s, bound_tcg_code=%s,"
                    " bound_card_language=%s, bound_set_code=%s,"
                    " bound_collector_number=%s, bound_printing_code=%s,"
                    " bound_parallel_code=%s, bound_edition_code=%s,"
                    " bound_finish_code=%s"
                    " WHERE source_code='pricecharting' AND external_entity_id=%s",
                    (
                        evidence_sha,
                        str(product_number or "")[:64],
                        json.dumps(extra["evidence"], ensure_ascii=False,
                                   sort_keys=True),
                        mirror[0][:32], mirror[1][:8], mirror[2][:24],
                        mirror[3][:96], mirror[4][:24], mirror[5][:64],
                        mirror[6][:191], mirror[7][:64], pid,
                    ),
                )
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    output = sha256_bytes(canonical_json(sorted(results)))
    return {"input_sha256": _pc_replay_input_sha(ctx), "output_sha256": output,
            "counts": counts}


def _pc_replay_input_sha(ctx: SimpleNamespace) -> str:
    """S6's input: S5's recorded output + the replay manifest artifact.

    Never reads the tables S6 mutates (bindings, receipts), so a completed
    stage cannot self-invalidate on the next linear pass."""

    manifest_path = _pc_replay_dir(ctx.generation) / "replay-manifest.json"
    if not manifest_path.is_file():
        raise SystemExit(
            f"replay artifact missing: {manifest_path} — run"
            f" pipelines/pc_cache_replay.py --generation {ctx.generation} first"
        )
    with ctx.conn.cursor() as cursor:
        cursor.execute(
            "SELECT output_sha256 FROM cardz_rebuild_checkpoint"
            " WHERE generation_id=%s AND stage='bind'",
            (ctx.generation,),
        )
        row = cursor.fetchone()
    return sha256_bytes(canonical_json([
        "pc-replay-input", (row or {}).get("output_sha256"),
        sha256_file(manifest_path),
    ]))


def _snk_dir(generation: str) -> Path:
    return ROOT / "data" / "private" / "snk" / f"rebuild-{generation}"


def _snk_language(master_name: str, localized: str) -> str:
    """The language SNKRDUNK itself stamps on the listing.

    Untagged means Japanese, but "not English" did NOT mean Japanese: SNK also
    lists Chinese and Korean printings of the same product number, and reading
    those as ja let them sail into a ja variant with no language conflict at
    all. Chinese stays the bare "zh" when the listing does not say which
    script, matching _language_from_set_name -- ambiguous fails closed.
    """

    text = f"{master_name} {localized}"
    lowered = text.casefold()
    if "【英語版】" in text or "english" in lowered:
        return "en"
    if "【韓国語版】" in text or "[kr]" in lowered or "korean" in lowered:
        return "ko"
    if "簡体" in text or "简体" in text:
        return "zhCN"
    if "繁体" in text or "繁體" in text:
        return "zhTW"
    if "中国語版" in text or "中國語版" in text or "[chn]" in lowered or "chinese" in lowered:
        return "zh"
    return "ja"


def _snk_tcg(master_name: str, localized: str) -> str:
    text = f"{master_name} {localized}".casefold()
    if "ワンピース" in text or "one piece" in text:
        return "one-piece"
    if "ポケモン" in text or "pokemon" in text or "pokémon" in text:
        return "pokemon"
    return ""


_SNK_INTERNAL_PN_RE = re.compile(r"pkmn-tcg-\d+")


def _snk_collector_claim(master_name: str, localized: str, product_number: str) -> str:
    """The provider's printed-number designation for an SNK item.

    SNK titles carry the real designation in brackets ("Rayquaza AR[S3a
    056/076]"); the product_number slug is an internal enumeration for older
    Pokemon items ("pkmn-tcg-1740") and must never be read as a collector
    number. Digit-less brackets ("[EN]") are markers, not designations.
    """

    for text in (master_name, localized):
        for found in re.findall(r"\[([^\]]+)\]", text or ""):
            if any(ch.isdigit() for ch in found):
                return " ".join(found.split())
    if _SNK_INTERNAL_PN_RE.fullmatch(product_number or ""):
        return ""
    return product_number or ""


def _snk_claim_number(claim: str) -> str:
    """Comparable number token of a designation ("S3a 056/076" -> "056/076")."""

    parts = claim.split()
    token = parts[-1] if parts else ""
    if token.casefold().startswith("no."):
        token = token[3:]
    return token


def snk_claim_set_agrees(claim: str, row: Mapping[str, Any]) -> bool:
    """Does the SNKRDUNK designation's own set claim agree with ours?

    One function because three lanes ask it -- S7, snk-identity-reverify and
    snk-identity-discover -- and until 2026-08-10 they asked it differently.
    The reverify lane read the claim by taking every token but the last, which
    is the bracketed Pokemon form ("S3a 056/076"); One Piece joins the code to
    the number ("OP09-106"), so that reading returned "" for every One Piece
    card ever put to it, the supersede below never fired once, and 16 cards sat
    in manual_review behind a set conflict nothing could clear.

    Our side is read from four places rather than two for the same reason. The
    set_code column is empty on 48 of the cards in the gap while the code is
    spelled inside set_name, and where the catalog knows neither,
    `printed_set_code` proves it off the Limitless page for the product GemRate
    itself named -- GemRate names the product a card was SOLD in, the card
    prints the set it FIRST appeared in, and refusing that difference is what
    was rejecting correct listings as `set_code:['op04']!=['op05']`.
    """

    import op_identity_rules  # deferred: it imports this module at its top

    in_claim = op_identity_rules.SET_CODE_RE.search(claim.upper())
    claim_set = in_claim.group(1) if in_claim else " ".join(claim.split()[:-1])
    named = op_identity_rules.SET_CODE_RE.search(str(row.get("set_name") or "").upper())
    ours = {
        str(row.get("v_set_code") or "").casefold(),
        str(row.get("p_set_code") or "").casefold(),
        named.group(1).casefold() if named else "",
        op_identity_rules.printed_set_code(row.get("variant_id")).casefold(),
        op_identity_rules.sold_in_set_code(row.get("variant_id")).casefold(),
    } - {""}
    return bool(claim_set) and claim_set.casefold() in ours


def stage_snk_refresh(ctx: SimpleNamespace) -> dict[str, Any]:
    """S7 (§6.4 SNK): land fresh SNK payloads for bound items, write capture
    receipts, and re-derive SNK bindings from the provider master record.

    Landing accepts every candidate payload; acceptance for the exact stamp is
    fixed to trading_card_single_psa10 with the 1-card variant resolved, and
    the identity comparison runs on what the master itself asserts
    (productNumber, 【英語版】 marker, set tokens in the name). primaryMedia
    travels inside the evidence file for S9. New-variant SNK discovery is not
    this stage — unbound variants stay market_pending, honestly."""

    from datetime import datetime, timezone

    import snk_market_data

    conn = ctx.conn
    conn.rollback()
    generation = ctx.generation
    snk_dir = _snk_dir(generation)
    items_dir = snk_dir / "items"
    items_dir.mkdir(parents=True, exist_ok=True)
    parser_version = "snkmd_" + sha256_file(
        ROOT / "pipelines" / "snk_market_data.py"
    )[:12]

    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT si.external_entity_id AS iid, si.variant_id, si.match_status,"
            " v.tcg_code, v.card_language, v.set_name, v.collector_number,"
            " v.canonical_name,"
            " v.set_code AS v_set_code, v.printing_code AS v_printing_code,"
            " p.parallel_code, p.printing_code, p.canonical_printing_sha256,"
            " p.tcg_code AS p_tcg_code, p.card_language AS p_card_language,"
            " p.set_code AS p_set_code, p.collector_number AS p_collector_number,"
            " p.edition_code AS p_edition_code, p.finish_code AS p_finish_code"
            " FROM catalog_source_identity si"
            " JOIN catalog_variant v ON v.id=si.variant_id"
            " LEFT JOIN catalog_printing_identity p ON p.variant_id=v.id"
            " WHERE si.source_code='snkrdunk'"
        )
        snk_bindings = cursor.fetchall()

    worklist = sorted({
        int(row["iid"]) for row in snk_bindings
        if str(row["iid"]).isdigit() and row["match_status"] != "rejected"
    })
    counts = {
        "worklist": len(worklist), "bindingsSeen": len(snk_bindings),
        "landedOk": 0, "landedError": 0, "receiptsUpserted": 0,
        "restampedExact": 0, "upgradedFromReview": 0, "downgradedToReview": 0,
        "hardConflicts": 0, "parallelSoftMismatch": 0, "notAcceptable": 0,
        "bindingsWithoutPayload": 0,
    }

    out_path = snk_dir / f"snk_harvest_{generation}.jsonl"
    report = snk_market_data.run(
        worklist, out_path, delay=0.0,
        condition_code=snk_market_data.PSA10_CONDITION,
        run_id=generation, workers=8,
    )
    # run() renames .partial -> out_path only on a 100% harvest. Items whose
    # PSA10 history has no unique 1-card variant fail permanently, so a small
    # remainder is a data condition, not an error: read the partial and let
    # those bindings fall through bindingsWithoutPayload (strict-view
    # excluded). A large remainder means the session died mid-run — abort.
    harvest_path = out_path
    if not harvest_path.is_file():
        partial_path = out_path.with_suffix(out_path.suffix + ".partial")
        missing_n = int(report.get("remaining") or 0)
        if not partial_path.is_file():
            raise SystemExit(f"snk harvest produced no file: {out_path}")
        if missing_n > 25:
            raise SystemExit(
                f"snk harvest missing {missing_n} items (systemic failure): {partial_path}"
            )
        harvest_path = partial_path
    rows_by_id: dict[int, dict[str, Any]] = {}
    with harvest_path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            item_id = row.get("item_id")
            if isinstance(item_id, int):
                rows_by_id[item_id] = row
    counts["landedError"] = sum(1 for row in rows_by_id.values() if row.get("error"))
    counts["landedOk"] = len(rows_by_id) - counts["landedError"]
    counts["harvestMissingItems"] = sorted(set(worklist) - set(rows_by_id))[:25]

    # --- evidence files + receipts ----------------------------------------
    receipts: dict[int, tuple[str, str, str]] = {}  # iid -> (sha, relpath, fetched)
    try:
        with conn.cursor() as cursor:
            for item_id in worklist:
                row = rows_by_id.get(item_id)
                if row is None or row.get("error"):
                    continue
                evidence_doc = {
                    "itemId": item_id,
                    "master": (row.get("source_payload") or {}).get("master"),
                    "conditionFilter": row.get("condition_filter"),
                    "quantityVariantId": row.get("quantity_variant_id"),
                    "productNumber": row.get("product_number"),
                    "imageUrl": row.get("image_url"),
                    "fetchedAt": row.get("fetched_at"),
                }
                blob = canonical_json(evidence_doc)
                item_path = items_dir / f"{item_id}.json"
                item_path.write_bytes(blob)
                digest = sha256_bytes(blob)
                rel = item_path.relative_to(ROOT).as_posix()
                fetched = str(row.get("fetched_at") or "")
                captured_at = datetime.strptime(
                    fetched, "%Y-%m-%dT%H:%M:%S%z"
                ) if fetched else datetime.now(timezone.utc)
                cursor.execute(
                    "INSERT INTO catalog_provider_capture_receipt (source_code,"
                    " external_entity_id, capture_sha256, capture_path,"
                    " captured_at, generation_id, parser_version)"
                    " VALUES ('snkrdunk', %s, %s, %s, %s, %s, %s)"
                    " ON DUPLICATE KEY UPDATE capture_path=VALUES(capture_path),"
                    " captured_at=VALUES(captured_at),"
                    " generation_id=VALUES(generation_id),"
                    " parser_version=VALUES(parser_version)",
                    (
                        str(item_id), digest, rel[:500], captured_at,
                        generation, parser_version[:64],
                    ),
                )
                counts["receiptsUpserted"] += 1
                receipts[item_id] = (digest, rel, fetched)
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    # --- rebind from the provider master ----------------------------------
    results: list[tuple[str, str, str]] = []
    updates: list[tuple[str, str, str | None, str | None, dict | None]] = []
    for row in snk_bindings:
        iid_str = str(row["iid"])
        status = row["match_status"]
        if not iid_str.isdigit() or status == "rejected":
            results.append((iid_str, status, ""))
            continue
        item_id = int(iid_str)
        payload = rows_by_id.get(item_id)
        receipt = receipts.get(item_id)
        if payload is None or payload.get("error") or receipt is None:
            counts["bindingsWithoutPayload"] += 1
            results.append((iid_str, status, ""))
            continue
        if not payload.get("quantity_variant_id"):
            counts["notAcceptable"] += 1  # no 1-card PSA10 variant resolved
            results.append((iid_str, status, ""))
            continue
        master = (payload.get("source_payload") or {}).get("master") or {}
        master_name = str(master.get("name") or "")
        localized = str(master.get("localizedName") or "")
        product_number = str(payload.get("product_number") or "").strip()
        language = _snk_language(master_name, localized)
        tcg = _snk_tcg(master_name, localized)
        claim = _snk_collector_claim(master_name, localized, product_number)
        pseudo_fp = {
            "cardNumber": _snk_claim_number(claim),
            "derivedLanguage": language,
            "setName": f"{master_name} {localized}",
        }
        conflicts = _fingerprint_variant_conflicts(pseudo_fp, row)
        # The bracket designation carries the provider's own set assertion
        # ("[S-P 227]" -> set "S-P"). When it equals the variant's set code,
        # that claim supersedes the noisy product-title-vs-set-name token
        # comparison (which can never contain era names like "SWSH").
        if snk_claim_set_agrees(claim, row):
            conflicts = [
                c for c in conflicts
                if not c.startswith("set:") and not c.startswith("set_code:")
            ]
        if tcg and str(row["tcg_code"] or "") and tcg != str(row["tcg_code"]):
            conflicts.append(f"tcg:{tcg}!={row['tcg_code']}")
        if not claim:
            conflicts.append("product_number_missing")
        # Mirror is a distinct print: the provider's treatment slot and the
        # variant's own wording must agree, in both directions. Variant side
        # reads the print codes only — a canonical name can legitimately
        # contain the word Mirror without being the mirror parallel.
        snk_mirror = _snk_treatment_mirror(master_name, localized)
        variant_blob = " ".join((
            str(row["parallel_code"] or ""),
            str(row["printing_code"] or ""),
            str(row["v_printing_code"] or ""),
        ))
        variant_mirror = "ミラー" in variant_blob or bool(
            re.search(r"(?i)mirror", variant_blob)
        )
        if snk_mirror != variant_mirror:
            conflicts.append(f"parallel:mirror {snk_mirror}!={variant_mirror}")
        if conflicts:
            counts["hardConflicts"] += 1
            if status == "exact":
                counts["downgradedToReview"] += 1
                updates.append((iid_str, "manual_review", None, None, None))
                results.append((iid_str, "manual_review", ""))
            else:
                results.append((iid_str, status, ""))
            continue
        snk_parallel = "parallel" if (
            "パラレル" in master_name or "parallel" in localized.casefold()
        ) else ""
        variant_parallel = str(row["parallel_code"] or "")
        parallel_ok = _parallel_agrees(snk_parallel, variant_parallel)
        if not parallel_ok and not snk_parallel:
            parallel_ok = _pc_rarity_only_parallel(variant_parallel)
        if not parallel_ok:
            counts["parallelSoftMismatch"] += 1
            results.append((iid_str, status, ""))
            continue
        digest, rel, fetched = receipt
        evidence = {
            "providerClaims": {
                "tcgCode": tcg,
                "cardLanguage": language,
                "collectorNumber": claim,
                "setCode": "",
                "printingCode": "",
                "parallelCode": snk_parallel,
            },
            "evidence": {
                "type": EVIDENCE_TYPE_PROVIDER_PAGE,
                "sha256": digest,
                "path": rel,
                "canonicalUrl": f"https://snkrdunk.com/en/trading-cards/{item_id}",
                "capturedAt": fetched,
                "generation": generation,
            },
        }
        evidence_sha = sha256_bytes(canonical_json(evidence))
        if row["canonical_printing_sha256"]:
            mirror = (
                str(row["p_tcg_code"] or ""), str(row["p_card_language"] or ""),
                str(row["p_set_code"] or ""), str(row["p_collector_number"] or ""),
                str(row["printing_code"] or ""), str(row["parallel_code"] or ""),
                str(row["p_edition_code"] or ""), str(row["p_finish_code"] or ""),
            )
        else:
            mirror = (
                str(row["tcg_code"] or ""), str(row["card_language"] or ""),
                str(row["v_set_code"] or ""), str(row["collector_number"] or ""),
                str(row["v_printing_code"] or ""), "", "", "",
            )
        if status == "manual_review":
            counts["upgradedFromReview"] += 1
        counts["restampedExact"] += 1
        updates.append((iid_str, "exact", evidence_sha, claim,
                        {"evidence": evidence, "mirror": mirror}))
        results.append((iid_str, "exact", evidence_sha))

    try:
        with conn.cursor() as cursor:
            for iid, new_status, evidence_sha, claim, extra in updates:
                if extra is None:
                    cursor.execute(
                        "UPDATE catalog_source_identity SET match_status=%s"
                        " WHERE source_code='snkrdunk' AND external_entity_id=%s",
                        (new_status, iid),
                    )
                    continue
                mirror = extra["mirror"]
                cursor.execute(
                    "UPDATE catalog_source_identity SET match_status='exact',"
                    " evidence_sha256=%s, source_product_number=%s,"
                    " bind_evidence_json=%s, bound_tcg_code=%s,"
                    " bound_card_language=%s, bound_set_code=%s,"
                    " bound_collector_number=%s, bound_printing_code=%s,"
                    " bound_parallel_code=%s, bound_edition_code=%s,"
                    " bound_finish_code=%s"
                    " WHERE source_code='snkrdunk' AND external_entity_id=%s",
                    (
                        evidence_sha,
                        str(claim or "")[:64],
                        json.dumps(extra["evidence"], ensure_ascii=False,
                                   sort_keys=True),
                        mirror[0][:32], mirror[1][:8], mirror[2][:24],
                        mirror[3][:96], mirror[4][:24], mirror[5][:64],
                        mirror[6][:191], mirror[7][:64], iid,
                    ),
                )
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    counts["runReplayed"] = bool(report.get("replayed"))
    output = sha256_bytes(canonical_json(sorted(results)))
    return {"input_sha256": _snk_refresh_input_sha(ctx), "output_sha256": output,
            "counts": counts}


def _snk_refresh_input_sha(ctx: SimpleNamespace) -> str:
    """S7's input: S6's recorded output. Never the tables/files S7 mutates."""

    with ctx.conn.cursor() as cursor:
        cursor.execute(
            "SELECT output_sha256 FROM cardz_rebuild_checkpoint"
            " WHERE generation_id=%s AND stage='pc-replay'",
            (ctx.generation,),
        )
        row = cursor.fetchone()
    return sha256_bytes(canonical_json([
        "snk-refresh-input", (row or {}).get("output_sha256"),
    ]))


def _strict_price_bindings(conn) -> tuple[
    dict[int, list[str]], dict[int, list[int]], dict[int, str]
]:
    """Strict-view PC/SNK bindings plus each variant's card language (D4 key).

    Only operator_strict_source_identity rows may feed current price/sales
    (§3.6): everything outside the projection simply has no price evidence
    here, honestly."""

    pc: dict[int, list[str]] = {}
    snk: dict[int, list[int]] = {}
    lang: dict[int, str] = {}
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT si.source_code, si.external_entity_id AS eid, si.variant_id,"
            " LOWER(COALESCE(NULLIF(TRIM(p.card_language),''),"
            "       NULLIF(TRIM(v.card_language),''), '')) AS lang"
            " FROM operator_strict_source_identity si"
            " JOIN catalog_variant v ON v.id=si.variant_id"
            " JOIN catalog_printing_identity p ON p.variant_id=si.variant_id"
            " WHERE si.source_code IN ('pricecharting','snkrdunk')"
        )
        for row in cursor.fetchall():
            vid = int(row["variant_id"])
            lang.setdefault(vid, str(row["lang"] or ""))
            if row["source_code"] == "pricecharting":
                bucket = pc.setdefault(vid, [])
                if str(row["eid"]) not in bucket:
                    bucket.append(str(row["eid"]))
            elif str(row["eid"]).isdigit():
                bucket_snk = snk.setdefault(vid, [])
                if int(row["eid"]) not in bucket_snk:
                    bucket_snk.append(int(row["eid"]))
    return pc, snk, lang


def _snk_harvest_path(generation: str) -> Path:
    """S7's harvest file (complete or honest .partial), fail if neither."""

    out_path = _snk_dir(generation) / f"snk_harvest_{generation}.jsonl"
    if out_path.is_file():
        return out_path
    partial = out_path.with_suffix(out_path.suffix + ".partial")
    if partial.is_file():
        return partial
    raise SystemExit(f"S8 ABORT: snk harvest missing: {out_path}")


def _jpy_per_usd(conn) -> float:
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT rate FROM market_fx_rate_observation"
            " WHERE base_currency='USD' AND quote_currency='JPY'"
            " ORDER BY effective_date DESC, id DESC LIMIT 1"
        )
        row = cursor.fetchone()
    rate = float(row["rate"]) if row and row.get("rate") is not None else 0.0
    if rate <= 0:
        raise SystemExit("S8 ABORT: USD/JPY FX rate missing; refuse to invent conversion")
    return rate


# A quarantined price row is released only when the provider item it was
# captured from is, today, the item this card is PROVEN to be. Two writers set
# this flag and they mean different things, exactly as with match_status:
#
#   apply_verified_source_bindings  refuses one (variant, external id) pair and
#                                   quarantines that pair's rows -- a verdict.
#   psa_identity_repair             cannot resolve a card's GemRate identity and
#                                   quarantines EVERY price row on the card,
#                                   whatever provider or item it came from --
#                                   collateral, and nobody looked at these rows.
#
# The join below can only ever free the second kind. A pair a contract refused
# carries match_status='rejected' with a verdict action, rejection_is_verdict
# keeps it there, and a row whose pair is not exact never meets the ON clause.
#
# The SNK ingester has had this lane since the 834-rows-of-good-evidence
# incident, but with `p.last_run_id = <this run>` attached: a row could only be
# freed by the same run that captured it. Identity is usually proven LATER than
# capture -- that is the whole shape of a repair -- so rows quarantined on
# 2026-08-07 and proven on 2026-08-09 could never qualify, and 29 cards reached
# S12 holding thousands of price rows and zero ready ones. Freshness was never
# the safety property; the identity match is. Dropping the run clause frees
# 2,183 of 44,007 quarantined rows -- the other 91 cards stay exactly as they
# were, because their identity is still unproven or refused.
#
# operator_strict_source_identity is required on top of an exact binding so this
# can only reach pairs the operator layer has already blessed, and the FE
# eligibility view (migration 024) re-proves the exact binding and the payload
# sha independently afterwards. Releasing the flag proves nothing on its own and
# is not asked to.
_RELEASABLE_PRICE_ROWS_JOIN = """
    market_price_observation p
    INNER JOIN catalog_source_identity si
       ON si.variant_id = p.variant_id
      AND si.source_code = CASE WHEN p.source_code IN ('snk','snk_psa10')
                                THEN 'snkrdunk' ELSE p.source_code END
      AND si.external_entity_id = p.source_external_entity_id
      AND LOWER(si.match_status) = 'exact'
    INNER JOIN operator_strict_source_identity osi
       ON osi.variant_id = p.variant_id
      AND osi.source_code = si.source_code
      AND osi.external_entity_id = si.external_entity_id
"""
_RELEASABLE_PRICE_ROWS_WHERE = (
    " WHERE p.metric_status = 'quarantined' AND p.price_usd > 0"
)
# Counting and updating share one predicate so they can never answer differently.
COUNT_RELEASABLE_PRICE_ROWS_SQL = (
    "SELECT COUNT(*) AS rows_n, COUNT(DISTINCT p.variant_id) AS variants_n FROM"
    + _RELEASABLE_PRICE_ROWS_JOIN + _RELEASABLE_PRICE_ROWS_WHERE
)
RELEASE_COLLATERAL_PRICE_QUARANTINE_SQL = (
    "UPDATE" + _RELEASABLE_PRICE_ROWS_JOIN
    + " SET p.metric_status = 'ready'" + _RELEASABLE_PRICE_ROWS_WHERE
)


def release_collateral_price_quarantine(conn: Any) -> dict[str, int]:
    """Free price rows whose card has since been proven to be their item."""

    try:
        with conn.cursor() as cur:
            cur.execute(COUNT_RELEASABLE_PRICE_ROWS_SQL)
            row = cur.fetchone()
            before = {
                "rows": int(row["rows_n"]), "variants": int(row["variants_n"]),
            }
            cur.execute(RELEASE_COLLATERAL_PRICE_QUARANTINE_SQL)
            updated = int(cur.rowcount)
        if updated != before["rows"]:
            # The count and the UPDATE run the same predicate back to back. If
            # they disagree, something is writing prices underneath this stage
            # and the receipt would describe a database that never existed. The
            # decision has to be made before the commit -- aborting after it
            # keeps the half-known write and only stops the reporting.
            raise SystemExit(
                "price quarantine release counted"
                f" {before['rows']} rows but updated {updated};"
                " another writer is active -- re-run with the freeze in place"
            )
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return {"rows": updated, "variants": before["variants"]}


def stage_price_materialize(ctx: SimpleNamespace) -> dict[str, Any]:
    """S8 (§6.6 + D4): land strict-scoped PC/SNK price + sales observations
    and decide, per variant, which provider owns the current price.

    Landing only — the acceptance chains the FE reads (metric history /
    canonical metric) are S12's projection rebuild. Current-price candidates
    exist only on the exact PC page (manualonly guide, replayed + sha-gated)
    or the exact SNK PSA10 kline. D4 is language primacy: en → PC, everything
    else → SNK, fallback only when the primary side has zero data, never an
    average. Volumes stay per-source and are never summed. The per-variant
    decision lands as data/runtime/rebuild-036/price-route-<generation>.json
    (there is deliberately no bookkeeping table for it)."""

    from datetime import date as dt_date, datetime, time as dt_time, timezone
    from decimal import Decimal

    import ingest_snk_trades_sales as snk_sales
    import snk_market_data
    from pc_psa10_price_materialize import (
        LOCAL_HISTORY_CONTRACT,
        materialize_local_history,
    )
    from pc_ungraded_reference_ingest import canonical_url_from_html, source_observed_at
    from pricecharting_page_parse import parse_product_html

    conn = ctx.conn
    conn.rollback()
    generation = ctx.generation
    counts: dict[str, Any] = {
        "pcVariants": 0, "pcPagesParsed": 0, "pcParseFailures": 0,
        "pcBindingsWithoutPage": 0, "pcHistoryRows": 0,
        "pcVariantsWithHistory": 0, "pcSaleRowsSeen": 0, "pcSaleRowsNew": 0,
        "snkVariants": 0, "snkKlineCards": 0, "snkKlinePoints": 0,
        "snkSaleRowsSeen": 0, "snkSaleRowsNew": 0, "snkTradesNotPsa10": 0,
        "routePc": 0, "routeSnk": 0, "routeNone": 0, "routeFallback": 0,
    }

    pc_bind, snk_bind, lang_by_vid = _strict_price_bindings(conn)
    counts["pcVariants"] = len(pc_bind)
    counts["snkVariants"] = len(snk_bind)
    if not pc_bind and not snk_bind:
        raise SystemExit("S8 ABORT: strict view projects no PC/SNK bindings")

    # --- PC leg: replayed pages -> daily guide history + completed sales ----
    replay_dir = _pc_replay_dir(generation)
    sidecars_by_pid: dict[str, Path] = {}
    for sidecar_path in sorted(replay_dir.glob("*.json")):
        if sidecar_path.name.startswith("replay-"):
            continue
        try:
            pid_value = str(json.loads(
                sidecar_path.read_text(encoding="utf-8")
            )["pcProductId"])
        except (json.JSONDecodeError, KeyError):
            continue
        sidecars_by_pid[pid_value] = sidecar_path
    parsed_pages: dict[str, dict[str, Any]] = {}  # pid -> {parsed, sha, ...}
    failures: list[str] = []
    wanted_pids = sorted({pid for pids in pc_bind.values() for pid in pids})
    for pid in wanted_pids:
        sidecar_path = sidecars_by_pid.get(pid)
        html_path = sidecar_path.with_suffix(".html") if sidecar_path else None
        if sidecar_path is None or not html_path.is_file():
            counts["pcBindingsWithoutPage"] += 1
            continue
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
        digest = sha256_file(html_path)
        if digest != sidecar["captureSha256"]:
            raise AssertionError(f"replay file drifted from sidecar: {html_path.name}")
        html = html_path.read_text(encoding="utf-8", errors="replace")
        parsed = parse_product_html(html)
        if not parsed.get("ok"):
            counts["pcParseFailures"] += 1
            failures.append(pid)
            continue
        parsed_pages[pid] = {
            "parsed": parsed,
            "sha256": digest,
            "capturePath": str(sidecar["capturePath"]),
            "capturedAt": datetime.strptime(
                str(sidecar["capturedAtUtc"]), "%Y-%m-%dT%H:%M:%SZ"
            ),
            "sourceUrl": canonical_url_from_html(html) or "",
        }
        counts["pcPagesParsed"] += 1
    if wanted_pids and not parsed_pages:
        raise SystemExit(f"S8 ABORT: no PC replay page parsed under {replay_dir}")
    if counts["pcParseFailures"] > max(5, len(wanted_pids) // 10):
        raise SystemExit(
            f"S8 ABORT: systemic PC parse failure ({counts['pcParseFailures']}"
            f"/{len(wanted_pids)}): {failures[:10]}"
        )
    counts["pcParseFailurePids"] = failures[:20]

    def _series_points(pid: str) -> list[Any]:
        history = (parsed_pages[pid]["parsed"].get("psa10") or {}).get("history") or {}
        return [
            point for point in (history.get("series") or [])
            if isinstance(point, list) and len(point) >= 2
            and source_observed_at(point) is not None
            and isinstance(point[1], (int, float)) and point[1] > 0
        ]

    # One owner pid per variant: richest guide history wins, then lowest pid.
    pc_choice: dict[int, str] = {}
    pc_current: dict[int, dict[str, Any]] = {}
    history_rows: list[dict[str, Any]] = []
    pc_sale_candidates: list[dict[str, Any]] = []
    for vid in sorted(pc_bind):
        candidates = [pid for pid in pc_bind[vid] if pid in parsed_pages]
        if not candidates:
            continue
        chosen = sorted(
            candidates, key=lambda pid: (-len(_series_points(pid)), int(pid))
        )[0]
        pc_choice[vid] = chosen
        page = parsed_pages[chosen]
        by_day: dict[str, dict[str, Any]] = {}
        for point in _series_points(chosen):
            observed_at = source_observed_at(point)
            price = (Decimal(str(point[1])) / Decimal(100)).quantize(
                Decimal("0.000001")
            )
            payload = {
                "contract": LOCAL_HISTORY_CONTRACT,
                "source": "pricecharting",
                "variantId": vid,
                "externalEntityId": chosen,
                "method": "pricecharting_explicit_psa10_history_v1",
                "field": "VGPC.chart_data.manualonly.series",
                "sourceUrl": page["sourceUrl"],
                "artifactSha256": page["sha256"],
                "artifactPath": page["capturePath"],
                "chartPoint": [int(point[0]), str(point[1])],
            }
            by_day[observed_at.date().isoformat()] = {
                "variantId": vid,
                "externalEntityId": chosen,
                "observedDate": observed_at.date().isoformat(),
                "effectiveAt": observed_at,
                "priceUsd": str(price),
                "payloadSha256": sha256_bytes(canonical_json(payload)),
                "payload": payload,
            }
        rows = [by_day[day] for day in sorted(by_day)]
        history_rows.extend(rows)
        if rows:
            counts["pcVariantsWithHistory"] += 1
            pc_current[vid] = {
                "observedDate": rows[-1]["observedDate"],
                "priceUsd": rows[-1]["priceUsd"],
                "points": len(rows),
            }
        sales = (parsed_pages[chosen]["parsed"].get("psa10") or {}).get(
            "completed_sales"
        ) or {}
        for sale in sales.get("rows") or []:
            date_text = str(sale.get("date") or "")
            itm = str(sale.get("ebay_itm") or "")
            price_usd = sale.get("price_usd")
            if not date_text or not itm or not isinstance(price_usd, (int, float)):
                continue
            if price_usd <= 0:
                continue
            # Both this leg and the daily c11 lane land the same physical sales,
            # so the fingerprint recipe lives in exactly one module. This used to
            # quantize to Decimal("0.01") while c11 quantized to six places, and
            # the two spellings of the same $65.00 produced two fingerprints and
            # two rows for one eBay item id.
            unit = pc_sale_price_text(price_usd)
            fingerprint = pc_sale_fingerprint(chosen, date_text, price_usd, itm)
            payload = {
                "transport": "pricecharting_replay_036",
                "pc_product_id": int(chosen),
                "ebay_itm": itm,
                "title": str(sale.get("title") or "")[:200],
                "date": date_text,
                "price_usd": float(unit),
                "variant_id": vid,
                "artifact_sha256": page["sha256"],
            }
            pc_sale_candidates.append({
                "variantId": vid,
                "externalEntityId": chosen,
                "fingerprint": fingerprint,
                "soldAt": datetime.combine(
                    dt_date.fromisoformat(date_text), dt_time.min
                ),
                "sourceDateText": date_text[:100],
                "fetchedAt": page["capturedAt"],
                "unitPriceUsd": unit,
                "payloadSha256": sha256_bytes(canonical_json(payload)),
                "listingItemId": itm[:32],
                "listingUrl": str(sale.get("ebay_url") or "")[:512],
                "listingTitle": str(sale.get("title") or "")[:255],
            })
    counts["pcHistoryRows"] = len(history_rows)
    counts["pcSaleRowsSeen"] = len(pc_sale_candidates)
    materialize_local_history(conn, history_rows)  # commits internally

    # --- PC completed sales (c11 shape: fingerprint-deduped, quantity=1) ----
    try:
        with conn.cursor() as cursor:
            existing: set[tuple[str, str]] = set()
            pairs = [
                (row["externalEntityId"], row["fingerprint"])
                for row in pc_sale_candidates
            ]
            for offset in range(0, len(pairs), 400):
                chunk = pairs[offset:offset + 400]
                placeholders = ",".join(["(%s,%s)"] * len(chunk))
                params: list[str] = []
                for pair in chunk:
                    params.extend(pair)
                cursor.execute(
                    "SELECT external_entity_id, transaction_fingerprint"
                    " FROM market_sale_observation"
                    " WHERE source_code='pricecharting'"
                    f" AND (external_entity_id, transaction_fingerprint) IN ({placeholders})",
                    params,
                )
                existing.update(
                    (str(row["external_entity_id"]), str(row["transaction_fingerprint"]))
                    for row in cursor.fetchall()
                )
            new_sales = [
                row for row in pc_sale_candidates
                if (row["externalEntityId"], row["fingerprint"]) not in existing
            ]
            counts["pcSaleRowsNew"] = len(new_sales)
            if new_sales:
                manifest = sha256_bytes("\n".join(
                    sorted(row["fingerprint"] for row in pc_sale_candidates)
                ).encode("utf-8"))
                now = datetime.now(timezone.utc).replace(tzinfo=None)
                cursor.execute(
                    "INSERT INTO market_ingest_run"
                    " (run_key, source_code, ingest_mode, effective_at,"
                    "  payload_sha256, manifest_sha256, status, observed_count,"
                    "  started_at)"
                    " VALUES (%s, 'pricecharting', 'backfill', %s, %s, %s,"
                    "  'running', %s, %s)"
                    " ON DUPLICATE KEY UPDATE id=LAST_INSERT_ID(id),"
                    "  status='running', started_at=VALUES(started_at)",
                    (
                        sha256_bytes(f"rebuild036_pc_sales|{generation}|{manifest}".encode("utf-8")),
                        now, manifest, manifest, len(new_sales), now,
                    ),
                )
                run_id = int(cursor.lastrowid)
                rows = [
                    (
                        run_id, row["variantId"], "pricecharting",
                        row["externalEntityId"], row["fingerprint"], "psa", "10",
                        row["soldAt"], row["sourceDateText"], row["fetchedAt"],
                        "exact_date", row["unitPriceUsd"], row["unitPriceUsd"],
                        row["payloadSha256"], "partial",
                        row["listingItemId"], row["listingUrl"], row["listingTitle"],
                    )
                    for row in new_sales
                ]
                for offset in range(0, len(rows), 400):
                    cursor.executemany(
                        "INSERT INTO market_sale_observation"
                        " (run_id, variant_id, source_code, external_entity_id,"
                        "  transaction_fingerprint, grader_code, grade_label,"
                        "  sold_at, source_date_text, fetched_at,"
                        "  timestamp_quality, unit_price_usd, quantity,"
                        "  transaction_value_usd, source_payload_sha256,"
                        "  coverage_status, listing_item_id, listing_url,"
                        "  listing_title)"
                        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,"
                        "  %s, 1, %s, %s, %s, %s, %s, %s)",
                        rows[offset:offset + 400],
                    )
                cursor.execute(
                    "UPDATE market_ingest_run SET status='completed',"
                    " accepted_count=%s, completed_at=%s WHERE id=%s",
                    (len(new_sales), datetime.now(timezone.utc).replace(tzinfo=None), run_id),
                )
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    # S12 accepts sale rows by fingerprint from this manifest — never by
    # run_id. Pre-freeze writers landed the same physical sales under other
    # fingerprint recipes; those rows stay unaccepted (invisible to the FE)
    # instead of double-counting.
    runtime_dir = ROOT / "data" / "runtime" / "rebuild-036"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    pc_manifest_path = runtime_dir / f"sales-manifest-pc-{generation}.jsonl"
    pc_manifest_blob = b"".join(
        canonical_json({
            "fingerprint": row["fingerprint"],
            "variantId": row["variantId"],
            "externalEntityId": row["externalEntityId"],
            "soldDate": row["sourceDateText"],
            "unitPriceUsd": row["unitPriceUsd"],
        }) + b"\n"
        for row in sorted(pc_sale_candidates, key=lambda row: row["fingerprint"])
    )
    pc_manifest_path.write_bytes(pc_manifest_blob)
    counts["pcSalesManifest"] = pc_manifest_path.relative_to(ROOT).as_posix()

    # --- SNK leg: kline prices via the shared ingester, strict-scoped -------
    harvest_path = _snk_harvest_path(generation)
    strict_item_map = {
        item: vid for vid, items in snk_bind.items() for item in items
    }
    kline_stats = snk_market_data.ingest_kline_jsonls(
        [harvest_path],
        run_key=f"rebuild036_snk_kline|{generation}",
        ingest_mode="backfill",
        conn=conn,
        item_to_variant=strict_item_map,
    )
    counts["snkKlineCards"] = int(kline_stats.get("cardsAccepted") or 0)
    counts["snkKlinePoints"] = int(kline_stats.get("pricePoints") or 0)

    rows_by_item: dict[int, dict[str, Any]] = {}
    with harvest_path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            item_id = row.get("item_id")
            if isinstance(item_id, int) and not row.get("error"):
                rows_by_item[item_id] = row

    fx = _jpy_per_usd(conn)
    snk_current: dict[int, dict[str, Any]] = {}
    for vid in sorted(snk_bind):
        best: tuple[int, int, list[tuple[str, float]]] | None = None
        for item in snk_bind[vid]:
            row = rows_by_item.get(item)
            if row is None:
                continue
            points = snk_market_data._valid_kline_points(row)
            if not points:
                continue
            key = (-len(points), item)
            if best is None or key < (-len(best[2]), best[1]):
                best = (vid, item, points)
        if best is None:
            continue
        _, item, points = best
        day, price_jpy = max(points, key=lambda pt: pt[0])
        snk_current[vid] = {
            "observedDate": day,
            "priceUsd": str(round(price_jpy / fx, 6)),
            "priceJpy": price_jpy,
            "points": len(points),
            "itemId": item,
        }

    # --- SNK unitized PSA10 trades -> sale observations ---------------------
    snk_sale_rows: list[tuple[Any, ...]] = []
    seen_fingerprints: set[str] = set()
    for item, vid in sorted(strict_item_map.items()):
        row = rows_by_item.get(item)
        if row is None:
            continue
        fetched_raw = str(row.get("fetched_at") or "")
        try:
            fetched_at = datetime.strptime(
                fetched_raw, "%Y-%m-%dT%H:%M:%S%z"
            ).astimezone(timezone.utc).replace(tzinfo=None)
        except ValueError:
            fetched_at = None
        for trade in row.get("recent_trades") or []:
            if not isinstance(trade, dict):
                continue
            title = str(trade.get("title") or "")
            if not snk_sales.is_psa10(title):
                counts["snkTradesNotPsa10"] += 1
                continue
            price_jpy = trade.get("price")
            if not isinstance(price_jpy, (int, float)) or price_jpy <= 0:
                continue
            qty = snk_sales.parse_qty(str(trade.get("label") or "1枚"))
            sold_raw = str(trade.get("soldAt") or trade.get("sold_at") or "")
            try:
                sold_at = datetime.fromisoformat(
                    sold_raw.replace("Z", "+00:00")
                ).replace(tzinfo=None)
            except ValueError:
                continue
            fingerprint = snk_sales.fingerprint(
                item, sold_raw, float(price_jpy), qty, title
            )
            if fingerprint in seen_fingerprints:
                continue
            seen_fingerprints.add(fingerprint)
            unit_usd = round(float(price_jpy) / qty / fx, 6)
            payload = {
                "itemId": item, "priceJpy": price_jpy, "qty": qty,
                "title": title, "soldAt": sold_raw,
            }
            snk_sale_rows.append(
                (
                    vid, "snkrdunk", str(item), fingerprint, "psa", "10",
                    sold_at, sold_raw[:100],
                    fetched_at or sold_at, "exact_date", unit_usd, qty,
                    round(unit_usd * qty, 6),
                    hashlib.sha256(
                        json.dumps(payload, sort_keys=True).encode("utf-8")
                    ).hexdigest(),
                    "partial",
                )
            )
    counts["snkSaleRowsSeen"] = len(snk_sale_rows)
    try:
        with conn.cursor() as cursor:
            existing_snk: set[str] = set()
            fingerprints = [str(row[3]) for row in snk_sale_rows]
            for offset in range(0, len(fingerprints), 400):
                chunk = fingerprints[offset:offset + 400]
                placeholders = ",".join(["%s"] * len(chunk))
                cursor.execute(
                    "SELECT transaction_fingerprint FROM market_sale_observation"
                    " WHERE source_code='snkrdunk'"
                    f" AND transaction_fingerprint IN ({placeholders})",
                    chunk,
                )
                existing_snk.update(
                    str(row["transaction_fingerprint"]) for row in cursor.fetchall()
                )
            counts["snkSaleRowsNew"] = sum(
                1 for row in snk_sale_rows if str(row[3]) not in existing_snk
            )
            if snk_sale_rows:
                manifest = sha256_bytes("\n".join(sorted(fingerprints)).encode("utf-8"))
                now = datetime.now(timezone.utc).replace(tzinfo=None)
                cursor.execute(
                    "INSERT INTO market_ingest_run"
                    " (run_key, source_code, ingest_mode, effective_at,"
                    "  payload_sha256, manifest_sha256, status, observed_count,"
                    "  started_at)"
                    " VALUES (%s, 'snkrdunk', 'full', %s, %s, %s, 'running',"
                    "  %s, %s)"
                    " ON DUPLICATE KEY UPDATE id=LAST_INSERT_ID(id),"
                    "  status='running', started_at=VALUES(started_at)",
                    (
                        sha256_bytes(f"rebuild036_snk_trades|{generation}|{manifest}".encode("utf-8")),
                        now, manifest, manifest, len(snk_sale_rows), now,
                    ),
                )
                run_id = int(cursor.lastrowid)
                rows = [(run_id, *row) for row in snk_sale_rows]
                for offset in range(0, len(rows), 400):
                    cursor.executemany(
                        "INSERT INTO market_sale_observation"
                        " (run_id, variant_id, source_code, external_entity_id,"
                        "  transaction_fingerprint, grader_code, grade_label,"
                        "  sold_at, source_date_text, fetched_at,"
                        "  timestamp_quality, unit_price_usd, quantity,"
                        "  transaction_value_usd, source_payload_sha256,"
                        "  coverage_status)"
                        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,"
                        "  %s, %s, %s, %s, %s)"
                        " ON DUPLICATE KEY UPDATE run_id=VALUES(run_id),"
                        "  variant_id=VALUES(variant_id),"
                        "  unit_price_usd=VALUES(unit_price_usd),"
                        "  quantity=VALUES(quantity),"
                        "  transaction_value_usd=VALUES(transaction_value_usd),"
                        "  sold_at=VALUES(sold_at), fetched_at=VALUES(fetched_at),"
                        "  source_payload_sha256=VALUES(source_payload_sha256),"
                        "  coverage_status=VALUES(coverage_status)",
                        rows[offset:offset + 400],
                    )
                cursor.execute(
                    "UPDATE market_ingest_run SET status='completed',"
                    " observed_count=%s, accepted_count=%s, completed_at=%s"
                    " WHERE id=%s",
                    (
                        len(snk_sale_rows), len(snk_sale_rows),
                        datetime.now(timezone.utc).replace(tzinfo=None), run_id,
                    ),
                )
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    snk_manifest_path = runtime_dir / f"sales-manifest-snk-{generation}.jsonl"
    snk_manifest_blob = b"".join(
        canonical_json({
            "fingerprint": str(row[3]),
            "variantId": int(row[0]),
            "itemId": str(row[2]),
            "soldAt": str(row[7]),
            "unitPriceUsd": row[10],
            "quantity": row[11],
        }) + b"\n"
        for row in sorted(snk_sale_rows, key=lambda row: str(row[3]))
    )
    snk_manifest_path.write_bytes(snk_manifest_blob)
    counts["snkSalesManifest"] = snk_manifest_path.relative_to(ROOT).as_posix()

    released = release_collateral_price_quarantine(conn)
    counts["priceQuarantineReleased"] = released["rows"]
    counts["priceQuarantineReleasedVariants"] = released["variants"]

    # --- D4 route decision, per variant -------------------------------------
    pc_sale_count: dict[int, int] = {}
    for row in pc_sale_candidates:
        pc_sale_count[row["variantId"]] = pc_sale_count.get(row["variantId"], 0) + 1
    snk_sale_count: dict[int, int] = {}
    for row in snk_sale_rows:
        snk_sale_count[int(row[0])] = snk_sale_count.get(int(row[0]), 0) + 1

    routes: list[dict[str, Any]] = []
    for vid in sorted(set(pc_bind) | set(snk_bind)):
        language = lang_by_vid.get(vid, "")
        pc_side = pc_current.get(vid)
        snk_side = snk_current.get(vid)
        primary = "pricecharting" if language == "en" else "snkrdunk"
        fallback_used = False
        if primary == "pricecharting":
            winner, loser = pc_side, snk_side
            winner_code, loser_code = "pricecharting", "snkrdunk"
        else:
            winner, loser = snk_side, pc_side
            winner_code, loser_code = "snkrdunk", "pricecharting"
        if winner is not None:
            route = winner_code
            current = winner
            reason = f"language_{language or 'unknown'}_primary"
        elif loser is not None and not (loser_code == "pricecharting" and language != "en"):
            # A PriceCharting price is only ever accepted for an English card
            # (_activation_accept_history gates on pi.card_language='en'), so
            # falling a non-English card back onto PC would route it to a price
            # S12 can never accept and abort activation on missing coverage.
            # SNKRDUNK is accepted for every language, so that fallback stands.
            route = loser_code
            current = loser
            fallback_used = True
            reason = "primary_zero_data"
        else:
            route = "none"
            current = None
            reason = "no_current_price_evidence"
        routes.append({
            "variantId": vid,
            "cardLanguage": language,
            "route": route,
            "fallbackUsed": fallback_used,
            "reason": reason,
            "current": current,
            "pcExternalIds": pc_bind.get(vid, []),
            "pcChosenExternalId": pc_choice.get(vid),
            "pcHistoryPoints": (pc_side or {}).get("points", 0),
            "pcSaleRows": pc_sale_count.get(vid, 0),
            "snkItemIds": snk_bind.get(vid, []),
            "snkChosenItemId": (snk_side or {}).get("itemId"),
            "snkKlinePoints": (snk_side or {}).get("points", 0),
            "snkSaleRows": snk_sale_count.get(vid, 0),
        })
        if route == "pricecharting":
            counts["routePc"] += 1
        elif route == "snkrdunk":
            counts["routeSnk"] += 1
        else:
            counts["routeNone"] += 1
        if fallback_used:
            counts["routeFallback"] += 1

    artifact = {
        "contract": "price_route_d4_v1",
        "generation": generation,
        "fxJpyPerUsd": fx,
        "rules": {
            "primary": "en->pricecharting, else->snkrdunk",
            "fallback": "only when primary has zero current-price evidence,"
                        " and never onto pricecharting for a non-en card",
            "never": ["averaging", "summed volumes", "non-strict identities"],
        },
        "pcSalesManifestSha256": sha256_bytes(pc_manifest_blob),
        "snkSalesManifestSha256": sha256_bytes(snk_manifest_blob),
        "routes": routes,
    }
    artifact_blob = canonical_json(artifact)
    artifact_path = ROOT / "data" / "runtime" / "rebuild-036" / (
        f"price-route-{generation}.json"
    )
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_bytes(artifact_blob)
    counts["routeArtifact"] = artifact_path.relative_to(ROOT).as_posix()
    counts["routeArtifactSha256"] = sha256_bytes(artifact_blob)

    return {
        "input_sha256": _price_materialize_input_sha(ctx),
        "output_sha256": sha256_bytes(canonical_json([
            "price-materialize-output", sha256_bytes(artifact_blob),
        ])),
        "counts": counts,
    }


def _price_materialize_input_sha(ctx: SimpleNamespace) -> str:
    """S8's input: S7's recorded output. Never the tables/files S8 mutates."""

    with ctx.conn.cursor() as cursor:
        cursor.execute(
            "SELECT output_sha256 FROM cardz_rebuild_checkpoint"
            " WHERE generation_id=%s AND stage='snk-refresh'",
            (ctx.generation,),
        )
        row = cursor.fetchone()
    return sha256_bytes(canonical_json([
        "price-materialize-input", (row or {}).get("output_sha256"),
    ]))


# ---------------------------------------------------------------------------
# Stage 9: image-bind (§6.6)
# ---------------------------------------------------------------------------

_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_PC_IMAGE_URL_RE = re.compile(
    r"https://storage\.googleapis\.com/images\.pricecharting\.com/([A-Za-z0-9]+)/1600\.jpg"
)
# product_details cover block = the product front; tolerant of attribute noise
# between the class anchor and the img src.
_PC_COVER_IMAGE_RE = re.compile(
    r'class="cover"[\s\S]{0,400}?'
    r"https://storage\.googleapis\.com/images\.pricecharting\.com/([A-Za-z0-9]+)/240\.jpg"
)
_PC_IMAGE_TRANSFORM = {
    "contract": "pc-product-image-transform-v1",
    "canvas": {"width": 429, "height": 600},
    "alphaPreserved": True,
    "roundedCorners": False,
    "encoder": {"format": "webp", "quality": 92},
}


def _market_assets_dir() -> Path:
    return ROOT / "data" / "public" / "market-assets"


def _pc_image_asset_dir() -> Path:
    return ROOT / "data" / "runtime" / "rebuild-036" / "pc-image-assets"


def _fe_image_state(conn, variant_ids: list[int]) -> dict[int, dict[str, Any]]:
    """FE 兩條 image query 原樣鏡射（live-db-snapshot.ts）。S9 gate 以 FE 為準。"""

    if not variant_ids:
        return {}
    ph = ",".join(["%s"] * len(variant_ids))
    state: dict[int, dict[str, Any]] = {}
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT variant_id, canonical_image_content_sha256 AS sha"
            f" FROM operator_canonical_image_projection WHERE variant_id IN ({ph})",
            variant_ids,
        )
        for row in cursor.fetchall():
            state[int(row["variant_id"])] = {
                "via": "projection", "contentSha256": str(row["sha"] or ""),
            }
        cursor.execute(
            "SELECT f.variant_id, a.content_sha256 AS sha"
            " FROM operator_binding_freeze f"
            " INNER JOIN market_canonical_image_acceptance ca"
            "   ON ca.id=f.canonical_image_acceptance_id AND ca.variant_id=f.variant_id"
            " INNER JOIN market_image_asset a"
            "   ON a.id=ca.image_asset_id AND a.variant_id=ca.variant_id"
            "  AND a.image_kind='raw_front' AND a.content_sha256=f.content_sha256"
            " INNER JOIN market_image_qc q"
            "   ON q.image_asset_id=a.id"
            "  AND q.id=(SELECT q2.id FROM market_image_qc q2"
            "            WHERE q2.image_asset_id=a.id"
            "            ORDER BY q2.checked_at DESC,q2.id DESC LIMIT 1)"
            "  AND q.public_allowed=1 AND q.raw_front_confirmed=1 AND q.card_number_match=1"
            "  AND q.language_match=1 AND q.tcg_match=1"
            "  AND q.semantic_match_status IN ('human_or_vision_confirmed','accepted_freeze')"
            f" WHERE f.variant_id IN ({ph})"
            "   AND f.freeze_kind='image' AND f.acceptance_status='accepted'"
            "   AND f.canonical_image_acceptance_id IS NOT NULL"
            "   AND f.content_sha256 REGEXP '^[0-9a-f]{64}$'"
            "   AND NOT EXISTS (SELECT 1 FROM market_canonical_image_acceptance newer"
            "                   WHERE newer.supersedes_acceptance_id=ca.id)",
            variant_ids,
        )
        for row in cursor.fetchall():
            state.setdefault(int(row["variant_id"]), {
                "via": "freeze_fallback", "contentSha256": str(row["sha"] or ""),
            })
    return state


def _image_rejections(conn, variant_ids: list[int]) -> dict[int, set[str]]:
    """人手 reject 過嘅 (variant, content) 永不復活。"""

    if not variant_ids:
        return {}
    ph = ",".join(["%s"] * len(variant_ids))
    rejected: dict[int, set[str]] = {}
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT variant_id, content_sha256 FROM market_image_rejection_registry"
            f" WHERE variant_id IN ({ph})",
            variant_ids,
        )
        for row in cursor.fetchall():
            rejected.setdefault(int(row["variant_id"]), set()).add(
                str(row["content_sha256"])
            )
    return rejected


def _exact_identity_evidence(conn, source_code: str, variant_ids: list[int],
                             ) -> dict[tuple[int, str], str]:
    """(variant, external) -> catalog evidence sha（exact rows only）。"""

    if not variant_ids:
        return {}
    ph = ",".join(["%s"] * len(variant_ids))
    evidence: dict[tuple[int, str], str] = {}
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT variant_id, external_entity_id, evidence_sha256"
            " FROM catalog_source_identity"
            " WHERE source_code=%s AND LOWER(match_status)='exact'"
            f"  AND variant_id IN ({ph})",
            [source_code, *variant_ids],
        )
        for row in cursor.fetchall():
            sha = str(row["evidence_sha256"] or "")
            if _HEX64_RE.fullmatch(sha):
                evidence[(int(row["variant_id"]), str(row["external_entity_id"]))] = sha
    return evidence


def _trio_missing(sha: str) -> list[str]:
    assets = _market_assets_dir()
    return [
        name for name in (f"{sha}.webp", f"{sha}_200.webp", f"{sha}_600.webp")
        if not (assets / name).is_file()
    ]


def _materialize_trio(sha: str, base_bytes: bytes | None) -> str | None:
    """確保 public webp 三件套存在。回 None=OK，否則 fail reason。

    base 來源優先序：public 已有 → runtime snk-en-assets → rebuild pc-image-assets
    → 傳入 bytes。任何 bytes 必須 sha 相符——唔准靜默改內容（self-heal 教訓）。
    衍生圖只做純 resize（encode_derivatives），唔 normalize 唔補圓角。
    """

    import g10_public_snapshot as g10
    from PIL import Image

    assets = _market_assets_dir()
    assets.mkdir(parents=True, exist_ok=True)
    base_path = assets / f"{sha}.webp"
    if base_path.is_file():
        data = base_path.read_bytes()
        if sha256_bytes(data) != sha:
            return "public_base_sha_mismatch"
    else:
        data = None
        for candidate in (
            ROOT / "data" / "runtime" / "operator" / "snk-en-assets" / f"{sha}.webp",
            _pc_image_asset_dir() / f"{sha}.webp",
        ):
            if candidate.is_file():
                blob = candidate.read_bytes()
                if sha256_bytes(blob) == sha:
                    data = blob
                    break
        if data is None and base_bytes is not None and sha256_bytes(base_bytes) == sha:
            data = base_bytes
        if data is None:
            return "no_base_bytes"
        base_path.write_bytes(data)
    if not _trio_missing(sha):
        return None
    with Image.open(io.BytesIO(data)) as opened:
        image = opened.copy()
    for suffix, blob in g10.encode_derivatives(image).items():
        path = assets / f"{sha}_{suffix}.webp"
        if not path.is_file():
            path.write_bytes(blob)
    return None


def _download_pc_image(url: str) -> bytes:
    import time
    import urllib.request

    request = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    })
    last: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                data = response.read()
            if data:
                return data
            last = ValueError("empty body")
        except Exception as exc:  # noqa: BLE001
            last = exc
        time.sleep(1.0 + attempt)
    raise RuntimeError(f"pc image download failed: {url}: {last}")


def _process_pc_product_image(raw: bytes) -> dict[str, Any]:
    """PC 1600.jpg → 統一畫布 webp。鏡 process_snk_default_image，換 PC provenance 標籤。"""

    import g10_public_snapshot as g10
    from PIL import Image
    from native_image_resolver import normalize_card_canvas

    if not raw:
        raise ValueError("PC product image is empty")
    with Image.open(io.BytesIO(raw)) as opened:
        normalized = normalize_card_canvas(opened.copy())
    content = g10._save_webp(normalized, 92)
    return {
        "content": content,
        "contentSha256": sha256_bytes(content),
        "width": normalized.width,
        "height": normalized.height,
        "transformSha256": sha256_bytes(canonical_json(_PC_IMAGE_TRANSFORM)),
        "qcVersion": "pc-product-v1",
    }


def _write_pc_image_asset(processed: Mapping[str, Any]) -> str:
    """Content-addressed 私有 asset 檔（atomic，鏡 _write_snk_en_asset）。"""

    asset_dir = _pc_image_asset_dir()
    asset_dir.mkdir(parents=True, exist_ok=True)
    path = asset_dir / f"{processed['contentSha256']}.webp"
    if path.is_file():
        if sha256_bytes(path.read_bytes()) != processed["contentSha256"]:
            raise RuntimeError(f"pc asset hash mismatch: {path}")
        return path.relative_to(ROOT).as_posix()
    temporary = path.with_name(f".{path.name}.next")
    temporary.write_bytes(processed["content"])
    if sha256_bytes(temporary.read_bytes()) != processed["contentSha256"]:
        temporary.unlink(missing_ok=True)
        raise RuntimeError(f"pc asset write hash mismatch: {path}")
    temporary.replace(path)
    return path.relative_to(ROOT).as_posix()


def _persist_pc_product_image(cursor, *, variant_id: int, pid: str, image_url: str,
                              page_sha: str, canonical_url: str, downloaded_sha: str,
                              identity_evidence: str, processed: Mapping[str, Any],
                              observed_at, completed_at) -> None:
    """PC 圖四表鏈：asset → pointer → qc → acceptance(fallback 分支) → freeze。

    Projection view fallback 分支要求：storefront_lineage_id NULL、
    fallback_source_path 非空且唔含 'snkrdunk'、lineage==fallback_version(64-hex)、
    observed_at 非空、freeze source_code 唔係 snk 系。
    """

    content_sha = str(processed["contentSha256"])
    private_key = _write_pc_image_asset(processed)
    source_version = sha256_bytes(canonical_json({
        "pageCaptureSha256": page_sha,
        "downloadedBytesSha256": downloaded_sha,
        "transformSha256": processed["transformSha256"],
    }))
    cursor.execute(
        "INSERT INTO market_image_asset"
        " (variant_id,image_kind,content_sha256,private_object_key,mime_type,"
        "  width_px,height_px,source_version_sha256,captured_at)"
        " VALUES (%s,'raw_front',%s,%s,'image/webp',%s,%s,%s,%s)"
        " ON DUPLICATE KEY UPDATE"
        "  private_object_key=VALUES(private_object_key),mime_type=VALUES(mime_type),"
        "  width_px=VALUES(width_px),height_px=VALUES(height_px),"
        "  source_version_sha256=VALUES(source_version_sha256),"
        "  captured_at=GREATEST(captured_at,VALUES(captured_at))",
        (variant_id, content_sha, private_key, processed["width"],
         processed["height"], source_version, completed_at),
    )
    cursor.execute(
        "SELECT id FROM market_image_asset"
        " WHERE variant_id=%s AND image_kind='raw_front' AND content_sha256=%s"
        " LIMIT 1",
        (variant_id, content_sha),
    )
    asset = cursor.fetchone()
    if not asset:
        raise RuntimeError(f"pc asset row missing: {variant_id}:{pid}")
    asset_id = int(asset["id"])
    cursor.execute(
        "INSERT INTO market_image_source_pointer"
        " (variant_id,image_kind,remote_url_sha256,source_path,"
        "  source_version_sha256,public_allowed,observed_at)"
        " VALUES (%s,'raw_front',%s,%s,%s,1,%s)"
        " ON DUPLICATE KEY UPDATE"
        "  remote_url_sha256=VALUES(remote_url_sha256),"
        "  source_path=VALUES(source_path),public_allowed=1,"
        "  observed_at=GREATEST(observed_at,VALUES(observed_at))",
        (variant_id, sha256_bytes(image_url.encode("utf-8")), image_url,
         source_version, observed_at),
    )
    cursor.execute(
        "INSERT INTO market_image_qc"
        " (image_asset_id,semantic_match_status,card_number_match,language_match,"
        "  tcg_match,raw_front_confirmed,public_allowed,rejection_reason,"
        "  checked_at,qc_version)"
        " VALUES (%s,'accepted_freeze',1,1,1,1,1,NULL,%s,%s)"
        " ON DUPLICATE KEY UPDATE"
        "  semantic_match_status='accepted_freeze',card_number_match=1,"
        "  language_match=1,tcg_match=1,raw_front_confirmed=1,"
        "  public_allowed=1,rejection_reason=NULL,checked_at=VALUES(checked_at)",
        (asset_id, completed_at, processed["qcVersion"]),
    )
    lineage_payload = {
        "contract": "pc-product-image-lineage-v1",
        "variantId": variant_id,
        "sourceCode": "pricecharting",
        "pcProductId": pid,
        "productUrl": canonical_url,
        "pageCaptureSha256": page_sha,
        "imageUrl": image_url,
        "downloadedBytesSha256": downloaded_sha,
        "processedContentSha256": content_sha,
        "transformSha256": processed["transformSha256"],
        "identityEvidenceSha256": identity_evidence,
        "sourceObservedAt": observed_at.isoformat(timespec="microseconds"),
    }
    lineage_sha = sha256_bytes(canonical_json(lineage_payload))
    acceptance_evidence = sha256_bytes(canonical_json({
        "contract": "canonical-pc-image-acceptance-v1",
        "lineageSha256": lineage_sha,
        "imageContentSha256": content_sha,
        "qcVersion": processed["qcVersion"],
    }))
    cursor.execute(
        "SELECT ca.id, ca.lineage_sha256 FROM market_canonical_image_acceptance ca"
        " WHERE ca.variant_id=%s"
        "  AND NOT EXISTS (SELECT 1 FROM market_canonical_image_acceptance newer"
        "                  WHERE newer.supersedes_acceptance_id=ca.id)"
        " ORDER BY ca.accepted_at DESC, ca.id DESC LIMIT 1",
        (variant_id,),
    )
    current = cursor.fetchone()
    if current and str(current["lineage_sha256"]) == lineage_sha:
        acceptance_id = int(current["id"])
    else:
        cursor.execute(
            "INSERT INTO market_canonical_image_acceptance"
            " (variant_id,storefront_lineage_id,image_asset_id,"
            "  fallback_source_path,fallback_source_version_sha256,"
            "  fallback_source_observed_at,lineage_sha256,evidence_sha256,"
            "  accepted_by,accepted_at,supersedes_acceptance_id)"
            " VALUES (%s,NULL,%s,%s,%s,%s,%s,%s,'rebuild_036:image_bind',%s,%s)",
            (variant_id, asset_id, f"pricecharting:{pid}:{image_url}",
             lineage_sha, observed_at, lineage_sha, acceptance_evidence,
             completed_at, int(current["id"]) if current else None),
        )
        acceptance_id = int(cursor.lastrowid)
    cursor.execute(
        "INSERT INTO operator_binding_freeze"
        " (variant_id,freeze_kind,source_code,external_entity_id,content_sha256,"
        "  canonical_image_acceptance_id,accepted_lineage_sha256,acceptance_status,"
        "  actor,evidence_sha256,note,accepted_at)"
        " VALUES (%s,'image','pricecharting',%s,%s,%s,%s,'accepted',"
        "  'rebuild_036:image_bind',%s,'exact PC product page image',%s)"
        " ON DUPLICATE KEY UPDATE"
        "  external_entity_id=VALUES(external_entity_id),"
        "  content_sha256=VALUES(content_sha256),"
        "  canonical_image_acceptance_id=VALUES(canonical_image_acceptance_id),"
        "  accepted_lineage_sha256=VALUES(accepted_lineage_sha256),"
        "  acceptance_status='accepted',actor=VALUES(actor),"
        "  evidence_sha256=VALUES(evidence_sha256),note=VALUES(note),"
        "  accepted_at=VALUES(accepted_at)",
        (variant_id, pid, content_sha, acceptance_id, lineage_sha,
         acceptance_evidence, completed_at),
    )


def stage_image_bind(ctx: SimpleNamespace) -> dict[str, Any]:
    """S9 §6.6: SNK EN primaryMedia 優先，PC 產品圖次之，兩者都冇 = 誠實唔 product-ready.

    Gate = FE 自己兩條 query（projection + freeze fallback）+ public webp 三件套。
    禁 Kado/TCGplayer/AI/無來源圖；rejection registry 內容永不復活。
    """

    from datetime import datetime, timezone

    conn = ctx.conn
    generation = ctx.generation
    from concurrent.futures import ThreadPoolExecutor

    from collect_control import (
        _local_snk_default_bytes,
        _local_snk_master_cache,
        _persist_prepared_snk_en,
        _prepare_snk_en_target,
        _require_migration_029,
    )
    from pc_ungraded_reference_ingest import canonical_url_from_html
    from snkrdunk_bulk import SnkrdunkApiPool

    counts: dict[str, Any] = {
        "alreadyOk": 0, "trioFilled": 0, "trioFillFailed": 0,
        "snkAttempted": 0, "snkBound": 0, "snkFailed": 0, "snkRejected": 0,
        "pcAttempted": 0, "pcBound": 0, "pcFailed": 0, "pcRejected": 0,
    }
    pc_bind, snk_bind, _lang = _strict_price_bindings(conn)
    universe = sorted(set(pc_bind) | set(snk_bind))
    counts["universe"] = len(universe)
    if not universe:
        raise SystemExit("S9 ABORT: strict view projects no variants")
    with conn.cursor() as cursor:
        _require_migration_029(cursor)

    rejections = _image_rejections(conn, universe)
    state = _fe_image_state(conn, universe)
    reasons: dict[int, str] = {}

    # --- Pass 1: FE 已通過嘅 variant → 補齊 public 三件套（純本地 resize） ----
    for vid in universe:
        entry = state.get(vid)
        if not entry:
            continue
        sha = entry["contentSha256"]
        if not _HEX64_RE.fullmatch(sha):
            reasons[vid] = "fe_state_bad_sha"
            continue
        if not _trio_missing(sha):
            counts["alreadyOk"] += 1
            continue
        failure = _materialize_trio(sha, None)
        if failure:
            counts["trioFillFailed"] += 1
            reasons[vid] = f"trio:{failure}"
        else:
            counts["trioFilled"] += 1

    # --- Pass 2: SNK EN 綁定（primaryMedia 優先；collect_control 證實鏈） --------
    todo_snk = [vid for vid in universe if vid not in state and vid in snk_bind]
    snk_evidence = _exact_identity_evidence(conn, "snkrdunk", todo_snk)
    snk_items: list[dict[str, Any]] = []
    for vid in todo_snk:
        items = snk_bind[vid]
        if len(items) != 1:
            reasons[vid] = "snk_multiple_exact_ids"
            continue
        external_id = str(items[0])
        evidence = snk_evidence.get((vid, external_id))
        if not evidence:
            reasons[vid] = "snk_no_exact_evidence"
            continue
        snk_items.append({
            "variantId": vid,
            "externalId": external_id,
            "identityEvidenceSha256": evidence,
        })
    counts["snkAttempted"] = len(snk_items)
    bound_by_snk: set[int] = set()
    if snk_items:
        external_ids = {item["externalId"] for item in snk_items}
        master_cache = _local_snk_master_cache(external_ids)
        bytes_cache = _local_snk_default_bytes(external_ids)
        counts["snkMasterCacheHits"] = len(master_cache)
        counts["snkBytesCacheHits"] = len(bytes_cache)
        api_pool = SnkrdunkApiPool(workers=4, delay=0.6, retries=1)
        executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="s9-snk")
        consecutive = 0
        try:
            futures = {
                int(item["variantId"]): executor.submit(
                    _prepare_snk_en_target, item, api_pool=api_pool,
                    master_cache=master_cache, bytes_cache=bytes_cache,
                )
                for item in snk_items
            }
            for item in snk_items:
                vid = int(item["variantId"])
                started_at = datetime.now(timezone.utc).replace(tzinfo=None)
                try:
                    prepared = futures[vid].result()
                except Exception as exc:  # noqa: BLE001
                    reasons[vid] = f"snk_prepare:{type(exc).__name__}"
                    counts["snkFailed"] += 1
                    consecutive += 1
                    if consecutive >= 20:
                        raise SystemExit(
                            "S9 ABORT: 20 consecutive SNK prepare failures"
                            f" (systemic); last={exc}"
                        )
                    continue
                consecutive = 0
                if prepared.image.content_sha256 in rejections.get(vid, set()):
                    reasons[vid] = "rejected_content"
                    counts["snkRejected"] += 1
                    continue
                completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
                try:
                    with conn.cursor() as cursor:
                        _persist_prepared_snk_en(
                            cursor, item=item, prepared=prepared,
                            mode="rebuild036", started_at=started_at,
                            completed_at=completed_at,
                        )
                    conn.commit()
                except Exception as exc:  # noqa: BLE001
                    conn.rollback()
                    reasons[vid] = f"snk_persist:{type(exc).__name__}:{exc}"[:200]
                    counts["snkFailed"] += 1
                    continue
                failure = _materialize_trio(
                    prepared.image.content_sha256, prepared.image.content,
                )
                if failure:
                    reasons[vid] = f"trio:{failure}"
                    counts["trioFillFailed"] += 1
                else:
                    counts["snkBound"] += 1
                    bound_by_snk.add(vid)
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    # --- Pass 3: PC 產品圖（replay 頁抽 1600.jpg，fallback 分支四表鏈） --------
    todo_pc = [
        vid for vid in universe
        if vid in pc_bind and vid not in state and vid not in bound_by_snk
        and reasons.get(vid) != "rejected_content"
    ]
    pc_evidence = _exact_identity_evidence(conn, "pricecharting", todo_pc)
    sidecars_by_pid: dict[str, Path] = {}
    replay_dir = _pc_replay_dir(generation)
    for sidecar_path in sorted(replay_dir.glob("*.json")):
        if sidecar_path.name.startswith("replay-"):
            continue
        try:
            pid_value = str(json.loads(
                sidecar_path.read_text(encoding="utf-8")
            )["pcProductId"])
        except (json.JSONDecodeError, KeyError):
            continue
        sidecars_by_pid.setdefault(pid_value, sidecar_path)
    counts["pcAttempted"] = len(todo_pc)
    download_failures = 0
    import time as _time
    for vid in todo_pc:
        outcome: str | None = None
        for pid in sorted(pc_bind[vid], key=lambda value: int(value)):
            sidecar_path = sidecars_by_pid.get(pid)
            if sidecar_path is None:
                outcome = outcome or "pc_no_page"
                continue
            html_path = sidecar_path.with_suffix(".html")
            if not html_path.is_file():
                outcome = outcome or "pc_no_page"
                continue
            sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
            digest = sha256_file(html_path)
            if digest != sidecar.get("captureSha256"):
                outcome = "pc_page_sha_mismatch"
                continue
            html = html_path.read_text(encoding="utf-8", errors="replace")
            image_ids = sorted(set(_PC_IMAGE_URL_RE.findall(html)))
            # "More Photos" user uploads (card backs etc.) add extra ids; the
            # product front is anchored by the product_details cover block.
            cover_ids = sorted(set(_PC_COVER_IMAGE_RE.findall(html)))
            if len(image_ids) == 1:
                chosen_id = image_ids[0]
            elif len(cover_ids) == 1:
                chosen_id = cover_ids[0]
            elif not image_ids:
                outcome = outcome or "pc_no_image"
                continue
            else:
                outcome = "pc_image_ambiguous"
                continue
            image_url = (
                "https://storage.googleapis.com/images.pricecharting.com/"
                f"{chosen_id}/1600.jpg"
            )
            evidence = pc_evidence.get((vid, pid))
            if not evidence:
                outcome = "pc_no_exact_evidence"
                continue
            try:
                raw = _download_pc_image(image_url)
                download_failures = 0
            except Exception:  # noqa: BLE001
                outcome = "pc_image_download_failed"
                download_failures += 1
                if download_failures >= 15:
                    raise SystemExit(
                        "S9 ABORT: 15 consecutive PC image download failures"
                    )
                continue
            try:
                processed = _process_pc_product_image(raw)
            except Exception:  # noqa: BLE001
                outcome = "pc_image_decode_failed"
                continue
            if processed["contentSha256"] in rejections.get(vid, set()):
                outcome = "rejected_content"
                counts["pcRejected"] += 1
                break
            observed_at = datetime.strptime(
                str(sidecar["capturedAtUtc"]), "%Y-%m-%dT%H:%M:%SZ"
            )
            completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
            canonical_url = canonical_url_from_html(html) or image_url
            try:
                with conn.cursor() as cursor:
                    _persist_pc_product_image(
                        cursor, variant_id=vid, pid=pid, image_url=image_url,
                        page_sha=digest, canonical_url=canonical_url,
                        downloaded_sha=sha256_bytes(raw),
                        identity_evidence=evidence, processed=processed,
                        observed_at=observed_at, completed_at=completed_at,
                    )
                conn.commit()
            except Exception as exc:  # noqa: BLE001
                conn.rollback()
                outcome = f"pc_persist:{type(exc).__name__}:{exc}"[:200]
                continue
            failure = _materialize_trio(processed["contentSha256"], processed["content"])
            if failure:
                outcome = f"trio:{failure}"
                counts["trioFillFailed"] += 1
            else:
                counts["pcBound"] += 1
                outcome = None
            _time.sleep(0.35)
            break
        if outcome is not None:
            reasons[vid] = outcome
            if outcome != "rejected_content":
                counts["pcFailed"] += 1

    # --- Final gate: FE 兩條 query + 三件套，逐 variant 判定 -------------------
    final_state = _fe_image_state(conn, universe)
    routes: list[dict[str, Any]] = []
    reason_histogram: dict[str, int] = {}
    for vid in universe:
        entry = final_state.get(vid)
        if entry and _HEX64_RE.fullmatch(entry["contentSha256"]):
            missing = _trio_missing(entry["contentSha256"])
            if not missing:
                routes.append({
                    "variantId": vid, "status": "product_ready",
                    "via": entry["via"], "contentSha256": entry["contentSha256"],
                })
                continue
            reason = "trio_missing:" + ",".join(sorted(missing))
        else:
            reason = reasons.get(vid, "no_strict_image_source")
        reason_key = reason.split(":", 1)[0]
        reason_histogram[reason_key] = reason_histogram.get(reason_key, 0) + 1
        routes.append({
            "variantId": vid, "status": "not_product_ready", "reason": reason,
            "snkCandidates": [str(item) for item in snk_bind.get(vid, [])],
            "pcCandidates": list(pc_bind.get(vid, [])),
        })
    counts["productReady"] = sum(
        1 for row in routes if row["status"] == "product_ready"
    )
    counts["notProductReady"] = len(routes) - counts["productReady"]
    counts["notReadyReasons"] = dict(sorted(reason_histogram.items()))

    artifact = {
        "contract": "image_bind_v1",
        "generation": generation,
        "policy": {
            "priority": ["snkrdunk_en_primary_media", "pricecharting_product_image"],
            "forbidden": ["kado", "tcgplayer", "ai_generated", "unsourced"],
            "rejectionRegistryConsulted": True,
            "gate": "fe_live_db_snapshot_image_queries + public webp trio",
        },
        "routes": routes,
    }
    artifact_blob = canonical_json(artifact)
    artifact_path = ROOT / "data" / "runtime" / "rebuild-036" / (
        f"image-bind-{generation}.json"
    )
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_bytes(artifact_blob)
    counts["imageArtifact"] = artifact_path.relative_to(ROOT).as_posix()
    counts["imageArtifactSha256"] = sha256_bytes(artifact_blob)

    return {
        "input_sha256": _image_bind_input_sha(ctx),
        "output_sha256": sha256_bytes(canonical_json([
            "image-bind-output", sha256_bytes(artifact_blob),
        ])),
        "counts": counts,
    }


def _image_bind_input_sha(ctx: SimpleNamespace) -> str:
    """S9's input: S8's recorded output. Never the tables/files S9 mutates."""

    with ctx.conn.cursor() as cursor:
        cursor.execute(
            "SELECT output_sha256 FROM cardz_rebuild_checkpoint"
            " WHERE generation_id=%s AND stage='price-materialize'",
            (ctx.generation,),
        )
        row = cursor.fetchone()
    return sha256_bytes(canonical_json([
        "image-bind-input", (row or {}).get("output_sha256"),
    ]))


# ---------------------------------------------------------------------------
# Stage 10: prune-plan (§6.7 — 算刪除集 + 順序，唔刪嘢)
# ---------------------------------------------------------------------------

def _fk_edges(conn) -> list[dict[str, str]]:
    """Schema-wide FK edges: child table/column -> parent table."""

    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT TABLE_NAME AS child_table, COLUMN_NAME AS child_column,"
            "       REFERENCED_TABLE_NAME AS parent_table"
            " FROM information_schema.KEY_COLUMN_USAGE"
            " WHERE TABLE_SCHEMA=DATABASE() AND REFERENCED_TABLE_NAME IS NOT NULL"
        )
        return [
            {
                "child_table": str(row["child_table"]),
                "child_column": str(row["child_column"]),
                "parent_table": str(row["parent_table"]),
            }
            for row in cursor.fetchall()
        ]


def _table_columns(conn, table: str) -> dict[str, str]:
    """column -> IS_NULLABLE for one base table."""

    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT COLUMN_NAME AS name, IS_NULLABLE AS nullable"
            " FROM information_schema.COLUMNS"
            " WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s",
            (table,),
        )
        return {str(row["name"]): str(row["nullable"]) for row in cursor.fetchall()}


def _view_count_bounded(conn, view: str, budget_ms: int = 60_000) -> int | None:
    """COUNT(*) with a server-side time budget; None = timed out.

    呢啲 operator_* view 有 correlated-EXISTS，個別會行 30 分鐘以上
    （operator_latest_accepted_daily_fact_projection 實測 34min 未完）。
    S10/S13 只攞嚟做 before/after telemetry，唔係 gate — 超時記 null，
    唔准擋住主線。
    """

    try:
        with conn.cursor() as cursor:
            cursor.execute(
                f"SELECT /*+ MAX_EXECUTION_TIME({budget_ms}) */ COUNT(*) AS n FROM {view}"
            )
            return int(cursor.fetchone()["n"] or 0)
    except pymysql.err.OperationalError as err:
        if err.args and err.args[0] == 3024:  # ER_QUERY_TIMEOUT
            conn.rollback()
            return None
        raise


def _chunked_in_count(conn, sql_template: str, ids: list[int], chunk: int = 800) -> int:
    """SUM of COUNT(*) over id chunks; sql_template has one {ph} placeholder slot."""

    total = 0
    for start in range(0, len(ids), chunk):
        batch = ids[start:start + chunk]
        ph = ",".join(["%s"] * len(batch))
        with conn.cursor() as cursor:
            cursor.execute(sql_template.format(ph=ph), batch)
            total += int(cursor.fetchone()["n"] or 0)
    return total


def stage_prune_plan(ctx: SimpleNamespace) -> dict[str, Any]:
    """S10 §6.7: derive the delete set + leaf-first order. ZERO deletes here.

    雙向對數：FK closure ∪ MANUAL_NO_FK_TABLES ∪ NEVER_DELETE_TABLES 必須同
    allowlist keys 完全相等，唔准自動刪未知表。nullify 欄必須可以 NULL。
    """

    conn = ctx.conn
    generation = ctx.generation
    counts: dict[str, Any] = {}

    allowlist_blob = PRUNE_ALLOWLIST.read_bytes()
    allowlist = json.loads(allowlist_blob.decode("utf-8"))["tables"]

    # --- candidate closure vs allowlist, both directions ----------------------
    edges = _fk_edges(conn)
    children_of: dict[str, set[str]] = {}
    for edge in edges:
        children_of.setdefault(edge["parent_table"], set()).add(edge["child_table"])
    closure: set[str] = set()
    frontier = ["catalog_variant"]
    while frontier:
        parent = frontier.pop()
        for child in children_of.get(parent, ()):
            if child != "catalog_variant" and child not in closure:
                closure.add(child)
                frontier.append(child)
    candidates = closure | set(MANUAL_NO_FK_TABLES) | set(NEVER_DELETE_TABLES)
    unknown = sorted(candidates - set(allowlist))
    if unknown:
        raise SystemExit(f"S10 ABORT: tables not in prune allowlist: {unknown}")
    stale = sorted(set(allowlist) - candidates)
    if stale:
        raise SystemExit(f"S10 ABORT: allowlist entries missing from schema closure: {stale}")
    counts["closureTables"] = len(closure)
    counts["candidateTables"] = len(candidates)

    # --- allowlist column existence + nullify nullability ---------------------
    for table, entry in allowlist.items():
        mode = entry["deleteMode"]
        columns = list(entry.get("columns") or [])
        if mode in {"delete", "nullify"} and not columns:
            raise SystemExit(f"S10 ABORT: {table} deleteMode={mode} without columns")
        schema_cols = _table_columns(conn, table)
        for column in columns:
            if column not in schema_cols:
                raise SystemExit(f"S10 ABORT: allowlist column missing: {table}.{column}")
            if mode == "nullify" and schema_cols[column] != "YES":
                raise SystemExit(
                    f"S10 ABORT: nullify column NOT NULL: {table}.{column}"
                )

    # --- victims: catalog_variant rows outside this generation's cohort ------
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT DISTINCT variant_id FROM catalog_rebuild_member"
            " WHERE generation_id=%s AND variant_id IS NOT NULL"
            "  AND cohort <> 'non_qualified'",
            (generation,),
        )
        protected = {int(row["variant_id"]) for row in cursor.fetchall()}
        cursor.execute("SELECT id FROM catalog_variant")
        all_variants = {int(row["id"]) for row in cursor.fetchall()}
    if not protected:
        raise SystemExit(
            "S10 ABORT: zero protected members for generation — refuse full-wipe plan"
        )
    orphan_protected = sorted(protected - all_variants)
    if orphan_protected:
        raise SystemExit(
            f"S10 ABORT: member rows point at missing variants: {orphan_protected[:5]}"
        )
    victims = sorted(all_variants - protected)
    counts["variantsTotal"] = len(all_variants)
    counts["variantsProtected"] = len(protected)
    counts["variantsToPrune"] = len(victims)

    # --- leaf-first topological order over candidate delete tables ------------
    delete_tables = {t for t, e in allowlist.items() if e["deleteMode"] == "delete"}
    internal_edges = [
        (edge["child_table"], edge["parent_table"]) for edge in edges
        if edge["child_table"] in delete_tables
        and edge["parent_table"] in delete_tables
        and edge["child_table"] != edge["parent_table"]
    ]
    parents_pending: dict[str, set[str]] = {t: set() for t in delete_tables}
    dependents: dict[str, set[str]] = {t: set() for t in delete_tables}
    for child, parent in internal_edges:
        parents_pending[parent].add(child)   # parent waits until child deleted
        dependents[child].add(parent)
    ordered: list[str] = []
    ready = sorted(t for t, blockers in parents_pending.items() if not blockers)
    while ready:
        table = ready.pop(0)
        ordered.append(table)
        for parent in sorted(dependents[table]):
            parents_pending[parent].discard(table)
            if not parents_pending[parent] and parent not in ordered and parent not in ready:
                ready.append(parent)
        ready.sort()
    if len(ordered) != len(delete_tables):
        cyclic = sorted(delete_tables - set(ordered))
        raise SystemExit(f"S10 ABORT: FK cycle among delete tables: {cyclic}")

    # --- per-table victim row counts (read-only) ------------------------------
    plan_tables: list[dict[str, Any]] = []
    position = 0
    nullify_tables = sorted(
        t for t, e in allowlist.items() if e["deleteMode"] == "nullify"
    )
    for table in nullify_tables:
        entry = allowlist[table]
        column_counts: dict[str, int] = {}
        for column in entry["columns"]:
            column_counts[column] = _chunked_in_count(
                conn,
                f"SELECT COUNT(*) AS n FROM {table}"
                f" WHERE {column} IN ({{ph}})",
                victims,
            ) if victims else 0
        position += 1
        plan_tables.append({
            "position": position, "table": table, "deleteMode": "nullify",
            "columns": entry["columns"], "victimRows": column_counts,
            "note": entry.get("note"),
        })
    for table in ordered:
        entry = allowlist[table]
        column_counts = {}
        via = entry.get("via")
        if via:
            link_column = entry["columns"][0]
            column_counts[f"via:{via}"] = _chunked_in_count(
                conn,
                f"SELECT COUNT(*) AS n FROM {table} c"
                f" JOIN {via} p ON c.{link_column}=p.id"
                f" WHERE p.variant_id IN ({{ph}})",
                victims,
            ) if victims else 0
        else:
            for column in entry["columns"]:
                column_counts[column] = _chunked_in_count(
                    conn,
                    f"SELECT COUNT(*) AS n FROM {table}"
                    f" WHERE {column} IN ({{ph}})",
                    victims,
                ) if victims else 0
        position += 1
        plan_tables.append({
            "position": position, "table": table, "deleteMode": "delete",
            "columns": entry["columns"], "via": via, "victimRows": column_counts,
            "note": entry.get("note"),
        })
    for table in sorted(NEVER_DELETE_TABLES | {
        t for t, e in allowlist.items() if e["deleteMode"] == "never"
    }):
        plan_tables.append({
            "position": None, "table": table, "deleteMode": "never",
            "columns": allowlist[table].get("columns") or [],
            "victimRows": {}, "note": allowlist[table].get("note"),
        })
    position += 1
    plan_tables.append({
        "position": position, "table": "catalog_variant", "deleteMode": "delete",
        "columns": ["id"], "victimRows": {"id": len(victims)},
        "note": "root — deleted last, after every child table",
    })

    # --- 17 variant_id views: name + current row count baseline ---------------
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT DISTINCT c.TABLE_NAME AS view_name"
            " FROM information_schema.COLUMNS c"
            " JOIN information_schema.VIEWS v"
            "   ON v.TABLE_SCHEMA=c.TABLE_SCHEMA AND v.TABLE_NAME=c.TABLE_NAME"
            " WHERE c.TABLE_SCHEMA=DATABASE() AND c.COLUMN_NAME='variant_id'"
        )
        view_names = sorted(str(row["view_name"]) for row in cursor.fetchall())
    views: list[dict[str, Any]] = []
    for view in view_names:
        rows = _view_count_bounded(conn, view)
        views.append({"view": view, "rows": rows, "countTimedOut": rows is None})
    counts["viewsTracked"] = len(views)
    counts["viewCountTimeouts"] = sum(1 for v in views if v["rows"] is None)

    artifact = {
        "contract": "prune_plan_v1",
        "generation": generation,
        "policy": {
            "noDeletesInThisStage": True,
            "foreignKeyChecksStayOn": True,
            "batch": {"idsPerBatch": 500, "deleteLimit": 5000},
            "qualifiedMarketPendingProtected": True,
        },
        "victimVariantIds": victims,
        "tables": plan_tables,
        "views": views,
        "allowlistSha256": sha256_bytes(allowlist_blob),
    }
    artifact_blob = canonical_json(artifact)
    artifact_path = ROOT / "data" / "runtime" / "rebuild-036" / (
        f"prune-plan-{generation}.json"
    )
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_bytes(artifact_blob)
    counts["pruneArtifact"] = artifact_path.relative_to(ROOT).as_posix()
    counts["pruneArtifactSha256"] = sha256_bytes(artifact_blob)
    counts["deleteTables"] = len(ordered) + 1
    counts["nullifyTables"] = len(nullify_tables)

    return {
        "input_sha256": _prune_plan_input_sha(ctx),
        "output_sha256": sha256_bytes(canonical_json([
            "prune-plan-output", sha256_bytes(artifact_blob),
        ])),
        "counts": counts,
    }


def _prune_plan_input_sha(ctx: SimpleNamespace) -> str:
    """S10's input: S9's recorded output. Never the plan file S10 writes."""

    with ctx.conn.cursor() as cursor:
        cursor.execute(
            "SELECT output_sha256 FROM cardz_rebuild_checkpoint"
            " WHERE generation_id=%s AND stage='image-bind'",
            (ctx.generation,),
        )
        row = cursor.fetchone()
    return sha256_bytes(canonical_json([
        "prune-plan-input", (row or {}).get("output_sha256"),
    ]))


# ---------------------------------------------------------------------------
# Stage 11: validate (034–036 validator → receipt 表；永遠重跑)
# ---------------------------------------------------------------------------

RAW_PAYLOAD_BASELINE = 317_169  # §6.7 [KNOWN] 2026-08-08; prune 前只准增唔准減


def stage_validate(ctx: SimpleNamespace) -> dict[str, Any]:
    """S11: cohort split + 全部 pre-activation invariant → validation receipt.

    product_ready = POP 合格 + exact 身份 + S8 有 current price route + S9 圖 gate 過。
    其餘 POP 合格者 = qualified_market_pending（唔公開、唔准刪）。
    receipt.passed=1 係 S12 activation 嘅先決條件。
    """

    from datetime import datetime, timezone

    conn = ctx.conn
    generation = ctx.generation
    checks: dict[str, dict[str, Any]] = {}

    def _artifact(name: str) -> Any | None:
        path = ROOT / "data" / "runtime" / "rebuild-036" / f"{name}-{generation}.json"
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    price_artifact = _artifact("price-route")
    image_artifact = _artifact("image-bind")
    prune_artifact = _artifact("prune-plan")
    checks["artifactsPresent"] = {
        "pass": all([price_artifact, image_artifact, prune_artifact]),
        "priceRoute": bool(price_artifact),
        "imageBind": bool(image_artifact),
        "prunePlan": bool(prune_artifact),
    }

    price_ready: set[int] = set()
    if price_artifact:
        price_ready = {
            int(row["variantId"]) for row in price_artifact["routes"]
            if row.get("route") in ("pricecharting", "snkrdunk")
        }
    image_ready: set[int] = set()
    if image_artifact:
        image_ready = {
            int(row["variantId"]) for row in image_artifact["routes"]
            if row.get("status") == "product_ready"
        }

    # --- A. cohort split (product_ready / qualified_market_pending) -----------
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT gemrate_id, variant_id, cohort, identity_pending"
            " FROM catalog_rebuild_member"
            " WHERE generation_id=%s AND cohort <> 'non_qualified'",
            (generation,),
        )
        qualified_rows = [dict(row) for row in cursor.fetchall()]
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")
    product_ready_n = market_pending_n = pending_n = unbound_n = updates = 0
    try:
        with conn.cursor() as cursor:
            for row in qualified_rows:
                if int(row["identity_pending"] or 0):
                    pending_n += 1
                    continue
                if row["variant_id"] is None:
                    unbound_n += 1
                    continue
                vid = int(row["variant_id"])
                new_cohort = (
                    "product_ready"
                    if vid in price_ready and vid in image_ready
                    else "qualified_market_pending"
                )
                if new_cohort == "product_ready":
                    product_ready_n += 1
                else:
                    market_pending_n += 1
                if new_cohort != str(row["cohort"]):
                    cursor.execute(
                        "UPDATE catalog_rebuild_member SET cohort=%s, computed_at=%s"
                        " WHERE generation_id=%s AND gemrate_id=%s",
                        (new_cohort, now_str, generation, row["gemrate_id"]),
                    )
                    updates += 1
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    qualified_total = len(qualified_rows)
    checks["cohortEquation"] = {
        "pass": product_ready_n + market_pending_n == qualified_total
        and pending_n == 0 and unbound_n == 0,
        "productReady": product_ready_n,
        "qualifiedMarketPending": market_pending_n,
        "qualifiedTotal": qualified_total,
        "identityPending": pending_n,
        "unbound": unbound_n,
        "cohortUpdates": updates,
    }

    # --- B. incidents unresolved == 0 ----------------------------------------
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT COUNT(*) AS n FROM catalog_population_identity_incident"
            " WHERE generation_id=%s AND resolved_at IS NULL",
            (generation,),
        )
        unresolved = int(cursor.fetchone()["n"] or 0)
    checks["incidentsResolved"] = {"pass": unresolved == 0, "unresolved": unresolved}

    # --- C. strictDatabaseLineageZero ----------------------------------------
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT COUNT(*) AS n FROM operator_strict_source_identity s"
            " JOIN catalog_source_identity si"
            "   ON si.source_code=s.source_code"
            "  AND si.external_entity_id=s.external_entity_id"
            "  AND si.variant_id=s.variant_id"
            " WHERE JSON_UNQUOTE(JSON_EXTRACT(si.bind_evidence_json,"
            "   '$.evidence.type')) IN ('manual_review','database_lineage')",
        )
        lineage_rows = int(cursor.fetchone()["n"] or 0)
    checks["strictDatabaseLineageZero"] = {
        "pass": lineage_rows == 0, "rows": lineage_rows,
    }

    # --- D. FE image gate == S9 admitted + trio 全齊 --------------------------
    pc_bind, snk_bind, _ = _strict_price_bindings(conn)
    universe = sorted(set(pc_bind) | set(snk_bind))
    fe_state = _fe_image_state(conn, universe)
    fe_pass_ids = {
        vid for vid, entry in fe_state.items()
        if _HEX64_RE.fullmatch(entry["contentSha256"])
        and not _trio_missing(entry["contentSha256"])
    }
    admitted_ids = {
        int(row["variantId"]) for row in (image_artifact or {"routes": []})["routes"]
        if row.get("status") == "product_ready"
    }
    checks["feRenderedImageEqualsAdmitted"] = {
        "pass": image_artifact is not None and fe_pass_ids == admitted_ids,
        "feRendered": len(fe_pass_ids),
        "admitted": len(admitted_ids),
        "feOnly": sorted(fe_pass_ids - admitted_ids)[:20],
        "admittedOnly": sorted(admitted_ids - fe_pass_ids)[:20],
    }

    # --- E. 人手 reject 內容冇復活 --------------------------------------------
    rejections = _image_rejections(conn, universe)
    resurrected = sorted(
        vid for vid, shas in rejections.items()
        if fe_state.get(vid, {}).get("contentSha256") in shas
    )
    checks["rejectedContentStaysDead"] = {
        "pass": not resurrected, "resurrectedVariantIds": resurrected,
    }

    # --- F. prune manifest 完整互斥 -------------------------------------------
    if prune_artifact:
        victims = set(prune_artifact["victimVariantIds"])
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT DISTINCT variant_id FROM catalog_rebuild_member"
                " WHERE generation_id=%s AND variant_id IS NOT NULL"
                "  AND cohort <> 'non_qualified'",
                (generation,),
            )
            protected = {int(row["variant_id"]) for row in cursor.fetchall()}
            cursor.execute("SELECT id FROM catalog_variant")
            all_variants = {int(row["id"]) for row in cursor.fetchall()}
        checks["pruneManifestExact"] = {
            "pass": victims == all_variants - protected
            and not (victims & protected),
            "victims": len(victims),
            "protected": len(protected),
            "variants": len(all_variants),
        }
    else:
        checks["pruneManifestExact"] = {"pass": False, "reason": "no prune artifact"}

    # --- G. raw evidence 只准增 -----------------------------------------------
    with conn.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) AS n FROM market_raw_payload_object")
        raw_count = int(cursor.fetchone()["n"] or 0)
    checks["rawPayloadRetained"] = {
        "pass": raw_count >= RAW_PAYLOAD_BASELINE,
        "rows": raw_count, "baseline": RAW_PAYLOAD_BASELINE,
    }

    # --- H. freshness (報數；72h 窗口 S12 會再 gate) ---------------------------
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT MAX(effective_at) AS latest"
            " FROM market_gemrate_psa10_observation_v2 WHERE generation_id=%s",
            (generation,),
        )
        pop_latest = cursor.fetchone()["latest"]
        cursor.execute(
            "SELECT MAX(effective_at) AS latest FROM market_price_observation"
            " WHERE source_code IN ('pricecharting','snkrdunk')",
        )
        price_latest = cursor.fetchone()["latest"]
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    def _age_hours(value: Any) -> float | None:
        if value is None:
            return None
        return round((now_utc - value).total_seconds() / 3600.0, 2)
    pop_age = _age_hours(pop_latest)
    price_age = _age_hours(price_latest)
    checks["freshness72h"] = {
        "pass": pop_age is not None and price_age is not None
        and pop_age <= 72.0 and price_age <= 72.0,
        "popAgeHours": pop_age, "priceAgeHours": price_age,
    }

    # --- I2. D8 故事: 762 條 canonical_locale_merge_v1 ko 模板故事非 current ----
    # 兩款模板（「…는 CARDZ 에서 PSA 10…」/「…CARDZ 보드의…」）全部係填充
    # boilerplate；名/set 名（576 條真韓文）保留，story 回 NULL 行 FE 英文邏輯。
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "UPDATE catalog_variant_locale SET market_story=NULL"
                " WHERE locale_code='ko'"
                "  AND provenance_source_code='canonical_locale_merge_v1'"
                "  AND market_story IS NOT NULL",
            )
            ko_nullified = cursor.rowcount
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT COUNT(*) AS n FROM catalog_variant_locale"
            " WHERE locale_code='ko'"
            "  AND provenance_source_code='canonical_locale_merge_v1'"
            "  AND market_story IS NOT NULL",
        )
        ko_left = int(cursor.fetchone()["n"] or 0)
    checks["koTemplateStoriesZero"] = {
        "pass": ko_left == 0, "nullifiedThisRun": ko_nullified, "remaining": ko_left,
    }

    # --- I. 034 validator (subprocess, SELECT-only, prints json) --------------
    validator_script = ROOT / "scripts" / "validate_psa_identity_repair.py"
    try:
        proc = subprocess.run(
            [sys.executable, "-X", "utf8", str(validator_script), "--phase", "036"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=900,
            encoding="utf-8", errors="replace",
        )
        report_line = (proc.stdout or "").strip().splitlines()[-1] if proc.stdout.strip() else "{}"
        legacy = json.loads(report_line)
        checks["validator034"] = {
            "pass": bool(legacy.get("pass")) and proc.returncode == 0,
            "returncode": proc.returncode,
            "invariants": legacy.get("invariants"),
            "stderrTail": (proc.stderr or "")[-400:] if proc.returncode else "",
        }
    except Exception as exc:  # noqa: BLE001
        checks["validator034"] = {
            "pass": False, "error": f"{type(exc).__name__}: {exc}"[:400],
        }

    passed = all(bool(entry.get("pass")) for entry in checks.values())
    report = {
        "contract": "rebuild_036_validation_v1",
        "generation": generation,
        "validatorVersion": "036-v1",
        "passed": passed,
        "checks": checks,
    }
    report_blob = canonical_json(report)
    receipt_sha = sha256_bytes(report_blob)
    report_path = ROOT / "data" / "runtime" / "rebuild-036" / (
        f"validate-{generation}.json"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_bytes(report_blob)
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO cardz_rebuild_validation_receipt"
                " (generation_id, receipt_sha256, passed, validator_version,"
                "  report_json, created_at)"
                " VALUES (%s,%s,%s,'036-v1',%s,%s)"
                " ON DUPLICATE KEY UPDATE passed=VALUES(passed),"
                "  report_json=VALUES(report_json), created_at=VALUES(created_at)",
                (
                    generation, receipt_sha, 1 if passed else 0,
                    report_blob.decode("utf-8"),
                    datetime.now(timezone.utc).replace(tzinfo=None),
                ),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    counts: dict[str, Any] = {
        "passed": passed,
        "receiptSha256": receipt_sha,
        "reportPath": report_path.relative_to(ROOT).as_posix(),
        "failedChecks": sorted(
            name for name, entry in checks.items() if not entry.get("pass")
        ),
        "productReady": product_ready_n,
        "qualifiedMarketPending": market_pending_n,
    }
    return {
        "input_sha256": _validate_input_sha(ctx),
        "output_sha256": sha256_bytes(canonical_json([
            "validate-output", receipt_sha, passed,
        ])),
        "counts": counts,
    }


def _validate_input_sha(ctx: SimpleNamespace) -> str:
    """S11's input: S10's recorded output. The receipt S11 writes is excluded."""

    with ctx.conn.cursor() as cursor:
        cursor.execute(
            "SELECT output_sha256 FROM cardz_rebuild_checkpoint"
            " WHERE generation_id=%s AND stage='prune-plan'",
            (ctx.generation,),
        )
        row = cursor.fetchone()
    return sha256_bytes(canonical_json([
        "validate-input", (row or {}).get("output_sha256"),
    ]))


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
    # Reuse is a crash-retry convenience, not a freshness policy: a catalog
    # older than 7 days misses newly listed sets, so it must be re-harvested.
    brute_reused = (
        brute_all.is_file()
        and (time.time() - brute_all.stat().st_mtime) < 7 * 24 * 3600
    )
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

    # 4) Keep pipelines/gemrate_ids.txt the running union (append-only).
    added_to_ids_file = gemrate_source.append_ids(worklist)

    # 5) The long leg: exact public card pages, strict resume, 429 ladder.
    result = gemrate_source.collect_public_card_details(
        worklist, cards_dir=cards_dir, delay=0.2, resume=True, chunk_size=200,
        workers=6,
    )
    if result.get("error"):
        raise SystemExit(f"S2 ABORT: browser-level failure: {result['error']}")

    # 6) Settled-id chase (D5 fetch-all): a captured page whose canonical URL
    #    settles on a different id names the successor entity of a retired
    #    slug. Without the successor's own capture it can never become a
    #    member, so S5 can never close the requested_id_resettled incident as
    #    an alias. Chase successors to fixpoint (chains are short; 5 rounds is
    #    a hard stop against a pathological cycle, not an expected depth).
    def _settled_ids(ids: list[str]) -> set[str]:
        found: set[str] = set()
        for gid in ids:
            try:
                page = json.loads(
                    (cards_dir / gid / "card_details.json").read_text(encoding="utf-8")
                ).get("publicCardPage") or {}
            except (OSError, json.JSONDecodeError):
                continue
            parts = [part for part in str(page.get("canonicalUrl") or "").split("/") if part]
            if "card" in parts:
                idx = parts.index("card")
                if len(parts) > idx + 1 and re.fullmatch(r"[0-9a-f]{40}", parts[idx + 1]):
                    found.add(parts[idx + 1])
        return found

    chase_rounds: list[dict[str, int]] = []
    seen = set(worklist)
    frontier = list(worklist)
    while len(chase_rounds) < 5:
        new_ids = sorted(_settled_ids(frontier) - seen)
        if not new_ids:
            break
        chase = gemrate_source.collect_public_card_details(
            new_ids, cards_dir=cards_dir, delay=0.2, resume=True, chunk_size=200,
            workers=6,
        )
        if chase.get("error"):
            raise SystemExit(f"S2 ABORT: settled-chase browser failure: {chase['error']}")
        for key in ("cached", "attempted", "succeeded", "failed"):
            result[key] += chase[key]
        result["promotable"] = result["promotable"] and chase["promotable"]
        result["failureReceipts"] = (
            (result.get("failureReceipts") or []) + (chase.get("failureReceipts") or [])
        )
        seen.update(new_ids)
        frontier = new_ids
        chase_rounds.append(
            {"added": len(new_ids), "succeeded": chase["succeeded"], "failed": chase["failed"]}
        )
        added_to_ids_file += gemrate_source.append_ids(new_ids)
    if len(seen) > len(worklist):
        worklist = sorted(seen)
        worklist_path.write_text("\n".join(worklist) + "\n", encoding="utf-8")

    gemrate_source._save(run_dir / "worklist-provenance.json", {
        "generation": ctx.generation,
        "builtAt": stamp,
        "bruteReused": brute_reused,
        "dbGemrateIdColumns": db_columns,
        "cacheMerge": {"copied": merged, "skippedExisting": skipped_existing},
        "sourceCounts": {name: len(values) for name, values in sorted(sources.items())},
        "settledChaseRounds": chase_rounds,
        "worklistCount": len(worklist),
        "worklistSha256": sha256_file(worklist_path),
    })

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

    counts = {
        "worklist": len(worklist),
        "cacheMergedFromOldCheckout": merged,
        "idsFileAppended": added_to_ids_file,
        "settledChase": chase_rounds,
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


# ---------------------------------------------------------------------------
# Stage 13: prune-apply (post-activation only; batched, FK checks stay ON)
# ---------------------------------------------------------------------------

def _next_batch_index(cur, generation: str, table: str) -> int:
    cur.execute(
        "SELECT COALESCE(MAX(batch_index),0) AS n FROM cardz_rebuild_prune_progress"
        " WHERE generation_id=%s AND table_name=%s",
        (generation, table),
    )
    return int(cur.fetchone()["n"]) + 1


def stage_prune_apply(ctx: SimpleNamespace) -> dict[str, Any]:
    """S13 §6.7: physically delete non-qualified variants per the S10 plan.

    Batches of ≤500 ids, DELETE ... LIMIT 5000 loops, commit per batch,
    progress in cardz_rebuild_prune_progress (idempotent rerun: victims are
    fixed, emptied batches count 0). FOREIGN_KEY_CHECKS stays ON — the 76
    NO ACTION FKs are the proof the order is right, never the obstacle."""

    from datetime import datetime, timezone

    conn = ctx.conn
    generation = ctx.generation
    plan_path = ROOT / "data" / "runtime" / "rebuild-036" / (
        f"prune-plan-{generation}.json"
    )
    if not plan_path.is_file():
        raise SystemExit(f"S13 ABORT: prune plan missing: {plan_path}")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    allowlist_sha = sha256_bytes(PRUNE_ALLOWLIST.read_bytes())
    if plan.get("allowlistSha256") != allowlist_sha:
        raise SystemExit(
            "S13 ABORT: allowlist changed since prune-plan; rerun prune-plan first"
        )
    victims = [int(v) for v in plan["victimVariantIds"]]

    # Victims must still be disjoint from the live protected set.
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT DISTINCT variant_id FROM catalog_rebuild_member"
            " WHERE generation_id=%s AND variant_id IS NOT NULL"
            "  AND cohort <> 'non_qualified'",
            (generation,),
        )
        protected = {int(row["variant_id"]) for row in cursor.fetchall()}
        cursor.execute("SELECT COUNT(*) AS n FROM market_raw_payload_object")
        raw_before = int(cursor.fetchone()["n"] or 0)
        # Self-referencing FKs (supersedes chains): rows must die newest-first,
        # else deleting a superseded parent while its successor lives hits 1451.
        cursor.execute(
            "SELECT DISTINCT TABLE_NAME FROM information_schema.KEY_COLUMN_USAGE"
            " WHERE TABLE_SCHEMA=DATABASE() AND REFERENCED_TABLE_NAME=TABLE_NAME"
        )
        self_ref_tables = {str(row["TABLE_NAME"]) for row in cursor.fetchall()}
    overlap = sorted(set(victims) & protected)
    if overlap:
        raise SystemExit(f"S13 ABORT: victims overlap protected set: {overlap[:20]}")

    table_stats: dict[str, dict[str, int]] = {}

    def _record_batch(table: str, deleted: int) -> None:
        with conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO cardz_rebuild_prune_progress"
                " (generation_id, table_name, batch_index, rows_deleted, finished_at)"
                " VALUES (%s,%s,%s,%s,%s)",
                (
                    generation, table, _next_batch_index(cursor, generation, table),
                    deleted, datetime.now(timezone.utc).replace(tzinfo=None),
                ),
            )
        conn.commit()

    def _delete_loop(table: str, sql: str, params: tuple) -> int:
        total = 0
        while True:
            try:
                with conn.cursor() as cursor:
                    cursor.execute(sql, params)
                    deleted = cursor.rowcount
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            _record_batch(table, deleted)
            total += deleted
            if deleted < 5000:
                return total

    ordered_entries = sorted(
        (entry for entry in plan["tables"] if entry.get("position") is not None),
        key=lambda entry: entry["position"],
    )
    for entry in ordered_entries:
        table = entry["table"]
        mode = entry["deleteMode"]
        columns = list(entry.get("columns") or [])
        via = entry.get("via")
        stats = table_stats.setdefault(table, {"deleted": 0, "nullified": 0})
        for start in range(0, len(victims), 500):
            chunk = victims[start:start + 500]
            ph = ",".join(["%s"] * len(chunk))
            if mode == "nullify":
                for column in columns:
                    try:
                        with conn.cursor() as cursor:
                            cursor.execute(
                                f"UPDATE {table} SET {column}=NULL"
                                f" WHERE {column} IN ({ph})",
                                tuple(chunk),
                            )
                            stats["nullified"] += cursor.rowcount
                        conn.commit()
                    except Exception:
                        conn.rollback()
                        raise
            elif mode == "delete" and via:
                order = " ORDER BY id DESC" if table in self_ref_tables else ""
                stats["deleted"] += _delete_loop(
                    table,
                    f"DELETE FROM {table} WHERE {columns[0]} IN"
                    f" (SELECT id FROM {via} WHERE variant_id IN ({ph}))"
                    f"{order} LIMIT 5000",
                    tuple(chunk),
                )
            elif mode == "delete":
                column = columns[0] if table != "catalog_variant" else "id"
                order = " ORDER BY id DESC" if table in self_ref_tables else ""
                stats["deleted"] += _delete_loop(
                    table,
                    f"DELETE FROM {table} WHERE {column} IN ({ph}){order} LIMIT 5000",
                    tuple(chunk),
                )

    with conn.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) AS n FROM market_raw_payload_object")
        raw_after = int(cursor.fetchone()["n"] or 0)
        cursor.execute("SELECT COUNT(*) AS n FROM catalog_variant")
        variants_left = int(cursor.fetchone()["n"] or 0)
    views_after = []
    for view in plan.get("views", []):
        after = _view_count_bounded(conn, view["view"])
        views_after.append({
            "view": view["view"],
            "before": view["rows"],
            "after": after,
            "countTimedOut": after is None,
        })
    if raw_after != raw_before:
        raise SystemExit(
            f"S13 ABORT: market_raw_payload_object moved {raw_before}->{raw_after};"
            " raw evidence must never change during prune"
        )
    if variants_left != len(protected):
        raise SystemExit(
            f"S13 ABORT: catalog_variant left {variants_left} != protected {len(protected)}"
        )

    artifact = {
        "contract": "prune_apply_v1",
        "generation": generation,
        "victims": len(victims),
        "tables": table_stats,
        "views": views_after,
        "rawPayloadRows": {"before": raw_before, "after": raw_after},
        "variantsRemaining": variants_left,
    }
    artifact_blob = canonical_json(artifact)
    artifact_sha = sha256_bytes(artifact_blob)
    artifact_path = ROOT / "data" / "runtime" / "rebuild-036" / (
        f"prune-apply-{generation}.json"
    )
    artifact_path.write_bytes(artifact_blob)
    return {
        "input_sha256": _prune_apply_input_sha(ctx),
        "output_sha256": sha256_bytes(canonical_json(["prune-apply-output", artifact_sha])),
        "counts": {
            "victims": len(victims),
            "variantsRemaining": variants_left,
            "deletedTotal": sum(s["deleted"] for s in table_stats.values()),
            "nullifiedTotal": sum(s["nullified"] for s in table_stats.values()),
            "artifact": artifact_path.relative_to(ROOT).as_posix(),
        },
    }


def _prune_apply_input_sha(ctx: SimpleNamespace) -> str:
    with ctx.conn.cursor() as cursor:
        cursor.execute(
            "SELECT activation_receipt_sha256 FROM cardz_rebuild_generation"
            " WHERE generation_id=%s",
            (ctx.generation,),
        )
        row = cursor.fetchone()
    plan_path = ROOT / "data" / "runtime" / "rebuild-036" / (
        f"prune-plan-{ctx.generation}.json"
    )
    plan_sha = sha256_file(plan_path) if plan_path.is_file() else None
    return sha256_bytes(canonical_json([
        "prune-apply-input", (row or {}).get("activation_receipt_sha256"), plan_sha,
    ]))


# ---------------------------------------------------------------------------
# Stage 14: canary — one fresh provider capture through the V2 incremental lane
# ---------------------------------------------------------------------------

def stage_canary(ctx: SimpleNamespace) -> dict[str, Any]:
    """S14 §6.7: land ONE new GemRate capture for a known qualified printing and
    prove the incremental lane: append-only history, POP monotonicity,
    generation mapping, FE invalidation, no duplicate current row.

    The capture comes from the operator-supplied file (produced by the
    sanctioned browser+mirror script) at
    data/runtime/rebuild-036/canary-capture-{generation}.json:
      {"gemrateId": <40-hex>, "psa10Population": int, "totalPopulation": int|null,
       "effectiveDate": "YYYY-MM-DD", "capturePath": str, "rawPayloadSha256": 64-hex,
       "psaRowSha256": 64-hex}
    Schedulers stay Disabled — this stage proves the lane, it does not enable it."""

    from datetime import datetime, timezone

    conn = ctx.conn
    generation = ctx.generation
    capture_path = ROOT / "data" / "runtime" / "rebuild-036" / (
        f"canary-capture-{generation}.json"
    )
    if not capture_path.is_file():
        raise SystemExit(
            f"S14 ABORT: canary capture missing: {capture_path};"
            " produce it with the sanctioned GemRate mirror script first"
        )
    capture = json.loads(capture_path.read_text(encoding="utf-8"))
    gemrate_id = str(capture["gemrateId"]).strip().casefold()
    if not re.fullmatch(r"[0-9a-f]{40}", gemrate_id):
        raise SystemExit("S14 ABORT: canary gemrateId must be 40-hex")
    for key in ("rawPayloadSha256", "psaRowSha256"):
        if not _HEX64_RE.fullmatch(str(capture.get(key) or "")):
            raise SystemExit(f"S14 ABORT: canary {key} must be 64-hex")
    new_pop = int(capture["psa10Population"])
    effective_date = str(capture["effectiveDate"])[:10]

    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT variant_id, cohort FROM catalog_rebuild_member"
            " WHERE generation_id=%s AND gemrate_id=%s",
            (generation, gemrate_id),
        )
        member = cursor.fetchone()
    if not member or member["cohort"] != "product_ready" or member["variant_id"] is None:
        raise SystemExit("S14 ABORT: canary card is not a product_ready member")
    variant_id = int(member["variant_id"])

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    now_str = now.strftime("%Y-%m-%d %H:%M:%S.%f")
    checks: dict[str, Any] = {}
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS n, COALESCE(MAX(id),0) AS max_id"
                " FROM market_metric_history_acceptance WHERE variant_id=%s",
                (variant_id,),
            )
            history_before = dict(cur.fetchone())
            cur.execute(
                "SELECT top_grade_population FROM market_grader_population_observation"
                " WHERE variant_id=%s AND source_code='gemrate' AND grader_code='PSA'"
                " ORDER BY observed_date DESC LIMIT 1",
                (variant_id,),
            )
            pop_before = int((cur.fetchone() or {}).get("top_grade_population") or 0)
            cur.execute(
                "SELECT ranking_generation_sha256, accepted_at"
                " FROM market_canonical_metric_acceptance metric"
                " WHERE variant_id=%s AND NOT EXISTS ("
                "   SELECT 1 FROM market_canonical_metric_acceptance newer"
                "   WHERE newer.supersedes_acceptance_id=metric.id)"
                " ORDER BY accepted_at DESC, id DESC LIMIT 1",
                (variant_id,),
            )
            fe_before = dict(cur.fetchone() or {})

            # V2 incremental landing (same shapes as S3 + activation bridge).
            canary_manifest_sha = sha256_bytes(canonical_json({
                "contract": "rebuild036-canary-v1",
                "generation": generation,
                "gemrateId": gemrate_id,
            }))
            cur.execute(
                "INSERT INTO market_ingest_run (run_key, source_code, ingest_mode,"
                " effective_at, status, observed_count, accepted_count,"
                " quarantined_count, rejected_count, started_at, completed_at,"
                " payload_sha256, manifest_sha256)"
                " VALUES (%s,'gemrate','incremental',%s,'completed',1,1,0,0,%s,%s,%s,%s)"
                " ON DUPLICATE KEY UPDATE id=LAST_INSERT_ID(id), completed_at=VALUES(completed_at)",
                (
                    f"rebuild036-canary-{generation}", now_str, now_str, now_str,
                    capture["rawPayloadSha256"], canary_manifest_sha,
                ),
            )
            run_id = int(cur.lastrowid)
            cur.execute(
                "INSERT INTO market_gemrate_psa10_observation_v2 (run_id, gemrate_id,"
                " variant_id, psa10_population, total_population, effective_at,"
                " observed_date, capture_path, raw_payload_sha256, psa_row_sha256,"
                " generation_id)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
                " ON DUPLICATE KEY UPDATE run_id=VALUES(run_id),"
                " psa10_population=VALUES(psa10_population),"
                " total_population=VALUES(total_population),"
                " effective_at=VALUES(effective_at),"
                " capture_path=VALUES(capture_path),"
                " raw_payload_sha256=VALUES(raw_payload_sha256),"
                " psa_row_sha256=VALUES(psa_row_sha256)",
                (
                    run_id, gemrate_id, variant_id, new_pop,
                    capture.get("totalPopulation"),
                    f"{effective_date} 00:00:00", effective_date,
                    str(capture.get("capturePath") or ""),
                    capture["rawPayloadSha256"], capture["psaRowSha256"],
                    generation,
                ),
            )
            cur.execute(
                "INSERT INTO market_grader_population_observation"
                " (run_id, variant_id, source_code, external_entity_id, grader_code,"
                "  top_grade_label, total_population, top_grade_population, estimated,"
                "  effective_at, observed_date, payload_sha256)"
                " VALUES (%s,%s,'gemrate',%s,'PSA','10',%s,%s,0,%s,%s,%s)"
                # top_grade_label and estimated are NOT in uq_market_grader_population
                # (variant_id, grader_code, source_code, observed_date) but they decide
                # what the row MEANS, so the update list must restate them: a row left
                # saying 'top' carries a PSA-10 number under a label no acceptance lane
                # reads. See _activation_bridge_population for the same rule.
                " ON DUPLICATE KEY UPDATE run_id=VALUES(run_id),"
                "  top_grade_label=VALUES(top_grade_label), estimated=VALUES(estimated),"
                "  top_grade_population=GREATEST(top_grade_population,VALUES(top_grade_population)),"
                "  total_population=VALUES(total_population),"
                "  effective_at=VALUES(effective_at), payload_sha256=VALUES(payload_sha256)",
                (
                    run_id, variant_id, gemrate_id, capture.get("totalPopulation"),
                    new_pop, f"{effective_date} 00:00:00", effective_date,
                    capture["psaRowSha256"],
                ),
            )
            history = _activation_accept_history(cur, now_str, set())
            cur.execute(
                "SELECT variant_id FROM catalog_rebuild_member"
                " WHERE generation_id=%s AND cohort='product_ready'"
                "  AND variant_id IS NOT NULL",
                (generation,),
            )
            ready_ids = [int(row["variant_id"]) for row in cur.fetchall()]
            canonical = _activation_rank_and_accept(cur, ready_ids, now_str)

            # -- assertions ---------------------------------------------------
            cur.execute(
                "SELECT COUNT(*) AS n FROM market_metric_history_acceptance"
                " WHERE variant_id=%s AND id <= %s",
                (variant_id, int(history_before["max_id"])),
            )
            checks["appendOnly"] = {
                "pass": int(cur.fetchone()["n"]) == int(history_before["n"]),
                "priorRows": int(history_before["n"]),
            }
            cur.execute(
                "SELECT top_grade_population FROM market_grader_population_observation"
                " WHERE variant_id=%s AND source_code='gemrate' AND grader_code='PSA'"
                " ORDER BY observed_date DESC LIMIT 1",
                (variant_id,),
            )
            pop_now = int(cur.fetchone()["top_grade_population"])
            checks["popMonotonic"] = {
                "pass": pop_now >= pop_before,
                "before": pop_before, "after": pop_now, "captured": new_pop,
            }
            cur.execute(
                "SELECT COUNT(*) AS n FROM market_gemrate_psa10_observation_v2"
                " WHERE gemrate_id=%s AND generation_id=%s",
                (gemrate_id, generation),
            )
            checks["generationMapping"] = {
                "pass": int(cur.fetchone()["n"]) >= 1, "generation": generation,
            }
            cur.execute(
                "SELECT ranking_generation_sha256, accepted_at"
                " FROM market_canonical_metric_acceptance metric"
                " WHERE variant_id=%s AND NOT EXISTS ("
                "   SELECT 1 FROM market_canonical_metric_acceptance newer"
                "   WHERE newer.supersedes_acceptance_id=metric.id)"
                " ORDER BY accepted_at DESC, id DESC LIMIT 1",
                (variant_id,),
            )
            fe_after = dict(cur.fetchone() or {})
            checks["feInvalidation"] = {
                "pass": bool(fe_after) and (
                    fe_after.get("ranking_generation_sha256")
                    != fe_before.get("ranking_generation_sha256")
                    or fe_after.get("accepted_at") != fe_before.get("accepted_at")
                ),
                "before": {k: str(v) for k, v in fe_before.items()},
                "after": {k: str(v) for k, v in fe_after.items()},
            }
            cur.execute(
                "SELECT COUNT(*) AS n FROM market_canonical_metric_acceptance metric"
                " WHERE variant_id=%s AND NOT EXISTS ("
                "   SELECT 1 FROM market_canonical_metric_acceptance newer"
                "   WHERE newer.supersedes_acceptance_id=metric.id)",
                (variant_id,),
            )
            checks["singleCurrentRow"] = {
                "pass": int(cur.fetchone()["n"]) == 1,
            }
        passed = all(entry["pass"] for entry in checks.values())
        if not passed:
            conn.rollback()
            raise SystemExit(
                "S14 ABORT: canary checks failed:"
                f" {[k for k, v in checks.items() if not v['pass']]}"
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    artifact = {
        "contract": "canary_v1",
        "generation": generation,
        "gemrateId": gemrate_id,
        "variantId": variant_id,
        "checks": checks,
        "historyInserted": history,
        "rankingGenerationSha256": canonical["rankingGenerationSha256"],
    }
    artifact_blob = canonical_json(artifact)
    artifact_path = ROOT / "data" / "runtime" / "rebuild-036" / (
        f"canary-{generation}.json"
    )
    artifact_path.write_bytes(artifact_blob)
    return {
        "input_sha256": sha256_bytes(canonical_json([
            "canary-input", sha256_file(capture_path),
        ])),
        "output_sha256": sha256_bytes(canonical_json([
            "canary-output", sha256_bytes(artifact_blob),
        ])),
        "counts": {
            "variantId": variant_id,
            "checksPassed": all(entry["pass"] for entry in checks.values()),
            "rankingGenerationSha256": canonical["rankingGenerationSha256"],
            "artifact": artifact_path.relative_to(ROOT).as_posix(),
        },
    }


# name -> (fn, input_fn, always_run). fn None = designed but not yet built in
# this working copy; the runner stops there instead of faking progress.
LINEAR_STAGES: list[tuple[str, Callable | None, Callable | None, bool]] = [
    ("preflight", stage_preflight, None, True),
    ("migrate", stage_migrate, _migration_input_sha, False),
    ("discover", stage_discover, None, False),
    ("pop-land", stage_popland, _popland_input_sha, False),
    ("identity-resolve", stage_identity_resolve, _identity_input_sha, False),
    ("bind", stage_bind, _bind_input_sha, False),
    ("pc-replay", stage_pc_replay, _pc_replay_input_sha, False),
    ("snk-refresh", stage_snk_refresh, _snk_refresh_input_sha, False),
    ("price-materialize", stage_price_materialize, _price_materialize_input_sha, False),
    ("image-bind", stage_image_bind, _image_bind_input_sha, False),
    ("prune-plan", stage_prune_plan, _prune_plan_input_sha, False),
    ("validate", stage_validate, _validate_input_sha, True),
]
POST_ACTIVATION_STAGES: list[tuple[str, Callable | None, Callable | None, bool]] = [
    ("prune-apply", stage_prune_apply, _prune_apply_input_sha, False),
    ("canary", stage_canary, None, True),
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


def printable_counts(counts: Mapping[str, Any], *, keep: int = 3) -> dict[str, Any]:
    """The checkpoint keeps the whole receipt; stdout gets a readable copy.

    A stage that skipped ~430 items printed all of them as one 30k-character
    line, which `| tail` cannot narrow because it is one line -- so the counts
    that matter scrolled away. Long lists are replaced by their length, a
    sample, and how many were left out, so nothing is quietly truncated: the
    full list is still in cardz_rebuild_checkpoint.counts_json."""

    printable: dict[str, Any] = {}
    for key, value in counts.items():
        if isinstance(value, (list, tuple)) and len(value) > keep:
            printable[key] = {
                "count": len(value),
                "sample": list(value[:keep]),
                "omitted": len(value) - keep,
                "seeAlso": "cardz_rebuild_checkpoint.counts_json",
            }
        else:
            printable[key] = value
    return printable


def _run_stage(ctx: SimpleNamespace, name: str, fn: Callable, *, forced: bool) -> None:
    print(json.dumps({"phase": "stage-start", "stage": name, "forced": forced}, ensure_ascii=False), flush=True)
    # Taken before the stage runs, and written over whatever the stage itself
    # returned: recording and comparison must come from one function, or the
    # skip decision is made against a number nothing else computes.
    input_sha = _stage_input_sha(name, ctx)
    _stage_begin(ctx.conn, ctx.generation, name)
    try:
        result = fn(ctx)
    except SystemExit as error:
        _stage_failed(ctx.conn, ctx.generation, name, str(error)[:64] or "systemexit")
        raise
    except Exception as error:  # noqa: BLE001 - checkpoint then surface
        _stage_failed(ctx.conn, ctx.generation, name, f"{type(error).__name__}: {error}"[:64])
        raise
    result["input_sha256"] = input_sha
    _stage_finish(ctx.conn, ctx.generation, name, result)
    print(
        json.dumps(
            {"phase": "stage-complete", "stage": name,
             "counts": printable_counts(result.get("counts") or {})},
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
            if record and record.get("input_sha256"):
                current = _stage_input_sha(name, ctx)
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
    # S0 lives in stage_preflight, and only the linear path walked through it.
    # `--stage prune-apply` is the command that DELETES rows and it reached
    # them having proven no backup, no stopped scheduler and no drained ingest
    # run -- it checked the freeze and the activation and nothing else. Every
    # stage pays it here: preflight is always_run, so this is a re-check rather
    # than a re-do, and _run_freeze_proof is its first act.
    if name != "preflight":
        stage_preflight(ctx)
    if entry in POST_ACTIVATION_STAGES:
        _assert_activated(ctx)
    record = _checkpoints(ctx.conn, ctx.generation).get(name)
    status = record["status"] if record else "pending"
    if status == "failed" and not always_run and not args.force_stage:
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
            if record.get("input_sha256"):
                current = _stage_input_sha(name, ctx)
                if current != record["input_sha256"]:
                    raise SystemExit(
                        f"ABORT: stage '{name}' input drifted (recorded"
                        f" {record['input_sha256'][:12]}.., now {current[:12]}..)."
                        f" Rerun with --invalidate-from {name} to invalidate it and downstream."
                    )
            print(json.dumps({"phase": "stage-skip", "stage": name, "status": "complete"}), flush=True)
            continue
        # A stage marked always_run re-judges from scratch every pass, so a
        # failed row describes the pass that wrote it and nothing else. Blocking
        # on it demanded --force-stage, and --force-stage is one flag for the
        # whole walk: clearing a harmless preflight row would also unblock a
        # stage that failed half-written. Measured 2026-08-10T17:13Z -- a
        # preflight that aborted on "writer freeze is not in force" stopped the
        # next run, which was itself holding the freeze, after it had already
        # paid for a 1.37 GB dump.
        if status == "failed" and not always_run and not args.force_stage:
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


ACTIVATION_ACTOR = "rebuild_036:activate"


def _load_sales_fingerprints(generation: str) -> set[str]:
    """§6.7 doctrine: sale rows enter acceptance ONLY via the S8 manifests."""

    fingerprints: set[str] = set()
    for leg in ("pc", "snk"):
        path = ROOT / "data" / "runtime" / "rebuild-036" / (
            f"sales-manifest-{leg}-{generation}.jsonl"
        )
        if not path.is_file():
            raise SystemExit(f"S12 ABORT: sales manifest missing: {path}")
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            value = str(json.loads(line).get("fingerprint") or "")
            if value:
                fingerprints.add(value)
    return fingerprints


def _load_daily_sales_manifests(
    cur, activated_at, fingerprints: set[str],
) -> dict[str, int]:
    """§6.7 extended to daily collector runs (see c11 write_daily_sales_manifest).

    A manifest lists every fingerprint a run's pages evidenced — including
    re-observations whose sale rows keep an older run_id through the unique
    constraint. Trust chain: the embedded runKey must resolve to a completed
    ingest run started after the current activation; anything else is counted
    stale and skipped, so files from a previous universe retire themselves at
    the next activation."""

    manifest_dir = ROOT / "data" / "runtime" / "rebuild-036" / "daily-sales-manifests"
    stats = {"files": 0, "accepted": 0, "stale": 0, "added": 0}
    if not manifest_dir.is_dir():
        return stats
    before = len(fingerprints)
    for path in sorted(manifest_dir.glob("*.jsonl")):
        stats["files"] += 1
        lines = path.read_text(encoding="utf-8").splitlines()
        if not lines:
            stats["stale"] += 1
            continue
        header = json.loads(lines[0])
        run_key = str(header.get("runKey") or "")
        if header.get("contract") != "daily-sales-manifest-v1" or not run_key:
            stats["stale"] += 1
            continue
        cur.execute(
            "SELECT 1 FROM market_ingest_run"
            " WHERE run_key=%s AND status IN ('complete','completed')"
            "   AND started_at >= %s",
            (run_key, activated_at),
        )
        if cur.fetchone() is None:
            stats["stale"] += 1
            continue
        stats["accepted"] += 1
        for line in lines[1:]:
            line = line.strip()
            if not line:
                continue
            value = str(json.loads(line).get("fingerprint") or "")
            if value:
                fingerprints.add(value)
    stats["added"] = len(fingerprints) - before
    return stats


def _activation_bridge_population(cur, generation: str, now_str: str) -> dict[str, int]:
    """v2 landing → canonical market_grader_population_observation (FE join target).

    S3 lands generation POP evidence in market_gemrate_psa10_observation_v2 only;
    the FE core query reads market_grader_population_observation. Bridge the
    generation's rows for bound qualified members, keyed to a dedicated ingest
    run. payload_sha256 = psa_row_sha256 (ties back to the raw capture).

    The unique key is (variant_id, grader_code, source_code, observed_date) --
    top_grade_label is not in it, so an older lane that landed the same day's
    GemRate observation as the undecomposed 'top' grade owns that row. The update
    list therefore restates top_grade_label and estimated: without them the bridge
    writes a correct PSA-10 number into a row still labelled 'top', which the
    population acceptance lane refuses by label, and 131 product_ready cards
    aborted S12 for missing a population they in fact had (2026-08-09)."""

    # ingest_mode is varchar(16) vocab (incremental/full/backfill/rebuild/stock);
    # the run_key alone identifies this as the 036 pop bridge. payload/manifest
    # sha are NOT NULL char(64) — stamp the bridge contract doc.
    run_key = f"rebuild036-pop-bridge-{generation}"
    bridge_sha = sha256_bytes(canonical_json({
        "contract": "rebuild036-pop-bridge-v1", "generation": generation,
    }))
    cur.execute(
        "INSERT INTO market_ingest_run (run_key, source_code, ingest_mode,"
        " effective_at, payload_sha256, manifest_sha256, status, observed_count,"
        " accepted_count, quarantined_count, rejected_count, started_at, completed_at)"
        " VALUES (%s,'gemrate','rebuild',%s,%s,%s,'completed',0,0,0,0,%s,%s)"
        " ON DUPLICATE KEY UPDATE id=LAST_INSERT_ID(id), completed_at=VALUES(completed_at)",
        (run_key, now_str, bridge_sha, bridge_sha, now_str, now_str),
    )
    run_id = int(cur.lastrowid)
    cur.execute(
        "INSERT INTO market_grader_population_observation"
        " (run_id, variant_id, source_code, external_entity_id, grader_code,"
        "  top_grade_label, total_population, top_grade_population, estimated,"
        "  effective_at, observed_date, payload_sha256)"
        " SELECT %s, m.variant_id, 'gemrate', v2.gemrate_id, 'PSA', '10',"
        "  v2.total_population, v2.psa10_population, 0,"
        "  v2.effective_at, v2.observed_date, v2.psa_row_sha256"
        " FROM market_gemrate_psa10_observation_v2 v2"
        " JOIN catalog_rebuild_member m"
        "   ON m.generation_id=v2.generation_id AND m.gemrate_id=v2.gemrate_id"
        " WHERE v2.generation_id=%s AND m.variant_id IS NOT NULL"
        "  AND m.cohort IN ('product_ready','qualified_market_pending')"
        "  AND v2.psa_row_sha256 REGEXP '^[0-9a-f]{64}$'"
        " ON DUPLICATE KEY UPDATE"
        "  run_id=VALUES(run_id), external_entity_id=VALUES(external_entity_id),"
        "  top_grade_label=VALUES(top_grade_label), estimated=VALUES(estimated),"
        "  top_grade_population=GREATEST(top_grade_population,VALUES(top_grade_population)),"
        "  total_population=VALUES(total_population),"
        "  effective_at=VALUES(effective_at), payload_sha256=VALUES(payload_sha256)",
        (run_id, generation),
    )
    bridged = int(cur.rowcount)
    cur.execute(
        "UPDATE market_ingest_run SET observed_count=%s, accepted_count=%s WHERE id=%s",
        (bridged, bridged, run_id),
    )
    return {"runId": run_id, "bridgedRows": bridged}


def _activation_accept_history(
    cur, now_str: str, fingerprints: set[str],
) -> dict[str, int]:
    """036 versions of the four 026 history-acceptance lanes (scoped to the
    freshly promoted is_current=1 lock). Sales differ from 026: only manifest
    fingerprints may enter."""

    inserted: dict[str, int] = {}
    cur.execute(
        """
        INSERT INTO market_metric_history_acceptance
          (variant_id,metric_kind,source_record_type,source_record_id,source_code,
           external_entity_id,observed_date,source_effective_at,source_payload_sha256,
           identity_evidence_sha256,acceptance_evidence_sha256,lineage_sha256,
           accepted_by,accepted_at)
        SELECT p.variant_id,'psa10_price','market_price_observation',p.id,
               CASE WHEN p.source_code IN ('snk','snk_psa10') THEN 'snkrdunk'
                    ELSE p.source_code END,
               p.source_external_entity_id,p.observed_date,p.effective_at,p.payload_sha256,
               si.evidence_sha256,
               SHA2(CONCAT_WS('|','accept-language-routed-exact-psa10-price-v1',p.id,p.variant_id,
                 CASE WHEN p.source_code IN ('snk','snk_psa10') THEN 'snkrdunk'
                      ELSE p.source_code END,
                 p.source_external_entity_id,p.payload_sha256,si.evidence_sha256),256),
               SHA2(CONCAT_WS('|','metric-history-v1','psa10_price',p.id,p.variant_id,
                 CASE WHEN p.source_code IN ('snk','snk_psa10') THEN 'snkrdunk'
                      ELSE p.source_code END,
                 p.source_external_entity_id,p.payload_sha256,si.evidence_sha256),256),
               %s,%s
        FROM market_price_observation p
        INNER JOIN market_universe_member am ON am.variant_id=p.variant_id
        INNER JOIN market_universe_lock ul ON ul.id=am.universe_lock_id AND ul.is_current=1
        INNER JOIN catalog_printing_identity pi ON pi.variant_id=p.variant_id
        INNER JOIN operator_strict_source_identity si ON si.variant_id=p.variant_id
          AND si.source_code=CASE WHEN p.source_code IN ('snk','snk_psa10')
                                  THEN 'snkrdunk' ELSE p.source_code END
          AND si.external_entity_id=p.source_external_entity_id
        INNER JOIN market_source_observation so ON so.id=p.source_observation_id
          AND so.source_code=p.source_code AND so.external_entity_id=p.source_external_entity_id
          AND so.payload_sha256=p.payload_sha256 AND so.observed_date=p.observed_date
        WHERE p.source_code IN ('snkrdunk','snk_psa10','snk','pricecharting')
          AND (pi.card_language='en' OR p.source_code IN ('snkrdunk','snk_psa10','snk'))
          AND (
            (p.source_code IN ('snkrdunk','snk_psa10','snk')
             AND so.observation_kind='psa10_reference_price')
            OR
            (p.source_code='pricecharting'
             AND p.source_priority=95
             AND so.observation_kind='psa10_price_guide'
             AND JSON_UNQUOTE(JSON_EXTRACT(so.payload_json,'$.source'))='pricecharting'
             AND JSON_UNQUOTE(JSON_EXTRACT(so.payload_json,'$.sourceUrl')) LIKE 'https://www.pricecharting.com/%%'
             AND p.source_external_entity_id REGEXP '^[0-9]+$'
             AND (
               (JSON_UNQUOTE(JSON_EXTRACT(so.payload_json,'$.contract'))='pc_psa10_current_price_v1'
                AND JSON_UNQUOTE(JSON_EXTRACT(so.payload_json,'$.method'))='pricecharting_explicit_psa10_field_v1'
                AND JSON_UNQUOTE(JSON_EXTRACT(so.payload_json,'$.field'))='VGPC.chart_data.manualonly.last')
               OR
               (JSON_UNQUOTE(JSON_EXTRACT(so.payload_json,'$.contract'))='pc_psa10_local_history_v1'
                AND JSON_UNQUOTE(JSON_EXTRACT(so.payload_json,'$.method'))='pricecharting_explicit_psa10_history_v1'
                AND JSON_UNQUOTE(JSON_EXTRACT(so.payload_json,'$.field'))='VGPC.chart_data.manualonly.series')
             ))
          )
          AND p.metric_status='ready' AND p.price_usd>0
          AND p.payload_sha256 REGEXP '^[0-9a-f]{64}$'
          AND si.evidence_sha256 REGEXP '^[0-9a-f]{64}$'
        ON DUPLICATE KEY UPDATE
          source_code=VALUES(source_code),external_entity_id=VALUES(external_entity_id),
          observed_date=VALUES(observed_date),source_effective_at=VALUES(source_effective_at),
          source_payload_sha256=VALUES(source_payload_sha256),
          identity_evidence_sha256=VALUES(identity_evidence_sha256),
          acceptance_evidence_sha256=VALUES(acceptance_evidence_sha256),
          lineage_sha256=VALUES(lineage_sha256),accepted_by=VALUES(accepted_by),
          accepted_at=VALUES(accepted_at)
        """,
        (ACTIVATION_ACTOR, now_str),
    )
    inserted["psa10Price"] = int(cur.rowcount)

    cur.execute(
        """
        INSERT IGNORE INTO market_metric_history_acceptance
          (variant_id,metric_kind,source_record_type,source_record_id,source_code,
           external_entity_id,observed_date,source_effective_at,source_payload_sha256,
           identity_evidence_sha256,acceptance_evidence_sha256,lineage_sha256,
           accepted_by,accepted_at)
        SELECT pop.variant_id,'psa10_population','market_grader_population_observation',
               pop.id,'gemrate',pop.external_entity_id,pop.observed_date,pop.effective_at,
               pop.payload_sha256,si.evidence_sha256,
               SHA2(CONCAT_WS('|','accept-exact-gemrate-psa10-pop-v1',pop.id,pop.variant_id,
                 pop.external_entity_id,pop.payload_sha256,si.evidence_sha256),256),
               SHA2(CONCAT_WS('|','metric-history-v1','psa10_population',pop.id,pop.variant_id,
                 pop.external_entity_id,pop.payload_sha256,si.evidence_sha256),256),
               %s,%s
        FROM market_grader_population_observation pop
        INNER JOIN market_universe_member am ON am.variant_id=pop.variant_id
        INNER JOIN market_universe_lock ul ON ul.id=am.universe_lock_id AND ul.is_current=1
        INNER JOIN operator_strict_source_identity si ON si.variant_id=pop.variant_id
          AND si.source_code='gemrate' AND si.external_entity_id=pop.external_entity_id
        WHERE pop.source_code='gemrate' AND UPPER(pop.grader_code)='PSA'
          AND UPPER(REPLACE(pop.top_grade_label,' ','')) IN ('10','10.0','PSA10','GEMMINT10')
          AND pop.estimated=0 AND pop.top_grade_population>=0
          AND pop.payload_sha256 REGEXP '^[0-9a-f]{64}$'
          AND si.evidence_sha256 REGEXP '^[0-9a-f]{64}$'
        """,
        (ACTIVATION_ACTOR, now_str),
    )
    inserted["psa10Population"] = int(cur.rowcount)

    inserted["psa10Sales"] = 0
    ordered = sorted(fingerprints)
    for start in range(0, len(ordered), 500):
        chunk = ordered[start:start + 500]
        ph = ",".join(["%s"] * len(chunk))
        cur.execute(
            f"""
            INSERT IGNORE INTO market_metric_history_acceptance
              (variant_id,metric_kind,source_record_type,source_record_id,source_code,
               external_entity_id,observed_date,source_effective_at,source_payload_sha256,
               identity_evidence_sha256,acceptance_evidence_sha256,lineage_sha256,
               accepted_by,accepted_at)
            SELECT s.variant_id,'psa10_sale','market_sale_observation',s.id,
                   CASE WHEN s.source_code IN ('snk','snk_psa10') THEN 'snkrdunk' ELSE s.source_code END,
                   s.external_entity_id,DATE(s.sold_at),s.sold_at,s.source_payload_sha256,
                   si.evidence_sha256,
                   SHA2(CONCAT_WS('|','accept-exact-psa10-sale-v1',s.id,s.variant_id,
                     s.source_code,s.external_entity_id,s.source_payload_sha256,si.evidence_sha256),256),
                   SHA2(CONCAT_WS('|','metric-history-v1','psa10_sale',s.id,s.variant_id,
                     s.source_code,s.external_entity_id,s.source_payload_sha256,si.evidence_sha256),256),
                   %s,%s
            FROM market_sale_observation s
            INNER JOIN market_universe_member am ON am.variant_id=s.variant_id
            INNER JOIN market_universe_lock ul ON ul.id=am.universe_lock_id AND ul.is_current=1
            INNER JOIN operator_strict_source_identity si ON si.variant_id=s.variant_id
              AND si.source_code=CASE WHEN s.source_code IN ('snk','snk_psa10') THEN 'snkrdunk' ELSE s.source_code END
              AND si.external_entity_id=s.external_entity_id
            WHERE s.sold_at IS NOT NULL AND s.quantity>0 AND s.transaction_value_usd>0
              AND UPPER(s.grader_code)='PSA'
              AND UPPER(REPLACE(s.grade_label,' ','')) IN ('10','10.0','PSA10','GEMMINT10')
              AND s.timestamp_quality IN ('exact','date','timestamp','exact_date','relative_resolved','relative_subday')
              AND s.source_payload_sha256 REGEXP '^[0-9a-f]{{64}}$'
              AND si.evidence_sha256 REGEXP '^[0-9a-f]{{64}}$'
              AND s.transaction_fingerprint IN ({ph})
            """,
            (ACTIVATION_ACTOR, now_str, *chunk),
        )
        inserted["psa10Sales"] += int(cur.rowcount)

    cur.execute(
        """
        INSERT IGNORE INTO market_metric_history_acceptance
          (variant_id,metric_kind,source_record_type,source_record_id,source_code,
           external_entity_id,observed_date,source_effective_at,source_payload_sha256,
           identity_evidence_sha256,acceptance_evidence_sha256,lineage_sha256,
           accepted_by,accepted_at)
        SELECT a.variant_id,'verified_zero_sales','market_daily_sales_aggregate',a.id,
               exact.source_code,exact.external_entity_id,a.observed_date,r.completed_at,
               a.payload_sha256,exact.evidence_sha256,
               SHA2(CONCAT_WS('|','accept-verified-zero-sales-v1',a.id,a.variant_id,
                 exact.source_code,exact.external_entity_id,a.payload_sha256,exact.evidence_sha256),256),
               SHA2(CONCAT_WS('|','metric-history-v1','verified_zero_sales',a.id,a.variant_id,
                 exact.source_code,exact.external_entity_id,a.payload_sha256,exact.evidence_sha256),256),
               %s,%s
        FROM market_daily_sales_aggregate a
        INNER JOIN market_ingest_run r ON r.id=a.run_id AND r.status IN ('complete','completed')
        INNER JOIN market_universe_member am ON am.variant_id=a.variant_id
        INNER JOIN market_universe_lock ul ON ul.id=am.universe_lock_id AND ul.is_current=1
        INNER JOIN (
          SELECT si.variant_id,si.source_code,MIN(si.external_entity_id) AS external_entity_id,
                 MIN(si.evidence_sha256) AS evidence_sha256
          FROM operator_strict_source_identity si
          GROUP BY si.variant_id,si.source_code HAVING COUNT(*)=1
        ) exact ON exact.variant_id=a.variant_id
          AND exact.source_code=CASE WHEN a.source_code IN ('snk','snk_psa10') THEN 'snkrdunk' ELSE a.source_code END
        WHERE a.sales_count=0 AND a.coverage_status='complete'
          AND a.payload_sha256 REGEXP '^[0-9a-f]{64}$'
          AND exact.evidence_sha256 REGEXP '^[0-9a-f]{64}$'
        """,
        (ACTIVATION_ACTOR, now_str),
    )
    inserted["verifiedZeroSales"] = int(cur.rowcount)
    return inserted


def _coverage_diagnosis(cur, variant_ids: list[int]) -> str:
    """Name the reason a product_ready card has no price, not just its id.

    The bare id list cost an evening of archaeology once: the answer turned out
    to be a metric_status flag set by a superseded binding repair, four joins
    away from anything the abort mentioned. Every clause the eligibility view
    can fail on is counted here so the next abort reads as an instruction."""
    if not variant_ids:
        return "[]"
    marks = ",".join(["%s"] * len(variant_ids[:20]))
    cur.execute(
        f"""
        SELECT v.id, v.card_language, v.collector_number,
               (SELECT COUNT(*) FROM operator_strict_source_identity si
                 WHERE si.variant_id=v.id) AS strict_identities,
               (SELECT COUNT(*) FROM market_price_observation p
                 WHERE p.variant_id=v.id AND p.price_usd>0) AS price_rows,
               (SELECT COUNT(*) FROM market_price_observation p
                 WHERE p.variant_id=v.id AND p.price_usd>0
                   AND p.metric_status='ready') AS ready_rows,
               (SELECT COUNT(*) FROM market_metric_history_acceptance h
                 WHERE h.variant_id=v.id AND h.metric_kind='psa10_price')
                 AS accepted_rows
        FROM catalog_variant v WHERE v.id IN ({marks})
        """,
        tuple(variant_ids[:20]),
    )
    lines = []
    for row in cur.fetchall():
        if not int(row["price_rows"]):
            why = "never_harvested"
        elif not int(row["strict_identities"]):
            why = "no_strict_identity"
        elif not int(row["ready_rows"]):
            why = "all_price_rows_quarantined"
        elif not int(row["accepted_rows"]):
            why = "no_acceptance_row"
        else:
            why = "acceptance_present_but_view_rejected"
        lines.append(
            f"{row['id']}({row['collector_number']}/{row['card_language']}:{why}"
            f",priceRows={row['price_rows']},ready={row['ready_rows']})"
        )
    extra = "" if len(variant_ids) <= 20 else f" +{len(variant_ids) - 20} more"
    return "[" + " ".join(lines) + "]" + extra


def _activation_rank_and_accept(
    cur, ready_ids: list[int], now_str: str,
) -> dict[str, Any]:
    """Winner selection + market cap ranking + canonical acceptance rows.

    Mirrors the proven 026 selector but with dynamic coverage (== product_ready
    set, not 762) and §3.11b: identical content still bumps accepted_at."""

    from datetime import datetime, timedelta
    from decimal import Decimal

    ready = set(ready_ids)
    price_cutoff = datetime.fromisoformat(now_str) - timedelta(days=MAX_CURRENT_PRICE_AGE_DAYS)
    cur.execute(
        """
        SELECT p.variant_id,p.price_history_acceptance_id,p.price_usd,
               p.price_effective_at,p.price_source_observed_at,
               p.observed_date AS price_observed_date,
               p.price_source_code,p.price_route_priority,p.price_lineage_sha256
        FROM operator_eligible_accepted_psa10_price_history p
        INNER JOIN market_universe_member am ON am.variant_id=p.variant_id
        INNER JOIN market_universe_lock ul ON ul.id=am.universe_lock_id AND ul.is_current=1
        """
    )
    latest_price: dict[int, dict] = {}
    latest_price_keys: dict[int, tuple] = {}
    latest_eligible_price: dict[int, dict] = {}
    latest_eligible_price_keys: dict[int, tuple] = {}
    stale_price: dict[int, str] = {}
    for raw in cur.fetchall():
        row = dict(raw)
        variant_id = int(row["variant_id"])
        winner_key = (
            -int(row["price_route_priority"]),
            row["price_observed_date"],
            row["price_effective_at"],
            row["price_source_observed_at"],
            int(row["price_history_acceptance_id"]),
        )
        if (
            variant_id not in latest_eligible_price_keys
            or winner_key > latest_eligible_price_keys[variant_id]
        ):
            latest_eligible_price_keys[variant_id] = winner_key
            latest_eligible_price[variant_id] = row
        observed_at = (
            # Price age is the market observation date. A successful poll can
            # refresh the source-page receipt without producing a new trade or
            # quote; using that page timestamp would relabel an old price as
            # current merely because the collector looked again today.
            row.get("price_observed_date")
            or row.get("price_effective_at")
            or row.get("price_source_observed_at")
        )
        if observed_at is None:
            stale_price[variant_id] = "missing timestamp"
            continue
        if not isinstance(observed_at, datetime):
            observed_at = datetime.fromisoformat(str(observed_at))
        if observed_at < price_cutoff:
            stale_price[variant_id] = observed_at.isoformat(sep=" ")
            continue
        if variant_id not in latest_price_keys or winner_key > latest_price_keys[variant_id]:
            latest_price_keys[variant_id] = winner_key
            latest_price[variant_id] = row

    cur.execute(
        """
        SELECT h.id AS population_history_acceptance_id,
               pop.variant_id,pop.observed_date AS population_observed_date,
               pop.top_grade_population AS psa10_population,
               pop.effective_at AS population_effective_at,
               h.lineage_sha256 AS population_lineage_sha256
        FROM market_metric_history_acceptance h
        INNER JOIN market_grader_population_observation pop
          ON h.source_record_type='market_grader_population_observation'
         AND h.source_record_id=pop.id
         AND h.variant_id=pop.variant_id
         AND h.source_code=pop.source_code
         AND h.external_entity_id=pop.external_entity_id
        INNER JOIN market_universe_member am ON am.variant_id=pop.variant_id
        INNER JOIN market_universe_lock ul ON ul.id=am.universe_lock_id AND ul.is_current=1
        WHERE h.metric_kind='psa10_population'
          AND h.source_code='gemrate' AND pop.source_code='gemrate'
          AND UPPER(pop.grader_code)='PSA'
          AND UPPER(REPLACE(pop.top_grade_label,' ','')) IN ('10','10.0','PSA10','GEMMINT10')
          AND pop.estimated=0
          AND h.lineage_sha256 REGEXP '^[0-9a-f]{64}$'
        """
    )
    latest_population: dict[int, dict] = {}
    latest_population_keys: dict[int, tuple] = {}
    for raw in cur.fetchall():
        row = dict(raw)
        variant_id = int(row["variant_id"])
        winner_key = (
            row["population_observed_date"],
            row["population_effective_at"],
            int(row["population_history_acceptance_id"]),
        )
        if (
            variant_id not in latest_population_keys
            or winner_key > latest_population_keys[variant_id]
        ):
            latest_population_keys[variant_id] = winner_key
            latest_population[variant_id] = row

    missing_price = sorted(v for v in ready if v not in latest_price)
    missing_price_entirely = sorted(v for v in ready if v not in latest_eligible_price)
    missing_pop = sorted(v for v in ready if v not in latest_population)
    if missing_price_entirely or missing_pop:
        raise SystemExit(
            "S12 ABORT: product_ready coverage incomplete:"
            f" missingPrice={_coverage_diagnosis(cur, missing_price_entirely)}"
            f" stalePriceOver{MAX_CURRENT_PRICE_AGE_DAYS}d="
            f"{[(variant_id, stale_price[variant_id]) for variant_id in missing_price if variant_id in stale_price][:20]}"
            f" missingPop={missing_pop[:20]}"
        )

    ranked: list[tuple[int, dict, Any]] = []
    for variant_id in sorted(ready - set(missing_price)):
        row = {**latest_price[variant_id], **latest_population[variant_id]}
        cap = Decimal(str(row["price_usd"])) * Decimal(int(row["psa10_population"]))
        ranked.append((variant_id, row, cap))
    ranked.sort(key=lambda item: (-item[2], item[0]))
    awaiting_price: list[tuple[int, dict, Any]] = []
    for variant_id in missing_price:
        row = {**latest_eligible_price[variant_id], **latest_population[variant_id]}
        awaiting_price.append((variant_id, row, Decimal("0")))
    ranking_generation_sha = sha256_bytes(canonical_json(
        [
            {
                "variantId": variant_id,
                "priceAcceptanceId": int(row["price_history_acceptance_id"]),
                "populationAcceptanceId": int(row["population_history_acceptance_id"]),
                "marketCapUsd": format(cap, "f"),
            }
            for variant_id, row, cap in ranked
        ] + [
            {
                "variantId": variant_id,
                "priceAcceptanceId": int(row["price_history_acceptance_id"]),
                "populationAcceptanceId": int(row["population_history_acceptance_id"]),
                "marketCapUsd": None,
                "canonicalMarketRank": None,
                "priceStatus": "awaiting_fresh_price",
            }
            for variant_id, row, _ in awaiting_price
        ]
    ))
    accepted_rows = [
        (variant_id, row, cap, rank, False)
        for rank, (variant_id, row, cap) in enumerate(ranked, start=1)
    ] + [
        (variant_id, row, cap, None, True)
        for variant_id, row, cap in awaiting_price
    ]
    for variant_id, row, cap, rank, price_pending in accepted_rows:
        metric_lineage_sha = sha256_bytes(canonical_json(
            {
                "kind": (
                    "canonical-current-metric-awaiting-price-v1"
                    if price_pending
                    else "canonical-current-metric-v1"
                ),
                "variantId": variant_id,
                "priceHistoryAcceptanceId": int(row["price_history_acceptance_id"]),
                "populationHistoryAcceptanceId": int(row["population_history_acceptance_id"]),
                "marketCapUsd": None if price_pending else format(cap, "f"),
                "canonicalMarketRank": rank,
                "rankingGenerationSha256": ranking_generation_sha,
            }
        ))
        evidence_sha = sha256_bytes(canonical_json(
            {
                "policy": (
                    f"language-routed-price-over-{MAX_CURRENT_PRICE_AGE_DAYS}d-awaiting-refresh-v1"
                    if price_pending
                    else "language-routed-exact-price-times-exact-gemrate-pop-v1"
                ),
                "priceLineageSha256": row["price_lineage_sha256"],
                "populationLineageSha256": row["population_lineage_sha256"],
                "metricLineageSha256": metric_lineage_sha,
            }
        ))
        cur.execute(
            """
            SELECT id,metric_lineage_sha256 FROM market_canonical_metric_acceptance
            WHERE variant_id=%s AND NOT EXISTS (
              SELECT 1 FROM market_canonical_metric_acceptance newer
              WHERE newer.supersedes_acceptance_id=market_canonical_metric_acceptance.id
            ) ORDER BY accepted_at DESC,id DESC LIMIT 1
            """,
            (variant_id,),
        )
        current = cur.fetchone()
        supersedes = (
            None
            if not current or current.get("metric_lineage_sha256") == metric_lineage_sha
            else int(current["id"])
        )
        # §3.11b: identical lineage hits uq_canonical_metric_lineage and MUST
        # still move accepted_at, or a same-content re-activation silently
        # never flips the FE.
        cur.execute(
            """
            INSERT INTO market_canonical_metric_acceptance
              (variant_id,price_history_acceptance_id,population_history_acceptance_id,
               market_cap_usd,canonical_market_rank,ranking_generation_sha256,
               metric_lineage_sha256,evidence_sha256,accepted_by,accepted_at,
               supersedes_acceptance_id)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE id=LAST_INSERT_ID(id),
              accepted_by=VALUES(accepted_by),accepted_at=VALUES(accepted_at)
            """,
            (
                variant_id, int(row["price_history_acceptance_id"]),
                int(row["population_history_acceptance_id"]), cap, rank,
                ranking_generation_sha, metric_lineage_sha, evidence_sha,
                ACTIVATION_ACTOR, now_str, supersedes,
            ),
        )
    return {
        "accepted": len(accepted_rows),
        "ranked": len(ranked),
        "awaitingFreshPrice": len(awaiting_price),
        "rankingGenerationSha256": ranking_generation_sha,
        "ranks": {
            variant_id: rank
            for variant_id, _, _, rank, _ in accepted_rows
        },
    }


# Receipt fields that move without any DB write: freshness ages are wall-clock
# readings rounded to 2dp hours (36-second buckets) and cohortUpdates counts the
# work applied during that particular validate run, not a state property.
_RECEIPT_VOLATILE_FIELDS = (
    ("freshness72h", "popAgeHours"),
    ("freshness72h", "priceAgeHours"),
    ("cohortEquation", "cohortUpdates"),
)


def _receipt_state_sha(report: dict[str, Any]) -> str:
    clone = json.loads(canonical_json(report))
    for check_name, field in _RECEIPT_VOLATILE_FIELDS:
        entry = (clone.get("checks") or {}).get(check_name)
        if isinstance(entry, dict):
            entry.pop(field, None)
    return sha256_bytes(canonical_json(clone))


def cmd_activate(args: argparse.Namespace) -> int:
    from datetime import datetime, timezone

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
        receipt_sha = getattr(args, "receipt_sha256", None)
        with conn.cursor() as cursor:
            if receipt_sha:
                cursor.execute(
                    "SELECT receipt_sha256, passed, report_json"
                    " FROM cardz_rebuild_validation_receipt"
                    " WHERE generation_id=%s AND receipt_sha256=%s",
                    (generation, receipt_sha),
                )
            else:
                # The sha used to be copied by hand out of the validate stage's
                # stdout into the next command line. A human retyping a hash
                # proves nothing -- the gate is the in-process revalidation
                # below, which recomputes the receipt against the database
                # actually being activated and refuses on any difference beyond
                # volatile fields. So read the stage's own receipt back.
                cursor.execute(
                    "SELECT receipt_sha256, passed, report_json"
                    " FROM cardz_rebuild_validation_receipt"
                    " WHERE generation_id=%s AND passed=1"
                    " ORDER BY created_at DESC LIMIT 1",
                    (generation,),
                )
            receipt = cursor.fetchone()
        if receipt is None or int(receipt["passed"]) != 1:
            raise SystemExit("ABORT: validation receipt missing or not passed for that sha")
        receipt_sha = receipt["receipt_sha256"]

        # §6.7: re-run the validator in-process; the operator-supplied receipt
        # must describe the database being activated. Equality is checked on
        # the volatile-field-normalized reports — raw sha equality can never
        # hold because the receipt embeds wall-clock freshness ages.
        ctx = SimpleNamespace(conn=conn, root=ROOT, generation=generation, args=args)
        validation = stage_validate(ctx)
        recomputed = validation["counts"]["receiptSha256"]
        if not validation["counts"]["passed"]:
            raise SystemExit(
                f"ABORT: validator fails now: {validation['counts']['failedChecks']}"
            )
        if recomputed != receipt_sha:
            supplied_report = json.loads(receipt["report_json"])
            recomputed_report = json.loads(
                (ROOT / validation["counts"]["reportPath"]).read_bytes()
            )
            if _receipt_state_sha(supplied_report) != _receipt_state_sha(recomputed_report):
                raise SystemExit(
                    "ABORT: recomputed receipt"
                    f" {recomputed[:16]}.. != supplied {receipt_sha[:16]}.."
                    " beyond volatile fields; state moved since that receipt"
                    " — re-read validate output"
                )
            print(
                f"receipt {receipt_sha[:16]}.. matches recomputed"
                f" {recomputed[:16]}.. after volatile-field normalization"
            )

        fingerprints = _load_sales_fingerprints(generation)
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")

        with conn.cursor() as cur:
            cur.execute(
                "SELECT variant_id FROM catalog_rebuild_member"
                " WHERE generation_id=%s AND cohort='product_ready'"
                "  AND variant_id IS NOT NULL ORDER BY variant_id",
                (generation,),
            )
            ready_ids = [int(row["variant_id"]) for row in cur.fetchall()]
        if not ready_ids:
            raise SystemExit("S12 ABORT: zero product_ready variants; refusing empty universe")

        try:
            with conn.cursor() as cur:
                # -- universe lock (is_current stays 0 until members verify) --
                lock_doc = {
                    "contract": "rebuild-036-universe-lock-v1",
                    "generation": generation,
                    "memberVariantIds": ready_ids,
                    "policy": POLICY,
                }
                lock_sha = sha256_bytes(canonical_json(lock_doc))
                cur.execute(
                    "INSERT INTO market_universe_lock"
                    " (lock_sha256, effective_at, policy_json, member_count, is_current)"
                    " VALUES (%s,%s,%s,%s,0)"
                    " ON DUPLICATE KEY UPDATE id=LAST_INSERT_ID(id),"
                    "  member_count=VALUES(member_count)",
                    (
                        lock_sha, now_str,
                        canonical_json(POLICY).decode("utf-8"), len(ready_ids),
                    ),
                )
                lock_id = int(cur.lastrowid)
                signals = canonical_json({
                    "origin": "rebuild_036_product_ready", "generation": generation,
                }).decode("utf-8")
                for variant_id in ready_ids:
                    cur.execute(
                        "INSERT INTO market_universe_member"
                        " (universe_lock_id, variant_id, segment_code, member_role,"
                        "  market_rank, selection_signals_json)"
                        " VALUES (%s,%s,'tracked','candidate',NULL,%s)"
                        " ON DUPLICATE KEY UPDATE segment_code=VALUES(segment_code),"
                        "  member_role=VALUES(member_role),"
                        "  selection_signals_json=VALUES(selection_signals_json)",
                        (lock_id, variant_id, signals),
                    )
                cur.execute(
                    "SELECT COUNT(*) AS members, COUNT(DISTINCT variant_id) AS variants"
                    " FROM market_universe_member WHERE universe_lock_id=%s",
                    (lock_id,),
                )
                counts_row = cur.fetchone()
                if (
                    int(counts_row["members"]) != len(ready_ids)
                    or int(counts_row["variants"]) != len(ready_ids)
                ):
                    raise SystemExit("S12 ABORT: lock membership drifted while writing")
                cur.execute(
                    "UPDATE market_universe_lock SET is_current=0"
                    " WHERE is_current=1 AND id <> %s",
                    (lock_id,),
                )
                cur.execute(
                    "UPDATE market_universe_lock SET is_current=1 WHERE id=%s",
                    (lock_id,),
                )

                bridge = _activation_bridge_population(cur, generation, now_str)
                history = _activation_accept_history(cur, now_str, fingerprints)
                canonical = _activation_rank_and_accept(cur, ready_ids, now_str)

                for variant_id, rank in canonical["ranks"].items():
                    cur.execute(
                        "UPDATE market_universe_member SET market_rank=%s"
                        " WHERE universe_lock_id=%s AND variant_id=%s",
                        (rank, lock_id, variant_id),
                    )
                cur.execute(
                    "UPDATE cardz_rebuild_generation"
                    " SET activated_at=%s, activation_receipt_sha256=%s"
                    " WHERE generation_id=%s",
                    (now_str, receipt_sha, generation),
                )
                if cur.rowcount == 0:
                    raise SystemExit("S12 ABORT: generation row vanished")
            conn.commit()
        except Exception:
            conn.rollback()
            raise

        report = {
            "activated": True,
            "generation": generation,
            "receiptSha256": receipt_sha,
            "universeLockId": lock_id,
            "universeLockSha256": lock_sha,
            "members": len(ready_ids),
            "populationBridge": bridge,
            "historyAcceptance": history,
            "canonical": {
                "accepted": canonical["accepted"],
                "rankingGenerationSha256": canonical["rankingGenerationSha256"],
            },
            "activatedAt": now_str,
        }
        artifact_path = ROOT / "data" / "runtime" / "rebuild-036" / (
            f"activation-{generation}.json"
        )
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_bytes(canonical_json(report))
        print(json.dumps(report, ensure_ascii=False, indent=1, default=str))
        return 0
    finally:
        conn.close()


DISCOVERY_BASELINE = ROOT / "pipelines" / "discovery_coverage_baseline.json"
DISCOVERY_GAP_POP = 1000


def _discovery_gap_census(cur: Any) -> dict[str, int]:
    """Qualified, heavily-graded cards that no provider candidate reaches.

    A card in this census cannot enter the universe no matter how many
    generations run, because every downstream stage draws its working set from
    the cards that already hold a source identity. That is invisible from the
    inside: coverage over the working set stays at 100% while the working set
    itself is missing cards. It has to be counted against the CATALOG.

    Only an EXACT binding counts. A manual_review row means a candidate was
    proposed and not proven, and proposing candidates is cheap -- if unproven
    rows counted, seeding a batch of them would close this census while the
    cards stayed exactly as missing from the front end as before.
    """

    cur.execute(
        """
        SELECT v.tcg_code AS tcg, COUNT(*) AS gap
          FROM catalog_rebuild_member rm
          JOIN catalog_variant v ON v.id = rm.variant_id
         WHERE rm.generation_id = (
                 SELECT generation_id FROM catalog_rebuild_member
                  ORDER BY computed_at DESC LIMIT 1)
           AND rm.cohort <> 'non_qualified'
           AND rm.latest_psa10_population >= %s
           AND NOT EXISTS (
                 SELECT 1 FROM catalog_source_identity si
                  WHERE si.variant_id = v.id
                    AND si.source_code IN ('snkrdunk', 'snk_psa10', 'pricecharting')
                    AND si.match_status = 'exact')
         GROUP BY v.tcg_code
        """,
        (DISCOVERY_GAP_POP,),
    )
    return {str(row["tcg"]): int(row["gap"]) for row in cur.fetchall()}


def _assert_discovery_gap_not_worse(cur: Any) -> dict[str, Any]:
    """Ratchet: the no-candidate gap may shrink, never grow.

    297 One Piece cards had PSA10 population >= 1000 and 129 of them had no
    provider candidate at all, so they were absent from the front end for
    months while every acceptance gate reported healthy -- the gates only ever
    looked at cards that already had a candidate. The baseline lives in the
    repo rather than in a table or an artifact directory precisely so that
    lowering it is a commit somebody can see, and so a deleted file fails the
    run instead of silently passing it.
    """

    census = _discovery_gap_census(cur)
    if not DISCOVERY_BASELINE.is_file():
        raise SystemExit(
            f"daily-accept ABORT: discovery baseline missing at {DISCOVERY_BASELINE}."
            " Restore it from git; an absent baseline is not a passing check."
        )
    baseline = json.loads(DISCOVERY_BASELINE.read_text(encoding="utf-8"))
    allowed = {str(k): int(v) for k, v in (baseline.get("noCandidateAtPop") or {}).items()}
    if int(baseline.get("population", 0)) != DISCOVERY_GAP_POP:
        raise SystemExit(
            "daily-accept ABORT: discovery baseline was written for population"
            f" {baseline.get('population')}, this build ratchets at {DISCOVERY_GAP_POP}"
        )
    worse = {
        tcg: {"now": count, "allowed": allowed.get(tcg, 0)}
        for tcg, count in census.items()
        if count > allowed.get(tcg, 0)
    }
    if worse:
        raise SystemExit(
            "daily-accept ABORT: cards with PSA10 population >="
            f" {DISCOVERY_GAP_POP} and no provider candidate grew past the"
            f" baseline: {json.dumps(worse, sort_keys=True)}."
            " Run snk-identity-discover / build_pc_gap_shard before accepting."
        )
    return {
        "population": DISCOVERY_GAP_POP,
        "census": census,
        "baseline": allowed,
        "improved": {
            tcg: {"was": allowed[tcg], "now": census.get(tcg, 0)}
            for tcg in allowed
            if census.get(tcg, 0) < allowed[tcg]
        },
    }


DAILY_ACCEPT_CANONICAL_FIELDS = (
    "accepted",
    "ranked",
    "awaitingFreshPrice",
    "rankingGenerationSha256",
)


def _daily_accept_canonical_receipt(canonical: Mapping[str, Any]) -> dict[str, Any]:
    """Keep every count needed to judge a daily ranking generation.

    Direct indexing is deliberate: adding a new acceptance result without one
    of these decision fields must stop the run instead of emitting a plausible
    but incomplete receipt.
    """

    return {field: canonical[field] for field in DAILY_ACCEPT_CANONICAL_FIELDS}


def cmd_daily_accept(args: argparse.Namespace) -> int:
    """Nightly acceptance + re-rank for the CURRENT activated universe.

    Reuses the S12 acceptance lanes without rewriting the universe lock:
    fresh collector evidence (price/pop/sales) becomes accepted history,
    the market-cap ranking recomputes over the same members, and §3.11b
    bumps accepted_at even when content is identical so the FE generation
    always flips to "accepted tonight". Sales stay manifest-gated: the
    activation manifests plus fingerprints landed by ingest runs completed
    after activation — rows from pre-freeze recipe writers can never enter.
    During a writer freeze the first INSERT fails and everything rolls
    back, so this command is naturally inert mid-rebuild.
    """

    from datetime import datetime, timezone

    credentials = args.credentials_env or DAILY_CREDENTIALS_ENV
    conn = connect(credentials)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, lock_sha256, member_count FROM market_universe_lock"
                " WHERE is_current=1"
            )
            locks = cur.fetchall()
            if len(locks) != 1:
                raise SystemExit(
                    f"daily-accept ABORT: expected exactly one current universe lock, found {len(locks)}"
                )
            lock_id = int(locks[0]["id"])
            lock_sha = str(locks[0]["lock_sha256"])
            cur.execute(
                "SELECT variant_id FROM market_universe_member"
                " WHERE universe_lock_id=%s ORDER BY variant_id",
                (lock_id,),
            )
            ready_ids = [int(row["variant_id"]) for row in cur.fetchall()]
            if not ready_ids:
                raise SystemExit("daily-accept ABORT: current lock has zero members")
            # The gate existed in operator_control.py but nothing called it, so
            # accepted_at could move while every collector checkpoint was old.
            # Importing here keeps the gate in its existing owner and runs it
            # before the first write of the daily transaction.
            import operator_control

            freshness = operator_control._active_checkpoint_gate(
                cur, active_ids=set(ready_ids)
            )
            cur.execute(
                "SELECT generation_id, activated_at FROM cardz_rebuild_generation"
                " WHERE activated_at IS NOT NULL ORDER BY activated_at DESC LIMIT 1"
            )
            generation_row = cur.fetchone()
            if not generation_row:
                raise SystemExit("daily-accept ABORT: no activated generation on record")
            generation = str(generation_row["generation_id"])
            activated_at = generation_row["activated_at"]
            # Before accepting anything, prove the catalog is not quietly
            # losing cards that no provider candidate reaches. Accepting a
            # healthy-looking universe that is missing its biggest cards is
            # the failure this command exists to stop repeating.
            discovery_gap = _assert_discovery_gap_not_worse(cur)

        # Tonight's discovery proves identities for cards whose price rows were
        # collateral-quarantined months ago. Nothing else would ever look at
        # those rows again, so the card would keep its new binding and still
        # have no price. Same predicate the rebuild stage uses.
        released = release_collateral_price_quarantine(conn)

        fingerprints = _load_sales_fingerprints(generation)
        manifest_count = len(fingerprints)
        with conn.cursor() as cur:
            # Post-activation sale rows carry the current recipes by
            # construction: the freeze stopped every legacy writer, and the
            # only sale writers since are the receipted collector ingests.
            cur.execute(
                """
                SELECT DISTINCT s.transaction_fingerprint
                FROM market_sale_observation s
                INNER JOIN market_ingest_run r ON r.id=s.run_id
                WHERE r.status IN ('complete','completed') AND r.started_at >= %s
                """,
                (activated_at,),
            )
            for row in cur.fetchall():
                value = str(row["transaction_fingerprint"] or "")
                if value:
                    fingerprints.add(value)
            daily_manifest_stats = _load_daily_sales_manifests(
                cur, activated_at, fingerprints
            )

        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")
        try:
            with conn.cursor() as cur:
                history = _activation_accept_history(cur, now_str, fingerprints)
                canonical = _activation_rank_and_accept(cur, ready_ids, now_str)
                for variant_id, rank in canonical["ranks"].items():
                    cur.execute(
                        "UPDATE market_universe_member SET market_rank=%s"
                        " WHERE universe_lock_id=%s AND variant_id=%s",
                        (rank, lock_id, variant_id),
                    )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

        report = {
            "dailyAccept": True,
            "generation": generation,
            "universeLockId": lock_id,
            "universeLockSha256": lock_sha,
            "members": len(ready_ids),
            "salesFingerprints": {
                "manifest": manifest_count,
                "postActivation": len(fingerprints) - manifest_count,
                "dailyManifests": daily_manifest_stats,
            },
            "historyAcceptance": history,
            "priceQuarantineReleased": released,
            "discoveryGap": discovery_gap,
            "freshness36h": freshness,
            "canonical": _daily_accept_canonical_receipt(canonical),
            "acceptedAt": now_str,
        }
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        artifact_path = ROOT / "data" / "runtime" / "rebuild-036" / (
            f"daily-accept-{stamp}.json"
        )
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_bytes(canonical_json(report))
        print(json.dumps(report, ensure_ascii=False, indent=1, default=str))
        return 0
    finally:
        conn.close()


# PC page brackets spell prints in full ("Treasure Rare") while the catalog
# stores the abbreviated print codes ("tr"). Explicit, defensible pairs only —
# anything not listed stays manual_review and lands in the report instead.
_PC_BRACKET_SYNONYMS: dict[str, frozenset[str]] = {
    "aa": frozenset({"alternate art", "alt art", "alternative art"}),
    "tr": frozenset({"treasure rare"}),
    "mr": frozenset({"manga rare", "manga"}),
    # The manga PARALLEL, which is not the "manga rare" rarity above. GemRate
    # writes "Manga Alternate Art" and PC writes the same two words the other
    # way round; both name the comic-art parallel of an existing card. A bare
    # "[Manga]" is deliberately NOT here -- it belongs to "mr", and pages
    # carrying it were seen against cards whose own treatment is plain
    # Alternate Art, which is a different card and must keep holding.
    "manga": frozenset({"alternate art manga", "manga alternate art"}),
    "1st": frozenset({"1st edition", "first edition"}),
    "wanted": frozenset({"wanted", "wanted poster"}),
    # "SP" is One Piece's own abbreviation of "Special"; both spellings appear
    # on PC pages for the same treatment. Deliberately excluded: [SP Foil] and
    # [SP Gold] are separate PC products, and the catalog only carries the
    # coarse "sp" code, so accepting them could bind the wrong physical card.
    "sp": frozenset({"sp alternate art", "special alternate art"}),
    # Pokemon Master Ball is a 1:1 pairing; the catalog spells out the finish
    # ("master ball reverse holo") where the page bracket says just the seal.
    "mb": frozenset({"master ball"}),
}

# printing_code values that claim no treatment, so a bracket-less page heading
# is allowed to be the card. Anything else names a treatment the page would
# have to spell out.
_PC_BASE_PRINTINGS = frozenset({"", "base"})


def _pc_page_product_id(html: str) -> str:
    """The page's own numeric product id; empty when the page doesn't say.

    Redirect captures ("…_r.html") land on whatever product PC now serves, so
    the binding's external id must be re-proved from the page body itself."""

    for pattern in (
        r"VGPC\.product\s*=\s*\{[^{}]*?\bid\b\s*[:=]\s*(\d+)",
        r'data-product-id="(\d+)"',
    ):
        match = re.search(pattern, html)
        if match:
            return match.group(1)
    return ""


def pc_capture_for_product(
    pages_dir: Path, variant_id: int, product_id: str, mapped: Path | None = None
) -> Path | None:
    """The capture that IS this product's page, not whichever sorts first.

    A variant collects several files under `{variant}_*.html`: product pages,
    and the search-results pages a lane saved while looking for one. Picking
    sorted()[0] let "2026_search-products-q-one-piece-Shanks-001….html" win on
    the letter 'e' over "2026_shanks-magazine-op09-001_r.html", and on
    2026-08-10 five cards were held `page_parse_failed: canonical_not_product`
    with their own product page sitting in the same folder.

    Nothing here decides whether a binding is right -- the caller still demands
    page id == bound id == map id and the whole contract after it. This only
    stops an arbitrary file from answering a question it is not about."""

    candidates: list[Path] = []
    if mapped is not None and mapped.is_file():
        candidates.append(mapped)
    candidates.extend(
        path for path in sorted(pages_dir.glob(f"{variant_id}_*.html"))
        if path not in candidates
    )
    fallback: Path | None = None
    for path in candidates:
        html = path.read_text(encoding="utf-8", errors="replace")
        if _pc_page_product_id(html) != str(product_id):
            continue
        # A search-results page can carry a product id too, so agreeing on the
        # id is not enough: it has to parse as that product's own page.
        identity, _ = _pc_page_identity(html)
        if identity is not None:
            return path
        if fallback is None:
            fallback = path
    return fallback or (candidates[0] if candidates else None)


def _pc_print_signature_ok(page_parallel: str, row: Mapping[str, Any]) -> bool:
    """Phase-D parallel agreement plus the abbreviation vocabulary.

    printing_code is the stronger catalog evidence when present (mirrors
    _print_signature_agrees); the synonym table only ever maps a page bracket
    onto that code, never loosens the no-bracket path."""

    variant_parallel = str(row.get("parallel_code") or "")
    if _parallel_agrees(page_parallel, variant_parallel):
        return True
    if not page_parallel:
        # A heading with no bracket is PriceCharting's base print. Letting it
        # satisfy a variant whose printing_code names a treatment is how seven
        # live bindings ended up on the cheap card: "Yamato OP01-121" (base)
        # was bound exact to OP05 Yamato Special Alternate Art, and while it
        # sat there the base card's own variant could not claim its page.
        # Measured 2026-08-09 over all 928 exact PC bindings: 919 unaffected,
        # 7 refused, and all 7 were parallels bound to a base print.
        if _norm_text(str(row.get("printing_code") or "")) not in _PC_BASE_PRINTINGS:
            return False
        return _pc_rarity_only_parallel(variant_parallel)
    bracket = _norm_text(page_parallel)
    printing = _norm_text(str(row.get("printing_code") or ""))
    if printing and bracket:
        if bracket == printing or bracket == f"{printing} edition":
            return True
        if bracket in _PC_BRACKET_SYNONYMS.get(printing, frozenset()):
            return True
        # Compound codes ("aa-errata"): collapse the long-forms inside the
        # bracket to their codes, then still demand exact equality.
        collapsed = bracket
        for code, longforms in _PC_BRACKET_SYNONYMS.items():
            for longform in longforms:
                collapsed = collapsed.replace(longform, code)
        collapsed = " ".join(collapsed.split())
        if collapsed == printing:
            return True
        # Compound codes are written "aa-errata" in the catalog and "Alternate
        # Art Errata" on the page: same tokens, different separator. Equality
        # is still demanded, only the separator is normalised.
        if collapsed.replace("-", " ") == printing.replace("-", " "):
            return True
    return False


def _pc_map_url_by_product() -> dict[str, str]:
    """product id -> PriceCharting URL, from every private source map on disk.

    The map rows were built out of page HTML without unescaping, so a console
    slug arrives as "pokemon-scarlet-&amp;-violet-151" while the real URL
    carries a literal ampersand -- the same entity leak the heading parser
    had. Fetching the escaped form gets a 404, the page is recorded missing,
    and the card drops out of product_ready for a reason that has nothing to
    do with the card."""

    urls: dict[str, str] = {}
    for path in sorted(ROOT.glob("data/runtime/private-source-map/*.jsonl")):
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            product_id = str(row.get("pc_product_id") or "")
            raw = row.get("pc_url")
            if product_id and raw and product_id not in urls:
                urls[product_id] = html_unescape(str(raw))
    return urls


def _pc_fetch_missing_pages(conn, pages_dir: Path, map_html_by_variant: dict[int, str]) -> dict[str, Any]:
    """Capture the pages pc-identity-reverify would hold as missing or unparseable.

    Run by hand on 2026-08-10 for 25 bindings; 20 came back and stopped being
    held. The command holds a binding when the page is not on disk, which is a
    statement about the folder rather than about the binding, so the fetch
    belongs next to the check that needs it. Same capture contract as
    pc_identity_discover: fetch, prove the page's own product id is the one
    asked for, keep it or delete it."""

    import pricecharting_cf_session as cf_session

    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT si.external_entity_id AS pid, si.variant_id"
            "  FROM catalog_source_identity si"
            " WHERE si.source_code='pricecharting'"
            "   AND si.match_status IN ('manual_review','rejected')"
            f"   AND {NOT_A_REJECTION_VERDICT_SQL}"
        )
        candidates = cursor.fetchall()

    urls = _pc_map_url_by_product()
    counts = {"considered": len(candidates), "alreadyGood": 0, "noUrl": 0,
              "fetched": 0, "fetchFailed": 0, "pageSaysOther": 0}
    for row in candidates:
        pid = str(row["pid"])
        variant_id = int(row["variant_id"])
        mapped = map_html_by_variant.get(variant_id, "")
        existing = pc_capture_for_product(
            pages_dir, variant_id, pid, (ROOT / mapped) if mapped else None
        )
        if existing is not None:
            body = existing.read_text(encoding="utf-8", errors="replace")
            if _pc_page_product_id(body) == pid and _pc_page_identity(body)[0] is not None:
                counts["alreadyGood"] += 1
                continue
        url = urls.get(pid)
        if not url:
            counts["noUrl"] += 1
            continue
        out = pages_dir / f"{variant_id}_{pid}.html"
        code = cf_session.cmd_fetch(url, out, timeout_s=90)
        if code != 0 or not out.is_file():
            counts["fetchFailed"] += 1
            continue
        body = out.read_text(encoding="utf-8", errors="replace")
        if _pc_page_product_id(body) != pid:
            out.unlink(missing_ok=True)
            counts["pageSaysOther"] += 1
            continue
        counts["fetched"] += 1
        time.sleep(2.0)
    print(json.dumps({"phase": "pc-fetch-missing", "counts": counts}, ensure_ascii=False), flush=True)
    return counts


def cmd_pc_identity_reverify(args: argparse.Namespace) -> int:
    """Re-verify manual_review PC bindings against freshly captured pages.

    S6 Phase D re-derived bindings from the replay archive; sweeps since then
    capture newer pages under data/private/pricecharting_session/html/full900.
    This command applies the same fail-closed contract to those captures and
    promotes a binding to exact ONLY when the page itself proves it:
    page product id == bound external id == map product id, no hard identity
    conflicts, and an agreeing print signature. Everything else keeps its
    status and is listed in the report for a human ruling. Never touches
    exact or conflict rows, and never overturns a rejection anybody reasoned
    about.

    It DOES reconsider a rejection nobody reasoned about. psa_identity_repair
    rejects every non-gemrate binding on a card whose gemrate identity would not
    resolve, without examining those bindings; skipping the whole status meant
    those cards could never come back even after the gemrate side was repaired.
    REJECTION_VERDICT_ACTIONS draws the line, and the page still has to prove the
    binding on its own -- reconsidering costs a card nothing, because the
    fail-closed contract below is the same one every other row faces."""

    from datetime import datetime, timezone

    # Imported here, not at module scope: op_identity_rules imports this
    # module for its text normaliser, so a top-level import would be circular.
    import op_identity_rules

    credentials = args.credentials_env or DAILY_CREDENTIALS_ENV
    pages_dir = args.pages_dir or (
        ROOT / "data" / "private" / "pricecharting_session" / "html" / "full900"
    )
    if not pages_dir.is_dir():
        raise SystemExit(f"pc-identity-reverify ABORT: pages dir missing: {pages_dir}")

    map_product_by_variant: dict[int, str] = {}
    map_html_by_variant: dict[int, str] = {}
    map_path = args.map or (
        ROOT / "data" / "runtime" / "private-source-map" / "c11_pc_ebay_map_full900.jsonl"
    )
    if map_path.is_file():
        for line in map_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            variant_id = int(entry.get("variant_id") or 0)
            if variant_id:
                map_product_by_variant[variant_id] = str(entry.get("pc_product_id") or "")
                map_html_by_variant[variant_id] = str(
                    entry.get("html_path") or entry.get("htmlPath") or ""
                )

    conn = connect(credentials)
    if getattr(args, "fetch_missing", False):
        _pc_fetch_missing_pages(conn, pages_dir, map_html_by_variant)
    counts = {
        "reviewBindings": 0, "quarantineReconsidered": 0,
        "pageMissing": 0, "pageParseFailures": 0,
        "pageProductMismatch": 0, "mapProductMismatch": 0, "hardConflicts": 0,
        "productMismatch": 0, "printSignatureMismatch": 0, "promoted": 0,
    }
    promoted: list[dict[str, Any]] = []
    held: list[dict[str, Any]] = []
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT si.external_entity_id AS pid, si.variant_id,"
                " si.match_status,"
                # GemRate's own wording for the printing. For a booster card it
                # names the treatment; for a promo it names the product, and
                # the product-agreement rule below needs to tell them apart.
                " (SELECT JSON_UNQUOTE(JSON_EXTRACT(rm.detail_json,"
                "         '$.fingerprint.parallel'))"
                "    FROM catalog_rebuild_member rm WHERE rm.variant_id=v.id"
                "   ORDER BY rm.computed_at DESC LIMIT 1) AS fp_parallel,"
                " v.tcg_code, v.card_language, v.set_name, v.collector_number,"
                " v.set_code AS v_set_code, v.printing_code AS v_printing_code,"
                " p.parallel_code, p.printing_code, p.canonical_printing_sha256,"
                " p.tcg_code AS p_tcg_code, p.card_language AS p_card_language,"
                " p.set_code AS p_set_code, p.collector_number AS p_collector_number,"
                " p.edition_code AS p_edition_code, p.finish_code AS p_finish_code"
                " FROM catalog_source_identity si"
                " JOIN catalog_variant v ON v.id=si.variant_id"
                " LEFT JOIN catalog_printing_identity p ON p.variant_id=v.id"
                " WHERE si.source_code='pricecharting'"
                "   AND si.match_status IN ('manual_review','rejected')"
                f"   AND {NOT_A_REJECTION_VERDICT_SQL}"
            )
            bindings = cursor.fetchall()

        updates: list[tuple[str, str, str, dict[str, Any], tuple[str, ...], Path]] = []
        for row in bindings:
            counts["reviewBindings"] += 1
            if str(row["match_status"]) == "rejected":
                counts["quarantineReconsidered"] += 1
            pid = str(row["pid"])
            variant_id = int(row["variant_id"])

            def hold(reason: str, detail: str = "") -> None:
                held.append({
                    "variant_id": variant_id, "pid": pid,
                    "reason": reason, "detail": detail,
                })

            map_product = map_product_by_variant.get(variant_id, "")
            if map_product and map_product != pid:
                counts["mapProductMismatch"] += 1
                hold("map_product_mismatch", f"map={map_product}")
                continue
            mapped_html = map_html_by_variant.get(variant_id, "")
            html_path = pc_capture_for_product(
                pages_dir, variant_id, pid,
                (ROOT / mapped_html) if mapped_html else None,
            )
            if html_path is None:
                counts["pageMissing"] += 1
                hold("page_missing")
                continue
            html = html_path.read_text(encoding="utf-8", errors="replace")
            page_product = _pc_page_product_id(html)
            if page_product != pid:
                counts["pageProductMismatch"] += 1
                hold("page_product_mismatch", f"page={page_product or '?'}")
                continue
            identity, reason = _pc_page_identity(html)
            if identity is None:
                counts["pageParseFailures"] += 1
                hold("page_parse_failed", reason)
                continue
            pseudo_fp = {
                "cardNumber": identity["collector"],
                "derivedLanguage": identity["language"],
                "setName": identity["setText"],
            }
            conflicts = _fingerprint_variant_conflicts(pseudo_fp, row)
            if identity["tcg"] and str(row["tcg_code"] or "") and \
                    identity["tcg"] != str(row["tcg_code"]):
                conflicts.append(f"tcg:{identity['tcg']}!={row['tcg_code']}")
            if conflicts:
                counts["hardConflicts"] += 1
                hold("hard_conflict", ";".join(conflicts))
                continue
            # A One Piece card keeps its ORIGINAL number when reprinted, so
            # the conflict check above passing on an agreeing collector number
            # is not proof of the same product: a search for "Boa Hancock 038"
            # resolved the PSA Magazine promo onto the OP07 booster Alternate
            # Art, and every check up to here agreed. The rule is applied only
            # where its vocabulary was derived.
            if str(row["tcg_code"] or "") == "one-piece":
                same_product, why = False, "product_mismatch:no_set_name"
                for candidate_set in set_names_a_card_could_carry(row):
                    same_product, why = op_identity_rules.product_agrees(
                        candidate_set, str(row["fp_parallel"] or ""),
                        identity["setText"], identity["canonicalUrl"],
                        identity["heading"],
                    )
                    if same_product:
                        break
                if not same_product:
                    counts["productMismatch"] += 1
                    hold("product_mismatch", why)
                    continue
            if not _pc_print_signature_ok(identity["parallel"], row):
                counts["printSignatureMismatch"] += 1
                hold(
                    "print_signature_mismatch",
                    f"page=[{identity['parallel']}] printing={row['printing_code']}"
                    f" parallel={row['parallel_code']}",
                )
                continue
            digest = sha256_file(html_path)
            evidence = {
                "providerClaims": {
                    "tcgCode": identity["tcg"],
                    "cardLanguage": identity["language"],
                    "collectorNumber": identity["collector"],
                    "setCode": "",
                    "printingCode": "",
                    "parallelCode": identity["parallel"],
                },
                "evidence": {
                    "type": EVIDENCE_TYPE_PROVIDER_PAGE,
                    "sha256": digest,
                    "path": html_path.relative_to(ROOT).as_posix(),
                    "canonicalUrl": identity["canonicalUrl"],
                    "pageHeading": identity["heading"],
                    "capturedAt": datetime.fromtimestamp(
                        html_path.stat().st_mtime, tz=timezone.utc
                    ).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "generation": "pc_reverify_full900",
                },
            }
            evidence_sha = sha256_bytes(canonical_json(evidence))
            if row["canonical_printing_sha256"]:
                mirror = (
                    str(row["p_tcg_code"] or ""), str(row["p_card_language"] or ""),
                    str(row["p_set_code"] or ""), str(row["p_collector_number"] or ""),
                    str(row["printing_code"] or ""), str(row["parallel_code"] or ""),
                    str(row["p_edition_code"] or ""), str(row["p_finish_code"] or ""),
                )
            else:
                mirror = (
                    str(row["tcg_code"] or ""), str(row["card_language"] or ""),
                    str(row["v_set_code"] or ""), str(row["collector_number"] or ""),
                    str(row["v_printing_code"] or ""), "", "", "",
                )
            updates.append(
                (pid, evidence_sha, identity["collector"],
                 {"evidence": evidence}, mirror, html_path)
            )
            promoted.append({
                "variant_id": variant_id, "pid": pid,
                "bracket": identity["parallel"],
                "printing_code": str(row["printing_code"] or ""),
                "evidence_sha256": evidence_sha,
            })

        if args.write and updates:
            try:
                with conn.cursor() as cursor:
                    for pid, evidence_sha, collector, extra, mirror, html_path in updates:
                        evidence = extra["evidence"]
                        cursor.execute(
                            "INSERT INTO catalog_provider_capture_receipt (source_code,"
                            " external_entity_id, capture_sha256, capture_path,"
                            " captured_at, generation_id, parser_version)"
                            " VALUES ('pricecharting', %s, %s, %s, %s, %s, %s)"
                            " ON DUPLICATE KEY UPDATE"
                            " capture_sha256=VALUES(capture_sha256),"
                            " capture_path=VALUES(capture_path),"
                            " captured_at=VALUES(captured_at),"
                            " generation_id=VALUES(generation_id),"
                            " parser_version=VALUES(parser_version)",
                            (
                                pid, evidence["evidence"]["sha256"],
                                evidence["evidence"]["path"][:500],
                                datetime.strptime(
                                    evidence["evidence"]["capturedAt"],
                                    "%Y-%m-%dT%H:%M:%SZ",
                                ).replace(tzinfo=timezone.utc),
                                "pc_reverify_full900", "pc_reverify_v1",
                            ),
                        )
                        cursor.execute(
                            "UPDATE catalog_source_identity SET match_status='exact',"
                            " evidence_sha256=%s, source_product_number=%s,"
                            " bind_evidence_json=%s, bound_tcg_code=%s,"
                            " bound_card_language=%s, bound_set_code=%s,"
                            " bound_collector_number=%s, bound_printing_code=%s,"
                            " bound_parallel_code=%s, bound_edition_code=%s,"
                            " bound_finish_code=%s"
                            " WHERE source_code='pricecharting'"
                            " AND external_entity_id=%s"
                            # Re-checked against the stored row, so a status that
                            # moved since the read above changes zero rows rather
                            # than overwriting whatever it moved to. 'exact' and
                            # 'conflict' are excluded by naming the two statuses
                            # this command is allowed to promote.
                            " AND match_status IN ('manual_review','rejected')"
                            f" AND {NOT_A_REJECTION_VERDICT_SQL}",
                            (
                                evidence_sha, str(collector or "")[:64],
                                json.dumps(evidence, ensure_ascii=False, sort_keys=True),
                                mirror[0][:32], mirror[1][:8], mirror[2][:24],
                                mirror[3][:96], mirror[4][:24], mirror[5][:64],
                                mirror[6][:191], mirror[7][:64], pid,
                            ),
                        )
                conn.commit()
                counts["promoted"] = len(updates)
            except Exception:
                conn.rollback()
                raise
        elif updates:
            counts["promoted"] = 0

        report = {
            "pcIdentityReverify": True,
            "write": bool(args.write),
            "pagesDir": pages_dir.relative_to(ROOT).as_posix()
            if pages_dir.is_relative_to(ROOT) else str(pages_dir),
            "counts": counts,
            "promotable": len(updates),
            "promotedSample": promoted[:40],
            "held": held,
        }
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        artifact_path = ROOT / "data" / "runtime" / "rebuild-036" / (
            f"pc-identity-reverify-{stamp}.json"
        )
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_bytes(canonical_json(report))
        print(json.dumps(report, ensure_ascii=False, indent=1, default=str))
        return 0
    finally:
        conn.close()


def cmd_snk_identity_reverify(args: argparse.Namespace) -> int:
    """Re-verify held SNK bindings against freshly fetched masters.

    Same acceptance contract as S7 (stage_snk_refresh): the provider master
    itself must resolve a 1-card PSA10 variant, assert no hard identity
    conflict, and agree on the print signature. Promotions write the same
    receipt + bind evidence shape S7 writes. Everything held is listed with a
    reason for a human ruling.

    Like cmd_pc_identity_reverify, this reconsiders a rejection nobody
    reasoned about. This lane used to read manual_review only, which is what
    it was born with -- REJECTION_VERDICT_ACTIONS arrived later and the PC
    lane learned it while this one did not. The consequence was measurable:
    82 snkrdunk rows sat at match_status='rejected' with their own
    bind_evidence_json still reading action='confirm', collateral from
    psa_identity_repair's blanket non-gemrate reject, and no lane in the repo
    could ever look at them again. Real verdicts and the 034 red list are
    still excluded by NOT_A_REJECTION_VERDICT_SQL, and reconsidering costs a
    card nothing because the fail-closed contract below is the same one every
    other row faces."""

    from datetime import datetime, timezone

    import snk_market_data

    credentials = args.credentials_env or DAILY_CREDENTIALS_ENV
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base_dir = ROOT / "data" / "runtime" / "rebuild-036" / "snk-identity-reverify" / stamp
    items_dir = base_dir / "items"
    items_dir.mkdir(parents=True, exist_ok=True)
    parser_version = "snkmd_" + sha256_file(
        ROOT / "pipelines" / "snk_market_data.py"
    )[:12]

    conn = connect(credentials)
    counts = {
        "reviewBindings": 0, "quarantineReconsidered": 0,
        "pageMissing": 0, "noPsa10OneCard": 0,
        "hardConflicts": 0, "parallelSoftMismatch": 0, "promoted": 0,
    }
    promoted: list[dict[str, Any]] = []
    held: list[dict[str, Any]] = []
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT si.external_entity_id AS iid, si.variant_id,"
                " si.match_status,"
                " v.tcg_code, v.card_language, v.set_name, v.collector_number,"
                " v.canonical_name,"
                " v.set_code AS v_set_code, v.printing_code AS v_printing_code,"
                " p.parallel_code, p.printing_code, p.canonical_printing_sha256,"
                " p.tcg_code AS p_tcg_code, p.card_language AS p_card_language,"
                " p.set_code AS p_set_code, p.collector_number AS p_collector_number,"
                " p.edition_code AS p_edition_code, p.finish_code AS p_finish_code"
                " FROM catalog_source_identity si"
                " JOIN catalog_variant v ON v.id=si.variant_id"
                " LEFT JOIN catalog_printing_identity p ON p.variant_id=v.id"
                " WHERE si.source_code='snkrdunk'"
                "   AND si.match_status IN ('manual_review','rejected')"
                f"   AND {NOT_A_REJECTION_VERDICT_SQL}"
            )
            bindings = cursor.fetchall()

        worklist = sorted({
            int(row["iid"]) for row in bindings if str(row["iid"]).isdigit()
        })
        harvest_path = base_dir / "snk_reverify_harvest.jsonl"
        snk_market_data.run(
            worklist, harvest_path, delay=0.0,
            condition_code=snk_market_data.PSA10_CONDITION,
            run_id=f"snk_reverify_{stamp}", workers=8,
        )
        if not harvest_path.is_file():
            partial = harvest_path.with_suffix(harvest_path.suffix + ".partial")
            if not partial.is_file():
                raise SystemExit(f"snk reverify harvest produced no file: {harvest_path}")
            harvest_path = partial
        rows_by_id: dict[int, dict[str, Any]] = {}
        with harvest_path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                payload = json.loads(line)
                item_id = payload.get("item_id")
                if isinstance(item_id, int):
                    rows_by_id[item_id] = payload

        updates: list[tuple[str, int, str, str, dict[str, Any], tuple[str, ...]]] = []
        for row in bindings:
            counts["reviewBindings"] += 1
            if str(row["match_status"]) == "rejected":
                counts["quarantineReconsidered"] += 1
            iid_str = str(row["iid"])
            variant_id = int(row["variant_id"])

            def hold(reason: str, detail: str = "") -> None:
                held.append({
                    "variant_id": variant_id, "iid": iid_str,
                    "reason": reason, "detail": detail,
                })

            if not iid_str.isdigit():
                counts["pageMissing"] += 1
                hold("non_numeric_id")
                continue
            payload = rows_by_id.get(int(iid_str))
            if payload is None or payload.get("error"):
                counts["pageMissing"] += 1
                hold("page_missing", str((payload or {}).get("error") or ""))
                continue
            if not payload.get("quantity_variant_id"):
                counts["noPsa10OneCard"] += 1
                hold("no_psa10_1card_variant")
                continue
            master = (payload.get("source_payload") or {}).get("master") or {}
            master_name = str(master.get("name") or "")
            localized = str(master.get("localizedName") or "")
            product_number = str(payload.get("product_number") or "").strip()
            language = _snk_language(master_name, localized)
            tcg = _snk_tcg(master_name, localized)
            claim = _snk_collector_claim(master_name, localized, product_number)
            pseudo_fp = {
                "cardNumber": _snk_claim_number(claim),
                "derivedLanguage": language,
                "setName": f"{master_name} {localized}",
            }
            conflicts = _fingerprint_variant_conflicts(pseudo_fp, row)
            # Same supersede rule as S7: the bracket designation's own set
            # claim beats the noisy title-vs-set-name token comparison.
            if snk_claim_set_agrees(claim, row):
                conflicts = [
                    c for c in conflicts
                    if not c.startswith("set:") and not c.startswith("set_code:")
                ]
            if tcg and str(row["tcg_code"] or "") and tcg != str(row["tcg_code"]):
                conflicts.append(f"tcg:{tcg}!={row['tcg_code']}")
            if not claim:
                conflicts.append("product_number_missing")
            snk_mirror = _snk_treatment_mirror(master_name, localized)
            variant_blob = " ".join((
                str(row["parallel_code"] or ""),
                str(row["printing_code"] or ""),
                str(row["v_printing_code"] or ""),
            ))
            variant_mirror = "ミラー" in variant_blob or bool(
                re.search(r"(?i)mirror", variant_blob)
            )
            if snk_mirror != variant_mirror:
                conflicts.append(f"parallel:mirror {snk_mirror}!={variant_mirror}")
            if conflicts:
                counts["hardConflicts"] += 1
                hold("hard_conflict", ";".join(conflicts))
                continue
            snk_parallel = "parallel" if (
                "パラレル" in master_name or "parallel" in localized.casefold()
            ) else ""
            variant_parallel = str(row["parallel_code"] or "")
            parallel_ok = _parallel_agrees(snk_parallel, variant_parallel)
            if not parallel_ok and not snk_parallel:
                parallel_ok = _pc_rarity_only_parallel(variant_parallel)
            if not parallel_ok:
                counts["parallelSoftMismatch"] += 1
                hold(
                    "parallel_soft_mismatch",
                    f"snk=[{snk_parallel}] parallel={variant_parallel}",
                )
                continue

            item_id = int(iid_str)
            evidence_doc = {
                "itemId": item_id,
                "master": master,
                "conditionFilter": payload.get("condition_filter"),
                "quantityVariantId": payload.get("quantity_variant_id"),
                "productNumber": payload.get("product_number"),
                "imageUrl": payload.get("image_url"),
                "fetchedAt": payload.get("fetched_at"),
            }
            blob = canonical_json(evidence_doc)
            item_path = items_dir / f"{item_id}.json"
            item_path.write_bytes(blob)
            digest = sha256_bytes(blob)
            rel = item_path.relative_to(ROOT).as_posix()
            fetched = str(payload.get("fetched_at") or "")
            evidence = {
                "providerClaims": {
                    "tcgCode": tcg,
                    "cardLanguage": language,
                    "collectorNumber": claim,
                    "setCode": "",
                    "printingCode": "",
                    "parallelCode": snk_parallel,
                },
                "evidence": {
                    "type": EVIDENCE_TYPE_PROVIDER_PAGE,
                    "sha256": digest,
                    "path": rel,
                    "canonicalUrl": f"https://snkrdunk.com/en/trading-cards/{item_id}",
                    "capturedAt": fetched,
                    "generation": "snk_reverify",
                },
            }
            evidence_sha = sha256_bytes(canonical_json(evidence))
            if row["canonical_printing_sha256"]:
                mirror = (
                    str(row["p_tcg_code"] or ""), str(row["p_card_language"] or ""),
                    str(row["p_set_code"] or ""), str(row["p_collector_number"] or ""),
                    str(row["printing_code"] or ""), str(row["parallel_code"] or ""),
                    str(row["p_edition_code"] or ""), str(row["p_finish_code"] or ""),
                )
            else:
                mirror = (
                    str(row["tcg_code"] or ""), str(row["card_language"] or ""),
                    str(row["v_set_code"] or ""), str(row["collector_number"] or ""),
                    str(row["v_printing_code"] or ""), "", "", "",
                )
            updates.append(
                (iid_str, variant_id, evidence_sha, claim,
                 {"evidence": evidence, "fetched": fetched}, mirror)
            )
            promoted.append({
                "variant_id": variant_id, "iid": iid_str,
                "claim": claim, "evidence_sha256": evidence_sha,
            })

        if args.write and updates:
            try:
                with conn.cursor() as cursor:
                    for iid, variant_id, evidence_sha, claim, extra, mirror in updates:
                        evidence = extra["evidence"]
                        fetched = extra["fetched"]
                        captured_at = datetime.strptime(
                            fetched, "%Y-%m-%dT%H:%M:%S%z"
                        ) if fetched else datetime.now(timezone.utc)
                        cursor.execute(
                            "INSERT INTO catalog_provider_capture_receipt (source_code,"
                            " external_entity_id, capture_sha256, capture_path,"
                            " captured_at, generation_id, parser_version)"
                            " VALUES ('snkrdunk', %s, %s, %s, %s, %s, %s)"
                            " ON DUPLICATE KEY UPDATE"
                            " capture_sha256=VALUES(capture_sha256),"
                            " capture_path=VALUES(capture_path),"
                            " captured_at=VALUES(captured_at),"
                            " generation_id=VALUES(generation_id),"
                            " parser_version=VALUES(parser_version)",
                            (
                                iid, evidence["evidence"]["sha256"],
                                evidence["evidence"]["path"][:500],
                                captured_at, "snk_reverify", parser_version[:64],
                            ),
                        )
                        cursor.execute(
                            "UPDATE catalog_source_identity SET match_status='exact',"
                            " evidence_sha256=%s, source_product_number=%s,"
                            " bind_evidence_json=%s, bound_tcg_code=%s,"
                            " bound_card_language=%s, bound_set_code=%s,"
                            " bound_collector_number=%s, bound_printing_code=%s,"
                            " bound_parallel_code=%s, bound_edition_code=%s,"
                            " bound_finish_code=%s"
                            " WHERE source_code='snkrdunk'"
                            " AND external_entity_id=%s AND variant_id=%s"
                            # Re-checked against the stored row so a status that
                            # moved since the read above changes zero rows rather
                            # than overwriting whatever it moved to.
                            " AND match_status IN ('manual_review','rejected')"
                            f" AND {NOT_A_REJECTION_VERDICT_SQL}",
                            (
                                evidence_sha, str(claim or "")[:64],
                                json.dumps(evidence, ensure_ascii=False, sort_keys=True),
                                mirror[0][:32], mirror[1][:8], mirror[2][:24],
                                mirror[3][:96], mirror[4][:24], mirror[5][:64],
                                mirror[6][:191], mirror[7][:64], iid, variant_id,
                            ),
                        )
                conn.commit()
                counts["promoted"] = len(updates)
            except Exception:
                conn.rollback()
                raise

        report = {
            "snkIdentityReverify": True,
            "write": bool(args.write),
            "harvest": harvest_path.relative_to(ROOT).as_posix(),
            "counts": counts,
            "promotable": len(updates),
            "promotedSample": promoted[:40],
            "held": held,
        }
        artifact_path = ROOT / "data" / "runtime" / "rebuild-036" / (
            f"snk-identity-reverify-{stamp}.json"
        )
        artifact_path.write_bytes(canonical_json(report))
        print(json.dumps(report, ensure_ascii=False, indent=1, default=str))
        return 0
    finally:
        conn.close()


FREEZE_REVOKE = "INSERT,UPDATE,DELETE,CREATE,DROP,ALTER,INDEX,REFERENCES"


def _freeze_sql(user: str, password: str) -> str:
    """§0.4 writer freeze, built here so the db name is only spelled once.

    MySQL reads "_" in a grant db name as a wildcard, so the grant that exists
    for cardz is stored escaped. REVOKE has to name it exactly the same way or
    it fails with error 1141 -- and because mysql stops at the first error, the
    GRANT SELECT after it never runs and the freeze silently does not happen."""

    literal = password.replace("\\", "\\\\").replace("'", "\\'")
    return (
        f"CREATE USER IF NOT EXISTS '{user}'@'%' IDENTIFIED BY '{literal}';\n"
        f"ALTER USER '{user}'@'%' IDENTIFIED BY '{literal}';\n"
        f"GRANT ALL PRIVILEGES ON `cardz\\_market\\_cap`.* TO '{user}'@'%';\n"
        f"REVOKE {FREEZE_REVOKE} ON `cardz\\_market\\_cap`.* FROM 'cardz'@'%';\n"
        "GRANT SELECT ON `cardz\\_market\\_cap`.* TO 'cardz'@'%';\n"
        "FLUSH PRIVILEGES;\n"
    )


def cmd_freeze(args: argparse.Namespace) -> int:
    """Put the §0.4 writer freeze in force and prove it before returning.

    The rebuild password is read from rebuild.env and handed to the container's
    client on stdin: it never reaches a command line, a log, or this output."""

    credentials = Path(args.credentials_env) if args.credentials_env else DEFAULT_CREDENTIALS_ENV
    env = load_env_file(credentials)
    user = str(env.get("CARDZ_DB_USER") or "")
    password = str(env.get("CARDZ_DB_PASSWORD") or "")
    if user != "cardz_rebuild":
        raise SystemExit(f"refusing to freeze: {credentials} names user {user!r}, expected 'cardz_rebuild'")
    if not password:
        raise SystemExit(f"refusing to freeze: {credentials} has no CARDZ_DB_PASSWORD")
    result = subprocess.run(
        [
            "docker", "exec", "-i", MYSQL_CONTAINER,
            "sh", "-lc", 'exec mysql -uroot -p"$MYSQL_ROOT_PASSWORD" --batch cardz_market_cap',
        ],
        input=_freeze_sql(user, password), capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise SystemExit(f"freeze failed (exit {result.returncode}): {result.stderr.strip()[:500]}")
    _run_freeze_proof()
    # Off the server's clock, not the box's: the ledger timestamps this is
    # compared against are written by MySQL, and this machine runs UTC+9.
    stamp = subprocess.run(
        [
            "docker", "exec", MYSQL_CONTAINER, "sh", "-lc",
            'exec mysql -uroot -p"$MYSQL_ROOT_PASSWORD" --batch --skip-column-names'
            ' -e "SELECT UTC_TIMESTAMP(6)"',
        ],
        capture_output=True, text=True,
    )
    opened = (stamp.stdout or "").strip()
    if stamp.returncode != 0 or not re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{6}", opened):
        raise SystemExit(f"freeze: could not read the server clock, got {opened!r}")
    FREEZE_WINDOW.parent.mkdir(parents=True, exist_ok=True)
    FREEZE_WINDOW.write_text(json.dumps({"openedAt": opened}, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"frozen": True, "rebuildUser": user, "openedAt": opened,
                      "proof": "INSERT as cardz denied (1142)"}, ensure_ascii=False))
    return 0


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
    # mysql --batch escapes backslashes in output (\_ prints as \\_): undo it.
    normalized = stdout.replace('"', "`").replace("\\\\", "\\")
    if "GRANT ALL PRIVILEGES ON `cardz\\_market\\_cap`.* TO `cardz`@`%`" not in normalized:
        raise SystemExit(f"unfreeze verification failed: cardz grant not ALL. Output:\n{stdout}")
    if not re.search(r"rebuild_users\n0\b", stdout.replace("\r", "")):
        raise SystemExit(f"unfreeze verification failed: cardz_rebuild still exists. Output:\n{stdout}")
    # Views created while the rebuild user existed carry it as DEFINER and
    # break with error 1449 once the user is dropped. Recreate each orphan
    # from its own definition under the root definer.
    repaired: list[str] = []
    probe = subprocess.run(
        [
            "docker", "exec", "-i", MYSQL_CONTAINER,
            "sh", "-lc", 'exec mysql -uroot -p"$MYSQL_ROOT_PASSWORD" --batch -N cardz_market_cap',
        ],
        input=(
            "SELECT TABLE_NAME FROM information_schema.VIEWS"
            " WHERE TABLE_SCHEMA='cardz_market_cap' AND DEFINER LIKE 'cardz_rebuild%';\n"
        ),
        capture_output=True, text=True,
    )
    orphans = [v.strip() for v in (probe.stdout or "").splitlines() if v.strip()]
    for view in orphans:
        if not re.fullmatch(r"[A-Za-z0-9_]+", view):
            raise SystemExit(f"unfreeze: refusing suspicious view name {view!r}")
        show = subprocess.run(
            [
                "docker", "exec", "-i", MYSQL_CONTAINER,
                "sh", "-lc", 'exec mysql -uroot -p"$MYSQL_ROOT_PASSWORD" --batch -N -r cardz_market_cap',
            ],
            input=f"SHOW CREATE VIEW `{view}`;\n", capture_output=True, text=True,
        )
        line = (show.stdout or "").strip().split("\t")
        if len(line) < 2:
            raise SystemExit(f"unfreeze: SHOW CREATE VIEW failed for {view}")
        ddl = re.sub(r"DEFINER=`[^`]+`@`[^`]+`\s*", "", line[1])
        ddl = ddl.replace("CREATE ", "CREATE OR REPLACE ", 1)
        redo = subprocess.run(
            [
                "docker", "exec", "-i", MYSQL_CONTAINER,
                "sh", "-lc", 'exec mysql -uroot -p"$MYSQL_ROOT_PASSWORD" cardz_market_cap',
            ],
            input=ddl + ";\n", capture_output=True, text=True,
        )
        if redo.returncode != 0:
            raise SystemExit(
                f"unfreeze: definer repair failed for {view}: {redo.stderr.strip()[:300]}"
            )
        repaired.append(view)
    # The window is closed, so the restore point taken inside it no longer
    # describes a database nobody else may write to. Leaving the file behind
    # would let the next freeze inherit a proof it did not earn.
    FREEZE_WINDOW.unlink(missing_ok=True)
    print(json.dumps({
        "unfrozen": True, "cardzGrant": "ALL", "rebuildUserDropped": True,
        "definerRepairedViews": repaired,
    }))
    return 0


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------

_SCHEDULER_QUERY = (
    "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
    " Get-ScheduledTask | ForEach-Object { [pscustomobject]@{"
    " name=$_.TaskName; path=$_.TaskPath; state=[string]$_.State;"
    " actions=(($_.Actions | ForEach-Object {"
    " \"$($_.Execute) $($_.Arguments) $($_.WorkingDirectory)\" }) -join ' ')"
    " } } | ConvertTo-Json -Compress"
)


def _cardz_scheduled_tasks() -> list[dict[str, str]]:
    """The same whole-task match S0 preflight uses, so the two never disagree."""
    query = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", _SCHEDULER_QUERY],
        capture_output=True,
    )
    if query.returncode != 0:
        raise SystemExit("scheduled-task query failed")
    rows = json.loads((query.stdout or b"").decode("utf-8"))
    if isinstance(rows, dict):
        rows = [rows]
    matched = []
    for row in rows:
        blob = " ".join(str(row.get(key) or "") for key in ("name", "path", "actions"))
        if "cardz" in blob.lower():
            matched.append({
                "name": str(row.get("name") or ""),
                "path": str(row.get("path") or "\\"),
                "state": str(row.get("state") or "").strip().lower(),
            })
    return matched


def _scheduler_set(tasks: list[dict[str, str]], *, enable: bool) -> list[str]:
    if not tasks:
        return []
    verb = "Enable-ScheduledTask" if enable else "Disable-ScheduledTask"
    script = "; ".join(
        f"{verb} -TaskName '{task['name']}' -TaskPath '{task['path']}' | Out-Null"
        for task in tasks
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise SystemExit(f"{verb} failed: {result.stderr.strip()[:400]}")
    return [f"{task['path']}{task['name']}" for task in tasks]


def _take_backup(generation: str) -> dict[str, Any]:
    """Dump the frozen database and write the restore proof S0 reads.

    The password expands inside the container shell, the way cmd_freeze does
    it: it never reaches a command line, this output, or the proof file.

    Written to `.part` and renamed on success, so a dump that dies halfway
    does not overwrite the last one that finished. That matters here more than
    usual: this file is the only way back from the stages about to run.
    """

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    dump = BACKUP_DIR / f"cardz-{generation}-pre.sql"
    part = dump.with_suffix(".part")
    with part.open("wb") as handle:
        result = subprocess.run(
            [
                "docker", "exec", MYSQL_CONTAINER, "sh", "-lc",
                'exec mysqldump -uroot -p"$MYSQL_ROOT_PASSWORD" --single-transaction'
                " --routines --triggers cardz_market_cap",
            ],
            stdout=handle, stderr=subprocess.PIPE,
        )
    if result.returncode != 0:
        part.unlink(missing_ok=True)
        raise SystemExit(
            f"backup failed (exit {result.returncode}):"
            f" {(result.stderr or b'').decode('utf-8', 'replace').strip()[:400]}"
        )
    with part.open("rb") as handle:
        handle.seek(max(0, part.stat().st_size - 4096))
        tail = handle.read().decode("utf-8", "replace")
    marker = next(
        (line.strip() for line in reversed(tail.splitlines())
         if line.startswith("-- Dump completed on ")),
        "",
    )
    if not marker:
        part.unlink(missing_ok=True)
        raise SystemExit("backup failed: mysqldump wrote no completion marker")
    part.replace(dump)
    proof = {
        "dumpFile": dump.as_posix(),
        "dumpSha256": sha256_file(dump),
        "dumpCompletedMarker": marker,
        "generation": generation,
        # Which freeze this covers. A dump is a restore point only for the
        # window it was taken in; once the window closes, collection resumes
        # and the same bytes stop being a place it is safe to go back to.
        "freezeOpenedAt": json.loads(
            FREEZE_WINDOW.read_text(encoding="utf-8")
        )["openedAt"],
    }
    RESTORE_PROOF.parent.mkdir(parents=True, exist_ok=True)
    RESTORE_PROOF.write_text(json.dumps(proof, indent=1) + "\n", encoding="utf-8")
    return {"dumpFile": proof["dumpFile"], "bytes": dump.stat().st_size,
            "completed": marker[len("-- Dump completed on "):].strip()}


def cmd_e2e(args: argparse.Namespace) -> int:
    """One command from code change to baked snapshot.

    Every step here was run by hand on 2026-08-10 and every one of them was a
    step that only worked while somebody remembered it: disable the schedulers
    before S0 would pass, freeze, run the stages, copy the receipt sha out of
    validate's stdout into activate's command line, remember that prune-apply
    and canary are --stage only, unfreeze, re-enable exactly the schedulers
    that had been on, re-bake. Forgetting the unfreeze leaves the nightly
    lanes writing into a database whose writer grant is revoked, which is why
    teardown runs from finally and not from the happy path.
    """
    steps: list[dict[str, Any]] = []

    def record(step: str, **fields: Any) -> None:
        entry = {"step": step, **fields}
        steps.append(entry)
        print(json.dumps({"phase": "e2e", **entry}, ensure_ascii=False, default=str), flush=True)

    stage_args = SimpleNamespace(
        generation=args.generation, resume=True, stage=None, invalidate_from=None,
        force_stage=False, dry_run=False, credentials_env=args.credentials_env,
        freshness_hours=args.freshness_hours,
    )

    tasks = _cardz_scheduled_tasks()
    to_restore = [task for task in tasks if task["state"] != "disabled"]
    disabled = _scheduler_set(to_restore, enable=False)
    record("scheduler-disable", tasks=disabled, alreadyDisabled=len(tasks) - len(to_restore))
    try:
        cmd_freeze(SimpleNamespace(credentials_env=args.credentials_env))
        record("freeze")

        # After the freeze, not before it. Invalidating goes through
        # cmd_rebuild, which connects as cardz_rebuild -- and cardz_rebuild
        # only exists inside the freeze window, so this step could only ever
        # end in 1045 Access denied (measured 2026-08-10T17:07Z, the first run
        # that passed --invalidate-from at all).
        if args.invalidate_from:
            cmd_rebuild(SimpleNamespace(**{**vars(stage_args), "invalidate_from": args.invalidate_from}))
            record("invalidate", fromStage=args.invalidate_from)

        # After the freeze, before the stages: the freeze is what makes the
        # dump a restore point that stays one, and S0 is the next thing to
        # run. Taking it by hand was the step nobody remembered -- the gate
        # spent 55 hours passing on a 2026-08-08 dump while the chain wrote.
        record("backup", **_take_backup(args.generation))

        code = cmd_rebuild(stage_args)
        if code != 0:
            raise SystemExit(f"linear stages stopped with exit {code}")
        record("linear", through=LINEAR_STAGES[-1][0])

        cmd_activate(SimpleNamespace(
            generation=args.generation, receipt_sha256=None,
            credentials_env=args.credentials_env,
        ))
        record("activate", receipt="read from cardz_rebuild_validation_receipt")

        for stage in (name for name, _, _, _ in POST_ACTIVATION_STAGES):
            cmd_rebuild(SimpleNamespace(**{
                **vars(stage_args), "stage": stage, "force_stage": True,
            }))
            record("post-activation", stage=stage)
    finally:
        cmd_unfreeze(SimpleNamespace(confirm=True))
        record("unfreeze")
        restored = _scheduler_set(to_restore, enable=True)
        record("scheduler-restore", tasks=restored)

    if not args.skip_bake:
        bake = subprocess.run(
            ["node", str(ROOT / "scripts" / "bake-public-snapshot.mjs")],
            cwd=str(ROOT), capture_output=True, text=True, shell=True,
        )
        if bake.returncode != 0:
            raise SystemExit(f"bake failed (exit {bake.returncode}): {bake.stderr.strip()[:600]}")
        record("bake", tail=(bake.stdout or "").strip().splitlines()[-1:])

    print(json.dumps({"phase": "e2e-complete", "generation": args.generation,
                      "steps": [entry["step"] for entry in steps]}, ensure_ascii=False))
    return 0
