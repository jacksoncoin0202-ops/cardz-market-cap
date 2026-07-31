from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

from pipelines import image_source_family_quarantine as mod


def _sha(index: int) -> str:
    return f"{index:064x}"


def _family_row(index: int, *, public: int = 1) -> dict[str, Any]:
    return {
        "asset_id": 1000 + index,
        "variant_id": index,
        "content_sha256": _sha(index),
        "source_version_sha256": _sha(5000 + index),
        "width_px": 429,
        "height_px": 600,
        "captured_at": "2026-07-31T00:00:00Z",
        "source_path": (
            "https://limitlesstcg.nyc3.cdn.digitaloceanspaces.com/"
            f"one-piece/OP01/renamed-{index}.png?cache=1"
        ),
        "pointer_public_allowed": public,
    }


def _replacement_row(index: int) -> dict[str, Any]:
    row = _family_row(index)
    row["asset_id"] = 9000 + index
    row["source_path"] = f"https://cdn.snkrdunk.com/cards/{index}.png"
    return row


def test_plan_requires_exact_320_split_146_plus_174_and_ignores_replacement() -> None:
    rows = [_family_row(index) for index in range(1, 321)]
    rows.append(_replacement_row(999))
    plan = mod.build_plan(rows, set(range(1, 147)))
    assert plan["matched"] == 320
    assert plan["releaseCohortOverlap"] == 146
    assert plan["outerNonRelease"] == 174
    assert plan["publicPointersBefore"] == 320
    assert all(row["family"] == mod.FAMILY for row in plan["candidates"])
    assert all("sourcePath" in row for row in plan["candidates"])


@pytest.mark.parametrize("count", [319, 321])
def test_plan_fails_closed_unless_family_count_is_exact(count: int) -> None:
    with pytest.raises(mod.QuarantineError, match="family_count_mismatch"):
        mod.build_plan(
            [_family_row(index) for index in range(1, count + 1)],
            set(range(1, 147)),
        )


def test_plan_rejects_duplicate_identity_inside_exact_count() -> None:
    rows = [_family_row(index) for index in range(1, 321)]
    rows[-1]["variant_id"] = rows[0]["variant_id"]
    with pytest.raises(
        mod.QuarantineError,
        match="family_candidate_identity_not_unique",
    ):
        mod.build_plan(rows, set(range(1, 147)))


def test_plan_allows_duplicate_polluted_bytes_across_distinct_assets() -> None:
    rows = [_family_row(index) for index in range(1, 321)]
    rows[-1]["content_sha256"] = rows[0]["content_sha256"]
    plan = mod.build_plan(rows, set(range(1, 147)))
    assert plan["matched"] == 320
    assert plan["candidates"][0]["contentSha256"] == plan["candidates"][-1][
        "contentSha256"
    ]


def test_plan_fails_when_frozen_release_cohort_is_not_fully_present() -> None:
    rows = [_family_row(index) for index in range(1, 321)]
    rows[0]["variant_id"] = 999
    with pytest.raises(mod.QuarantineError, match="release_cohort_overlap_mismatch"):
        mod.build_plan(rows, set(range(1, 147)))


class _Connection:
    def __init__(self, candidate: dict[str, Any]) -> None:
        self.candidate = candidate
        self.qc: dict[str, Any] | None = None
        self.pointer_public_allowed = candidate["pointerPublicAllowed"]
        self.commands: list[tuple[str, Any]] = []

    def cursor(self) -> "_Cursor":
        return _Cursor(self)


