#!/usr/bin/env node
/*
 * 分享目的地 → 尺寸（owner 2026-08-20：「彈個 button 出嚟問你想分享去邊：WhatsApp、
 * Threads、X.COM、IG 定係其他地方？因應分享去唔同地方，要配合返唔同嘅最佳 social
 * media size」）。純靜態，唔使 dev server，由 run_all_tests.py glob 入 npm test。
 *
 * 守嘅係「一錯就靜靜出錯尺寸、但三邊都照 200」嗰批：
 *  ① `SHARE_TARGETS` 七個目的地齊：**IG 自己一行 1:1**（owner 2026-08-22：「IG 原來
 *     係正方形出 POST」），Threads / X / WhatsApp / 其他四個 4:5，全屏面 `status`
 *     自己一行 9:16，`desktop` 1.91:1。
 *     ⚠️ 呢條就係整件事嘅重點：feed 對太直嘅圖**唔裁而係縮細**，邊個目的地寫錯咗
 *     9:16 就係張圖永遠得七八成闊，冇 error、冇 log、冇人發現。
 *     ⚠️ 亦都守住反方向：IG 改咗方之後，唔准順手將 Threads / X 一齊拉落 square ——
 *     嗰三家 4:5 係 owner 貼出去實測嘅結果，一齊改就係為咗表面整齊而令三張圖變差。
 *  ② `story` alias 仲係指住 `post`。派咗出去嘅舊 HTML 仲喺 CDN／用戶 tab 度，
 *     嗰粒係**通用**分享掣，指去 9:16 = ① 嗰個陷阱由舊 tab 直接中。
 *  ③ `status` 1080×1920 真身喺 `FORMAT_SIZES`，og route 有佢嘅 JPEG 質素。
 *  ④ 兩個面（熱力圖／卡片內頁）行同一個 `ShareMenu`，冇第二份 picker（AGENTS.md 規矩 13）。
 *  ⑤ i18n 四條新 key 五個語言齊，舊嗰批死 key 清得乾淨。
 *  ⑥ 熱力圖經 OG API 出固定比例，而 `exportHeatmap` 冇預設值 —— 有預設值就係
 *     「call 少個參數都行得，靜靜出咗上次嗰個比例」。
 */
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const failed = [];
const check = (label, condition, detail) => { if (!condition) failed.push(detail ? `${label}: ${detail}` : label); };
const read = (path) => readFileSync(join(ROOT, path), "utf8");

const DEST_REL = "apps/web/src/lib/share-destinations.ts";
const destinations = read(DEST_REL);
const shareCopy = read("apps/web/src/lib/share-copy.ts");
const shareMenu = read("apps/web/src/components/share-menu.tsx");
const heatmap = read("apps/web/src/components/heatmap.tsx");
const cardDetail = read("apps/web/src/components/card-detail.tsx");
const shareFetch = read("apps/web/src/lib/share-fetch.ts");
const ogRoute = read("apps/web/src/app/api/og/card/[id]/route.tsx");
const i18n = read("apps/web/src/lib/i18n.ts");
const css = read("apps/web/src/app/globals.css");

/* ① 目的地表。唔用 regex 逐條 assert 字面 —— 讀返出嚟砌返 object，改咗次序／加多個
   目的地都唔會假紅，但改錯咗比例就一定紅。 */
const targets = new Map();
for (const m of destinations.matchAll(/\{\s*id:\s*"([\w-]+)",\s*format:\s*"(\w+)",\s*ratio:\s*"([\d.:]+)"/g)) {
  targets.set(m[1], { format: m[2], ratio: m[3] });
}
check("SHARE_TARGETS 讀得返（改咗寫法就要更新呢個 test）", targets.size >= 7, `搵到 ${targets.size} 個`);

/* owner 2026-08-20 點名嗰四個平台 + 全屏面 + 兩個兜底，一個都唔准少 */
for (const id of ["instagram", "threads", "x", "whatsapp", "status", "other", "desktop"]) {
  check(`SHARE_TARGETS 有 ${id}`, targets.has(id));
}
/* ⚠️ IG 自己一行 1:1（owner 2026-08-22 更正）。呢度唔准變返 post，亦唔准變 9:16。 */
const instagram = targets.get("instagram");
check("instagram 出 1:1（owner 2026-08-22：IG 係正方形出 POST）",
  instagram?.format === "square" && instagram?.ratio === "1:1", JSON.stringify(instagram));

/* ⚠️ 淨返四個貼圖目的地仍然係 4:5。呢度唔准出現 9:16，**亦唔准出現 square** ——
   IG 改咗方之後最容易犯嘅錯就係「順手一齊改晒」。WhatsApp 都喺呢個 list：佢個對話／
   群組氣泡保持比例唔裁，4:5 佔到最高（owner 2026-08-20，e61c1aa9）。
   全屏 9:16 係下面 `status` 自己嗰行。 */
for (const id of ["threads", "x", "whatsapp", "other"]) {
  const target = targets.get(id);
  if (!target) continue;
  check(`${id} 出 4:5（feed／氣泡唔裁，太直只會縮細；IG 改方唔准拉埋佢）`,
    target.format === "post" && target.ratio === "4:5",
    JSON.stringify(target));
}
const status = targets.get("status");
check("status 出 9:16（全屏面自己一行，唔掛公司名）",
  status?.format === "status" && status?.ratio === "9:16", JSON.stringify(status));
const desktop = targets.get("desktop");
check("desktop 出固定 1.91:1 闊版", desktop?.format === "wide" && desktop?.ratio === "1.91:1", JSON.stringify(desktop));
const ratioMarkup = /className="share-menu-ratio">\{target\.ratio\}<\/span>/;
check("兩個面都顯示實際出圖比例", ratioMarkup.test(shareMenu));
/* 只改記憶體副本：種返「跟畫面」顯示，以上同一條 assertion 必須拒絕。 */
const wrongRatioMenu = shareMenu.replace("{target.ratio}</span>", "{copy.frame}</span>");
check("比例標 regression 真係會攔到跟畫面舊寫法",
  wrongRatioMenu !== shareMenu && !ratioMarkup.test(wrongRatioMenu));

/* 目的地表同 alias 表對得返 —— import-time guard 已經會炸，但 build 唔行到就冇人知 */
for (const [id, target] of targets) {
  check(`FORMAT_ALIASES.${id} 同選單一致`, new RegExp(`^\\s*"?${id}"?: "${target.format}",`, "m").test(destinations),
    `選單話 ${target.format}`);
}
check("import-time drift guard 仲喺度", /const drifted = GUARDED_TARGETS\.filter/.test(destinations));
/* ⚠️ guard 掃嘅一定要係**選單真正出嗰批**。2026-08-23 加咗 SHARE_EXTRA_TARGETS
   （IG 直向 3:4／橫向 16:9）之後，如果 guard 仲淨係掃 SHARE_TARGETS，新嗰兩行標錯
   比例／對錯 alias 一樣唔會炸 —— 即係「有 guard 但唔覆蓋新嘢」＝ 冇 guard。 */
check("drift guard 覆蓋埋選單額外嗰兩行",
  /const GUARDED_TARGETS: readonly ShareTarget\[\] = SHARE_MENU_TARGETS;/.test(destinations)
  && /const SHARE_MENU_TARGETS: readonly ShareTarget\[\] = \[\.\.\.SHARE_TARGETS, \.\.\.SHARE_EXTRA_TARGETS\]/.test(destinations));
check("import-time unknown-format guard 仲喺度", /const unknown = Object\.entries\(FORMAT_ALIASES\)/.test(destinations));

/* ② 舊 CDN tab 嗰粒通用分享掣：`story` 唔准變 9:16 */
check("story alias 仍然係 post（舊 tab 撳分享唔可以突然攞到 9:16）", /^\s*story: "post",/m.test(destinations));

/*
 * ②b ⚠️⚠️ **呢條係全個檔最貴嗰條。** `?format=whatsapp` 唯一嘅真實叫方係 HERMES 條
 * cron 鏈（唔喺呢個 repo），佢每日 download 呢張圖再 upload 去 WhatsApp **對話／群組**
 * —— owner 2026-08-20（e61c1aa9）：「WhatsApp 氣泡保持比例唔裁」= 4:5。
 *
 * 2026-08-20 加目的地選單嗰陣真係一度將佢改成 `status`（9:16）：條鏈個 URL 一個字
 * 都唔使改、照 200、`x-og-format` 照出「status」，即係當晚會靜靜 upload 咗一批高瘦圖，
 * 冇 error 冇 log 冇人發現，要等有人肉眼睇到張圖怪先知。選單想要全屏 9:16 就明寫
 * `?format=status`（自己一個 target id），唔准借公司名。
 */
check("whatsapp alias 仍然係 post（條 cron 鏈送對話／群組氣泡，唔係 Status）",
  /^\s*whatsapp: "post",/m.test(destinations), "改咗就等於靜靜換咗條鏈每日出嗰批圖");

/* ③ og route 認得 status */
/* ⚠️ 舊版對死成句字面，加一個新格式就假紅。而家讀返個 array 逐個對：加格式唔會紅，
   但**剷走**任何一個舊格式（＝ 靜靜將某個目的地跌返 wide）一定紅。 */
const formatList = (/export const SHARE_FORMATS = \[([^\]]*)\] as const;/.exec(destinations)?.[1] ?? "")
  .split(",").map((item) => item.trim().replace(/^"|"$/g, "")).filter(Boolean);
