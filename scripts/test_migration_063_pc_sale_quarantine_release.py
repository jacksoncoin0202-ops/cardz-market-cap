#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""063：證實係真卡嘅 PC 成交，逐 id 連證據由 sticky 隔離表放返出嚟，唔使連 DB。

價格審計 2026-09-26：market_pc_sale_title_quarantine（058）upsert-only、第一個
reason 贏，判別器一錯殺就永遠剔走。155 條 title_collector_contradiction 入面有
14 條係真卡（blind Opus 2 票 match + Jev p(match) >= 0.86），但冇一條結構性收窄
規則可以淨係放返呢 14 條（同形狀嘅 2401467／2474447／2533071／1621171 Jev 判
wrong_card_or_parallel），所以放返係逐 id、帶證據，唔係靠 pattern。

呢個檔守五樣嘢：

  1. DDL 契約：release 表（INSERT-only；CHECK 鎖死 reason／status／evidence 格式）、
     effective view（表減去 status='released' 而且 reason 對得上嘅放返）、seed
     （INSERT IGNORE，只放返仲係 title_collector_contradiction 嘅行）、sales view
     （同 062 逐粒字一樣，淨係 NOT EXISTS 改讀 effective view）、schema '063'。
  2. 證據：seed 每個 id 嘅 evidence_sha256 = 證據檔嗰行（bytes，冇換行）嘅 sha256，
     而且嗰行 Jev match p>=0.8、blind panel match 2 票。證據檔喺 gitignored 嘅
     data/private；release clone 冇呢個檔就淨係驗格式（印 info）。
  3. 語義 fixture：真係喺 sqlite 行 063 嘅表 DDL（連 CHECK）、effective view、
     seed 同 sales view body：放返嘅成交計返、冇放返／reason 唔啱／revoked 嘅照剔；
     062 body（今日 live）要紅。
  4. 讀者一個 predicate：load_pc_sales、FE loadDbExcludedSaleIds、receipt builder
     都讀 effective view，063 未 apply（1146）先退返原表；其他 error 照拋。
  5. 最新定義 sales view 嘅 migration 一定讀 effective view（之後有人重寫 view
     唔帶佢 = 紅）。

停泊（.mysql.sql.pending）同落地二揀一；statement 切法用 db_runtime.split_sql。
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from daily_chain_v2_contract import list_v2_migrations  # noqa: E402
from db_runtime import split_sql  # noqa: E402

MIGRATIONS_DIR = ROOT / "pipelines" / "migrations"
MIGRATION_NAME = "063_daily_chain_v2_pc_sale_quarantine_release.mysql.sql"
PARKED_NAME = f"{MIGRATION_NAME}.pending"
BASELINE_PATH = MIGRATIONS_DIR / "062_daily_chain_v2_sales_rows_strict_identity.mysql.sql"
FE_BAKE_PATH = ROOT / "apps" / "web" / "src" / "lib" / "live-db-snapshot.ts"
EVIDENCE_DIR = ROOT / "data" / "private" / "handoff_20260926" / "jev"
EVIDENCE_PATH = "data/private/handoff_20260926/jev/candidates_released.jsonl"
SALES_VIEW = "operator_eligible_accepted_psa10_sales_rows"
EFFECTIVE = "market_pc_sale_title_quarantine_effective"
RELEASE = "market_pc_sale_quarantine_release"
RAW_READ = "SELECT 1 FROM market_pc_sale_title_quarantine tq WHERE"
EFFECTIVE_READ = f"SELECT 1 FROM {EFFECTIVE} tq WHERE"
EFFECTIVE_VIEW = (
    f"CREATE OR REPLACE VIEW {EFFECTIVE} AS"
    " SELECT tq.sale_observation_id, tq.variant_id, tq.reason, tq.receipt_sha256, tq.written_at"
    " FROM market_pc_sale_title_quarantine tq WHERE NOT EXISTS ("
    f" SELECT 1 FROM {RELEASE} r"
    " WHERE r.sale_observation_id=tq.sale_observation_id"
    " AND r.released_reason=tq.reason AND r.status='released' )"
)
TABLE_MUSTS = (
    f"CREATE TABLE IF NOT EXISTS {RELEASE} (",
    "PRIMARY KEY (sale_observation_id)",
    "CHECK (released_reason IN ('title_collector_contradiction'))",
    "CHECK (status IN ('released','revoked'))",
    "CHECK (evidence_sha256 REGEXP '^[0-9a-f]{64}$')",
    "status VARCHAR(16) NOT NULL DEFAULT 'released'",
)
SEED_MUSTS = (
    f"INSERT IGNORE INTO {RELEASE}",
    "FROM market_pc_sale_title_quarantine tq INNER JOIN (",
    ") ev ON ev.sale_observation_id=tq.sale_observation_id"
    " WHERE tq.reason='title_collector_contradiction'",
    f"'{EVIDENCE_PATH}'",
)
PAIR_RE = re.compile(r"SELECT (\d+)(?: AS sale_observation_id)?, '([^']*)'")

