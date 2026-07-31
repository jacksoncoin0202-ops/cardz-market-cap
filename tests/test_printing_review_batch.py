from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from printing_review_batch import (  # noqa: E402
    build_worksheet,
    current_approval_variants,
    select_batch_cards,
)
from printing_agent_fill_pass import explicit_source_language  # noqa: E402
import printing_review_batch  # noqa: E402


def _receipt(**overrides: object) -> dict[str, object]:
    return {
        "type": "canonical_printing_approval",
        "schemaVersion": 1,
        "decision": "approve",
        "qcRunId": "qc_current",
        "opaqueId": "cmc_current",
        "cardEvidenceSha256": "a" * 64,
        **overrides,
    }


def test_current_approval_variants_reopens_stale_receipts() -> None:
    cards = [
        {"variantId": 1, "id": "cmc_current", "evidenceSha256": "a" * 64},
        {"variantId": 2, "id": "cmc_new", "evidenceSha256": "b" * 64},
        {"variantId": 3, "id": "cmc_evidence", "evidenceSha256": "c" * 64},
        {"variantId": 4, "id": "cmc_nonapproval", "evidenceSha256": "d" * 64},
    ]
    decisions = {
        1: [{"receipt": _receipt()}],
        2: [{"receipt": _receipt(opaqueId="cmc_old")}],
        3: [{"receipt": _receipt(cardEvidenceSha256="e" * 64)}],
        4: [{"receipt": _receipt(decision="reject")}],
    }

    assert current_approval_variants(cards, decisions, qc_run="qc_current") == {1}


def test_current_approval_variants_reopens_other_qc_run() -> None:
    cards = [{"variantId": 5, "id": "cmc_current", "evidenceSha256": "a" * 64}]
    decisions = {
        5: [{"receipt": _receipt(qcRunId="qc_previous")}],
    }

    assert current_approval_variants(cards, decisions, qc_run="qc_current") == set()


def test_select_batch_cards_excludes_non_printing_blockers() -> None:
    cards = [
        {
            "variantId": 1,
            "marketRank": 2,
            "blockers": ["image_not_human_or_vision_confirmed"],
        },
        {
            "variantId": 2,
            "marketRank": 1,
            "blockers": ["canonical_printing_missing"],
        },
    ]

    selected = select_batch_cards(cards, limit=0, skip_variants=set())

    assert [card["variantId"] for card in selected] == [2]

    forced = select_batch_cards(
        cards,
        limit=0,
        skip_variants=set(),
        require_printing_blocker=False,
    )
    assert [card["variantId"] for card in forced] == [2, 1]


def test_zero_limit_means_unbounded_batch(monkeypatch) -> None:
    cards = [
        {
            "variantId": 1,
            "id": "card-1",
            "evidenceSha256": "sha-1",
            "blockers": ["canonical_printing_missing"],
            "facts": {"identity": {"name": "Card 1"}},
        }
    ]
    variant = {
        "variant_id": 1,
        "opaque_id": "card-1",
        "canonical_name": "Card 1",
        "tcg_code": "pokemon",
        "card_language": "ja",
        "set_name": "Set",
        "collector_number": "1",
    }
    sources = {
        1: [
            {
                "source_code": "gemrate",
                "external_entity_id": "g1",
                "match_status": "exact",
            },
            {
                "source_code": "snkrdunk",
                "external_entity_id": "s1",
                "match_status": "exact",
            },
        ]
    }

    monkeypatch.setattr(
        "printing_review_batch.load_canonical_db_qc_bundle",
        lambda **_: {
            "cards": cards,
            "runId": "qc_current",
            "reportSha256": "report",
            "receiptSha256": "receipt",
            "asOf": "2026-07-31T00:00:00Z",
        },
    )
    monkeypatch.setattr(
        "printing_review_batch.load_identity_quarantine_bundle",
        lambda *_: (set(), [], "manifest"),
    )
    monkeypatch.setattr(
        "printing_review_batch.load_printing_decisions",
        lambda *_: ({}, [], "manifest"),
    )
    monkeypatch.setattr(
        "printing_review_batch.load_printing_db_state",
        lambda _cur, _ids: ({1: variant}, sources),
    )

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, *_args):
            return None

        def fetchall(self):
            return [{"id": 1, "canonical_name": "Card 1"}]

    class Connection:
        def cursor(self):
            return Cursor()

        def close(self):
            return None

    monkeypatch.setattr("printing_review_batch.db", Connection)

    result = __import__("printing_review_batch").cmd_open(
        qc_run="qc_current", limit=0, write=False
    )

    assert result["counts"]["worksheets"] == 1
    assert result["counts"]["bindingReady"] == 1


def test_open_can_filter_exact_variant_ids(monkeypatch) -> None:
    cards = [
        {"variantId": 1, "blockers": ["canonical_printing_missing"]},
        {"variantId": 2, "blockers": ["canonical_printing_missing"]},
    ]
    monkeypatch.setattr(
        "printing_review_batch.load_canonical_db_qc_bundle",
        lambda **_: {
            "cards": cards,
            "runId": "qc_current",
            "reportSha256": "report",
            "receiptSha256": "receipt",
            "asOf": "2026-07-31T00:00:00Z",
        },
    )
    monkeypatch.setattr(
        "printing_review_batch.load_identity_quarantine_bundle",
        lambda *_: (set(), [], "manifest"),
    )
    monkeypatch.setattr(
        "printing_review_batch.load_printing_decisions",
        lambda *_: ({}, [], "manifest"),
    )
    monkeypatch.setattr(
        "printing_review_batch.load_printing_db_state",
        lambda _cur, ids: (
            {
                variant_id: {
                    "variant_id": variant_id,
                    "opaque_id": f"card-{variant_id}",
                    "tcg_code": "pokemon",
                    "card_language": "en",
                    "set_name": "Set",
                    "collector_number": str(variant_id),
                }
                for variant_id in ids
            },
            {},
        ),
    )

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, *_args):
            return None

        def fetchall(self):
            return []

    class Connection:
        def cursor(self):
            return Cursor()

        def close(self):
            return None

    monkeypatch.setattr("printing_review_batch.db", Connection)

    result = __import__("printing_review_batch").cmd_open(
        qc_run="qc_current",
        limit=0,
        write=False,
        variant_ids={2},
    )

    assert [row["variantId"] for row in result["worksheets"]] == [2]


