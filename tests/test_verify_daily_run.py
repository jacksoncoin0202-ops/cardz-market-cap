import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "verify_daily_run", ROOT / "scripts" / "verify_daily_run.py"
)
verify_daily_run = importlib.util.module_from_spec(spec)
sys.modules["verify_daily_run"] = verify_daily_run
spec.loader.exec_module(verify_daily_run)

default_expected_date = verify_daily_run.default_expected_date


def evaluate_checks(expected_date, price_max_date, snapshot_dates, source_counts, *args, **kwargs):
    """Wrapper that hands every legacy test a flat, healthy volume baseline.

    `volume_floor` fails closed when it gets no baseline, so without this every
    test below would fail on an axis it was never written to exercise. Passing
    yesterday == today keeps each test measuring exactly what it always did.
    The fail-closed behaviour itself is covered by the VolumeFloor tests, which
    call `verify_daily_run.evaluate_checks` directly.
    """
    kwargs.setdefault("previous_source_counts", dict(source_counts))
    return verify_daily_run.evaluate_checks(
        expected_date, price_max_date, snapshot_dates, source_counts, *args, **kwargs
    )

EXPECTED = "2026-07-26"
FRESH_SNAPSHOTS = {
    "tcg-combined": "2026-07-26",
    "pokemon": "2026-07-26",
    "one-piece": "2026-07-26",
}
FRESH_SOURCES = {"gemrate": 3779, "snk_psa10": 175, "snkrdunk": 142, "ebay": 66}


def test_all_fresh_passes():
    checks, ok = evaluate_checks(
        EXPECTED, "2026-07-26", FRESH_SNAPSHOTS, FRESH_SOURCES, (262, 262)
    )
    assert ok
    assert all(c["pass"] for c in checks)


def test_stale_prices_fail():
    checks, ok = evaluate_checks(
        EXPECTED, "2026-07-24", FRESH_SNAPSHOTS, FRESH_SOURCES, None
    )
    assert not ok
    assert not next(c for c in checks if c["check"] == "price_freshness")["pass"]


def test_missing_price_history_fails():
    _, ok = evaluate_checks(EXPECTED, None, FRESH_SNAPSHOTS, FRESH_SOURCES, None)
    assert not ok


def test_one_stale_index_fails():
    snapshots = dict(FRESH_SNAPSHOTS, **{"one-piece": "2026-07-24"})
    checks, ok = evaluate_checks(
        EXPECTED, "2026-07-26", snapshots, FRESH_SOURCES, None
    )
    assert not ok
    snapshot_check = next(c for c in checks if c["check"] == "snapshot_freshness")
    assert not snapshot_check["pass"]
    assert "one-piece" in snapshot_check["detail"]


def test_missing_index_fails():
    snapshots = {k: v for k, v in FRESH_SNAPSHOTS.items() if k != "pokemon"}
    _, ok = evaluate_checks(EXPECTED, "2026-07-26", snapshots, FRESH_SOURCES, None)
    assert not ok


def test_gemrate_only_fails_coverage():
    # the 07-25 failure mode: gemrate rows landed but zero SNK price rows
    sources = {"gemrate": 3779}
    checks, ok = evaluate_checks(
        EXPECTED, "2026-07-26", FRESH_SNAPSHOTS, sources, None
    )
    assert not ok
    assert not next(c for c in checks if c["check"] == "source_coverage")["pass"]


def test_snk_without_gemrate_fails_coverage():
    sources = {"snk_psa10": 175}
    _, ok = evaluate_checks(EXPECTED, "2026-07-26", FRESH_SNAPSHOTS, sources, None)
    assert not ok


def test_either_snk_code_satisfies_coverage():
    for snk_code in ("snk_psa10", "snkrdunk"):
        sources = {"gemrate": 100, snk_code: 5}
        _, ok = evaluate_checks(
            EXPECTED, "2026-07-26", FRESH_SNAPSHOTS, sources, None
        )
        assert ok


