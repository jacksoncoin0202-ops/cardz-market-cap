from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "pipelines" / "repair_pokemon151_twin_language.py"
SPEC = importlib.util.spec_from_file_location("pokemon151_twin_repair", PATH)
assert SPEC and SPEC.loader
repair = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = repair
SPEC.loader.exec_module(repair)


class ManifestTests(unittest.TestCase):
    def test_manifest_is_finite_and_complete(self) -> None:
        self.assertEqual(repair.manifest_errors(), [])
        self.assertEqual(len(repair.TWIN_MANIFEST), 15)
        self.assertEqual(len({row["jpSnk"] for row in repair.TWIN_MANIFEST}), 15)
        self.assertEqual(len({row["enSnk"] for row in repair.TWIN_MANIFEST}), 15)
        self.assertEqual(sum(bool(row.get("pcIdentityMoves")) for row in repair.TWIN_MANIFEST), 2)

    def test_manifest_rejects_duplicate_pc_product(self) -> None:
        broken = [dict(row) for row in repair.TWIN_MANIFEST]
        broken[-1]["pc"] = broken[0]["pc"]
        self.assertIn("manifest must contain 15 unique EN PriceCharting product ids", repair.manifest_errors(broken))


class ReceiptGateTests(unittest.TestCase):
    def test_snk_requires_explicit_english_mew_product(self) -> None:
        self.assertTrue(repair.is_explicit_en_snk('{"product_number":"pkmn-tcg-en-MEW-EN-168"}'))
        self.assertTrue(repair.is_explicit_en_snk('{"name":"Pokemon MEW EN Charmander"}'))
        self.assertFalse(repair.is_explicit_en_snk('{"product_number":"pkmn-tcg-ja-SV2A-168"}'))

    def test_snk_requires_explicit_japanese_sv2a_product(self) -> None:
        self.assertTrue(repair.is_explicit_ja_snk('{"product_number":"pkmn-tcg-SV2a-168"}'))
        self.assertFalse(repair.is_explicit_ja_snk('{"product_number":"pkmn-tcg-en-MEW-EN-168"}'))

    def test_transport_sale_rebind_includes_ebay_and_snkrdunk(self) -> None:
        # G10/SNK records can be transported through eBay rows while retaining
        # the SNK product id.  A twin swap must not leave those sales behind.
        self.assertEqual(repair.SNK_SALE_TRANSPORT_SOURCES, ("snkrdunk", "ebay"))

    def test_pc_requires_english_not_japanese_route(self) -> None:
        self.assertTrue(repair.is_explicit_en_pc([{"pc_url": "https://www.pricecharting.com/game/pokemon-scarlet-&-violet-151/charmander-168"}]))
        self.assertFalse(repair.is_explicit_en_pc([{"pc_url": "https://www.pricecharting.com/game/pokemon-japanese-scarlet-&-violet-151/charmander-168"}]))
