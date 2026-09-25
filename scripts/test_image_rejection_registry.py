#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""061：人手 image reject 喺 FE 讀嗰下生效 —— migration 契約 + 三處 predicate 一致，唔使連 DB。

背景：market_image_rejection_registry 係人手 image 判決（2026-09-25 有 42 行），
但一直只有 writer（rebuild_036 image-bind、SNK EN freeze）讀，FE 讀路
（operator_canonical_image_projection + live-db-snapshot.ts 嘅 freeze fallback）
從來冇讀過：任何 lane 再 accept 一張已 reject 嘅 (variant, content)，佢就直接返上網。
DADDY 2026-09-26：圖啱先（同卡、編號、parallel、語言）；錯卡／EN 用 JP 圖／overlay／唔係卡正面要換圖，
SAMPLE 水印但卡啱照出街，有乾淨正確版先換。人手 reject 係 hard authority。

呢個檔守四樣嘢：

  1. 落地閘：061 停泊喺 ".mysql.sql.pending"（V2 task 由呢棵 working tree 跑，
     見到 0[5-9][0-9]_daily_chain_v2_*.mysql.sql 下一 tick 就 apply，commit
     唔 commit 都一樣）。停泊同落地只可以二揀一，落地 = drop ".pending" 一個 rename，
     落地名一定要俾 chain 嘅 migrate stage 揀到。go/no-go query 喺 migration 檔頭。
  2. 張表：CREATE TABLE IF NOT EXISTS 逐個 definition 等於 live 3308 嘅
     SHOW CREATE TABLE（2026-09-25 抄落嚟）——喺 live 上係 no-op，新 DB 攞到同一套 key／FK。
  3. view：除咗尾嗰句 NOT EXISTS，同 033 嘅定義 byte-for-byte 一樣。
  4. 同一句 predicate 喺三個讀點一字不差：061 view、live-db-snapshot.ts 嘅
     freeze fallback、rebuild_036._fe_image_state（S9 gate 用嘅 FE 鏡像）。
     少一處，嗰條路就會將 reject 咗嘅圖放返出街。

每條判別器都有 poison 反假綠：種毒 → 一定要紅。
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from daily_chain_v2_contract import list_v2_migrations  # noqa: E402
from db_runtime import split_sql  # noqa: E402

MIGRATIONS_DIR = ROOT / "pipelines" / "migrations"
LANDED_NAME = "061_daily_chain_v2_image_rejection_registry.mysql.sql"
PARKED_NAME = f"{LANDED_NAME}.pending"
BASELINE_PATH = MIGRATIONS_DIR / "033_canonical_projection_binding_lineage.mysql.sql"
FE_BAKE_PATH = ROOT / "apps" / "web" / "src" / "lib" / "live-db-snapshot.ts"
FE_MIRROR_PATH = ROOT / "pipelines" / "rebuild_036.py"
VIEW_NAME = "operator_canonical_image_projection"
TABLE_NAME = "market_image_rejection_registry"
PREDICATE = (
    "AND NOT EXISTS (SELECT 1 FROM market_image_rejection_registry rej"
    " WHERE rej.variant_id=a.variant_id AND rej.content_sha256=a.content_sha256)"
)
# SHOW CREATE TABLE market_image_rejection_registry, live 3308, 2026-09-25.
LIVE_REGISTRY_DDL = """CREATE TABLE `market_image_rejection_registry` (
  `variant_id` bigint unsigned NOT NULL,
  `content_sha256` char(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `first_image_asset_id` bigint unsigned DEFAULT NULL,
  `rejection_reason` varchar(500) COLLATE utf8mb4_unicode_ci NOT NULL,
  `decision_code_sha256` char(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `review_run_id` varchar(191) COLLATE utf8mb4_unicode_ci NOT NULL,
  `rejected_at` datetime(6) NOT NULL,
  `created_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`variant_id`,`content_sha256`),
  KEY `ix_image_rejection_registry_asset` (`first_image_asset_id`),
  CONSTRAINT `fk_image_rejection_registry_asset` FOREIGN KEY (`first_image_asset_id`) REFERENCES `market_image_asset` (`id`),
  CONSTRAINT `fk_image_rejection_registry_variant` FOREIGN KEY (`variant_id`) REFERENCES `catalog_variant` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"""

FAILED: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    if ok:
        print(f"ok   {label}")
    else:
        FAILED.append(label)
        print(f"FAIL {label}{': ' + detail if detail else ''}")


def _compact(text: str) -> str:
    return " ".join(text.split())


def _norm(text: str) -> str:
    text = " ".join(text.replace("`", "").upper().split())
    return re.sub(r"\s*([(),=])\s*", r"\1", text)


