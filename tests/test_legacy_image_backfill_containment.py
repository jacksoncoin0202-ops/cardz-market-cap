from __future__ import annotations

import builtins
import inspect
import io
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

from pipelines import canvas_normalize_backfill, native_image_refetch


class LegacyImageBackfillContainmentTests(unittest.TestCase):
    def test_archived_tools_refuse_before_any_filesystem_io(self) -> None:
        for tool in (native_image_refetch, canvas_normalize_backfill):
            with self.subTest(tool=tool.__name__):
                stderr = io.StringIO()
                forbidden = AssertionError(f"{tool.__name__} attempted filesystem I/O")
                with (
                    mock.patch.object(Path, "open", side_effect=forbidden),
                    mock.patch.object(Path, "read_bytes", side_effect=forbidden),
                    mock.patch.object(Path, "read_text", side_effect=forbidden),
                    mock.patch.object(Path, "write_bytes", side_effect=forbidden),
                    mock.patch.object(Path, "write_text", side_effect=forbidden),
                    mock.patch.object(builtins, "open", side_effect=forbidden),
                    redirect_stderr(stderr),
                ):
                    self.assertEqual(tool.main(), 2)
                self.assertIn("archived", stderr.getvalue())
                self.assertIn("official publisher", stderr.getvalue())

    def test_archived_tools_have_no_pointer_or_snapshot_write_path(self) -> None:
        for tool in (native_image_refetch, canvas_normalize_backfill):
            with self.subTest(tool=tool.__name__):
                source = inspect.getsource(tool)
                self.assertNotIn("LATEST_POINTER =", source)
                self.assertNotIn(".write_text(", source)
                self.assertNotIn(".write_bytes(", source)


if __name__ == "__main__":
    unittest.main()
