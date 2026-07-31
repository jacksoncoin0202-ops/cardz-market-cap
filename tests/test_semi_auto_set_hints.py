#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit tests for semi_auto_identity set_hints / pure-digit verify_set gate."""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "semi_auto_identity", ROOT / "pipelines" / "semi_auto_identity.py"
)
sai = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(sai)


class TestSetHints(unittest.TestCase):
    def test_mega_evolution_plain_set_has_m_codes(self) -> None:
        h = sai.set_hints("Pokemon Mega Evolution", "125", "Charizard ex")
        self.assertTrue({"M1", "M2", "M2A"} <= {sai.norm_alnum(x) for x in h})

    def test_black_bolt_white_flare_narrow(self) -> None:
        self.assertIn("SV11B", {sai.norm_alnum(x) for x in sai.set_hints(
            "Pokemon Scarlet and Violet Black Bolt", "100", "x"
        )})
        self.assertIn("SV11W", {sai.norm_alnum(x) for x in sai.set_hints(
            "Pokemon Scarlet and Violet White Flare", "100", "x"
        )})

    def test_celebrations_title_token(self) -> None:
        h = sai.set_hints("Pokemon Celebrations Classic Collection", "17", "Umbreon Star")
        self.assertIn("CELEBRATIONS", {sai.norm_alnum(x) for x in h})
        self.assertIn("S8A", {sai.norm_alnum(x) for x in h})

    def test_verify_celebrations_without_bracket_code(self) -> None:
        cand = 'Umbreon Star [17/17](Sword & Shield "Celebrations")'
        keys = sai.extract_snk_keys(cand, "")
        ok, reason, _score = sai.verify_pair(
            "Umbreon Star",
            "17",
            cand,
            keys,
            watch_set="Pokemon Celebrations Classic Collection",
            product_number="",
        )
        self.assertTrue(ok, reason)
        self.assertEqual(reason, "verify_ok")

    def test_verify_rejects_cross_set_same_number(self) -> None:
        # Evolving Skies #212 must not accept Terastal Festival Sylveon
        cand = 'Sylveon ex SAR [SV8a 212/187](High Class Pack "Terastal Festival ex")'
        keys = sai.extract_snk_keys(cand, "pkmn-tcg-SV8a-212")
        ok, reason, _ = sai.verify_pair(
            "Sylveon VMAX (Alternate Art Secret)",
            "212",
            cand,
            keys,
            watch_set="Pokemon Sword and Shield: Evolving Skies",
            product_number="pkmn-tcg-SV8a-212",
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "verify_set")

    def test_mega_evolution_hypothetical_m2_pass(self) -> None:
        cand = 'Charizard ex SAR [M2 125/063](Expansion Pack "Phantasmal Flames")'
        pn = "pkmn-tcg-M2-125"
        keys = sai.extract_snk_keys(cand, pn)
        ok, reason, _ = sai.verify_pair(
            "Charizard ex",
            "125",
            cand,
            keys,
            watch_set="Pokemon Mega Evolution",
            product_number=pn,
        )
        self.assertTrue(ok, reason)


class TestCollectorForms(unittest.TestCase):
    """Playbook §3 expansions — recall-side only; verify stays strict."""

    def test_op_hyphen_expands_compact_and_bare(self) -> None:
        forms = sai.collector_forms("OP01-016")
        self.assertIn("OP01016", forms)
        self.assertIn("OP0116", forms)  # strip leading zero on card no
        self.assertIn("016", forms)
        self.assertIn("16", forms)

    def test_slash_fraction_numerator(self) -> None:
        forms = sai.collector_forms("215/203")
        self.assertIn("215203", forms)
        self.assertIn("215", forms)
        self.assertNotIn("203", forms)  # bare denominator is too noisy

    def test_slash_hyphen_equiv(self) -> None:
        forms = sai.collector_forms("215-203")
        self.assertIn("215", forms)
        self.assertIn("215203", forms)

    def test_subset_gg70_and_space(self) -> None:
        self.assertIn("GG70", sai.collector_forms("GG70"))
        self.assertIn("GG70", sai.collector_forms("GG 70"))

    def test_fullwidth_and_nbsp(self) -> None:
        forms = sai.collector_forms("０１５／１６５")
        self.assertIn("015", forms)
        self.assertIn("15", forms)
        self.assertIn("015165", forms)
        forms_nbsp = sai.collector_forms("\u00a0215/203")
        self.assertIn("215", forms_nbsp)

    def test_verify_still_rejects_cross_set_after_expand(self) -> None:
        cand = 'Sylveon ex SAR [SV8a 212/187](High Class Pack "Terastal Festival ex")'
        keys = sai.extract_snk_keys(cand, "pkmn-tcg-SV8a-212")
        ok, reason, _ = sai.verify_pair(
            "Sylveon VMAX (Alternate Art Secret)",
            "212",
            cand,
            keys,
            watch_set="Pokemon Sword and Shield: Evolving Skies",
            product_number="pkmn-tcg-SV8a-212",
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "verify_set")


if __name__ == "__main__":
    unittest.main()
