#!/usr/bin/env python3
"""卡圖歸一：將散落六個目錄嘅 QC 合格卡圖收斂返去單一位置，並出一份人睇得明嘅索引。

點解要有呢個檔
--------------
`manifests/image-qc.json` 入面 611 條記錄**全部** `publicAllowed: true`，即係全部
過咗 QC。但係 `canonical_public_snapshot.load_public_images()` 認唔認一張圖，
最後一關係「`data/public/market-assets/<sha>.webp` 呢個檔實際存唔存在」。

實況係只有 251 條 v4 記錄嘅檔喺嗰度；另外 360 條 v3 記錄嘅檔散喺：

    data/public/publish-staging/remote-assets.verify/            418 個
    data/public/publish-staging/generations/daily_*/assets/      360 個
    data/runtime/private-quarantine/legacy-public-assets/*/     1223 個
    docs/mockups/market-assets/                                  360 個

所以出街 snapshot 少咗卡，唔係因為冇圖、唔係因為圖唔合格，純粹係**擺錯位**。
呢個腳本就係執屍：搵齊、驗身份、統一規格、歸位、出索引。

三個 mode
---------
    （預設）           唯讀盤點，出 manifests/image-store-index.json + docs/IMAGE_STORE_INDEX.md
    --recover         真正歸位：normalize 到 429×600 → 寫入 market-assets → append v4 QC 記錄
    --require-catalog 冇 DB 就直接失敗（排程用，唔准靜靜地降級成半套）

點解一張 QC 合格圖會歸唔到位
--------------------------
QC manifest 嘅 `publicId` 係 2026-07-22 嗰代身份，之後 export 重新綁過
（relinked_printing_key=176 / new_from_catalog=59），所以 419 個 publicId 入面
只有一部分仲喺今日嘅 `catalog_variant`。搵唔返身份嘅圖唔准亂認 —— 會標
`orphan_identity` 放入索引等人手裁決，唔准硬塞落 snapshot。

Exit codes: 0 = 完成, 2 = QC manifest 讀唔到, 3 = --require-catalog 但連唔到 DB
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

QC_PATH = ROOT / "manifests" / "image-qc.json"
ASSETS = ROOT / "data" / "public" / "market-assets"
INDEX_JSON = ROOT / "manifests" / "image-store-index.json"
INDEX_MD = ROOT / "docs" / "IMAGE_STORE_INDEX.md"

# 搜檔優先次序：越前越可信。market-assets 排第一係因為佢已經係統一規格，
# 揾到就唔使再 normalize。
SEARCH_STORES: tuple[tuple[str, Path], ...] = (
    ("market-assets", ASSETS),
    ("publish-staging/remote-assets.verify", ROOT / "data/public/publish-staging/remote-assets.verify"),
    ("publish-staging/generations", ROOT / "data/public/publish-staging/generations"),
    ("private-quarantine/legacy-public-assets", ROOT / "data/runtime/private-quarantine/legacy-public-assets"),
    ("docs/mockups/market-assets", ROOT / "docs/mockups/market-assets"),
)

# store_native_image() 會將圖統一到呢個規格；細過呢個尺寸嘅來源放大會糊，唔收。
CANVAS_W, CANVAS_H = 429, 600
MIN_SOURCE_EDGE = (360, 500)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def qc_records(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    """QC 合格嘅 raw_front 記錄。呢個係 load_public_images() 頭四關嘅同一份契約。"""

    out: list[dict[str, Any]] = []
    for record in document.get("records", []):
        if not isinstance(record, Mapping) or not record.get("publicAllowed"):
            continue
        if str(record.get("imageKind") or "") != "raw_front":
            continue
        sha = str(record.get("contentSha256") or "")
        if len(sha) != 64:
            continue
        out.append(dict(record))
    return out


def index_stores(stores: Iterable[tuple[str, Path]]) -> dict[str, tuple[str, Path]]:
    """sha256 → (store 名, 檔案路徑)。同一個 sha 喺多處出現就守返優先次序，唔覆蓋。"""

    found: dict[str, tuple[str, Path]] = {}
    for label, base in stores:
        if not base.is_dir():
            continue
        for path in base.rglob("*.webp"):
            stem = path.stem
            # 只收 master（<sha>.webp），唔收 <sha>_200 / <sha>_600 衍生檔
            if len(stem) != 64:
                continue
            found.setdefault(stem, (label, path))
    return found


def load_catalog(args: argparse.Namespace) -> dict[str, dict[str, Any]] | None:
    """今日嘅 catalog_variant，key 係 opaque_id。連唔到 DB 回 None（唯讀盤點照做得）。"""

    try:
        from db_runtime import connection_from_args
    except Exception:
        return None
    try:
        connection = connection_from_args(args)
    except Exception as exc:
        print(f"catalog unavailable: {exc}", file=sys.stderr)
        return None
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT opaque_id, tcg_code, canonical_name,
                       set_name, collector_number, identity_status
                FROM catalog_variant
                """
            )
            return {str(row["opaque_id"]): dict(row) for row in cursor.fetchall()}
    finally:
        connection.close()


