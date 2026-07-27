"""用 TCGplayer 搜尋 API 幫缺圖卡搵 productId，再砌 CDN 圖片候選。

點解要有呢個檔：缺圖嗰批本機 bytes 幾乎全部係 PSA/BGS 評級殼相（卡封喺膠殼、
連標籤），同用戶已經否決嘅 EB02-010 舊圖係一模一樣嘅毛病，所以本機路線行唔通，
要出去搵乾淨卡面。EB02-010 換圖用嘅 `tcgplayer-cdn.tcgplayer.com/product/<id>_in_1000x1000.jpg`
係已經驗證過嘅來源，唯一問題係 `<id>` 要點搵——呢個檔就係答呢條。

搜尋端點（用 EB02-010 反向驗證過：搜 `EB02-010` 攞返 641620，即係已知正確答案）：
    POST https://mp-search-api.tcgplayer.com/v1/search/request?q=<卡號>&isList=false

**呢個檔唔會自己決定用邊張圖。** 它只負責列候選＋落盤，揀邊個 productId 要人
眼對過美術圖先算（同一個卡號有 base / manga alt / parallel / gold foil 幾個印次，
揀錯印次就係入次貨）。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "pipelines"))

from curl_cffi import requests  # noqa: E402

SEARCH_URL = "https://mp-search-api.tcgplayer.com/v1/search/request"
CDN = "https://tcgplayer-cdn.tcgplayer.com/product/{pid}_in_1000x1000.jpg"


def search(query: str, size: int = 24) -> list[dict[str, object]]:
    body = {
        "algorithm": "sales_dismax",
        "from": 0,
        "size": size,
        "filters": {"term": {}, "range": {}, "match": {}},
        "listingSearch": {
            "context": {"cart": {}},
            "filters": {"term": {"sellerStatus": "Live", "channelId": 0},
                        "range": {"quantity": {"gte": 1}},
                        "exclude": {"channelExclusion": 0}},
        },
        "context": {"cart": {}, "shippingCountry": "US", "userProfile": {}},
        "settings": {"useFuzzySearch": True, "didYouMean": {}},
        "sort": {},
    }
    response = requests.post(
        f"{SEARCH_URL}?q={query}&isList=false",
        json=body,
        impersonate="chrome",
        timeout=30,
        headers={"origin": "https://www.tcgplayer.com",
                 "referer": "https://www.tcgplayer.com/"},
    )
    response.raise_for_status()
    payload = response.json()
    results = payload.get("results") or []
    hits = results[0].get("results", []) if results else []
    return [
        {
            "productId": int(hit["productId"]),
            "productName": hit.get("productName"),
            "setName": hit.get("setName"),
            "productUrlName": hit.get("productUrlName"),
            "rarity": hit.get("rarityName"),
            "cdn": CDN.format(pid=int(hit["productId"])),
        }
        for hit in hits
        if hit.get("productId")
    ]


def main() -> int:
    work = json.loads((ROOT / "temp" / "missing22_work.json").read_text(encoding="utf-8"))
    todo = [card for card in work if card["variantId"] not in (35, 168)]
    out: list[dict[str, object]] = []
    for card in todo:
        query = card["collector"]
        try:
            hits = search(query)
            error = None
        except Exception as exc:  # noqa: BLE001 — 一張失敗唔應該炸停成個掃描
            hits, error = [], f"{type(exc).__name__}: {exc}"
        out.append({
            "variantId": card["variantId"],
            "collector": query,
            "wantName": card["name"],
            "wantSet": card["set"],
            "opaqueId": card["opaqueId"],
            "localReference": card["localPath"],
            "hitCount": len(hits),
            "error": error,
            "hits": hits,
        })
        print(f"{card['variantId']:>4} {query:<12} hits={len(hits):<3} {error or ''}", flush=True)
        time.sleep(1.2)
    target = ROOT / "temp" / "tcgplayer_candidates.json"
    target.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n寫入 {target}")
    print("有候選:", sum(1 for row in out if row["hitCount"]), "/", len(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
