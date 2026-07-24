# SNKRDUNK Market Data Pipeline

**目的**：針對 cardz-market-cap 嘅 600 張卡，拎 SNKRDUNK K 線、成交額、交易量。

**檔案**：
- `pipelines/snkrdunk_bulk.py` — API client（由 cardz-platform 搬過嚟）
- `pipelines/snk_market_data.py` — 市場數據 pipeline
- `docs/SNKRDUNK_API_MANUAL.md` — 完整操作手冊

---

## 快速開始

### 1. 單張卡測試

```powershell
python -X utf8 pipelines/snk_market_data.py 116069 --condition trading_card_single_psa10 --out data/runtime/private-source-runs/test/snk.jsonl --delay 0.5
```

**輸出**：一行 JSON，含：
- `kline` — 3 年逐日價格（1083 點）
- `recent_trades` — 最近 20 單成交
- `daily_activity` — 按日成交額 + 交易量
- `used_min_price`, `used_listing_count` — 即時數據

### 2. BFS 自動發現 600 張卡

由一張卡開始，沿 same-category 擴展到 600 張：

```powershell
python -X utf8 pipelines/snk_market_data.py --discover-from 116069 --max-ids 600 --out data/private/snk/market_data.jsonl --delay 1.5
```

**時間**：BFS 約 2 分鐘，拎數據約 15 分鐘（600 卡 × 1.5 秒）。

### 3. 由 ID list 批量

如果你已經有 SNK apparel IDs：

```powershell
# 準備 ID list（一行一個）
echo 116069 > pipelines/snk_ids.txt
echo 115238 >> pipelines/snk_ids.txt

# 批量拎
python -X utf8 pipelines/snk_market_data.py --ids-file pipelines/snk_ids.txt --out data/private/snk/market_data.jsonl
```

### 4. 逐 condition K 線

PSA10 獨立 K 線：

```powershell
python -X utf8 pipelines/snk_market_data.py 116069 --condition trading_card_single_psa10 --out data/private/snk/psa10.jsonl
```

---

## 輸出格式

每張卡一行 JSON：

```json
{
  "item_id": 116069,
  "product_catalog_id": 192226,
  "product_number": "pkmn-tcg-SV1a-080",
  "localized_name": "コイキング AR[SV1a 080/073]",
  "kline": [
    {"date": "2023-03-09", "price_jpy": 1362},
    {"date": "2023-03-10", "price_jpy": 1624},
    ...
  ],
  "recent_trades": [
    {"price": 25000, "soldAt": "2026-07-22T10:46:49Z", "title": "A", "label": "1枚"},
    ...
  ],
  "daily_activity": {
    "2026-07-22": {"count": 2, "value_jpy": 70000},
    "2026-07-21": {"count": 8, "value_jpy": 350000},
    ...
  },
  "used_min_price": 23333,
  "used_listing_count": 42,
    "fetched_at": "2026-07-23T00:00:00Z"
}
```

---

## 整合入現有 pipeline

### 轉換成 `historyDaily` 格式

現有 `g10_public_snapshot.py` 用嘅格式：

```python
def snk_to_history_daily(snk_data: dict) -> list[dict]:
    """Convert SNK kline to historyDaily format."""
    return [
        {
            "at": f"{point['date']}T00:00:00Z",
            "priceStatus": "available",
            "priceUsd": None,  # 要 FX 轉換
            "priceJpy": point["price_jpy"],
            "salesCoverage": "full",
            "trackedSalesCount": snk_data["daily_activity"].get(point["date"], {}).get("count", 0),
            "trackedSalesValueJpy": snk_data["daily_activity"].get(point["date"], {}).get("value_jpy", 0),
        }
        for point in snk_data["kline"]
    ]
```

### 計算市場指標

```python
def compute_market_metrics(snk_data: dict, psa10_population: int) -> dict:
    """Compute reference price and market cap using exact PSA 10 population."""
    kline = snk_data["kline"]
    if not kline:
        return {}

    latest_price = kline[-1]["price_jpy"]
    listing_count = snk_data.get("used_listing_count", 0)

    # 7 日成交量
    recent_7d = [
        day for day, activity in snk_data["daily_activity"].items()
        if day >= (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    ]
    volume_7d = sum(snk_data["daily_activity"][d]["count"] for d in recent_7d)
    value_7d = sum(snk_data["daily_activity"][d]["value_jpy"] for d in recent_7d)

    return {
        "latest_price_jpy": latest_price,
        "listing_count": listing_count,
        "volume_7d": volume_7d,
        "value_7d_jpy": value_7d,
        "market_cap_jpy": latest_price * psa10_population,
    }
```

---

## 進階用法

### 批量 PSA10 K 線

```powershell
python -X utf8 pipelines/snk_market_data.py --ids-file pipelines/snk_ids.txt --condition trading_card_single_psa10 --out data/private/snk/psa10_klines.jsonl
```

### 每日增量更新

第一次保留完整 K 線作 1d／7d／30d backfill；之後每個 immutable daily run append 新 close 及最近成交。最近 20 單只代表 partial coverage，唔可以當全市場成交額。

```python
# 每日 job
api = SnkrdunkApi(delay=1.5)
for item_id in watchlist_ids:
    master = api.get_master(item_id)
    history = api.get_trading_history(master["productCatalogId"], range_="all")
    today_trades = [
        t for t in history["trades"]
        if t["soldAt"].startswith(datetime.now().strftime("%Y-%m-%d"))
    ]
    # append 入 database
```

### 發現新卡

每週行一次 BFS，搵新出嘅卡：

```powershell
python -X utf8 pipelines/snk_market_data.py --discover-from 116069 --max-ids 2000 --out data/private/snk/discovery.jsonl
```

跟住比對現有 600 張卡，搵出新嘅 item_id。

---

## 故障排除

### Q: 點解有啲卡拎唔到？

A: 檢查：
1. item_id 係咪 numeric（唔係 product number）
2. 張卡係咪仲喺 SNK 度（可能已下架）
3. 網絡問題（retry）

### Q: K 線點數好少？

A: 正常。冷門卡可能得幾十點，熱門卡先有 1000+ 點。

### Q: 成交額點解係 0？

A: `daily_activity` 只係由 `recent_trades`（最近 20 單）計算。如果最近 20 單都係舊日期，今日就冇數據。

### Q: 點樣拎晒全部歷史成交？

A: SNK API 只俾最近 20 單。要全量歷史，你要每日累積。

---

## 效能

| 操作 | 時間（1.5s delay） |
|---|---|
| 1 張卡 | 4.5 秒 |
| 600 張卡 | 15 分鐘 |
| BFS 發現 600 張 | 2 分鐘 |
| BFS + 拎數據 600 張 | 17 分鐘 |

**加速**：減 delay 到 0.5 秒（實測冇問題），但唔好再快。

---

## 下一步

1. **建立 SNK ID mapping**：將現有 600 張卡 map 到 SNK apparel IDs
2. **每日增量**：append `recent_trades` 入 `historyDaily`
3. **FX 轉換**：JPY → USD（用現有 `fx_rates.py`）
4. **整合入 snapshot**：將 SNK 數據 merge 入 `g10_public_snapshot.py`
