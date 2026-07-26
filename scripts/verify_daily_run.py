#!/usr/bin/env python3
"""Post-run outcome gate for the daily pipeline.

The daily chain can exit 0 without producing any new data (collector replay,
INSERT IGNORE no-op, stale effective_date).  This script checks the outcome,
not the process: did today's prices, source coverage, and index snapshots
actually land?  Any hard failure writes an alert file under
data/runtime/alerts/ and exits non-zero so Task Scheduler LastTaskResult
reflects the true result.

Exit codes: 0 = all checks pass, 1 = data checks failed, 2 = could not verify
(DB unreachable / config missing).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "data" / "runtime" / "config" / "backend.env"
ALERTS_DIR = ROOT / "data" / "runtime" / "alerts"
# 逐源逐日行數嘅流水賬。存在嘅理由：單日「今日 vs 尋日」睇唔到「一連七日
# 每日縮 5%」——每日都過閘，一個禮拜之後冇聲冇氣少咗三成。
VOLUME_LEDGER_PATH = ROOT / "data" / "runtime" / "verify" / "source_volume.json"
VOLUME_LEDGER_KEEP_DAYS = 30

# 通報層。systemd 嘅 OnFailure= 只捉到 process 層面嘅死法；「chain exit 0 但數據
# 唔啱」呢種只有呢個 gate 自己知，所以要喺 process 入面通報一次。
sys.path.insert(0, str(Path(__file__).resolve().parent))
from notify_alert import clear as clear_notify_state, notify_failure  # noqa: E402

INDEX_CODES = ("tcg-combined", "pokemon", "one-piece")
INDEX_VERSION = "psa10-v3-complete"
# gemrate must land every day; SNK price rows may arrive under either code.
REQUIRED_SOURCES = ("gemrate",)
ANY_OF_SOURCES = ("snk_psa10", "snkrdunk")
CONSTITUENT_DROP_WARN_PCT = 20.0
# 今日某個源嘅行數低過尋日呢個比例就 fail。source_coverage 只答「有冇」，
# 一個尋日 3779 行、今日 12 行嘅 gemrate 喺佢眼中係完美嘅一日。
VOLUME_FLOOR_RATIO = 0.9
# 尋日得幾行嘅源，波動一兩行就會跌穿 90%。呢個底線以下唔當佢係訊號。
VOLUME_FLOOR_MIN_BASELINE = 10


def read_env_file(path: Path) -> dict:
    values = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def db_config() -> dict:
    import os

    file_values = read_env_file(CONFIG_PATH)

    def pick(key: str, default: str = "") -> str:
        return os.environ.get(key) or file_values.get(key) or default

    config = {
        "host": pick("CARDZ_DB_HOST", "127.0.0.1"),
        "port": int(pick("CARDZ_DB_PORT", "3308")),
        "database": pick("CARDZ_DB_NAME", "cardz_market_cap"),
        "user": pick("CARDZ_DB_USER", "cardz"),
        "password": pick("CARDZ_DB_PASSWORD"),
    }
    if not config["password"]:
        raise RuntimeError("CARDZ_DB_PASSWORD is not set (env or backend.env)")
    return config


def default_expected_date(now: datetime | None = None) -> str:
    """排程跑嗰陣，上游最新可用嘅日線係邊一日。

    唔可以用「UTC 今日」做基準。SNKRDUNK 嘅日線要 JST 午夜（= UTC 15:00）
    先埋單，而 daily timer 跑喺 09:30 本地（UTC 01:30）—— run 當日嗰條線
    根本仲未存在。實測抓取時刻對最新觀察日嘅落差：UTC 10:26 抓 → 落差 0 日
    （JST 19:26，已埋單）；UTC 06:28 抓 → 落差 1 日（JST 15:28，未埋單）。
    即係話舊基準喺正常排程時段下**每日必 fail**，個閘由頭到尾冇區分過
    「真斷更」同「數據仲未出」。

    T-1 基準仍然捉到真斷更：07-24 → 07-26 斷咗兩日，07-26 跑見到 max=07-23
    < 07-25，照 fail。至於「run 完全冇動作但 T-1 數據仲喺度」呢個新盲點，
    由 `ingest_activity` 另外量，唔靠日期閘兼職。
    """

    now = now or datetime.now(timezone.utc)
    return (now.date() - timedelta(days=1)).isoformat()


def previous_day(date_str: str) -> str | None:
    try:
        return (datetime.fromisoformat(date_str).date() - timedelta(days=1)).isoformat()
    except ValueError:
        return None


def _volume_floor_check(
    expected_date: str,
    source_counts: dict,
    previous_source_counts: dict | None,
) -> dict:
    """逐個源比較今日同尋日嘅行數。量唔到基準 = fail，唔係 pass。"""
    baseline_date = previous_day(expected_date) or "unknown"

    if not previous_source_counts:
        return {
            "check": "volume_floor",
            "pass": False,
            "detail": f"no baseline rows on {baseline_date}: cannot measure volume, "
                      f"failing closed (an unmeasurable gate must not report pass)",
        }

    floor_pct = int(VOLUME_FLOOR_RATIO * 100)
    shortfalls = []
    for source, baseline in sorted(previous_source_counts.items()):
        if baseline < VOLUME_FLOOR_MIN_BASELINE:
            continue
        current = int(source_counts.get(source, 0))
        if current < baseline * VOLUME_FLOOR_RATIO:
            shortfalls.append(f"{source} {baseline}->{current} ({current / baseline * 100:.0f}%)")

    return {
        "check": "volume_floor",
        "pass": not shortfalls,
        "detail": (
            f"every source held >={floor_pct}% of its {baseline_date} row count "
            f"(baseline {previous_source_counts})"
            if not shortfalls
            else f"below {floor_pct}% of {baseline_date}: {'; '.join(shortfalls)}"
        ),
    }


def record_source_volume(expected_date: str, source_counts: dict) -> Path | None:
    """Append today's per-source row counts to the rolling ledger.

    Telemetry, not a gate: a failure to write it must never change the verdict.
    """
    try:
        ledger = {}
        if VOLUME_LEDGER_PATH.exists():
            loaded = json.loads(VOLUME_LEDGER_PATH.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                ledger = loaded
        ledger[expected_date] = {k: int(v) for k, v in sorted(source_counts.items())}
        for stale in sorted(ledger)[:-VOLUME_LEDGER_KEEP_DAYS]:
            del ledger[stale]
        VOLUME_LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
        VOLUME_LEDGER_PATH.write_text(
            json.dumps(ledger, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
        return VOLUME_LEDGER_PATH
    except Exception as error:
        print(f"[verify] volume ledger not written: {type(error).__name__}: {error}")
        return None


def evaluate_checks(
    expected_date: str,
    price_max_date: str | None,
    snapshot_dates: dict,
    source_counts: dict,
    latest_constituents: tuple | None,
    today_rows: int | None = None,
    previous_source_counts: dict | None = None,
) -> tuple[list, bool]:
    """Pure check logic; returns (checks, ok). Hard failures set ok=False."""
    checks = []
    ok = True

    fresh = bool(price_max_date and price_max_date >= expected_date)
    checks.append({
        "check": "price_freshness",
        "pass": fresh,
        "detail": f"max(observed_date)={price_max_date or 'none'} expected>={expected_date}",
    })
    ok = ok and fresh

    missing = [
        code for code in INDEX_CODES
        if not (snapshot_dates.get(code) and snapshot_dates[code] >= expected_date)
    ]
    checks.append({
        "check": "snapshot_freshness",
        "pass": not missing,
        "detail": (
            f"all {len(INDEX_CODES)} indexes have effective_date>={expected_date}"
            if not missing
            else f"stale/missing: {missing} (latest per index: {snapshot_dates or 'none'})"
        ),
    })
    ok = ok and not missing

    required_missing = [s for s in REQUIRED_SOURCES if not source_counts.get(s)]
    any_of_ok = any(source_counts.get(s) for s in ANY_OF_SOURCES)
    coverage_ok = not required_missing and any_of_ok
    checks.append({
        "check": "source_coverage",
        "pass": coverage_ok,
        "detail": f"counts on {expected_date}: {source_counts or 'none'}; "
                  f"require {list(REQUIRED_SOURCES)} and any of {list(ANY_OF_SOURCES)}",
    })
    ok = ok and coverage_ok

    # 日期閘放寬到 T-1 之後，「run 行咗但一行都冇寫」就會靜靜地 pass ——
    # 尋日嘅數據頂得住今日嘅檢查。所以直接量今日有冇動作，同觀察日無關。
    if today_rows is not None:
        active = today_rows > 0
        checks.append({
            "check": "ingest_activity",
            "pass": active,
            "detail": f"source observations written today (UTC): {today_rows}",
        })
        ok = ok and active

    # 到呢一步為止，上面全部檢查答嘅都係「有冇」，冇一個答「有幾多」。
    # 一個尋日寫 3779 行、今日寫 12 行嘅 gemrate，price_freshness 過、
    # snapshot_freshness 過、source_coverage 過、ingest_activity 過 ——
    # 全綠，然後靜靜地少咗 99.7% 嘅數據。呢個閘專門量幅度。
    #
    # 呢個 check 一定會出現喺 checks 入面，唔會好似 constituent_sanity 咁
    # 「量唔到就唔出現」。量唔到 = fail，唔係 pass：一個唔存在嘅檢查同一個
    # 通過咗嘅檢查，喺 summary 入面睇落一模一樣。
    checks.append(_volume_floor_check(expected_date, source_counts, previous_source_counts))
    ok = ok and checks[-1]["pass"]

    if latest_constituents and latest_constituents[1]:
        current, previous = latest_constituents
        drop_pct = (previous - current) / previous * 100.0
        warn = drop_pct > CONSTITUENT_DROP_WARN_PCT
        checks.append({
            "check": "constituent_sanity",
            "pass": True,
            "warning": warn,
            "detail": f"tcg-combined constituents {previous} -> {current} ({drop_pct:+.1f}% drop)"
                      if warn else f"tcg-combined constituents {previous} -> {current}",
        })

    return checks, ok


def collect_facts(connection, expected_date: str):
    with connection.cursor() as cursor:
        cursor.execute("SELECT MAX(observed_date) AS d FROM market_price_observation")
        row = cursor.fetchone()
        price_max_date = str(row["d"]) if row and row["d"] else None

        cursor.execute(
            """
            SELECT index_code, MAX(effective_date) AS d
            FROM market_index_snapshot
            WHERE index_version = %s
            GROUP BY index_code
            """,
            (INDEX_VERSION,),
        )
        snapshot_dates = {r["index_code"]: str(r["d"]) for r in cursor.fetchall()}

        def counts_on(day: str | None) -> dict:
            if not day:
                return {}
            cursor.execute(
                """
                SELECT source_code, COUNT(*) AS n
                FROM market_source_observation
                WHERE observed_date = %s
                GROUP BY source_code
                """,
                (day,),
            )
            return {r["source_code"]: int(r["n"]) for r in cursor.fetchall()}

        source_counts = counts_on(expected_date)
        # volume_floor 嘅基準。同上面用同一條 query，唔會走音。
        previous_source_counts = counts_on(previous_day(expected_date))

        cursor.execute(
            """
            SELECT constituent_count
            FROM market_index_snapshot
            WHERE index_code = 'tcg-combined' AND index_version = %s
            ORDER BY effective_date DESC
            LIMIT 2
            """,
            (INDEX_VERSION,),
        )
        rows = cursor.fetchall()
        latest_constituents = (
            (int(rows[0]["constituent_count"]), int(rows[1]["constituent_count"]))
            if len(rows) == 2
            else None
        )

        cursor.execute(
            """
            SELECT COUNT(*) AS n FROM market_source_observation
            WHERE created_at >= %s
            """,
            (datetime.now(timezone.utc).date().isoformat(),),
        )
        row = cursor.fetchone()
        today_rows = int(row["n"]) if row else 0

    return (
        price_max_date,
        snapshot_dates,
        source_counts,
        latest_constituents,
        today_rows,
        previous_source_counts,
    )


def write_alert(expected_date: str, checks: list, reason: str, tag: str = "daily") -> Path:
    ALERTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = ALERTS_DIR / f"{tag}_verify_{expected_date}_{stamp}.json"
    path.write_text(
        json.dumps(
            {
                "alert": f"{tag}_verify_failed",
                "reason": reason,
                "tag": tag,
                "expectedDate": expected_date,
                "checkedAt": datetime.now(timezone.utc).isoformat(),
                "checks": checks,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--expected-date",
        default=None,
        help="Date (YYYY-MM-DD) the run must have produced; defaults to UTC yesterday, "
             "the newest daily bar upstream can have closed by the time the timer runs",
    )
    parser.add_argument(
        "--tag",
        default="daily",
        help="Caller label written into the alert filename/payload (daily | watchdog)",
    )
    parser.add_argument(
        "--no-alert",
        action="store_true",
        help="Report only; never write an alert file. For side-effect-free status checks.",
    )
    args = parser.parse_args()
    expected_date = args.expected_date or default_expected_date()
    tag = args.tag

    def emit_alert(checks: list, reason: str, exit_code: int) -> None:
        if args.no_alert:
            print("[verify] (--no-alert) alert suppressed")
            return
        print(f"[verify] alert written: {write_alert(expected_date, checks, reason, tag)}")
        outcome = notify_failure(
            tag=tag,
            expected_date=expected_date,
            exit_code=exit_code,
            checks=checks,
            reason=reason,
        )
        print(f"[verify] notify: {outcome['summary']}")

    try:
        import pymysql
        from pymysql.cursors import DictCursor

        config = db_config()
        connection = pymysql.connect(
            host=config["host"], port=config["port"], database=config["database"],
            user=config["user"], password=config["password"],
            charset="utf8mb4", cursorclass=DictCursor,
        )
    except Exception as error:  # infra failure: cannot verify at all
        message = f"verify infra failure: {type(error).__name__}: {error}"
        print(f"[verify] FAIL {message}")
        emit_alert([], message, 2)
        return 2

    try:
        facts = collect_facts(connection, expected_date)
    finally:
        connection.close()

    checks, ok = evaluate_checks(expected_date, *facts)

    # 流水賬同判決分開：即使今日 fail（尤其係今日 fail）都要留低逐源行數，
    # 否則事後翻查只剩「fail 咗」三個字，冇數字可以對。
    ledger_path = record_source_volume(expected_date, facts[2])
    if ledger_path:
        print(f"[verify] per-source rows on {expected_date}: {facts[2] or 'none'} -> {ledger_path}")

    for check in checks:
        status = "PASS" if check["pass"] else "FAIL"
        if check.get("warning"):
            status = "WARN"
        print(f"[verify] {status} {check['check']}: {check['detail']}")

    summary = {
        "expectedDate": expected_date,
        "result": "pass" if ok else "fail",
        "checks": checks,
    }
    print(json.dumps(summary, ensure_ascii=False))

    if not ok:
        emit_alert(checks, f"{tag} outcome checks failed", 1)
        return 1

    # 修好之後即刻忘記舊狀態，否則下次同款失敗會被 throttle 壓住，唔會出聲。
    if not args.no_alert and clear_notify_state(tag):
        print(f"[verify] notify: cleared throttle state for {tag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
