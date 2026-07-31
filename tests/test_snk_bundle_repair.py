import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from snk_bundle_repair import load_trade_groups


def test_repair_groups_bundle_day_and_keeps_single_card_comparables(tmp_path: Path) -> None:
    run = tmp_path / "sources_x"
    run.mkdir()
    row = {
        "item_id": 12,
        "recent_trades": [
            {"soldAt": "2026-07-01T01:00:00Z", "price": 10_000, "label": "1枚"},
            {"soldAt": "2026-07-01T02:00:00Z", "price": 36_000, "label": "3枚"},
            {"soldAt": "2026-07-02T01:00:00Z", "price": 11_000, "label": "1枚"},
        ],
    }
    (run / "snk-psa10.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    groups = load_trade_groups(tmp_path)
    assert list(groups) == [(12, "2026-07-01")]
    assert len(groups[(12, "2026-07-01")]) == 2
