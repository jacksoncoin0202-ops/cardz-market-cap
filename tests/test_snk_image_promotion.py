# -*- coding: utf-8 -*-
"""Regression tests for SNK image promotion hard gates."""
from __future__ import annotations

import json
import hashlib
import sys
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import snk_image_promotion as promo  # noqa: E402


def _queue_item(**over):
    base = {
        "variantId": 24,
        "opaqueId": "cmc_test",
        "snkId": "135438",
        "assetId": 100,
        "contentSha256": "a" * 64,
        "rawSha256": "b" * 64,
        "sourcePath": "snkrdunk:135438:https://cdn.example/x.webp",
        "canonicalPrintingSha256": "c" * 64,
        "qcRunId": "qc_20260729_full_03",
        "qcReportSha256": "e" * 64,
        "qcReceiptSha256": "f" * 64,
        "cardEvidenceSha256": "d" * 64,
        "status": "pending",
        "marketRank": 10,
        "tcg": "one-piece",
        "width": 429,
        "height": 600,
    }
    base.update(over)
    base["queueFingerprint"] = promo.compute_queue_fingerprint(base)
    return base


def _live_from_queue(q: dict, **over):
    live = {
        "snkIds": [q["snkId"]],
        "assetId": q["assetId"],
        "contentSha256": q["contentSha256"],
        "rawSha256": q["rawSha256"],
        "sourcePath": q["sourcePath"],
        "canonicalPrintingSha256": q["canonicalPrintingSha256"],
    }
    live.update(over)
    return live


class TestFingerprint:
    def test_stable(self):
        q = _queue_item()
        assert promo.compute_queue_fingerprint(q) == q["queueFingerprint"]

    def test_changes_when_printing_changes(self):
        q = _queue_item()
        q2 = dict(q)
        q2["canonicalPrintingSha256"] = "e" * 64
        assert promo.compute_queue_fingerprint(q2) != q["queueFingerprint"]

    def test_changes_when_qc_run_changes(self):
        q = _queue_item()
        q2 = dict(q)
        q2["qcRunId"] = "qc_other"
        assert promo.compute_queue_fingerprint(q2) != q["queueFingerprint"]

    def test_changes_when_qc_bundle_hash_changes(self):
        q = _queue_item()
        for field in ("qcReportSha256", "qcReceiptSha256"):
            q2 = dict(q)
            q2[field] = "0" * 64
            assert promo.compute_queue_fingerprint(q2) != q["queueFingerprint"]


class TestQuickWin:
    def test_image_only(self):
        assert promo.is_quick_win_card(["image_not_human_or_vision_confirmed"])

    def test_image_plus_printing(self):
        assert promo.is_quick_win_card(
            ["image_not_human_or_vision_confirmed", "canonical_printing_missing"]
        )

    def test_rejects_sales_blocker(self):
        assert not promo.is_quick_win_card(
            [
                "image_not_human_or_vision_confirmed",
                "psa10_sales_30d_missing",
            ]
        )

    def test_requires_image_blocker(self):
        assert not promo.is_quick_win_card(["canonical_printing_missing"])


