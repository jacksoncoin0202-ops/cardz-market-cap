# CARDZ Market Cap — Project Rules

寶可夢卡市價網站（Next.js，`apps/web`）。呢度嘅規則全部係 2026-07-23 retro 從真實犯錯總結，hard rules，唔好再犯。

## UI 文案與產品定位（high）
- 寫任何 user-facing 文案或狀態標籤：預設當 go-live 標準交付，**唔准自行加**「資料已逾時／累積中／預覽／測試／未驗證」類 disclaimer 或 banner（加過被用戶鬧「唔好再有呢啲嘢，你阻住我評估」）。數據不足用中性顯示（「—」或低調 dim），內部驗證狀態擺 console/report。想加 caveat 先問用戶。
- 產品 i18n 文案一律**書面中文**——對話可以廣東話，落代碼嘅 zh-TW/zh-CN copy 唔准有口語句式（嘅/咗/喺/唔係）。寫完 copy 自查一次先提交。
- 卡名/專有名詞中文化：只准用官方中文譯名或玩家社群共識俗名（例：梵高皮卡丘），**唔准逐詞直譯**（直譯出過「戴灰色毛氈帽的皮卡丘」，破壞專業定位）。無共識嘅卡保留英文名。批量譯名完成後將完整清單俾用戶過目先 ship，唔好淨報覆蓋率。

## 數據語義（high）
- 加任何升跌/delta 顯示前，先分類指標：**流量型**（價、成交額）先可以用 ±delta；**存量/累積型**（population、收錄數）只升唔跌，永遠唔准顯示負 delta 或跌箭嘴（被鬧過「PSA 10 嘅數量點會跌？啲卡唔會消失」）。冇對應窗口歷史數據就直話用戶，唔准攞另一個指標嘅 changePct 頂替。
- 數據源文檔講「有邊幾個廠商/實體」時，必須標明清單係邊個層面嘅事實（per-card API enum / 週報 / 網站界面）。發現官方界面同 API 有落差，開「已知落差」section 主動記低，唔好等用戶貼圖質問（GemRate 四廠 vs 五廠 TAG 事件）。

## 視覺標準（medium）
- 新頁面/改表格佈局：colgroup 各欄百分比加埋必須 = 100%；欄寬、行距、字級對照首頁現有標準。改完 screenshot 同首頁並排對照先話完成（出過加埋 110.5%、欄位肥過首頁）。
- 用戶可見控制元件（dropdown、menu、tooltip）：唔好用原生 `<select>`/OS 樣式交差，要自訂 component 配 theme tokens；「做完未」嘅標準包埋互動展開態。
- 詳情頁/新頁面遵守「數據密度優先」：hero 圖唔准佔超過首屏 1/3，騰出空間放數據。唔好照搬 showcase 網站嘅大圖排版。
- **裸卡圖一律圓角**（2026-07-24 用戶欽點規矩）：所有 `<img>` 卡圖（detail-art、preview-image、sheet-image、ranking-thumb、grader-card、heatmap tile-card）必須 `border-radius: var(--card-img-radius)`（9%，實卡卡角比例），目的係鏟走原始圖角落嘅白色邊位，全站整齊。新加卡圖位置必須照加。
- **白邊 QC 入庫管道**（2026-07-24 用戶規矩）：裸卡圖入 DB 前必須過 `trim_white_border`（喺 `pipelines/g10_public_snapshot.py`）。白邊帶判定 = 由邊向內掃連續近白帶（亮度 ≥220 兼 chroma ≤26——有色亮區如黃框、米白畫布係卡畫，唔算白邊），邊帶 ≥45% pixel 近白先裁，每邊最多 15%，淺過 2px 唔裁。全白卡面（Reshiram ex WHT）同斜放截圖留 ≤8px 殘留係物理下限，9% 圓角遮得住。QC 準則嘅 regression test 喺 `tests/test_white_border_trim.py`。

## CSS 陷阱（high，同一 bug class 犯過兩次）
- 改 CSS media query 後 reload computed style 唔變：**第一個假設永遠係「同檔案後面有同 specificity 嘅 base rule 冚咗」**——即刻 Grep 該 selector 全部出現位置檢查順序，唔好連續 reload 超過 2 次。修完順手 Grep 晒成個 stylesheet 有冇同類位一次過清。每個新 layout block 都要有 base CSS，唔好齋寫 media query。

## Mockup → App 移植（high）
- `docs/mockups`（或任何 prototype HTML）永遠只係 prototype。用戶話「套用／用呢款／跟呢版」＝ 整合入 `apps/web` 正式 app，唔係繼續改 mockup（搞錯過被 interrupt）。有歧義動手前一句確認。
- 移植已驗收 mockup：先列出具體視覺元素清單（框、色標、光暈、rank chip、排序演算法、badge），移植後逐項 browser 驗證＋截圖同 mockup 並排對比先話搞掂。未經用戶同意唔准更換佈局/排序演算法（整唔見過排名 badge、擅自換排序被彈「個排序明顯有問題」）。

## 代碼改動驗證（medium）
- 改動任何現有 `.py`（provider、pipeline、workflow）後：必須跑 repo 現有測試先可以報「搞掂」。冇測試覆蓋改動嘅 interface 要明講「未覆蓋，風險係 X」。
- 前端改動：tsc + 相關 vitest 過，加一張成功 screenshot（desktop 同 mobile viewport），先叫完成。
