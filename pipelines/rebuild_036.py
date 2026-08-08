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
from typing import Any, Callable, Mapping

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
    except OSError:
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
    total = None
    if isinstance(grades, dict):
        candidate_total = sum(
            value for value in grades.values() if isinstance(value, int) and value >= 0
        )
        total = candidate_total if len(grades) > 1 else None
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
    import gemrate_source

    worklist_path = gemrate_source.OUT_DIR / "runs" / ctx.generation / "worklist.txt"
    if not worklist_path.is_file():
        return "worklist-missing"
    return sha256_file(worklist_path)


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
    lowered = set_name.lower()
    if "japanese" in lowered:
        language = "ja"
    elif "chinese" in lowered:
        language = "zh"
    elif "korean" in lowered:
        language = "ko"
    else:
        language = "en"
    return {
        "gemrateId": gid,
        "description": str(raw.get("description") or ""),
        "name": str(raw.get("name") or ""),
        "year": str(raw.get("year") or ""),
        "setName": set_name,
        "cardNumber": str(raw.get("card_number") or ""),
        "parallel": str(raw.get("parallel") or ""),
        "category": str(raw.get("category") or ""),
        "derivedLanguage": language,
        "canonicalUrl": canonical_url,
        "settledId": settled_id,
        "slug": slug,
        "psaRowCount": len(psa_rows),
        "rawSha256": str(receipt.get("contentSha256") or ""),
    }, ""


def _norm_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def _fingerprint_variant_conflicts(
    fp: Mapping[str, Any], variant: Mapping[str, Any],
) -> list[str]:
    """Hard identity conflicts between a discovery fingerprint and a variant.

    Only signals both vocabularies can express are compared; parallel/printing
    wording differences are soft evidence and stay in detail_json instead.
    """

    conflicts: list[str] = []
    v_number = _norm_text(variant.get("collector_number") or "")
    f_number = _norm_text(fp.get("cardNumber") or "")
    if v_number and f_number and v_number != f_number:
        conflicts.append(f"collector_number:{f_number}!={v_number}")
    v_lang = str(variant.get("card_language") or "")
    f_lang = str(fp.get("derivedLanguage") or "")
    if v_lang and f_lang:
        same = v_lang == f_lang or (v_lang.startswith("zh") and f_lang.startswith("zh"))
        if not same:
            conflicts.append(f"language:{f_lang}!={v_lang}")
    v_set = _norm_text(variant.get("set_name") or "")
    f_set = _norm_text(fp.get("setName") or "")
    if v_set and f_set:
        # GemRate prefixes franchise ("Pokemon Sword and Shield Crown Zenith");
        # catalog names can be shorter ("Crown Zenith"). Containment either way
        # is agreement; disjoint names are a conflict.
        if v_set not in f_set and f_set not in v_set:
            conflicts.append(f"set:{f_set!r}!={v_set!r}")
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
            if conflicts:
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
                    " variant_id=VALUES(variant_id)",
                    (
                        ctx.generation, incident["gemrate_id"], incident["variant_id"],
                        incident["incident_kind"],
                        json.dumps(incident["detail"], ensure_ascii=False, sort_keys=True),
                        now,
                    ),
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
    ("pop-land", stage_popland, _popland_input_sha, False),
    ("identity-resolve", stage_identity_resolve, _identity_input_sha, False),
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
