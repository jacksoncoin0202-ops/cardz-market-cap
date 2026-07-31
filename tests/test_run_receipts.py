from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from pipelines.run_receipts import write_stage_receipt


class RunReceiptTests(unittest.TestCase):
    def test_stage_receipts_are_ordered_and_bind_exact_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.json"
            source.write_text('{"rows":1}\n', encoding="utf-8")
            at = datetime(2026, 7, 29, tzinfo=timezone.utc)
            acquired = write_stage_receipt(
                root / "receipts",
                run_id="daily_1",
                stage="ACQUIRED",
                inputs=[source],
                counts={"rows": 1},
                completed_at=at,
            )
            verified = write_stage_receipt(
                root / "receipts",
                run_id="daily_1",
                stage="VERIFIED",
                inputs=[source],
                outputs=[acquired],
                completed_at=at,
            )
            document = json.loads(verified.read_text(encoding="utf-8"))
            self.assertEqual(document["runId"], "daily_1")
            self.assertEqual(document["stage"], "VERIFIED")
            self.assertEqual(document["inputs"][0]["bytes"], len(source.read_bytes()))

    def test_stage_cannot_skip_its_predecessor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(RuntimeError, "VERIFIED"):
                write_stage_receipt(
                    Path(temporary),
                    run_id="daily_1",
                    stage="INGESTED",
                )

    def test_existing_receipt_is_immutable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            at = datetime(2026, 7, 29, tzinfo=timezone.utc)
            write_stage_receipt(
                root,
                run_id="daily_1",
                stage="ACQUIRED",
                counts={"rows": 1},
                completed_at=at,
            )
            with self.assertRaisesRegex(RuntimeError, "immutable"):
                write_stage_receipt(
                    root,
                    run_id="daily_1",
                    stage="ACQUIRED",
                    counts={"rows": 2},
                    completed_at=at,
                )


if __name__ == "__main__":
    unittest.main()
