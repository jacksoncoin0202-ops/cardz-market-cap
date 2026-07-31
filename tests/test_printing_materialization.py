from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import db_runtime  # noqa: E402


CARD_EVIDENCE_SHA = "a" * 64


def write_json(path: Path, value: object) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
        "utf-8"
    )
    path.write_bytes(raw)
    return raw


def write_content_addressed_json(root: Path, value: object) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for existing in root.glob("*.json"):
        existing.unlink()
    raw = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
        "utf-8"
    )
    path = root / f"{db_runtime.sha256(raw)}.json"
    path.write_bytes(raw)
    return path


def make_qc_bundle(root: Path, run_id: str = "qc_test_01") -> Path:
    qc_root = root / "qc"
    counts = {
        "qualified": 1,
        "monitoring": 0,
        "releaseReadyQualified": 0,
        "releaseBlockedQualified": 1,
    }
    report_counts = {**counts, "catalog": 1, "sourceIdentities": 2}
    gate = {"eligible": False, "blockers": ["canonical_printing_missing"]}
    report = {
        "runId": run_id,
        "asOf": "2026-07-29T09:02:00Z",
        "status": "blocked",
        "counts": report_counts,
        "releaseGate": gate,
        "cards": [
            {
                "id": "cmc_fixture_24",
                "variantId": 24,
                "segment": "qualified",
                "marketRank": 1,
                "evidenceSha256": CARD_EVIDENCE_SHA,
            }
        ],
    }
    report_bytes = write_json(qc_root / run_id / "report.json", report)
    receipt = {
        "runId": run_id,
        "database": "cardz_market_cap",
        "readOnly": True,
        "asOf": report["asOf"],
        "status": report["status"],
        "counts": counts,
        "releaseGate": gate,
        "reportSha256": db_runtime.sha256(report_bytes),
    }
    write_json(qc_root / run_id / "receipt.json", receipt)
    return qc_root


def make_decision(root: Path) -> tuple[Path, dict[str, Any]]:
    identity = {
        "tcgCode": "one-piece",
        "cardLanguage": "ja",
        "setName": "Fixture Set",
        "collectorNumber": "OP05-119",
        "editionCode": "first-edition",
        "parallelCode": "manga-alt",
        "finishCode": "foil",
    }
    fields: dict[str, dict[str, str]] = {}
    for index, (field, value) in enumerate(identity.items()):
        source_code = "gemrate" if index < 3 else "snkrdunk"
        external_id = "gem-24" if index < 3 else "snk-24"
        field_receipt: dict[str, Any] = {
            "type": db_runtime.PRINTING_FIELD_RECEIPT_TYPE,
            "schemaVersion": 1,
            "variantId": 24,
            "field": field,
            "rawValue": value,
            "normalizedValue": value,
            "evidenceType": "human_verified_source_field",
            "extractorVersion": "fixture-v1",
            "sourceCode": source_code,
            "externalEntityId": external_id,
        }
        field_receipt["evidenceSha256"] = (
            db_runtime.printing_field_receipt_sha256(field_receipt)
        )
        evidence_path = root / "evidence" / f"{field}.json"
        evidence_bytes = write_json(evidence_path, field_receipt)
        fields[field] = {
            "rawValue": value,
            "normalizedValue": value,
            "evidenceType": "human_verified_source_field",
            "extractorVersion": "fixture-v1",
            "sourceCode": source_code,
            "externalEntityId": external_id,
            "sourceReceiptPath": str(evidence_path),
            "sourceReceiptSha256": db_runtime.sha256(evidence_bytes),
        }
    decision = {
        "type": "canonical_printing_approval",
        "schemaVersion": 1,
        "decision": "approve",
        "variantId": 24,
        "opaqueId": "cmc_fixture_24",
        "actor": "fixture-reviewer",
        "approvedAt": "2026-07-29T10:00:00Z",
        "policyVersion": "canonical-printing-v1",
        "qcRunId": "qc_test_01",
        "cardEvidenceSha256": CARD_EVIDENCE_SHA,
        "identity": identity,
        "sourceBindings": [
            {"sourceCode": "gemrate", "externalEntityId": "gem-24"},
            {"sourceCode": "snkrdunk", "externalEntityId": "snk-24"},
        ],
        "fields": fields,
    }
    decision_root = root / "decisions"
    write_content_addressed_json(decision_root, decision)
    return decision_root, decision


def retarget_field_receipts(
    decision: dict[str, Any],
    *,
    current_source_code: str,
    source_code: str,
    external_entity_id: str,
) -> None:
    for evidence in decision["fields"].values():
        if evidence["sourceCode"] != current_source_code:
            continue
        evidence["sourceCode"] = source_code
        evidence["externalEntityId"] = external_entity_id
        receipt_path = Path(evidence["sourceReceiptPath"])
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt["sourceCode"] = source_code
        receipt["externalEntityId"] = external_entity_id
        receipt.pop("evidenceSha256")
        receipt["evidenceSha256"] = db_runtime.printing_field_receipt_sha256(receipt)
        evidence["sourceReceiptSha256"] = db_runtime.sha256(
            write_json(receipt_path, receipt)
        )


