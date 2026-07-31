from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Callable

import pytest
from PIL import Image

from pipelines import limitless_replacement_import as mod


def _pass_sample_gate(_raw: bytes, **_kwargs: Any) -> dict[str, str]:
    return {"policyId": "cardz-source-sample-v1", "status": "scan_required"}


def _write_json(path: Path, value: Any) -> str:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _mutated_contract(
    tmp_path: Path,
    mutate_manifest: Callable[[dict[str, Any]], None],
) -> tuple[dict[str, Any], dict[str, Any], list[mod.CandidatePlan]]:
    manifest = json.loads(mod.MANIFEST_PATH.read_text(encoding="utf-8"))
    ledger = json.loads(mod.FAILURE_LEDGER_PATH.read_text(encoding="utf-8"))
    mutate_manifest(manifest)
    manifest_path = tmp_path / "replacement-manifest-wave3.json"
    ledger_path = tmp_path / "failure-ledger-wave3.json"
    manifest_sha256 = _write_json(manifest_path, manifest)
    ledger["wave3Manifest"]["sha256"] = manifest_sha256
    ledger_sha256 = _write_json(ledger_path, ledger)
    return mod.validate_contract(
        manifest_path,
        ledger_path,
        manifest_sha256=manifest_sha256,
        failure_ledger_sha256=ledger_sha256,
        repo_root=mod.ROOT,
        claim_root=tmp_path,
        source_media_root=mod.SOURCE_MEDIA_ROOT,
        sample_gate=_pass_sample_gate,
    )


def test_wave3_contract_is_13_ready_and_133_non_ready() -> None:
    manifest, ledger, plans = mod.validate_contract(sample_gate=_pass_sample_gate)
    assert manifest["counts"] == {
        "ready": 13,
        "review": 10,
        "reject": 15,
        "unresolved": 108,
        "total": 146,
    }
    assert len(ledger["entries"]) == 133
    assert [plan.variant_id for plan in plans] == [
        19, 90, 132, 1232, 1234, 1238, 1300,
        1309, 1424, 1428, 1430, 1441, 1467,
    ]
    assert all(plan.major_component_count == 1 for plan in plans)
    assert all(
        mod.FOREGROUND_ASPECT_MINIMUM
        <= plan.foreground_aspect_ratio
        <= mod.FOREGROUND_ASPECT_MAXIMUM
        for plan in plans
    )


def test_wave3_manifest_sha_is_immutable() -> None:
    with pytest.raises(mod.ImportContractError, match="replacement_manifest_sha256_mismatch"):
        mod.validate_contract(manifest_sha256="0" * 64, sample_gate=_pass_sample_gate)


def test_actual_1741_known_sample_hash_is_rejected() -> None:
    manifest = json.loads(mod.MANIFEST_PATH.read_text(encoding="utf-8"))
    record = copy.deepcopy(mod._record_for(manifest, "reject", 1741))
    candidate = record["selectedCandidate"]
    source_path = mod._candidate_source_path(
        candidate["localPath"],
        repo_root=mod.ROOT,
        claim_media_root=mod.SOURCE_MEDIA_ROOT,
        variant_id=1741,
    )
    assert mod.sha256_file(source_path) == mod.KNOWN_SAMPLE_SHA256
    assert candidate["mediaQc"]["visualSampleQc"] == {
        "contentSha256": mod.KNOWN_SAMPLE_SHA256,
        "priorSampleOcrStatus": "clean",
        "reviewOwner": "MAIN",
        "status": "reject_known_sample_visual_confirmed",
        "visibleToken": "SAMPLE",
    }
    candidate["decision"] = "ready"
    candidate["integrationState"] = "source_ready_for_registered_normalization"
    candidate["semanticState"] = "pass"
    candidate["mediaQc"]["sampleOcrStatus"] = "clean"
    with pytest.raises(mod.ImportContractError, match="known_sample_content_sha256_rejected:1741"):
        mod._validate_ready_candidate(
            record,
            repo_root=mod.ROOT,
            claim_media_root=mod.SOURCE_MEDIA_ROOT,
            manifest_sha256=mod.MANIFEST_SHA256,
            captured_at=mod._parse_generated_at(manifest["generatedAt"]),
            sample_gate=_pass_sample_gate,
        )


