# SNKRDUNK 深度 API 操作手冊

**版本**：2026-07-24
**狀態**：全部 endpoint 已驗證（CloudFront 200、免登入、無簽名、無 cookie 校驗）
**目標**：一鍵拎整份表，唔使下下 call 咁耐，唔使開瀏覽器

> **2026-07-24 更新**：新增**全圖鑑 discovery 路線**（`snkrdunk_discover.py`）。發現 BFS `same-category` 只會停喺一個 set（~26 張），而 `/search?keywords=...&page=N` 係 **server-rendered HTML**，plain `requests` 就抽到 `/apparels/{id}`，分頁去到 100+ 都有新卡。呢條先係真正嘅全圖鑑 route。

---

## 0. TL;DR

```powershell
# 0. 全圖鑑發現（最快，plain requests，唔使 browser）→ 幾千個 item id
cd C:\Users\jackson0202\Documents\Playground\cardz-market-cap
.\.venv-backend\Scripts\python.exe -X utf8 pipelines\snkrdunk_discover.py `
  --keywords ポケモンカードゲーム pokemon ワンピースカードゲーム `
  --max-pages 60 --out data\private\snkrdunk_brute\discovered_ids.txt

# 1. 發現完即時 harvest 全表（master + 16 condition + K線 + 成交，斷點續傳）
.\.venv-backend\Scripts\python.exe -X utf8 pipelines\snkrdunk_discover.py `
  --keywords ポケモンカードゲーム --max-pages 60 --harvest `
  --harvest-out data\private\snkrdunk_brute\snkrdunk_all.jsonl

# 2. 已知 id 直接批量
.\.venv-backend\Scripts\python.exe -X utf8 pipelines\snkrdunk_bulk.py 116069 115238 116083 --out snkrdunk_dump.jsonl

# 3. 逐 condition K 線（PSA10）
.\.venv-backend\Scripts\python.exe -X utf8 pipelines\snkrdunk_bulk.py 116069 --condition trading_card_single_psa10
```

**注意**：`snkrdunk_bulk.py` 而家有兩份——一份喺 `cardz-platform\workers\scrapers\`（舊整合），一份喺 `cardz-market-cap\pipelines\`（今次驗證嘅版本）。上面指令用 market-cap 嗰份。

---

## 0.1 全圖鑑 discovery（2026-07-24 新增，推薦）

| 路線 | 覆蓋 | 速度 | 備註 |
|---|---|---|---|
| **search-HTML（推薦）** `GET /search?keywords=...&page=N` 抽 `/apparels/{id}` | 幾千張，跨晒所有 set | plain requests，~0.7s/頁 | **真正全圖鑑**，server-rendered 唔使 browser |
| BFS `same-category` | ~26 張（一個 set） | 快但窄 | 只適合單 set 擴展 |

**search-HTML 點用**：

```python
import requests, re, urllib.parse, time
s = requests.Session()
s.headers["User-Agent"] = "Mozilla/5.0 ... Chrome/150.0.0.0 Safari/537.36"
seen = set()
for page in range(1, 61):
    url = "https://snkrdunk.com/search?keywords=" + urllib.parse.quote("ポケモンカードゲーム") + f"&page={page}"
    ids = [int(x) for x in dict.fromkeys(re.findall(r"/apparels/(\d+)", s.get(url).text))]
    seen.update(ids)
    time.sleep(0.7)
