# PriceCharting CF 清關成功經驗（2026-07-30）

## 結論（一句）

**用你已經開住、有 remote debugging 嘅真 Chrome（CDP `connect` / `fetch`）過 CF；headless / 舊 cookie 重放會失敗。**

EN 卡 PC 頁係有嘅——失敗係 transport，唔係「搵唔到卡」。

---

## 成功路徑（已驗證 · 兩輪）

```text
1) 本機 Chrome 已開 remote debugging（例 :9222）
2) python -X utf8 pipelines/pricecharting_cf_session.py connect \
     --port 9222 \
     --url "https://www.pricecharting.com/game/pokemon-paldea-evolved/magikarp-203" \
     --timeout 180
3) 等 title 變產品名（唔係「請稍候」/ Just a moment）
4) 儲存 cf_clearance + capture HTML
5) 同一 session 批量 fetch（仍優先 CDP，唔 headless）
6) parse: pipelines/pricecharting_page_parse.py <html> --out ... --psa10-only
7) attach 去 variant_id ledger + catalog_source_identity(pricecharting)
```

### 清關硬條件

| 檢查 | 通過 |
|------|------|
| CDP port | `http://127.0.0.1:9222/json/version` 有 Browser 回覆 |
| stdout | `ok=True` / `cf=False` |
| title | 卡名 + set（唔係 Just a moment） |
| html_len | 通常 **> 100000**（成功約 850–920KB） |
| cookie | `has_cf_clearance: true` |

---

## 實測 batch r2（2026-07-30 ~14:48–14:49）

Chrome: `Chrome/150` · CDP `:9222` · 三張連續 **全部 ok**

| variant_id | 卡 | PC URL（canonical） | product_id | PSA10 sold | price range USD | CF |
|------------|----|---------------------|------------|------------|-----------------|-----|
| **1268** | Magikarp #203/193 Paldea Evolved | `/game/pokemon-paldea-evolved/magikarp-203` | **5287492** | **30** | **$3750–5400** | connect ok · html 910KB |
| **1728** | Squirtle #148 Stellar Crown | `/game/pokemon-stellar-crown/squirtle-148` | **7418633** | **30** | **$295–420** | fetch ok · html 861KB |
| **104** | Rayquaza VMAX TG20 Silver Tempest | `/game/pokemon-silver-tempest/rayquaza-vmax-tg20` | **4277108** | **30** | **$380–485** | fetch ok · html 912KB |

身份：三張均有 **GemRate + PriceCharting** → 滿足 **≥2 源** 準則。  
Ledger actor：`agent_pc_cf_r2`；`catalog_source_identity.source_code=pricecharting` 已 exact bind product_id。

### Magikarp 首通（同日稍早 · r1）

| 項 | 結果 |
|----|------|
| connect :9222 | **ok=True**，title `Magikarp #203 Prices \| Pokemon Paldea Evolved` |
| `cf_clearance` | **有** |
| product_id | **5287492** |
| PSA10 sold | **30** 筆 |
| GemRate PSA10 POP (DB) | **3969** |
| headless `fetch` 重放 | **仍 cf=True 失敗**（對照組） |

---

## 失敗路徑（唔再用）

| 方法 | 結果 |
|------|------|
| `web_fetch` / 裸 HTTP | CF challenge |
| `fetch` headless + 舊 storage_state | 請稍候 / Just a moment |
| 只靠 `cf_clearance` cookie 檔、唔經真 Chrome | 易過期／指紋唔匹配 |

---

## 標準 SOP（每次 CF 過期）

1. **確保 Chrome 帶 debug port**
   ```text
   chrome.exe --remote-debugging-port=9222
   ```
   或跑 [`scripts/open_chrome_pricecharting_debug.ps1`](../scripts/open_chrome_pricecharting_debug.ps1)。  
   驗證：`Invoke-WebRequest http://127.0.0.1:9222/json/version`

2. **connect 清關 + 落第一頁**
   ```powershell
   cd C:\Users\jackson0202\Documents\Playground\cardz-market-cap
   python -X utf8 pipelines/pricecharting_cf_session.py connect --port 9222 --url "https://www.pricecharting.com/game/..." --timeout 180
   ```
   若 Chrome 視窗出 challenge，用人手 click 一次。

3. **確認 success**
   - stdout `ok=True` / `has_cf_clearance: true`
   - title 係卡名 + set
   - html_len 通常 > 100000