def test_constituent_drop_warns_but_passes():
    checks, ok = evaluate_checks(
        EXPECTED, "2026-07-26", FRESH_SNAPSHOTS, FRESH_SOURCES, (150, 262)
    )
    assert ok  # warning only, never a hard fail
    sanity = next(c for c in checks if c["check"] == "constituent_sanity")
    assert sanity["pass"] and sanity["warning"]


def test_small_constituent_change_no_warning():
    checks, _ = evaluate_checks(
        EXPECTED, "2026-07-26", FRESH_SNAPSHOTS, FRESH_SOURCES, (260, 262)
    )
    sanity = next(c for c in checks if c["check"] == "constituent_sanity")
    assert not sanity.get("warning")


def test_default_expected_date_is_utc_yesterday():
    """上游日線 JST 午夜先埋單，timer 喺 UTC 01:30 跑，run 當日嗰條線唔存在。"""
    from datetime import datetime, timezone

    assert default_expected_date(datetime(2026, 7, 26, 6, 28, tzinfo=timezone.utc)) == "2026-07-25"
    assert default_expected_date(datetime(2026, 1, 1, 1, 30, tzinfo=timezone.utc)) == "2025-12-31"


def test_t_minus_one_baseline_still_catches_real_outage():
    """07-24 → 07-26 斷咗兩日：T-1 基準照 fail，唔會因為放寬咗就盲。"""
    checks, ok = evaluate_checks(
        "2026-07-25", "2026-07-23",
        {"tcg-combined": "2026-07-23", "pokemon": "2026-07-23", "one-piece": "2026-07-23"},
        FRESH_SOURCES, None,
    )
    assert not ok
    assert not next(c for c in checks if c["check"] == "price_freshness")["pass"]
    assert not next(c for c in checks if c["check"] == "snapshot_freshness")["pass"]


def test_idle_run_fails_even_when_yesterday_data_present():
    """放寬到 T-1 之後嘅新盲點：run 冇寫過一行，尋日數據唔可以幫佢頂。"""
    checks, ok = evaluate_checks(
        "2026-07-25", "2026-07-25",
        {"tcg-combined": "2026-07-25", "pokemon": "2026-07-25", "one-piece": "2026-07-25"},
        FRESH_SOURCES, None, today_rows=0,
    )
    assert not ok
    assert not next(c for c in checks if c["check"] == "ingest_activity")["pass"]


def test_active_run_passes_ingest_activity():
    checks, ok = evaluate_checks(
        "2026-07-25", "2026-07-25",
        {"tcg-combined": "2026-07-25", "pokemon": "2026-07-25", "one-piece": "2026-07-25"},
        FRESH_SOURCES, (255, 262), today_rows=117,
    )
    assert ok
    assert next(c for c in checks if c["check"] == "ingest_activity")["pass"]


def test_ingest_activity_absent_when_not_measured():
    """舊 caller 冇傳 today_rows 就唔應該憑空多咗個 check。"""
    checks, _ = evaluate_checks(EXPECTED, "2026-07-26", FRESH_SNAPSHOTS, FRESH_SOURCES, None)
    assert not any(c["check"] == "ingest_activity" for c in checks)


# --- volume_floor：靜靜地少咗嘢 -----------------------------------------
# 上面全部 check 答嘅都係「有冇」。一個尋日 3779 行、今日 12 行嘅 gemrate
# 喺佢哋眼中係完美嘅一日 —— price 新、snapshot 新、coverage 有、今日有動作。
# 呢組測試釘住嗰個量度幅度嘅閘。

raw_evaluate = verify_daily_run.evaluate_checks
BASELINE = {"gemrate": 3779, "snk_psa10": 175, "snkrdunk": 142, "ebay": 66}


def _volume_case(today_counts, baseline=BASELINE):
    return raw_evaluate(
        EXPECTED, "2026-07-26", FRESH_SNAPSHOTS, today_counts, None,
        previous_source_counts=baseline,
    )


