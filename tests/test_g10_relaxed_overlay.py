from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import db_runtime  # noqa: E402
from tracked_universe import build_g10_relaxed_overlay, write_g10_relaxed_overlay  # noqa: E402


EFFECTIVE_AT = datetime(2026, 7, 31, 0, 0, tzinfo=timezone.utc)


def crosswalk_600() -> dict[str, object]:
    cards: list[dict[str, object]] = []
    for index in range(600):
        source = "snkrdunk" if index < 479 else "ebay"
        external_id = str(100_000 + index) if source == "snkrdunk" else f"g10-{index:032x}"
        cards.append(
            {
                "canonicalSourceCode": source,
                "canonicalExternalId": external_id,
                "gemrateId": f"{index:040x}",
                "snkItemId": 100_000 + index if source == "snkrdunk" else None,
                "market": "pokemon" if index < 500 else "one-piece",
                "language": "ja" if index % 2 else "en",
                "name": f"Card {index}",
                "setName": f"Set {index // 20}",
                "collectorNumberRaw": "" if index < 24 else f"{index:03d}/SVP",
                "identityStatus": "confirmed" if index % 2 else "review",
                "canonicalPrintingKey": None if index % 2 == 0 else f"fixture-{index}",
                "edition": "standard",
                "parallel": "",
                "finish": "foil",
            }
        )
    return {"schemaVersion": "2.0.0", "payloadSha256": "a" * 64, "cards": cards}


class G10RelaxedOverlayTests(unittest.TestCase):
    def test_600_crosswalk_cards_are_private_provisional_members_with_contiguous_ranks(self) -> None:
        crosswalk = crosswalk_600()
        first = build_g10_relaxed_overlay(crosswalk, effective_at=EFFECTIVE_AT)
        second = build_g10_relaxed_overlay(crosswalk, effective_at=EFFECTIVE_AT)

        self.assertEqual(first, second)
        self.assertEqual(first["schemaVersion"], "4.0.0")
        self.assertEqual(first["counts"], {
            "cards": 600,
            "pokemon": 500,
            "onePiece": 100,
            "gemrateMapped": 600,
            "snkMapped": 479,
            "provisionalCollectorMarkers": 24,
        })
        self.assertEqual(len(first["cards"]), 600)
        self.assertEqual(len({card["pokedexId"] for card in first["cards"]}), 600)
        self.assertTrue(all(card["pokedexStatus"] == "provisional" for card in first["cards"]))
        self.assertTrue(all(card["identityStatus"] == "provisional" for card in first["cards"]))

        db_cards = db_runtime.validate_active_universe(first)
        self.assertEqual(len(db_cards), 600)
        self.assertEqual(
            [card["rankMemberships"]["tcg"] for card in first["cards"]],
            list(range(1, 601)),
        )
        pokemon = [card for card in first["cards"] if card["tcg"] == "pokemon"]
        one_piece = [card for card in first["cards"] if card["tcg"] == "one-piece"]
        self.assertEqual([card["rankMemberships"]["pokemon"] for card in pokemon], list(range(1, 501)))
        self.assertEqual([card["rankMemberships"]["one-piece"] for card in one_piece], list(range(1, 101)))

        marker_rows = [card for card in first["cards"] if card["g10Bootstrap"]["collectorNumberStatus"] == "provisional_marker"]
        self.assertEqual(len(marker_rows), 24)
        self.assertTrue(all(card["collectorNumber"].startswith("G10-") for card in marker_rows))
        self.assertTrue(all(card["g10Bootstrap"]["collectorNumberRaw"] is None for card in marker_rows))

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "g10-relaxed-universe.json"
            gemrate_ids = root / "g10-relaxed-gemrate-ids.txt"
            snk_ids = root / "g10-relaxed-snk-ids.txt"
            write_g10_relaxed_overlay(first, output, gemrate_ids, snk_ids)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), first)
            self.assertEqual(len(gemrate_ids.read_text(encoding="ascii").splitlines()), 600)
            self.assertEqual(len(snk_ids.read_text(encoding="ascii").splitlines()), 479)


if __name__ == "__main__":
    unittest.main()
