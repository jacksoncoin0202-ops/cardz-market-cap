# CardZ Marketcap 宣傳鏈

> **每日一條固定路。** Repo 17:45 task 只砌渠道 pack；Hermes gate 等 live bake
> 至少 30 分鐘後，按 receipt 只補未完成嘅 X／Threads 英中四步。
> Instagram、WhatsApp 目前暫停，唔計入 missing／完成條件；repo pack 亦只產四個 active 渠道。
> 公開出帖只准由 Hermes `cardz_marketcap_x_post.py`／`cardz_marketcap_meta_post.py`
> 經指定隔離 CDP 執行；Hermes job 啟用時，repo `promo_post.py --confirm` 會
> fail closed，避免雙 publisher。

> **錯帖清理鐵律：** 已確認重複、錯帳、錯 audience／community 或內容錯誤，
> 必須先刪除公開貼文，之後先可以重發。只停 retry、只寫 receipt、或只話
> `posted=true` 都唔算收口。每次公開動作必須保存平台 post URL／ID；如果已撳
> Post 但未攞到 URL，事件保持 `cleanup_required`，唔准自動再試。

**唔係即時 publish。** Live bake 成功後等 30 分鐘，再跑圖／文／generation gate。heatmap 同上一手 pack 同一 sha、或者 live `box.asOf` 唔係今日 JST = **舊 bake／舊圖**，即停。X／Threads 英中全部固定用隔離持久化 9222；9333 只屬 PriceCharting。

Hermes browser 操作按 port 使用 `~/.hermes/state/cardz-social-cdp-<PORT>.lock`；poster 進程必須先取得對應 lock，先可以讀 account、轉 tab 或 compose。未知 Threads handle 冇專用 port mapping 就 fail closed，唔准 fallback。

每日公開流程：

1. 自動更新鏈驗證並推咗上 Live（等 30 分鐘）
2. HTTP 拎熱力圖：`GET /api/og/heatmap?period=7d&show=40&scope=all&format=post&theme=dark&updown=green-up&lang=en`（對 generation header；**唔開 browser、唔截 :3900**）。`scope` = all / pokemon / one-piece；`format` = post（4:5 直）/ status（9:16）/ wide 或 landscape（橫）；`updown=red-up` 顏色反轉；`lang` = en / zh-TW / zh-CN。
3. 腳本出 TCG Top 100 最大升 3／最大跌 3（圖可以係 Top 40，**數字永遠講 Top 100 排名**）
4. 文案係模板，唔寫評論；結尾一條 https://cardzmarketcap.com
5. 依 receipt 推 X、Threads EN／ZH（圖+字）；Instagram、WhatsApp 暫停

---

## 邊步腳本、邊步 AI

| 步 | 邊個做 | 點做 | 無頭？ |
|---|---|---|---|
| 等 live | 人 | health generation 對到今次 run | — |
| Top 3 升／跌 | **腳本** | `python -X utf8 scripts/promo_chain.py brief` 讀 live `/api/v1/market`，**禁止睇圖估** | 係 |
| 熱力圖 | **腳本** `GET /api/og/heatmap`（period/show/scope/format/theme/updown/lang） | 先對 live health generation | 係，零 browser |
| 文案 | **腳本** `render_copy`：排名 + 語言名 + % + 🟢／🔴 | 唔准加評論 | 係 |
| 語文閘／洩漏閘 | **腳本** | `promo_chain.py assert <pack>` | 係 |
| Telegram 內部 | Hermes `send --to telegram:…`（已 set） | 內部通報，唔係公開帖 | 係 |
| X EN / X 中文 | 9222 重用 x.com tab | Hermes **冇** X token | 要頭 |
| Threads EN / 中文 | 9222；`@cardz.game`／`@cardz.gamezh` | 目標帳唔啱即停 | 要頭 |

腳本一次過做得到、唔好再用 AI 估：排行、generation、period、文案模板、洩漏掃、簡繁閘、9222 揀 tab。