class TestSelectRanked:
    def test_sorts_by_market_rank(self):
        cards = [
            {
                "variantId": 2,
                "marketRank": 50,
                "tcg": "one-piece",
                "blockers": ["image_not_human_or_vision_confirmed"],
            },
            {
                "variantId": 1,
                "marketRank": 3,
                "tcg": "one-piece",
                "blockers": [
                    "image_not_human_or_vision_confirmed",
                    "canonical_printing_missing",
                ],
            },
            {
                "variantId": 9,
                "marketRank": 1,
                "tcg": "pokemon",
                "blockers": ["image_not_human_or_vision_confirmed"],
            },
        ]
        out = promo.select_ranked_image_queue(cards, limit=10, tcg="one-piece")
        assert [c["variantId"] for c in out] == [1, 2]

    def test_limit(self):
        cards = [
            {
                "variantId": i,
                "marketRank": i,
                "tcg": "one-piece",
                "blockers": ["image_not_human_or_vision_confirmed"],
            }
            for i in range(1, 20)
        ]
        out = promo.select_ranked_image_queue(cards, limit=5, tcg="one-piece")
        assert len(out) == 5

    def test_operator_alias_op_is_canonicalized(self):
        cards = [
            {
                "variantId": 1,
                "marketRank": 1,
                "tcg": "one-piece",
                "blockers": ["image_not_human_or_vision_confirmed"],
            }
        ]
        assert promo.select_ranked_image_queue(cards, limit=5, tcg="OP") == cards

    def test_unknown_tcg_filter_is_rejected(self):
        with pytest.raises(promo.PromotionGateError, match="unsupported_tcg_filter"):
            promo.select_ranked_image_queue([], limit=5, tcg="made-up")


class TestVerifyLive:
    def test_ok(self):
        q = _queue_item()
        live = _live_from_queue(q)
        promo.verify_live_against_queue(q, live)

    def test_multi_snk(self):
        q = _queue_item()
        live = _live_from_queue(q, snkIds=["1", "2"])
        with pytest.raises(promo.PromotionGateError, match="multi_or_zero_snk"):
            promo.verify_live_against_queue(q, live)

    def test_snk_drift(self):
        q = _queue_item()
        live = _live_from_queue(q, snkIds=["999"])
        with pytest.raises(promo.PromotionGateError, match="snk_id_drift"):
            promo.verify_live_against_queue(q, live)

    def test_content_drift(self):
        q = _queue_item()
        live = _live_from_queue(q, contentSha256="f" * 64)
        with pytest.raises(promo.PromotionGateError, match="content_sha_drift"):
            promo.verify_live_against_queue(q, live)

    def test_printing_missing(self):
        q = _queue_item(canonicalPrintingSha256="")
        # recompute fp with empty printing
        q["queueFingerprint"] = promo.compute_queue_fingerprint(q)
        live = _live_from_queue(q, canonicalPrintingSha256="")
        with pytest.raises(promo.PromotionGateError, match="canonical_printing_missing"):
            promo.verify_live_against_queue(q, live)

    def test_printing_drift(self):
        q = _queue_item()
        live = _live_from_queue(q, canonicalPrintingSha256="e" * 64)
        with pytest.raises(promo.PromotionGateError, match="printing_hash_drift"):
            promo.verify_live_against_queue(q, live)

    def test_fingerprint_stale_on_path(self):
        q = _queue_item()
        live = _live_from_queue(q, sourcePath="other")
        with pytest.raises(promo.PromotionGateError, match="source_path_drift"):
            promo.verify_live_against_queue(q, live)

    def test_not_pending(self):
        q = _queue_item(status="approved")
        live = _live_from_queue(q)
        with pytest.raises(promo.PromotionGateError, match="queue_item_not_pending"):
            promo.verify_live_against_queue(q, live)


class TestApproveNoBypass:
    def test_not_in_queue_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr(promo, "QUEUE_PATH", tmp_path / "queue.jsonl")
        monkeypatch.setattr(promo, "REVIEW_DIR", tmp_path)
        # empty queue
        class FakeCur:
            pass

        with pytest.raises(promo.PromotionGateError, match="not_in_pending_queue"):
            promo.cmd_approve(
                FakeCur(),
                variant_id=24,
                operator="human",
                note="",
                write=False,
                snk_id=None,
            )