def replace_field_value(
    decision: dict[str, Any],
    *,
    field: str,
    value: str,
) -> None:
    decision["identity"][field] = value
    evidence = decision["fields"][field]
    evidence["rawValue"] = value
    evidence["normalizedValue"] = value
    receipt_path = Path(evidence["sourceReceiptPath"])
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["rawValue"] = value
    receipt["normalizedValue"] = value
    receipt.pop("evidenceSha256")
    receipt["evidenceSha256"] = db_runtime.printing_field_receipt_sha256(receipt)
    evidence["sourceReceiptSha256"] = db_runtime.sha256(
        write_json(receipt_path, receipt)
    )


def variant_state() -> dict[str, Any]:
    return {
        "variant_id": 24,
        "opaque_id": "cmc_fixture_24",
        "tcg_code": "one-piece",
        "card_language": "ja",
        "set_name": "Fixture Set",
        "collector_number": "OP05-119",
        "variant_identity_status": "confirmed",
        "canonical_variant_id": None,
        "printing_tcg_code": None,
        "printing_card_language": None,
        "printing_set_name": None,
        "printing_collector_number": None,
        "edition_code": None,
        "parallel_code": None,
        "finish_code": None,
        "canonical_printing_sha256": None,
        "printing_identity_status": None,
        "printing_evidence_sha256": None,
    }


class PrintingCursor:
    def __init__(self, connection: "PrintingConnection") -> None:
        self.connection = connection
        self.last_query = ""
        self.last_args: tuple[Any, ...] = ()

    def __enter__(self) -> "PrintingCursor":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, query: str, args: object = None) -> None:
        self.last_query = " ".join(query.split())
        self.last_args = tuple(args or ())  # type: ignore[arg-type]
        self.connection.calls.append((self.last_query, self.last_args))
        if self.last_query.startswith("INSERT INTO catalog_printing_identity"):
            if self.connection.before_write is None:
                self.connection.before_write = copy.deepcopy(self.connection.variant)
            values = self.last_args
            self.connection.variant.update(
                {
                    "printing_tcg_code": values[1],
                    "printing_card_language": values[2],
                    "printing_set_name": values[3],
                    "printing_collector_number": values[4],
                    "edition_code": values[5],
                    "parallel_code": values[6],
                    "finish_code": values[7],
                    "canonical_printing_sha256": values[8],
                    "printing_identity_status": "canonical",
                    "printing_evidence_sha256": values[9],
                }
            )

    def fetchall(self) -> list[dict[str, Any]]:
        if "FROM catalog_variant v" in self.last_query:
            return [copy.deepcopy(self.connection.variant)]
        if "FROM catalog_source_identity" in self.last_query:
            return copy.deepcopy(self.connection.sources)
        if "SELECT canonical_printing_sha256, variant_id" in self.last_query:
            return []
        if "FROM catalog_printing_identity" in self.last_query:
            variant = self.connection.variant
            if variant.get("printing_identity_status") is None:
                return []
            result = [
                {
                    "variant_id": variant["variant_id"],
                    "tcg_code": variant["printing_tcg_code"],
                    "card_language": variant["printing_card_language"],
                    "set_name": variant["printing_set_name"],
                    "collector_number": variant["printing_collector_number"],
                    "edition_code": variant["edition_code"],
                    "parallel_code": variant["parallel_code"],
                    "finish_code": variant["finish_code"],
                    "canonical_printing_sha256": variant[
                        "canonical_printing_sha256"
                    ],
                    "identity_status": variant["printing_identity_status"],
                    "evidence_sha256": variant["printing_evidence_sha256"],
                }
            ]
            if self.connection.corrupt_readback:
                result[0]["finish_code"] = "corrupt-readback"
            return result
        return []

    def fetchone(self) -> dict[str, Any] | None:
        if self.last_query.startswith("SELECT DATABASE()"):
            return {"database_name": self.connection.database_name}
        if self.last_query.startswith("SELECT GET_LOCK"):
            return {"acquired": 1}
        if "WHERE canonical_printing_sha256=%s AND variant_id<>%s" in self.last_query:
            return {"variant_id": 999} if self.connection.duplicate_owner else None
        return None


class PrintingConnection:
    def __init__(self) -> None:
        self.variant = variant_state()
        self.sources = [
            {
                "variant_id": 24,
                "source_code": "gemrate",
                "external_entity_id": "gem-24",
                "match_status": "exact",
            },
            {
                "variant_id": 24,
                "source_code": "snkrdunk",
                "external_entity_id": "snk-24",
                "match_status": "exact",
            },
        ]
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.before_write: dict[str, Any] | None = None
        self.database_name = "cardz_market_cap"
        self.corrupt_readback = False
        self.duplicate_owner = False
        self.commits = 0
        self.rollbacks = 0
        self.begins = 0

    def cursor(self) -> PrintingCursor:
        return PrintingCursor(self)

    def commit(self) -> None:
        self.commits += 1
        self.before_write = None

    def begin(self) -> None:
        self.begins += 1

    def rollback(self) -> None:
        self.rollbacks += 1
        if self.before_write is not None:
            self.variant = self.before_write
            self.before_write = None