for (const format of ["wide", "square", "post", "status", "portrait", "widescreen"]) {
  check(`SHARE_FORMATS 有 ${format}`, formatList.includes(format), JSON.stringify(formatList));
}
check("SHARE_FORMATS 頭四個次序冇郁",
  formatList.slice(0, 4).join() === "wide,square,post,status", JSON.stringify(formatList));
check("share-destinations 有 square 1080×1080（IG）", /square: \{ width: 1080, height: 1080 \}/.test(destinations));
check("og route JPEG_QUALITY 有 square", /JPEG_QUALITY: Record<ShareFormat, number> = \{[^}]*square: \d+/.test(ogRoute));
/* ⚠️ square 高度得 1080（比 post 少 270px），卡圖上限一定要細過 post ——
   共用 560 個舞台就會爆。呢條係唯一擋得住「照抄 post 一行」嘅檢查。 */
const squareGeo = ogRoute.match(/^\s*square: \{ padX: (\d+), padY: (\d+), artStage: (\d+), chartHeight: (\d+), artMaxWidth: \S+ artMaxHeight: (\d+) \},/m);
check("TALL_GEO 有 square 而且卡圖上限細過 post 嘅 560",
  !!squareGeo && Number(squareGeo[5]) < 560 && Number(squareGeo[3]) < 620, squareGeo?.[0] ?? "搵唔到 TALL_GEO.square");
