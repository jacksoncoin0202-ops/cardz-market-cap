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


def _collector_core(value: str) -> str:
    """Comparable core of a collector number across vocabularies.

    GemRate stores the bare printed number ("016", "236"); the catalog stores
    display forms with a set prefix and/or denominator ("OP01-016", "236/187").
    Core = last dash segment, denominator dropped, numeric leading zeros folded.
    """

    text = _norm_text(value).replace(" ", "")
    if not text:
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
}


def _set_signals(set_name: str, collector: str = "") -> tuple[set[str], set[str]]:
    """(set codes, meaningful name tokens) a side exposes for set comparison."""

    text = _norm_text(str(set_name or "").replace("&", " and "))
    codes = set(_SET_CODE_RE.findall(text)) | set(
        _SET_CODE_RE.findall(_norm_text(collector))
    )
    # GemRate's "<franchise> <CODE> <LANG>-<Name>" prefix: code is already
    # harvested above; strip it so the code token never pollutes name tokens.
    text = re.sub(r"\b([a-z0-9]{2,6})\s+(en|jp|ja)-", " ", text)
    for phrase in _ERA_PHRASES:
        text = text.replace(phrase, " ")
    tokens = {
        "promo" if token == "p" else token
        for token in re.split(r"[^a-z0-9]+", text)
        if token and token not in _NOISE_TOKENS and token not in codes
    }
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
    f_lang = str(fp.get("derivedLanguage") or "")
    if v_lang and f_lang:
        same = v_lang == f_lang or (v_lang.startswith("zh") and f_lang.startswith("zh"))
        if not same:
            conflicts.append(f"language:{f_lang}!={v_lang}")
    f_codes, f_tokens = _set_signals(fp.get("setName") or "", fp.get("cardNumber") or "")
    v_codes, v_tokens = _set_signals(
        variant.get("set_name") or "", variant.get("collector_number") or ""
    )
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


def _parallel_agrees(fp_parallel: str, variant_parallel: str) -> bool:
    fpp = _norm_text(fp_parallel)
    vpp = _norm_text(variant_parallel)
    if fpp == vpp:
        return True
    # A variant with no printing evidence + a Base fingerprint is the default
    # print of the same card; alt-art wordings never collapse into "".
    return {fpp, vpp} == {"", "base"} or (vpp == "" and fpp == "base")


def _gemrate_bind_evidence(fp: Mapping[str, Any], generation: str) -> tuple[dict[str, Any], str]:
    """Contract shape for operator_strict_source_identity (037): providerClaims
    carry what the provider itself said; evidence ties the bind to a
    raw-verified v2 observation via rawPayloadSha256."""

    codes, _ = _set_signals(fp.get("setName") or "", fp.get("cardNumber") or "")
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
            " v.set_name, v.collector_number, v.identity_status,"
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

    printing_sha_owner: dict[str, int] = {}
    adoption_index: dict[tuple[str, str], list[int]] = {}
    for vid, variant in variants.items():
        sha = variant.get("canonical_printing_sha256")
        if sha:
            printing_sha_owner[sha] = vid
        key = (
            str(variant.get("tcg_code") or "").casefold(),
            _norm_text(variant.get("collector_number") or ""),
        )
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
    counts = {
        "variantsCreated": 0, "variantsAdopted": 0, "bindingsUpgradedExact": 0,
        "bindingsRebound": 0, "bindingsRejected": 0, "aliasesClosed": 0,
        "incidentsClosed": 0, "incidentsStillOpen": 0, "newIncidents": 0,
        "ownershipUnresolved": 0,
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
                    binding_updates[gid] = {"action": "reject"}
            # settled entity missing from members: incident stays open.

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
            pending_extra.setdefault(gid, []).append(f"mint:{reason}")
            return
        sha = _gemrate_printing_sha(fields)
        target = printing_sha_owner.get(sha)
        if target is None:
            key = (fields["tcg_code"], _norm_text(fields["collector_number"]))
            candidates = []
            for vid in adoption_index.get(key, []):
                variant = variants[vid]
                if _fingerprint_variant_conflicts(fp, variant):
                    continue
                if not _parallel_agrees(fp["parallel"], variant.get("parallel_code") or ""):
                    continue
                candidates.append(vid)
            if len(candidates) > 1:
                pending_extra.setdefault(gid, []).append("adoption_ambiguous")
                return
            if candidates:
                target = candidates[0]
        if target is not None:
            owner = variant_exact_owner.get(target)
            if owner and owner != gid:
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
                if _norm_text(fingerprints[gid]["parallel"])
                == _norm_text(variant.get("parallel_code") or "")
            ]
            if len(para) == 1:
                owner = para[0]
        if owner is None and len(gids) == 1 and not zero:
            # Sole binding whose fingerprint moved away: evict and re-home it.
            owner = ""
        if owner is None:
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
                        "UPDATE catalog_source_identity SET match_status='rejected'"
                        " WHERE source_code='gemrate' AND external_entity_id=%s"
                        " AND match_status<>'rejected'",
                        (gid,),
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
    ("bind", stage_bind, _bind_input_sha, False),
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
