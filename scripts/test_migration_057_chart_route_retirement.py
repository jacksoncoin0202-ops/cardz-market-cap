#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""057（Phase 2）退役 chart quote route 嘅不變式，唔使連 DB。

056 只係將 chart lane 壓到 priority 90，057 先至真係攞走佢哋嘅 is_eligible。
呢個檔守住三條會死人嘅界線：

  * v2 chart 行只准 `is_eligible=0`，**永遠唔准** `is_active=0`。
    daily_chain_v2_db.sync_source_registry 用
    `SELECT DISTINCT source_code ... WHERE is_active=1` 做「已經有 policy」名單，
    唔喺名單就會重新 seed 一行 is_eligible=1 嘅預設 route —— 即係悄悄解返
    呢個檔想退役嘅 lane。
  * `is_active=0` 只准落 cardz-route-v1。
  * v2 成交 lane（*_sales）一個字都唔准出現喺 057 嘅 statement 入面。

第四條係落地閘，同樣唔使連 DB：057 停泊喺 `.mysql.sql.pending`，chain 嘅
migrate glob 同 db_runtime.migrate 嘅 `*.mysql.sql` 兩個都見唔到佢。落地就係
drop 走 `.pending` 一個 rename——而嗰個 rename 會令呢個 test 紅，逼落地嗰個
commit 同時交代檔頭嗰條 go/no-go query 已經返 0（2026-08-23 讀數係 1604）。
之前呢度反而 assert 佢「會被 migrate stage 揀到」，即係將危險狀態鎖死做綠。

chart code 名單唔喺呢度抄第二次：由 current_quote_revision 推導
（全部註冊 storage code 減去可以 mint 嘅），所以將來加一條成交 lane、或者再
退役多一個 chart lane，migration 同呢個 test 會一齊紅，唔會靜靜咁分岔。

statement 切法用返 db_runtime.split_sql —— 即係真正 apply migration 嗰個
splitter，唔係喺呢度再寫一個近似版。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from current_quote_revision import (  # noqa: E402
    all_quote_storage_source_codes,
    mintable_quote_storage_source_codes,
)
from daily_chain_v2_contract import list_v2_migrations  # noqa: E402
from db_runtime import split_sql  # noqa: E402

MIGRATION_NAME = "057_daily_chain_v2_retire_chart_quote_routes.mysql.sql"
# 057 停泊喺 ".pending"：兩個 apply glob（daily_chain_v2_contract 嘅
# "0[5-9][0-9]_daily_chain_v2_*.mysql.sql" 同 db_runtime.migrate 嘅
# "*.mysql.sql"）都要求檔名以 ".mysql.sql" 結尾，所以停泊 = 唔會 apply。
# 落地就係 drop 走 ".pending" 一個 rename，而嗰個 rename 會即刻令下面兩條
# check 紅 —— 逼你喺同一個 commit 交代 go/no-go 已經返 0。
PARKED_NAME = "057_daily_chain_v2_retire_chart_quote_routes.mysql.sql.pending"
MIGRATION_PATH = ROOT / "pipelines" / "migrations" / PARKED_NAME
MIGRATIONS_DIR = ROOT / "pipelines" / "migrations"

FAILED: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    if ok:
        print(f"ok   {label}")
    else:
        FAILED.append(label)
        print(f"FAIL {label}{': ' + detail if detail else ''}")


def _compact(statement: str) -> str:
    return " ".join(statement.split()).replace("' ", "'").replace(" '", "'")