# seen = 全圖鑑 item ids，逐個 call /v1/apparels/{id} 拎 master
```

**實測**（keyword=`ポケモンカードゲーム`）：page 1=39 卡，page 10 累計 236，page 100 累計 200+（每頁仍有 11-30 張新卡）。ID 空間**唔連續**（116069=SV1a 鯉魚王，618445=另一張卡，854923=One Piece），所以唔可以 ID 範圍掃，一定要靠 search 翻頁。

---

## 1. 舊 vs 新：你本機腳本落後咗一代

| | 舊（`LiveSnkrdunkProvider`） | 新（`snkrdunk_bulk.py`） |
|---|---|---|
| **方法** | HTML GET `/search?keyword=...` 然後 regex 夾 `<span class="price">` | 直接打內部 JSON API |
| **拎到咩** | 1 個最低價 | 3 年逐日 K 線 + 最近 20 單成交 + 16 condition 價 + 完整 master |
| **歷史** | 冇（要靠每日 snapshot 累積） | 一個 call 拎晒 2023-03 至今 |
| **condition** | 冇 | A/B/C/D/PSA10/PSA9/... 16 個逐個拎 |
| **速度** | 每卡 1 個 HTML page（慢、重） | 每卡 3 個 JSON call（輕、CloudFront 快取） |
| **結構改動** | 會死（regex 對唔到就 `ScraperStructureError`） | API 穩定得多（係 frontend 自己用嘅） |
| **browser** | 唔使 | 唔使（`requests` 直接搞掂） |

---

## 2. Endpoint 總表（全部驗證過）

### 2.1 Product Master

```
GET /v1/apparels/{itemId}
```

**返回**：`id`, `productCatalogId`, `productNumber`（例如 `pkmn-tcg-SV1a-080`）, `name`, `localizedName`, `primaryMedia.imageUrl`, `releasedAt`, `usedMinPrice`, `usedListingCount`, `brands`, `sizes`, `categories`

**用途**：由 apparel id 攞 `productCatalogId`（chart endpoint 要用佢）。

### 2.2 16 Condition 即時價

```
GET /v2/products/{itemId}/size-chips?type=apparel
```

**返回**：`chips[]`，每個 condition 一個：

```json
{
  "conditionId": 22,
  "filterConditionId": "psa_10",
  "usedMinPrice": 45000,
  "text": "PSA10",
  "hasListing": true,
  "listingCount": null
}
```

**16 個 condition**：

| filterConditionId | 名 | chart condition_code |
|---|---|---|
| `like_new` | A | `trading_card_single_nearly_unused` |
| `minor_scratches` | B | `trading_card_single_little_scratches` |
| `moderate_scratches` | C | `trading_card_single_medium_scratches` |
| `significant_damage` | D | `trading_card_single_large_damages` |
| `psa_10` | PSA10 | `trading_card_single_psa10` |
| `psa_9` | PSA9 | `trading_card_single_psa9` |
| `psa_8_below` | PSA8以下 | `trading_card_single_psa8_under` |
| `bgs_10_black` | BGS10 BL | `trading_card_single_bgs10bl` |
| `bgs_10_gold` | BGS10 GL | `trading_card_single_bgs10gl` |
| `bgs_9_5` | BGS9.5 | `trading_card_single_bgs95` |
| `bgs_9_below` | BGS9以下 | `trading_card_single_bgs9_less_than_or_equal` |
| `ars_10_plus` | ARS10+ | `trading_card_single_ars10plus` |
| `ars_10` | ARS10 | `trading_card_single_ars10` |
| `ars_9` | ARS9 | `trading_card_single_ars9` |
| `ars_8_below` | ARS8以下 | `trading_card_single_ars8_less_than_or_equal` |
| `other_grading_company` | 他鑑定品 | `trading_card_single_other_grading_company` |

### 2.3 3 年逐日 K 線 + 最近成交

```
GET /v3/products/{productCatalogId}/trading-history?range=all
```

**返回**：
- `filters.variants.options[]` — 枚數 variant ids（1993521=1枚 ... 1993530=10枚）
- `filters.conditions.options[]` — 16 個 condition codes
- `trades[]` — 最近 20 單成交（`price`, `soldAt`, `title`, `label`）
- `chart.lines[0].points[]` — 逐日 K 線（`timestamp` ms, `price` JPY），由 2023-03 到今日，共 ~1082 點

**參數**：

| 參數 | 值 | 效果 |
|---|---|---|
| `range` | `1w` / `1m` / `3m` / `all` | 而家 server 唔理，全部回傳全量 |
| `condition_code` | `trading_card_single_*` | 逐 condition K 線（**呢個係真參數**） |
| `variant_id` | `1993521`...`1993530` | 逐枚數 K 線（**呢個都係真參數**） |

**示例**：
- `?range=all&condition_code=trading_card_single_psa10` → 921 點 PSA10 K 線
- `?range=all&variant_id=1993522` → 136 點 2枚 K 線
- `?range=all&condition_code=trading_card_single_psa10&variant_id=1993522` → 118 點 PSA10 2枚 K 線

### 2.4 BFS 擴展（發現新卡）

```
GET /v1/apparels/{itemId}/group-items/same-category?page=1&perPage=13
```

**返回**：`apparels[]`（13 張同組卡，每張都有完整 master 欄位）, `apparelGroupId`, `apparelGroupTitle`

**用途**：由一張卡開始，沿 same-category 逐頁行到空，自動擴展全圖鑑。

### 2.5 其他（備用）

- `GET /v1/apparels/{id}/group-items?page=1&perPage=5` — 同 product 其他 variant
- `GET /v3/search/suggestions?keyword=...&limit=10` — autocomplete
- `GET /v3/search/filter` — category tree

---

## 3. 四大進階技巧

### 3.1 免硬編碼 x-version

**事實**：`x-version` 係 build tag（例如 `prod-20260722-02`），唔送都 200。但送埋同前端一致，穩陣啲。

**做法**：每次開 job，由 `/apparels/{id}` 頁面 HTML scrape 一次：

```python
import re
X_VERSION_RE = re.compile(r'\\?"version\\?":\\?"(prod-\d{8}-\d{2})\\?"')