FAILED: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    if ok:
        print(f"ok   {label}")
    else:
        FAILED.append(label)
        print(f"FAIL {label}{': ' + detail if detail else ''}")


def _compact(text: str) -> str:
    return " ".join(text.split())


def _statement(sql_text: str, head: str) -> str:
    return next((s for s in split_sql(sql_text) if _compact(s).startswith(head)), "")


def seed_pairs(sql_text: str) -> list[tuple[int, str]]:
    seed = _statement(sql_text, f"INSERT IGNORE INTO {RELEASE}")
    return [(int(i), sha) for i, sha in PAIR_RE.findall(seed)]


# ---------------------------------------------------------------- 1. DDL 契約


def audit_063(sql_text: str, baseline_view: str) -> list[str]:
    problems: list[str] = []
    statements = [_compact(s) for s in split_sql(sql_text)]
    heads = [
        f"CREATE TABLE IF NOT EXISTS {RELEASE}",
        f"CREATE OR REPLACE VIEW {EFFECTIVE} AS",
        f"INSERT IGNORE INTO {RELEASE}",
        f"CREATE OR REPLACE VIEW {SALES_VIEW} AS",
        "INSERT IGNORE INTO cardz_schema_version",
    ]
    if len(statements) != len(heads) or any(not s.startswith(h) for s, h in zip(statements, heads)):
        problems.append(f"statements are not table, effective view, seed, sales view, version: "
                        f"{[s[:50] for s in statements]}")
        return problems
    table, effective, seed, sales, version = statements
    for statement in statements:
        upper = statement.upper()
        if any(word in upper for word in ("DELETE ", "DROP ", "TRUNCATE ", "UPDATE ", "ALTER ")):
            problems.append(f"destructive statement: {statement[:80]}")
    for must in TABLE_MUSTS:
        if must not in table:
            problems.append(f"release table lacks {must}")
    if effective != EFFECTIVE_VIEW:
        problems.append("the effective view is not exactly 'table minus live same-reason releases'")
    for must in SEED_MUSTS:
        if must not in seed:
            problems.append(f"seed lacks {must[:70]}")
    expected_sales = baseline_view.replace(RAW_READ, EFFECTIVE_READ, 1)
    if expected_sales == baseline_view:
        problems.append("the 062 baseline no longer reads the raw quarantine table")
    elif sales != expected_sales:
        problems.append("the sales view is not the 062 body with the effective read")
    if "INSERT IGNORE" not in version or "VALUES ('063')" not in version:
        problems.append(f"schema version row is not an INSERT IGNORE of '063': {version[:80]}")
    return problems


# ---------------------------------------------------------------- 2. 證據


