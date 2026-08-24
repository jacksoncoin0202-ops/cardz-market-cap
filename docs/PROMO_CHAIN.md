# CardZ Marketcap 宣傳鏈

> **冇任何嘢會自動出帖。** 全條鏈只會**砌 pack、寫 receipt**。出街係人手一句
> `promo_post.py compose --confirm`，冇第二條路：`live.confirmed` 唔會 post、
> 17:45 嗰個排程 task 唔會 post、dry-run 連瀏覽器都唔會掂。

**唔係自動更新鏈。** `live.confirmed` **唔准即刻 post**。Daddy 2026-08-21：Live bake 成功之後 **再等 30 分鐘** 先跑宣傳 `brief`（圖／文／閘）。出 X／Threads／WhatsApp 仍然要 9222／固定群名，未齊就停喺 pack。

每日公開流程：

1. 自動更新鏈驗證並推咗上 Live（等 30 分鐘）
2. HTTP 拎熱力圖：`GET /api/og/heatmap?period=7d&show=40&scope=all&format=post&theme=dark&updown=green-up&lang=en`（對 generation header；**唔開 browser、唔截 :3900**）。`scope` = all / pokemon / one-piece；`format` = post（4:5 直）/ status（9:16）/ wide 或 landscape（橫）；`updown=red-up` 顏色反轉；`lang` = en / zh-TW / zh-CN。
3. 腳本出 TCG Top 100 最大升 3／最大跌 3（圖可以係 Top 40，**數字永遠講 Top 100 排名**）
4. 文案係模板，唔寫評論；結尾一條 https://cardzmarketcap.com
5. 分語文推 X / Threads / WhatsApp（圖+字）

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
| WhatsApp 群 | Hermes `send --to whatsapp:#PTCG`（Pokémon 圖）／`whatsapp:#Yaichi x Cardz.Game TCG 社區｜4號群`（TCG 圖）／`whatsapp:#海賊王`（海賊王圖） + `MEDIA:<jpg>` | 固定表 `CHANNEL_HERMES_NAME`，同 `CHANNEL_BOARD` 對齊。唔 `send --list`、唔模糊、唔 AI | 係 |
| X EN / X 中文 | 9222 重用 x.com tab | Hermes **冇** X token | 要頭 |
| Threads EN / 中文 | 9222 重用 threads.net tab | Hermes **冇** Threads token | 要頭 |

腳本一次過做得到、唔好再用 AI 估：排行、generation、period、文案模板、洩漏掃、簡繁閘、9222 揀 tab。

**唔好寫評論。** 一講嘢，人就當你係真人嚟針對。

---

## 9222 tab 紀律（hard）

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

宣傳帳喺用戶 Chrome（9222），唔係 9333（9333 係 PriceCharting）。

1. 重用嗰個站已開嘅 tab
2. 已經登入目標帳 → 繼續
3. 登入頁有**已儲存帳號／一鍵撳入** → 先撳，唔好打字
4. 預設帳唔啱 → Hermes 1Password（`OP_SERVICE_ACCOUNT_TOKEN` 已喺 Hermes env）攞正確 item，再填。密碼唔准印 log、唔准入 brief
5. 2FA／passkey → 停，交返 Daddy。唔好開新 tab 再試

### Threads 出帖（hard）

compose 有「post 去邊個社羣／主題」。**一定揀 CARDZGAME**，唔好留喺預設個人主 feed。EN／ZH 兩則都係。未見到 CARDZGAME 就停，唔好發。

---

## 語文（hard）

| 渠道 | 語文 |
|---|---|
| x.com 英文 | en |
| x.com 中文 | **簡體** |
| Threads 英文 | en |
| Threads 中文、WhatsApp PTCG、WhatsApp 海賊王、Fork zh、站內中文 | **繁體** |

簡體只准 `x.com-zh`。引文（例如小紅書原帖）可以保留簡體，CARDZ 自己寫嘅字唔准。

---

## 文案（模板，腳本出）

X / Threads = **TCG Top 100**。WhatsApp PTCG = 寶可夢 Top 100；WhatsApp 海賊王 = 海賊王 Top 100。圖 slider Top 40，行內 `#N` 永遠係該榜 Top 100 排名。

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
| WhatsApp 群 | **4096** | Cloud API 文字上限；普通聊天更高，fail-closed 用細嗰個 |

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
python -X utf8 scripts/promo_post.py compose --channel threads-zh --pack data/runtime/promo/<generation> --confirm
python -X utf8 scripts/test_promo_pack.py
python -X utf8 scripts/test_promo_post.py
```

`brief` 寫 `post=false`。`promo_post.py compose` 三個模式：

| 模式 | 掂唔掂瀏覽器 | 出唔出街 |
|---|---|---|
| 預設（dry-run） | **零** CDP／websocket／network，淨係寫 receipt | 否 |
| `--fill-only` | 重用 9222 個 tab、填 composer + 貼圖 | 否（**唔撳 Post**） |
| `--confirm` | 同上 | **係**，人手先准 |

`--cdp` 可以換 endpoint，預設仍然係 `http://127.0.0.1:9222`（9333 係 PriceCharting，唔關事）。
`--confirm` 同 `--fill-only` 唔可以一齊用。

出帖腳本（2026-08-21 建）：

| 渠道 | 點發 | 未齊就停 |
|---|---|---|
| X.com EN／ZH | 9222 同一 x.com tab，`tweetTextarea_0` + `fileInput` + `tweetButton` | 9222 熄／未登入 |
| Threads（Daddy 講 Flock＝Threads 社羣） | 9222 同一 threads.net tab，**一定揀 CARDZGAME** | 見唔到 CARDZGAME |
| WhatsApp | Hermes WS：`send --to whatsapp:#PTCG`（Pokémon）／`whatsapp:#Yaichi x Cardz.Game TCG 社區｜4號群`（TCG）／`whatsapp:#海賊王` + `MEDIA:<jpg>`（`CHANNEL_HERMES_NAME`） | Hermes WhatsApp directory 仍空／gateway 未 ready。**唔** list 估群 |
| Facebook | 唔做 | — |

`data/runtime/promo/destinations.json` 抄 [`scripts/promo_destinations.example.json`](../scripts/promo_destinations.example.json)。未有 Daddy `--confirm` 之前，中午只跑 `brief` + `plan`。

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

AI 嘅位：讀 receipt 修腳本。**唔係**每日創作文案、亦唔係自動推送。