4. **批量產品頁（同一真 Chrome session）**
   ```powershell
   python -X utf8 pipelines/pricecharting_cf_session.py fetch `
     --url "https://www.pricecharting.com/game/<set>/<slug>" `
     --out data/private/pricecharting_session/html/<slug>.html `
     --timeout 120
   ```
   `fetch` 已 **CDP-first**（試 9222 → 9333 → 9223），唔好 `--headed` headless 當主路徑。

5. **parse + ledger**
   ```powershell
   python -X utf8 pipelines/pricecharting_page_parse.py data/private/pricecharting_session/html/<slug>.html `
     --out data/private/pricecharting_session/parsed_<slug>.json --psa10-only

   python -X utf8 pipelines/identity_evidence_ledger.py attach `
     --variant-id <VID> --source pricecharting --kind external_url `
     --url "https://www.pricecharting.com/game/..." `
     --external-id <product_id> --status verified --actor agent_pc_cf
   ```

---

## 身份／URL 教訓（batch r2）

1. **必須用正確 set slug**  
   - Squirtle #148 DB = **Stellar Crown** → PC path `pokemon-stellar-crown/squirtle-148`  
   - 誤用 `pokemon-scarlet-violet-151/squirtle-148` 會被 site 搜尋導向；要靠 title/set 再核對，唔好盲 attach。
2. **TPL 不可信作 identity**（1728 曾 bind Base Set 2 Squirtle）— 已 purge；PC + GemRate 先係 EN 主證據。
3. **identity 最低 = 任意 2 源一致**（例 GemRate + PC）— 見 [`IDENTITY_VERIFICATION_CRITERIA.md`](IDENTITY_VERIFICATION_CRITERIA.md)。

---

## 同身份準則（2 源）

- **最低：任意 2 源一致**（例 GemRate + PC）→ 身份準  
- EN 卡第二源優先 **PriceCharting**（產品頁應有）  
- 見 `docs/IDENTITY_VERIFICATION_CRITERIA.md`

---

## 產物路徑

| 檔 | 用途 |
|----|------|
| `data/private/pricecharting_session/session_meta.json` | 最新 session 狀態 |
| `data/private/pricecharting_session/storage_state.json` | Playwright state（headless 仍可能失效） |
| `data/private/pricecharting_session/html/capture_connect.html` | connect 成功 HTML（Magikarp） |
| `data/private/pricecharting_session/html/squirtle-148.html` | Squirtle batch |
| `data/private/pricecharting_session/html/rayquaza-vmax-tg20.html` | TG20 batch |
| `data/private/pricecharting_session/parsed_magikarp_203.json` | parse 結果 |
| `data/private/pricecharting_session/parsed_squirtle_148_stellarcrown.json` | parse 結果 |
| `data/private/pricecharting_session/parsed_rayquaza_tg20.json` | parse 結果 |

---

## 成功經驗金句（之後批量照抄）

1. **真 Chrome + CDP 9222** = 過 CF 唯一穩定路徑  
2. **connect 一次 → fetch 連打**；唔使每張 relaunch  
3. **parse 先睇 title/set/product_id** 先 attach variant_id  
4. **ledger 掛喺 variant_id**（PC product_id + URL + sold count）  
5. **headless 當對照失敗就停**，唔再硬 retry storage_state  

---

## 本機工具對照（2026-07-30 實測 · 唔係天衣無縫）

### 錢 / 政策（hard · daddy）

- **只允許本地 / 自架 / 開源零月費**。
- **唔買** 雲端 Firecrawl / Browserless / CapSolver / 住宅代理訂閱。
- 本地已部署 Firecrawl / Camofox = **免費自架 OK**；要 SaaS API key 收費 = **唔用**。

### Camofox 搞錯釐清

| | 你部機實際 | GitHub 你貼 |
|--|-----------|-------------|
| Package | `@askjo/camofox-browser`（jo-inc） | [redf0x1/camofox-browser](https://github.com/redf0x1/camofox-browser) |
| 關係 | 同一「Camoufox 反偵測瀏覽器 server」家族 | redf0x1 = TS rewrite + CLI + 可選 MCP companion |
| Port | WSL **:9377** 已 health ok | 文檔預設一樣 9377 |
| 收費 | **本地自架免費** | npm/ghcr 自架免費；唔使買 SaaS |

**Camofox ≠ Firecrawl。** Firecrawl = 頁→MD；Camofox = 瀏覽器 API。兩者加埋**唔係一掣串流**。

### 本機矩陣（目標：PriceCharting Magikarp · 同一 URL）

| 工具 | 錢 | 本機狀態 | PC/CF 結果 | 適合作咩 |
|------|-----|----------|------------|----------|
| **真 Chrome + CDP :9222** | 免費 | Windows Chrome debug | **✅ 通**（主路徑） | PC 價/sold 全量 |
| **curl / 裸 HTTP** | 免費 | — | ❌ 403 Just a moment | 唔用 |
| **Firecrawl 本地 :3002** | 自架免費 | 已跑 | ❌ CF 驗證頁 / 403 | 普通文章→MD（非 PC） |
| **Camofox 本地 :9377** | 自架免費 | 已跑 + **自設 local key** | Auth ✅；PC 頁仍可能 CF（要再 A/B） | 見下方「本地 key」；agent 瀏覽/QA |
| **Scrapling stealth** | 免費 | WSL 有 package | ⛔ 缺 Playwright chromium（未 install） | 裝完可再測 |
| **agent-browser** | 免費 | 有 CLI | ⛔ 缺 headless shell | 裝完可再測 |
| **playwright-mcp / chrome-devtools-mcp** | 免費 | on-demand 掛 **:9222** | = CDP 路徑（同主路徑） | agent 控真 Chrome |
| 雲端 Firecrawl / 付費 anti-bot API | 要錢 | — | **政策：唔用** | — |

### Camofox 本地 key（自設 · 免費 · 唔使買）

**唔係付費 SaaS key**——自己 `openssl` 生、只喺本機 docker 用。

| 項 | 值 |
|----|-----|
| Env | `CAMOFOX_API_KEY` |
| WSL 檔 | `~/.camofox/local-api.env`（chmod 600） |
| Windows 私檔 | `data/private/camofox_local.env`（已 gitignore） |
| 用法 | `Authorization: Bearer $CAMOFOX_API_KEY` |
| Smoke | 2026-07-30：Bearer + evaluate `document.title` → `Example Domain` ✅ |
| 1Password | 標題建議：`Camofox Local API Key`（CLI 未 signin 時用下面指令補） |

```bash
# 解鎖 1Password 後（WSL 或 Windows op）：
source ~/.camofox/local-api.env   # 或讀 data/private/camofox_local.env
op item create --category "API Credential" --title "Camofox Local API Key" --vault Private \
  "credential=$CAMOFOX_API_KEY" \
  "username=local-self-issued" \
  "notesPlain=Self-issued FREE local key for WSL docker camofox-browser :9377. NOT paid SaaS."