def evidence_problems(pairs: list[tuple[int, str]]) -> list[str] | None:
    """None = private evidence not in this checkout (release clone)."""

    lines_path = EVIDENCE_DIR / "candidates_released.jsonl"
    verdicts_path = EVIDENCE_DIR / "final_verdicts.json"
    if not (lines_path.is_file() and verdicts_path.is_file()):
        return None
    lines = {}
    for raw in lines_path.read_bytes().split(b"\n"):
        if raw.strip():
            record = json.loads(raw)
            lines[int(str(record["id"]).split(":")[1])] = (hashlib.sha256(raw).hexdigest(), record)
    panel = {}
    for batch in json.loads(verdicts_path.read_text(encoding="utf-8"))["batches"]:
        for verdict in batch["verdicts"]:
            panel[str(verdict["id"])] = verdict
    problems = []
    for sale_id, sha in pairs:
        if sale_id not in lines:
            problems.append(f"{sale_id}: not in the evidence file")
            continue
        line_sha, record = lines[sale_id]
        if sha != line_sha:
            problems.append(f"{sale_id}: evidence sha is not its line's sha256")
        if record.get("jevChoice") != "match" or float(record.get("pMatch") or 0) < 0.8:
            problems.append(f"{sale_id}: Jev did not judge it a match with p>=0.8")
        if record.get("receipt") != ["title_collector_contradiction"]:
            problems.append(f"{sale_id}: not a title_collector_contradiction kill")
        verdict = panel.get(f"pc:{sale_id}", {})
        if verdict.get("class") != "match" or int(verdict.get("votes") or 0) < 2:
            problems.append(f"{sale_id}: the blind panel did not vote match twice")
    return problems


# ---------------------------------------------------------------- 3. 語義 fixture

HEX = "a" * 64
# (sale id, day, value, quarantine reason or None, release status or None)
# The first three ids are real seeded ids so the migration's own seed runs.


def _sqlite_ddl(statement: str) -> str:
    text = statement.replace("CREATE OR REPLACE VIEW", "CREATE VIEW")
    text = text.replace("INSERT IGNORE INTO", "INSERT OR IGNORE INTO")
    if text.startswith(f"CREATE TABLE IF NOT EXISTS {RELEASE}"):
        text = text[: text.rindex(")") + 1]
    return text


