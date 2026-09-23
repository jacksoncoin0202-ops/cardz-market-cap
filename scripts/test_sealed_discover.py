from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipelines"))

import sealed_fullname_backfill as fullname  # noqa: E402
import sealed_live_qualify as live_qualify  # noqa: E402
import sealed_pc_inventory_ingest as pc_ingest  # noqa: E402
from sealed_discover_lib import (  # noqa: E402
    expand_pc_image_sizes,
    grammar_full_name_en,
    grammar_full_name_ja,
    insert_candidate_bind,
    is_pc_box_url,
    match_pc_inventory,
    parse_pc_category_consoles,
    parse_pc_games_table,
    parse_pc_usd,
    pc_cover_urls,
    pc_slug_variants,
    pick_pc_inventory_box,
    same_item_ids,
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


# Catalog names as they stand (2026-09-24), one Yahoo group each.
PTCG_JP = {"ptcg-jp": [(240, "Rebellion Crash", "反逆クラッシュ"), (242, "Shield", "シールド"), (243, "Sword", "ソード"),
                       (254, "Tag Bolt", "タッグボルト"), (276, "Sun & Moon (JP enhanced)", "強化拡張パック「サン&ムーン」"),
                       (277, "Collection Sun", "コレクションサン"), (185, "Mega Dream ex (High Class)", "メガドリームex"),
                       (191, "Glory of Team Rocket", "ロケット団の栄光"), (352, "Rocket Gang", "ロケット団"),
                       (181, "Storm Emeralda", "ストームエメラルダ")],
           "optcg-jp": [(48, "Premium Booster — The Best", "プレミアムブースター THE BEST"),
                        (50, "Premium Booster — The Best Vol.2", "プレミアムブースター THE BEST vol.2")]}


def _set_verdict(title, group, sealed_id):
    from sealed_collect import set_names
    from sealed_runtime import title_set_contamination

    return title_set_contamination(title, *set_names(PTCG_JP, group, sealed_id))["reason"] or "ok"


def test_yahoo_title_names_its_own_set():
    # 2026-09-23: every S2 title reads 'ソード＆シールド 拡張パック 反逆クラッシュ', and S1W ソード / S1H シールド are its
    # peers, so its own sales were rejected as foreign; PRB-02's 'THE BEST Vol.2' was read as PRB-01's 'THE BEST'.
    cases = [
        ("ポケモンカード ソード＆シールド 拡張パック 反逆クラッシュ BOX シュリンク付き", "ptcg-jp", 240, "ok"),
        ("ソード&シールド 拡張パック ソード 1BOX 未開封", "ptcg-jp", 243, "ok"),
        ("ソード&シールド 拡張パック ソード 1BOX 未開封", "ptcg-jp", 240, "own_set_name_missing"),
        ("ポケモンカード ソード＆シールド BOX 未開封", "ptcg-jp", 243, "own_set_name_missing"),  # the era alone names no set
        ("ソード&シールド 反逆クラッシュ シールド BOX", "ptcg-jp", 240, "foreign_set_in_title"),
        ("ポケモンカード 強化拡張パック サン&ムーン BOX", "ptcg-jp", 276, "ok"),
        ("サン&ムーン 拡張パック タッグボルト BOX", "ptcg-jp", 254, "ok"),
        ("サン&ムーン 拡張パック タッグボルト BOX", "ptcg-jp", 276, "own_set_name_missing"),
        ("ワンピースカード プレミアムブースター THE BEST Vol.2 BOX", "optcg-jp", 50, "ok"),
        ("ワンピースカード プレミアムブースター the best VOL.2 1BOX", "optcg-jp", 50, "ok"),
        ("ワンピースカード プレミアムブースター THE BEST Vol.2 BOX", "optcg-jp", 48, "own_set_name_missing"),
        ("ワンピースカード プレミアムブースター THE BEST BOX", "optcg-jp", 48, "ok"),
        ("ワンピースカード プレミアムブースター THE BEST BOX", "optcg-jp", 50, "own_set_name_missing"),
        ("Pokemon SWORD & SHIELD BOX", "ptcg-jp", 242, "own_set_name_missing"),  # the English era names no set either
        ("ポケモンカードゲーム ロケット団の栄光 BOX シュリンク付", "ptcg-jp", 191, "ok"),  # not jp4 ロケット団
        ("ポケモンカードゲーム MEGAドリームex & ロケット団の栄光 2BOXセット", "ptcg-jp", 191, "foreign_set_in_title"),
        ("ポケモンカード ストームエメラルド 1BOX シュリンク付き", "ptcg-jp", 181, "ok"),
        ("ポケモンカードゲーム MEGA 拡張パック ストームエメラルダ BOX", "ptcg-jp", 181, "ok"),
    ]
    wrong = [(t, sid, want, got) for t, g, sid, want in cases if (got := _set_verdict(t, g, sid)) != want]
    assert not wrong, "title set-name verdicts: %r" % wrong


def test_box_title_rejects_other_products():
    # 2026-09-24 rejudge dry run: these titles passed the box QC and would have priced a booster box.
    from sealed_runtime import qc_box_title

    other = ["[BOX無し]ポケモンカードゲーム ロケット団の栄光 アタッシュケースセット 1個入り",
             "ロケット団の栄光 アタッシュケースのみ ポケモンカードゲーム アタッシュケース boxなし",
             "新品未開封 テープ付き ポケモンカード ジャンボカードコレクション ミュウ 5BOX Vstarユニバース パック付き",
             "ポケモンカードゲーム ソード＆シールド ミステリーボックス 新品 未開封 シュリンク付き パラダイムトリガー",
             "ポケモンカードゲーム ソード&シールド プレミアムトレーナーボックス VSTAR スターバース 未開封 1BOX",
             "ポケモンカード 未開封 クロバットV シャイニーボックス シュリンク付き シャイニースターv",
             "ポケモンカードゲーム プレシャスコレクターボックス SWORD & SHIELD",
             "ポケモンカード インフェルノX BOXなし 30パック",
             "ポケモンカードゲーム MEGA グミ ニンジャスピナー 20個 BOX 未開封 食玩 ポケカ メガゲッコウガex",
             "ポケモンカードゲーム スペシャルBOX ポケモンセンタートウホク 1個 ＋ メガドリームex 2個セット",
             "ポケモンカード コレクションファイルセット リーリエ N メガゲンガーEX ムニキスゼロ BOX 計4点セット",
             "ワンピースカードゲーム BOX 6種セット ヒロインズ 決戦の刻 他",
             "ポケモンカードゲーム 未開封BOX 4種セット インフェルノX ストームエメラルダ アビスアイ メガドリームex",
             "ワンピースカードゲーム 【全てワンオーナー品】 蒼海の七傑他　新品未開封テープ付き8BOXセット",
             "ポケモンカード BOX 楽園ドラゴーナ他2種 シュリンク付き",
             "Pokemon JP SV11W + SV11B Booster Box Set Black Bolt & White Flare TCG Sealed US Japanese",
             "One Piece OP-10+OP 08 Royal Blood Booster Box ENGLISH SEALED",
             "ワンピースカードゲーム 神の島の冒険 OP-15 2BOX 分　４８パック 説明文必読",
             # 2026-09-24 QC: single cards and a box's loose cards counted as box sales
             "☆【ポケモンカードゲーム】MサーナイトEX 1枚/ディスピアーレイ/エクストラレギュレーションBOX/新品未使用/ ① 冷酷の反逆者",
             "ポケモンカード びっくりボックス 044/055 ナイトユニゾン 2枚セット",
             "ポケモンカードゲーム ソード＆シールド 強化拡張パック ダークファンタズマ 1BOX シュリンクなし（全160枚）",
             "ポケモンカードゲーム XY エクストラレギュレーションBOX 未開封",
             "ポケモンカード リザードンex 1枚 BOX出し",
             "ポケモンカード ナンジャモ SAR 3枚セット 未開封BOXから",
             "ポケモンカード ナンジャモ 091/071 SAR BOX出し",
             "ポケモンカード 空き箱 2BOX 漆黒のガイスト",
             "ポケモンカードゲーム 強化拡張パック ドリームリーグ 空パックとBOX",
             "【韓国語版】ポケモンカードゲーム ソード＆シールド 強化拡張パック 伝説の鼓動 1BOX 未開封シュリンク付き",
             "ポケモンカードゲーム 強化拡張パック ダークファンタズマ インドネシア語版 BOX",
             "Pokemon Sun & Moon Tormenta Celestial Storm Spanish Sealed Booster Box Spanish",
             "pokemon display Team Up 🇪🇸 - sealed, perfect - Booster box Team Up 🇪🇸",
             "【1円スタート】 ポケカ ゲーム 新品 未開封 ポケモンカード バトルリージョン 5 パック 強化 拡張 1/4 ボックス ソード＆シールド 希少",
             "ワンピースカード ボア・ハンコック OP02-059 UC 頂上決戦 BOX ONE PIECE",
             "ワンピースカードゲーム ボア・ハンコック パラレル【ブースターパック頂上決戦 Box封入特典】",
             "1BOX【新品・未開封】ポケモンカードゲーム/V-UNION スペシャルカードセット ミュウツー【送料無料】ボックス/箱/蒼空ストリーム/Pokemon",
             "Pokémon TCG: Sun & Moon Unified Minds Booster Box (36 Packs)( i m i t a t i o n)",
             "One Piece TCG OP-09 Emperors in the New World Booster Box English Bulk sale",
             "ポケモンカード SM9a ☆ びっくりボックス ☆ ゲンガー TRAINER'S グッズ ☆ ナイトユニゾン",
             "ポケモンカード シークレットボックス ACE 変幻の仮面 BOX出し"]
    boxes = ["ポケモンカードゲーム ソード＆シールド 拡張パック 白銀のランス 1BOX（シュリンクなし）",
             "ポケモンカードゲーム ソード＆シールド 強化拡張パック 白熱のアルカナ BOX シュリンク付き BOXケース付き",
             "ポケモンカード インフェルノx ペリペリなし 1BOX",
             "未開封 シュリンク付き ポケモンカードゲーム ロケット団の栄光 1BOX 保護ケース付き",
             "【購入専門様専用】他の人は購入しないでください。ポケモンカードゲーム サン＆ムーン 強化拡張パック ひかる伝説 1BOX",
             "ポケモンカード 黒炎の支配者 BOX シュリンク付き 他にも出品中",
             "ポケモンカード 変幻の仮面 1Box 30パック 新品未開封 【ヤマダ電機購入分】",
             "ポケモンカード　強化拡張パック「ウルトラフォース」SM5+　未開封ボックス",
             "新品 ポケモンカードゲーム ハイクラスパック MEGAドリームex BOX 10パック シュリンク付き 拡張パック ランダム10枚入り メガシンカ",
             "シュリンクあり メガブレイブ 拡張パック ポケモンカードゲーム MEGA BOX box ポケモン ポケカ ランダム５枚入り",
             "新品 ポケモンカードゲーム 黒炎の支配者 拡張パック 30パック ランダム5枚入りシュリンク付き BOX POKEMON",
             "ポケモンカード 白熱のアルカナ 1BOX 30パック入り 各5枚 シュリンク付き",
             "One Piece Card Game Paramount War OP02 Booster Box Sealed Japanese New In Stock #OP09-001",
             "Pokémon Pitch Black Booster Box (36 Packs) | New & Sealed | BULK AVAILABLE"]
    wrong = [t for t in other if qc_box_title(t)["accepted"]] + [t for t in boxes if not qc_box_title(t)["accepted"]]
    assert not wrong, "box title QC: %r" % wrong


def test_box_title_counts_boxes():
    # 2026-09-24: "OP-03 BOX" read 3 boxes, "BOX 10パック" and "Booster Box 24 Packs" the X of BOX, "2BOXセット" 1.
    from sealed_runtime import qc_box_title

    cases = [("反逆クラッシュ 2BOXセット シュリンク付き", 2), ("ワンピースカードゲーム 謀略の王国 OP-04 BOX", 1), ("One Piece OP05 Box sealed", 1),
             ("ハイクラスパック MEGAドリームex BOX 10パック シュリンク付き ランダム10枚入り", 1), ("蒼空ストリーム BOX 未開封パック×13", 1),
             ("One Piece Booster Box 24 Packs", 1), ("Booster Box x 24 packs", 1), ("反逆クラッシュ BOX×3", 3),
             ("500年後の未来 OP-07 BOX 2個セット", 2), ("未開封BOX 8個セット 師弟の絆×4", 8), ("テラスタルフェスex BOX 2セット", 2),
             ("メガドリームex 4点セット BOX", 4), ("ムニキスゼロ 5BOXセット", 5), ("MEGAドリーム ex 10 BOX セット", 10),
             ("反逆クラッシュ BOX x2", 2), ("BOX パック 12個", 1), ("頂上決戦 BOX 全6種", 1), ("４ボックス 決戦の刻", 4),
             ("ポケモンカード151 2BOX", 2), ("Pokemon 151 Booster Box", 1), ("テラスタルフェスex\u30005BOXセット", 5),
             ("【シュリンク、ローダー付き_2点目】ポケモンカードゲーム 拡張パック 未来の一閃 BOX", 1),
             ("Pokemon Card Incandescent Arcana Booster Box 2 Set s11a Japanese", 2)]
    rejects = [("BOX用プラスチック保護ケース 5枚", "not_booster_box"), ("ハーフBOX用プラスチックケース 白熱のアルカナ", "not_booster_box"),
               ("ポケモンカードゲーム ハイクラスロングカードボックス ロケット団", "not_booster_box"),
               ("ドリームex 5 パック セット 1/2 ボックス", "box_equivalent_lot"), ("テラスタルフェスex 5 パック 1/2BOX 分", "box_equivalent_lot"),
               ("頂上決戦 ボックス購入特典パック OP-02 全6種", "promo_card")]
    wrong = [(t, want, qc_box_title(t)) for t, want in cases if (qc_box_title(t)["accepted"], qc_box_title(t)["quantity"]) != (True, want)]
    wrong += [(t, want, qc_box_title(t)) for t, want in rejects if qc_box_title(t)["reason"] != want]
    assert not wrong, "box count: %r" % wrong


def test_yahoo_query_drops_quote_brackets():
    # SM1+'s name_jp is 強化拡張パック「サン&ムーン」; sellers type 強化拡張パック サン&ムーン, so a bracketed query found nothing.
    query = yahoo_jp_query("強化拡張パック「サン&ムーン」", "Sun & Moon (JP enhanced)", "std")
    assert query == "ポケモンカード 強化拡張パック サン&ムーン BOX", query


REJUDGE_DB = """
CREATE TABLE catalog_sealed_product (id INTEGER PRIMARY KEY, sku_id TEXT, slug TEXT, group_code TEXT, status TEXT,
  name_en TEXT, name_jp TEXT);
CREATE TABLE market_sealed_sale_observation (id INTEGER PRIMARY KEY, sealed_id INTEGER, source_code TEXT, parser TEXT,
  title TEXT, native_price REAL, native_currency TEXT, quantity INTEGER, total_native_price REAL, unit_price_usd REAL,
  metric_status TEXT);
CREATE TABLE market_fx_rate_observation (base_currency TEXT, quote_currency TEXT, rate REAL, effective_date TEXT);
INSERT INTO market_fx_rate_observation VALUES ('USD', 'JPY', 100.0, '2026-09-23'), ('USD', 'JPY', 90.0, '2026-09-01');
INSERT INTO catalog_sealed_product VALUES
  (240, 'ptcg:jp:S2:booster-box:std', 's2', 'ptcg-jp', 'active', 'Rebellion Crash', '反逆クラッシュ'),
  (242, 'ptcg:jp:S1H:booster-box:std', 's1h', 'ptcg-jp', 'active', 'Shield', 'シールド'),
  (243, 'ptcg:jp:S1W:booster-box:std', 's1w', 'ptcg-jp', 'active', 'Sword', 'ソード'),
  (299, 'ptcg:jp:XX:booster-box:std', 'xx', 'ptcg-jp', 'no-box', 'Nothing', 'ナッシング');
INSERT INTO market_sealed_sale_observation VALUES
  (1, 240, 'yahoo', 'yahoo_closedsearch_v1', 'ソード＆シールド 拡張パック 反逆クラッシュ BOX', 20000, 'JPY', 1, 20000, NULL, 'rejected_foreign_set_in_title'),
  (2, 240, 'yahoo', 'yahoo_closedsearch_v1', 'ポケモンカード 拡張パック シールド BOX', 15000, 'JPY', 1, 15000, 150.0, 'ok'),
  (3, 240, 'yahoo', 'yahoo_closedsearch_v1', 'ソード＆シールド 拡張パック 反逆クラッシュ BOX', 90000, 'JPY', 1, 90000, NULL, 'quarantined'),
  (4, 240, 'yahoo', 'yahoo_closedsearch_v1', 'ソード＆シールド 反逆クラッシュ 1パック', 300, 'JPY', 1, 300, NULL, 'rejected_single_pack'),
  (5, 240, 'yahoo', 'yahoo_closedsearch_v1', '反逆クラッシュ BOX シュリンク付き', 60000, 'JPY', 1, 60000, 600.0, 'outlier_trimmed'),
  (6, 240, 'yahoo', 'yahoo_closedsearch_v1', '反逆クラッシュ 空箱', 500, 'JPY', 1, 500, NULL, 'rejected_own_set_name_missing'),
  (7, 240, 'snkrdunk', 'snk_history_v1', 'ソード＆シールド 拡張パック シールド BOX', 1, 'JPY', 1, 1, 1.0, 'ok'),
  (8, 243, 'yahoo', 'yahoo_closedsearch_v1', 'ソード&シールド BOX', 18000, 'JPY', 1, 18000, 180.0, 'ok'),
  (9, 299, 'yahoo', 'yahoo_closedsearch_v1', 'ナッシング BOX', 1, 'JPY', 1, 1, 1.0, 'ok'),
  (10, 240, 'yahoo', 'yahoo_closedsearch_v1', 'ポケモンカード 拡張パック ソード BOX', 12000, 'JPY', 1, 12000, NULL, 'quarantined'),
  (11, 240, 'yahoo', 'yahoo_closedsearch_v1', '反逆クラッシュ アタッシュケース BOX', 9000, 'JPY', 1, 9000, 90.0, 'ok'),
  (12, 240, 'yahoo', 'yahoo_closedsearch_v1', 'ナッシング & 反逆クラッシュ 2BOXセット', 20000, 'JPY', 1, 20000, 100.0, 'ok'),
  (13, 240, 'yahoo', 'yahoo_closedsearch_v1', '反逆クラッシュ アタッシュケース BOX', 9000, 'JPY', 1, 9000, NULL, 'rejected_single_pack'),
  (14, 240, 'yahoo', 'yahoo_closedsearch_v1', '反逆クラッシュ 2BOXセット シュリンク付き', 40000, 'JPY', 1, 40000, 444.44, 'ok'),
  (15, 240, 'yahoo', 'yahoo_closedsearch_v1', '反逆クラッシュ BOX 10パック', 1500, 'JPY', 10, 15000, NULL, 'rejected_foreign_set_in_title'),
  (16, 240, 'ebay', 'pc_page_v1', 'Rebellion Crash Booster Box 24 Packs', 120, 'USD', 24, 120, 5.0, 'ok'),
  (17, 240, 'ebay', 'ebay_import_v1', 'Rebellion Crash Booster Box 24 Packs', 120, 'USD', 24, 120, 5.0, 'ok'),
  (18, 240, 'ebay', 'pc_page_v1', 'Rebellion Crash Premium Trainer Box', 50, 'USD', 1, 50, 50.0, 'ok'),
  (19, 240, 'ebay', 'pc_page_v1', 'Rebellion Crash Booster Box', 110, 'USD', 1, 110, NULL, 'rejected_foreign_set_in_title'),
  (20, 240, 'ebay', 'pc_page_v1', 'Pokemon S2 Rebel Clash Japanese Booster Box', 100, 'USD', 1, 100, 100.0, 'ok'),
  (21, 240, 'ebay', 'pc_page_v1', 'Rebellion Crash Booster Box x2 sealed', 220, 'USD', 1, 220, 220.0, 'ok');
"""


def test_rejudge_sales_moves_only_what_todays_title_says():
    # Sales are INSERT IGNORE by lot, so the 2026-09-24 title QC and box-count fixes never reach a row already written.
    # Today's QC takes any counted row down; only a yahoo set-name reject comes back; a row that stays or comes back
    # takes today's box count at the rate it was priced at; a quarantine, another reject or an eBay import is not read.
    import sealed_operator as so

    conn, logs = DecimalConn(REJUDGE_DB), []
    so.load_env, so.db, so._log_catalog_change = (lambda: None), (lambda: conn), logs.append

    def state():
        cur = LiteConn.cursor(conn)
        cur.execute("SELECT id, metric_status, unit_price_usd, quantity, native_price FROM market_sealed_sale_observation ORDER BY id")
        return {r["id"]: (r["metric_status"], r["unit_price_usd"], r["quantity"], r["native_price"]) for r in cur.fetchall()}

    before = state()
    dry = so.cmd_sealed_rejudge_sales(source="yahoo", skus=[], actor="t", note="n", dry_run=True)
    assert state() == before and not logs, "a dry run wrote: %r" % (state(),)
    assert dry["skus"] == 2, "S2 and S1W, the SKUs with yahoo rows, and not the no-box one: %r" % dry["skus"]
    so.cmd_sealed_rejudge_sales(source="yahoo", skus=[], actor="t", note="n")
    after = state()
    assert after[1] == ("ok", 200.0, 1, 20000), "S2's own title stayed rejected or was priced off an old rate: %r" % (after[1],)
    assert after[2][:2] == ("rejected_own_set_name_missing", None), "S1H's title stayed counted on S2: %r" % (after[2],)
    assert after[8][:2] == ("rejected_own_set_name_missing", None), "a bare era title stayed counted on S1W: %r" % (after[8],)
    assert after[6][:2] == ("rejected_opened_or_empty", None), "an empty box kept its set-name reason: %r" % (after[6],)
    assert after[11][:2] == ("rejected_not_booster_box", None), "an attache case stayed counted: %r" % (after[11],)
    assert after[12][:2] == ("rejected_foreign_set_in_title", None), "a no-box set in a bundle title was not foreign: %r" % (after[12],)
    assert after[14] == ("ok", 222.22, 2, 20000), "a 2BOX lot stayed one box, or lost the rate it was priced at: %r" % (after[14],)
    assert after[15] == ("ok", 150.0, 1, 15000), "'BOX 10パック' came back as 10 boxes: %r" % (after[15],)
    kept = {i: before[i] for i in (3, 4, 5, 7, 9, 10, 13, 16, 17, 18, 19, 20, 21)}
    assert {i: after[i] for i in kept} == kept, "moved a row that is not its call: %r" % ({i: after[i] for i in kept},)
    assert sorted(m["id"] for m in logs[0]["rows"]) == [1, 2, 6, 8, 11, 12, 14, 15], logs

    ebay = so.cmd_sealed_rejudge_sales(source="ebay", skus=[], actor="t", note="n")
    final = state()
    assert ebay["skus"] == 1 and sorted(m["id"] for m in ebay["rows"]) == [16, 18, 21], ebay["rows"]
    assert final[21] == ("ok", 110.0, 2, 220), "an eBay lot of two lost its lot price or stayed one box: %r" % (final[21],)
    assert final[16] == ("ok", 120.0, 1, 120), "'Booster Box 24 Packs' stayed 24 boxes: %r" % (final[16],)
    assert final[18][:2] == ("rejected_not_booster_box", None), "a trainer box stayed counted: %r" % (final[18],)
    assert final[17] == before[17] and final[19] == before[19], "an eBay import or a set-name reject was read: %r" % (final,)
    assert final[20] == before[20], "an English eBay title was judged by the Yahoo set names: %r" % (final[20],)
    try:
        so.cmd_sealed_rejudge_sales(source="yahoo", skus=["xx"], actor="t", note="n")
    except SystemExit as exc:
        assert "no-box" in str(exc), exc
    else:
        raise AssertionError("a no-box SKU has no peers to judge by and must be refused")


class BindCursor:
    """catalog_sealed_source_identity in memory (all rows snkrdunk). The item lookup answers only the ids the query
    names, so a lookup that leaves out a spelling misses that row the way MySQL would; every statement recorded."""

    def __init__(self, *rows):
        self.rows, self.sql, self.last = [dict(r) for r in rows], [], []

    def execute(self, sql, params=()):
        sql = " ".join(sql.split())
        self.sql.append(sql)
        if sql.startswith("SELECT") and "external_entity_id IN" in sql:
            ids = params[1:]
            self.last = [r for r in self.rows if r["external_entity_id"] in ids]
        elif sql.startswith("SELECT") and "WHERE sealed_id=%s" in sql:
            self.last = [r for r in self.rows if r["sealed_id"] == params[0] and r["match_status"] != "rejected"]
        elif sql.startswith("SELECT"):
            raise AssertionError(f"unexpected lookup: {sql}")

    def fetchone(self):
        return self.last[0] if self.last else None

    def fetchall(self):
        return list(self.last)


def _row(sealed_id, ext, status):
    return {"sealed_id": sealed_id, "external_entity_id": ext, "match_status": status}


def _bind(cur, sealed_id, ext="apparels:552991"):
    return insert_candidate_bind(cur, source="snkrdunk", external_id=ext, sealed_id=sealed_id,
                                 url="https://snkrdunk.com/" + ext.replace(":", "/"), note="{}", origin="search")


def _writes(cur):
    return [s for s in cur.sql if s.startswith(("UPDATE", "INSERT"))]


def test_candidate_bind_never_reopens_a_reject():
    # 2026-09-23: the weekly SNK search found apparels:552991 (a DIESEL T-shirt) for BW1B again and flipped its
    # rejected row back to candidate, one bulk accept away from pricing a box off a T-shirt.
    cur = BindCursor(_row(307, "apparels:552991", "rejected"))
    status = _bind(cur, 307)
    assert status == "already_rejected" and not _writes(cur), "a rejected item was reopened: %r %r" % (status, cur.sql)
    cur = BindCursor(_row(307, "apparels:552991", "candidate"))
    assert _bind(cur, 307) == "updated" and cur.sql[-1].startswith("UPDATE"), cur.sql
    cur = BindCursor()
    assert _bind(cur, 356, "apparels:881421") == "inserted" and cur.sql[-1].startswith("INSERT"), cur.sql
    cur = BindCursor(_row(41, "apparels:767625", "exact"))
    assert _bind(cur, 45, "apparels:767625") == "conflict_exact" and len(cur.sql) == 1, cur.sql


def test_candidate_bind_sees_every_snk_spelling():
    # 2026-09-23: SNK's adapter fetches /v1/apparels/{itemId} whatever the prefix. OP-01 EN (id 1) held
    # trading-cards:136031, the JP box OP-01 JP (id 2) holds as apparels:136031; six boxes sat on two SKUs each.
    assert same_item_ids("snkrdunk", "trading-cards:136031") == ("trading-cards:136031", "apparel-groups:136031", "apparels:136031")
    assert same_item_ids("pricecharting", "one-piece-romance-dawn/booster-box") == ("one-piece-romance-dawn/booster-box",)
    cur = BindCursor(_row(2, "apparels:136031", "exact"))
    status = _bind(cur, 1, "trading-cards:136031")
    assert status == "conflict_exact" and not _writes(cur), \
        "OP-01 EN took the OP-01 JP box under its other spelling: %r %r" % (status, cur.sql)
    cur = BindCursor(_row(307, "apparels:552991", "rejected"))
    status = _bind(cur, 307, "trading-cards:552991")
    assert status == "already_rejected" and not _writes(cur), "a reject must hold under every spelling: %r %r" % (status, cur.sql)
    cur = BindCursor(_row(356, "apparels:881421", "candidate"))
    status = _bind(cur, 356, "trading-cards:881421")
    assert status == "already_bound" and not _writes(cur), "one item is one row per SKU: %r %r" % (status, cur.sql)
    # Another SKU's reject rules on that SKU only; the row still owns its key, so that exact id cannot be added.
    cur = BindCursor(_row(242, "apparels:58920", "rejected"))
    status = _bind(cur, 999, "apparels:58920")
    assert status == "conflict_other" and not _writes(cur), "that key is another SKU's row: %r %r" % (status, cur.sql)
    cur = BindCursor(_row(242, "apparels:58920", "rejected"))
    assert _bind(cur, 999, "trading-cards:58920") == "inserted" and cur.sql[-1].startswith("INSERT"), cur.sql


def test_candidate_bind_never_demotes_an_accept():
    # 2026-09-23 a scan's PC inventory ingest walks every SKU; it found OP-02 EN's accepted box again and set the row
    # back to candidate (193 accepted PC binds), overwriting the note that named the product.
    for source, ext in (("pricecharting", "one-piece-paramount-war/booster-box"), ("snkrdunk", "trading-cards:136031")):
        cur = BindCursor(_row(5, ext, "exact"))
        status = insert_candidate_bind(cur, source=source, external_id=ext, sealed_id=5, url="https://x/" + ext,
                                       note="{}", origin="console-inventory")
        assert status == "already_exact" and not _writes(cur), \
            "an accepted bind was demoted to candidate: %r %r" % (status, cur.sql)


class NameCursor:
    """Answers the full-name loaders by table: source freezes, then identity rows of the queried source."""

    def __init__(self, freezes, idents):
        self.freezes, self.idents, self.last = freezes, idents, []

    def execute(self, sql, params=()):
        if "operator_sealed_binding_freeze" in sql:
            self.last = self.freezes
        else:
            source = "snkrdunk" if "'snkrdunk'" in sql else "pricecharting"
            self.last = [r for r in self.idents if r["source"] == source]

    def fetchall(self):
        return self.last


def test_fullname_only_from_accepted_binds():
    # 2026-09-23 dry run: the backfill would have named BW1B after a DIESEL T-shirt and BW1W after the Shiny
    # Collection box, both unreviewed SNK candidates. Only an accepted source bind may name a product.
    def snk(en, ja=""):
        return json.dumps({"snkName": en, "snkLocalized": ja})

    freezes = [
        {"sealed_id": 41, "source_code": "snkrdunk", "external_entity_id": "trading-cards:767625", "acceptance_status": "accepted"},
        {"sealed_id": 69, "source_code": "pricecharting", "external_entity_id": "pokemon-151/booster-box", "acceptance_status": "accepted"},
        {"sealed_id": 308, "source_code": "snkrdunk", "external_entity_id": "apparels:480865", "acceptance_status": "revoked"},
    ]
    idents = [
        {"source": "snkrdunk", "sealed_id": 41, "external_entity_id": "trading-cards:767625", "note": snk("EB-03 EN Box", "EB-03 英語版")},
        {"source": "snkrdunk", "sealed_id": 307, "external_entity_id": "apparels:552991", "note": snk('DIESEL T-BOXT-R29 "BLACK"')},
        {"source": "snkrdunk", "sealed_id": 308, "external_entity_id": "apparels:480865", "note": snk("Shiny Collection 1ED Box")},
        {"source": "pricecharting", "sealed_id": 69, "external_entity_id": "pokemon-151/booster-box",
         "note": json.dumps({"pcName": "151 Booster Box"}), "canonical_url": None},
        {"source": "pricecharting", "sealed_id": 35, "external_entity_id": "yugioh-x/booster-box",
         "note": json.dumps({"pcName": "Yu-Gi-Oh Booster Box"}), "canonical_url": None},
    ]
    cur = NameCursor(freezes, idents)
    accepted = fullname.accepted_binds(cur)
    names = fullname.load_snk_names(cur, accepted), fullname.load_pc_names(cur, accepted)
    assert names == ({41: {"en": "EB-03 EN Box", "ja": "EB-03 英語版", "source": "snkrdunk"}},
                     {69: {"en": "151 Booster Box", "ja": "", "source": "pricecharting"}}), \
        "only an accepted bind may name a product: %r" % (names,)


class QualifyCursor:
    """live-qualify's two reads (products, candidate binds) from fixed rows; every statement recorded."""

    def __init__(self, products, candidates):
        self.products, self.candidates, self.sql, self.last, self.rowcount = products, candidates, [], [], 0

    def execute(self, sql, params=()):
        sql = " ".join(sql.split())
        self.sql.append((sql, params))
        self.last = (self.products if "FROM catalog_sealed_product" in sql
                     else self.candidates if "WHERE match_status='candidate'" in sql else [])
        self.rowcount = 1 if sql.startswith("UPDATE") else 0

    def fetchall(self):
        return list(self.last)


class QualifyConn:
    def __init__(self, cur):
        self.cur = cur

    def cursor(self):
        return self.cur

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


def test_live_qualify_apply_never_accepts():
    # 2026-08-14 live-qualify --apply accepted all 451 sealed source binds and froze 334 box images, none looked at:
    # /box showed a Game Boy jukebox on BW2 and JP boxes behind EN SKUs. OP-06 EN got its JP box from a re-score
    # on the English name alone; BW1B/BW1W got DIESEL T-shirts frozen as box art.
    def product(sid, sku, game, lang, en, jp):
        return {"id": sid, "sku_id": sku, "slug": sku.replace(":", "-").lower(), "game": game, "lang": lang,
                "group_code": f"{game}-{lang}", "set_code": sku.split(":")[2], "name_en": en, "name_jp": jp,
                "print_wave": "std", "product_kind": "booster-box", "status": "active"}

    def cand(sid, ext, en, ja):
        return {"sealed_id": sid, "source_code": "snkrdunk", "external_entity_id": ext, "canonical_url": None,
                "note": json.dumps({"snkName": en, "snkLocalized": ja}), "match_status": "candidate", "resolved": 1}

    products = [product(13, "optcg:en:OP-06:booster-box:std", "optcg", "en", "Wings of the Captain", "双璧の覇者"),
                product(307, "ptcg:jp:BW1B:booster-box:std", "ptcg", "jp", "Black Collection", "ブラックコレクション"),
                product(356, "ptcg:jp:M6a:booster-box:std", "ptcg", "jp", "30th Celebration Collection", "拡張パック 30th CELEBRATION")]
    candidates = [cand(13, "trading-cards:145974", "ONE PIECE Card Game Wings Of The Captain Box", "ワンピースカードゲーム 双璧の覇者 ボックス"),
                  cand(307, "apparels:552991", 'DIESEL T-BOXT-R29 "BLACK"', 'ディーゼル ティーボックスティーアール 29 "ブラック"'),
                  cand(356, "apparels:881421", 'Pokemon Card Game MEGA Expansion Pack "30th CELEBRATION" Box',
                       "ポケモンカードゲームMEGA 拡張パック「30th CELEBRATION」ボックス")]
    cur = QualifyCursor(products, candidates)
    live_qualify.load_env, live_qualify.db = (lambda: None), (lambda: QualifyConn(cur))
    live_qualify.OUT_DIR = Path(tempfile.mkdtemp(prefix="live-qualify-"))
    assert live_qualify.run(apply=True) == 0
    writes = [s for s in cur.sql if not s[0].startswith("SELECT")]
    assert len(writes) == 1 and "SET match_status='rejected'" in writes[0][0] and writes[0][1][2] == "apparels:552991", \
        "live-qualify must never accept or freeze, only reject junk: %r" % (writes,)
    receipt = json.loads((live_qualify.OUT_DIR / "live-qualify-receipt.json").read_text(encoding="utf-8"))
    verdict = {d["ext"]: (d["decision"], d["reason"]) for key in ("rejectSample", "holdSample", "acceptSample") for d in receipt[key]}
    assert verdict["trading-cards:145974"] == ("hold", "snk_lang_mismatch"), "OP-06 EN read its JP box as EN: %r" % (verdict,)
    assert verdict["apparels:881421"][0] == "accept" and receipt["applied"] == {"rejected": 1}, \
        "an accept verdict is listed, never applied: %r" % ((verdict, receipt["applied"]),)


class LiteConn:
    """MySQL-flavoured SQL on in-memory sqlite (%s -> ?), dict rows, so the ingest's own queries run for real.
    close() keeps the data for the asserts."""

    def __init__(self, script):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(script)

    def cursor(self):
        return LiteCursor(self.conn.cursor())

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()

    def close(self):
        pass


class LiteCursor:
    def __init__(self, cur):
        self.cur, self.rowcount = cur, 0

    def execute(self, sql, params=()):
        self.cur.execute(sql.replace("%s", "?"), tuple(params or ()))
        self.rowcount = self.cur.rowcount

    def fetchone(self):
        row = self.cur.fetchone()
        return dict(row) if row else None

    def fetchall(self):
        return [dict(r) for r in self.cur.fetchall()]


class DecimalConn(LiteConn):
    """pymysql hands DECIMAL columns back as Decimal (2026-09-24: the first real dry run died in json.dumps on one)."""

    def cursor(self):
        cur = super().cursor()
        rows = cur.fetchall
        cur.fetchall = lambda: [{k: (Decimal(str(v)) if k in DECIMAL_COLUMNS and v is not None else v) for k, v in r.items()}
                                for r in rows()]
        return cur


DECIMAL_COLUMNS = ("unit_price_usd", "native_price", "total_native_price", "rate")


PC_GAME = "https://www.pricecharting.com/game/"
PC_DB = f"""
CREATE TABLE catalog_sealed_product (id INTEGER PRIMARY KEY, sku_id TEXT, game TEXT, lang TEXT, group_code TEXT,
  set_code TEXT, name_en TEXT, print_wave TEXT, product_kind TEXT, status TEXT);
CREATE TABLE catalog_sealed_source_hint (sealed_id INTEGER, source_code TEXT, url TEXT);
CREATE TABLE catalog_sealed_source_identity (source_code TEXT, external_entity_id TEXT, sealed_id INTEGER, canonical_url TEXT,
  match_status TEXT, resolved INTEGER, evidence_sha256 TEXT, note TEXT, PRIMARY KEY (source_code, external_entity_id));
CREATE TABLE operator_sealed_binding_freeze (sealed_id INTEGER, freeze_kind TEXT, source_code TEXT, external_entity_id TEXT,
  acceptance_status TEXT, PRIMARY KEY (sealed_id, freeze_kind, source_code));
INSERT INTO catalog_sealed_product VALUES
  (5, 'optcg:en:OP-02:booster-box:std', 'optcg', 'en', 'optcg-en', 'OP-02', 'Paramount War', 'std', 'booster-box', 'active'),
  (179, 'ptcg:en:JU:booster-box:std', 'ptcg', 'en', 'ptcg-en', 'JU', 'Jungle', 'std', 'booster-box', 'active'),
  (400, 'ptcg:en:ME02:booster-box:std', 'ptcg', 'en', 'ptcg-en', 'ME02', 'Phantasmal Flames', 'std', 'booster-box', 'active');
INSERT INTO catalog_sealed_source_identity VALUES
  ('pricecharting', 'one-piece-paramount-war/booster-box', 5, '{PC_GAME}one-piece-paramount-war/booster-box', 'exact', 1, '', '{{}}'),
  ('pricecharting', 'pokemon-jungle/booster-box-1st-edition', 179, '{PC_GAME}pokemon-jungle/booster-box-1st-edition', 'candidate', 1, '', '{{}}'),
  ('pricecharting', 'pokemon-jungle/booster-box', 179, '{PC_GAME}pokemon-jungle/booster-box', 'candidate', 1, '', '{{}}');
INSERT INTO operator_sealed_binding_freeze VALUES
  (5, 'source', 'pricecharting', 'one-piece-paramount-war/booster-box', 'accepted'),
  (179, 'source', 'pricecharting', 'pokemon-jungle/booster-box-1st-edition', 'accepted');
"""


def test_pc_inventory_prices_only_the_accepted_item():
    # 2026-09-23 a scan's PC inventory ingest priced every SKU it matched: JU EN, frozen on the 1st edition Jungle box,
    # got the unlimited box's price from its candidate row, which /box showed. A candidate is unreviewed.
    def box(console, slug, pid, usd):
        return {"console": console, "consoleTitle": console.replace("-", " ").title(), "slug": slug, "title": slug,
                "href": f"{PC_GAME}{console}/{slug}", "pid": pid, "ungraded": f"${usd:,.2f}"}

    inventory = Path(tempfile.mkdtemp(prefix="pc-inventory-")) / "console-inventory.json"
    inventory.write_text(json.dumps({"boxes": [
        box("one-piece-paramount-war", "booster-box", "p5", 180.0),
        box("pokemon-jungle", "booster-box", "p179u", 13584.68),
        box("pokemon-jungle", "booster-box-1st-edition", "p179f", 14375.0),
        box("pokemon-phantasmal-flames", "booster-box", "p400", 210.0),
    ]}), encoding="utf-8")
    conn, priced = LiteConn(PC_DB), []
    pc_ingest.db = lambda: conn
    pc_ingest.warehouse_sealed = lambda cur, **kw: None
    pc_ingest.upsert_sealed_price = lambda cur, **kw: priced.append((kw["sealed_id"], kw["external_entity_id"], kw["price_usd"]))
    doc = pc_ingest.ingest_inventory(inventory)
    assert doc["statuses"] == {"already_exact": 1, "updated": 1, "inserted": 1}, doc["statuses"]
    assert not [p for p in priced if p[0] == 179], "JU EN took the unlimited Jungle box's price from a candidate: %r" % priced
    assert not [p for p in priced if p[0] == 400], "an unreviewed candidate priced a new SKU: %r" % priced
    assert priced == [(5, "one-piece-paramount-war/booster-box", 180.0)], "the accepted item keeps today's table price: %r" % priced
    cur = conn.cursor()
    cur.execute("SELECT match_status FROM catalog_sealed_source_identity WHERE sealed_id=5")
    assert cur.fetchall() == [{"match_status": "exact"}], "the ingest demoted OP-02 EN's accepted bind"


COMPOSE_DB = """
CREATE TABLE operator_sealed_binding_freeze (sealed_id INTEGER, freeze_kind TEXT, source_code TEXT, external_entity_id TEXT,
  acceptance_status TEXT, PRIMARY KEY (sealed_id, freeze_kind, source_code));
CREATE TABLE market_sealed_price_observation (sealed_id INTEGER, source_code TEXT, price_kind TEXT, observed_date TEXT,
  native_price REAL, native_currency TEXT, price_usd REAL, external_entity_id TEXT, source_url TEXT, metric_status TEXT,
  ingest_run_key TEXT, PRIMARY KEY (sealed_id, source_code, price_kind, observed_date));
CREATE TABLE market_sealed_sale_observation (id INTEGER PRIMARY KEY, sealed_id INTEGER, source_code TEXT, sold_at TEXT,
  unit_price_usd REAL, quantity INTEGER, metric_status TEXT);
INSERT INTO operator_sealed_binding_freeze VALUES
  (1, 'source', 'snkrdunk', 'apparels:100', 'accepted'),
  (1, 'source', 'pricecharting', 'pokemon-x-&-y/booster-box', 'accepted'),
  (1, 'image', 'snkrdunk', 'apparels:999', 'accepted'),
  (2, 'source', 'snkrdunk', 'apparels:200', 'rejected');
INSERT INTO market_sealed_price_observation (sealed_id, source_code, price_kind, observed_date, price_usd, native_price,
  external_entity_id, metric_status) VALUES
  (1, 'snkrdunk', 'market', '2026-09-01', 100, 15000, 'trading-cards:100', 'ok'),
  (1, 'snkrdunk', 'market', '2026-09-02', 900, 135000, 'apparels:999', 'ok'),
  (1, 'pricecharting', 'market', '2026-09-01', 50, 50, 'pokemon-x-%26-y/booster-box', 'ok'),
  (1, 'pricecharting', 'market', '2026-09-02', 70, 70, 'pokemon-x-&-y/booster-box-1st-edition', 'ok'),
  (2, 'snkrdunk', 'market', '2026-09-01', 200, 30000, 'apparels:200', 'ok'),
  (3, 'pricecharting', 'market', '2026-09-01', 300, 300, 'pokemon-z/booster-box', 'ok'),
  (1, 'snkrdunk', 'ask', '2026-09-01', 110, 16500, 'apparels:100', 'ok'),
  (1, 'snkrdunk', 'ask', '2026-09-02', 990, 148500, 'apparels:999', 'ok');
INSERT INTO market_sealed_sale_observation VALUES
  (1, 1, 'snkrdunk', '2026-09-01', 100, 1, 'ok'),
  (2, 1, 'ebay', '2026-09-01', 55, 1, 'ok'),
  (3, 2, 'snkrdunk', '2026-09-01', 200, 1, 'ok'),
  (4, 3, 'ebay', '2026-09-01', 310, 1, 'ok'),
  (5, 3, 'yahoo', '2026-09-01', 290, 1, 'outlier_trimmed');
"""


def test_compose_reads_only_the_frozen_item():
    # 2026-09-23 compose and export read every row of a SKU: 1,585 /box prices came from items no accepted freeze
    # named (S10b off a rejected SNK item, OP-01 EN off the JP box, JU EN off the unlimited Jungle box).
    import sealed_price_compose as compose

    cur = LiteConn(COMPOSE_DB).cursor()
    market = compose.load_market(cur)
    assert market == {(1, "snkrdunk"): [("2026-09-01", 100.0)], (1, "pricecharting"): [("2026-09-01", 50.0)]}, \
        "market read a row off another item, a rejected freeze or no freeze (SNK spelling, PC quoting fold): %r" % (market,)
    asks = compose.load_asks(cur)
    assert asks == {1: ("2026-09-01", 110.0, 16500.0)}, "the latest ask came off another item: %r" % (asks,)
    sales = {sid: sorted(r["id"] for r in rows) for sid, rows in compose.load_sales(cur).items()}
    assert sales == {1: [1, 2], 3: [5]}, \
        "an SNK / eBay sale counted without its SKU's accepted SNK / PC freeze, or a Yahoo sale was dropped: %r" % (sales,)


def test_compose_trims_old_sales_by_their_neighbours():
    # 2026-09-24: the trim judged only the 30d window, and the full daily line reads every ok sale: $90-$100 Unified
    # Minds boxes (median $2,850) stayed on the line, and marks made under the old box counts never came back.
    from datetime import date, datetime

    import sealed_price_compose as compose

    class MarkCursor:
        def __init__(self):
            self.marks = {}

        def execute(self, sql, params=()):
            assert sql.startswith("UPDATE market_sealed_sale_observation SET metric_status="), sql
            self.marks[params[1]] = params[0]

    def sale(i, day, usd, status="ok"):
        return {"id": i, "sold_at": datetime.fromisoformat(day), "unit_price_usd": usd, "metric_status": status}

    sales = [sale(1, "2026-06-10", 100), sale(2, "2026-06-15", 110), sale(3, "2026-06-20", 90), sale(4, "2026-06-25", 105),
             sale(5, "2026-06-18", 5),  # far under its neighbours
             sale(6, "2026-06-22", 100, "outlier_trimmed"),  # marked against a median of the old box counts
             sale(7, "2025-01-01", 1), sale(8, "2025-01-20", 10),  # two sales alone: too few to judge
             sale(9, "2026-09-10", 300), sale(10, "2026-09-12", 310), sale(11, "2026-09-14", 290), sale(12, "2026-09-15", 3000)]
    cur = MarkCursor()
    kept, marked = compose.trim_outliers(cur, 1, sales, date(2026, 9, 24))
    want = {5: "outlier_trimmed", 6: "ok", 12: "outlier_trimmed"}
    assert cur.marks == want and marked == 3, "trim marks: %r (%d)" % (cur.marks, marked)
    assert sorted(s["id"] for s in kept) == [9, 10, 11], "the current price read: %r" % kept
    status = {s["id"]: s["metric_status"] for s in sales}
    assert all(status[i] == w for i, w in want.items()) and status[7] == status[8] == "ok", \
        "the daily line reads these dicts in the same run: %r" % status


def test_price_upsert_keeps_a_quarantine():
    # Every sealed price write shares one ON DUPLICATE head: the same item rewriting a row an operator quarantined
    # keeps it out; another item's write carries its id in, so compose's frozen-item filter judges it afresh.
    import re

    import sealed_runtime as rt

    head = rt.PRICE_UPSERT_HEAD
    assert head.index("metric_status=") < head.index("external_entity_id=VALUES"), \
        "MySQL assigns left to right: metric_status must be judged before external_entity_id is overwritten"
    for name in ("sealed_runtime.py", "sealed_collect.py"):
        src = (Path(__file__).resolve().parents[1] / "pipelines" / name).read_text(encoding="utf-8")
        writes = src.count("INSERT INTO market_sealed_price_observation")
        headed = len(re.findall(r"INSERT INTO market_sealed_price_observation.*?ON DUPLICATE KEY UPDATE\s*\"\"\"\s*\+\s*"
                                r"PRICE_UPSERT_HEAD\s*\+", src, re.S))
        assert writes and headed == writes, f"{name}: {writes} price writes, {headed} open with PRICE_UPSERT_HEAD"

    class UpsertCursor(LiteCursor):
        def execute(self, sql, params=()):
            sql = sql.replace("ON DUPLICATE KEY UPDATE",
                              "ON CONFLICT(sealed_id, source_code, price_kind, observed_date) DO UPDATE SET")
            super().execute(re.sub(r"\bVALUES\((\w+)\)", r"excluded.\1", sql).replace("IF(", "iif("), params)

    conn = LiteConn(COMPOSE_DB)
    cur = UpsertCursor(conn.conn.cursor())
    row = dict(sealed_id=9, source_code="snkrdunk", price_kind="market", observed_date="2026-09-03", native_price=1.0,
               native_currency="JPY", ingest_run_key="t")

    def state():
        cur.execute("SELECT external_entity_id, metric_status, price_usd FROM market_sealed_price_observation WHERE sealed_id=9")
        return tuple(cur.fetchone().values())

    rt.upsert_sealed_price(cur, price_usd=10.0, external_entity_id="apparels:1", **row)
    cur.execute("UPDATE market_sealed_price_observation SET metric_status='quarantined' WHERE sealed_id=9")
    rt.upsert_sealed_price(cur, price_usd=11.0, external_entity_id="apparels:1", **row)
    assert state() == ("apparels:1", "quarantined", 11.0), "the same item's rewrite released a quarantine: %r" % (state(),)
    rt.upsert_sealed_price(cur, price_usd=20.0, external_entity_id="apparels:2", **row)
    assert state() == ("apparels:2", "ok", 20.0), "another item's write kept the old item id: %r" % (state(),)
    rt.upsert_sealed_price(cur, price_usd=21.0, external_entity_id="apparels:2", **row)
    assert state() == ("apparels:2", "ok", 21.0), state()


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
