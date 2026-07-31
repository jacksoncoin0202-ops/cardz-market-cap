from __future__ import annotations

import unittest

from pipelines.snk_page_identity import parse_page, validate_page


BODY = """
<html>
  <head>
    <title>リザードン R :リバースホロ [025/185]【英語版】
      (ソード&amp;シールド「ビビットボルテージ」)</title>
  </head>
  <body>
    <h1>Charizard R :Reverse Holo [025/185][EN]
      (Sword &amp; Shield "Vivid Voltage")</h1>
    <img src="https://cdn.snkrdunk.com/upload_bg_removed/card.webp">
  </body>
</html>
"""


class SnkPageIdentityTests(unittest.TestCase):
    def test_exact_page_extracts_clean_image_and_passes_all_gates(self) -> None:
        page = parse_page(849532, BODY)
        validate_page(
            page,
            expected_collector="025/185",
            expected_language="en",
            required_tokens=["Charizard", "Reverse Holo", "Vivid Voltage"],
        )
        self.assertEqual(page["snkId"], 849532)
        self.assertEqual(
            page["imageUrl"],
            "https://cdn.snkrdunk.com/upload_bg_removed/card.webp",
        )

    def test_collector_mismatch_is_fail_closed(self) -> None:
        page = parse_page(849532, BODY)
        with self.assertRaisesRegex(ValueError, "collector_mismatch"):
            validate_page(
                page,
                expected_collector="025/186",
                expected_language="en",
                required_tokens=["Charizard"],
            )

    def test_bare_numeric_collector_cannot_create_exact_evidence(self) -> None:
        page = parse_page(849532, BODY)
        with self.assertRaisesRegex(ValueError, "expected_collector_incomplete"):
            validate_page(
                page,
                expected_collector="25",
                expected_language="en",
                required_tokens=["Charizard"],
            )

    def test_language_mismatch_is_fail_closed(self) -> None:
        page = parse_page(849532, BODY)
        with self.assertRaisesRegex(ValueError, "expected_ja"):
            validate_page(
                page,
                expected_collector="025/185",
                expected_language="ja",
                required_tokens=["Charizard"],
            )

    def test_required_treatment_is_mandatory(self) -> None:
        page = parse_page(849532, BODY)
        with self.assertRaisesRegex(ValueError, "required_token_missing"):
            validate_page(
                page,
                expected_collector="025/185",
                expected_language="en",
                required_tokens=["Staff Promo"],
            )


if __name__ == "__main__":
    unittest.main()
