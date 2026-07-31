# 前端收尾缺口表 + 30 日流動性閘（2026-07-29）

> asOf **2026-07-29** · 規則：**30 日 `market_sale_observation` 成交筆數 = 0 → 低流動性 → 禁止入 Top100 市值榜**
> 成交日欄：`created_at` · 窗 `2026-06-29` → `2026-07-29`

## 1. 池 vs 流動性

| 指標 | 張數 |
|---|---:|
| 合資格 watchlist | 940 |
| 有市值（有價×POP） | 916 |
| **有市值且 30 日有成交**（可進市值榜） | **98** |
| 有市值但 **低流動性**（30 日 0 成交） | **818** |
| 無價 | 24 |

## 2. Top 100 市值 — 舊規則 vs 新規則

| | 張數 |
|---|---:|
| 舊：純市值 Top100 | 100 |
| 其中 **會被新規則踢走**（30 日 0 成交） | **39** |
| 新：市值排序且非低流動性 Top100 | **98** |

### 舊 Top100 內會被剔除（低流動性）樣本

| 舊 rank | 板 | 名 | 市值(約) | 30日成交 | 最後成交日 |
|---:|---|---|---:|---:|---|
| 3 | pokemon | Mew ex | 60866758 | 0 | — |
| 5 | pokemon | Bulbasaur | 47381123 | 0 | — |
| 18 | pokemon | Mew V | 20303437 | 0 | — |
| 19 | one-piece | Monkey D. Luffy | 19344000 | 0 | — |
| 23 | pokemon | Eevee | 16114171 | 0 | — |
| 26 | pokemon | Charizard ex | 14248500 | 0 | — |
| 28 | pokemon | Pikachu ex | 13913841 | 0 | — |
| 31 | one-piece | Monkey D. Luffy | 13026000 | 0 | — |
| 32 | pokemon | Team Rocket's Houndoom | 12154710 | 0 | — |
| 34 | pokemon | Squirtle | 11239276 | 0 | — |
| 38 | pokemon | Charmeleon | 9975000 | 0 | — |
| 31 | one-piece | Monkey D. Luffy | 9951500 | 0 | — |
| 43 | one-piece | Monkey D. Luffy (1st Anniversary) | 9809946 | 0 | — |
| 52 | pokemon | Bulbasaur | 8358588 | 0 | — |
| 56 | pokemon | Leafeon VMAX (Alternate Art Secret) | 7598320 | 0 | — |
| 57 | pokemon | Snorlax | 7435900 | 0 | — |
| 61 | pokemon | Glaceon VMAX (Alternate Art Secret) | 6866890 | 0 | — |
| 62 | pokemon | Wartortle | 6863247 | 0 | — |
| 63 | one-piece | Shanks | 6851200 | 0 | — |
| 65 | pokemon | Origin Forme Palkia VSTAR (Secret) | 6294357 | 0 | — |
| 66 | pokemon | Ivysaur | 6291750 | 0 | — |
| 67 | pokemon | Tyranitar V (Alternate Full Art) | 6229731 | 0 | — |
| 68 | one-piece | Sanji | 6135000 | 0 | — |
| 70 | pokemon | Charizard VMAX (Secret) | 6024902 | 0 | — |
| 72 | pokemon | Origin Forme Dialga VSTAR (Secret) | 6007239 | 0 | — |
| 74 | pokemon | Voltorb | 5909971 | 0 | — |
| 75 | one-piece | Nami (Errata) | 5883000 | 0 | — |
| 80 | pokemon | Geodude | 5412517 | 0 | — |
| 81 | pokemon | Psyduck | 5400450 | 0 | — |
| 82 | pokemon | Blaziken VMAX (Alternate Art Secret) | 5381964 | 0 | — |

… 共 39 張，見 JSON。

## 3. 要推前端嘅 class（新規則）— 仲爭咩

