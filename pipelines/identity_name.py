"""公開卡名同編號嘅唯一組裝點。

點解要開呢個 module：卡名之前有四個權威 —— `catalog_variant.canonical_name`、
`catalog_official_name_acceptance`、`catalog_psa_identity_acceptance`、
`operator_official_name_projection` —— 而 FE 淨係讀第一個
（apps/web/src/lib/live-db-snapshot.ts:489），第一個又每次 bind 都被 GemRate 原字
覆蓋。所以喺另外三個度修幾多次都出唔到街，而且每跑一次 036 就打返轉頭。

2026-08-11 實測嘅損壞：出街 top 100 入面 71 張個名斷咗條尾（`… Special Art Rare
110`，真編號 `110/80`），全 universe 436/1605；另外 top 100 有 29 張連編號欄本身
都冇分母但照標 `complete: true`，全 universe 1169/1605。

呢個 module 全部係純字串運算、冇 DB、冇 IO，所以各個 writer（rebuild_036
stage_bind、new_era_db_tidy、resolve_active_psa_identity、每日新卡 intake）可以行
同一份邏輯而唔會各自飄。
"""

from __future__ import annotations

import re
from typing import Any, Iterable


def normalise_text(value: Any) -> str:
    """收乾空白。GemRate 有真係出過雙空格（v8 `… One Piece Night  010`）。"""

    return re.sub(r"\s+", " ", str(value or "")).strip()


def collector_core(value: Any) -> str:
    """跨詞彙比得埋嘅編號核心。

    GemRate 出裸號（`016`、`236`），catalog 出帶前綴／分母嘅顯示式
    （`OP01-016`、`236/187`）。核心 = 最後一段 dash、掉分母、折前導零。

    折前導零唔係靚化，係命：舊版 `_canonical_full_name`（2026-08-04 被 commit
    f24b2447 整段刪走）用 regex 直接對字面，所以 `079` 對唔上 `79/73`，跌落
    append 分支出咗 `… Secret 079 79/73`。實測 79 行係呢個形狀 —— 個 function
    就係因為咁被人當壞嘢刪咗，而唔係因為補編號呢件事本身錯。
    """

    text = normalise_text(value).replace(" ", "").casefold()
    if not text or text == "unknown":  # catalog placeholder，唔係一個號
        return ""
    text = text.split("/", 1)[0]
    if "-" in text:
        text = text.rsplit("-", 1)[1]
    return str(int(text)) if text.isdigit() else text


def _has_denominator(value: str) -> bool:
    return "/" in value or "-" in value


def collector_is_complete(value: Any) -> bool:
    """同 FE 個 `collectorNumber.complete` 同一條定義（live-db-snapshot.ts：顯示
    式有 `/` 或 `-`）。裸號 `091` 係 False，`OP13-091`、`110/80` 係 True。"""

    return _has_denominator(normalise_text(value))


def collector_prefix(value: Any) -> str:
    """Set 前綴（`OP09-061` → `OP09`），冇就 ''。

    前綴係「邊套卡」嘅證據，唔係格式：同一個 core 061，OP09 同 EB02 係兩張卡。
    """

    text = normalise_text(value).replace(" ", "").upper().split("/", 1)[0]
    return text.rsplit("-", 1)[0] if "-" in text else ""


def restate_collector_number(current: Any, candidate: Any) -> tuple[str, str]:
    """已出街嘅編號可唔可以被 candidate 取代；回 (出街值, 理由)。

    candidate 係 complete_collector_number 由 GemRate capture 砌出嚟嘅號，前綴／
    分母可以係借其他 grader 行。借嘅證據會錯：2026-09-26 實測 v2030 PSA 行印
    `061`，GemRate 將 EB02 Anime 25th Collection 嘅 SGC/CGC 行併咗入同一個 entity，
    借出 `EB02-061`，但出街嘅 `OP09-061` 先啱。所以 restate 只准補全，唔准改寫：

      - candidate 空或者一樣 → same
      - 現值空或 placeholder → filled（用 candidate）
      - core 唔同 → core_conflict（照舊）：restate 唔係換卡
      - 現值完整、candidate 裸號 → keep_complete（照舊）：唔准降級
      - 現值有前綴、candidate 前綴唔同 → prefix_conflict（照舊）：v2030 呢類
      - 其餘（裸號補全、大細階、分母寫法）→ restated（用 candidate）
    """

    now = normalise_text(current)
    new = normalise_text(candidate)
    if not new or new == now:
        return now, "same"
    if not collector_core(now):
        return new, "filled"
    if collector_core(new) != collector_core(now):
        return now, "core_conflict"
    if _has_denominator(now) and not _has_denominator(new):
        return now, "keep_complete"
    if collector_prefix(now) and collector_prefix(new) != collector_prefix(now):
        return now, "prefix_conflict"
    return new, "restated"


