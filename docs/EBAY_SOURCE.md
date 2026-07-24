# eBay 操作手冊：CARDZ 驗證／fallback 價格來源

**版本**：2026-07-24
**定位**：eBay 係 SNK 之外嘅 **validation / fallback** 價格來源。

> **⚠ 2026-07-24 重要發現**：eBay 用 **PerimeterX**，佢做 **JS fingerprinting**（唔止 TLS 指紋）。`curl_cffi` 可以完美過 GemRate 嘅 Cloudflare，但**過唔到 eBay 嘅 PerimeterX**（實測 10 個 impersonate target 全部彈 challenge）。所以 **eBay item 頁一定要靠真 browser（Playwright + stealth）**，冇得「內部直打」。SNKRDUNK（CloudFront）同 GemRate（Cloudflare）就可以純 `curl_cffi`/`requests` 唔使 browser。

---

## 0. TL;DR

```powershell
cd C:\Users\jackson0202\Documents\Playground\cardz-market-cap

# 逐件貨結構化抽取（要靠 Playwright，見下）
# 用返 git 歷史入面嘅 Playwright 版 ebay_brute_harvest.py item
```

**三個來源嘅「使唔使 browser」總表**：

| 來源 | 防護 | 使唔使 browser | 方法 |
|---|---|---|---|
| **SNKRDUNK** | CloudFront | **唔使** | plain `requests` 打 JSON API |
| **GemRate** | Cloudflare | **唔使** | `curl_cffi` 直拎 inline `setsData`/`rowData` |
| **eBay** | **PerimeterX（JS fingerprinting）** | **要** | Playwright + 真 Chrome + stealth |

---

## 1. 已驗證路線

### 1.1 Item 頁 Product JSON-LD（✅ 可用，但要 Playwright）

`GET https://www.ebay.com/itm/{item_id}` 嘅 HTML 入面有 `<script type="application/ld+json">`，`@type: "Product"` 個 object 直接有齊：

| 欄位 | JSON-LD 路徑 | 實測（item 176641588834） |
|---|---|---|
| 價錢 | `offers.price` | `130518.0` |
| 貨幣 | `offers.priceCurrency` | `JPY` |
| 鑑定公司 | `additionalProperty[name="Grading Service"].value` | `PSA` |
| 等級 | `additionalProperty[name="Grade"].value` | `9` |
| **PSA cert number** | `hasCertification.certificationIdentification` | **`97279964`** |

**過 PerimeterX**：要用 Playwright + 真 Chrome（`executable_path`）+ stealth init script（去 `navigator.webdriver`）。item 頁會自動過（warmup 首頁之後）。`curl_cffi` 過唔到。

### 1.2 Completed/Sold 搜尋（⚠ PerimeterX 擋住）

`GET /sch/i.html?LH_Complete=1&LH_Sold=1` 會彈 challenge，browser 都唔自動過（要手動）。curl_cffi 更唔使諗。

---

## 2. 同 SNK 嘅分工

| 用途 | 用邊個 | 原因 |
|---|---|---|
| PSA 10 市值嘅價格 | **SNK** | 每日 K 線、逐 condition、唔使 browser |
| 驗證 SNK 價離唔離譜 | eBay sold（≥3 張 median） | 獨立市場 |
| PSA cert 驗證 | eBay item JSON-LD | SNK 冇 cert number（但要 browser 拎） |

---

## 3. 腳本同產物

| 檔案 | 用途 | browser |
|---|---|---|
| [pipelines/ebay_brute_harvest.py](pipelines/ebay_brute_harvest.py) | item JSON-LD 抽取 | **Playwright 版（git 歷史）** |
| [pipelines/ebay_sold_data.py](pipelines/ebay_sold_data.py) | 嚴格 normalizer | — |

**注意**：而家 repo 入面嘅 `ebay_brute_harvest.py` 係 curl_cffi 版（過唔到 PerimeterX，拎到空）。**item 路線要用返 git 歷史嘅 Playwright 版**（`git log --oneline -- pipelines/ebay_brute_harvest.py` 搵返）。

---

## 4. 已知問題 / 下一步

1. **eBay 冇得「內部直打」**：PerimeterX 要 JS 執行環境，curl_cffi 過唔到。想要真內部 API 要用官方 eBay Browse API（要 developer account + OAuth）。
2. **Sold search**：要咪手動過一次，要咪官方 API。