def fetch_x_version(session):
    r = session.get("https://snkrdunk.com/apparels/116069")
    m = X_VERSION_RE.search(r.text)
    if m:
        session.headers["x-version"] = m.group(1)
        return m.group(1)
```

**實測**：2026-07-22 係 `prod-20260722-02`。

### 3.2 itemId 自動發現（BFS）

**問題**：你手頭 530 個 ID 係死嘅。新卡出咗你要手動加。

**做法**：由一張卡開始，沿 `same-category` 逐頁行：

```python
def bfs_discover(api, seeds, max_ids=2000):
    seen = set(seeds)
    queue = list(seeds)
    result = list(seeds)
    while queue and len(result) < max_ids:
        current = queue.pop(0)
        page = 1
        while len(result) < max_ids:
            ids = api.get_same_category(current, page=page)
            if not ids:
                break
            for iid in ids:
                if iid not in seen:
                    seen.add(iid)
                    result.append(iid)
                    queue.append(iid)
            if len(ids) < 13:
                break
            page += 1
    return result
```

**實測**：由 116069（コイキング AR）出發，page 1 拎到 115238, 116083, 116082... page 2 拎到 116076, 116075... 一路擴展。

### 3.3 逐 condition K 線

**問題**：`trading-history` 預設係 aggregate（混合全部 condition）。

**做法**：加 `condition_code` 參數：

```python
# aggregate（全部 condition 混埋）
api.get_trading_history(192226)

# PSA10 獨立 K 線
api.get_trading_history(192226, condition_code="trading_card_single_psa10")

# A condition 獨立 K 線
api.get_trading_history(192226, condition_code="trading_card_single_nearly_unused")
```

**實測**（productCatalogId=192226, コイキング AR）：

| condition_code | 點數 | 最新價 | 最近成交 |
|---|---|---|---|
| （無，aggregate） | 1082 | ¥22,333 | A ¥25,000 |
| `trading_card_single_psa10` | 921 | ¥48,175 | PSA10 ¥48,500 |
| `trading_card_single_nearly_unused` | 545 | — | A ¥25,000 |
| `trading_card_single_psa9` | 111 | — | PSA9 ¥27,980 |

**用途**：你可以逐 grade 出 K 線，唔使淨係 aggregate。PSA10 同 A 嘅走勢完全唔同。

### 3.4 逐枚數 K 線

**做法**：加 `variant_id` 參數（由 `filters.variants.options` 拎）：

```python
# 2枚 K 線
api.get_trading_history(192226, variant_id=1993522)

# PSA10 2枚 K 線
api.get_trading_history(192226, condition_code="trading_card_single_psa10", variant_id=1993522)
```

**實測**：2枚 variant 有 136 點，PSA10 2枚 有 118 點。

---

## 4. 純 requests 實作（唔使瀏覽器）

**事實**：全部 endpoint 係 CloudFront 前面嘅 JSON API，無簽名、無 cookie 校驗、免登入。`requests` 直接搞掂。

```python
import requests

