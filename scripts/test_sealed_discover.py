from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipelines"))

from sealed_discover_lib import (  # noqa: E402
    expand_pc_image_sizes,
    grammar_full_name_en,
    grammar_full_name_ja,
    is_pc_box_url,
    match_pc_inventory,
    parse_pc_category_consoles,
    parse_pc_games_table,
    parse_pc_usd,
    pc_cover_urls,
    pc_slug_variants,
    pick_pc_inventory_box,
    score_snk_box,
    yahoo_jp_query,
    yahoo_query_needs_rewrite,
)


def test_snk_score_jp_quoted_set():
    sku = {
        "game": "ptcg",
        "lang": "jp",
        "name_en": "Triplet Beat",
        "name_jp": "強化拡張パック トリプレットビート",
        "print_wave": "std",
    }
    score, reason = score_snk_box(
        sku,
        'Pokemon Card Game Scarlet & Violet Enhanced Expansion Pack "Triplet Beat" Box',
        "ポケモンカードゲーム スカーレット&バイオレット 強化拡張パック「トリプレットビート」ボックス",
    )
    assert reason == "ok"
    assert score >= 0.5


def test_snk_rejects_promo_single():
    sku = {"game": "optcg", "lang": "jp", "name_en": "Romance Dawn", "name_jp": "ロマンスドーン", "print_wave": "wave2"}
    score, reason = score_snk_box(
        sku,
        "Cavendish C-P [OP01-008] (Booster Pack ROMANCE DAWN)",
        "キャベンディッシュ C-P [OP01-008] (ブースターパック ロマンスドーン BOX封入特典)",
    )
    assert reason == "not_box"
    assert score == 0.0


def test_snk_rejects_en_for_jp_sku():
    sku = {"game": "optcg", "lang": "jp", "name_en": "Romance Dawn", "name_jp": "ロマンスドーン", "print_wave": "std"}
    score, reason = score_snk_box(
        sku,
        "ONE PIECE Card Game Booster Pack Romance Dawn EN Box",
        "ワンピースカードゲーム ブースターパック ロマンスドーン 英語版 ボックス",
    )
    assert reason == "lang_mismatch"
    assert score == 0.0


def test_pc_cover_prefers_large_size():
    html = '''<div class="cover"><img src="https://storage.googleapis.com/images.pricecharting.com/kkqoyg3d24fssskh/240.jpg"></div>'''
    urls = pc_cover_urls(html)
    assert urls[0].endswith("/1600.jpg")
    assert expand_pc_image_sizes(urls[-1])[0].endswith("/1600.jpg")


def test_pc_box_url_and_slugs():
    assert is_pc_box_url("https://www.pricecharting.com/game/one-piece-romance-dawn/booster-box")
    assert not is_pc_box_url("https://www.pricecharting.com/game/one-piece-romance-dawn/elite-trainer-box")
    variants = pc_slug_variants("one-piece-japanese-romance-dawn", "wave1")
    assert variants[0].endswith("/booster-box-1st-edition")


def test_fullname_grammar_never_empty():
    product = {
        "game": "optcg",
        "lang": "jp",
        "name_en": "Romance Dawn",
        "name_jp": "ロマンスドーン",
        "product_kind": "booster-box",
        "print_wave": "wave1",
    }
    assert "ロマンスドーン" in grammar_full_name_ja(product)
    assert "1st Edition" in grammar_full_name_en(product)


def test_pc_games_table_is_row_bounded():
    html = """
    <link rel="canonical" href="https://www.pricecharting.com/console/one-piece-romance-dawn">
    <table id="games_table"><tbody>
    <tr id="product-1"><td class="title"><a href="/game/one-piece-romance-dawn/nami-op01-016">Nami OP01-016</a></td>
    <td class="price numeric used_price"><span class="js-price">$10.00</span></td></tr>
    <tr id="product-2"><td class="title"><a href="/game/one-piece-romance-dawn/booster-box">Booster Box </a></td>
    <td class="price numeric used_price"><span class="js-price">$1,456.44</span></td></tr>
    </tbody></table>
    """
    rows = parse_pc_games_table(html)
    assert [r["kind"] for r in rows] == ["card", "box"]
    assert rows[1]["hasPrice"] and rows[1]["ungraded"] == "$1,456.44"


def test_pc_category_skips_starter_and_nav_noise():
    html = """
    <a href="/console/nes">Nintendo NES</a>
    <a href="/console/one-piece-romance-dawn">One Piece OP01</a>
    <a href="/console/one-piece-japanese-starter-deck-1-straw-hat-crew">Japanese Starter Deck 1</a>
    """
    consoles = parse_pc_category_consoles(html)
    slugs = {c["slug"]: c["kind"] for c in consoles}
    assert slugs["one-piece-romance-dawn"] == "en-set"
    assert slugs["one-piece-japanese-starter-deck-1-straw-hat-crew"] == "skip"
    assert "nes" not in slugs


def test_pc_inventory_match_wave_and_usd():
    assert parse_pc_usd("$1,456.44") == 1456.44
    sku_w1 = {"game": "optcg", "lang": "en", "name_en": "Romance Dawn", "set_code": "OP-01", "print_wave": "wave1", "product_kind": "booster-box"}
    boxes = [
        {"slug": "booster-box", "title": "Booster Box", "console": "one-piece-romance-dawn", "consoleTitle": "One Piece OP01"},
        {"slug": "booster-box-blue-bottom", "title": "Booster Box [Blue Bottom]", "console": "one-piece-romance-dawn"},
    ]
    assert pick_pc_inventory_box(sku_w1, boxes)["slug"] == "booster-box-blue-bottom"
    by_console = {"one-piece-romance-dawn": boxes, "pokemon-korean-abyss-eye": [{"slug": "booster-box", "title": "Booster Box"}]}
    picked = match_pc_inventory(sku_w1, by_console, "https://www.pricecharting.com/console/one-piece-romance-dawn")
    assert picked and picked["slug"] == "booster-box-blue-bottom"
    assert match_pc_inventory({**sku_w1, "lang": "jp"}, by_console, "") is None


def test_yahoo_rewrite_wave_english():
    url = "https://auctions.yahoo.co.jp/closedsearch/closedsearch?p=ROMANCE+DAWN+Wave+1+BOX"
    assert yahoo_query_needs_rewrite(url, "jp")
    query = yahoo_jp_query("ロマンスドーン", "Romance Dawn", "wave1")
    assert "初版" in query and "BOX" in query


if __name__ == "__main__":
    ran = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"POSITIVE_OK {name}")
            ran += 1
    if ran < 1:
        raise SystemExit("no test_* functions ran")
    print(f"{ran} sealed discover tests passed")