def _table_shape(statement: str) -> tuple[list[str], str]:
    """(top-level definitions, table options) of a CREATE TABLE statement."""

    open_at = statement.index("(")
    close_at = statement.rindex(")")
    body = statement[open_at + 1:close_at]
    parts: list[str] = []
    depth = 0
    current = ""
    for char in body:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "," and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += char
    parts.append(current)
    return [_norm(part) for part in parts if part.strip()], _norm(statement[close_at + 1:])


def _statement(sql_text: str, prefix: str) -> str:
    for statement in split_sql(sql_text):
        if statement.startswith(prefix):
            return statement
    return ""


# ---------------------------------------------------------------- 061 契約


def audit_061(sql_text: str, baseline_view: str) -> list[str]:
    problems: list[str] = []
    statements = split_sql(sql_text)
    if len(statements) != 3:
        problems.append(f"expected 3 statements (table, view, schema version), got {len(statements)}")
    for statement in statements:
        if any(word in statement.upper() for word in ("DELETE ", "DROP ", "TRUNCATE ")):
            problems.append(f"destructive statement: {_compact(statement)[:80]}")

    table = _statement(sql_text, f"CREATE TABLE IF NOT EXISTS {TABLE_NAME} (")
    if not table:
        problems.append("no CREATE TABLE IF NOT EXISTS for the registry")
    elif _table_shape(table) != _table_shape(LIVE_REGISTRY_DDL):
        problems.append("registry DDL differs from the live SHOW CREATE TABLE")

    view = _statement(sql_text, f"CREATE OR REPLACE VIEW {VIEW_NAME} AS")
    if not view:
        problems.append("no CREATE OR REPLACE VIEW: the FE read never learns the registry")
    elif not view.startswith(baseline_view):
        problems.append("view body drifted from 033: every reader would change meaning")
    elif _compact(view[len(baseline_view):]) != PREDICATE:
        problems.append(
            "the only addition to the 033 view must be the registry NOT EXISTS, keyed on"
            " the asset's variant_id + content_sha256"
        )

    version = _statement(sql_text, "INSERT IGNORE INTO cardz_schema_version")
    if "'061'" not in version:
        problems.append("no INSERT IGNORE of cardz_schema_version '061'")
    return problems


def run_migration_checks() -> str:
    parked = MIGRATIONS_DIR / PARKED_NAME
    landed = MIGRATIONS_DIR / LANDED_NAME
    check("061 停泊或者落地，二揀一", parked.is_file() != landed.is_file(),
          f"parked={parked.is_file()} landed={landed.is_file()}")
    others = sorted(
        path.name for path in MIGRATIONS_DIR.iterdir()
        if path.name.startswith("061_") and path.name not in {PARKED_NAME, LANDED_NAME}
    )
    check("冇第二個 061 檔搶號", not others, str(others))
    if parked.is_file():
        check("停泊中：chain 嘅 migrate stage 揀唔到", LANDED_NAME not in list_v2_migrations(ROOT))
        check("停泊中：db_runtime.migrate 嘅 *.mysql.sql 揀唔到",
              not any(p.name.startswith("061_") for p in MIGRATIONS_DIR.glob("*.mysql.sql")))
    if landed.is_file():
        check("落地咗：chain 嘅 migrate stage 真係揀到", LANDED_NAME in list_v2_migrations(ROOT))
    path = parked if parked.is_file() else landed
    if not path.is_file() or not BASELINE_PATH.is_file():
        check("061 同 033 baseline 都喺度", False, f"{path} / {BASELINE_PATH}")
        return ""
    body = path.read_text(encoding="utf-8")
    baseline_view = _statement(
        BASELINE_PATH.read_text(encoding="utf-8"), f"CREATE OR REPLACE VIEW {VIEW_NAME} AS"
    )
    check("喺 033 搵到現役 view 做 baseline", bool(baseline_view))

    problems = audit_061(body, baseline_view)
    check("061 不變式全部成立", not problems, "; ".join(problems[:4]))

    poisons = {
        "view 冇咗 registry 條件（等於冇做 061）": body.replace(
            "\n  AND NOT EXISTS (SELECT 1 FROM market_image_rejection_registry rej\n"
            "                  WHERE rej.variant_id=a.variant_id AND rej.content_sha256=a.content_sha256)",
            "",
        ),
        "registry 條件 key 錯 sha（freeze 嗰邊）": body.replace(
            "rej.content_sha256=a.content_sha256", "rej.content_sha256=f.content_sha256"
        ),
        "view 順手改咗欄位": body.replace(
            "a.width_px AS canonical_image_width", "a.height_px AS canonical_image_width"
        ),
        "張表漏咗 FK（新 DB 同 live 唔同形）": body.replace(
            ",\n    CONSTRAINT fk_image_rejection_registry_variant\n"
            "      FOREIGN KEY (variant_id) REFERENCES catalog_variant (id)",
            "",
        ),
    }
    for label, poisoned in poisons.items():
        check(f"poison「{label}」真係種到", poisoned != body)
        check(f"poison「{label}」會紅", bool(audit_061(poisoned, baseline_view)))
    return body


