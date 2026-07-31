from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from backend import promote_additive_universe  # noqa: E402


def universe(*external_ids: str) -> dict[str, object]:
    return {
        "cards": [
            {
                "canonicalSourceCode": "gemrate",
                "canonicalExternalId": external_id,
            }
            for external_id in external_ids
        ]
    }


class UniversePromotionTests(unittest.TestCase):
    def write_candidate(
        self,
        root: Path,
        document: dict[str, object],
    ) -> tuple[Path, Path, Path]:
        candidate = root / "candidate.json"
        gemrate = root / "candidate-gemrate.txt"
        snk = root / "candidate-snk.txt"
        candidate.write_text(json.dumps(document), encoding="utf-8")
        gemrate.write_text("1\n2\n", encoding="ascii")
        snk.write_text("10\n20\n", encoding="ascii")
        return candidate, gemrate, snk

    def test_promotes_additive_lock_and_archives_exact_bytes(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            tracked_root = root / "data/runtime/private-source-map"
            tracked_root.mkdir(parents=True)
            (tracked_root / "tracked-universe.json").write_text(
                json.dumps(universe("1")),
                encoding="utf-8",
            )
            (tracked_root / "tracked-gemrate-ids.txt").write_text("1\n", encoding="ascii")
            (tracked_root / "tracked-snk-ids.txt").write_text("10\n", encoding="ascii")
            candidate, gemrate, snk = self.write_candidate(root, universe("1", "2"))

            with mock.patch("backend.ROOT", root):
                report = promote_additive_universe(candidate, gemrate, snk)

            self.assertEqual(report["previousMembers"], 1)
            self.assertEqual(report["members"], 2)
            self.assertEqual(report["addedMembers"], 1)
            self.assertEqual(
                json.loads((tracked_root / "tracked-universe.json").read_text(encoding="utf-8")),
                universe("1", "2"),
            )
            archive = Path(str(report["archiveRoot"]))
            self.assertEqual((archive / "tracked-universe.json").read_bytes(), candidate.read_bytes())

    def test_rejects_shrink_without_touching_current_lock(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            tracked_root = root / "data/runtime/private-source-map"
            tracked_root.mkdir(parents=True)
            current = json.dumps(universe("1", "2")).encode()
            (tracked_root / "tracked-universe.json").write_bytes(current)
            (tracked_root / "tracked-gemrate-ids.txt").write_text("1\n2\n", encoding="ascii")
            (tracked_root / "tracked-snk-ids.txt").write_text("10\n20\n", encoding="ascii")
            candidate, gemrate, snk = self.write_candidate(root, universe("2"))

            with mock.patch("backend.ROOT", root):
                with self.assertRaisesRegex(RuntimeError, "not additive"):
                    promote_additive_universe(candidate, gemrate, snk)

            self.assertEqual((tracked_root / "tracked-universe.json").read_bytes(), current)


if __name__ == "__main__":
    unittest.main()
