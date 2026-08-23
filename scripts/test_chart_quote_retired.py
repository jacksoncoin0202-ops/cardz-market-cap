#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""成交價 lane 落地之後，chart quote 唔可以再返嚟（DB gate；--no-db 會跳過）。

Owner 2026-08-23：出街嘅 PSA10 價 = 最新一單真成交。之前全board 1,604/1,604
都係 chart 值 —— PriceCharting `manualonly.last` 月線頭，同 SNKRDUNK K 線頭棒。
三個 minting 位已經刪咗，但「刪咗 call site」唔等於「唔會再有人加返」：
所以真正嘅閘係 current_quote_revision.assert_quote_mint_allowed，呢個檔就係
證明佢真係會 fire、而且係對住真 DB cursor fire，唔係得個 unit fixture。

呢度刻意有兩種 check：
  * 而家即刻會紅嘅（mint 閘、mintable 集合、payload 契約）
  * CUTOVER 之後先會紅嘅（唔准再有新 chart quote 出世）
第二種本身喺 CUTOVER 之前必然綠，所以配一個「查詢形狀真係搵到嘢」嘅
反假綠 check：DB 入面而家一定仲有舊 chart quote，如果連舊嘅都搵唔到，
即係 source_code 打錯字，成個 gate 係假嘅。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

# 新 code 第一次跑日更之後嘅 UTC 日界。之前嘅 chart quote 係歷史，唔郁。
CUTOVER_UTC = "2026-08-24 00:00:00"

FAILED: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    if ok:
        print(f"ok   {label}")
    else:
        FAILED.append(label)
        print(f"FAIL {label}{': ' + detail if detail else ''}")


def main() -> int:
    import current_quote_revision as CQ  # noqa: E402
    from rebuild_036 import DAILY_CREDENTIALS_ENV, connect  # noqa: E402

    mintable = set(CQ.mintable_quote_storage_source_codes())
    retired = [
        code for code in CQ.all_quote_storage_source_codes() if code not in mintable
    ]
    check("成交 lane 唔係空集（空集 = 全board 冇價）",
          mintable == {"pricecharting_sales", "snkrdunk_sales"}, str(mintable))
    check("chart lane 仲係註冊住（歷史 revision 要認得返佢哋）",
          set(retired) == {"pricecharting", "snkrdunk", "snk", "snk_psa10"}, str(retired))

    conn = connect(DAILY_CREDENTIALS_ENV)
    cur = conn.cursor()
    try:
        cur.execute("SET SESSION max_execution_time=60000")

        # --- 而家就會紅：真 cursor 落去 mint chart quote 一定要炸 -------------
        # 閘壞咗嘅話呢度會真係寫一行，所以成段包住 rollback，唔 commit。
        for code in retired:
            try:
                CQ.insert_quote_revision(
                    cur,
                    variant_id=1,
                    source_code=code,
                    source_external_entity_id="1",
                    price_usd="1.000000",
                    source_period_at="2026-08-23",
                    checked_at="2026-08-23 00:00:00",
                    payload_sha256="a" * 64,
                    purpose=CQ.QUOTE_MINT_PURPOSE_LIVE,
                )
            except ValueError as error:
                check(f"chart lane {code} 用真 cursor 都 mint 唔到",
                      "retired" in str(error), str(error))
            else:
                check(f"chart lane {code} 用真 cursor 都 mint 唔到", False,
                      "insert_quote_revision 冇炸，chart quote 又可以出街")

        # --- 反假綠：查詢形狀真係搵到舊 chart quote ---------------------------
        marks = ",".join(["%s"] * len(retired))
        cur.execute(
            f"""
            SELECT COUNT(*) AS n
            FROM market_current_quote_revision
            WHERE source_code IN ({marks})
            """,
            tuple(retired),
        )
        historical = int((cur.fetchone() or {}).get("n") or 0)
        check("查詢真係命中到歷史 chart quote（唔係 source_code 打錯字）",
              historical > 0, f"歷史 chart quote 行數 = {historical}")

        # --- CUTOVER 之後：唔准再有新 chart quote --------------------------
        cur.execute(
            f"""
            SELECT source_code, COUNT(*) AS n, MAX(created_at) AS newest
            FROM market_current_quote_revision
            WHERE source_code IN ({marks})
              AND created_at >= %s
              AND (reconstruction_kind IS NULL OR reconstruction_kind='')
            GROUP BY source_code
            """,
            (*retired, CUTOVER_UTC),
        )
        offenders = [dict(row) for row in cur.fetchall()]
        check(f"{CUTOVER_UTC} 之後冇新 chart quote 出世",
              not offenders, str(offenders[:5]))

        # --- 成交 quote 嘅 payload 契約：evidence 一定要跟得返 ---------------
        sale_marks = ",".join(["%s"] * len(mintable))
        cur.execute(
            f"""
            SELECT COUNT(*) AS n
            FROM market_current_quote_revision q
            LEFT JOIN market_source_observation so ON so.id=q.source_observation_id
            WHERE q.source_code IN ({sale_marks})
              AND (so.id IS NULL
                   OR so.observation_kind<>%s
                   OR so.payload_sha256<>q.payload_sha256)
            """,
            (*sorted(mintable), CQ.SALE_QUOTE_OBSERVATION_KIND),
        )
        orphans = int((cur.fetchone() or {}).get("n") or 0)
        check("每一個成交 quote 都指得返自己嗰行 evidence",
              orphans == 0, f"斷線 quote 行數 = {orphans}")
    finally:
        # 呢個 test 唔准留低任何寫入。
        conn.rollback()
        conn.close()

    print()
    if FAILED:
        print(f"FAILED {len(FAILED)}: {FAILED}")
        return 1
    print("chart quote 退役契約全部成立")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
