#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""價格行 lane 契約（DB gate；--no-db 會跳過）。

2026-08-12 事故（runbook 缺陷形狀清單）：一批 ad-hoc 補數 lane 將掛價/捏造日寫入
market_price_observation，同埋錯綁 identity 令兩個 source 家族嘅價互相交錯。
清毒係一次性，呢個測試係長期閘：

  1. FREEZE_UTC 之後開始嘅 run，唔准再產生任何非 canonical lane 嘅 ready
     SNK-family 價格行。canonical = snk_kline_ingest_* / rebuild036_snk_kline*。
     （歷史 keep 行唔郁 —— 佢哋喺 FREEZE 之前。）
  2. 跨家族矛盾 monitor：180 日家族中位數比 >= 3x 嘅 variant，只可以係
     price_identity_conflict_audit 已裁決過嘅 both-strict 名單（機器唔准自己揀邊，
     人手裁決 backlog）。出現新矛盾 variant = 有毒源重新開波，即刻紅。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

# 落閘時刻：2026-08-12 清毒行動完成、audit 印落齊之後。
FREEZE_UTC = "2026-08-12 14:10:00"
CANONICAL_RUN_PREFIXES = ("snk_kline_ingest_", "rebuild036_snk_kline")
SNK_SOURCES = ("snkrdunk", "snk", "snk_psa10")

# price_identity_conflict_audit 20260812T134622Z 裁決：兩邊都 exact+strict，
# 機器唔准自己揀邊個啱，掛喺人手 backlog。佢哋繼續矛盾係已知狀態，唔算新事故。
KNOWN_BOTH_STRICT_CONFLICTS = {653, 658, 666, 789}

FAILED: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    if ok:
        print(f"ok   {label}")
    else:
        FAILED.append(label)
        print(f"FAIL {label}{': ' + detail if detail else ''}")


def main() -> int:
    from rebuild_036 import connect, DAILY_CREDENTIALS_ENV  # noqa: E402

    conn = connect(DAILY_CREDENTIALS_ENV)
    cur = conn.cursor()

    # 1. freeze 之後嘅非 canonical SNK ready 行
    cur.execute(
        f"""
        SELECT r.run_key, COUNT(*) n
        FROM market_price_observation p
        INNER JOIN market_ingest_run r ON r.id = p.run_id
        WHERE p.source_code IN {SNK_SOURCES!r}
          AND p.metric_status = 'ready'
          AND r.started_at > %s
          AND r.run_key NOT LIKE '{CANONICAL_RUN_PREFIXES[0]}%%'
          AND r.run_key NOT LIKE '{CANONICAL_RUN_PREFIXES[1]}%%'
        GROUP BY r.run_key
        """,
        (FREEZE_UTC,),
    )
    offenders = [dict(r) for r in cur.fetchall()]
    check(
        "no new non-canonical snk price lanes after freeze",
        not offenders,
        f"offending run_keys: {offenders[:5]}",
    )

    # 2. 跨家族矛盾 monitor（重用 audit 嘅偵測器 —— 一個概念一份實現）
    from price_identity_conflict_audit import READY_ROWS_SQL, find_conflicts  # noqa: E402

    cur.execute("SELECT CURDATE() d")
    today = cur.fetchone()["d"]
    cur.execute(READY_ROWS_SQL)
    rows = [dict(r) for r in cur.fetchall()]
    conflicts = find_conflicts(rows, today)
    unexpected = sorted(set(conflicts) - KNOWN_BOTH_STRICT_CONFLICTS)
    check(
        "no unadjudicated cross-family conflicts",
        not unexpected,
        f"new conflict variants: {[(v, conflicts[v]['ratio']) for v in unexpected[:8]]}",
    )

    conn.close()
    print()
    if FAILED:
        print(f"FAILED {len(FAILED)}: {FAILED}")
        return 1
    print("all price lane contracts hold")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
