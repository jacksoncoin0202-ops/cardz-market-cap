from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GENERATOR_PATH = ROOT / "scripts" / "generate_release_policy.py"
SPEC = importlib.util.spec_from_file_location("generate_release_policy", GENERATOR_PATH)
assert SPEC is not None and SPEC.loader is not None
GENERATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GENERATOR)


class ReleasePolicyGeneratedTests(unittest.TestCase):
    def test_typescript_policy_is_current_routing_config_output(self) -> None:
        document = json.loads((ROOT / "config" / "data-routing.json").read_text(encoding="utf-8"))
        expected = GENERATOR.generated_source(document)
        actual = (ROOT / "packages" / "market-data" / "src" / "release-policy.generated.ts").read_text(
            encoding="utf-8"
        )
        self.assertEqual(actual, expected)

    def test_effective_profile_is_relaxed_launch(self) -> None:
        document = json.loads((ROOT / "config" / "data-routing.json").read_text(encoding="utf-8"))
        source = GENERATOR.generated_source(document)
        self.assertIn('DEFAULT_RELEASE_PROFILE: ReleaseProfileId = "relaxed-launch-v1"', source)


if __name__ == "__main__":
    unittest.main()