def audit_057(sql_text: str) -> list[str]:
    """Return the invariant violations in a 057-shaped migration body."""

    problems: list[str] = []
    statements = [_compact(item) for item in split_sql(sql_text)]
    if not statements:
        return ["migration has no statements at all"]

    retired = sorted(
        set(all_quote_storage_source_codes()) - set(mintable_quote_storage_source_codes())
    )
    sale_lanes = sorted(mintable_quote_storage_source_codes())

    saw_eligibility_retire = False
    saw_v1_deactivate = False
    saw_schema_version = False
    for statement in statements:
        upper = statement.upper()
        if any(word in upper for word in ("DELETE ", "DROP ", "TRUNCATE ")):
            problems.append(f"destructive statement: {statement[:80]}")
        for lane in sale_lanes:
            if lane in statement:
                problems.append(f"sale lane {lane!r} must not appear in 057: {statement[:80]}")
        if "CARDZ-ROUTE-V2" in upper and "IS_ACTIVE" in upper.replace(" ", ""):
            problems.append(
                f"v2 rows must never be deactivated (sync_source_registry re-seeds them"
                f" as eligible): {statement[:80]}"
            )
        if "IS_ELIGIBLE=0" in upper.replace(" ", ""):
            saw_eligibility_retire = True
            if "policy_version='cardz-route-v2'" not in statement:
                problems.append(f"is_eligible=0 is not scoped to cardz-route-v2: {statement[:80]}")
            for code in retired:
                if f"'{code}'" not in statement:
                    problems.append(f"retired chart lane {code!r} is not in the is_eligible=0 scope")
        if "IS_ACTIVE=0" in upper.replace(" ", ""):
            saw_v1_deactivate = True
            if "policy_version='cardz-route-v1'" not in statement:
                problems.append(f"is_active=0 is not scoped to cardz-route-v1: {statement[:80]}")
        if "CARDZ_SCHEMA_VERSION" in upper:
            saw_schema_version = True
            if "INSERT IGNORE" not in upper or "'057'" not in statement:
                problems.append(f"schema version row is not an INSERT IGNORE of '057': {statement[:80]}")

    if not saw_eligibility_retire:
        problems.append("no is_eligible=0 statement: the chart lanes are not retired at all")
    if not saw_v1_deactivate:
        problems.append("no is_active=0 statement: cardz-route-v1 still wins its own tiebreak")
    if not saw_schema_version:
        problems.append("no cardz_schema_version row: the DB cannot report 057 as applied")
    return problems


def main() -> int:
    # 閘唔准再係一段文字。go/no-go 今日仲係 1604（見檔頭），所以 057 唔准
    # 出現喺任何一個 apply glob 入面；一 glob 到就係下一 tick apply。呢兩條
    # 行喺「停泊檔案存唔存在」之前，咁 un-park（即係落地）先會報返個真正
    # 死因，而唔係得句「搵唔到 .pending 檔」。
    check("057 唔會被 chain 嘅 migrate stage 揀到", MIGRATION_NAME not in list_v2_migrations(ROOT))
    check(
        "057 唔會被 db_runtime.migrate 嘅 *.mysql.sql 揀到",
        MIGRATION_NAME not in {path.name for path in MIGRATIONS_DIR.glob("*.mysql.sql")},
    )

    # 落地係「一個 rename」而唔係「抄多份」：停泊名剝走 ".pending" 之後
    # 一定要 exactly 係真 migration 名。
    check(
        "停泊名剝走 .pending 就係真 migration 名",
        PARKED_NAME == f"{MIGRATION_NAME}.pending",
        PARKED_NAME,
    )

    check("057 停泊檔案喺 migrations 度", MIGRATION_PATH.is_file(), str(MIGRATION_PATH))
    if not MIGRATION_PATH.is_file():
        print(f"FAILED {len(FAILED)}: {FAILED}")
        return 1
    body = MIGRATION_PATH.read_text(encoding="utf-8")

    problems = audit_057(body)
    check("057 三條不變式全部成立", not problems, "; ".join(problems[:4]))

    # 反假綠 1：判別器對種落去嘅毒真係會紅。呢三個 mutation 就係「唔小心會做錯」
    # 嗰三種寫法。
    poisons = {
        "v2 行俾人 deactivate": body.replace(
            "SET is_eligible=0\nWHERE policy_version='cardz-route-v2'",
            "SET is_active=0\nWHERE policy_version='cardz-route-v2'",
        ),
        "v1 deactivate 走去打 v2": body.replace(
            "SET is_active=0\nWHERE policy_version='cardz-route-v1'",
            "SET is_active=0\nWHERE policy_version='cardz-route-v2'",
        ),
        "成交 lane 被拖落去一齊退役": body.replace(
            "AND source_code IN ('pricecharting','snkrdunk','snk','snk_psa10')",
            "AND source_code IN ('pricecharting','snkrdunk','snk','snk_psa10','pricecharting_sales')",
        ),
    }
    for label, poisoned in poisons.items():
        check(f"poison「{label}」真係種到（唔係 replace 落空）", poisoned != body)
        check(f"poison「{label}」會紅", bool(audit_057(poisoned)))

    # 反假綠 2：判別器唔係「見咩都紅」。原文已經綠咗（上面），呢度再確認
    # splitter 真係切到嘢，唔係得個空 list 令每個迴圈都跳過。
    check("split_sql 真係切到 057 嘅 statement", len(split_sql(body)) == 3, str(len(split_sql(body))))

    print()
    if FAILED:
        print(f"FAILED {len(FAILED)}: {FAILED}")
        return 1
    print("057 chart route 退役契約成立")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