class TestPublicImagePolicy:
    def test_requires_responsive_derivatives_before_public_write(self, tmp_path, monkeypatch):
        q = _queue_item(languageMatchEvidence=True)
        live = _live_from_queue(q, tcg="one-piece", width=429, height=600)
        asset = tmp_path / f"{q['contentSha256']}.webp"
        Image.new("RGB", (429, 600), (255, 255, 255)).save(asset, "WEBP")
        monkeypatch.setattr(promo, "inspect_path", lambda _path: {"status": "passed"})
        with pytest.raises(promo.PromotionGateError, match="public_derivative_missing:200"):
            promo.assert_public_image_policy(q, live=live, asset_path=asset)

        Image.new("RGB", (200, 280), (255, 255, 255)).save(
            tmp_path / f"{q['contentSha256']}_200.webp", "WEBP"
        )
        with pytest.raises(promo.PromotionGateError, match="public_derivative_missing:600"):
            promo.assert_public_image_policy(q, live=live, asset_path=asset)

        Image.new("RGB", (429, 600), (255, 255, 255)).save(
            tmp_path / f"{q['contentSha256']}_600.webp", "WEBP"
        )
        promo.assert_public_image_policy(q, live=live, asset_path=asset)

    def test_requires_explicit_language_evidence_before_any_public_write(self, tmp_path):
        q = _queue_item()
        live = _live_from_queue(q, tcg="one-piece", width=429, height=600)
        with pytest.raises(promo.PromotionGateError, match="source_language_evidence_missing"):
            promo.assert_public_image_policy(q, live=live, asset_path=tmp_path / "missing.webp")

    def test_rejects_known_bad_content_before_public_write(self, tmp_path):
        q = _queue_item(languageMatchEvidence=True)
        live = _live_from_queue(
            q,
            contentSha256="4a53529faf845d82b7c06ab04e9fa161792b2d2d9c1e9bee01b594cfef3895a6",
            tcg="one-piece",
            width=429,
            height=600,
        )
        with pytest.raises(promo.PromotionGateError, match="content_policy_rejected"):
            promo.assert_public_image_policy(q, live=live, asset_path=tmp_path / "missing.webp")

    def test_rejects_limitless_family_before_public_write(self, tmp_path):
        q = _queue_item(languageMatchEvidence=True)
        live = _live_from_queue(
            q,
            sourcePath="https://limitlesstcg.nyc3.cdn.digitaloceanspaces.com/one-piece/OP01_EN.webp",
            tcg="one-piece",
            width=429,
            height=600,
        )
        with pytest.raises(promo.PromotionGateError, match="source_policy_rejected"):
            promo.assert_public_image_policy(q, live=live, asset_path=tmp_path / "missing.webp")

    def test_rejects_geometry_failure_before_public_write(self, tmp_path):
        q = _queue_item(languageMatchEvidence=True)
        live = _live_from_queue(q, tcg="one-piece", width=429, height=600)
        asset = tmp_path / "bad.webp"
        Image.new("RGBA", (20, 20), (0, 0, 0, 0)).save(asset, "WEBP")
        with pytest.raises(promo.PromotionGateError, match="image_geometry_failed"):
            promo.assert_public_image_policy(q, live=live, asset_path=asset)


class TestReceipt:
    def test_binds_printing_and_qc_run(self):
        q = _queue_item()
        r = promo.build_approval_receipt(q, operator="human", note="ok", approved_at="t")
        assert r["canonicalPrintingSha256"] == q["canonicalPrintingSha256"]
        assert r["qcRunId"] == q["qcRunId"]
        assert r["qcReportSha256"] == q["qcReportSha256"]
        assert r["qcReceiptSha256"] == q["qcReceiptSha256"]
        assert r["queueFingerprint"] == q["queueFingerprint"]
        assert len(r["receiptSha256"]) == 64
        # body hash excludes nested self — receiptSha256 set after
        body = dict(r)
        del body["receiptSha256"]
        assert promo.sha256_obj(body) == r["receiptSha256"]


class TestEnrollDeprecated:
    def test_legacy_enroll_exits(self, monkeypatch):
        # ensure enroll-from-qc path is preferred in CLI; pure function smoke
        cards = [
            {
                "variantId": 1,
                "marketRank": 1,
                "tcg": "one-piece",
                "blockers": [
                    "image_not_human_or_vision_confirmed",
                    "psa10_sales_30d_missing",
                ],
            }
        ]
        # not quick-win
        assert promo.select_ranked_image_queue(cards, limit=10, quick_win_only=True) == []


