#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""062：一單成交只會喺價格 lane 信得過佢嘅 identity 時先計數，唔使連 DB。

價格審計 2026-09-26：operator_eligible_accepted_psa10_sales_rows（026／058）
淨係要 binding match_status='exact'，但 quote 一路都係由
operator_strict_source_identity mint。兩者之間嘅缺口啱啱就係死咗嘅 G10 'ebay'
archive：3308 上面 2,713 行／70 張卡入咗 trackedSales，其中 346 行同一日同一價
仲有一行 PC —— 一單交易計兩次。

呢個檔守三樣嘢：

  1. DDL 契約：062 嘅 view 剝走新加嗰句 EXISTS 之後，同 058 逐粒字一樣；
     新句 EXISTS 一定係對住 operator_strict_source_identity、用齊
     variant_id／source_code／external_entity_id 三條 key，而且冇點名任何
     source code（唔係 `<>'ebay'` 咁補鑊）。
  2. 語義 fixture：真係將 062 同 058 嘅 view SELECT body 喺 sqlite 行一次，
     fixture 砌咗「同一單 PC＋archive eBay」、「第二個 exact-but-not-strict PC
     product」、「snk_psa10 storage code」、「隔離中嘅 PC 成交」、「rejected
     binding」五種形狀。按 026 history view 嘅 SUM(quantity)／
     SUM(transaction_value_usd) 攞日數，要同預期完全一樣。
  3. 反假綠：058 原 body（即係今日 live）要紅 —— double count 返嚟就紅；
     另外四種「唔小心會寫錯」嘅 062 變種都要紅。

落地閘：062 停泊喺 ".mysql.sql.pending"（見 migration 檔頭）。停泊同落地只可以
二揀一；停泊時兩個 apply glob 都唔准見到佢，落地就係 drop ".pending" 一個
rename，呢個 test 唔使改。

