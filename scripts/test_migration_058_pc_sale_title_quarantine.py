#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""058：PC title↔卡號隔離由 receipt 變 DB fact —— DDL 契約 + sync function，唔使連 DB。

背景（事故形狀 29，v1326 Latias +556%）：PriceCharting 自己嘅 fuzzy match 會將
第二張卡嘅成交塞入 exact product page，張 $91 成交照入庫，仲做咗 30 日窗嘅
anchor。修佢嘅隔離帳本一路只係一個 receipt 檔，所以「有冇應用」係逐個 call site
靠人手記得：psa10_latest_sale_quote.plan() 出報價時剔走，live-db-snapshot.ts 出圖
之後再減；而 operator_fe_export 嘅日 projection、operator_card_daily_fact_projection
同佢哋嘅 fact_content / fact_lineage hash 一路照計毒行。

058 將 receipt materialise 落 market_pc_sale_title_quarantine，再由
operator_eligible_accepted_psa10_sales_rows 一次過剔走 —— 一個概念一個執行點。

呢個檔守三樣嘢：

  1. DDL 契約：view 除咗尾嗰句 NOT EXISTS 之外，同 026 嘅定義**逐個字一樣**。
     view 唔係新加一個，係 CREATE OR REPLACE 現役 view；欄位／別名／join／
     predicate 有任何一粒字飄咗，下游全部讀者（operator_accepted_psa10_sales_history
     → operator_card_daily_fact_dates / operator_card_daily_fact_projection /
     operator_fe_export / live-db-snapshot.ts）就會靜靜咁換咗語意。
  2. sync function：只准 upsert，永遠唔准 DELETE；receipt 唔見要拋，唔准當
     「冇嘢隔離」；每行嘅 receipt_sha256 要等於 receipt **原始 bytes** 嘅 sha；
     duplicate key 唔改 reason（2026-09-25 兩個判別器之後 first reason wins）。
  3. call site：_mint_sale_quotes 真係喺 mint **之前**叫佢（PC lane），同埋 FE
     bake 唔會喺 DB 剔走之後再喺 TS 減多次（同一日仲有真成交嘅話，減兩次會連真
     嗰單都食埋）。