class TestQcBundle:
    @staticmethod
    def _write_bundle(root: Path) -> Path:
        run = root / "qc_test"
        run.mkdir()
        report = {
            "runId": "qc_test",
            "asOf": "2026-07-29T00:00:00Z",
            "status": "blocked",
            "counts": {"qualified": 1},
            "releaseGate": {"eligible": False},
            "cards": [],
        }
        report_path = run / "report.json"
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        receipt = {
            "runId": "qc_test",
            "asOf": report["asOf"],
            "status": "blocked",
            "database": "cardz_market_cap",
            "readOnly": True,
            "counts": report["counts"],
            "releaseGate": report["releaseGate"],
            "reportSha256": promo.sha256_file(report_path),
        }
        (run / "receipt.json").write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return run

    def test_binds_actual_report_and_receipt_file_hashes(self, tmp_path):
        run = self._write_bundle(tmp_path)
        receipt, report, report_sha, receipt_sha = promo.load_qc_bundle(run)
        assert receipt["runId"] == report["runId"] == "qc_test"
        assert report_sha == promo.sha256_file(run / "report.json")
        assert receipt_sha == promo.sha256_file(run / "receipt.json")

    def test_tampered_report_is_rejected(self, tmp_path):
        run = self._write_bundle(tmp_path)
        report_path = run / "report.json"
        report_path.write_text(
            report_path.read_text(encoding="utf-8").replace(
                '"qualified": 1', '"qualified": 999'
            ),
            encoding="utf-8",
        )
        with pytest.raises(
            promo.PromotionGateError, match="qc_bundle_hash_or_contract_invalid"
        ):
            promo.load_qc_bundle(run)