**唔好寫評論。** 一講嘢，人就當你係真人嚟針對。

---

## Social CDP tab 紀律（hard）

同一 host **一個 tab**。唔得就喺**同一個 tab** 再試，最多 2 次；第三次停，唔准再開分頁。

```
python -X utf8 scripts/promo_chain.py pick-tab --channel x.com-en
```

- `reuse` → `chrome-devtools select_page` 去嗰個 pageId
- `open_once` → 先至 `new_page` **一次**
- `extraSameHost` 有值 = 已經開多咗，重用第一個，**唔好再 new_page**

禁止：開十幾個 tab 試同一 login／同一 compose。

---

## 登入順序（hard）

宣傳帳使用 watchdog 維持嘅隔離持久 9222 profile；唔係 Codex Chrome、用戶日常 Chrome、9224，亦唔係 9333（PriceCharting）。

1. 重用嗰個站已開嘅 tab
2. 已經登入目標帳 → 繼續
3. 登入頁有**已儲存帳號／一鍵撳入** → 先撳，唔好打字
4. Instagram／Threads 每個帳先由 Hermes `cardz_marketcap_meta_session.py --handle <cardz.game|cardz.gamezh> --port 9222` 建立 session；1Password 用 item title `Instagram`＋username 精確配對。密碼／TOTP／item ID 唔准印 log、唔准入 brief
5. helper 只喺 2FA input 可見後攞 fresh TOTP；Instagram 同 Threads profile 都見到 `Edit profile` 先算 `owner_confirmed`。唔接受 transient redirect，唔准開 composer

Threads 英／中分別由 `@cardz.game`／`@cardz.gamezh` 主 feed 出；每步先讀回目標 handle，唔啱即停，唔准靠 composer 內文或 audience 字樣估帳號。

---

## 語文（hard）

| 渠道 | 語文 |
|---|---|
| x.com 英文 | en |
| x.com 中文 | **簡體** |
| Threads 英文 | en |
| Threads 中文、Fork zh、站內中文 | **繁體** |

簡體只准 `x.com-zh`。引文（例如小紅書原帖）可以保留簡體，CARDZ 自己寫嘅字唔准。

---

## 文案（模板，腳本出）

X / Threads = **TCG Top 100**。圖 slider Top 40，行內 `#N` 永遠係該榜 Top 100 排名。

```
TCG Top 100 · 7D

🟢 #75 噴火龍V …  +11.69%
🟢 #94 水箭龜ex …  +10.28%
🟢 #68 騎拉帝納VSTAR …  +9.12%

🔴 #29 超夢＆夢幻 …  −20.84%
🔴 #30 騎拉帝納 V …  −17.50%
🔴 #6 超級噴火龍 X ex …  −10.92%

https://cardzmarketcap.com
```

要：排名、短名、**卡編號**（collector number；冇套名都要有，唔好淨係「魯夫」）、升跌 %、🟢／🔴、最多升 3／跌 3、最後一條 link。

英文唔用 PSA 全串 `officialName`（年份＋set＋編號），剝主體（同站 `cardSubject`）。

**字數（hard，腳本 `assert_text` 超就停）：**

