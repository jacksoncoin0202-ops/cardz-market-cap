from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_windows_watchlist_task_runs_complete_scan_and_db_sync() -> None:
    script = (
        ROOT / "deploy/windows/run-cardz-gemrate-psa10-watchlist.ps1"
    ).read_text(encoding="utf-8")
    assert "--all-sets --sync-db" in script
    assert "gemrate_brute_harvest.py" in script