def test_actual_1718_disconnected_alpha_artifact_is_review_only() -> None:
    manifest = json.loads(mod.MANIFEST_PATH.read_text(encoding="utf-8"))
    record = mod._record_for(manifest, "review", 1718)
    candidate = record["selectedCandidate"]
    source_path = mod._candidate_source_path(
        candidate["localPath"],
        repo_root=mod.ROOT,
        claim_media_root=mod.SOURCE_MEDIA_ROOT,
        variant_id=1718,
    )
    assert mod.sha256_file(source_path) == mod.FOREGROUND_REVIEW_SHA256
    with Image.open(source_path) as opened:
        opened.load()
        metrics = mod.inspect_foreground(opened)
    assert metrics == {
        "bbox": (283, 60, 930, 670),
        "widthPx": 647,
        "heightPx": 610,
        "aspectRatio": 1.060656,
        "majorComponentCount": 2,
        "majorComponentPixels": [264484, 26966],
        "majorComponents": [
            {"areaPx": 264484, "bbox": (283, 60, 717, 670)},
            {"areaPx": 26966, "bbox": (747, 487, 930, 670)},
        ],
    }
    with pytest.raises(mod.ImportContractError, match="foreground_aspect_rejected:1718"):
        mod.require_foreground_pass(1718, metrics)


def test_candidate_must_remain_inside_wave2_source_media(
    tmp_path: Path,
) -> None:
    def mutate(manifest: dict[str, Any]) -> None:
        manifest["ready"][0]["selectedCandidate"][
            "localPath"
        ] = "pipelines/limitless_replacement_import.py"

    with pytest.raises(mod.ImportContractError, match="outside_registered_claim"):
        _mutated_contract(tmp_path, mutate)


def test_ready_source_cannot_drift_back_to_limitless(tmp_path: Path) -> None:
    def mutate(manifest: dict[str, Any]) -> None:
        manifest["ready"][0]["selectedCandidate"][
            "sourceUrl"
        ] = "https://limitlesstcg.nyc3.cdn.digitaloceanspaces.com/one-piece/card.webp"

    with pytest.raises(
        mod.ImportContractError, match="known_limitless_or_sample_source_rejected"
    ):
        _mutated_contract(tmp_path, mutate)


def test_all_146_original_limitless_rows_remain_blacklisted(tmp_path: Path) -> None:
    def mutate(manifest: dict[str, Any]) -> None:
        manifest["unresolved"][0]["target"]["wave1Target"][
            "rejectReason"
        ] = "drifted"

    with pytest.raises(mod.ImportContractError, match="limitless_family_blacklist_drift"):
        _mutated_contract(tmp_path, mutate)


def test_expected_ja_rejects_english_language_evidence(tmp_path: Path) -> None:
    def mutate(manifest: dict[str, Any]) -> None:
        manifest["ready"][0]["selectedCandidate"]["sixPartQc"]["language"][
            "observed"
        ] = "en"

    with pytest.raises(mod.ImportContractError, match="expected_ja_has_english_drift"):
        _mutated_contract(tmp_path, mutate)


def test_six_part_evidence_must_all_pass(tmp_path: Path) -> None:
    def mutate(manifest: dict[str, Any]) -> None:
        manifest["ready"][0]["selectedCandidate"]["sixPartQc"]["finish"][
            "status"
        ] = "uncertain"

    with pytest.raises(mod.ImportContractError, match="six_part_not_pass:19:finish"):
        _mutated_contract(tmp_path, mutate)