def test_worksheet_opens_authoritative_language_as_unsealed_draft() -> None:
    worksheet = build_worksheet(
        {"id": "cmc_1", "evidenceSha256": "a" * 64},
        variant={
            "variant_id": 1,
            "opaque_id": "cmc_1",
            "tcg_code": "pokemon",
            "card_language": "ja",
            "set_name": "SV2a",
            "collector_number": "201/165",
        },
        sources=[],
        qc_run_id="qc_current",
    )

    language = worksheet["fields"]["cardLanguage"]
    assert language["rawValue"] == "ja"
    assert language["normalizedValue"] == "ja"
    assert language["status"] == "draft_base"
    assert language["evidenceType"] == ""


def test_worksheet_selects_pc_v2_only_without_snk_conflict() -> None:
    card = {"id": "cmc_1", "evidenceSha256": "a" * 64}
    variant = {
        "variant_id": 1,
        "opaque_id": "cmc_1",
        "tcg_code": "pokemon",
        "card_language": "ja",
        "set_name": "SV2a",
        "collector_number": "201/165",
    }
    sources = [
        {"source_code": "gemrate", "external_entity_id": "g1", "match_status": "exact"},
        {"source_code": "pricecharting", "external_entity_id": "630417", "match_status": "exact"},
    ]

    worksheet = build_worksheet(card, variant=variant, sources=sources, qc_run_id="qc")

    assert worksheet["policyVersion"] == "canonical-printing-v2-pc"
    assert worksheet["bindingReady"] is True
    assert [item["sourceCode"] for item in worksheet["sourceBindings"]] == [
        "gemrate",
        "pricecharting",
    ]

    conflicted = build_worksheet(
        card,
        variant=variant,
        sources=[*sources, {"source_code": "snkrdunk", "external_entity_id": "s1", "match_status": "candidate"}],
        qc_run_id="qc",
    )
    assert conflicted["policyVersion"] == "canonical-printing-v1"
    assert conflicted["bindingReady"] is False


def test_worksheet_does_not_open_v2_for_non_product_id() -> None:
    worksheet = build_worksheet(
        {"id": "cmc_1", "evidenceSha256": "a" * 64},
        variant={
            "variant_id": 1,
            "opaque_id": "cmc_1",
            "tcg_code": "pokemon",
            "card_language": "ja",
            "set_name": "SV2a",
            "collector_number": "201/165",
        },
        sources=[
            {"source_code": "gemrate", "external_entity_id": "g1", "match_status": "exact"},
            {"source_code": "pricecharting", "external_entity_id": "slug-not-id", "match_status": "exact"},
        ],
        qc_run_id="qc",
    )
    assert worksheet["bindingReady"] is False


def test_agent_language_fill_requires_explicit_source_field() -> None:
    assert explicit_source_language({"name": "Japanese Charizard"}) is None
    assert explicit_source_language({"language": "Japanese"}) == ("Japanese", "ja")


def test_open_rejects_root_outside_repository(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="qc_root_outside_repository"):
        printing_review_batch.cmd_open(
            qc_run="qc_current",
            limit=1,
            write=False,
            qc_root=tmp_path,
        )


def test_open_records_contained_roots_and_readback(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(printing_review_batch, "ROOT", tmp_path)
    roots = {
        "qc": tmp_path / "qc",
        "decisions": tmp_path / "decisions",
        "batches": tmp_path / "batches",
    }
    captured = {}
    def load_qc(**kwargs):
        captured["qcRoot"] = kwargs["qc_root"]
        return {
            "cards": [], "runId": "qc_current", "reportSha256": "report",
            "receiptSha256": "receipt", "asOf": "2026-07-31T00:00:00Z",
        }

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, *_args):
            return None

        def fetchall(self):
            return []

    class Connection:
        def cursor(self):
            return Cursor()

        def close(self):
            return None

    monkeypatch.setattr(printing_review_batch, "load_canonical_db_qc_bundle", load_qc)
    monkeypatch.setattr(printing_review_batch, "load_identity_quarantine_bundle", lambda *_: (set(), "q", {}))
    monkeypatch.setattr(printing_review_batch, "load_printing_decisions", lambda root: ({}, [], "manifest"))
    monkeypatch.setattr(printing_review_batch, "db", Connection)

    result = printing_review_batch.cmd_open(
        qc_run="qc_current", limit=1, write=True,
        qc_root=roots["qc"], decision_root=roots["decisions"], batch_root=roots["batches"],
    )

    index_path = next(roots["batches"].glob("batch_*/index.json"))
    persisted = __import__("json").loads(index_path.read_text(encoding="utf-8"))
    assert captured["qcRoot"] == roots["qc"].resolve()
    assert result["roots"] == persisted["roots"]
    assert persisted["decisionManifestSha256"] == "manifest"
    assert persisted["worksheetManifestSha256"] == printing_review_batch.sha256(
        printing_review_batch.canonical_json([])
    )