| FE 集合 | 張數 | 價+圖+QC+史+故事 全齊 | 欠圖/QC | 欠日史 | 欠小故事i18n |
|---|---:|---:|---:|---:|---:|
| Top100 市值（新） | 98 | **31** | 19 | 0 | 48 |
| FE≈300（top100+watch 帶） | 98 | **31** | 19 | 0 | 48 |
| Pokémon Top100 | 81 | **18** | 19 | 0 | 44 |
| One Piece Top100 | 17 | **13** | 0 | 0 | 4 |

## 4. 新 Top100 逐項狀態

- **READY（可推）**：31
- **NEED_FILL（要補）**：67

| rank | 板 | 名 | 30d成交 | 圖 | QC | 史日 | 故事 | 缺口 |
|---:|---|---|---:|:---:|:---:|---:|:---:|---|
| 1 | pokemon | Charizard | 101 | Y | Y | 14 | N | no_story_i18n |
| 2 | pokemon | Umbreon VMAX (Alternate Art  | 81 | Y | Y | 53 | N | no_story_i18n |
| 3 | pokemon | Umbreon ex | 197 | Y | N | 595 | Y | no_qc_public |
| 4 | pokemon | Rayquaza VMAX (Alternate Art | 79 | Y | Y | 57 | Y | — |
| 5 | pokemon | Charizard ex | 60 | Y | Y | 84 | N | no_story_i18n |
| 6 | pokemon | Gengar VMAX (Alternate Art S | 59 | Y | Y | 68 | N | no_story_i18n |
| 7 | pokemon | Lugia V (Alternate Full Art) | 66 | Y | Y | 60 | N | no_story_i18n |
| 8 | pokemon | Squirtle | 280 | Y | N | 51 | Y | no_qc_public |
| 9 | one-piece | Monkey D. Luffy (1st Anniver | 34 | Y | Y | 21 | Y | — |
| 10 | pokemon | Giratina V (Alternate Full A | 61 | Y | Y | 92 | N | no_story_i18n |
| 11 | pokemon | Umbreon VMAX | 329 | Y | Y | 757 | Y | — |
| 12 | pokemon | Charizard ex | 449 | Y | N | 870 | Y | no_qc_public |
| 13 | one-piece | Monkey D. Luffy (Super) | 44 | Y | Y | 52 | Y | — |
| 14 | pokemon | Magikarp | 59 | Y | Y | 72 | N | no_story_i18n |
| 15 | pokemon | Team Rocket's Mewtwo ex | 74 | Y | Y | 30 | N | no_story_i18n |
| 16 | pokemon | Giratina VSTAR (Secret) | 61 | Y | Y | 54 | Y | — |
| 17 | pokemon | Mewtwo VSTAR | 62 | Y | Y | 60 | Y | — |
| 18 | pokemon | Charmander | 387 | Y | Y | 402 | Y | — |
| 19 | pokemon | ______'s Pikachu | 110 | Y | Y | 20 | Y | — |
| 20 | pokemon | Charmander | 221 | Y | N | 56 | Y | no_qc_public |
| 21 | pokemon | Rayquaza V (Alternate Full A | 59 | Y | Y | 71 | N | no_story_i18n |
| 22 | pokemon | Charizard V (Alternate Full  | 59 | Y | Y | 58 | N | no_story_i18n |
| 23 | pokemon | Dragonite V (Alternate Full  | 52 | Y | Y | 53 | N | no_story_i18n |
| 24 | pokemon | Mew ex | 455 | Y | Y | 741 | Y | — |
| 25 | pokemon | Blastoise ex | 55 | Y | Y | 88 | N | no_story_i18n |
| 26 | pokemon | Umbreon Star | 64 | Y | Y | 55 | Y | — |
| 27 | pokemon | Charizard ex | 59 | Y | Y | 74 | N | no_story_i18n |
| 28 | pokemon | Pikachu ex | 64 | Y | Y | 109 | N | no_story_i18n |
| 29 | pokemon | Sylveon ex | 378 | Y | N | 584 | Y | no_qc_public |
| 30 | pokemon | Espeon VMAX (Alternate Art S | 60 | Y | Y | 96 | N | no_story_i18n |
| 31 | pokemon | Greninja ex | 54 | Y | Y | 68 | N | no_story_i18n |
| 32 | pokemon | Zekrom ex | 59 | Y | Y | 68 | N | no_story_i18n |
| 33 | pokemon | Pikachu | 320 | Y | Y | 823 | Y | — |
| 34 | pokemon | Charizard VMAX | 71 | Y | Y | 78 | Y | — |
| 35 | pokemon | Pikachu (Secret) | 53 | Y | Y | 33 | N | no_story_i18n |
| 36 | pokemon | Arceus VSTAR (Secret) | 57 | Y | Y | 68 | Y | — |
| 37 | pokemon | Rayquaza VMAX | 61 | Y | Y | 62 | Y | — |
| 38 | pokemon | Charizard V (Secret) | 70 | Y | Y | 60 | N | no_story_i18n |
| 39 | pokemon | Umbreon V (Alternate Full Ar | 58 | Y | Y | 79 | N | no_story_i18n |
| 40 | pokemon | Reshiram ex | 65 | Y | Y | 102 | N | no_story_i18n |
| 41 | one-piece | Boa Hancock | 34 | Y | Y | 14 | Y | — |
| 42 | pokemon | Venusaur ex | 61 | Y | Y | 70 | N | no_story_i18n |
| 43 | one-piece | Roronoa Zoro | 71 | Y | Y | 37 | Y | — |
| 44 | pokemon | Gengar | 217 | Y | Y | 536 | Y | — |
| 45 | pokemon | Zapdos ex | 57 | Y | Y | 61 | N | no_story_i18n |
| 46 | pokemon | Mega Lucario ex | 52 | Y | Y | 46 | N | no_story_i18n |
| 47 | one-piece | Monkey D. Luffy | 47 | Y | Y | 66 | Y | — |
| 48 | pokemon | Sylveon ex | 56 | Y | Y | 50 | N | no_story_i18n |
| 49 | pokemon | Cynthia's Garchomp ex | 63 | Y | Y | 52 | N | no_story_i18n |
| 50 | pokemon | Espeon ex | 318 | Y | N | 544 | Y | no_qc_public |
| 51 | pokemon | Venusaur ex | 318 | Y | N | 535 | Y | no_qc_public |
| 52 | pokemon | Pikachu | 58 | Y | Y | 81 | N | no_story_i18n |
| 53 | pokemon | Pikachu | 410 | Y | Y | 712 | Y | — |
| 54 | pokemon | Mega Gardevoir ex | 65 | Y | Y | 81 | N | no_story_i18n |
| 55 | one-piece | Boa Hancock | 44 | Y | Y | 61 | Y | — |
| 56 | pokemon | Blastoise ex | 368 | Y | N | 541 | Y | no_qc_public |
| 57 | pokemon | Leafeon ex | 327 | Y | N | 544 | Y | no_qc_public |
| 58 | pokemon | Glaceon ex | 326 | Y | N | 521 | Y | no_qc_public |
| 59 | pokemon | Latias ex | 55 | Y | Y | 77 | N | no_story_i18n |
| 60 | pokemon | Vaporeon ex | 315 | Y | N | 522 | Y | no_qc_public |
| 61 | pokemon | Mega Charizard X ex | 59 | Y | Y | 78 | N | no_story_i18n |
| 62 | pokemon | Zekrom ex | 60 | Y | Y | 70 | N | no_story_i18n |
| 63 | pokemon | Alakazam ex | 57 | Y | Y | 62 | N | no_story_i18n |
| 64 | pokemon | Ethan's Ho-Oh ex | 64 | Y | Y | 55 | N | no_story_i18n |
| 65 | pokemon | Victini | 45 | Y | Y | 79 | N | no_story_i18n |
| 66 | pokemon | Blastoise | 38 | Y | Y | 57 | N | no_story_i18n |
| 67 | pokemon | Flareon ex | 313 | Y | N | 521 | Y | no_qc_public |
| 68 | pokemon | Espeon ex | 52 | Y | Y | 100 | N | no_story_i18n |
| 69 | pokemon | Eevee ex | 348 | Y | N | 542 | Y | no_qc_public |
| 70 | pokemon | Jolteon ex | 310 | Y | N | 532 | Y | no_qc_public |
| 71 | pokemon | Mewtwo | 374 | Y | N | 505 | Y | no_qc_public |
| 72 | pokemon | Eevee ex | 271 | Y | N | 505 | Y | no_qc_public |
| 73 | pokemon | Umbreon | 333 | N | N | 519 | Y | no_image,no_qc_public |
| 74 | pokemon | Mega Charizard X ex | 63 | Y | Y | 30 | N | no_story_i18n |
| 75 | pokemon | Zapdos ex | 314 | Y | N | 559 | Y | no_qc_public |
| 76 | pokemon | Venusaur | 15 | Y | Y | 61 | N | no_story_i18n |
| 77 | pokemon | Sylveon VMAX (Alternate Art  | 59 | Y | Y | 584 | N | no_story_i18n |
| 78 | pokemon | Mega Venusaur ex | 59 | Y | Y | 63 | N | no_story_i18n |
| 79 | one-piece | Dracule Mihawk | 42 | Y | Y | 32 | N | no_story_i18n |
| 80 | pokemon | Pikachu ex | 361 | Y | Y | 454 | Y | — |
| 81 | pokemon | Mewtwo | 240 | Y | Y | 446 | Y | — |
| 82 | pokemon | Mew ex | 126 | Y | Y | 73 | Y | — |
| 83 | pokemon | Roaring Moon ex | 354 | Y | N | 496 | Y | no_qc_public |
| 84 | pokemon | Charizard ex | 297 | Y | Y | 392 | Y | — |
| 85 | pokemon | Charmander | 57 | Y | Y | 388 | N | no_story_i18n |
| 86 | one-piece | Trafalgar Law | 68 | Y | Y | 191 | Y | — |
| 87 | pokemon | Squirtle | 59 | Y | Y | 342 | N | no_story_i18n |
| 88 | pokemon | Charmeleon | 59 | Y | Y | 318 | N | no_story_i18n |
| 96 | one-piece | Monkey D. Luffy | 51 | Y | Y | 68 | N | no_story_i18n |
| 90 | pokemon | Mega Charizard X ex | 67 | Y | Y | 6 | N | no_story_i18n |
| 94 | one-piece | Boa Hancock | 39 | Y | Y | 156 | Y | — |
| 92 | one-piece | Roronoa Zoro | 47 | Y | Y | 63 | Y | — |
| 93 | one-piece | Monkey D. Luffy | 27 | Y | Y | 52 | Y | — |
| 94 | one-piece | Boa Hancock | 39 | Y | Y | 156 | Y | — |
| 95 | one-piece | Monkey D. Luffy | 51 | Y | Y | 68 | N | no_story_i18n |
| 96 | one-piece | Monkey D. Luffy | 51 | Y | Y | 68 | N | no_story_i18n |
| 97 | one-piece | Shanks | 60 | Y | Y | 70 | Y | — |
| 98 | one-piece | Portgas D. Ace | 35 | Y | Y | 28 | Y | — |

## 5. 解讀（務實）

1. **低流動性閘已按 DB 成交量度**；若成交表本身覆蓋薄，大量卡會被標低流動性——符合「30 日無人買唔准上 Top100」。
2. **補數優先序**：先保證新 Top100 有價（已滿足）→ 圖+QC → 日史 → 小故事。
3. **producer 要改**：`canonical_public_snapshot` / ranking 必須套用 `sales_30d>0`（或等價）先入 top100。

JSON 詳情：`temp/fe_liquidity_gap_report.json`
