"""把分批寫好嘅卡片故事合併入 data/editorial/top100-stories.json。

四語故事由 agent 分批寫入 temp/stories-out-<N>.json（格式 [{id, stories:{en,zhTW,zhCN,ja}}]），
素材同身分事實喺 temp/story-source-bundle.json。呢個腳本負責把兩者對齊、
補回 entries 需要嘅身分欄位、逐條驗 validate.ts 嘅硬契約，先寫入正式檔。

用法：
    python -X utf8 pipelines/editorial_story_merge.py --dry-run
    python -X utf8 pipelines/editorial_story_merge.py --write
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "data" / "editorial" / "top100-stories.json"
BUNDLE = ROOT / "temp" / "story-source-bundle.json"
BATCH_GLOB = str(ROOT / "temp" / "stories-out-*.json")

LOCALES = ("en", "zhTW", "zhCN", "ja")
MIN_CHARS = 80
# validate.ts:290 嘅禁詞，中咗即刻整份 snapshot 出唔到。
BANNED = re.compile(
    r"trader market view|炒家市場觀察|受到市場關注，重點不只是單張報價",
    re.IGNORECASE,
)


def collector_display(value: Any) -> str:
    """bundle 嘅 collectorNumber 有時係 dict（帶 complete/display/normalized），有時已經係字串。"""
    if isinstance(value, dict):
        return str(value.get("display") or "")
    return str(value or "")


def evidence_kinds(row: dict[str, Any]) -> list[str]:
    kinds = ["asset_identity"]
    # 注意：g10Research 有機會係空字串（rank 87 就係），唔可以用 `is not None` 判。
    if (row.get("g10Research") or "").strip():
        kinds.append("research_summary")
    return kinds


def evidence_sha(row: dict[str, Any]) -> str:
    """故事係基於邊份素材寫，就對嗰份素材做內容定址 —— 唔准填假值。"""
    material = {
        "id": row.get("id"),
        "nameEn": row.get("nameEn"),
        "setEn": row.get("setEn"),
        "collectorNumber": collector_display(row.get("collectorNumber")),
        "tcg": row.get("tcg"),
        "cardLanguage": row.get("cardLanguage"),
        "psa10Pop": row.get("psa10Pop"),
        "pricePsa10Usd": row.get("pricePsa10Usd"),
        "matchedBy": row.get("matchedBy"),
        "g10SetName": row.get("g10SetName"),
        "g10Research": row.get("g10Research") or "",
    }
    blob = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def story_defect(stories: Any) -> str | None:
    """逐條對 validate.ts:280-297 嘅硬契約。回 None 即係過關。"""
    if not isinstance(stories, dict):
        return "stories 唔係 object"
    if set(stories) != set(LOCALES):
        return f"locale 唔齊：{sorted(stories)}"
    for loc in LOCALES:
        value = stories[loc]
        if not isinstance(value, str) or not value.strip():
            return f"{loc} 空白"
        if len(value.strip()) < MIN_CHARS:
            return f"{loc} 得 {len(value.strip())} 字元，短過 {MIN_CHARS}"
    if len({stories[loc].strip() for loc in LOCALES}) != len(LOCALES):
        return "四語有重複"
    if BANNED.search(" ".join(stories[loc] for loc in LOCALES)):
        return "命中禁詞"
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--write", action="store_true", help="真係寫入 top100-stories.json")
    group.add_argument("--dry-run", action="store_true", help="只報告唔寫檔")
    args = parser.parse_args()

    bundle = {row["id"]: row for row in json.loads(BUNDLE.read_text(encoding="utf-8"))}

    incoming: dict[str, dict[str, str]] = {}
    collisions: list[str] = []
    for path in sorted(glob.glob(BATCH_GLOB)):
        for row in json.loads(Path(path).read_text(encoding="utf-8")):
            card_id = row["id"]
            if card_id in incoming:
                collisions.append(card_id)
            incoming[card_id] = row["stories"]
    print(f"批次讀入 {len(incoming)} 張" + (f"｜⚠ 重複 id {collisions}" if collisions else ""))

    document = json.loads(TARGET.read_text(encoding="utf-8"))
    entries: list[dict[str, Any]] = list(document.get("entries") or [])
    by_id = {entry["id"]: entry for entry in entries}

    rejected: list[tuple[str, str]] = []
    orphaned: list[str] = []
    updated = 0
    added = 0

    for card_id, stories in incoming.items():
        defect = story_defect(stories)
        if defect:
            rejected.append((card_id, defect))
            continue
        row = bundle.get(card_id)
        if row is None:
            orphaned.append(card_id)
            continue

        entry = by_id.get(card_id)
        if entry is None:
            entry = {"id": card_id}
            entries.append(entry)
            by_id[card_id] = entry
            added += 1
        else:
            updated += 1

        entry.update(
            {
                "rankAtReview": int(row["rank"]),
                "tcg": row.get("tcg"),
                "cardLanguage": row.get("cardLanguage"),
                "collectorNumber": collector_display(row.get("collectorNumber")),
                "status": "ready",
                "evidenceSha256": evidence_sha(row),
                "evidence": evidence_kinds(row),
                "reviewReason": None,
                "stories": {loc: stories[loc].strip() for loc in LOCALES},
            }
        )

    entries.sort(key=lambda e: (e.get("rankAtReview") or 10_000, e["id"]))
    ready = sum(1 for e in entries if e.get("status") == "ready")
    document["entries"] = entries
    document["readyCount"] = ready
    document["reviewRequiredCount"] = len(entries) - ready

    print(f"新增 {added}｜覆寫 {updated}｜總 entries {len(entries)}｜ready {ready}")
    if rejected:
        print(f"❌ 契約唔過 {len(rejected)} 張，冇寫入：")
        for card_id, defect in rejected:
            print(f"   {card_id} — {defect}")
    if orphaned:
        print(f"⚠ bundle 揾唔到素材 {len(orphaned)} 張，冇寫入：{orphaned}")

    if args.dry_run:
        print("dry-run：冇寫檔")
        return 1 if rejected or orphaned else 0

    TARGET.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"✅ 已寫入 {TARGET.relative_to(ROOT)}")
    return 1 if rejected or orphaned else 0


if __name__ == "__main__":
    raise SystemExit(main())
