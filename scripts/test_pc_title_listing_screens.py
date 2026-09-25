#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PC sale listing screens (c11_pc_sold_ingest.title_listing_conflict) -- 2026-09-25.

One implementation decides both new rows (verify_sale) and stored rows
(pc_sale_title_quarantine.build_receipt).  Fixtures are real PriceCharting
PSA 10 tab titles with the bound card's real catalog identity (sale id /
variant id in the comment); the few shapes taken from the brief are marked
"synthetic".

  L1 lot / bundle   x2, 2x, lot of 3, x3, Pair, 6 PCS, 8set, + Display and
                    sequential sets reject; seller stock codes (x608 / x296),
                    "Inferno X 2025", "Lv.X 2021" and one card from a split
                    sequential run pass
  L2 damaged slab   DAMAGED/CRACKED SLAB rejects; Cracked Ice and a card whose
                    own name says Cracked pass
  L3 other grade    PSA 9 / 8 / AUTHENTIC reject; no grade (PC's PSA 10 tab),
                    PSA GEM Mint 10 and the "PSA 0" typo pass
  L4 language       Spanish / JP / China / Korean / Indonesian / ES- label on
                    the wrong card reject; JP + English on a JP card and a
                    language the card's own identity names pass
  W  wiring         verify_sale uses the screens; main() attaches the catalog
                    identity that revives the dead ingest collector check
                    ("107/095" landed on a 213/214 card)
  R  receipt        stored rows get per-class reasons; the SQL carries identity

Every rule is pinned twice (AGENTS.md rule 9): the shipped module passes, and a
twin of the module with that one rule planted back to the bug must fail.
"""
from __future__ import annotations

import sys
import types
from collections import Counter
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import c11_pc_sold_ingest as C  # noqa: E402
import pc_sale_title_quarantine as Q  # noqa: E402

C11_PATH = ROOT / "pipelines" / "c11_pc_sold_ingest.py"
Q_PATH = ROOT / "pipelines" / "pc_sale_title_quarantine.py"
SOURCES = {path: path.read_bytes().decode("utf-8") for path in (C11_PATH, Q_PATH)}

LOT = C.TITLE_REASON_LOT
DAMAGED = C.TITLE_REASON_DAMAGED
GRADE = C.TITLE_REASON_OTHER_GRADE
LANGUAGE = C.TITLE_REASON_LANGUAGE


def card(language: str, collector: str, set_name: str, canonical_name: str) -> dict[str, str]:
    return {"card_language": language, "collector_number": collector,
            "set_name": set_name, "canonical_name": canonical_name}


# Real catalog identities (catalog_printing_identity + catalog_variant), by variant id.
V847 = card("en", "209", "pokemon sword and shield evolving skies",
            "2021 Pokemon Sword & Shield Evolving Skies Full Art/Glaceon Vmax Secret 209/203")
V176 = card("ja", "173/165", "SV2a: Pokemon Card 151",
            "2023 Pokemon Japanese Sv2a-Pokemon Card 151 Pikachu Art Rare 173/165")
V2058 = card("ja", "013", "Pokemon Japanese M2-Inferno X",
             "2025 Pokemon Japanese M2-Inferno X Mega Charizard X EX Base 013/080")
V905 = card("en", "109", "Pokemon Sword and Shield Celebrations Classic Collection",
            "2021 Pokemon Celebrations Classic Collection Luxray GL LV.X-Holo Base 109/111")
V960 = card("en", "7", "Pokemon Sword and Shield Celebrations",
            "2021 Pokemon Celebrations Full Art/Flying Pikachu Vmax Base 007/025")
V2140 = card("ja", "6", "Pokemon Japanese CD Promo", "1999 Pokemon Japanese CD Promo Charizard-Holo CD Promo 6")
V26 = card("ja", "293/XY-P", "XY-P: XY Promos",
           "2016 Pokemon Japanese XY Promo Mario Pikachu Holo-Mario Pikachu Special Box 293/XY-P")
V870 = card("en", "204", "pokemon sword and shield evolving skies",
            "2021 Pokemon Sword & Shield Evolving Skies Full Art/Leafeon Vmax Secret 204/203")
V1276 = card("en", "op13-007", "one piece carrying on his will",
             "2025 One Piece OP13-Carrying on His Will Ace & Sabo & Luffy Alternate Art OP13-007")
V1878 = card("ja", "232", "Pokemon Japanese XY Promo",
             "2016 Pokemon Japanese XY Promo Rayquaza Cracked Ice Pokemon Center Skytree Town 232/XY-P")
V1878_NO_PATTERN = card("ja", "232", "Pokemon Japanese XY Promo",  # synthetic: name without "Cracked Ice"
                        "2016 Pokemon Japanese XY Promo Rayquaza Pokemon Center Skytree Town 232/XY-P")
V6 = card("ja", "217/187", "SV8a: Terastal Fest ex",
          "2024 Pokemon Japanese Sv8a-Terastal Fest EX Umbreon EX Special Art Rare 217/187")
V12 = card("ja", "231/XY-P", "XY-P: XY Promos",
           "2016 Pokemon Japanese XY Promo Poncho-Wearing Pikachu Rayquaza Poncho-Wearing Pikachu Box 231/XY-P")
V1928 = card("en", "205", "Pokemon 151 Ultra-Premium Collection",
             "2023 Pokemon 151 Ultra-Premium Collection Mew EX Base 205/165")
V1637 = card("ja", "OP09-119", "Promotional Card 3rd ANNIVERSARY SET",
             "2025 One Piece Japanese 3rd Anniversary Set Monkey D. Luffy Base OP09-119")
V2030 = card("ja", "061", "One Piece Japanese English Version 2nd Anniversary Set",
             "2025 One Piece Japanese English Version 2nd Anniversary Set Monkey D. Luffy Base EB02-061")
V2194 = card("en", "101", "Pokemon Indonesian SV-P Promo",
             "2024 Pokemon Indonesian SV-P Promo Pikachu in Batik Shirt Pikachu's Indonesia Journey Campaign 101/SV-P")
V1 = card("en", "085/SVP", "SVP EN “Van gogh exhibition”",
          "2023 Pokemon Svp EN-SV Black Star Promo Pikachu With Grey Felt Hat Pokemon X Van Gogh 085/SVP")
V2066 = card("en", "262", "Pokemon Swsh Black Star Promo",
             "2022 Pokemon Swsh Black Star Promo Full Art/Charizard Vstar Sword & Shield Ultra-Premium Collection-Charizard 262")
V2291 = card("en", "SV10", "Pokemon Sun & Moon Hidden Fates", "2019 Pokemon Sun & Moon Hidden Fates Quagsire-Holo Base SV10")
V2192 = card("ja", "324", "Pokemon Japanese S Promo", "2022 Pokemon Japanese S Promo Lugia V Lugia Get Challenge 324/S-P")
V1024 = card("ja", "170", "pokemon japanese card 151",
             "2023 Pokemon Japanese Sv2a-Pokemon Card 151 Squirtle Art Rare 170/165")
V2238 = card("ja", "109", "One Piece Japanese OP07-500 Years in the Future",
             "2024 One Piece Japanese OP07-500 Years in the Future Monkey D. Luffy Base OP07-109")
V2233 = card("zhCN", "172", "Pokemon Simplified Chinese 151 C-Collection 151",
             "2025 Pokemon Simplified Chinese 151 C-Collection 151 Pikachu Art Rare 172/151")
V80 = card("ja", "OP09-118", "Booster Pack Emperors In The New World",
           "2024 One Piece Japanese OP09-Emperors in the New World Gol D. Roger Gold Manga Alternate Art OP09-118")
V945 = card("en", "94", "Pokemon Scarlet and Violet Shrouded Fable",
            "2024 Pokemon Sfa EN-Shrouded Fable Cassiopeia Special Illustration Rare 094/064")
V445 = card("en", "124", "Pokemon Sword and Shield Lost Origin",
            "2022 Pokemon Sword & Shield Lost Origin Radiant Steelix Base 124/196")
V9 = card("ja", "208/XY-P", "Pokemon Japanese XY Promo",
          "2016 Pokemon Japanese XY Promo Poncho-Wearing Pikachu Mega Charizard Y Pikachu Special Box 208/XY-P")
V2037 = card("en", "144", "Pokemon Swsh Black Star Promo",
             "2021 Pokemon Swsh Black Star Promo Greninja-Gold Star Celebrations Elite Trainer Box 144")
V1120 = card("en", "111", "Pokemon XY Evolutions", "2016 Pokemon XY Evolutions Surfing Pikachu Base 111/108")
V944 = card("en", "71", "Pokemon Scarlet and Violet Shrouded Fable",
            "2024 Pokemon Sfa EN-Shrouded Fable Cresselia Illustration Rare 071/064")
V820 = card("en", "150", "pokemon scarlet and violet prismatic evolutions",
            "2025 Pokemon Pre EN-Prismatic Evolutions Glaceon EX Special Illustration Rare 150/131")
V1870 = card("en", "131", "Pokemon Svp EN-SV Black Star Promo",
             "2024 Pokemon Svp EN-SV Black Star Promo Kingdra EX Shrouded Fable Special Illustration Collection 131")
V638 = card("en", "4", "pokemon sword and shield pokemon go", "2022 Pokemon Go Radiant Venusaur Base 004/078")
V652 = card("en", "6", "pokemon scarlet and violet 151 mew", "2023 Pokemon Mew EN-151 Charizard EX Base 006/165")
V1428 = card("en", "OP08-118", "One Piece Two Legends",
             "2024 One Piece OP08-Two Legends Silvers Rayleigh Manga Alternate Art OP08-118")
V1935 = card("ja", "095", "Pokemon Japanese Sword & Shield Eevee Heroes",
             "2021 Pokemon Japanese Sword & Shield Eevee Heroes Full Art/Umbreon Vmax-Hyper Base 095/069")
V191 = card("ja", "205/187", "SV8a: Terastal Fest ex",
            "2024 Pokemon Japanese Sv8a-Terastal Fest EX Vaporeon EX Special Art Rare 205/187")
V1323 = card("en", "213", "Pokemon Sun and Moon Unbroken Bonds",
             "2019 Pokemon Sun & Moon Unbroken Bonds Full Art/Red's Challenge Base 213/214")

# (title, card, tag).  Tag names the rule the pass fixture guards.
STOCK, SEQ_SINGLE, CRACKED, NO_GRADE, OWN_LANGUAGE = "stock", "seq", "cracked", "grade", "language"
MUST_PASS: list[tuple[str, dict, str]] = [
    ("PSA 10 - Glaceon VMAX 209/203 SWSH Evolving Skies x608 - Pokemon See Card", V847, STOCK),  # pid 2513018
    ("PSA 10 - Pikachu 173/165 AR SV2a Japanese 151 x296 - Pokemon See Card", V176, STOCK),  # pid 5326214
    ("Mega Charizard X ex #013 M2 Inferno X 2025 Pokemon Japanese PSA 10 154257191 Japanese", V2058, STOCK),
    ("Pokemon Luxray GL Lv.X 2021 Celebrations 109/111 PSA 10 109/111", V905, STOCK),  # 1635604
    ("POKEMON CELEBRATIONS #007 FLYING PIKACHU VMAX PSA 10 (PART OF SEQUENTIAL SET) #007", V960, SEQ_SINGLE),  # 2091324
    ("Charizard Japanese CD Promo Holo Pokemon Card 1999 PSA 10 GEM MINT Sequential Japanese #6", V2140, SEQ_SINGLE),
    ("POKEMON MARIO PIKACHU 2016 JPN XY PROMO #293 SPECIAL BOX HOLO PSA 10 *Sequential Japanese #293", V26, SEQ_SINGLE),
    ("Leafeon VMAX #204/203 Evolving Skies SR PSA 10 Pokemon Card (Sequential IDs)*TE 204/203", V870, SEQ_SINGLE),
    ("2025 One Piece OP13 Ace & Sabo & Luffy SR & SR Alternate Art SEQUENTIAL PSA 10 #2025", V1276, SEQ_SINGLE),  # 2340518
    ("2016 POKEMON JP XY PROMO P.C. SKYTREE TOWN #232 RAYQUAZA CRACKED ICE PSA 10 232/XY-P", V1878, CRACKED),  # 2232225
    ("2016 POKEMON JP XY PROMO P.C. SKYTREE TOWN #232 RAYQUAZA CRACKED ICE PSA 10 232/XY-P", V1878_NO_PATTERN, CRACKED),
    ("PSA 10 Rayquaza 232/XY-P Promo Cracked Pokemon Center Skytree Japanese POKEMON Japanese 232/XY-P", V1878, CRACKED),
    ("Pokémon Umbreon SV8a JP ex Special Art Rare #217 Japanese", V6, NO_GRADE),  # 2276153: no PSA at all
    ("2016 Pokemon Japanese XY Promo Poncho  Rayquaza 231/XY-P PSA GEM Mint 10 Japanese 231/XY-P", V12, NO_GRADE),
    ("2023 POKEMON 151 ULTRA-PREMIUM COLLECTION #205 MEW ex PSA 0 #205", V1928, NO_GRADE),  # 2365059 typo
    ("PSA 10 - Monkey D. Luffy OP09-119 One Piece JP 3rd Anniversary Set Promo English #OP09-119", V1637,
     OWN_LANGUAGE),  # 2250070: judged a match
    ("2025 ONE PIECE OP09 061 MONKEY D LUFFY ENGLISH 2ND ANNIVERSARY SET PROMO PSA 10 #OP09-061", V2030,
     OWN_LANGUAGE),  # 2250829
    ("PSA 10 Pikachu Berkemeja Batik 101/SV-P Indonesia Journey Promo Pokemon Card Indonesian", V2194,
     OWN_LANGUAGE),  # 1832870
    ("Pikachu with Grey Felt Hat 085 PSA 10 Pokemon Van Gogh English Black Star Promo #085", V1, OWN_LANGUAGE),
]
MUST_REJECT: list[tuple[str, dict, str]] = [
    ("Pikachu 173/165 AR SV2a Japanese 151 PSA 10 x2", V176, LOT),  # synthetic
    ("2x Pikachu 173/165 AR SV2a Japanese 151 PSA 10", V176, LOT),  # synthetic
    ("Lot of 3 PSA 10 Pikachu 173/165 AR SV2a Japanese 151", V176, LOT),  # synthetic
    ("Glaceon VMAX 209/203 Evolving Skies x3 PSA 10", V847, LOT),  # synthetic
    ("Mewtwo VSTAR GG44 & Charizard VSTAR 262 Pokemon Cards PSA 10 Gem Mint Pair #SWSH262", V2066, LOT),  # 2557711
    ("SEQUENTIAL PSA 10 SET WOOPER & QUAGSIRE HOLO #SV9 SV10 2019 POKEMON HIDDEN FATES #SV9", V2291, LOT),  # 2252501
    ("PSA 10 POKEMON SEQUENTIAL HO-OH V LUGIA V 324/S-P 055/068 Japanese Promo Japanese", V2192, LOT),  # 2236524
    ("Pokemon 151 Bulbasaur 166 Charmander 168 Squirtle 170 AR sv2a PSA 10 Sequential Japanese 166, 168, 170/165",
     V1024, LOT),  # 2115331
    ("One Piece Day '24 OP07-109 Monkey D. Luffy and DON!! Sequential PSA 10 Japanese Japanese", V2238, LOT),  # 2146262
    ("6 PCS PSA 10 2025 POKEMON  CHINESE 151C-COLLECTION 151 #172 PIKACHU ART RARE Chinese 172/151", V2233, LOT),
    ("One Piece Card Game - Gol D. Roger OP09-118 Gold Manga Alt Art PSA 10 + Display", V80, LOT),  # 2248959
    ("PSA 10 ONE PIECE Card Game English 2nd Anniversary SQ 8set Four Emperor JP Ver. Japanese #OP09-001", V2030,
     LOT),  # 2250826
    ("DAMAGED/CRACKED SLAB 2024 Pokemon Shrouded Fable Cassiopeia 094/064 PSA 10 094/064", V945, DAMAGED),  # 2546238
    ("Radiant Steelix (Radiant Rare) - 124/196 - #124 - Pokemon: Lost Origin - PSA 9", V445, GRADE),  # 1623538
    ("Pokemon Poncho-Wearing Pikachu M Charizard Y JPN FA Promo 208/XY-P PSA AUTHENTIC", V9, GRADE),  # 1775615
    ("2021 POKEMON SWSH CELEBRATIONS ELITE TRAINER BOX #144 GRENINJA-GOLD STAR PSA 8 #144", V2037, GRADE),  # 1952722
    ("2016 Pokémon TCG XY Surfing Pikachu Evolutions #111/108 NM-MT PSA mint 9 111/108", V1120, GRADE),  # 1789855
    ("113137110 Cresselia 2024 Pokemon SV Shrouded Fable - SFA EN #71 PSA 9 071/064", V944, GRADE),  # 1636566
    ("2025 POKEMON PRE ES-PRISMATIC EVOLUTIONS #150 GLACEON ex SIR PSA 10 Spanish #150", V820, LANGUAGE),  # 2558370
    ("2024 POKEMON SVP ES-SV BLACK STAR PROMO #131 KINGDRA ex SFA SIR PSA 10", V1870, LANGUAGE),  # 2314273: label only
    ("2022 Pokemon SWSH Go JP Radiant Venusaur Rare #004/071 PSA 10 GEM MINT", V638, LANGUAGE),  # 2377821
    ("Charizard ex 006/165 PSA 10 Gem Mint 2023 Japanese Pokémon 151 SV2a Japanese 006/165", V652, LANGUAGE),  # 1952262
    ("2024 One Piece OP08 China Manga Alternate Art #118 Silvers Rayleigh PSA 10 #OP08-118", V1428, LANGUAGE),  # 1829027
    ("2021 Pokemon Eevee Heroes Korean Umbreon VMAX Hyper Rare #095/069 PSA 10", V1935, LANGUAGE),  # 2288085
    ("POKEMON VAPOREON ex 2025 INDO SV8A I-TERASTAL FEST ex #205 SAR PSA 10 Indonesian #205", V191,
     LANGUAGE),  # 1717852
]
RED_CHALLENGE = "2019 Pokemon Sun & Moon Double Blaze FA Red’s Challenge 107/095 PSA 10 Gem"  # 2192713 on v1323


def reason_of(module: Any, title: str, row: dict) -> str | None:
    return module.title_listing_conflict(
        title, card_language=row["card_language"], identity_text=module.identity_text(row))


def expect(module: Any, *, passes: list, rejects: list) -> None:
    for title, row, tag in passes:
        got = reason_of(module, title, row)
        assert got is None, f"{title!r} ({tag}) should pass, got {got}"
    for title, row, want in rejects:
        got = reason_of(module, title, row)
        assert got == want, f"{title!r}: got {got}, want {want}"


def passes(*tags: str) -> list:
    return [f for f in MUST_PASS if f[2] in tags]


def rejects(reason: str) -> list:
    return [f for f in MUST_REJECT if f[2] == reason]


def gated(row: dict) -> dict:
    return {"variant_id": 1, "pc_product_id": 999001, "_pc_exact_product_gate": True, **row}


def sale(title: str) -> dict:
    return {"title": title, "ebay_itm": "123456789012", "ebay_url": "https://www.ebay.com/itm/123456789012",
            "price_usd": 91.0, "date": "2026-07-14"}


# ── checks (each takes the module under test) ──────────────────────────────

def check_stock_codes(m: Any) -> None:
    expect(m, passes=passes(STOCK), rejects=[])


def check_lot(m: Any) -> None:
    expect(m, passes=passes(STOCK, SEQ_SINGLE), rejects=rejects(LOT))


def check_damaged(m: Any) -> None:
    expect(m, passes=passes(CRACKED), rejects=rejects(DAMAGED))


def check_grade(m: Any) -> None:
    expect(m, passes=passes(NO_GRADE), rejects=rejects(GRADE))


def check_language(m: Any) -> None:
    expect(m, passes=MUST_PASS, rejects=rejects(LANGUAGE))


def check_verify_sale(m: Any) -> None:
    stats: Counter = Counter()
    for title, row, _ in MUST_REJECT:
        assert m.verify_sale(gated(row), sale(title), stats) is None, f"verify_sale kept {title!r}"
    for title, row, tag in MUST_PASS:
        assert m.verify_sale(gated(row), sale(title), stats) is not None, f"verify_sale dropped {title!r} ({tag})"
    want = dict(Counter(m.LISTING_REJECT_STAT[reason] for _, _, reason in MUST_REJECT))
    got = {key: value for key, value in stats.items() if key.startswith("reject_")}
    assert got == want, f"reject buckets {got} != {want}"


class FakeCursor:
    """catalog_printing_identity + catalog_variant rows, as CARD_IDENTITY_SQL returns them."""

    def __init__(self, rows: list[dict]):
        self.rows = rows
        self.queries: list[tuple[str, list]] = []
        self._result: list[dict] = []

    def execute(self, sql: str, params: list) -> None:
        self.queries.append((sql, list(params)))
        wanted = {int(p) for p in params}
        self._result = [r for r in self.rows if int(r["variant_id"]) in wanted]

    def fetchall(self) -> list[dict]:
        return self._result


MAP_ROW = {  # the real c11 map row shape: no collector_number / set / language
    "variant_id": 1323, "pc_product_id": 999002, "_pc_exact_product_gate": True,
    "card_name": "Red's Challenge", "pc_url": "https://www.pricecharting.com/game/x/y",
}


def check_attach_identity(m: Any) -> None:
    cursor = FakeCursor([{"variant_id": 1323, **V1323}])
    enriched = m.attach_card_identity(cursor, [MAP_ROW])
    assert cursor.queries and "catalog_printing_identity" in cursor.queries[0][0], cursor.queries
    assert enriched[0].get("collector_number") == "213" and enriched[0].get("card_language") == "en", enriched
    stats: Counter = Counter()
    assert m.verify_sale(enriched[0], sale(RED_CHALLENGE), stats) is None, "107/095 landed on 213/214"
    assert stats["reject_collector_contradiction"] == 1, dict(stats)
    # The bare map row is what ingest used to see: the check could never fire.
    assert m.verify_sale(dict(MAP_ROW), sale(RED_CHALLENGE), Counter()) is not None
    # A variant with no identity row keeps its row untouched.
    assert m.attach_card_identity(FakeCursor([]), [MAP_ROW]) == [MAP_ROW]


def scan_row(sale_id: int, title: str, row: dict) -> dict:
    return {"id": sale_id, "variant_id": 7, "sold_at": None, "unit_price_usd": "100", "quantity": 1,
            "transaction_value_usd": "100", "listing_title": title, **row}


def check_receipt(q: Any) -> None:
    rows = [scan_row(10 + i, title, row) for i, (title, row, _) in enumerate(MUST_REJECT)]
    rows += [scan_row(100 + i, title, row) for i, (title, row, _) in enumerate(MUST_PASS)]
    rows.append(scan_row(500, RED_CHALLENGE, V1323))
    # the collector rule keeps precedence: a Spanish title with a wrong number keeps that reason
    rows.append(scan_row(501, "PRE ES-PRISMATIC #150 GLACEON 206/187 PSA 10 Spanish", V1323))
    got = {e["saleObservationId"]: e["reason"] for e in q.build_receipt(rows)}
    want = {10 + i: reason for i, (_, _, reason) in enumerate(MUST_REJECT)}
    want[500] = want[501] = q.REASON_TITLE
    assert got == want, f"receipt reasons {got} != {want}"
    assert all(len(reason) <= 64 for reason in got.values()), "reason exceeds VARCHAR(64)"
    # without the card's language the language rule has nothing to compare against
    bare = scan_row(9, "2022 Pokemon SWSH Go JP Radiant Venusaur Rare #004/071 PSA 10 GEM MINT", V638)
    bare.pop("card_language")
    assert q.build_receipt([bare]) == [], "language judged without card_language"


def check_receipt_sql(q: Any) -> None:
    for name in ("SCAN_SQL", "STORED_SQL", "DETAIL_SQL"):
        sql = getattr(q, name)
        for column in ("p.collector_number", "p.card_language", "p.set_name", "v.canonical_name"):
            assert column in sql, f"{name} lacks {column}"
    doc = q.build_document(stamp="t", title_rows=[], sales_by_variant={},
                           detail_loader=lambda ids: [], stored_rows=[])
    assert q.DISCRIMINATOR_LISTING in doc["discriminators"], doc["discriminators"]


# ── plants ──────────────────────────────────────────────────────────────────

FAILED: list[str] = []


def twin(path: Path, fixed: str, buggy: str) -> types.ModuleType:
    source = SOURCES[path]
    assert source.count(fixed) == 1, f"plant anchor must be unique in {path.name}: {fixed!r}"
    name = path.stem + "_planted_twin"
    module = types.ModuleType(name)
    module.__file__ = str(path)
    sys.modules[name] = module
    try:
        exec(compile(source.replace(fixed, buggy), str(path), "exec"), module.__dict__)
    finally:
        sys.modules.pop(name, None)
    return module


def rule(name: str, check: Callable[[Any], None], module: Any, path: Path,
         plants: list[tuple[str, str]]) -> None:
    try:
        check(module)
        print(f"ok   {name}")
    except AssertionError as error:
        FAILED.append(name)
        print(f"FAIL {name}: {error}")
        return
    for fixed, buggy in plants:
        planted = twin(path, fixed, buggy)
        try:
            check(planted)
        except AssertionError as error:
            print(f"ok     plant fires: {buggy.strip()[:48]!r} -> {str(error)[:80]}")
            continue
        FAILED.append(f"{name}: plant {buggy.strip()[:48]!r} did not fire")
        print(f"FAIL   plant {buggy.strip()!r} did not fire")


X_QUANTITY = '    r"|(?<![\\w.])x\\s?[1-9]\\d?\\b(?!\\s*/|\\.\\d)"\n'
rule("L1 stock codes, Inferno X and Lv.X are single cards", check_stock_codes, C, C11_PATH, [
    (X_QUANTITY, '    r"|\\bx\\s*\\d+\\b"\n'),  # the old BUNDLE_RE x-token
    (X_QUANTITY, '    r"|x\\s?\\d+"\n'),
])
rule("L1 lots, pairs and sequential sets reject; split singles pass", check_lot, C, C11_PATH, [
    ('    r"\\b(?:lot|bundle|set of|pairs?)\\b"\n', '    r"\\b(?:lot|bundle|set of)\\b"\n'),
    (X_QUANTITY, '    r"|(?<![\\w.])x\\s?[1-9]\\d?\\b(?!\\s*/|\\.\\d)(?=never)"\n'),
    ('    r"|\\b[2-9]\\s?x\\b"\n', ''),
    ('    r"|\\b[2-9]\\s?pcs\\b|\\b[2-9]sets?\\b"\n', ''),
    ('    r"|\\+\\s*display\\b",\n', '    r"|(?!)",\n'),
    ('    if not SEQUENTIAL_RE.search(title):\n        return False\n', '    if True:\n        return False\n'),
    ('    if SEQUENTIAL_GROUP_RE.search(title) or "+" in title', '    if True or "+" in title'),
    ('    if len(_collector_claims(title)) >= 2:', '    if False:'),
    ('"+" in title or SEQUENTIAL_NUMBER_LIST_RE.search(title):', '"+" in title:'),
    ('            if "&" not in identity_cf:', '            if True:'),
    ('        elif not re.search(rf"\\b{cue}\\b", identity_cf):', '        elif False:'),
    ('SEQUENTIAL_RE = re.compile(r"(?<!part of )(?<!part of a )\\bsequential\\b", re.IGNORECASE)',
     'SEQUENTIAL_RE = re.compile(r"\\bsequential\\b", re.IGNORECASE)'),
])
rule("L2 damaged slab rejects; Cracked Ice and a Cracked card pass", check_damaged, C, C11_PATH, [
    ('    if title_damaged_slab(title, identity_text):', '    if False:'),
    ('|\\bcracked\\b(?!\\s+ice)", re.IGNORECASE)', '|\\bcracked\\b", re.IGNORECASE)'),
    ('    return not re.search(rf"\\b{re.escape(m.group(0))}\\b", identity_text or "", re.IGNORECASE)',
     '    return True'),
])
rule("L3 PSA 9 / 8 / AUTHENTIC reject; no grade, GEM Mint 10, PSA 0 pass", check_grade, C, C11_PATH, [
    ('    if title_other_psa_grade(title):', '    if False:'),
    ('graded?\\s*)?(10|[1-9])(?![\\w/.])"', 'graded?\\s*)?(\\d{1,2})(?![\\w/.])"'),
    ('    return bool(grades) or bool(PSA_AUTHENTIC_RE.search(title or ""))', '    return bool(grades)'),
    ('    if 10 in grades:\n        return False\n', '    if not grades:\n        return True\n'),
])
rule("L4 wrong-language sales reject; JP+English and own-identity languages pass", check_language, C, C11_PATH, [
    ('    if title_language_mismatch(title, card_language, identity_text):', '    if False:'),
    ('    return not (claims & have)', '    return bool(claims - have)'),
    ('CARD_LANGUAGE_CODES.get(language, language[:2].lower())} | language_claims(identity_text)',
     'CARD_LANGUAGE_CODES.get(language, language[:2].lower())}'),
    ('    claims |= {m.group(1).lower() for m in LANGUAGE_LABEL_RE.finditer(text or "")}', '    pass'),
])
rule("W verify_sale rejects through the shared screens", check_verify_sale, C, C11_PATH, [
    ('    if listing_reason is not None:', '    if False:'),
])
rule("W attach_card_identity revives the dead 107/095 check", check_attach_identity, C, C11_PATH, [
    ('                work[key] = str(found.get(key) or "")', '                pass'),
])
rule("R receipt gives stored sales per-class reasons", check_receipt, Q, Q_PATH, [
    ('    return title_listing_conflict(\n', '    return None and title_listing_conflict(\n'),
    ('        card_language=str(row.get("card_language") or ""),', '        card_language="",'),
    ('    if released_reason != REASON_TITLE and title_collector_contradiction(\n'
     '        title, str(row["collector_number"])\n    ):\n        return REASON_TITLE\n', ''),
])
rule("R receipt SQL carries the card identity", check_receipt_sql, Q, Q_PATH, [
    ('           p.card_language, p.set_name, v.canonical_name\n    FROM market_sale_observation s\n    INNER JOIN',
     '           p.set_name, v.canonical_name\n    FROM market_sale_observation s\n    INNER JOIN'),
    ('"discriminators": [DISCRIMINATOR_TITLE, DISCRIMINATOR_LISTING, DISCRIMINATOR_PRICE]',
     '"discriminators": [DISCRIMINATOR_TITLE, DISCRIMINATOR_PRICE]'),
])

print()
if FAILED:
    print(f"FAILED {len(FAILED)}: {FAILED}")
    raise SystemExit(1)
print("all pc title listing screen checks passed")