| 渠道 | 上限 | 點數 |
|---|---|---|
| x.com-en / x.com-zh | **280 加權** | [X counting](https://docs.x.com/fundamentals/counting-characters)：Latin=1、CJK／emoji=2、URL 一律 23。未核對 Premium 就當 280，唔當 25k |
| threads-en / threads-zh | **500 字** | 每則 post |

塞唔入 3+3 就自動減 2+2 → 1+1，再塞唔入就 `PromoError`，唔准截一半名出街。

唔要：評論、解釋、6M 圖、手打排名、本機 URL、超字數硬貼。

---

## 新鮮度閘（hard）

Live payload 舊過 `PROMO_MAX_LIVE_LAG_HOURS`（env，預設 **26** 個鐘）就**唔准砌 pack**：
`brief_from_payload` / `build_live_brief` 會 raise `PromoStaleLive`，CLI **exit 3**。

- 26h（唔係 24h）留位畀遲咗嘅 bake，但唔會開多成日。
- `generatedAt` 冇／解唔到 = **當舊**（fail-closed），唔會當新鮮。
- 真係要出舊 board 先加 `--allow-stale`；brief 入面會有 `allowStale: true`、`lagHours`、`maxLagHours`。

出面講「今日最大升跌」但個 board 係前日，就係講大話。所以呢個閘擋喺砌 pack 嗰步，唔係擋喺出帖嗰步。

---

## Receipt（每次砌／每次出，dry-run 都有）

位置：`data/runtime/promo/receipts/<business_date>_<destination>_<utc-ts>.json`
（`PROMO_RUNTIME_DIR` env 可以搬走成個 `data/runtime/promo`。）

| 欄 | 意思 |
|---|---|
| `business_date` | JST 日（`YYYY-MM-DD`） |
| `destination` | channel key，例如 `x.com-en` |
| `dry_run` | 冇 `--confirm` 就係 `true` |
| `fill_only` | 有冇用 `--fill-only` 掂過 composer |
| `posted` | 真係撳咗／send 咗先係 `true` |
| `text_sha256` | 出嗰段字嘅 sha256 |
| `live_generated_at` | live `generatedAt` |
| `lag_hours` | 出嗰刻 live 舊咗幾多個鐘 |
| `outcome` | `built` / `dry_run` / `filled` / `posted` / `audience_mismatch` / `error` |
| `error` | 冇錯就 `null` |

pack 自己嗰個 `receipt.json`（heatmap 斷點／`errors[]`）照舊，兩者唔同嘢。

---

## Threads audience read-back（hard）

真發（`--confirm`）之後即刻讀返嗰篇帖嘅 audience／社羣 label，同硬常數
`THREADS_COMMUNITY = "CARDZGAME"` 對。呢個唔准由任何檔覆蓋（AGENTS.md 16）。唔對就：

- `outcome = "audience_mismatch"`，寫入 receipt
- stderr 印 `PROMO_POST_AUDIENCE_MISMATCH`
- **exit 4**（唔准 exit 0 扮成功）

讀唔到 label 一樣當 mismatch —— 未證實 = 未過。

---

## 17:45 JST 排程（砌 pack，**唔會 post**）

Windows installer 註冊 `\CARDZ-Promo-After-Publish`，每日 **17:45 JST**，經
`scripts/cardz_silent_run.vbs` 無視窗跑：

```text
wsl.exe -d Ubuntu -- python3 -X utf8 <repo>/scripts/promo_after_publish.py
```

`scripts/promo_after_publish.py` 係唯讀 consumer：讀 live published snapshot →
行新鮮度閘 → 逐個 `scripts/promo_destinations.json` 嘅 destination 出文案 →
寫 pack + receipt 落 `data/runtime/promo/<generation>/`，再原子寫當日排程結果落
`data/runtime/promo/scheduler/<business_date>.json`。呢份排程收據入面嘅 `exitCode`
係跨 VBS／WSL 邊界嘅權威結果；Task Scheduler 外層 result 唔可以單獨當成功證據。
冇 `promo_destinations.json`
就 fallback 去 `promo_destinations.example.json`（stderr 有 `PROMO_PACK_WARN`）。

佢**唔 import `promo_post`／playwright／websocket**，開唔到瀏覽器（`test_promo_pack.py`
會 assert 跑完之後 `sys.modules` 冇呢啲）。

| 輸出 | exit |
|---|---|
| `PROMO_PACK_OK <business_date> destinations=<n> lag_h=<x> receipt=<path>` | 0 |
| `PROMO_PACK_STALE …` | 3 |
| `PROMO_PACK_ERROR …` | 2 |

點解要有：宣傳鏈成日冇人跑，閘同文案就靜靜爛咗都冇人知。每日砌一次 pack ＝ 每日
證明條鏈仲行得，而**完全唔會出帖**。

---

## 命令

```text
python -X utf8 scripts/promo_chain.py brief
python -X utf8 scripts/promo_chain.py brief --allow-stale          # 過新鮮度閘（exit 3 果個）
python -X utf8 scripts/promo_chain.py heatmap --scope pokemon --lang zh-TW --format post --updown green-up
python -X utf8 scripts/promo_chain.py assert data/runtime/promo/<generation>
python -X utf8 scripts/promo_chain.py status data/runtime/promo/<generation>
python -X utf8 scripts/promo_after_publish.py                      # 排程用；砌 pack，唔 post
python -X utf8 scripts/promo_post.py plan --pack data/runtime/promo/<generation>
python -X utf8 scripts/promo_post.py compose --channel x.com-en --pack data/runtime/promo/<generation>
python -X utf8 scripts/promo_post.py compose --channel x.com-en --pack data/runtime/promo/<generation> --fill-only
python -X utf8 scripts/test_promo_pack.py
python -X utf8 scripts/test_promo_post.py
```

`brief` 寫 `post=false`。`promo_post.py compose` 三個模式：

| 模式 | 掂唔掂瀏覽器 | 出唔出街 |
|---|---|---|
| 預設（dry-run） | **零** CDP／websocket／network，淨係寫 receipt | 否 |
| `--fill-only` | 重用 9222 個 tab、填 composer + 貼圖 | 否（**唔撳 Post**） |
| `--confirm` | X／IG／Threads 立即 fail-closed | 否；公開出帖只屬 Hermes 四步鏈 |

`--cdp` 可以換 endpoint，預設仍然係 `http://127.0.0.1:9222`（9333 係 PriceCharting，唔關事）。
`--confirm` 同 `--fill-only` 唔可以一齊用。

出帖腳本（2026-08-21 建）：

| 渠道 | 點發 | 未齊就停 |
|---|---|---|
| X.com EN／ZH | Hermes `cardz_marketcap_x_post.py`；9222；先核對目標帳 | 9222 熄／未登入／錯帳 |
| Threads EN／ZH | Hermes `cardz_marketcap_meta_post.py --platform threads`；9222；先核對 handle | 9222 熄／錯帳／mapping 缺失 |
| Facebook | 唔做 | — |

Repo pack destination contract 見 [`scripts/promo_destinations.example.json`](../scripts/promo_destinations.example.json)；日更 completion gate 只計 X／Threads 英中四步，Instagram／WhatsApp／fork／site 暫停。Hermes 官方 job `bc4615fdd701`（歷史名稱 `cardz-mc-6acct-12jst-daily`）每 15 分鐘於 12–20 JST 觸發；保留 job ID，不因名稱含 6 而重建或恢復 Instagram。

---

## 斷線／斷點（promo pack）

同一 `generation` 資料夾：

- 熱力圖 JPEG 已喺碟、≥8KB、magic 啱、冇洩漏 → **skip GET**（唔由零再下載）
- GET 失敗 → 最多試 **3** 次，再寫入 `receipt.json` `errors[]`，其餘 key 繼續
- 再跑 `brief` 只補未齊嗰幾張

自動更新鏈（`daily_chain_v2`）另有 MySQL checkpoint／lease，**唔係**呢條宣傳鏈。

## 實戰前點睇錯（唔係 24h AI 掛住）

腳本未出過街，錯一定有。正確圈：

1. 連續幾日只跑 `brief` + `assert`（`post=false`），**唔 post**
2. 錯全部入 `receipt.json` `errors`
3. 你叫「睇呢幾日 receipt」→ AI **一次過**分類形狀、改腳本、種 bug 證明閘會紅
4. 唔好每晚開 9222 試發；唔好 LLM 坐通宵睇 log（貴、擾人瞓）

AI 嘅位：讀 receipt 修腳本。公開推送只由 Hermes deterministic no-agent job 執行。