def _db_rows(
    plans: list[mod.CandidatePlan],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    printing_rows: list[dict[str, Any]] = []
    source_rows: list[dict[str, Any]] = []
    for plan in plans:
        printing_rows.append(
            {
                "variant_id": plan.variant_id,
                "opaque_id": plan.target["cardId"],
                "variant_identity_status": "confirmed",
                "tcg_code": plan.target["game"],
                "card_language": plan.target["language"],
                "set_name": plan.target["set"],
                "collector_number": plan.target["collector"],
                "edition_code": plan.target["edition"],
                "parallel_code": plan.target["parallel"],
                "finish_code": plan.target["finish"],
                "canonical_printing_sha256": plan.target["canonicalPrintingSha256"],
                "printing_identity_status": "canonical",
                "printing_evidence_sha256": plan.target["printingEvidenceSha256"],
            }
        )
        source_rows.append(
            {
                "source_code": plan.source,
                "external_entity_id": plan.source_id,
                "variant_id": plan.variant_id,
                "match_status": "exact",
                "evidence_sha256": plan.source_binding_evidence_sha256,
            }
        )
    return printing_rows, source_rows


def test_db_current_printing_and_source_binding_drift_fail_closed() -> None:
    _manifest, _ledger, plans = mod.validate_contract(sample_gate=_pass_sample_gate)
    printing_rows, source_rows = _db_rows(plans)
    assert mod.compare_db_state(plans, printing_rows, source_rows) == []
    printing_rows[0]["parallel_code"] = "drifted"
    source_rows[1]["match_status"] = "review"
    assert mod.compare_db_state(plans, printing_rows, source_rows) == [
        "19:printing_drift:parallel_code",
        "90:source_binding_not_exact",
    ]


class _WriteConnection:
    def __init__(self) -> None:
        self.commands: list[tuple[str, Any]] = []
        self.asset: dict[str, Any] | None = None
        self.qc: dict[str, Any] | None = None

    def cursor(self) -> "_WriteCursor":
        return _WriteCursor(self)


class _WriteCursor:
    def __init__(self, connection: _WriteConnection) -> None:
        self.connection = connection
        self.row: dict[str, Any] | None = None

    def __enter__(self) -> "_WriteCursor":
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def execute(self, sql: str, params: Any = None) -> None:
        self.connection.commands.append((sql, params))
        normalized = " ".join(sql.split())
        if normalized.startswith("INSERT IGNORE INTO market_image_asset"):
            if self.connection.asset is None:
                (
                    variant_id, image_kind, content_sha256, private_object_key,
                    mime_type, width_px, height_px, source_version_sha256, captured_at,
                ) = params
                self.connection.asset = {
                    "id": 77,
                    "variant_id": variant_id,
                    "image_kind": image_kind,
                    "content_sha256": content_sha256,
                    "private_object_key": private_object_key,
                    "mime_type": mime_type,
                    "width_px": width_px,
                    "height_px": height_px,
                    "source_version_sha256": source_version_sha256,
                    "captured_at": captured_at,
                }
            self.row = None
        elif normalized.startswith("SELECT id, private_object_key"):
            self.row = dict(self.connection.asset or {})
        elif normalized.startswith("INSERT IGNORE INTO market_image_qc"):
            if self.connection.qc is None:
                asset_id, status, reason, checked_at, qc_version = params
                self.connection.qc = {
                    "image_asset_id": asset_id,
                    "semantic_match_status": status,
                    "card_number_match": 1,
                    "language_match": 1,
                    "tcg_match": 1,
                    "raw_front_confirmed": 0,
                    "public_allowed": 0,
                    "rejection_reason": reason,
                    "checked_at": checked_at,
                    "qc_version": qc_version,
                }
            self.row = None
        elif normalized.startswith("SELECT semantic_match_status"):
            self.row = dict(self.connection.qc or {})
        else:
            raise AssertionError(f"unexpected SQL: {normalized}")

    def fetchone(self) -> dict[str, Any] | None:
        return self.row


def test_private_writer_is_idempotent_and_has_no_pointer_or_public_write(
    tmp_path: Path,
) -> None:
    _manifest, _ledger, plans = mod.validate_contract(sample_gate=_pass_sample_gate)
    plan = plans[0]
    durable_root = tmp_path / "repo/data/runtime/private-source-map/replacements"
    repo_root = tmp_path / "repo"
    connection = _WriteConnection()
    first = mod.apply_candidate(
        connection,
        plan,
        durable_root=durable_root,
        repo_root=repo_root,
        manifest_sha256=mod.MANIFEST_SHA256,
    )
    second = mod.apply_candidate(
        connection,
        plan,
        durable_root=durable_root,
        repo_root=repo_root,
        manifest_sha256=mod.MANIFEST_SHA256,
    )
    assert first == second == 77
    destination, _private_key = mod.destination_for(
        plan,
        durable_root=durable_root,
        repo_root=repo_root,
        manifest_sha256=mod.MANIFEST_SHA256,
    )
    assert hashlib.sha256(destination.read_bytes()).hexdigest() == plan.content_sha256
    assert connection.qc and connection.qc["public_allowed"] == 0
    assert connection.qc["semantic_match_status"] == mod.SEMANTIC_PENDING
    sql_text = "\n".join(sql for sql, _params in connection.commands)
    assert "market_image_source_pointer" not in sql_text
    assert "UPDATE " not in sql_text
    assert mod.build_parser().parse_args([]).write is False