class _Cursor:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection
        self.row: dict[str, Any] | None = None
        self.rowcount = 0

    def __enter__(self) -> "_Cursor":
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def execute(self, sql: str, params: Any = None) -> None:
        self.connection.commands.append((sql, params))
        normalized = " ".join(sql.split())
        candidate = self.connection.candidate
        self.rowcount = 0
        if normalized.startswith("SELECT asset.id AS asset_id"):
            self.row = {
                "asset_id": candidate["assetId"],
                "variant_id": candidate["variantId"],
                "content_sha256": candidate["contentSha256"],
                "source_version_sha256": candidate["sourceVersionSha256"],
                "width_px": 429,
                "height_px": 600,
                "source_path": candidate["sourcePath"],
                "pointer_public_allowed": self.connection.pointer_public_allowed,
            }
        elif normalized.startswith("SELECT id FROM market_image_asset"):
            self.row = {"id": candidate["assetId"]}
        elif normalized.startswith("SELECT semantic_match_status"):
            self.row = dict(self.connection.qc) if self.connection.qc else None
        elif normalized.startswith("INSERT INTO market_image_qc"):
            self.connection.qc = {
                "semantic_match_status": mod.SEMANTIC_STATUS,
                "card_number_match": 0,
                "language_match": 0,
                "tcg_match": 0,
                "raw_front_confirmed": 0,
                "public_allowed": 0,
                "rejection_reason": mod.REASON,
                "qc_version": mod.QC_VERSION,
            }
            self.rowcount = 1
            self.row = None
        elif normalized.startswith("UPDATE market_image_source_pointer"):
            assert params == (
                candidate["variantId"],
                candidate["sourceVersionSha256"],
                candidate["sourcePath"],
            )
            self.connection.pointer_public_allowed = 0
            self.rowcount = 1
            self.row = None
        elif normalized.startswith("SELECT qc.semantic_match_status"):
            self.row = {
                **dict(self.connection.qc or {}),
                "pointer_public_allowed": self.connection.pointer_public_allowed,
            }
        else:
            raise AssertionError(f"unexpected SQL: {normalized}")

    def fetchone(self) -> dict[str, Any] | None:
        return self.row


def test_apply_is_exact_scoped_and_second_run_is_noop() -> None:
    candidate = mod.build_plan(
        [_family_row(index) for index in range(1, 321)],
        set(range(1, 147)),
    )["candidates"][0]
    connection = _Connection(candidate)
    first = mod.apply_candidate(connection.cursor(), candidate)
    second = mod.apply_candidate(connection.cursor(), candidate)
    assert first["qcInserted"] is True
    assert first["pointerDisabled"] is True
    assert first["noOp"] is False
    assert second["qcInserted"] is False
    assert second["pointerDisabled"] is False
    assert second["noOp"] is True
    assert connection.qc == {
        "semantic_match_status": mod.SEMANTIC_STATUS,
        "card_number_match": 0,
        "language_match": 0,
        "tcg_match": 0,
        "raw_front_confirmed": 0,
        "public_allowed": 0,
        "rejection_reason": mod.REASON,
        "qc_version": mod.QC_VERSION,
    }
    sql_text = "\n".join(sql for sql, _params in connection.commands)
    assert "DELETE " not in sql_text
    assert "market_image_asset SET" not in sql_text
    assert "source_version_sha256=%s" in sql_text
    assert "source_path=%s" in sql_text


def test_write_schema_requires_canonical_database_and_exact_qc_version_capacity() -> None:
    cursor = Mock()
    cursor.fetchone.side_effect = [
        {"database_name": "cardz_market_cap"},
        {"max_length": len(mod.QC_VERSION)},
    ]
    mod.assert_write_schema(cursor)

    short = Mock()
    short.fetchone.side_effect = [
        {"database_name": "cardz_market_cap"},
        {"max_length": len(mod.QC_VERSION) - 1},
    ]
    with pytest.raises(mod.QuarantineError, match="qc_version_column_too_short"):
        mod.assert_write_schema(short)


def test_failure_events_are_stable_and_skip_existing_open_items(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plan = mod.build_plan(
        [_family_row(index) for index in range(1, 321)],
        set(range(1, 147)),
    )
    first_key = (
        f"variant:1:asset:1001:{_sha(1)}"
    )
    monkeypatch.setattr(
        mod,
        "current_failures",
        lambda **_kwargs: [{"itemKey": first_key}],
    )
    record = Mock(return_value=tmp_path / "event.json")
    monkeypatch.setattr(mod, "record_failure", record)
    written = mod.record_failure_events(
        plan,
        run_id="test_run",
        receipt_path=tmp_path / "receipt.json",
        ledger_root=tmp_path / "failures",
    )
    assert written == 320
    assert record.call_count == 320
    assert all(
        call.kwargs["next_action"] == "replace_image_source"
        and call.kwargs["reason_code"] == mod.REASON
        for call in record.call_args_list
    )


def test_cli_defaults_to_fixed_family_dry_run() -> None:
    args = mod.build_parser().parse_args([])
    assert args.family == mod.FAMILY
    assert args.write is False