def fixture(sql_text: str, sales_body: str) -> tuple[sqlite3.Connection, list[int]]:
    pairs = seed_pairs(sql_text)
    seeded, other_seeded, wrong_reason = pairs[0][0], pairs[1][0], pairs[2][0]
    db = sqlite3.connect(":memory:")
    db.create_function("REGEXP", 2, lambda p, v: v is not None and re.search(p, str(v)) is not None)
    db.create_function("UTC_TIMESTAMP", 1, lambda _n: "2026-09-26 00:00:00.000000")
    db.executescript(
        """
        CREATE TABLE market_metric_history_acceptance (
          id INTEGER, source_record_type TEXT, source_record_id INTEGER, variant_id INTEGER,
          observed_date TEXT, source_effective_at TEXT, external_entity_id TEXT,
          source_payload_sha256 TEXT, metric_kind TEXT, source_code TEXT,
          identity_evidence_sha256 TEXT, acceptance_evidence_sha256 TEXT, lineage_sha256 TEXT);
        CREATE TABLE market_sale_observation (
          id INTEGER, variant_id INTEGER, sold_at TEXT, quantity INTEGER, transaction_value_usd REAL,
          coverage_status TEXT, source_code TEXT, fetched_at TEXT, external_entity_id TEXT,
          source_payload_sha256 TEXT, grader_code TEXT, grade_label TEXT, timestamp_quality TEXT);
        CREATE TABLE catalog_source_identity (
          variant_id INTEGER, source_code TEXT, external_entity_id TEXT, match_status TEXT, evidence_sha256 TEXT);
        CREATE TABLE operator_strict_source_identity (
          variant_id INTEGER, source_code TEXT, external_entity_id TEXT);
        CREATE TABLE market_pc_sale_title_quarantine (
          sale_observation_id INTEGER PRIMARY KEY, variant_id INTEGER, reason TEXT,
          receipt_sha256 TEXT, written_at TEXT);
        """
    )
    db.execute("INSERT INTO catalog_source_identity VALUES (1,'pricecharting','pc-1','exact',?)", (HEX,))
    db.execute("INSERT INTO operator_strict_source_identity VALUES (1,'pricecharting','pc-1')")
    # sale id -> (value, quarantine reason)
    sales = {
        1: (100.0, None),                                           # clean
        seeded: (200.0, "title_collector_contradiction"),           # seeded + reason matches -> back
        other_seeded: (400.0, "title_collector_contradiction"),     # seeded, release revoked -> out
        wrong_reason: (800.0, "price_isolated_spike"),              # seeded id, other reason -> out
        5: (1600.0, "title_collector_contradiction"),               # not seeded -> out
    }
    for sale_id, (value, reason) in sales.items():
        payload = f"{sale_id:064x}"
        db.execute(
            "INSERT INTO market_sale_observation VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (sale_id, 1, "2026-09-01 00:00:00", 1, value, "complete", "pricecharting",
             "2026-09-01 00:00:00", "pc-1", payload, "PSA", "10", "date"),
        )
        db.execute(
            "INSERT INTO market_metric_history_acceptance VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (1000 + sale_id, "market_sale_observation", sale_id, 1, "2026-09-01",
             "2026-09-01 00:00:00", "pc-1", payload, "psa10_sale", "pricecharting", HEX, HEX, HEX),
        )
        if reason:
            db.execute("INSERT INTO market_pc_sale_title_quarantine VALUES (?,?,?,?,?)",
                       (sale_id, 1, reason, HEX, "2026-09-25"))
    statements = split_sql(sql_text)
    table, effective, seed = statements[0], statements[1], statements[2]
    db.execute(_sqlite_ddl(table))
    db.execute(_sqlite_ddl(effective))
    db.execute(_sqlite_ddl(seed))
    db.execute(_sqlite_ddl(seed))  # idempotent
    # A human revokes one release: the only change a release row may get.
    db.execute(f"UPDATE {RELEASE} SET status='revoked' WHERE sale_observation_id=?", (other_seeded,))
    # A release row for a reason the table no longer says can never free the sale.
    db.execute(f"INSERT OR IGNORE INTO {RELEASE} VALUES (?,?,?,?,?,?,?)",
               (wrong_reason, "title_collector_contradiction", HEX, "p", "r", "released", "x"))
    db.execute(f"CREATE VIEW {SALES_VIEW} AS {sales_body}")
    return db, [seeded, other_seeded, wrong_reason]


def evaluate(sql_text: str, sales_body: str) -> tuple[float | None, list[int], list[str]]:
    """(day SUM through the sales view, released ids, CHECK rejections)."""

    try:
        db, _ = fixture(sql_text, sales_body)
    except sqlite3.Error as error:
        return None, [], [f"fixture does not run: {error}"]
    try:
        total = db.execute(f"SELECT SUM(transaction_value_usd) FROM {SALES_VIEW}").fetchone()[0]
        released = sorted(r[0] for r in db.execute(
            f"SELECT sale_observation_id FROM market_pc_sale_title_quarantine"
            f" WHERE sale_observation_id NOT IN (SELECT sale_observation_id FROM {EFFECTIVE})"))
        rejected = []
        for label, row in {
            "reason": (90, "price_isolated_spike", HEX, "released"),
            "status": (91, "title_collector_contradiction", HEX, "gone"),
            "evidence": (92, "title_collector_contradiction", "XYZ", "released"),
        }.items():
            try:
                db.execute(f"INSERT INTO {RELEASE} VALUES (?,?,?,'p','r',?,'x')", row)
            except sqlite3.IntegrityError:
                rejected.append(label)
        return float(total or 0), released, rejected
    finally:
        db.close()