class TestIdentityQuarantine:
    @staticmethod
    def _receipt(variant_id: int) -> dict:
        body = {
            "type": "identity_quarantine",
            "reason": "multi_snk_identity",
            "variantId": variant_id,
        }
        body["receiptSha256"] = promo.sha256_obj(body)
        return body

    def test_active_receipt_blocks_promotion(self, tmp_path, monkeypatch):
        monkeypatch.setattr(promo, "IDENTITY_RECEIPT_DIR", tmp_path)
        receipt = self._receipt(24)
        (tmp_path / f"{receipt['receiptSha256']}.json").write_text(
            json.dumps(receipt), encoding="utf-8"
        )
        with pytest.raises(promo.PromotionGateError, match="identity_quarantined"):
            promo.assert_not_quarantined(24)

    def test_invalid_receipt_fails_closed(self, tmp_path, monkeypatch):
        monkeypatch.setattr(promo, "IDENTITY_RECEIPT_DIR", tmp_path)
        (tmp_path / f"{'a' * 64}.json").write_text(
            json.dumps(
                {
                    "type": "identity_quarantine",
                    "reason": "bad",
                    "variantId": 24,
                    "receiptSha256": "a" * 64,
                }
            ),
            encoding="utf-8",
        )
        with pytest.raises(
            promo.PromotionGateError, match="identity_quarantine_receipt_invalid"
        ):
            promo.load_active_identity_quarantine()

    def test_valid_resolution_preserves_evidence_and_unblocks_selected_snk(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(promo, "IDENTITY_RECEIPT_DIR", tmp_path)
        quarantine = self._receipt(24)
        quarantine["snkIds"] = ["snk-old", "snk-selected"]
        quarantine_body = dict(quarantine)
        quarantine_body.pop("receiptSha256")
        quarantine["receiptSha256"] = promo.sha256_obj(quarantine_body)
        (tmp_path / f"{quarantine['receiptSha256']}.json").write_text(
            json.dumps(quarantine), encoding="utf-8"
        )
        resolution_body = {
            "type": "identity_quarantine_resolution",
            "schemaVersion": 1,
            "variantId": 24,
            "supersedesReceiptSha256": quarantine["receiptSha256"],
            "selectedSnkId": "snk-selected",
            "preSourceIdentitySha256": promo.sha256_obj(
                {
                    "variantId": 24,
                    "snkIds": ["snk-old", "snk-selected"],
                }
            ),
            "postSourceIdentitySha256": "b" * 64,
            "actor": "fixture-reviewer",
            "resolvedAt": "2026-07-29T00:00:00Z",
            "policyVersion": "identity-quarantine-resolution-v1",
        }
        resolution = {
            **resolution_body,
            "receiptSha256": promo.sha256_obj(resolution_body),
        }
        (tmp_path / f"{resolution['receiptSha256']}.json").write_text(
            json.dumps(resolution), encoding="utf-8"
        )

        promo.assert_not_quarantined(24, snk_id="snk-selected")
        with pytest.raises(
            promo.PromotionGateError,
            match="identity_quarantine_resolution_drift",
        ):
            promo.assert_not_quarantined(24, snk_id="snk-other")

    def test_unknown_resolution_fails_closed(self, tmp_path, monkeypatch):
        monkeypatch.setattr(promo, "IDENTITY_RECEIPT_DIR", tmp_path)
        body = {
            "type": "identity_quarantine_resolution",
            "schemaVersion": 1,
            "variantId": 24,
            "supersedesReceiptSha256": "a" * 64,
            "selectedSnkId": "snk-selected",
            "preSourceIdentitySha256": "b" * 64,
            "postSourceIdentitySha256": "c" * 64,
            "actor": "fixture-reviewer",
            "resolvedAt": "2026-07-29T00:00:00Z",
            "policyVersion": "identity-quarantine-resolution-v1",
        }
        receipt = {**body, "receiptSha256": promo.sha256_obj(body)}
        (tmp_path / f"{receipt['receiptSha256']}.json").write_text(
            json.dumps(receipt), encoding="utf-8"
        )
        with pytest.raises(
            promo.PromotionGateError,
            match="identity_quarantine_resolution_unknown_or_mismatched",
        ):
            promo.load_identity_quarantine_state()


class TestCanonicalPrinting:
    def test_requires_complete_non_placeholder_tuple_and_exact_hash(self):
        row = {
            "printing_status": "canonical",
            "printing_tcg_code": "one-piece",
            "printing_set_name": "OP-09",
            "printing_collector_number": "OP09-051",
            "edition_code": "first-edition",
            "parallel_code": "alternate-art",
            "finish_code": "foil",
        }
        key = "|".join(
            str(row[field]).casefold()
            for field in (
                "printing_tcg_code",
                "printing_set_name",
                "printing_collector_number",
                "edition_code",
                "parallel_code",
                "finish_code",
            )
        )
        row["canonical_printing_sha256"] = hashlib.sha256(key.encode("utf-8")).hexdigest()
        assert promo.canonical_printing_sha256(row) == row["canonical_printing_sha256"]

        incomplete = dict(row, finish_code="")
        assert promo.canonical_printing_sha256(incomplete) is None
        placeholder = dict(row, finish_code="unknown")
        assert promo.canonical_printing_sha256(placeholder) is None
        wrong_hash = dict(row, canonical_printing_sha256="0" * 64)
        assert promo.canonical_printing_sha256(wrong_hash) is None


class TestManifestUpsert:
    def test_replaces_all_duplicate_records_for_target_public_id(self):
        old = {"publicId": "cmc_a", "imageKind": "raw_front", "contentSha256": "old"}
        other = {"publicId": "cmc_b", "imageKind": "raw_front", "contentSha256": "b"}
        new = {"publicId": "cmc_a", "imageKind": "raw_front", "contentSha256": "new"}
        out = promo.build_manifest_document({"records": [old, other, old]}, new)
        target = [
            row
            for row in out["records"]
            if row["publicId"] == "cmc_a" and row["imageKind"] == "raw_front"
        ]
        assert target == [new]
        assert other in out["records"]

    def test_removes_only_target_raw_front_records(self):
        document = {
            "records": [
                {"publicId": "cmc_a", "imageKind": "raw_front"},
                {"publicId": "cmc_a", "imageKind": "thumbnail"},
                {"publicId": "cmc_b", "imageKind": "raw_front"},
            ]
        }
        out = promo.build_manifest_without_public_ids(document, {"cmc_a"})
        assert out["records"] == [
            {"publicId": "cmc_a", "imageKind": "thumbnail"},
            {"publicId": "cmc_b", "imageKind": "raw_front"},
        ]


class TestRejectQueue:
    class Cursor:
        def __init__(self):
            self.calls = []

        def execute(self, query, args=None):
            self.calls.append((" ".join(query.split()), args))

    def test_db_changes_are_precommit_and_queue_is_postcommit_artifact(
        self, monkeypatch
    ):
        queue = [
            {
                "variantId": 24,
                "opaqueId": "cmc_a",
                "status": "pending",
            }
        ]
        cursor = self.Cursor()
        monkeypatch.setattr(promo, "read_queue", lambda: queue)
        monkeypatch.setattr(
            promo,
            "write_queue",
            lambda _rows: (_ for _ in ()).throw(AssertionError("precommit file write")),
        )
        out = promo.cmd_reject_queue(
            variant_id=24,
            reason="wrong card",
            write=True,
            cur=cursor,
        )
        assert out["rejected"] == 1
        assert out["_artifacts"]["queue"][0]["status"] == "rejected"
        assert out["_artifacts"]["publicIds"] == ["cmc_a"]
        assert len(cursor.calls) == 2
        assert all("public_allowed=0" in query for query, _ in cursor.calls)

    def test_reject_requires_pending_queue(self, monkeypatch):
        monkeypatch.setattr(promo, "read_queue", lambda: [])
        with pytest.raises(promo.PromotionGateError, match="not_in_pending_queue"):
            promo.cmd_reject_queue(
                variant_id=24,
                reason="wrong card",
                write=True,
                cur=self.Cursor(),
            )


class TestExactSnkIdentity:
    class NonExactCursor:
        def execute(self, _query, _args=None):
            return None

        def fetchall(self):
            return [
                {
                    "external_entity_id": "123",
                    "match_status": "candidate",
                }
            ]

    def test_sole_non_exact_snk_binding_is_rejected(self):
        with pytest.raises(promo.PromotionGateError, match="snk_identity_not_exact"):
            promo.fetch_live_image_state(self.NonExactCursor(), 24)


class TestNoProductionBypass:
    def test_cli_and_pointer_update_have_no_broad_fallback(self):
        source = (ROOT / "pipelines" / "snk_image_promotion.py").read_text(
            encoding="utf-8"
        )
        assert "--allow-missing-printing" not in source
        assert "reviewed_pointer_promote_failed" in source
        assert "source_version_sha256=%s AND source_path=%s" in source
        assert "match_status='exact'" in source
        assert '"snkIdentitySha256"' in source
        assert "source_language_evidence_missing" in source
        assert "language_match=0, tcg_match=1" not in source

    def test_legacy_bulk_public_writers_are_disabled(self):
        ensure_abc = (ROOT / "pipelines" / "ensure_image_abc.py").read_text(encoding="utf-8")
        qualified = (ROOT / "pipelines" / "qualified_pool_operator.py").read_text(encoding="utf-8")
        assert "ensure_image_abc public writer permanently disabled" in ensure_abc
        assert "fill-images public writer permanently disabled" in qualified
        image_picker = (ROOT / "tools/image-picker/apply_auto_selections.py").read_text(encoding="utf-8")
        g10_bind = (ROOT / "tools/bind_g10_raw_public_images.py").read_text(encoding="utf-8")
        assert "apply_auto_selections public writer permanently disabled" in image_picker
        assert "bind_g10_raw_public_images public writer permanently disabled" in g10_bind