s = requests.Session()
s.headers["User-Agent"] = "Mozilla/5.0 ..."

# 拎 master
master = s.get("https://snkrdunk.com/v1/apparels/116069").json()
pcid = master["productCatalogId"]

# 拎 K 線
history = s.get(f"https://snkrdunk.com/v3/products/{pcid}/trading-history?range=all").json()
points = history["chart"]["lines"][0]["points"]

# 拎 16 condition 價
chips = s.get("https://snkrdunk.com/v2/products/116069/size-chips?type=apparel").json()["chips"]
```

**點解我哋用 DevTools fetch**：只係為咗觀察同驗證。Production scraper 唔使。

---

## 5. 一鍵全表操作

### 5.1 單張卡

```powershell
python -X utf8 workers\scrapers\snkrdunk_bulk.py 116069 --out card.jsonl
```

**輸出**：一行 JSON，含 master + chips + 1082 點 K 線 + 20 單成交。

### 5.2 批量（你手頭 530 個 ID）

```powershell
python -X utf8 workers\scrapers\snkrdunk_bulk.py 116069 115238 116083 ... --out dump.jsonl
```

**斷點續傳**：已喺檔案嘅 id 會 skip。ctrl+C 之後再行，會由上次停嘅位繼續。

### 5.3 自動發現全圖鑑

```powershell
python -X utf8 workers\scrapers\snkrdunk_bulk.py --discover-from 116069 --max-ids 2000 --out all.jsonl
```

**流程**：BFS 由 116069 擴展到 2000 張卡，然後逐張拎 master + K 線 + chips。

### 5.4 逐 condition K 線

```powershell
python -X utf8 workers\scrapers\snkrdunk_bulk.py 116069 --condition trading_card_single_psa10 --out psa10.jsonl
```

**用途**：PSA10 同 A 嘅走勢完全唔同，你要分開追蹤。

---

## 6. 整合入現有 pipeline

### 6.1 取代 `LiveSnkrdunkProvider`

舊嘅 `get_lowest_price` 可以改成：

```python
def get_lowest_price(self, card_key: str) -> PricePoint:
    item_id = self._resolve_item_id(card_key)  # 由 productNumber 搵 itemId
    master = self.api.get_master(item_id)
    return PricePoint(
        source="snkrdunk",
        card_key=card_key,
        price=master["usedMinPrice"],
        currency="JPY",
        condition="A",
        url=f"https://snkrdunk.com/apparels/{item_id}",
        fetched_at=time.time(),
    )
```

### 6.2 加返 `get_price_history`

舊嘅 `get_price_history` 係 `NotImplementedError`，而家可以：

```python
def get_price_history(self, card_key: str, days: int = 30) -> list[PricePoint]:
    item_id = self._resolve_item_id(card_key)
    master = self.api.get_master(item_id)
    history = self.api.get_trading_history(master["productCatalogId"])
    points = history["chart"]["lines"][0]["points"]
    cutoff = time.time() - days * 86400
    return [
        PricePoint(
            source="snkrdunk",
            card_key=card_key,
            price=p["price"],
            currency="JPY",
            condition="aggregate",
            url=f"https://snkrdunk.com/apparels/{item_id}",
            fetched_at=p["timestamp"] / 1000,
        )
        for p in points if p["timestamp"] / 1000 >= cutoff
    ]
```

### 6.3 加逐 condition 歷史

```python
def get_price_history_by_condition(self, card_key: str, condition: str) -> list[PricePoint]:
    item_id = self._resolve_item_id(card_key)
    master = self.api.get_master(item_id)
    history = self.api.get_trading_history(
        master["productCatalogId"],
        condition_code=SnkrdunkApi.CONDITION_CODE_MAP[condition]
    )
    # ... 同上