check("TEXT_ONLY_GEO 有 square（唔准跌落 wide 嗰套）", /^\s*square: \{ padX: 72, padY: 48,/m.test(ogRoute));
/* ⚠️ 尺寸 2026-08-20 起住喺 `FORMAT_SIZES`（route.tsx 讀返佢）——見下面 ⑦a 攞真值再對。 */
check("share-destinations 有 status 1080×1920", /status: \{ width: 1080, height: 1920 \}/.test(destinations));
check("og route JPEG_QUALITY 有 status", /JPEG_QUALITY: Record<ShareFormat, number> = \{[^}]*status: \d+/.test(ogRoute));
check("TALL_GEO 直度 format 各自幾何（type 由 TallShareFormat 出）",
  /const TALL_GEO: Record<\s*TallShareFormat/.test(ogRoute)
  && /export type TallShareFormat = Exclude<ShareFormat, WideShareFormat>;/.test(destinations));
check("status 避開 story 平台 UI（上下 250 安全區）", /status: \{ padX: 56, padY: 250,/.test(ogRoute));
check("TEXT_ONLY_GEO 有 status（唔准跌落 wide 嗰套）", /^\s*status: \{ padX: 72, padY: 250,/m.test(ogRoute));
/* ⚠️ 卡圖母版得 429×600：status 高咗唔准順手谷大卡圖，一谷就係放大糊咗 */
check("status 冇谷大卡圖（POST_ART_MAX_* 冇郁）", /const POST_ART_MAX_WIDTH = 452;/.test(ogRoute) && /const POST_ART_MAX_HEIGHT = 560;/.test(ogRoute));
/* 三處 format 分支一律問「係咪 wide」——問「係咪 post」嘅話 status 會靜靜跌落 wide 嗰套幾何。
   剝走註釋先掃：上面 TEXT_ONLY_GEO 個註**特登**引住呢句錯寫法做反面教材。 */
const ogCode = ogRoute.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
check("og route 冇剩返 `format === \"post\"` 分支（status 會跌落 wide）", !/format === "post"/.test(ogCode),
  "改成 format === \"wide\" ? wide : 直度版");

/* ④ 一個 picker，兩個面共用 */
check("ShareMenu 由 SHARE_MENU_TARGETS 出（一張表，component 唔准自己砌）",
  /SHARE_MENU_TARGETS\.map\(/.test(shareMenu));
check("熱力圖用 ShareMenu", /<ShareMenu/.test(heatmap));
check("卡片內頁用 ShareMenu", /<ShareMenu/.test(cardDetail));
check("熱力圖冇剩返舊 picker markup", !/heatmap-share-menu|heatmap-export-post|heatmap-export-wa/.test(heatmap));
check("熱力圖冇剩返 shareAspect state", !/setShareAspectState|cardz-heatmap-share-aspect/.test(heatmap));
/* 目的地→比例只准有一張表：兩個 component 都唔准自己寫死平台名對比例 */
for (const [name, src] of [["heatmap.tsx", heatmap], ["card-detail.tsx", cardDetail], ["share-menu.tsx", shareMenu]]) {
  const bare = src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
  check(`${name} 冇自己寫死比例字串`, !/"4:5"|"9:16"|"16:9"|"1\.91:1"/.test(bare),
    "比例只准住喺 lib/share-destinations.ts");
}
/* ⚠️ 分享圖 fetch 一定要有 AbortSignal：冇 timeout 嘅 fetch 吊死 = 個掣永遠 busy。
   2026-08-22：兩個面共用 `lib/share-fetch.ts`（規矩 13）。卡片內頁本來自己寫住個
   20 秒死 timeout —— 開咗 4K（實測 100–126 秒）之後嗰個數就係「揀 4K 一定 fail」。
   所以呢條由「卡片內頁有 timeout」變成「卡片內頁唔准自己再寫一份」。 */
check("share fetch 有 timeout（住喺共用 lib）", /AbortSignal\.timeout\(per\)/.test(shareFetch));
check("卡片內頁行共用 fetchShareBlob，冇自己再寫一份",
  /fetchShareBlob\(path, res, "card OG"\)/.test(cardDetail)
  && !/SHARE_FETCH_TIMEOUT_MS/.test(cardDetail)
  && !/AbortSignal\.timeout/.test(cardDetail));
check("ShareMenu 有死鎖閘（PICK_TIMEOUT_MS）", /const PICK_TIMEOUT_MS = /.test(shareMenu) && /withTimeout\(Promise\.resolve\(onPick/.test(shareMenu));
/* 品牌名唔准入 i18n（專有名詞，五個語言一樣） */
check("品牌名寫死喺 share-menu（唔入 i18n）", /const BRAND_NAME/.test(shareMenu));
/* ⚠️ 貼邊反轉：熱力圖喺手機個掣排喺**左**手邊，塊板照右貼就飛咗出畫面左邊，
   個名同比例齊齊斬走（2026-08-20 390px 截圖影到）。TS 度位、CSS 出左貼那版，
   兩邊少一邊都係靜靜返去斬走嗰個樣。 */
check("ShareMenu 開嗰下度返貼邊", /setAlign\(trigger\.getBoundingClientRect\(\)\.right - panel\.offsetWidth < EDGE_GUTTER/.test(shareMenu));
check("panel 出 data-align", /data-align=\{align\}/.test(shareMenu));
check("globals.css 有左貼那版", /\.share-menu-panel\[data-align="left"\]\s*\{[^}]*left: 0;/.test(css));
/*
 * ⚠️ 退場動畫係 specificity 陷阱，唔係手民之誤：`.select-menu-exit`（0,1,0）喺呢個檔
 * **前面**，`.share-menu-panel { animation: menu-in }`（一樣 0,1,0）喺後面 —— 打和睇
 * source order，後嗰條贏，所以淨係加個 exit class 係改唔到 animation-name，塊板會硬
 * 企成 120ms 再一格閃走。要明寫 `.share-menu-panel.select-menu-exit`（0,1,1）先贏得返。
 * 同一個道理，reduced-motion 個 `animation: none` 都要一齊寫上呢條 0,1,1 selector，
 * 唔係就變咗「淨係 reduced-motion 用家仲有動畫」。
 */
check("退場動畫明寫（唔靠 .select-menu-exit 單獨贏）",
  /\.share-menu-panel\.select-menu-exit\s*\{\s*animation: menu-out/.test(css));
check("reduced-motion 蓋得住嗰條 0,1,1",
  /\.share-menu-panel, \.share-menu-panel\.select-menu-exit \{ animation: none; \}/.test(css));
/*
 * ⚠️ 分享圖 warm cache 一定要連語言做 key：換語言係 query-only soft navigation，
 * component 唔 remount，個 ref 原封不動 —— 淨係 key format 就會攞返上一個語言嗰張圖。
 */
/* 2026-08-22 再加清晰度：1080p warm 完再揀 4K，key 唔連 res 就攞返 1080p 嗰張，
   檔名同用戶睇到嘅級數講住 4K。冇 error 冇 log。 */
check("warm cache key 連埋語言同清晰度", /const key = `\$\{format\}\|\$\{res\}\|\$\{imageLang\}`;/.test(cardDetail)
  && /shareBlobs\.current\.get\(`\$\{target\.format\}\|\$\{shareRes\}\|\$\{imageLang\}`\)/.test(cardDetail));

/* ⑤ i18n：四條新 key × 5 語言，舊 key 清乾淨 */
const localeText = i18n.slice(i18n.indexOf("export const copy"));
for (const key of ["shareTo", "shareToStatus", "shareToOther", "shareToDesktop",
  "shareToPortrait", "shareToWidescreen"]) {
  check(`五個語言都有 ${key}`, (localeText.match(new RegExp(`${key}:`, "g")) || []).length === 5,
    `${(localeText.match(new RegExp(`${key}:`, "g")) || []).length} 個`);
}
for (const dead of ["shareImagePost", "shareImageWa", "shareShape"]) {
  check(`死 key ${dead} 清乾淨`, !i18n.includes(dead));
}

/* ⑥ 熱力圖分享走 OG download API，唔再喺 client canvas 截圖 */
check("heatmap 分享 GET /api/og/heatmap", /heatmapOgPath\(/.test(heatmap)
  && /fetchShareBlob\(path, res, "heatmap OG"\)/.test(heatmap) && /fetch\(path/.test(shareFetch));
/* 2026-08-21：timeout 由寫死 45 秒改成跟清晰度（4K 實測 50–57 秒，45 秒會次次自斬）。
   同日再收窄：真站量到 4K 一定俾 gateway 喺 60 秒斬，所以除咗**單次** timeout，
   仲要有**成個 retry 迴圈**嘅預算。兩個數都要喺度 —— 淨得一個嗰種寫法就係
   「等一次然後放棄」或者「一次過等十分鐘」，兩樣都錯。呢條比原本嚴，冇放鬆。 */
check("分享 timeout 跟清晰度", /AbortSignal\.timeout\(per\)/.test(shareFetch)
  && /RESOLUTION_TIMEOUT_MS\[res\]/.test(shareFetch)
  && /RESOLUTION_RETRY_BUDGET_MS\[res\]/.test(shareFetch));
check("heatmap warm cache key 連語言 period scope updown res",
  /`\$\{format\}\|\$\{imageLang\}\|\$\{activePeriod\}\|\$\{visibleCount\}\|\$\{scope\}\|\$\{ogTheme\}\|\$\{upDown\}\|\$\{res\}`/.test(heatmap));
check("exportHeatmap 冇預設 target", /const exportHeatmap = useCallback\(async \(target: ShareTarget\) =>/.test(heatmap));
check("heatmap onWarm 預先 fetch", /onWarm=\{\(target\) => warmShareImage\(target\.format\)\}/.test(heatmap));
check("filename 比例喺 heatmap-og", /9x16/.test(read("apps/web/src/lib/heatmap-og.ts")));


/* ═════════════════════════════════════════════════════════════════════════
 * ⑦ 2026-08-20 上街之後嘅紅隊審計捉返嚟嗰批。每條都係「照出 200、冇 error、
 *    冇 log」嗰種靜默錯，所以一定要有 test，唔可以改完就算。
 * ═════════════════════════════════════════════════════════════════════════ */
const { FORMAT_SIZES, SHARE_MENU_TARGETS: RUNTIME_TARGETS, SHARE_FORMATS: RUNTIME_FORMATS,
  FORMAT_QUERY_NAME, readShareFormat: runtimeReadFormat, isWideFormat, shareDestinations } =
  await import(pathToFileURL(join(ROOT, DEST_REL)).href);

/* ⑦a 尺寸真身得一份：route.tsx 唔准再自己寫多組（AGENTS.md 規矩 13） */
check("FORMAT_SIZES wide = 1200×630",
  FORMAT_SIZES.wide.width === 1200 && FORMAT_SIZES.wide.height === 630, JSON.stringify(FORMAT_SIZES.wide));
check("FORMAT_SIZES post = 1080×1350",
  FORMAT_SIZES.post.width === 1080 && FORMAT_SIZES.post.height === 1350, JSON.stringify(FORMAT_SIZES.post));
check("FORMAT_SIZES status = 1080×1920",
  FORMAT_SIZES.status.width === 1080 && FORMAT_SIZES.status.height === 1920, JSON.stringify(FORMAT_SIZES.status));
check("og route 讀 FORMAT_SIZES 唔自己開表",
  /import \{[^}]*\bFORMAT_SIZES\b[^}]*\breadShareFormat\b[^}]*\} from "@\/lib\/share-destinations"/.test(ogRoute)
  && /const spec = FORMAT_SIZES\[format\];/.test(ogRoute));

/*
 * ⑦b 比例標唔准講大話 —— 呢個就係 2026-08-20 出咗街嗰單：`desktop` 標「16:9」但
 * `wide` 真身 1200×630＝1.91:1，UI 向用戶報咗個假數字而三個 test 全綠。
 * 唔係比字串，係攞真數字計返出嚟對。
 */
for (const target of RUNTIME_TARGETS) {
  const [labelW, labelH] = target.ratio.split(":").map(Number);
  const { width, height } = FORMAT_SIZES[target.format];
  const real = width / height;
  const off = Math.abs(labelW / labelH - real) / real;
  check(`${target.id} 個比例標同真實尺寸夾得返`, off <= 0.01,
    `標 ${target.ratio} 但 ${target.format} 係 ${width}×${height}（差 ${(off * 100).toFixed(1)}%）`);
}
check("desktop 標 1.91:1（唔准寫返 16:9 —— 1200×630 唔係 16:9）",
  RUNTIME_TARGETS.find((t) => t.id === "desktop")?.ratio === "1.91:1");

/* ⑦c 第三個 guard 真係有牙：植返個假標籤，import 一定要炸（AGENTS.md 規矩 9） */
{
  const dir = mkdtempSync(join(tmpdir(), "cardz-share-ratio-guard-"));
  try {
    const mutated = destinations.replace('ratio: "1.91:1"', 'ratio: "16:9"');
    check("⑦c: 改得到 desktop 個標籤（改咗寫法就要更新呢個 test）", mutated !== destinations);
    const probe = join(dir, "share-destinations.ratio-probe.ts");
    writeFileSync(probe, mutated, "utf8");
    let message = "";
    try { await import(pathToFileURL(probe).href); } catch (error) { message = String(error?.message ?? error); }
    check("⑦c: 比例標講大話會即刻炸", message.includes("比例標同真實尺寸唔夾"), `掟嘅係：${message || "(乜都冇掟)"}`);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

/*
 * ⑦d prototype key 唔准打到 500。`FORMAT_ALIASES` 係普通 object literal，直接 index
 * `constructor` / `__proto__` 會攞到繼承嚟嘅 `Object`（truthy，`??` 接唔到手），route
 * 攞住個 undefined spec 就喺 request 度炸。2026-08-20 上街實測真係 HTTP 500。
 */
for (const bad of ["constructor", "__proto__", "CONSTRUCTOR", "prototype", "toString", "valueOf", "hasOwnProperty"]) {
  check(`⑦d: ?format=${bad} 跌返 wide 唔准 500`, runtimeReadFormat(bad) === "wide", `→ ${String(runtimeReadFormat(bad))}`);
}
check("⑦d: readShareFormat 行 Object.hasOwn", /Object\.hasOwn\(FORMAT_ALIASES, key\)/.test(destinations));
check("⑦d: readShareLang 一樣行 Object.hasOwn", /Object\.hasOwn\(LANG_ALIASES, key\)/.test(shareCopy));

/*
 * ⑦e 用戶喺 OS share sheet 撳取消 = `"dismissed"` = **乜都冇分享到**，唔准出綠剔
 * （個 aria-live 會即刻讀「圖片已匯出」俾讀屏用戶聽）。兩個叫方都要交返個 outcome。
 */
check("ShareMenu 認得 dismissed", /outcome === "dismissed"/.test(shareMenu) && /setState\("idle"\);/.test(shareMenu));
check("卡片內頁交返個 outcome", /return await shareImageBlob\(blob, \{/.test(cardDetail));
check("熱力圖交返個 outcome", /return outcome;/.test(heatmap));
/* 2026-08-21 加咗 retry 之後，「失敗」有兩條路，兩條都唔准靜靜返個壞 blob：
   ① 唔喺 retry 名單嗰啲 status（真係錯）→ 即刻掟；
   ② retry 預算用晒都仲未攞到 → 迴圈出到嚟嗰下要掟，唔准 `return undefined`。
   呢條由一個 needle 變兩個，收窄咗。 */
check("分享 OG 失敗唔准靜靜扮成功（要掟）",
  /throw new Error\(`\$\{label\} HTTP \$\{response\.status\}`\)/.test(shareFetch)
  && /throw new Error\(`\$\{label\} 攞唔到/.test(shareFetch));
/* `label` 淨係做錯誤訊息 —— 兩個面都要各自傳，否則報錯講唔出邊張圖死咗。 */
check("兩個面各自傳 label", /"heatmap OG"/.test(heatmap) && /"card OG"/.test(cardDetail));

/*
 * ⑦f busy 唔准用 DOM `disabled`：瀏覽器將一個正攞住 focus 嘅 button 設成 disabled
 * 會即刻 blur 佢，鍵盤／讀屏用戶分享完一次就跌返 <body>。
 */
check("trigger 用 aria-disabled 唔用 disabled",
  /aria-disabled=\{busy\}/.test(shareMenu) && !/[^-]disabled=\{busy\}/.test(shareMenu));
check("globals.css 補返 aria-disabled 個樣", /\.share-menu-trigger\[aria-disabled="true"\]/.test(css));

/* ⑦g 開住 menu 轉橫屏／拉窗口要重量貼邊，唔係塊板仲貼住舊嗰邊，凸出畫面外 */
check("貼邊量度抽咗做 measureAlign", /const measureAlign = useCallback/.test(shareMenu));
check("轉屏／resize 會重量",
  /addEventListener\("resize", measureAlign\)/.test(shareMenu)
  && /addEventListener\("orientationchange", measureAlign\)/.test(shareMenu));

/*
 * ⑦h 七行板要有內部 scroll：橫置手機 viewport 淨係 390 高、塊板 324 高，唔加閘就要
 * 成版 scroll 落去先撳到下面四個目的地。同 `.currency-menu`（31 隻貨幣）行同一套。
 */
/* 先剝走註釋再切：`.share-menu-panel {` 喺上面嗰段講特異性嘅註釋入面
   引多一次，`indexOf` 一切就切咗去註釋度，個 block 飄咗都睇唔出。 */
const cssBare = css.replace(/\/\*[\s\S]*?\*\//g, "");
const panelBlock = cssBare.slice(
  cssBare.indexOf(".share-menu-panel {"),
  cssBare.indexOf(".share-menu-panel[data-align="),
);
/* 切歪咗就乜都 test 唔到（空字串一樣全部 false）—— 所以要先驗返切中嘅真係塊板。 */
check("panel 個 slice 真係鎖到塊板（改咗 CSS 排位就要更新呢個 test）",
  panelBlock.length > 200 && panelBlock.length < 2000
  && panelBlock.includes("position: absolute;") && panelBlock.includes("animation: menu-in")
  // 剝乾淨之後全份都唔應該再有結尾符；仲有就代表起點跌咗入註釋。
  && !panelBlock.includes("*" + "/"),
  `slice 長度 ${panelBlock.length}`);
check("panel 有 max-height", /max-height: min\(420px, calc\(100svh/.test(panelBlock));
check("panel 有 overflow-y", /overflow-y: auto;/.test(panelBlock));
check("panel 有 overscroll-behavior", /overscroll-behavior: contain;/.test(panelBlock));

/* ═════════════════════════════════════════════════════════════════════════
 * ⑧ 2026-08-23 加咗兩個尺寸：`portrait` 1080×1440（IG feed／grid 3:4）同
 *    `widescreen` 1920×1080（16:9）。owner 貼圖嗰四個面（X／Threads／WhatsApp／IG）
 *    連埋呢兩個先算齊，冇人再需要手動 crop。
 *
 *    呢批 check 守嘅同樣係「照出 200、冇 error、冇 log」嗰種錯：
 *     · 新 format 冇 alias 叫得到 = 一堆死 code，冇 test 會紅
 *     · **`portrait` 呢個 alias 字係陷阱**：佢由第一日起就指住 4:5 嘅 `post`，
 *       而條 HERMES 自動鏈（唔喺呢個 repo）真係會寫 `?format=portrait`。新嗰個
 *       3:4 板要用 `3x4` / `ig-portrait` / `grid` 叫，**唔准搶**呢個字，
 *       一搶就係靜靜換咗條鏈每日出嗰批圖（同 ②b `whatsapp` 一模一樣嘅單）。
 *     · `widescreen` 係**橫**嘅但唔係 `wide` —— 分支寫 `format === "wide"` 就會
 *       攞住 1920×1080 個畫布行直度 layout，出到嚟一嚿嘢但照 200。
 *     · 檔名比例字如果由 `w/h` 估返出嚟，3:4 會估成 `4x5`、16:9 會估成 `wide`，
 *       兩張圖同一個檔名，直接互相覆蓋。
 * ═════════════════════════════════════════════════════════════════════════ */
const heatmapOgSrc = read("apps/web/src/lib/heatmap-og.ts");
const heatmapRoute = read("apps/web/src/app/api/og/heatmap/route.tsx");
/*
 * `heatmap-og.ts` / `share-resolution.ts` 個 import 冇副檔名（`./share-destinations`），
 * Node 直接 import 會 ERR_MODULE_NOT_FOUND。所以成條鏈抄入 tmpdir，淨係幫 specifier
 * 加返 `.ts` —— 同 `test-fe-heatmap-resolution.mjs` 一樣嘅做法。想種 bug 就交個
 * mutator 落對應嗰個檔，`entry` 決定 import 邊個。
 */
const LIB_CHAIN = ["share-destinations.ts", "share-resolution.ts", "share-copy.ts", "heatmap-og.ts"];
const probeDirs = [];
async function loadLibChain({ mutate = {}, entry = "heatmap-og.ts" } = {}) {
  const dir = mkdtempSync(join(tmpdir(), "cardz-share-format-"));
  probeDirs.push(dir);
  for (const file of LIB_CHAIN) {
    const src = (mutate[file] ?? ((x) => x))(read(`apps/web/src/lib/${file}`));
    writeFileSync(join(dir, file), src.replace(/from "(\.\/[\w-]+)"/g, 'from "$1.ts"'), "utf8");
  }
  try {
    return { mod: await import(pathToFileURL(join(dir, entry)).href), error: "" };
  } catch (error) {
    return { mod: null, error: String(error?.message ?? error) };
  }
}
const chain = await loadLibChain();
check("⑧: heatmap-og 條鏈 import 得（四個 import-time guard 對住真數據唔會炸）",
  !!chain.mod, chain.error);
const { FORMAT_RATIO_LABEL, heatmapOgFilename } = chain.mod ?? {};
const { RESOLUTION_SCALE } = (await loadLibChain({ entry: "share-resolution.ts" })).mod ?? {};
const shareDestinationList = shareDestinations();

/* ⑧a 尺寸真身。唔止對數字，仲要對返個比例 —— 1080×1440 打錯做 1080×1350 一樣係
   「有張圖出到」，但就同 post 一模一樣，加咗等於冇加。 */
check("⑧a: portrait = 1080×1440（IG feed／grid 3:4）",
  FORMAT_SIZES.portrait?.width === 1080 && FORMAT_SIZES.portrait?.height === 1440,
  JSON.stringify(FORMAT_SIZES.portrait));
check("⑧a: widescreen = 1920×1080（16:9）",
  FORMAT_SIZES.widescreen?.width === 1920 && FORMAT_SIZES.widescreen?.height === 1080,
  JSON.stringify(FORMAT_SIZES.widescreen));
check("⑧a: portrait 真係 3:4（唔係 4:5 嘅另一個名）",
  FORMAT_SIZES.portrait.width / FORMAT_SIZES.portrait.height === 3 / 4);
check("⑧a: widescreen 真係 16:9",
  FORMAT_SIZES.widescreen.width / FORMAT_SIZES.widescreen.height === 16 / 9);
/* 通用直度圖跟 FORMAT_SIZES.post 嘅 4:5 —— 3:4 = 0.75，**低過**
   條線：Threads／X 對呢個比例會縮細唔裁。所以佢淨係俾 IG feed／grid 用，
   唔准順手搶咗通用直度目的地（嗰批仲係 4:5）。 */
const MIN_ASPECT = FORMAT_SIZES.post.width / FORMAT_SIZES.post.height;
check("⑧a: 通用直度尺寸真係 4:5", MIN_ASPECT === 4 / 5, String(MIN_ASPECT));
check("⑧a: 3:4 低過通用直度比例，所以唔准搶通用直度目的地",
  FORMAT_SIZES.portrait.width / FORMAT_SIZES.portrait.height < MIN_ASPECT
  && targets.get("threads")?.format === "post" && targets.get("x")?.format === "post");

/* ⑧b alias：新名叫得到，舊名一個都唔准搬。 */
for (const alias of ["3x4", "ig-portrait", "grid"]) {
  check(`⑧b: ?format=${alias} → portrait`, runtimeReadFormat(alias) === "portrait", `→ ${runtimeReadFormat(alias)}`);
}
for (const alias of ["16x9", "widescreen", "hd", "youtube"]) {
  check(`⑧b: ?format=${alias} → widescreen`, runtimeReadFormat(alias) === "widescreen", `→ ${runtimeReadFormat(alias)}`);
}
check("⑧b: 大細楷都認", runtimeReadFormat("3X4") === "portrait" && runtimeReadFormat("YouTube") === "widescreen");
/*
 * ⚠️⚠️ **呢四條係全段最貴。** 加新尺寸最順手嘅寫法就係「`portrait` 應該指 3:4 先啱」
 * 同「`landscape` 應該指 16:9 先啱」—— 兩個都係派咗出街嘅 URL：
 *  · `?format=portrait` 條 HERMES 鏈日日照叫，改咗 = 靜靜由 1080×1350 變 1080×1440
 *  · `?format=landscape` 一路出 1200×630（og:image 1.91:1），改咗 = unfurl 卡片變咗
 * 兩邊都係照 200、`x-og-format` 照出、冇 error 冇 log。要 3:4 就明寫 `3x4`。
 */
check("⑧b: portrait alias 冇被搶（仲係 4:5 post —— HERMES 鏈日日叫緊）",
  runtimeReadFormat("portrait") === "post", `→ ${runtimeReadFormat("portrait")}`);
check("⑧b: landscape alias 冇被搶（仲係 1.91:1 wide —— og:image 靠佢）",
  runtimeReadFormat("landscape") === "wide", `→ ${runtimeReadFormat("landscape")}`);
check("⑧b: story 仲係 post", runtimeReadFormat("story") === "post");
check("⑧b: whatsapp 仲係 post", runtimeReadFormat("whatsapp") === "post");
check("⑧b: instagram 仲係 square", runtimeReadFormat("instagram") === "square");
/* 每個 format 都要至少一個 alias 叫得到（import-time guard 已經擋住，呢度 runtime
   再驗一次：guard 掟得出 error 但冇 call site 就等於冇 guard）。 */
for (const format of RUNTIME_FORMATS) {
  check(`⑧b: ${format} 有 alias 叫得到`,
    shareDestinationList.some((dest) => runtimeReadFormat(dest) === format), `冇任何 alias 對到 ${format}`);
}

/* ⑧c 橫定直：`widescreen` 係橫但唔係 `wide`，分支唔准再問 `=== "wide"`。 */
for (const format of RUNTIME_FORMATS) {
  const { width, height } = FORMAT_SIZES[format];
  check(`⑧c: isWideFormat(${format}) 同真實闊高夾得返`, isWideFormat(format) === (width > height),
    `isWideFormat=${isWideFormat(format)} 但 ${width}×${height}`);
}
check("⑧c: widescreen 算橫", isWideFormat("widescreen"));
check("⑧c: portrait 唔算橫", !isWideFormat("portrait"));
check("⑧c: og route 冇剩返 `format === \"wide\"` 分支（widescreen 會跌落直度幾何）",
  !/format === "wide"/.test(ogCode), "改成 isWideFormat(format)");

/* ⑧d 檔名比例字：唔准由 w/h 估，一估 3:4 就變 `4x5`、16:9 就變 `wide`，兩張圖撞名。 */
check("⑧d: portrait 個比例字係 3x4", FORMAT_RATIO_LABEL.portrait === "3x4", FORMAT_RATIO_LABEL.portrait);
check("⑧d: widescreen 個比例字係 16x9", FORMAT_RATIO_LABEL.widescreen === "16x9", FORMAT_RATIO_LABEL.widescreen);
check("⑧d: 六個比例字冇撞（撞就係兩張圖同一個檔名，直接覆蓋）",
  new Set(RUNTIME_FORMATS.map((f) => FORMAT_RATIO_LABEL[f])).size === RUNTIME_FORMATS.length,
  JSON.stringify(FORMAT_RATIO_LABEL));
check("⑧d: 舊比例字一個都冇郁（出咗街嘅檔名唔准改）",
  FORMAT_RATIO_LABEL.wide === "wide" && FORMAT_RATIO_LABEL.square === "1x1"
  && FORMAT_RATIO_LABEL.post === "4x5" && FORMAT_RATIO_LABEL.status === "9x16", JSON.stringify(FORMAT_RATIO_LABEL));
check("⑧d: heatmapOgFilename 出到新比例字",
  heatmapOgFilename({ format: "portrait" }).endsWith("-3x4")
  && heatmapOgFilename({ format: "widescreen" }).endsWith("-16x9"),
  `${heatmapOgFilename({ format: "portrait" })} / ${heatmapOgFilename({ format: "widescreen" })}`);
check("⑧d: 4K 檔名照樣加後綴（同一 folder 唔准互相覆蓋）",
  heatmapOgFilename({ format: "widescreen", res: "4k" }).endsWith("-16x9-4k"),
  heatmapOgFilename({ format: "widescreen", res: "4k" }));
check("⑧d: heatmapOgFilename 讀返張表，唔係自己由尺寸算",
  /const ratio = FORMAT_RATIO_LABEL\[format\];/.test(heatmapOgSrc));

/* ⑧e 4K：兩個新尺寸都要 ×2 出到真 4K（唔係得個名）。 */
for (const [format, want1080p, want4k] of [
  ["portrait", [1080, 1440], [2160, 2880]],
  ["widescreen", [1920, 1080], [3840, 2160]],
]) {
  for (const [res, want] of [["1080p", want1080p], ["4k", want4k]]) {
    const got = [
      Math.round(FORMAT_SIZES[format].width * RESOLUTION_SCALE[res]),
      Math.round(FORMAT_SIZES[format].height * RESOLUTION_SCALE[res]),
    ];
    check(`⑧e: ${format} ${res} = ${want[0]}×${want[1]}`, got[0] === want[0] && got[1] === want[1], got.join("×"));
  }
}

/* ⑧f 選單：兩行係**額外**加，owner 點名嗰七個一行都唔准郁。 */
check("⑧f: SHARE_TARGETS 仲係七行（新嘢入 SHARE_EXTRA_TARGETS）",
  /export const SHARE_TARGETS: readonly ShareTarget\[\] = \[[^\]]*\]/.test(destinations)
  && RUNTIME_TARGETS.length === 9, `選單 ${RUNTIME_TARGETS.length} 行`);
const igPortrait = RUNTIME_TARGETS.find((t) => t.id === "ig-portrait");
const widescreenTarget = RUNTIME_TARGETS.find((t) => t.id === "widescreen");
check("⑧f: 選單有 ig-portrait 3:4", igPortrait?.format === "portrait" && igPortrait?.ratio === "3:4",
  JSON.stringify(igPortrait));
check("⑧f: 選單有 widescreen 16:9", widescreenTarget?.format === "widescreen" && widescreenTarget?.ratio === "16:9",
  JSON.stringify(widescreenTarget));
check("⑧f: share-menu 兩行各有 glyph（冇 glyph 就兩行一模一樣，撳錯都唔知）",
  /id === "ig-portrait"/.test(shareMenu) && /id === "widescreen"/.test(shareMenu));
check("⑧f: 兩行個名行 i18n 唔係品牌名（唔准寫死中文入 component）",
  /"ig-portrait": "portrait"/.test(shareMenu) && /widescreen: "widescreen"/.test(shareMenu));
/* 五個語言喺上面 ⑤ 已經數過，呢度釘死 owner 點名嗰三隻嘅字面 */
for (const want of ["Instagram portrait 3:4", "Widescreen 16:9", "IG 直向 3:4", "橫向 16:9", "IG 竖版 3:4"]) {
  check(`⑧f: i18n 有「${want}」`, i18n.includes(want));
}
check("⑧f: 兩個面都傳埋新 copy", /portrait: t\.labels\.shareToPortrait/.test(heatmap)
  && /widescreen: t\.labels\.shareToWidescreen/.test(heatmap)
  && /portrait: t\.labels\.shareToPortrait/.test(cardDetail)
  && /widescreen: t\.labels\.shareToWidescreen/.test(cardDetail));

/* ⑧g 卡片 OG route 要真係識畫呢兩個尺寸 —— 唔係就攞住個新 spec 跌返舊幾何。 */
check("⑧g: JPEG_QUALITY 有兩個新 format",
  /JPEG_QUALITY: Record<ShareFormat, number> = \{[^}]*portrait: \d+[^}]*widescreen: \d+/.test(ogRoute));
check("⑧g: FORMAT_THEMES 有兩個新 format",
  /portrait: "(dark|light)",/.test(ogRoute) && /widescreen: "(dark|light)",/.test(ogRoute));
check("⑧g: TALL_GEO 有 portrait（1440 高，唔准照抄 post 個 1350 幾何）",
  /^\s*portrait: \{ padX: \d+, padY: \d+, artStage:/m.test(ogRoute));
check("⑧g: TEXT_ONLY_GEO 兩個新 format 都有（唔准跌落 wide 嗰套）",
  /^\s*portrait: \{ padX: \d+, padY: \d+, gapTop:/m.test(ogRoute)
  && /^\s*widescreen: \{ padX: \d+, padY: \d+, gapTop:/m.test(ogRoute));
/* ⚠️ 畫布同版面係兩件事：畫布跟清晰度（`resScale`），版面仲要乘個 format 幾何倍數。
   1920 闊嘅畫布照 1200 闊嘅版面畫 = 個圖細細粒黏喺左上角，照 200。 */
check("⑧g: 畫布跟清晰度、版面跟 FORMAT_LAYOUT_SCALE（兩件事唔准撈埋）",
  /const scale = resScale \* FORMAT_LAYOUT_SCALE\[format\];/.test(ogRoute)
  && /width: S\(spec\.width, resScale\),/.test(ogRoute)
  && /height: S\(spec\.height, resScale\),/.test(ogRoute));
check("⑧g: 舊四個 format 版面倍數釘死 1（新嘢唔准郁到出咗街嗰批）",
  /wide: 1,\s*square: 1,\s*post: 1,\s*status: 1,\s*portrait: 1,/.test(ogRoute.replace(/\/\*[\s\S]*?\*\//g, "")));

/* ⑧h 熱力圖 OG route：外框（標題／圖例／stamp）要跟畫布闊度放大，唔係 1920 嗰張
   個 header 細到睇唔到。舊四個一定要釘死 1，否則今日出咗街嗰批圖會靜靜郁咗。 */
check("⑧h: heatmap route 有 CHROME_SCALE 而且舊四個釘死 1",
  /CHROME_SCALE: Record<ShareFormat, number> = \{\s*wide: 1,\s*square: 1,\s*post: 1,\s*status: 1,\s*portrait: 1,/.test(heatmapRoute));
check("⑧h: widescreen 個外框倍數由 FORMAT_SIZES 算，唔係手寫死",
  /widescreen: FORMAT_SIZES\.widescreen\.width \/ CHROME_BASE_WIDTH,/.test(heatmapRoute));
check("⑧h: 外框用 chrome，唔係用返 scale", /const chrome = scale \* CHROME_SCALE\[format\];/.test(heatmapRoute));
/* 熱力圖冇 per-format 版面分支 —— 塊板行 squarified treemap，自己會按闊高排。
   一有 `format === "..."` 分支就係「加多個尺寸要再抄一次版面」。 */
check("⑧h: heatmap route 冇 per-format 版面分支",
  !/format === "(post|square|status|portrait|widescreen)"/.test(heatmapRoute.replace(/\/\*[\s\S]*?\*\//g, "")));

/* ⑧i 兩個新 guard 真係有牙（AGENTS.md 規矩 9：有檢查但冇 call site = 冇檢查）。
   種返個 bug 落去，import 一定要炸。 */
{
  {
    /* ⑧i-1 橫直分類講大話：話 portrait 係橫嘅（但佢 1080×1440）。 */
    const anchor = 'export const WIDE_FORMATS = ["wide", "widescreen"] as const satisfies readonly ShareFormat[];';
    check("⑧i-1: 錨點仲喺度（改咗寫法就要更新呢個 test）", destinations.split(anchor).length - 1 === 1);
    const { error: message1 } = await loadLibChain({
      entry: "share-destinations.ts",
      mutate: {
        "share-destinations.ts": (src) => src.replace(anchor,
          'export const WIDE_FORMATS = ["wide", "widescreen", "portrait"] as const satisfies readonly ShareFormat[];'),
      },
    });
    check("⑧i-1: 橫直分類同真實闊高唔夾會即刻炸", message1.includes("WIDE_FORMATS 同真實闊高唔夾"),
      `掟嘅係：${message1 || "(乜都冇掟)"}`);

    /* ⑧i-2 新 format 冇 alias 叫得到（＝ 死 code）都要炸。 */
    const aliasAnchor = '  "3x4": "portrait",\n';
    check("⑧i-2: 錨點仲喺度（改咗寫法就要更新呢個 test）", destinations.split(aliasAnchor).length - 1 === 1);
    const { error: message2 } = await loadLibChain({
      entry: "share-destinations.ts",
      mutate: {
        "share-destinations.ts": (src) => src
          .replace(aliasAnchor, "")
          .replace('  "ig-portrait": "portrait",\n', "")
          .replace('  grid: "portrait",\n', ""),
      },
    });
    check("⑧i-2: 冇 alias 叫得到嘅 format 會即刻炸", message2.includes("冇任何 alias 叫得到"),
      `掟嘅係：${message2 || "(乜都冇掟)"}`);

    /* ⑧i-3 檔名比例字講大話（`portrait` 標返 `4x5`）都要炸。 */
    const labelAnchor = '  portrait: "3x4",\n';
    check("⑧i-3: 錨點仲喺度（改咗寫法就要更新呢個 test）", heatmapOgSrc.split(labelAnchor).length - 1 === 1);
    const { error: message3 } = await loadLibChain({
      mutate: { "heatmap-og.ts": (src) => src.replace(labelAnchor, '  portrait: "4x5",\n') },
    });
    check("⑧i-3: 檔名比例標講大話會即刻炸", message3.includes("檔名比例標同真實尺寸唔夾"),
      `掟嘅係：${message3 || "(乜都冇掟)"}`);
  }
  for (const dir of probeDirs) rmSync(dir, { recursive: true, force: true });
}

/*
 * ⑧j format 個名 ≠ 佢喺 URL 度點叫。
 *
 * ⚠️ 呢條係上面 ⑧b 嗰個陷阱嘅另一半：`portrait`（format 名）＝ 3:4，但
 * `?format=portrait`（alias）＝ 4:5 post。任何叫方由 format 名砌 URL 都要經
 * `FORMAT_QUERY_NAME` 譯一次 —— 唔譯就係 CLI 一句 `--format portrait` 靜靜攞返
 * 4:5，照 200、`x-og-format` 仲會講「post」，差 90px 高冇人發現。
 */
const cliSrc = read("scripts/heatmap-download.mjs");
check("⑧j: portrait 過 wire 要寫 3x4", FORMAT_QUERY_NAME.portrait === "3x4", FORMAT_QUERY_NAME.portrait);
/* 唔係比字串，係行真嗰條解析路兜返轉頭。 */
for (const format of RUNTIME_FORMATS) {
  check(`⑧j: ?format=${FORMAT_QUERY_NAME[format]} 叫得返 ${format}`,
    runtimeReadFormat(FORMAT_QUERY_NAME[format]) === format,
    `→ ${runtimeReadFormat(FORMAT_QUERY_NAME[format])}`);
}
check("⑧j: 舊四個 format 個 wire 名同自己一樣（出咗街嘅 URL 唔准郁）",
  ["wide", "square", "post", "status"].every((f) => FORMAT_QUERY_NAME[f] === f),
  JSON.stringify(FORMAT_QUERY_NAME));
/* CLI 唔准自己抄一份，亦唔准直接塞個 format 名落 URL。 */
check("⑧j: CLI 讀返 lib 張表，唔准自己抄一份",
  /function queryNames\(src\)/.test(cliSrc) && /FORMAT_QUERY_NAME\[\^=\]\*=/.test(cliSrc)
  && /queryName: queryNames\(destinations\),/.test(cliSrc));
check("⑧j: CLI 砌 URL 前譯一次",
  /const wireFormat = opts\.queryName\[q\.format\] \?\? q\.format;/.test(cliSrc)
  && /new URLSearchParams\(\{ \.\.\.q, format: wireFormat \}\)/.test(cliSrc),
  "CLI 仲係直接塞 q.format 落 URL");
/* guard 有牙：叫唔返同一個 format 就要炸。 */
{
  const queryAnchor = '  portrait: "3x4",\n  widescreen: "widescreen",\n};';
  check("⑧j: 錨點仲喺度（改咗寫法就要更新呢個 test）",
    destinations.split(queryAnchor).length - 1 === 1, `搵到 ${destinations.split(queryAnchor).length - 1} 個`);
  const { error } = await loadLibChain({
    entry: "share-destinations.ts",
    mutate: {
      "share-destinations.ts": (src) => src.replace(queryAnchor,
        '  portrait: "portrait",\n  widescreen: "widescreen",\n};'),
    },
  });
  check("⑧j: wire 名叫唔返同一個 format 會即刻炸", error.includes("FORMAT_QUERY_NAME 叫唔返同一個 format"),
    `掟嘅係：${error || "(乜都冇掟)"}`);
  for (const dir of probeDirs) rmSync(dir, { recursive: true, force: true });
}

/* ⑧k 網頁兩個砌 URL 位都要行 FORMAT_QUERY_NAME。2026-08-23 review 實測：選單「IG 直向 3:4」
   撳落去送 `?format=portrait` → server 回 4:5 post（x-og-format: post），檔名卻叫 3x4。
   CLI 譯咗，網頁冇譯 —— 呢個 test 對住 source 攔返。 */
{
  const ogSrc = read("apps/web/src/lib/heatmap-og.ts");
  const detailSrc = read("apps/web/src/components/card-detail.tsx");
  check("⑧k: heatmapOgSearch 過 wire 行 FORMAT_QUERY_NAME",
    /q\.set\("format", FORMAT_QUERY_NAME\[opts\.format \?\? "post"\]\);/.test(ogSrc)
    && /import \{[^}]*FORMAT_QUERY_NAME[^}]*\} from "\.\/share-destinations"/.test(ogSrc),
    "heatmap-og 仲係直接塞 opts.format 落 ?format=");
  check("⑧k: heatmapOgSearch 冇剩返舊寫法", !/q\.set\("format", opts\.format \?\? "post"\);/.test(ogSrc));
  check("⑧k: card-detail 張卡 OG URL 過 wire 行 FORMAT_QUERY_NAME",
    /\?format=\$\{FORMAT_QUERY_NAME\[format\]\}&res=/.test(detailSrc)
    && /import \{[^}]*FORMAT_QUERY_NAME[^}]*\} from "@\/lib\/share-destinations"/.test(detailSrc),
    "card-detail 仲係直接塞 format 落 ?format=");
  check("⑧k: card-detail 冇剩返舊寫法", !/\?format=\$\{format\}&res=/.test(detailSrc));
  /* 張表真係會譯：選單個 3:4 行過 wire 一定係 3x4，唔係 portrait */
  const igPortrait = RUNTIME_TARGETS.find((t) => t.id === "ig-portrait");
  check("⑧k: ig-portrait 行過 wire 係 3x4", Boolean(igPortrait) && FORMAT_QUERY_NAME[igPortrait.format] === "3x4",
    `→ ${igPortrait ? FORMAT_QUERY_NAME[igPortrait.format] : "(冇 ig-portrait)"}`);
}

if (failed.length) {
  console.error("FAIL share destinations:\n" + failed.map((item) => ` - ${item}`).join("\n"));
  process.exit(1);
}
console.log(`PASS share destinations (${targets.size} 個目的地 → ${Object.keys(FORMAT_SIZES).length} 個尺寸，兩個面共用一個 picker)`);