def build_fixture_plan(
    tmp_path: Path,
    *,
    connection: PrintingConnection | None = None,
) -> tuple[dict[str, Any], PrintingConnection, Path, Path, Path]:
    qc_root = make_qc_bundle(tmp_path)
    decision_root, _ = make_decision(tmp_path)
    quarantine_root = tmp_path / "quarantine"
    active_connection = connection or PrintingConnection()
    plan = db_runtime.build_printing_plan(
        active_connection,  # type: ignore[arg-type]
        decision_root=decision_root,
        qc_root=qc_root,
        quarantine_root=quarantine_root,
        allowed_evidence_roots=(tmp_path,),
    )
    return plan, active_connection, qc_root, decision_root, quarantine_root


def test_build_plan_binds_qc_receipt_evidence_and_post_apply_state(
    tmp_path: Path,
) -> None:
    plan, _, _, _, _ = build_fixture_plan(tmp_path)

    assert plan["counts"] == {
        "qualifiedQcCards": 1,
        "approvedRows": 1,
        "quarantinedRows": 0,
    }
    row = plan["rows"][0]
    assert row["qcRunId"] == "qc_test_01"
    assert row["cardEvidenceSha256"] == CARD_EVIDENCE_SHA
    approval_path = next((tmp_path / "decisions").glob("*.json"))
    assert row["approvalReceiptSha256"] == db_runtime.sha256(approval_path.read_bytes())
    assert row["dbFingerprint"] != row["postApplyDbFingerprint"]
    assert db_runtime.printing_plan_sha256(plan) == plan["planSha256"]


def test_printing_hash_binds_language_and_all_printing_dimensions() -> None:
    identity = {
        "tcgCode": "one-piece",
        "cardLanguage": "ja",
        "setName": "Fixture Set",
        "collectorNumber": "OP05-119",
        "editionCode": "first-edition",
        "parallelCode": "manga-alt",
        "finishCode": "foil",
    }
    expected = db_runtime.printing_identity_sha256(identity)
    assert db_runtime.printing_identity_sha256(
        {**identity, "cardLanguage": "en"}
    ) != expected
    for field in ("editionCode", "parallelCode", "finishCode"):
        assert db_runtime.printing_identity_sha256(
            {**identity, field: f"{identity[field]}-different"}
        ) != expected