```

---

## 7. 故障排除

### 7.1 404 on `/trading-cards/{id}`

**原因**：卡唔係住喺 `/trading-cards/`，係 `/apparels/`。

**解決**：全部卡都係 `GET /v1/apparels/{id}`。

### 7.2 `range=1m` 冇效果

**現狀**：server 唔理 `range` 參數，全部回傳全量。

**解決**：照用 `range=all`，喺 client 側 filter 日期。

### 7.3 condition filter 冇效果

**原因**：你用咗 `condition=` 或者 `conditionId=`，呢啲唔係真參數。

**解決**：用 `condition_code=trading_card_single_*`。

### 7.4 `variantId` 冇效果

**原因**：你用咗駝峰式 `variantId`，server 唔認。

**解決**：用蛇形式 `variant_id=1993522`。

### 7.5 x-version 搵唔到

**現象**：`fetch_x_version()` 返回 `None`。

**影響**：冇影響，唔送 `x-version` header 都 200。

---

## 8. 效能預算

| 操作 | Call 數 | 時間（1.5s delay） |
|---|---|---|
| 1 張卡（master + chips + history） | 3 | 4.5 秒 |
| 530 張卡 | 1590 | 40 分鐘 |
| BFS 發現 2000 張卡 | ~154（每卡 1 個 same-category call，假設每頁 13 張） | 4 分鐘 |
| BFS 發現 + 拎全表 2000 張卡 | 154 + 6000 | 2.6 小時 |

**加速**：
- 減 delay 到 0.5 秒（實測冇問題，但係唔好再快）
- 唔好逐張拎 chips（如果你淨係要 K 線）
- 唔好逐張拎 history（如果你淨係要即時價）

---

## 9. 下一步

1. **取代舊 provider**：將 `snkrdunk_bulk.py` 整合入 `apps/api/app/providers/snkrdunk.py`
2. **每日增量**：唔好下下拎全量，可以：
   - 每日拎 `trades[]`（最近 20 單），filter 出今日嘅成交，append 入 `price_history`
   - 每週拎一次全量 K 線做 backfill
3. **逐 condition 追蹤**：PSA10 同 A 分開兩條線

---

## 附錄 A：完整 condition_code 對照表

| chips.filterConditionId | chips.text | trading-history condition_code |
|---|---|---|
| `like_new` | A | `trading_card_single_nearly_unused` |
| `minor_scratches` | B | `trading_card_single_little_scratches` |
| `moderate_scratches` | C | `trading_card_single_medium_scratches` |
| `significant_damage` | D | `trading_card_single_large_damages` |
| `psa_10` | PSA10 | `trading_card_single_psa10` |
| `psa_9` | PSA9 | `trading_card_single_psa9` |
| `psa_8_below` | PSA8以下 | `trading_card_single_psa8_under` |
| `bgs_10_black` | BGS10 BL | `trading_card_single_bgs10bl` |
| `bgs_10_gold` | BGS10 GL | `trading_card_single_bgs10gl` |
| `bgs_9_5` | BGS9.5 | `trading_card_single_bgs95` |
| `bgs_9_below` | BGS9以下 | `trading_card_single_bgs9_less_than_or_equal` |
| `ars_10_plus` | ARS10+ | `trading_card_single_ars10plus` |
| `ars_10` | ARS10 | `trading_card_single_ars10` |
| `ars_9` | ARS9 | `trading_card_single_ars9` |
| `ars_8_below` | ARS8以下 | `trading_card_single_ars8_less_than_or_equal` |
| `other_grading_company` | 他鑑定品 | `trading_card_single_other_grading_company` |

---

## 附錄 B：實測數據（116069 コイキング AR）

```
x_version: prod-20260722-02
name: コイキング AR[SV1a 080/073]
pcid: 192226
points: 1082 (2023-03-09 → 2026-07-22)
last point: {timestamp: 1784646000000, price: 22333}
trades: 20
first trade: {price: 25000, soldAt: "2026-07-22T10:46:49Z", title: "A", label: "1枚"}
chips: 16
psa10 chip: {usedMinPrice: 45000, hasListing: True}
```

**逐 condition K 線點數**：
- aggregate: 1082 點
- PSA10: 921 點
- A: 545 點
- PSA9: 111 點

**逐枚數 K 線點數**：
- 1枚（預設）: 1082 點
- 2枚: 136 點
- PSA10 2枚: 118 點