```

### Firecrawl 實測（Magikarp）

```text
POST http://127.0.0.1:3002/v1/scrape
url = pricecharting Magikarp #203
→ success=true 但 markdown = security verification / title Just a moment... / statusCode 403
```

結論：**Firecrawl 適合「文章→MD」；唔適合 PriceCharting 硬 CF 產品頁。**

### Camofox 實測（Magikarp）

```text
health ok, engine=camoufox, browserRunning=true
POST /tabs {userId, sessionKey, url} → tabId 有，但 url 帶 __cf_chl_rt_tk（仍在 challenge）
POST /tabs/:id/evaluate 無 Bearer → 403 Forbidden
```

結論：**有 Camofox ≠ 自動過 PC CF。**  
要：`Authorization: Bearer $CAMOFOX_API_KEY` + 等 challenge 清完 + 自己寫腳本 parse HTML。  
**同 Firecrawl 唔係「一插即合」流水線**——兩套 API、兩個 port、兩個失敗模式。

### 路由建議（務實）

```text
文章 / 文檔 / 研究 MD     → Firecrawl（或 trafilatura）
硬 CF 產品頁（PriceCharting）→ 真 Chrome CDP（而家）
Camofox                     → 可選實驗：有 API key 後再 A/B Magikarp；
                              成功先寫 adapter，失敗繼續 CDP
```

**有冇需要改全用 Camofox？**  
- **而家唔使**——CDP 已能量產 HTML/sold。  
- **值得再試嘅情況**：想減少長開 Chrome / Windows CDP 鎖瓶頸，且願意配 `CAMOFOX_API_KEY` 做穩定 adapter。  
- **成功標準**：title 含卡名、html_len>100KB、parse 出 product_id + PSA10 sold rows。

---

## 下一步（PC 版本）

1. 用 **CDP fetch** 繼續全量 / residual（主路徑）  
2. `c11_pc_sold_ingest` 寫 PSA10 sold；有料有價就留 DB link  
3. fail 加深 resolve + retry  
4. （可選）Camofox + API key 單點 A/B；通先寫 `pipelines/pricecharting_camofox_fetch.py`  
5. 勿 pointer / deploy 除非 DADDY 明確批准  
