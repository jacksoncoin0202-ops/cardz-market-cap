from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import printing_agent_fill_pass as fill_pass  # noqa: E402


def worksheet(*, status: str = "open", binding_ready: bool = True) -> dict[str, object]:
    return {
        "variantId": "variant-1",
        "status": status,
        "bindingReady": binding_ready,
        "sourceBindings": [
            {"sourceCode": "snkrdunk", "externalEntityId": "123"},
            {"sourceCode": "gemrate", "externalEntityId": "456"},
        ],
        "fields": {},
    }


class PrintingAgentFillPassTests(unittest.TestCase):
    def test_dry_run_binding_ready_never_fetches_or_mutates_worksheet(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "worksheet.json"
            original = json.dumps(worksheet(), ensure_ascii=False, indent=2) + "\n"
            path.write_text(original, encoding="utf-8")

            with patch.object(fill_pass, "fetch_snk_master", side_effect=AssertionError("must not fetch")):
                result = fill_pass.process_worksheet(path, write=False)

            self.assertEqual(result, {
                "variantId": "variant-1",
                "status": "would_fetch_and_fill",
                "bindingReady": True,
            })
            self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_dry_run_main_writes_no_summary_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            batch = Path(temporary)
            (batch / "worksheets").mkdir()
            (batch / "index.json").write_text(
                json.dumps({"worksheets": [{"variantId": "variant-1", "path": "worksheets/variant-1.json"}]}),
                encoding="utf-8",
            )
            (batch / "worksheets" / "variant-1.json").write_text(
                json.dumps(worksheet()), encoding="utf-8"
            )

            with patch.object(fill_pass, "fetch_snk_master", side_effect=AssertionError("must not fetch")), patch.object(
                sys, "argv", ["printing_agent_fill_pass.py", "--batch", str(batch)]
            ):
                self.assertEqual(fill_pass.main(), 0)

            self.assertEqual(list(batch.glob("agent_fill_pass_*.json")), [])

    def test_write_mode_still_writes_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            batch = Path(temporary)
            (batch / "worksheets").mkdir()
            (batch / "index.json").write_text(
                json.dumps({"worksheets": [{"variantId": "variant-1", "path": "worksheets/variant-1.json"}]}),
                encoding="utf-8",
            )
            worksheet_path = batch / "worksheets" / "variant-1.json"
            worksheet_path.write_text(json.dumps(worksheet()), encoding="utf-8")

            with patch.object(fill_pass, "process_worksheet", return_value={"variantId": "variant-1", "status": "sealed"}) as process, patch.object(
                sys, "argv", ["printing_agent_fill_pass.py", "--batch", str(batch), "--write"]
            ):
                self.assertEqual(fill_pass.main(), 0)

            process.assert_called_once_with(worksheet_path, write=True)
            self.assertEqual(len(list(batch.glob("agent_fill_pass_*.json"))), 1)

    def test_dry_run_sealed_and_unbound_are_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sealed = root / "sealed.json"
            unbound = root / "unbound.json"
            sealed.write_text(json.dumps(worksheet(status="sealed")), encoding="utf-8")
            unbound.write_text(json.dumps(worksheet(binding_ready=False)), encoding="utf-8")

            self.assertEqual(fill_pass.process_worksheet(sealed, write=False)["status"], "already_sealed")
            self.assertEqual(fill_pass.process_worksheet(unbound, write=False)["status"], "skip_bindings")

    def test_g10_language_requires_explicit_structured_value(self) -> None:
        self.assertEqual(fill_pass.explicit_g10_language({"language": "jp"}), ("jp", "ja"))
        self.assertIsNone(
            fill_pass.explicit_g10_language({"displayTitle": "Japanese Card"})
        )

    def test_write_never_infers_printing_fields_from_title_rarity_or_image(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence_root = root / "evidence"
            path = root / "worksheet.json"
            document = worksheet()
            values = {
                "tcgCode": "one-piece",
                "cardLanguage": "ja",
                "setName": "OP05",
                "collectorNumber": "OP05-119",
                "editionCode": "",
                "parallelCode": "",
                "finishCode": "",
            }
            document["fields"] = {
                field: {
                    "rawValue": value,
                    "normalizedValue": value,
                    "evidenceType": "",
                    "extractorVersion": "",
                    "sourceCode": "",
                    "externalEntityId": "",
                    "sourceReceiptPath": "",
                    "sourceReceiptSha256": "",
                }
                for field, value in values.items()
            }
            path.write_text(json.dumps(document), encoding="utf-8")
            master = {
                "name": "Luffy SEC-SP (First Edition) Foil",
                "language": "ja",
                "primaryMedia": {"imageUrl": "https://example.invalid/card.webp"},
            }
            with patch.object(fill_pass, "ROOT", root), patch.object(
                fill_pass, "EVIDENCE_ROOT", evidence_root
            ), patch.object(
                fill_pass, "G10_SNK_ROOTS", (root / "no-g10",)
            ), patch.object(
                fill_pass, "GEMRATE_CARDS", root / "no-gemrate"
            ), patch.object(
                fill_pass, "fetch_snk_master", return_value=master
            ):
                result = fill_pass.process_worksheet(path, write=True)

            self.assertEqual(result["status"], "incomplete")
            self.assertEqual(
                result["missingFields"],
                ["editionCode", "parallelCode", "finishCode"],
            )
            written = json.loads(path.read_text(encoding="utf-8"))
            for field in ("editionCode", "parallelCode", "finishCode"):
                self.assertEqual(written["fields"][field]["normalizedValue"], "")
                self.assertEqual(written["fields"][field]["evidenceType"], "")
            self.assertEqual(
                written["agentPass"]["automaticPrintingFieldInference"],
                "disabled",
            )
            self.assertFalse(
                (evidence_root / "snk" / "123" / "primary.webp").exists()
            )

    def test_g10_lookup_uses_cross_runtime_roots(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            asset = root / "123" / "asset_info.json"
            asset.parent.mkdir(parents=True)
            asset.write_text("{}", encoding="utf-8")
            with patch.object(fill_pass, "G10_SNK_ROOTS", (root,)):
                self.assertEqual(fill_pass.find_g10_asset_info("123"), asset)


if __name__ == "__main__":
    unittest.main()