def classify(
    records: list[dict[str, Any]],
    located: dict[str, tuple[str, Path]],
    catalog: dict[str, dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """逐條 QC 記錄判狀態。呢度唔改任何嘢，純粹講清楚每張圖而家係咩處境。"""

    # 同一張卡出過多代 QC 記錄。舊代記錄嘅檔唔喺 market-assets 係正常，因為
    # 新代已經頂上——唔標返出嚟就會永遠讀成「仲有 29 張未歸位」，睇報告嘅人白做一次。
    shipping_ids = {
        str(r.get("publicId") or "")
        for r in records
        if (located.get(str(r["contentSha256"])) or ("", None))[0] == "market-assets"
    }

    rows: list[dict[str, Any]] = []
    for record in records:
        sha = str(record["contentSha256"])
        public_id = str(record.get("publicId") or "")
        hit = located.get(sha)
        identity = (catalog or {}).get(public_id)

        if hit and hit[0] == "market-assets":
            status = "shipping"
        elif public_id in shipping_ids:
            status = "superseded"
        elif not hit:
            status = "missing_file"
        elif catalog is None:
            status = "catalog_unknown"
        elif identity is None:
            status = "orphan_identity"
        else:
            status = "recoverable"

        rows.append(
            {
                "publicId": public_id,
                "contentSha256": sha,
                "qcVersion": str(record.get("qcVersion") or ""),
                "width": record.get("width"),
                "height": record.get("height"),
                "status": status,
                "store": hit[0] if hit else None,
                "path": str(hit[1].relative_to(ROOT)).replace("\\", "/") if hit else None,
                "sourceContentSha256": str(
                    (record.get("resolverEvidence") or {}).get("sourceContentSha256") or ""
                )
                or None,
                "method": str((record.get("resolverEvidence") or {}).get("method") or "") or None,
                "tcg": (identity or {}).get("tcg_code"),
                "collectorNumber": (identity or {}).get("collector_number"),
                "name": (identity or {}).get("canonical_name"),
                "setName": (identity or {}).get("set_name"),
                "identityStatus": (identity or {}).get("identity_status"),
            }
        )
    return rows


def recover(rows: list[dict[str, Any]], document: dict[str, Any], *, write: bool) -> dict[str, Any]:
    """將 recoverable 嘅圖統一規格後寫入 market-assets，並 append raw-front-v4 記錄。

    每張圖走 `store_native_image()`，即係同每日自愈完全一樣嘅路 —— 唔另開規格。
    新記錄嘅 `sourceContentSha256` 一律沿用舊記錄嘅上游 sha，**唔准攞新 contentSha256
    頂替**（頂替等於偽造來源鏈）。
    """

    from PIL import Image

    import native_image_resolver as nir

    outcome: dict[str, Any] = {"written": [], "skipped": [], "failed": []}
    existing_ids = {str(r.get("publicId") or "") for r in document.get("records", [])
                    if str(r.get("qcVersion") or "") == "raw-front-v4"}

    # 同一張卡可以有多條舊 QC 記錄（唔同世代重抓）。一個 publicId 只准出一條 v4，
    # 揀來源像素最多嗰張 —— 唔去重就會寫兩條記錄撞同一個 publicId，
    # load_public_images() 靜靜地取先到嗰條，變成隨機邊張贏。
    best: dict[str, tuple[int, dict[str, Any]]] = {}
    for row in rows:
        if row["status"] != "recoverable":
            continue
        area = int(row.get("width") or 0) * int(row.get("height") or 0)
        current = best.get(row["publicId"])
        if current is None or area > current[0]:
            if current is not None:
                outcome["skipped"].append({**current[1], "reason": "superseded_by_larger_source"})
            best[row["publicId"]] = (area, row)
        else:
            outcome["skipped"].append({**row, "reason": "superseded_by_larger_source"})

    for public_id, (_, row) in sorted(best.items()):
        if public_id in existing_ids:
            outcome["skipped"].append({**row, "reason": "already_v4"})
            continue
        source = ROOT / row["path"]
        raw = source.read_bytes()
        try:
            with Image.open(io.BytesIO(raw)) as opened:
                size = opened.size
        except Exception as exc:
            outcome["failed"].append({**row, "reason": f"unreadable: {exc}"})
            continue
        if size[0] < MIN_SOURCE_EDGE[0] or size[1] < MIN_SOURCE_EDGE[1]:
            # 放大細圖出街等於交次貨，寧願缺一張卡。
            outcome["skipped"].append({**row, "reason": f"source_too_small_{size[0]}x{size[1]}"})
            continue
        if not write:
            outcome["written"].append({**row, "sourceSize": f"{size[0]}x{size[1]}", "dryRun": True})
            continue

        block = nir.store_native_image(raw, row["name"] or "")
        document["records"].append(
            {
                "cardNumberMatch": True,
                "contentSha256": block["sha256"],
                "height": block["height"],
                "imageKind": "raw_front",
                "languageMatch": True,
                "nativeRgba": nir.is_native_rounded(raw),
                "publicAllowed": True,
                "publicId": public_id,
                "qcAt": block["qcAt"],
                "qcVersion": "raw-front-v4",
                "resolverEvidence": {
                    "collectorMatch": True,
                    "languageMetadataMatch": True,
                    "method": "store_consolidation",
                    "sourceContentSha256": row["sourceContentSha256"] or row["contentSha256"],
                    "tcgMetadataMatch": True,
                },
                "semanticMatchStatus": "metadata_exact_unreviewed",
                "stdCanvas": "std-429x600",
                "tcgMatch": True,
                "width": block["width"],
            }
        )
        outcome["written"].append(
            {**row, "newContentSha256": block["sha256"], "sourceSize": f"{size[0]}x{size[1]}"}
        )
    return outcome


def write_index(rows: list[dict[str, Any]], summary: Mapping[str, Any]) -> None:
    """出兩份索引：JSON 俾腳本讀，Markdown 俾人睇。"""

    INDEX_JSON.write_text(
        json.dumps(
            {"generatedAt": utc_now(), "summary": dict(summary), "records": rows},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    by_status: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_status[row["status"]].append(row)

    lines = [
        "# 卡圖倉索引",
        "",
        f"生成時間：{utc_now()}　·　產生者：[pipelines/image_store_consolidate.py](../pipelines/image_store_consolidate.py)",
        "",
        "呢份係 `manifests/image-qc.json` 每一條 QC 合格記錄嘅落地實況：",
        "張圖而家實際喺邊個目錄、對返邊張卡、出唔出到街。",
        "**唔好手改呢份文** —— 重新跑腳本就會覆寫。",
        "",
        "## 狀態定義",
        "",
        "| 狀態 | 意思 | 出唔出到街 |",
        "|---|---|---|",
        "| `shipping` | 檔已經喺 `data/public/market-assets/` | ✅ 出得 |",
        "| `recoverable` | 檔喺其他目錄，身份對得返今日 catalog | 🔧 `--recover` 就出到 |",
        "| `orphan_identity` | 檔搵到，但 `publicId` 已經唔喺今日 catalog（07-22 舊世代） | ⛔ 要人手裁決 |",
        "| `missing_file` | 六個目錄都搵唔到呢個 sha | ⛔ 要重下載 |",
        "| `catalog_unknown` | 連唔到 DB，身份未驗 | ❔ 重跑（要 DB） |",
        "",
        "## 總計",
        "",
        "| 狀態 | 條數 |",
        "|---|---:|",
    ]
    for status, items in sorted(by_status.items(), key=lambda kv: -len(kv[1])):
        lines.append(f"| `{status}` | {len(items)} |")
    lines += ["", "## 明細", ""]

    for status in ("recoverable", "orphan_identity", "missing_file", "catalog_unknown", "shipping"):
        items = by_status.get(status)
        if not items:
            continue
        lines += [
            f"### `{status}`（{len(items)}）",
            "",
            "| sha256 | 卡 | TCG | 語言 | 卡號 | 尺寸 | 所在目錄 |",
            "|---|---|---|---|---|---|---|",
        ]
        for row in sorted(items, key=lambda r: (r.get("tcg") or "", r.get("collectorNumber") or "", r["contentSha256"])):
            lines.append(
                "| `{sha}` | {name} | {tcg} | {lang} | {num} | {w}×{h} | {store} |".format(
                    sha=row["contentSha256"][:12],
                    name=row.get("name") or "—",
                    tcg=row.get("tcg") or "—",
                    lang=row.get("language") or "—",
                    num=row.get("collectorNumber") or "—",
                    w=row.get("width") or "?",
                    h=row.get("height") or "?",
                    store=row.get("store") or "—",
                )
            )
        lines.append("")

    INDEX_MD.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="卡圖歸一：收斂 QC 合格卡圖到單一位置並出索引")
    parser.add_argument("--recover", action="store_true", help="真正歸位（唔加淨係盤點）")
    parser.add_argument("--require-catalog", action="store_true", help="連唔到 DB 就失敗，唔准降級")
    try:
        from db_runtime import add_connection_args

        add_connection_args(parser)
    except Exception:
        pass
    args = parser.parse_args()
    if args.recover:
        raise SystemExit(
            "image_store_consolidate --recover public writer permanently disabled: use the gated image review pipeline"
        )

    if not QC_PATH.is_file():
        print(json.dumps({"status": "missing_qc", "path": str(QC_PATH)}), file=sys.stderr)
        return 2
    document = json.loads(QC_PATH.read_text(encoding="utf-8"))

    records = qc_records(document)
    located = index_stores(SEARCH_STORES)
    catalog = load_catalog(args)
    if catalog is None and args.require_catalog:
        print(json.dumps({"status": "catalog_required"}), file=sys.stderr)
        return 3

    rows = classify(records, located, catalog)
    counts = Counter(row["status"] for row in rows)

    result: dict[str, Any] = {
        "status": "ok",
        "qcRecords": len(records),
        "distinctPublicIds": len({r["publicId"] for r in rows}),
        "filesIndexed": len(located),
        "catalogRows": len(catalog) if catalog is not None else None,
        "byStatus": dict(sorted(counts.items())),
    }

    if args.recover:
        outcome = recover(rows, document, write=True)
        QC_PATH.write_text(
            json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        result["recovered"] = len(outcome["written"])
        result["recoverSkipped"] = [
            {"publicId": r["publicId"], "reason": r["reason"]} for r in outcome["skipped"]
        ]
        result["recoverFailed"] = [
            {"publicId": r["publicId"], "reason": r["reason"]} for r in outcome["failed"]
        ]
        # 歸位之後實況變咗，重新盤點先出索引，唔好出一份已經過時嘅圖
        document = json.loads(QC_PATH.read_text(encoding="utf-8"))
        rows = classify(qc_records(document), index_stores(SEARCH_STORES), catalog)
        result["byStatusAfter"] = dict(sorted(Counter(r["status"] for r in rows).items()))

    write_index(rows, result)
    result["index"] = {
        "json": str(INDEX_JSON.relative_to(ROOT)).replace("\\", "/"),
        "markdown": str(INDEX_MD.relative_to(ROOT)).replace("\\", "/"),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
