from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("cardz_handoff", ROOT / "scripts/verify_handoff.py")
assert SPEC and SPEC.loader
handoff = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = handoff
SPEC.loader.exec_module(handoff)


class HandoffVerificationTests(unittest.TestCase):
    def make_repo(self, root: Path) -> None:
        for relative in handoff.REQUIRED_FILES:
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            if relative == ".gitattributes":
                path.write_text(
                    "data/private/cardz-active-bootstrap.tar.gz filter=lfs diff=lfs merge=lfs -text\n",
                    encoding="utf-8",
                )
            elif relative == ".gitignore":
                path.write_text(".env\ndata/runtime/\n", encoding="utf-8")
            elif relative.endswith("cardz-market-cap-daily.service"):
                path.write_text("ExecStart=/run-cardz-daily.sh\n", encoding="utf-8")
            elif relative.endswith("cardz-market-cap-bootstrap.service"):
                path.write_text("ExecStart=/backend.sh bootstrap --external-db\n", encoding="utf-8")
            else:
                path.write_text("placeholder\n", encoding="utf-8")

    def test_layout_requires_explicit_archive_lfs_rule_and_private_rules(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_repo(root)
            report = handoff.validate_layout(root)
        self.assertEqual(report["lfsRule"], "pinned")

    def test_unresolved_lfs_pointer_fails_even_when_archive_exists(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_repo(root)
            archive = root / handoff.ARCHIVE_RELATIVE
            archive.parent.mkdir(parents=True, exist_ok=True)
            archive.write_text("version https://git-lfs.github.com/spec/v1\noid sha256:test\n", encoding="utf-8")
            with self.assertRaisesRegex(handoff.HandoffError, "unresolved Git LFS pointer"):
                handoff.validate_archive(root, require_archive=True, require_tracked=False, verify_archive=False)

    def test_tracked_gate_rejects_untracked_release_archive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_repo(root)
            archive = root / handoff.ARCHIVE_RELATIVE
            archive.parent.mkdir(parents=True, exist_ok=True)
            archive.write_bytes(b"not-a-pointer")
            with mock.patch.object(
                handoff.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 1, "", ""),
            ):
                with self.assertRaisesRegex(handoff.HandoffError, "not Git-tracked"):
                    handoff.validate_archive(root, require_archive=True, require_tracked=True, verify_archive=False)


if __name__ == "__main__":
    unittest.main()