statement 切法用返 db_runtime.split_sql —— 真正 apply migration 嗰個 splitter。
"""
from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from daily_chain_v2_contract import list_v2_migrations  # noqa: E402
from db_runtime import split_sql  # noqa: E402

MIGRATIONS_DIR = ROOT / "pipelines" / "migrations"
MIGRATION_NAME = "062_daily_chain_v2_sales_rows_strict_identity.mysql.sql"
PARKED_NAME = f"{MIGRATION_NAME}.pending"
BASELINE_PATH = MIGRATIONS_DIR / "058_daily_chain_v2_pc_sale_title_quarantine.mysql.sql"
FE_BAKE_PATH = ROOT / "apps" / "web" / "src" / "lib" / "live-db-snapshot.ts"
VIEW_NAME = "operator_eligible_accepted_psa10_sales_rows"
STRICT_CLAUSE = (
    "AND EXISTS ( SELECT 1 FROM operator_strict_source_identity osi"
    " WHERE osi.variant_id=si.variant_id"
    " AND osi.source_code=si.source_code"
    " AND osi.external_entity_id=si.external_entity_id )"
)

FAILED: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    if ok:
        print(f"ok   {label}")
    else:
        FAILED.append(label)
        print(f"FAIL {label}{': ' + detail if detail else ''}")


def _compact(text: str) -> str:
    return " ".join(text.split())


def _view_statement(sql_text: str) -> str:
    for statement in split_sql(sql_text):
        if f"CREATE OR REPLACE VIEW {VIEW_NAME}" in statement:
            return statement
    return ""


def _view_body(sql_text: str) -> str:
    """CREATE OR REPLACE VIEW ... AS 後面嗰橛 SELECT（原文，唔壓縮）。"""

    statement = _view_statement(sql_text)
    match = re.search(rf"CREATE OR REPLACE VIEW {VIEW_NAME} AS\s*(SELECT\b.*)", statement, re.S)
    return match.group(1).rstrip().rstrip(";") if match else ""


# ---------------------------------------------------------------- DDL 契約


def audit_062(sql_text: str, baseline_view: str) -> list[str]:
    problems: list[str] = []
    statements = split_sql(sql_text)
    if not statements:
        return ["migration has no statements at all"]
    saw_view = False
    saw_schema_version = False
    for statement in statements:
        upper = statement.upper()
        if any(word in upper for word in ("DELETE ", "DROP ", "TRUNCATE ", "UPDATE ")):
            problems.append(f"destructive statement: {_compact(statement)[:80]}")
        if "CREATE TABLE" in upper or "ALTER TABLE" in upper:
            problems.append(f"062 is a view-only change: {_compact(statement)[:80]}")
        if f"CREATE OR REPLACE VIEW {VIEW_NAME}" in statement:
            saw_view = True
            compact = _compact(statement)
            if "'ebay'" in compact.lower():
                problems.append("the view names the archive source: identity, not a source list, decides")
            if STRICT_CLAUSE not in compact:
                problems.append("the view has no strict-identity EXISTS keyed on variant/source/entity")
                continue
            head, _, tail = compact.partition(STRICT_CLAUSE)
            if tail.strip():
                problems.append(f"something follows the strict clause: {tail.strip()[:80]}")
            if head.strip() != baseline_view:
                problems.append(
                    "the view body drifted from the 058 definition: every existing reader"
                    " would silently change meaning"
                )
        if "CARDZ_SCHEMA_VERSION" in upper:
            saw_schema_version = True
            if "INSERT IGNORE" not in upper or "'062'" not in statement:
                problems.append(f"schema version row is not an INSERT IGNORE of '062': {_compact(statement)[:80]}")
    if not saw_view:
        problems.append("no CREATE OR REPLACE VIEW: the readers never inherit the rule")
    if not saw_schema_version:
        problems.append("no cardz_schema_version row: the DB cannot report 062 as applied")
    return problems


# ---------------------------------------------------------------- 語義 fixture

HEX = "a" * 64
# (variant, source_code, external_entity_id, match_status, strict)
BINDINGS = [
    (100, "pricecharting", "pc-100", "exact", True),
    (100, "ebay", "eb-100", "exact", False),          # 死 G10 archive：exact 但唔 strict
    (200, "pricecharting", "pc-200", "exact", True),
    (200, "pricecharting", "pc-200b", "exact", False),  # 第二個未證實嘅 PC product
    (300, "snkrdunk", "snk-300", "exact", True),
    (300, "pricecharting", "pc-300", "exact", True),
    (400, "pricecharting", "pc-400", "rejected", False),  # bake 之後先 reject（vid 1904 形狀）
]
# (sale id, variant, storage source_code, external_entity_id, sold_at, qty, value_usd, quarantined)
SALES = [
    (1, 100, "pricecharting", "pc-100", "2026-07-20 00:00:00", 1, 500.0, False),
    (2, 100, "ebay", "eb-100", "2026-07-20 00:00:00", 1, 500.0, False),   # 同一單交易
    (3, 100, "ebay", "eb-100", "2026-07-21 00:00:00", 1, 520.0, False),
    (4, 200, "pricecharting", "pc-200", "2026-09-10 00:00:00", 1, 100.0, False),
    (5, 200, "pricecharting", "pc-200b", "2026-09-10 00:00:00", 1, 90.0, False),
    (6, 300, "snk_psa10", "snk-300", "2026-09-20 00:00:00", 2, 600.0, False),
    (7, 300, "pricecharting", "pc-300", "2026-09-20 00:00:00", 1, 91.0, True),
    (8, 400, "pricecharting", "pc-400", "2026-09-01 00:00:00", 1, 70.0, False),
]
# 026 history view：sales_count=SUM(quantity)，sales_value_usd=SUM(transaction_value_usd)
EXPECTED = {
    (100, "2026-07-20"): (1, 500.0),
    (200, "2026-09-10"): (1, 100.0),
    (300, "2026-09-20"): (2, 600.0),
}


def _fixture() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.create_function("REGEXP", 2, lambda pattern, value: value is not None and re.search(pattern, str(value)) is not None)
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
        CREATE TABLE market_pc_sale_title_quarantine (sale_observation_id INTEGER);
        """
    )
    for variant, source, entity, status, strict in BINDINGS:
        db.execute("INSERT INTO catalog_source_identity VALUES (?,?,?,?,?)", (variant, source, entity, status, HEX))
        if strict:
            db.execute("INSERT INTO operator_strict_source_identity VALUES (?,?,?)", (variant, source, entity))
    for sale_id, variant, source, entity, sold_at, qty, value, quarantined in SALES:
        payload = f"{sale_id:064x}"
        binding_source = "snkrdunk" if source in ("snk", "snk_psa10") else source
        db.execute(
            "INSERT INTO market_sale_observation VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (sale_id, variant, sold_at, qty, value, "complete", source, sold_at, entity, payload,
             "PSA", "10", "date"),
        )
        db.execute(
            "INSERT INTO market_metric_history_acceptance VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (1000 + sale_id, "market_sale_observation", sale_id, variant, sold_at[:10], sold_at, entity,
             payload, "psa10_sale", binding_source, HEX, HEX, HEX),
        )
        if quarantined:
            db.execute("INSERT INTO market_pc_sale_title_quarantine VALUES (?)", (sale_id,))
    return db