def test_collapsed_source_fails_even_though_every_other_check_is_green():
    """07-26 想像中嘅失敗：gemrate 由 3779 行跌到 12 行，其餘全綠。"""
    checks, ok = _volume_case({"gemrate": 12, "snk_psa10": 175, "snkrdunk": 142, "ebay": 66})
    assert not ok
    assert next(c for c in checks if c["check"] == "source_coverage")["pass"]
    volume = next(c for c in checks if c["check"] == "volume_floor")
    assert not volume["pass"]
    assert "gemrate" in volume["detail"]


def test_source_that_vanished_entirely_is_caught():
    """ebay 唔喺 REQUIRED_SOURCES / ANY_OF_SOURCES 入面，冇晒都唔會有人嘈。"""
    checks, ok = _volume_case({"gemrate": 3779, "snk_psa10": 175, "snkrdunk": 142})
    assert not ok
    assert "ebay" in next(c for c in checks if c["check"] == "volume_floor")["detail"]


def test_normal_daily_wobble_passes():
    checks, ok = _volume_case({"gemrate": 3612, "snk_psa10": 168, "snkrdunk": 160, "ebay": 70})
    assert ok
    assert next(c for c in checks if c["check"] == "volume_floor")["pass"]


def test_exactly_at_the_floor_passes():
    checks, ok = _volume_case({"gemrate": 3402, "snk_psa10": 175, "snkrdunk": 142, "ebay": 66})
    assert ok, next(c for c in checks if c["check"] == "volume_floor")["detail"]


def test_missing_baseline_fails_closed():
    """量唔到 = fail。一個唔存在嘅檢查同一個通過咗嘅檢查，喺 summary 睇落一樣。"""
    for empty in ({}, None):
        checks, ok = _volume_case(FRESH_SOURCES, baseline=empty)
        assert not ok
        volume = next(c for c in checks if c["check"] == "volume_floor")
        assert not volume["pass"]
        assert "cannot measure" in volume["detail"]


def test_volume_floor_always_appears():
    """唔准好似 constituent_sanity 咁「量唔到就唔出現」。"""
    for baseline in (None, {}, BASELINE):
        checks, _ = _volume_case(FRESH_SOURCES, baseline=baseline)
        assert any(c["check"] == "volume_floor" for c in checks)


def test_tiny_baseline_is_not_treated_as_signal():
    """尋日得 3 行嘅源，今日 1 行係噪音唔係斷更。"""
    _, ok = _volume_case({"gemrate": 3779, "snk_psa10": 175, "experimental": 1},
                         baseline={"gemrate": 3779, "snk_psa10": 175, "experimental": 3})
    assert ok


def test_previous_day_handles_month_boundary():
    assert verify_daily_run.previous_day("2026-03-01") == "2026-02-28"
    assert verify_daily_run.previous_day("2026-01-01") == "2025-12-31"
    assert verify_daily_run.previous_day("not-a-date") is None


def test_ledger_keeps_a_bounded_window_and_never_raises(tmp_path, monkeypatch):
    ledger = tmp_path / "verify" / "source_volume.json"
    monkeypatch.setattr(verify_daily_run, "VOLUME_LEDGER_PATH", ledger)
    monkeypatch.setattr(verify_daily_run, "VOLUME_LEDGER_KEEP_DAYS", 3)
    import json as _json

    for day in range(1, 7):
        verify_daily_run.record_source_volume(f"2026-07-0{day}", {"gemrate": day * 100})
    stored = _json.loads(ledger.read_text(encoding="utf-8"))
    assert sorted(stored) == ["2026-07-04", "2026-07-05", "2026-07-06"]
    assert stored["2026-07-06"] == {"gemrate": 600}

    # 流水賬係遙測，唔係閘：寫唔到都唔可以拋。
    monkeypatch.setattr(verify_daily_run, "VOLUME_LEDGER_PATH", tmp_path / "verify")
    assert verify_daily_run.record_source_volume("2026-07-07", {"gemrate": 1}) is None