「有 check 但零 call site」＝冇 check，所以 (3) 同 (1)(2) 一樣係硬 check。
"""
from __future__ import annotations

import hashlib
import inspect
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from daily_chain_v2_contract import list_v2_migrations  # noqa: E402
from db_runtime import split_sql  # noqa: E402

MIGRATION_NAME = "058_daily_chain_v2_pc_sale_title_quarantine.mysql.sql"
MIGRATION_PATH = ROOT / "pipelines" / "migrations" / MIGRATION_NAME
BASELINE_PATH = ROOT / "pipelines" / "migrations" / "026_snk_en_storefront_historical_lineage.mysql.sql"
FE_BAKE_PATH = ROOT / "apps" / "web" / "src" / "lib" / "live-db-snapshot.ts"
VIEW_NAME = "operator_eligible_accepted_psa10_sales_rows"
TABLE_NAME = "market_pc_sale_title_quarantine"

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


def _strip_exclusion(view_sql: str) -> str:
    """攞走 058 加嘅 NOT EXISTS 子句，淨返應該同 026 一模一樣嗰橛。"""

    compact = _compact(view_sql)
    marker = "AND NOT EXISTS ("
    if marker not in compact:
        return compact
    return compact[: compact.index(marker)].strip()


# ---------------------------------------------------------------- DDL 契約


def audit_058(sql_text: str, baseline_view: str) -> list[str]:
    """返回 058 body 入面違反不變式嘅地方。"""

    problems: list[str] = []
    statements = split_sql(sql_text)
    if not statements:
        return ["migration has no statements at all"]

    saw_table = False
    saw_view = False
    saw_schema_version = False
    for statement in statements:
        upper = statement.upper()
        if any(word in upper for word in ("DELETE ", "DROP ", "TRUNCATE ")):
            problems.append(f"destructive statement: {_compact(statement)[:80]}")
        if f"CREATE TABLE IF NOT EXISTS {TABLE_NAME}" in statement:
            saw_table = True
            compact = _compact(statement)
            for column in (
                "sale_observation_id BIGINT UNSIGNED NOT NULL",
                "variant_id BIGINT UNSIGNED NOT NULL",
                "reason VARCHAR(64) NOT NULL",
                "receipt_sha256 CHAR(64) NOT NULL",
                "written_at DATETIME(6) NOT NULL",
            ):
                if column not in compact:
                    problems.append(f"quarantine table is missing column: {column}")
            if "PRIMARY KEY (sale_observation_id)" not in compact:
                problems.append(
                    "quarantine table has no PRIMARY KEY (sale_observation_id):"
                    " the nightly upsert would duplicate rows instead of re-stamping them"
                )
        if f"CREATE OR REPLACE VIEW {VIEW_NAME}" in statement:
            saw_view = True
            compact = _compact(statement)
            if TABLE_NAME not in compact:
                problems.append("the view does not reference the quarantine table at all")
            if "NOT EXISTS" not in compact.upper():
                problems.append("the view has no NOT EXISTS exclusion: quarantined sales still reach FE")
            if "tq.sale_observation_id=s.id" not in compact.replace(" =", "=").replace("= ", "="):
                problems.append("the exclusion is not keyed on the sale observation id")
            if _strip_exclusion(compact) != baseline_view:
                problems.append(
                    "the view body drifted from the 026 definition: every existing reader"
                    " would silently change meaning"
                )
        if "CARDZ_SCHEMA_VERSION" in upper:
            saw_schema_version = True
            if "INSERT IGNORE" not in upper or "'058'" not in statement:
                problems.append(f"schema version row is not an INSERT IGNORE of '058': {_compact(statement)[:80]}")

    if not saw_table:
        problems.append("no CREATE TABLE IF NOT EXISTS for the quarantine table")
    if not saw_view:
        problems.append("no CREATE OR REPLACE VIEW: the readers never inherit the exclusion")
    if not saw_schema_version:
        problems.append("no cardz_schema_version row: the DB cannot report 058 as applied")
    return problems


def run_ddl_checks() -> None:
    check("058 檔案喺 migrations 度", MIGRATION_PATH.is_file(), str(MIGRATION_PATH))
    check("026 baseline 仲喺度", BASELINE_PATH.is_file(), str(BASELINE_PATH))
    if not (MIGRATION_PATH.is_file() and BASELINE_PATH.is_file()):
        return

    body = MIGRATION_PATH.read_text(encoding="utf-8")
    baseline_view = _compact(_view_statement(BASELINE_PATH.read_text(encoding="utf-8")))
    check("喺 026 搵到現役 view 定義做 baseline", bool(baseline_view))

    # 檔名要真係跟到 V2 glob，唔係 chain 嘅 migrate stage 永遠唔會 apply 佢。
    check("058 會被 chain 嘅 migrate stage 揀到", MIGRATION_NAME in list_v2_migrations(ROOT))

    problems = audit_058(body, baseline_view)
    check("058 DDL 不變式全部成立", not problems, "; ".join(problems[:4]))
    check("split_sql 真係切到 058 嘅 statement", len(split_sql(body)) == 3, str(len(split_sql(body))))

    # 反假綠：判別器對種落去嘅毒真係會紅。呢三種就係「唔小心會做錯」嘅寫法。
    poisons = {
        "view 冇咗隔離條件（等於冇做過 058）": body.replace(
            "  AND NOT EXISTS (\n    SELECT 1 FROM market_pc_sale_title_quarantine tq\n    WHERE tq.sale_observation_id=s.id\n  )",
            "",
        ),
        "view 順手改咗欄位（下游 hash 靜靜變）": body.replace(
            "  s.coverage_status,s.source_code,h.lineage_sha256,s.fetched_at AS evidence_at",
            "  s.coverage_status,s.source_code,h.lineage_sha256,s.sold_at AS evidence_at",
        ),
        "隔離表冇 primary key（upsert 變重複行）": body.replace(
            "    PRIMARY KEY (sale_observation_id),\n",
            "",
        ),
    }
    for label, poisoned in poisons.items():
        check(f"poison「{label}」真係種到（唔係 replace 落空）", poisoned != body)
        check(f"poison「{label}」會紅", bool(audit_058(poisoned, baseline_view)))


# ------------------------------------------------------- sync function 契約


class _FakeCursor:
    def __init__(self, log: list[tuple[str, object]], raise_on_write: Exception | None = None) -> None:
        self._log = log
        self._raise_on_write = raise_on_write

    def executemany(self, sql: str, rows: list[tuple]) -> None:
        self._log.append(("executemany", (sql, list(rows))))
        if self._raise_on_write is not None:
            raise self._raise_on_write

    def execute(self, sql: str, params: object = None) -> None:  # pragma: no cover - 唔應該行到
        self._log.append(("execute", (sql, params)))


class _FakeConnection:
    def __init__(self, log: list[tuple[str, object]], raise_on_write: Exception | None = None) -> None:
        self._log = log
        self._raise_on_write = raise_on_write

    def cursor(self):
        return _FakeCursor(self._log, self._raise_on_write)

    def commit(self) -> None:
        self._log.append(("commit", None))

    def close(self) -> None:
        self._log.append(("close", None))


def _run_sync(
    entries_doc: object | None,
    tmp: Path,
    raise_on_write: Exception | None = None,
) -> tuple[dict, list[tuple[str, object]], bytes | None]:
    """喺假 DB 上面行一次 _sync_pc_sale_title_quarantine，返回 (result, log, raw)。"""

    import collect_control as CC
    import psa10_latest_sale_quote as PLSQ

    receipt = tmp / "pc_sale_title_quarantine_current.json"
    raw: bytes | None = None
    if entries_doc is None:
        # 上一個 case 寫低咗嘅檔要清走，否則「receipt 唔見」呢個 case 根本冇試過。
        receipt.unlink(missing_ok=True)
    else:
        raw = json.dumps(entries_doc, ensure_ascii=False).encode("utf-8")
        receipt.write_bytes(raw)

    log: list[tuple[str, object]] = []
    original_receipt = PLSQ.QUARANTINE_RECEIPT
    original_db = CC.db
    original_load_env = CC.load_env
    PLSQ.QUARANTINE_RECEIPT = receipt
    CC.db = lambda *a, **k: _FakeConnection(log, raise_on_write)  # type: ignore[assignment]
    CC.load_env = lambda *a, **k: None  # type: ignore[assignment]
    try:
        return CC._sync_pc_sale_title_quarantine(), log, raw
    finally:
        PLSQ.QUARANTINE_RECEIPT = original_receipt
        CC.db = original_db  # type: ignore[assignment]
        CC.load_env = original_load_env  # type: ignore[assignment]


def run_sync_checks() -> None:
    import collect_control as CC

    with tempfile.TemporaryDirectory() as raw_tmp:
        tmp = Path(raw_tmp)

        doc = {
            "generatedAt": "2026-08-23T00:00:00Z",
            "discriminator": "c11_pc_sold_ingest.title_collector_contradiction",
            "entries": [
                {
                    "saleObservationId": 1601127,
                    "variantId": 651,
                    "observedDate": "2026-07-20",
                    "transactionValueUsd": 398.85,
                    "quantity": 1,
                    "reason": "title_collector_contradiction",
                },
                {
                    "saleObservationId": 1601128,
                    "variantId": 652,
                    "observedDate": "2026-07-21",
                    "transactionValueUsd": 91.0,
                    "quantity": 1,
                },
            ],
        }
        result, log, raw = _run_sync(doc, tmp)
        assert raw is not None

        sent = [item for item in log if item[0] == "executemany"]
        check("sync 有寫落 DB", len(sent) == 1, str(log))
        if sent:
            sql, rows = sent[0][1]  # type: ignore[misc]
            upper = sql.upper()
            check("寫入係 INSERT ... ON DUPLICATE KEY UPDATE（upsert）",
                  "INSERT INTO" in upper and "ON DUPLICATE KEY UPDATE" in upper)
            check("sync 永遠唔會 DELETE（刪一行＝放返條毒成交出街）",
                  not any(word in upper for word in ("DELETE", "TRUNCATE", "DROP")))
            # 2026-09-25 起 receipt 有兩個判別器：同一單後嚟俾另一個判別器再中，
            # 張表要記住當初點解隔離（first reason wins），receipt 亦照 stored
            # reason 帶返出嚟（pc_sale_title_quarantine.compose_entries）。
            update_clause = ""
            if "ON DUPLICATE KEY UPDATE" in upper:
                update_clause = _compact(sql[upper.index("ON DUPLICATE KEY UPDATE"):])
                update_clause = update_clause.replace(" =", "=").replace("= ", "=")
            check("duplicate key 唔改 reason（first reason wins）",
                  bool(update_clause) and "reason=" not in update_clause, update_clause)
            check("duplicate key 仍然 re-stamp receipt_sha256 同 written_at",
                  "receipt_sha256=VALUES(receipt_sha256)" in update_clause
                  and "written_at=VALUES(written_at)" in update_clause, update_clause)
            check("每張 receipt entry 都寫一行", len(rows) == 2, str(len(rows)))
            check("sale_observation_id / variant_id 對得返 receipt",
                  [(row[0], row[1]) for row in rows] == [(1601127, 651), (1601128, 652)],
                  str([(row[0], row[1]) for row in rows]))
            check("冇 reason 嘅 entry 跌返落 discriminator 名，唔係空白",
                  rows[1][2] == "title_collector_contradiction", str(rows[1][2]))
            expected_sha = hashlib.sha256(raw).hexdigest()
            check("receipt_sha256 = receipt 原始 bytes 嘅 sha256",
                  all(row[3] == expected_sha for row in rows) and result["receiptSha256"] == expected_sha)
        check("sync 有 commit", ("commit", None) in log)
        check("sync 有收線", ("close", None) in log)
        check("sync 報返寫咗幾多行", result.get("entries") == 2, str(result))

        # 價格尖刺 entry 照自己個 reason 寫，唔准跌返做 title 嗰個。
        spike_doc = {"entries": [
            {"saleObservationId": 1920226, "variantId": 1148, "reason": "price_isolated_spike"},
        ]}
        _, spike_log, _ = _run_sync(spike_doc, tmp)
        spike_rows = [row for kind, payload in spike_log if kind == "executemany" for row in payload[1]]  # type: ignore[index]
        check("price_isolated_spike entry 照 reason 寫落表",
              [(row[0], row[2]) for row in spike_rows] == [(1920226, "price_isolated_spike")],
              str(spike_rows))

        # 空 receipt：唔使開連線，但一樣要報 sha（下游可以憑佢知讀過邊份）。
        empty_result, empty_log, empty_raw = _run_sync({"entries": []}, tmp)
        assert empty_raw is not None
        check("空 receipt 唔會開 DB 連線", empty_log == [], str(empty_log))
        check("空 receipt 一樣報 sha", empty_result["receiptSha256"] == hashlib.sha256(empty_raw).hexdigest())

        # receipt 唔見 = 硬停，唔准當「冇嘢隔離」。
        missing_raised = False
        try:
            _run_sync(None, tmp)
        except FileNotFoundError:
            missing_raised = True
        except Exception as exc:  # noqa: BLE001
            missing_raised = True
            print(f"     (missing receipt raised {type(exc).__name__})")
        check("receipt 唔見會拋，唔會靜靜當冇嘢隔離", missing_raised)

        # 058 未 apply（1146 table doesn't exist）：報 skipped，唔好炸咗成條 PC
        # lane —— 嗰陣 DB 入面仲係舊 view，隔離一樣行緊（planner 剔、FE 減），
        # 即係退返 058 之前，唔係開窿。
        absent_result: dict = {}
        absent_log: list[tuple[str, object]] = []
        absent_blew_up = ""
        try:
            absent_result, absent_log, _ = _run_sync(
                doc, tmp, raise_on_write=RuntimeError(1146, "Table 'x.market_pc_sale_title_quarantine' doesn't exist")
            )
        except Exception as exc:  # noqa: BLE001 - 就係要證明佢唔應該拋
            absent_blew_up = f"{type(exc).__name__}: {exc}"
        check("058 未 apply 唔會炸 PC lane",
              not absent_blew_up and absent_result.get("skipped") == "table_absent",
              absent_blew_up or str(absent_result))
        check("058 未 apply 唔會報「寫咗行」", absent_result.get("entries") == 0, str(absent_result))
        check("058 未 apply 唔會 commit（冇嘢寫成功）", ("commit", None) not in absent_log)

        # 其他 DB error 一律拋：唔准借 1146 條路吞低任何寫入失敗。
        other_error_raised = False
        try:
            _run_sync(doc, tmp, raise_on_write=RuntimeError(1213, "Deadlock found"))
        except RuntimeError as exc:
            other_error_raised = tuple(exc.args)[:1] == (1213,)
        check("其他 DB error 照拋（唔准當 table_absent 吞低）", other_error_raised)

        # receipt 形狀壞咗一樣要拋。
        shape_raised = False
        try:
            _run_sync({"generatedAt": "x"}, tmp)
        except ValueError:
            shape_raised = True
        check("receipt 冇 entries list 會拋 ValueError", shape_raised)

    # call site：有 function 冇人叫 = 冇做過。
    mint_src = inspect.getsource(CC._mint_sale_quotes)
    check("_mint_sale_quotes 真係叫 sync", "_sync_pc_sale_title_quarantine()" in mint_src)
    if "_sync_pc_sale_title_quarantine()" in mint_src and "_run(cmd" in mint_src:
        check("sync 行喺 mint 之前（唔係報完價先隔離）",
              mint_src.index("_sync_pc_sale_title_quarantine()") < mint_src.index("_run(cmd"))
    check("sync 只落 PriceCharting lane", 'source == "pricecharting"' in mint_src)


# ------------------------------------------------------------- FE bake 契約


def run_fe_checks() -> None:
    check("FE bake 檔仲喺度", FE_BAKE_PATH.is_file(), str(FE_BAKE_PATH))
    if not FE_BAKE_PATH.is_file():
        return
    fe = FE_BAKE_PATH.read_text(encoding="utf-8")

    # 058 之後 DB 已經剔走咗嗰啲成交。TS 如果照舊無條件再減一次，同一日仲有真
    # 成交嘅卡就會被減兩次，真成交都冇埋 —— 呢個 check 就係守住呢一步。
    check("FE 唔會再無條件減一次（loadSaleQuarantine() 冇參數＝雙重扣）",
          "loadSaleQuarantine()" not in fe)
    check("FE 減之前排除咗 DB 已經剔走嗰批",
          "loadSaleQuarantine(await loadDbExcludedSaleIds(connection))" in fe)
    check("FE 讀 DB 隔離表",
          f"FROM {TABLE_NAME}" in fe)
    check("表未 apply 先當空 set（退返 058 之前嘅行為，唔係 fail-open）",
          'ER_NO_SUCH_TABLE' in fe)
    check("其他 DB error 照拋", "throw error;" in fe)


def main() -> int:
    run_ddl_checks()
    print()
    run_sync_checks()
    print()
    run_fe_checks()

    print()
    if FAILED:
        print(f"FAILED {len(FAILED)}: {FAILED}")
        return 1
    print("058 PC 成交隔離契約成立")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