def test_stale_receipt_does_not_make_current_receipt_ambiguous(
    tmp_path: Path,
) -> None:
    qc_root = make_qc_bundle(tmp_path)
    decision_root, current = make_decision(tmp_path)
    stale = copy.deepcopy(current)
    stale["qcRunId"] = "qc_old"
    stale_bytes = (
        json.dumps(stale, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    (decision_root / f"{db_runtime.sha256(stale_bytes)}.json").write_bytes(stale_bytes)

    plan = db_runtime.build_printing_plan(
        PrintingConnection(),  # type: ignore[arg-type]
        decision_root=decision_root,
        qc_root=qc_root,
        quarantine_root=tmp_path / "quarantine",
        allowed_evidence_roots=(tmp_path,),
    )

    assert [row["variantId"] for row in plan["rows"]] == [24]
    assert not any(
        item.get("reason") == "ambiguous_printing_receipts"
        for item in plan["quarantine"]
    )


@pytest.mark.parametrize("field", db_runtime.PRINTING_FIELDS)
def test_missing_printing_field_is_quarantined(tmp_path: Path, field: str) -> None:
    qc_root = make_qc_bundle(tmp_path)
    decision_root, decision = make_decision(tmp_path)
    decision["identity"][field] = ""
    write_content_addressed_json(decision_root, decision)

    plan = db_runtime.build_printing_plan(
        PrintingConnection(),  # type: ignore[arg-type]
        decision_root=decision_root,
        qc_root=qc_root,
        quarantine_root=tmp_path / "quarantine",
        allowed_evidence_roots=(tmp_path,),
    )

    assert plan["rows"] == []
    assert plan["quarantine"][0]["reason"] == "missing_printing_field"


@pytest.mark.parametrize(
    ("mutation", "reason"),
    (
        ("placeholder", "missing_printing_field"),
        ("receipt_hash", "receipt_hash_mismatch"),
        ("base_mismatch", "canonical_base_mismatch"),
        ("extra_binding", "non_exact_or_discovery_evidence"),
        ("unverified_source_field", "non_exact_or_discovery_evidence"),
    ),
)
def test_untrusted_printing_evidence_is_quarantined(
    tmp_path: Path, mutation: str, reason: str
) -> None:
    qc_root = make_qc_bundle(tmp_path)
    decision_root, decision = make_decision(tmp_path)
    if mutation == "placeholder":
        decision["identity"]["editionCode"] = "unknown"
        decision["fields"]["editionCode"]["normalizedValue"] = "unknown"
    elif mutation == "receipt_hash":
        decision["fields"]["editionCode"]["sourceReceiptSha256"] = "0" * 64
    elif mutation == "base_mismatch":
        decision["identity"]["collectorNumber"] = "OP05-120"
        decision["fields"]["collectorNumber"]["normalizedValue"] = "OP05-120"
    elif mutation == "unverified_source_field":
        for evidence in decision["fields"].values():
            evidence["evidenceType"] = "source_field"
    else:
        decision["sourceBindings"].append(
            {"sourceCode": "ebay", "externalEntityId": "listing-1"}
        )
    write_content_addressed_json(decision_root, decision)

    plan = db_runtime.build_printing_plan(
        PrintingConnection(),  # type: ignore[arg-type]
        decision_root=decision_root,
        qc_root=qc_root,
        quarantine_root=tmp_path / "quarantine",
        allowed_evidence_roots=(tmp_path,),
    )

    assert plan["rows"] == []
    assert plan["quarantine"][0]["reason"] == reason


@pytest.mark.parametrize(
    ("mutation", "reason"),
    (
        ("value", "field_receipt_value_mismatch"),
        ("variant", "field_receipt_variant_mismatch"),
        ("field", "field_receipt_field_mismatch"),
        ("evidence_hash", "field_receipt_evidence_hash_mismatch"),
    ),
)
def test_field_receipt_must_bind_variant_field_value_and_evidence_hash(
    tmp_path: Path,
    mutation: str,
    reason: str,
) -> None:
    qc_root = make_qc_bundle(tmp_path)
    decision_root, decision = make_decision(tmp_path)
    evidence = decision["fields"]["finishCode"]
    receipt_path = Path(evidence["sourceReceiptPath"])
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if mutation == "value":
        receipt["rawValue"] = "regular"
        receipt["normalizedValue"] = "regular"
    elif mutation == "variant":
        receipt["variantId"] = 25
    elif mutation == "field":
        receipt["field"] = "editionCode"
    else:
        receipt["evidenceSha256"] = "0" * 64
    if mutation != "evidence_hash":
        receipt["evidenceSha256"] = db_runtime.printing_field_receipt_sha256(receipt)
    receipt_bytes = write_json(receipt_path, receipt)
    evidence["sourceReceiptSha256"] = db_runtime.sha256(receipt_bytes)
    write_content_addressed_json(decision_root, decision)

    plan = db_runtime.build_printing_plan(
        PrintingConnection(),  # type: ignore[arg-type]
        decision_root=decision_root,
        qc_root=qc_root,
        quarantine_root=tmp_path / "quarantine",
        allowed_evidence_roots=(tmp_path,),
    )

    assert plan["rows"] == []
    assert plan["quarantine"][0]["reason"] == reason


def test_alias_and_non_exact_source_are_rejected(tmp_path: Path) -> None:
    connection = PrintingConnection()
    connection.variant["canonical_variant_id"] = 9
    plan, _, _, _, _ = build_fixture_plan(tmp_path, connection=connection)
    assert plan["quarantine"][0]["reason"] == "alias_or_duplicate_unresolved"

    connection = PrintingConnection()
    connection.sources[1]["match_status"] = "candidate"
    plan, _, _, _, _ = build_fixture_plan(tmp_path / "non-exact", connection=connection)
    assert plan["quarantine"][0]["reason"] == "non_exact_or_discovery_evidence"

    connection = PrintingConnection()
    connection.sources.append(
        {
            "variant_id": 24,
            "source_code": "gemrate",
            "external_entity_id": "gem-24-second",
            "match_status": "exact",
        }
    )
    plan, _, _, _, _ = build_fixture_plan(
        tmp_path / "ambiguous-gemrate", connection=connection
    )
    assert plan["quarantine"][0]["reason"] == "ambiguous_source_binding"

    connection = PrintingConnection()
    connection.variant.update(
        {
            "printing_tcg_code": "one-piece",
            "printing_card_language": "ja",
            "printing_set_name": "Fixture Set",
            "printing_collector_number": "OP05-119",
            "edition_code": "different-edition",
            "parallel_code": "different-parallel",
            "finish_code": "foil",
            "canonical_printing_sha256": "b" * 64,
            "printing_identity_status": "canonical",
            "printing_evidence_sha256": "c" * 64,
        }
    )
    plan, _, _, _, _ = build_fixture_plan(
        tmp_path / "canonical-conflict", connection=connection
    )
    assert plan["quarantine"][0]["reason"] == "existing_canonical_conflict"


def test_pc_v2_accepts_one_exact_product_id_without_snk(tmp_path: Path) -> None:
    qc_root = make_qc_bundle(tmp_path)
    decision_root, decision = make_decision(tmp_path)
    decision["policyVersion"] = "canonical-printing-v2-pc"
    decision["sourceBindings"][1] = {
        "sourceCode": "pricecharting",
        "externalEntityId": "630417",
    }
    retarget_field_receipts(
        decision,
        current_source_code="snkrdunk",
        source_code="pricecharting",
        external_entity_id="630417",
    )
    write_content_addressed_json(decision_root, decision)
    connection = PrintingConnection()
    connection.sources[1] = {
        "variant_id": 24,
        "source_code": "pricecharting",
        "external_entity_id": "630417",
        "match_status": "exact",
    }

    plan = db_runtime.build_printing_plan(
        connection,  # type: ignore[arg-type]
        decision_root=decision_root,
        qc_root=qc_root,
        quarantine_root=tmp_path / "quarantine",
        allowed_evidence_roots=(tmp_path,),
    )
    assert plan["counts"]["approvedRows"] == 1
    assert plan["rows"][0]["operation"] == "upsert"


def test_pokemon_numerator_only_collector_cannot_materialize_as_exact(
    tmp_path: Path,
) -> None:
    qc_root = make_qc_bundle(tmp_path)
    decision_root, decision = make_decision(tmp_path)
    for field, value in {
        "tcgCode": "pokemon",
        "setName": "Unknown Pokemon Set",
        "collectorNumber": "GG69",
    }.items():
        replace_field_value(decision, field=field, value=value)
    write_content_addressed_json(decision_root, decision)
    connection = PrintingConnection()
    connection.variant.update(
        {
            "tcg_code": "pokemon",
            "set_name": "Unknown Pokemon Set",
            "collector_number": "GG69",
        }
    )

    plan = db_runtime.build_printing_plan(
        connection,  # type: ignore[arg-type]
        decision_root=decision_root,
        qc_root=qc_root,
        quarantine_root=tmp_path / "quarantine",
        allowed_evidence_roots=(tmp_path,),
    )

    assert plan["counts"]["approvedRows"] == 0
    assert plan["quarantine"][0]["reason"] == "collector_number_incomplete"


def test_pc_v2_rejects_snk_conflict_and_non_product_id(tmp_path: Path) -> None:
    qc_root = make_qc_bundle(tmp_path)
    decision_root, decision = make_decision(tmp_path)
    decision["policyVersion"] = "canonical-printing-v2-pc"
    decision["sourceBindings"][1] = {
        "sourceCode": "pricecharting",
        "externalEntityId": "not-a-product-id",
    }
    retarget_field_receipts(
        decision,
        current_source_code="snkrdunk",
        source_code="pricecharting",
        external_entity_id="not-a-product-id",
    )
    write_content_addressed_json(decision_root, decision)
    connection = PrintingConnection()
    connection.sources[1] = {
        "variant_id": 24,
        "source_code": "pricecharting",
        "external_entity_id": "not-a-product-id",
        "match_status": "exact",
    }
    plan = db_runtime.build_printing_plan(
        connection,  # type: ignore[arg-type]
        decision_root=decision_root,
        qc_root=qc_root,
        quarantine_root=tmp_path / "quarantine",
        allowed_evidence_roots=(tmp_path,),
    )
    assert plan["quarantine"][0]["reason"] == "pricecharting_product_id_invalid"

    decision["sourceBindings"][1]["externalEntityId"] = "630417"
    retarget_field_receipts(
        decision,
        current_source_code="pricecharting",
        source_code="pricecharting",
        external_entity_id="630417",
    )
    write_content_addressed_json(decision_root, decision)
    connection.sources[1]["external_entity_id"] = "630417"
    connection.sources.append(
        {
            "variant_id": 24,
            "source_code": "snkrdunk",
            "external_entity_id": "s1",
            "match_status": "candidate",
        }
    )
    plan = db_runtime.build_printing_plan(
        connection,  # type: ignore[arg-type]
        decision_root=decision_root,
        qc_root=qc_root,
        quarantine_root=tmp_path / "quarantine",
        allowed_evidence_roots=(tmp_path,),
    )
    assert plan["quarantine"][0]["reason"] == "snkrdunk_conflict_for_v2"


def test_explicit_evidence_cas_can_replace_wrong_canonical_printing(
    tmp_path: Path,
) -> None:
    connection = PrintingConnection()
    connection.variant.update(
        {
            "printing_tcg_code": "one-piece",
            "printing_card_language": "ja",
            "printing_set_name": "Fixture Set",
            "printing_collector_number": "OP05-119",
            "edition_code": "wrong-edition",
            "parallel_code": "wrong-parallel",
            "finish_code": "foil",
            "canonical_printing_sha256": "b" * 64,
            "printing_identity_status": "canonical",
            "printing_evidence_sha256": "c" * 64,
        }
    )
    qc_root = make_qc_bundle(tmp_path)
    decision_root, decision = make_decision(tmp_path)
    decision["supersedesEvidenceSha256"] = "c" * 64
    decision["repairReason"] = "exact source binding corrected"
    write_content_addressed_json(decision_root, decision)

    plan = db_runtime.build_printing_plan(
        connection,  # type: ignore[arg-type]
        decision_root=decision_root,
        qc_root=qc_root,
        quarantine_root=tmp_path / "quarantine",
        allowed_evidence_roots=(tmp_path,),
    )

    assert plan["rows"][0]["operation"] == "replace"
    result = db_runtime.materialize_printing_candidate(
        connection,  # type: ignore[arg-type]
        plan,
        expected_plan_sha256=plan["planSha256"],
        commit=True,
        decision_root=decision_root,
        qc_root=qc_root,
        quarantine_root=tmp_path / "quarantine",
        allowed_evidence_roots=(tmp_path,),
    )
    assert result["changedRows"] == 1


def test_decisions_are_content_addressed_and_evidence_paths_are_private(
    tmp_path: Path,
) -> None:
    qc_root = make_qc_bundle(tmp_path)
    decision_root, _ = make_decision(tmp_path)
    proper = next(decision_root.glob("*.json"))
    proper.rename(decision_root / "mutable-name.json")
    plan = db_runtime.build_printing_plan(
        PrintingConnection(),  # type: ignore[arg-type]
        decision_root=decision_root,
        qc_root=qc_root,
        quarantine_root=tmp_path / "quarantine",
        allowed_evidence_roots=(tmp_path,),
    )
    assert plan["rows"] == []
    assert any(
        item["reason"] == "printing_receipt_not_content_addressed"
        for item in plan["quarantine"]
    )

    other = tmp_path / "outside-evidence"
    qc_root = make_qc_bundle(other)
    decision_root, _ = make_decision(other)
    plan = db_runtime.build_printing_plan(
        PrintingConnection(),  # type: ignore[arg-type]
        decision_root=decision_root,
        qc_root=qc_root,
        quarantine_root=other / "quarantine",
        allowed_evidence_roots=(other / "allowed-only",),
    )
    assert plan["rows"] == []
    assert any(
        item["reason"] == "source_receipt_path_outside_private_evidence_roots"
        for item in plan["quarantine"]
    )


def test_qc_run_path_traversal_and_noncanonical_database_fail_closed(
    tmp_path: Path,
) -> None:
    qc_root = make_qc_bundle(tmp_path)
    with pytest.raises(ValueError, match="canonical_db_qc_run_id_invalid"):
        db_runtime.load_canonical_db_qc_bundle(qc_root=qc_root, qc_run="../qc_test_01")

    receipt_path = qc_root / "qc_test_01" / "receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["counts"] = {}
    write_json(receipt_path, receipt)
    with pytest.raises(
        ValueError, match="canonical_db_qc_bundle_hash_or_contract_invalid"
    ):
        db_runtime.load_canonical_db_qc_bundle(
            qc_root=qc_root, qc_run="qc_test_01"
        )

    connection = PrintingConnection()
    connection.database_name = "cardz_market_cap_clone"
    other = tmp_path / "noncanonical"
    qc_root = make_qc_bundle(other)
    decision_root, _ = make_decision(other)
    with pytest.raises(RuntimeError, match="non-canonical database"):
        db_runtime.build_printing_plan(
            connection,  # type: ignore[arg-type]
            decision_root=decision_root,
            qc_root=qc_root,
            quarantine_root=other / "quarantine",
            allowed_evidence_roots=(other,),
        )


def test_future_dated_qc_authority_fails_closed(tmp_path: Path) -> None:
    qc_root = make_qc_bundle(tmp_path)
    run_root = qc_root / "qc_test_01"
    report_path = run_root / "report.json"
    receipt_path = run_root / "receipt.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    report["asOf"] = "2099-01-01T00:00:00Z"
    receipt["asOf"] = report["asOf"]
    report_bytes = write_json(report_path, report)
    receipt["reportSha256"] = db_runtime.sha256(report_bytes)
    write_json(receipt_path, receipt)

    with pytest.raises(ValueError, match="canonical_db_qc_future_dated"):
        db_runtime.load_canonical_db_qc_bundle(
            qc_root=qc_root, qc_run="qc_test_01"
        )


def test_active_identity_quarantine_blocks_plan(tmp_path: Path) -> None:
    qc_root = make_qc_bundle(tmp_path)
    decision_root, _ = make_decision(tmp_path)
    quarantine_root = tmp_path / "quarantine"
    body = {
        "type": "identity_quarantine",
        "variantId": 24,
        "reason": "multi_snk_unresolved",
    }
    digest = db_runtime.sha256(db_runtime.canonical_json(body))
    write_json(
        quarantine_root / f"{digest}.json",
        {**body, "receiptSha256": digest},
    )

    plan = db_runtime.build_printing_plan(
        PrintingConnection(),  # type: ignore[arg-type]
        decision_root=decision_root,
        qc_root=qc_root,
        quarantine_root=quarantine_root,
        allowed_evidence_roots=(tmp_path,),
    )

    assert plan["rows"] == []
    assert plan["quarantine"][0]["reason"] == "identity_quarantined"


def test_plan_is_deterministic_and_candidate_path_is_immutable(tmp_path: Path) -> None:
    first, connection, qc_root, decision_root, quarantine_root = build_fixture_plan(
        tmp_path
    )
    second = db_runtime.build_printing_plan(
        connection,  # type: ignore[arg-type]
        decision_root=decision_root,
        qc_root=qc_root,
        quarantine_root=quarantine_root,
        allowed_evidence_roots=(tmp_path,),
    )
    assert first == second

    output_root = tmp_path / "candidates"
    first_path = db_runtime.write_printing_candidate(first, output_root)
    second_path = db_runtime.write_printing_candidate(second, output_root)
    assert first_path == second_path
    assert first_path.name == f"printing_{first['planSha256']}.json"


def test_materialize_is_transactional_and_idempotent(tmp_path: Path) -> None:
    plan, connection, qc_root, decision_root, quarantine_root = build_fixture_plan(
        tmp_path
    )
    arguments = {
        "expected_plan_sha256": plan["planSha256"],
        "decision_root": decision_root,
        "qc_root": qc_root,
        "quarantine_root": quarantine_root,
        "allowed_evidence_roots": (tmp_path,),
    }

    first = db_runtime.materialize_printing_candidate(
        connection, plan, commit=True, **arguments  # type: ignore[arg-type]
    )
    second = db_runtime.materialize_printing_candidate(
        connection, plan, commit=True, **arguments  # type: ignore[arg-type]
    )

    assert first["changedRows"] == 1
    assert first["replayedRows"] == 0
    assert second["changedRows"] == 0
    assert second["replayedRows"] == 1
    assert connection.commits == 2
    assert connection.variant["canonical_printing_sha256"] == plan["rows"][0][
        "canonicalPrintingSha256"
    ]
    insert_sql = next(
        query
        for query, _ in connection.calls
        if query.startswith("INSERT INTO catalog_printing_identity")
    )
    assert "canonical_printing_sha256=VALUES(canonical_printing_sha256)" in insert_sql


def test_dry_run_rolls_back_and_reports_would_change(tmp_path: Path) -> None:
    plan, connection, qc_root, decision_root, quarantine_root = build_fixture_plan(
        tmp_path
    )
    result = db_runtime.materialize_printing_candidate(
        connection,  # type: ignore[arg-type]
        plan,
        expected_plan_sha256=plan["planSha256"],
        commit=False,
        decision_root=decision_root,
        qc_root=qc_root,
        quarantine_root=quarantine_root,
        allowed_evidence_roots=(tmp_path,),
    )
    assert result["changedRows"] == 0
    assert result["wouldChangeRows"] == 1
    assert connection.variant["printing_identity_status"] is None
    assert connection.rollbacks >= 1


def test_readback_mismatch_rolls_back_and_releases_all_locks(tmp_path: Path) -> None:
    plan, connection, qc_root, decision_root, quarantine_root = build_fixture_plan(
        tmp_path
    )
    connection.corrupt_readback = True
    with pytest.raises(RuntimeError, match="printing_materialize_readback_mismatch"):
        db_runtime.materialize_printing_candidate(
            connection,  # type: ignore[arg-type]
            plan,
            expected_plan_sha256=plan["planSha256"],
            commit=True,
            decision_root=decision_root,
            qc_root=qc_root,
            quarantine_root=quarantine_root,
            allowed_evidence_roots=(tmp_path,),
        )
    assert connection.commits == 0
    assert connection.rollbacks == 1
    assert connection.variant["printing_identity_status"] is None
    releases = [
        query for query, _ in connection.calls if query.startswith("SELECT RELEASE_LOCK")
    ]
    assert len(releases) == len(db_runtime.PRINTING_LOCKS)


def test_existing_printing_hash_owner_rolls_back(tmp_path: Path) -> None:
    plan, connection, qc_root, decision_root, quarantine_root = build_fixture_plan(
        tmp_path
    )
    connection.duplicate_owner = True
    with pytest.raises(RuntimeError, match="duplicate_printing_hash"):
        db_runtime.materialize_printing_candidate(
            connection,  # type: ignore[arg-type]
            plan,
            expected_plan_sha256=plan["planSha256"],
            commit=True,
            decision_root=decision_root,
            qc_root=qc_root,
            quarantine_root=quarantine_root,
            allowed_evidence_roots=(tmp_path,),
        )
    assert connection.commits == 0
    assert connection.rollbacks == 1
    assert connection.variant["printing_identity_status"] is None


def test_apply_refuses_zero_approved_rows(tmp_path: Path) -> None:
    qc_root = make_qc_bundle(tmp_path)
    decision_root = tmp_path / "empty-decisions"
    decision_root.mkdir()
    quarantine_root = tmp_path / "quarantine"
    connection = PrintingConnection()
    plan = db_runtime.build_printing_plan(
        connection,  # type: ignore[arg-type]
        decision_root=decision_root,
        qc_root=qc_root,
        quarantine_root=quarantine_root,
        allowed_evidence_roots=(tmp_path,),
    )
    assert plan["status"] == "blocked"
    assert plan["counts"]["approvedRows"] == 0
    with pytest.raises(ValueError, match="no_approved_printing_rows"):
        db_runtime.materialize_printing_candidate(
            connection,  # type: ignore[arg-type]
            plan,
            expected_plan_sha256=plan["planSha256"],
            commit=True,
            decision_root=decision_root,
            qc_root=qc_root,
            quarantine_root=quarantine_root,
            allowed_evidence_roots=(tmp_path,),
        )


def test_materialize_rejects_duplicate_candidate_rows_even_with_resealed_plan(
    tmp_path: Path,
) -> None:
    plan, connection, qc_root, decision_root, quarantine_root = build_fixture_plan(
        tmp_path
    )
    tampered = copy.deepcopy(plan)
    tampered["rows"].append(copy.deepcopy(tampered["rows"][0]))
    tampered["counts"]["approvedRows"] = 2
    tampered["planSha256"] = db_runtime.printing_plan_sha256(tampered)
    with pytest.raises(ValueError, match="stale_plan"):
        db_runtime.materialize_printing_candidate(
            connection,  # type: ignore[arg-type]
            tampered,
            expected_plan_sha256=tampered["planSha256"],
            commit=True,
            decision_root=decision_root,
            qc_root=qc_root,
            quarantine_root=quarantine_root,
            allowed_evidence_roots=(tmp_path,),
        )


def test_apply_revalidates_latest_qc_and_decision_manifest(tmp_path: Path) -> None:
    plan, connection, qc_root, decision_root, quarantine_root = build_fixture_plan(
        tmp_path
    )
    make_qc_bundle(tmp_path, run_id="qc_test_02")
    with pytest.raises(ValueError, match="stale_plan:qc_authority"):
        db_runtime.materialize_printing_candidate(
            connection,  # type: ignore[arg-type]
            plan,
            expected_plan_sha256=plan["planSha256"],
            commit=True,
            decision_root=decision_root,
            qc_root=qc_root,
            quarantine_root=quarantine_root,
            allowed_evidence_roots=(tmp_path,),
        )

    # Rebuild with the original bundle as the newest authority, then drift the
    # decision directory after the immutable plan is written.
    other = tmp_path / "manifest-drift"
    plan, connection, qc_root, decision_root, quarantine_root = build_fixture_plan(other)
    write_json(decision_root / "unrelated.json", {"variantId": 999})
    with pytest.raises(ValueError, match="stale_plan:decision_manifest"):
        db_runtime.materialize_printing_candidate(
            connection,  # type: ignore[arg-type]
            plan,
            expected_plan_sha256=plan["planSha256"],
            commit=True,
            decision_root=decision_root,
            qc_root=qc_root,
            quarantine_root=quarantine_root,
            allowed_evidence_roots=(other,),
        )


def test_apply_revalidates_card_evidence_and_active_quarantine(tmp_path: Path) -> None:
    plan, connection, qc_root, decision_root, quarantine_root = build_fixture_plan(
        tmp_path
    )
    report_path = qc_root / "qc_test_01" / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["cards"][0]["evidenceSha256"] = "b" * 64
    report_bytes = write_json(report_path, report)
    receipt_path = report_path.with_name("receipt.json")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["reportSha256"] = db_runtime.sha256(report_bytes)
    write_json(receipt_path, receipt)
    with pytest.raises(ValueError, match="stale_plan:qc_authority"):
        db_runtime.materialize_printing_candidate(
            connection,  # type: ignore[arg-type]
            plan,
            expected_plan_sha256=plan["planSha256"],
            commit=True,
            decision_root=decision_root,
            qc_root=qc_root,
            quarantine_root=quarantine_root,
            allowed_evidence_roots=(tmp_path,),
        )

    other = tmp_path / "quarantine-drift"
    plan, connection, qc_root, decision_root, quarantine_root = build_fixture_plan(other)
    body = {
        "type": "identity_quarantine",
        "variantId": 24,
        "reason": "multi_snk_unresolved",
    }
    digest = db_runtime.sha256(db_runtime.canonical_json(body))
    write_json(
        quarantine_root / f"{digest}.json",
        {**body, "receiptSha256": digest},
    )
    with pytest.raises(
        ValueError, match="stale_plan:identity_quarantine_manifest"
    ):
        db_runtime.materialize_printing_candidate(
            connection,  # type: ignore[arg-type]
            plan,
            expected_plan_sha256=plan["planSha256"],
            commit=True,
            decision_root=decision_root,
            qc_root=qc_root,
            quarantine_root=quarantine_root,
            allowed_evidence_roots=(other,),
        )
    assert connection.variant["printing_identity_status"] is None