def complete_collector_number(
    psa_card_number: Any,
    population_rows: Iterable[Any] | None,
    printed_collector_number: Any = "",
) -> str:
    """一張卡對外嘅完整編號。

    權威次序，由窄到闊：

    1. `catalog_printing_identity.collector_number` 已經帶分母／前綴 → 照用。
       436/1605 屬呢類，佢哋顯示嘢一個字都唔會郁。
    2. 同一份 GemRate payload 入面其他 grader 行嘅 `card_number`。PSA 嗰行印裸號
       （v5 = `110`），但同一頁 CGC 行印足 `110/080`；兩行講緊同一張實體卡，所以
       分母係現成證據，唔使再爬。實測 1169 張冇分母嘅入面 1108 張補得返。
       **必須 numerator core 相等先至採用** —— `8` ↔ `008/032` 過得，
       `110` ↔ `110/091` 就唔會撞去第二張卡。
    3. 兩樣都冇 → 照出裸號，唔准砌。實測 61 張真係邊個 grader 都冇分母；佢哋
       出短號，`complete` 標 false，唔准扮完整。

    一律 `.upper()`：`printing_sha()` 十個欄全部 casefold 先 hash
    （resolve_active_psa_identity.py:56），所以大細階對 hash 係 no-op；而
    `catalog_printing_identity` 有 163 行係細階（`op01-001`），同 PSA 標籤同卡面
    印嘅大階唔一致。
    """

    printed = normalise_text(printed_collector_number)
    if printed and _has_denominator(printed):
        return printed.upper()

    base = normalise_text(psa_card_number)
    if not base:
        return printed.upper()
    if _has_denominator(base):
        return base.upper()

    want = collector_core(base)
    if not want:
        return base.upper()
    for row in population_rows or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("grader") or "").casefold() == "psa":
            continue
        candidate = normalise_text(row.get("card_number"))
        if not _has_denominator(candidate):
            continue
        if collector_core(candidate) != want:
            continue
        return candidate.upper()
    return base.upper()


def complete_collector_tail(psa_description: Any, collector_number: Any) -> str:
    """PSA 全名，但條尾用返完整編號。

    PSA 標籤本身就係斷尾嘅：GemRate 個 PSA 行 description 直接以裸號收尾
    （`… Special Art Rare 110`），而我哋自己手上就有 `110/80`。呢度做嘅唯一一件事
    係將條尾換成完整號，其餘一個 byte 都唔郁 —— 年份、set、卡名、parallel 全部
    照 PSA。owner 2026-08-11 定：英文卡唔硬加 `English`，語言由獨立欄位出。

    三條分支，冇第四條：
      - 已經以完整編號收尾 → 原封不動
      - 最尾一個 token 嘅 core 同完整編號嘅 core 相等 → 換咗佢
      - 除此以外，完整編號又未喺個名度出現過 → 先至 append（實測得
        `… Alternate Art-Gold` + `OPCD-093` 呢一類，個名根本冇數字尾巴）

    編號一個數字都冇就唔係號，三條分支都唔行：v2307 個 PSA 號係 `Old`，照 append
    會出 `… Base OLD`（2026-09-26 實測）。
    """

    title = normalise_text(psa_description)
    collector = normalise_text(collector_number)
    if not title or not collector or not re.search(r"\d", collector):
        return title
    if title.casefold().endswith(collector.casefold()):
        return title

    parts = title.split(" ")
    want = collector_core(collector)
    if want and collector_core(parts[-1]) == want:
        return " ".join(parts[:-1] + [collector])

    # 條尾唔係呢張卡個號。淨係喺個號完全未出現過嗰陣先加，唔係會出雙號。
    if re.search(rf"(?i)(?:^|\s){re.escape(collector)}(?:/|\s|$)", title):
        return title
    return f"{title} {collector}"