# ---------------------------------------------------------------- 4. 讀者


class LoadCursor:
    def __init__(self, first_error):
        self.first_error = first_error
        self.sent: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, _params=None):
        self.sent.append(_compact(sql))
        if EFFECTIVE in sql and self.first_error is not None:
            raise self.first_error

    def fetchall(self):
        return [{"transaction_fingerprint": "fp"}]


class LoadConnection:
    def __init__(self, first_error=None):
        self.cursor_obj = LoadCursor(first_error)

    def cursor(self):
        return self.cursor_obj


def reader_problems(fe_text: str) -> list[str]:
    from datetime import datetime, timezone

    import pc_psa10_price_derivation as D
    import pc_sale_title_quarantine as Q

    problems: list[str] = []
    as_of = datetime(2026, 9, 26, tzinfo=timezone.utc)
    live = LoadConnection()
    D.load_pc_sales(live, [1], as_of=as_of)
    if len(live.cursor_obj.sent) != 1 or f"FROM {EFFECTIVE} tq" not in live.cursor_obj.sent[0]:
        problems.append("load_pc_sales does not read the effective view first")
    early = LoadConnection(RuntimeError(1146, f"Table '{EFFECTIVE}' doesn't exist"))
    rows = D.load_pc_sales(early, [1], as_of=as_of)
    if (len(early.cursor_obj.sent) != 2 or "FROM market_pc_sale_title_quarantine tq" not in early.cursor_obj.sent[1]
            or not rows):
        problems.append("load_pc_sales does not fall back to the raw table when 063 is not applied")
    try:
        D.load_pc_sales(LoadConnection(RuntimeError(1356, "view invalid")), [1], as_of=as_of)
        problems.append("load_pc_sales swallowed a non-1146 error")
    except RuntimeError:
        pass
    if f"LEFT JOIN {EFFECTIVE} e" not in _compact(Q.STORED_RELEASE_SQL):
        problems.append("the receipt builder does not ask the effective view what is released")
    body = fe_text.split("async function loadDbExcludedSaleIds", 1)[-1].split("\n}\n", 1)[0]
    effective_at = body.find(f"FROM {EFFECTIVE}\"")
    raw_at = body.find("FROM market_pc_sale_title_quarantine\"")
    if effective_at < 0 or raw_at < 0 or effective_at > raw_at:
        problems.append("FE loadDbExcludedSaleIds does not read the effective view before the raw table")
    if 'code !== "ER_NO_SUCH_TABLE") throw error;' not in body:
        problems.append("FE loadDbExcludedSaleIds does not rethrow errors other than ER_NO_SUCH_TABLE")
    return problems


RAW_FROM = re.compile(r"FROM\s+market_pc_sale_title_quarantine\b")
# Raw reads that are allowed: the receipt builder reads the whole table (and
# joins the effective view for `released`), the FE falls back to it pre-063.
RAW_FROM_ALLOWED = {"pipelines/pc_sale_title_quarantine.py": 2, "apps/web/src/lib/live-db-snapshot.ts": 1}


def raw_reader_counts() -> dict[str, int]:
    counts: dict[str, int] = {}
    for path in [*sorted((ROOT / "pipelines").glob("*.py")), FE_BAKE_PATH]:
        found = len(RAW_FROM.findall(path.read_text(encoding="utf-8")))
        if found:
            counts[path.relative_to(ROOT).as_posix()] = found
    return counts


# ---------------------------------------------------------------- main


