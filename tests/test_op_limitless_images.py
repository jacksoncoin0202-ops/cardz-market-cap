from __future__ import annotations

import argparse
import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "op_limitless_images", ROOT / "pipelines" / "op_limitless_images.py"
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class OpLimitlessImagesAllowlistTests(unittest.TestCase):
    def test_repeated_ids_and_csv_are_deduplicated_in_order(self) -> None:
        parser = MODULE.build_parser()
        args = parser.parse_args(
            ["--variant-id", "1753", "--variant-id-csv", "1752,1753,1755", "--variant-id", "1752"]
        )

        self.assertEqual(MODULE.selected_variant_ids(args), [1753, 1752, 1755])
        self.assertFalse(args.write)

    def test_csv_rejects_non_positive_and_non_numeric_ids(self) -> None:
        with self.assertRaises(argparse.ArgumentTypeError):
            MODULE.parse_variant_id_csv(["1752,nope"])
        with self.assertRaises(argparse.ArgumentTypeError):
            MODULE.parse_variant_id_csv(["0"])

    def test_cli_defaults_to_allowlist_required_dry_run(self) -> None:
        parser = MODULE.build_parser()
        args = parser.parse_args(["--variant-id-csv", "1752,1753,1755,1758,1759,1760"])

        self.assertEqual(MODULE.selected_variant_ids(args), [1752, 1753, 1755, 1758, 1759, 1760])
        self.assertFalse(args.write)

    def test_main_rejects_full_pool_before_opening_database(self) -> None:
        with patch.object(sys, "argv", ["op_limitless_images.py"]):
            with self.assertRaises(SystemExit) as exit_result:
                MODULE.main()

        self.assertEqual(exit_result.exception.code, 2)

    def test_allowlisted_write_is_disabled_before_opening_database(self) -> None:
        with (
            patch.object(
                sys,
                "argv",
                ["op_limitless_images.py", "--variant-id", "1752", "--write"],
            ),
            patch.object(MODULE, "db") as database,
            self.assertRaises(SystemExit) as exit_result,
        ):
            MODULE.main()

        self.assertEqual(exit_result.exception.code, 2)
        database.assert_not_called()


if __name__ == "__main__":
    unittest.main()