# ------------------------------------------------------- 三處 predicate 一致


def _sql_without_comments(sql: str) -> str:
    return _compact("\n".join(
        line for line in sql.splitlines() if not line.lstrip().startswith("--")
    ))


def ts_fallback_sql(ts_text: str) -> list[str]:
    """live-db-snapshot.ts 入面所有讀 freeze fallback 嘅 template literal。"""

    return [
        _sql_without_comments(literal)
        for literal in re.findall(r"`([^`]*)`", ts_text)
        if "FROM operator_binding_freeze f" in literal and "market_image_asset a" in literal
    ]


def _literal(node: ast.AST) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(_literal(value) for value in node.values)
    return "{}"


def py_fe_mirror_sql(py_text: str) -> list[str]:
    tree = ast.parse(py_text)
    function = next(
        (node for node in ast.walk(tree)
         if isinstance(node, ast.FunctionDef) and node.name == "_fe_image_state"),
        None,
    )
    if function is None:
        return []
    calls = sorted(
        (node for node in ast.walk(function)
         if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "execute"),
        key=lambda node: node.lineno,
    )
    return [_compact(_literal(call.args[0])) for call in calls if call.args]


def parity_problems(migration_text: str, ts_text: str, py_text: str) -> list[str]:
    problems: list[str] = []
    view = _compact(_statement(migration_text, f"CREATE OR REPLACE VIEW {VIEW_NAME} AS"))
    if not view.endswith(PREDICATE):
        problems.append("061 view does not end with the registry predicate")

    ts_sql = ts_fallback_sql(ts_text)
    if len(ts_sql) != 1:
        problems.append(f"expected exactly one TS freeze fallback query, found {len(ts_sql)}")
    for sql in ts_sql:
        if sql.count(PREDICATE) != 1:
            problems.append("live-db-snapshot.ts freeze fallback lacks the registry predicate")

    py_sql = py_fe_mirror_sql(py_text)
    fallback = [sql for sql in py_sql if "FROM operator_binding_freeze f" in sql]
    projection = [sql for sql in py_sql if f"FROM {VIEW_NAME}" in sql]
    if len(fallback) != 1 or len(projection) != 1:
        problems.append(
            f"rebuild_036._fe_image_state no longer mirrors both FE queries"
            f" (fallback={len(fallback)}, projection={len(projection)})"
        )
    for sql in fallback:
        if sql.count(PREDICATE) != 1:
            problems.append("rebuild_036._fe_image_state fallback lacks the registry predicate")
    # The view carries the predicate itself; reading it through the view must
    # not be the only place the Python mirror can drift from TS.
    for sql in projection:
        if TABLE_NAME in sql:
            problems.append("the projection read re-implements the registry filter")
    return problems


def run_parity_checks(migration_text: str) -> None:
    ts_text = FE_BAKE_PATH.read_text(encoding="utf-8")
    py_text = FE_MIRROR_PATH.read_text(encoding="utf-8")
    problems = parity_problems(migration_text, ts_text, py_text)
    check("三個 FE 讀點都用同一句 registry predicate", not problems, "; ".join(problems[:4]))
    check("TS fallback 真係抽到一條 query", len(ts_fallback_sql(ts_text)) == 1)
    check("Python 鏡像真係抽到兩條 query", len(py_fe_mirror_sql(py_text)) == 2)

    removed_ts = re.sub(
        r"\n\s*AND NOT EXISTS \(SELECT 1 FROM market_image_rejection_registry rej\n"
        r"\s*WHERE rej\.variant_id=a\.variant_id AND rej\.content_sha256=a\.content_sha256\)",
        "", ts_text,
    )
    poisons = {
        "TS fallback 冇咗 predicate": (migration_text, removed_ts, py_text),
        "TS fallback key 錯 sha": (
            migration_text,
            ts_text.replace("rej.content_sha256=a.content_sha256", "rej.content_sha256=f.content_sha256"),
            py_text,
        ),
        "Python 鏡像冇咗 predicate": (
            migration_text, ts_text,
            py_text.replace(
                '"   AND NOT EXISTS (SELECT 1 FROM market_image_rejection_registry rej"', '""'
            ),
        ),
        "Python 鏡像 key 錯 variant": (
            migration_text, ts_text,
            py_text.replace("rej.variant_id=a.variant_id", "rej.variant_id=f.variant_id"),
        ),
    }
    originals = (migration_text, ts_text, py_text)
    for label, poisoned in poisons.items():
        check(f"poison「{label}」真係種到", poisoned != originals)
        check(f"poison「{label}」會紅", bool(parity_problems(*poisoned)))


def main() -> int:
    body = run_migration_checks()
    if body:
        run_parity_checks(body)
    print()
    if FAILED:
        print(f"FAILED {len(FAILED)}: {FAILED}")
        return 1
    print("061 image rejection registry 契約成立")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