def main() -> int:
    parked = (MIGRATIONS_DIR / PARKED_NAME).is_file()
    landed = (MIGRATIONS_DIR / MIGRATION_NAME).is_file()
    check("063 停泊同落地只可以二揀一", parked != landed, f"parked={parked} landed={landed}")
    if parked:
        check("停泊中：chain 嘅 migrate stage 揀唔到", MIGRATION_NAME not in list_v2_migrations(ROOT))
        check("停泊中：db_runtime.migrate 嘅 *.mysql.sql 揀唔到",
              MIGRATION_NAME not in {p.name for p in MIGRATIONS_DIR.glob("*.mysql.sql")})
    if landed:
        check("落地：chain 嘅 migrate stage 揀到", MIGRATION_NAME in list_v2_migrations(ROOT))
    path = MIGRATIONS_DIR / (PARKED_NAME if parked else MIGRATION_NAME)
    if not path.is_file():
        print(f"FAILED {len(FAILED)}: {FAILED}")
        return 1
    raw = path.read_bytes()
    check("063 係 LF（冇 CR）", b"\r" not in raw)
    sql_text = raw.decode("utf-8")
    baseline_view = _compact(_statement(BASELINE_PATH.read_text(encoding="utf-8"),
                                        f"CREATE OR REPLACE VIEW {SALES_VIEW} AS"))
    check("062 baseline view 搵到，而且讀緊原表", RAW_READ in baseline_view)

    problems = audit_063(sql_text, baseline_view)
    check("063 DDL 契約成立", not problems, "; ".join(problems[:4]))
    ddl_poisons = {
        "CHECK reason 冇咗": sql_text.replace(
            "    CONSTRAINT ck_pc_sale_quarantine_release_reason\n"
            "      CHECK (released_reason IN ('title_collector_contradiction')),\n", "", 1),
        "effective view 唔對 reason": sql_text.replace("    AND r.released_reason=tq.reason\n", "", 1),
        "effective view 唔睇 status": sql_text.replace("    AND r.status='released'\n", "", 1),
        "sales view 讀返原表": sql_text.replace(
            f"SELECT 1 FROM {EFFECTIVE} tq\n", "SELECT 1 FROM market_pc_sale_title_quarantine tq\n", 1),
        "seed 唔守 reason": sql_text.replace("\nWHERE tq.reason='title_collector_contradiction';", ";", 1),
        "seed 唔用 IGNORE": sql_text.replace(f"INSERT IGNORE INTO {RELEASE}", f"INSERT INTO {RELEASE}", 1),
        "062 body 飄咗": sql_text.replace("  AND s.quantity>0\n", "  AND s.quantity>=0\n", 1),
        "schema version 寫錯": sql_text.replace("VALUES ('063')", "VALUES ('062')", 1),
        "加咗 DELETE": sql_text.replace(
            "\nINSERT IGNORE INTO cardz_schema_version",
            f"\nDELETE FROM {RELEASE} WHERE status='revoked';\n\nINSERT IGNORE INTO cardz_schema_version", 1),
    }
    for label, poisoned in ddl_poisons.items():
        check(f"DDL poison「{label}」會紅", poisoned != sql_text and bool(audit_063(poisoned, baseline_view)))

    print()
    pairs = seed_pairs(sql_text)
    ids = [sale_id for sale_id, _ in pairs]
    check("seed 放返 14 個唔同 id，每個都有 64-hex 證據",
          len(pairs) == 14 and len(set(ids)) == 14
          and all(re.fullmatch(r"[0-9a-f]{64}", sha) for _, sha in pairs), str(pairs[:3]))
    evidence = evidence_problems(pairs)
    if evidence is None:
        print("info 證據檔唔喺呢個 checkout（data/private 係 gitignored）：淨係驗咗格式")
    else:
        check("每個放返嘅 id：證據行 sha 啱、Jev match p>=0.8、blind panel match 2 票",
              not evidence, "; ".join(evidence[:4]))
        tampered = [(pairs[0][0], "b" * 64), *pairs[1:]]
        check("plant：改一個 evidence sha 會紅", bool(evidence_problems(tampered)))
        check("plant：塞一個冇證據嘅 id（2401467，Jev wrong_card）會紅",
              bool(evidence_problems([*pairs, (2401467, "c" * 64)])))

    print()
    statements = split_sql(sql_text)
    sales_body = re.search(rf"CREATE OR REPLACE VIEW {SALES_VIEW} AS\s*(SELECT\b.*)", statements[3], re.S).group(1)
    baseline_body = re.search(
        rf"CREATE OR REPLACE VIEW {SALES_VIEW} AS\s*(SELECT\b.*)",
        _statement(BASELINE_PATH.read_text(encoding="utf-8"), f"CREATE OR REPLACE VIEW {SALES_VIEW} AS"),
        re.S).group(1)
    total, released, rejected = evaluate(sql_text, sales_body)
    seeded, revoked, wrong_reason = pairs[0][0], pairs[1][0], pairs[2][0]
    check("fixture：淨係 seed 咗、reason 對、未 revoke 嗰單放返",
          released == [seeded], f"released={released} (want [{seeded}]; revoked {revoked}, wrong reason {wrong_reason})")
    check("fixture：sales view 計返放返嗰單（100 + 200），其他隔離照剔", total == 300.0, str(total))
    check("fixture：表 CHECK 擋住其他 reason／status／非 hex 證據",
          rejected == ["reason", "status", "evidence"], str(rejected))
    base_total, _, _ = evaluate(sql_text, baseline_body)
    check("plant：062 body（今日 live，讀原表）會紅：放返嗰單冇計", base_total == 100.0, str(base_total))
    semantic_poisons = {
        "effective view 唔對 reason": sql_text.replace("    AND r.released_reason=tq.reason\n", "", 1),
        "effective view 唔睇 status": sql_text.replace("    AND r.status='released'\n", "", 1),
        # Either guard alone holds (the CHECK refuses a price-reason release the
        # unguarded seed would write), so the plant removes both.
        "seed 唔守 reason 兼冇 CHECK reason": sql_text.replace(
            "\nWHERE tq.reason='title_collector_contradiction';", ";", 1).replace(
            "    CONSTRAINT ck_pc_sale_quarantine_release_reason\n"
            "      CHECK (released_reason IN ('title_collector_contradiction')),\n", "", 1),
    }
    for label, poisoned in semantic_poisons.items():
        got = evaluate(poisoned, sales_body)
        check(f"fixture plant「{label}」會紅",
              poisoned != sql_text and got[0] is not None and got[:2] != (300.0, [seeded]), str(got))

    print()
    fe = FE_BAKE_PATH.read_text(encoding="utf-8")
    problems = reader_problems(fe)
    check("讀者全部經 effective view（063 未 apply 先退原表）", not problems, "; ".join(problems))
    swapped = fe.replace(
        f'    "SELECT sale_observation_id FROM {EFFECTIVE}",\n'
        '    "SELECT sale_observation_id FROM market_pc_sale_title_quarantine",\n',
        '    "SELECT sale_observation_id FROM market_pc_sale_title_quarantine",\n'
        f'    "SELECT sale_observation_id FROM {EFFECTIVE}",\n', 1)
    check("plant：FE 先讀原表會紅", swapped != fe and bool(reader_problems(swapped)))
    counts = raw_reader_counts()
    check("冇新嘅原表讀者（其他讀者一律經 effective view）", counts == RAW_FROM_ALLOWED, str(counts))

    defining = [
        p for p in sorted(MIGRATIONS_DIR.glob("*.mysql.sql"))
        if f"CREATE OR REPLACE VIEW {SALES_VIEW}" in p.read_text(encoding="utf-8")
    ]
    if landed:
        newest = _compact(_statement(defining[-1].read_text(encoding="utf-8"),
                                     f"CREATE OR REPLACE VIEW {SALES_VIEW} AS"))
        check("最新定義 sales view 嘅 migration 讀 effective view",
              EFFECTIVE_READ in newest and RAW_READ not in newest, defining[-1].name)

    print()
    if FAILED:
        print(f"FAILED {len(FAILED)}: {FAILED}")
        return 1
    print(f"063 PC 成交放返契約成立（{'停泊' if parked else '落地'}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