def evaluate(view_body: str) -> list[str]:
    """跑 view body，按日 SUM 返 count／value，同 EXPECTED 對。返回差異。"""

    db = _fixture()
    try:
        rows = db.execute(
            f"SELECT variant_id, observed_date, SUM(quantity), SUM(transaction_value_usd)"
            f" FROM ({view_body}) tx GROUP BY variant_id, observed_date"
        ).fetchall()
    except sqlite3.Error as error:
        return [f"view body does not run: {error}"]
    finally:
        db.close()
    got = {(int(v), str(d)): (int(c), round(float(x), 2)) for v, d, c, x in rows}
    problems = []
    for key in sorted(set(got) | set(EXPECTED)):
        if got.get(key) != EXPECTED.get(key):
            problems.append(f"{key}: got {got.get(key)} want {EXPECTED.get(key)}")
    return problems


# ---------------------------------------------------------------- main


def main() -> int:
    parked = (MIGRATIONS_DIR / PARKED_NAME).is_file()
    landed = (MIGRATIONS_DIR / MIGRATION_NAME).is_file()
    check("062 停泊同落地只可以二揀一", parked != landed, f"parked={parked} landed={landed}")
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
    body_text = path.read_bytes()
    check("062 係 LF（冇 CR）", b"\r" not in body_text)
    sql_text = body_text.decode("utf-8")
    baseline_text = BASELINE_PATH.read_text(encoding="utf-8")
    baseline_view = _compact(_view_statement(baseline_text))
    check("058 baseline view 搵到", bool(baseline_view))

    problems = audit_062(sql_text, baseline_view)
    check("062 DDL 契約成立", not problems, "; ".join(problems[:4]))

    ddl_poisons = {
        "冇咗 strict EXISTS": sql_text.replace("  AND EXISTS (", "  AND 1=1 AND NOT_USED_EXISTS (", 1),
        "EXISTS 漏咗 external_entity_id": sql_text.replace(
            "\n      AND osi.external_entity_id=si.external_entity_id", "", 1),
        "用 source 名單補鑊": sql_text.replace(
            ");\n\nINSERT IGNORE", ")\n  AND s.source_code<>'ebay';\n\nINSERT IGNORE", 1),
        "058 body 飄咗": sql_text.replace("AND s.quantity>0", "AND s.quantity>=0", 1),
        "schema version 寫錯": sql_text.replace("VALUES ('062')", "VALUES ('058')", 1),
    }
    for label, poisoned in ddl_poisons.items():
        check(f"DDL poison「{label}」會紅", poisoned != sql_text and bool(audit_062(poisoned, baseline_view)))

    print()
    body_062 = _view_body(sql_text)
    body_058 = _view_body(baseline_text)
    check("062 view body 抽得到", bool(body_062))
    diff = evaluate(body_062)
    check("062：每單交易計一次、隔離／rejected／未證實 identity 全部唔計", not diff, "; ".join(diff))

    # 反假綠：058（今日 live 嘅定義）一定要紅，而且紅喺 double count 嗰日。
    diff_058 = evaluate(body_058)
    check("plant：058 原 body（double count 返嚟）會紅", bool(diff_058))
    check("plant：058 紅喺同一單交易嗰日（100, 2026-07-20 計咗 2 單）",
          any("(100, '2026-07-20'): got (2, 1000.0)" in line for line in diff_058), "; ".join(diff_058))

    strict_tail = r"\n  AND EXISTS \(\n    SELECT 1 FROM operator_strict_source_identity.*\Z"
    semantic_poisons = {
        "strict EXISTS 刪走": re.sub(strict_tail, "", body_062, flags=re.S),
        "EXISTS 反轉做 NOT EXISTS": body_062.replace("  AND EXISTS (", "  AND NOT EXISTS (", 1),
        "EXISTS 漏咗 external_entity_id": body_062.replace(
            "\n      AND osi.external_entity_id=si.external_entity_id", "", 1),
        "用 source 名單代替 identity": re.sub(
            strict_tail, "\n  AND s.source_code<>'ebay'", body_062, flags=re.S),
        "EXISTS 用 storage code（s.source_code）對": body_062.replace(
            "osi.source_code=si.source_code", "osi.source_code=s.source_code", 1),
    }
    for label, poisoned in semantic_poisons.items():
        check(f"fixture plant「{label}」會紅", poisoned != body_062 and bool(evaluate(poisoned)))

    print()
    fe = FE_BAKE_PATH.read_text(encoding="utf-8")
    check("FE 成交數得一個讀點：operator_accepted_psa10_sales_history",
          fe.count("FROM operator_accepted_psa10_sales_history") == 1)
    check("FE 冇繞過 view 直讀 market_sale_observation", "market_sale_observation" not in fe)

    print()
    if FAILED:
        print(f"FAILED {len(FAILED)}: {FAILED}")
        return 1
    print(f"062 成交 strict identity 契約成立（{'停泊' if parked else '落地'}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
