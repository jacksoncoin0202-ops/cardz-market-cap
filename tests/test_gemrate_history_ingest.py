import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from gemrate_history_ingest import extract_psa_history


def test_extracts_only_psa_history_from_real_shape():
    document = {
        "data": {
            "universal_gemrate_id": "a" * 40,
            "population": {
                "population_data": {
                    "by_grader": {
                        "psa": {"history": [{"date": "2026-01-01", "total": 12, "grades": {"psa_10": 7}}]},
                        "cgc": {"history": [{"date": "2026-01-01", "total": 99}]},
                    }
                }
            },
        }
    }
    gemrate_id, rows = extract_psa_history(document)
    assert gemrate_id == "a" * 40
    assert rows == [{"date": "2026-01-01", "total": 12, "grades": {"psa_10": 7}}]


def test_history_migration_keeps_unmatched_identity_nullable():
    sql = (ROOT / "pipelines" / "migrations" / "014_gemrate_psa10_history.mysql.sql").read_text(
        encoding="utf-8"
    )
    assert "variant_id BIGINT UNSIGNED NULL" in sql
    assert "PRIMARY KEY (gemrate_id, observed_date)" in sql
