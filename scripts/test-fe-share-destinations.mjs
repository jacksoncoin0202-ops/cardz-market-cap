#!/usr/bin/env node
/*
 * 分享目的地 → 尺寸（owner 2026-08-20：「彈個 button 出嚟問你想分享去邊：WhatsApp、
 * Threads、X.COM、IG 定係其他地方？因應分享去唔同地方，要配合返唔同嘅最佳 social
 * media size」）。純靜態，唔使 dev server，由 run_all_tests.py glob 入 npm test。
 *
 * 守嘅係「一錯就靜靜出錯尺寸、但三邊都照 200」嗰批：
 *  ① `SHARE_TARGETS` 七個目的地齊：五個貼圖目的地（IG / Threads / X / WhatsApp / 其他）
 *     全部 4:5，全屏面 `status` 自己一行 9:16，`desktop` 1.91:1。
 *     ⚠️ 呢條就係整件事嘅重點：feed 三家（IG / Threads / X）對太直嘅圖**唔裁而係縮細**，
 *     邊個目的地寫錯咗 9:16 就係張圖永遠得七八成闊，冇 error、冇 log、冇人發現。
 *  ② `story` alias 仲係指住 `post`。派咗出去嘅舊 HTML 仲喺 CDN／用戶 tab 度，
 *     嗰粒係**通用**分享掣，指去 9:16 = ① 嗰個陷阱由舊 tab 直接中。
 *  ③ `status` 1080×1920 真身喺 `FORMAT_SIZES`，og route 有佢嘅 JPEG 質素。
 *  ④ 兩個面（熱力圖／卡片內頁）行同一個 `ShareMenu`，冇第二份 picker（AGENTS.md 規矩 13）。
 *  ⑤ i18n 四條新 key 五個語言齊，舊嗰批死 key 清得乾淨。
 *  ⑥ 熱力圖 canvas 三個比例槽仲喺度，而 `exportHeatmap` 冇預設值 —— 有預設值就係
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
const shareImage = read("apps/web/src/lib/share-image.ts");
const shareMenu = read("apps/web/src/components/share-menu.tsx");
const heatmap = read("apps/web/src/components/heatmap.tsx");
const cardDetail = read("apps/web/src/components/card-detail.tsx");
const ogRoute = read("apps/web/src/app/api/og/card/[id]/route.tsx");
const i18n = read("apps/web/src/lib/i18n.ts");
const css = read("apps/web/src/app/globals.css");

/* ① 目的地表。唔用 regex 逐條 assert 字面 —— 讀返出嚟砌返 object，改咗次序／加多個
   目的地都唔會假紅，但改錯咗比例就一定紅。 */
const targets = new Map();
for (const m of destinations.matchAll(/\{\s*id:\s*"([\w-]+)",\s*format:\s*"(\w+)",\s*aspect:\s*"(\w+)",\s*ratio:\s*"([\d.:]+)"/g)) {
  targets.set(m[1], { format: m[2], aspect: m[3], ratio: m[4] });
}
check("SHARE_TARGETS 讀得返（改咗寫法就要更新呢個 test）", targets.size >= 7, `搵到 ${targets.size} 個`);

/* owner 2026-08-20 點名嗰四個平台 + 全屏面 + 兩個兜底，一個都唔准少 */
for (const id of ["instagram", "threads", "x", "whatsapp", "status", "other", "desktop"]) {
  check(`SHARE_TARGETS 有 ${id}`, targets.has(id));
}
/* ⚠️ 五個貼圖目的地全部 4:5。呢度唔准出現 9:16。
   WhatsApp 都喺呢個 list：佢個對話／群組氣泡保持比例唔裁，4:5 佔到最高（owner
   2026-08-20，e61c1aa9）。全屏 9:16 係下面 `status` 自己嗰行。 */
for (const id of ["instagram", "threads", "x", "whatsapp", "other"]) {
  const target = targets.get(id);
  if (!target) continue;
  check(`${id} 出 4:5（feed／氣泡唔裁，太直只會縮細）`, target.format === "post" && target.aspect === "post" && target.ratio === "4:5",
    JSON.stringify(target));
}
const status = targets.get("status");
check("status 出 9:16（全屏面自己一行，唔掛公司名）",
  status?.format === "status" && status?.aspect === "wa" && status?.ratio === "9:16", JSON.stringify(status));
const desktop = targets.get("desktop");
check("desktop 出闊版", desktop?.format === "wide" && desktop?.aspect === "frame", JSON.stringify(desktop));
check("desktop 喺熱力圖跟畫面（frameOnHeatmap）", /id: "desktop"[^}]*frameOnHeatmap: true/.test(destinations));

/* 目的地表同 alias 表對得返 —— import-time guard 已經會炸，但 build 唔行到就冇人知 */
for (const [id, target] of targets) {
  check(`FORMAT_ALIASES.${id} 同選單一致`, new RegExp(`^\\s*${id}: "${target.format}",`, "m").test(destinations),
    `選單話 ${target.format}`);
}
check("import-time drift guard 仲喺度", /const drifted = SHARE_TARGETS\.filter/.test(destinations));
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
check("SHARE_FORMATS 有 status", /export const SHARE_FORMATS = \["wide", "post", "status"\] as const;/.test(destinations));
/* ⚠️ 尺寸 2026-08-20 起住喺 `FORMAT_SIZES`（route.tsx 讀返佢）——見下面 ⑦a 攞真值再對。 */
check("share-destinations 有 status 1080×1920", /status: \{ width: 1080, height: 1920 \}/.test(destinations));
check("og route JPEG_QUALITY 有 status", /JPEG_QUALITY: Record<ShareFormat, number> = \{[^}]*status: \d+/.test(ogRoute));
check("TALL_GEO 兩個直度 format 各自幾何", /const TALL_GEO: Record<Exclude<ShareFormat, "wide">/.test(ogRoute));
check("status 避開 story 平台 UI（上下 250 安全區）", /status: \{ padding: "250px 56px"/.test(ogRoute));
check("TEXT_ONLY_GEO 有 status（唔准跌落 wide 嗰套）", /^\s*status: \{ padding: "250px 72px"/m.test(ogRoute));
/* ⚠️ 卡圖母版得 429×600：status 高咗唔准順手谷大卡圖，一谷就係放大糊咗 */
check("status 冇谷大卡圖（POST_ART_MAX_* 冇郁）", /const POST_ART_MAX_WIDTH = 452;/.test(ogRoute) && /const POST_ART_MAX_HEIGHT = 560;/.test(ogRoute));
/* 三處 format 分支一律問「係咪 wide」——問「係咪 post」嘅話 status 會靜靜跌落 wide 嗰套幾何。
   剝走註釋先掃：上面 TEXT_ONLY_GEO 個註**特登**引住呢句錯寫法做反面教材。 */
const ogCode = ogRoute.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
check("og route 冇剩返 `format === \"post\"` 分支（status 會跌落 wide）", !/format === "post"/.test(ogCode),
  "改成 format === \"wide\" ? wide : 直度版");

/* ④ 一個 picker，兩個面共用 */
check("ShareMenu 由 SHARE_TARGETS 出", /SHARE_TARGETS\.map\(/.test(shareMenu));
check("熱力圖用 ShareMenu", /<ShareMenu/.test(heatmap) && /surface="heatmap"/.test(heatmap));
check("卡片內頁用 ShareMenu", /<ShareMenu/.test(cardDetail) && /surface="card"/.test(cardDetail));
check("熱力圖冇剩返舊 picker markup", !/heatmap-share-menu|heatmap-export-post|heatmap-export-wa/.test(heatmap));
check("熱力圖冇剩返 shareAspect state", !/setShareAspectState|cardz-heatmap-share-aspect/.test(heatmap));
/* 目的地→比例只准有一張表：兩個 component 都唔准自己寫死平台名對比例 */
for (const [name, src] of [["heatmap.tsx", heatmap], ["card-detail.tsx", cardDetail], ["share-menu.tsx", shareMenu]]) {
  const bare = src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
  check(`${name} 冇自己寫死比例字串`, !/"4:5"|"9:16"|"16:9"|"1\.91:1"/.test(bare),
    "比例只准住喺 lib/share-destinations.ts");
}
/* ⚠️ 分享圖 fetch 一定要有 AbortSignal：冇 timeout 嘅 fetch 吊死 = 個掣永遠 busy */
check("卡片內頁 share fetch 有 timeout", /AbortSignal\.timeout\(SHARE_FETCH_TIMEOUT_MS\)/.test(cardDetail));
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
check("warm cache key 連埋語言", /const key = `\$\{format\}\|\$\{imageLang\}`;/.test(cardDetail)
  && /shareBlobs\.current\.get\(`\$\{target\.format\}\|\$\{imageLang\}`\)/.test(cardDetail));

/* ⑤ i18n：四條新 key × 5 語言，舊 key 清乾淨 */
const localeText = i18n.slice(i18n.indexOf("export const copy"));
for (const key of ["shareTo", "shareToStatus", "shareToOther", "shareToDesktop", "shareRatioFrame"]) {
  check(`五個語言都有 ${key}`, (localeText.match(new RegExp(`${key}:`, "g")) || []).length === 5,
    `${(localeText.match(new RegExp(`${key}:`, "g")) || []).length} 個`);
}
for (const dead of ["shareImagePost", "shareImageWa", "shareShape"]) {
  check(`死 key ${dead} 清乾淨`, !i18n.includes(dead));
}

/* ⑥ 熱力圖分享走 OG download API，唔再喺 client canvas 截圖 */
check("SHARE_WA_ASPECT is 9/16", /export const SHARE_WA_ASPECT = 9 \/ 16;/.test(shareImage));
check("ShareAspect 由 share-destinations 出（一張表）", /export type \{ ShareAspect \};/.test(shareImage)
  && /import type \{ ShareAspect \} from "\.\/share-destinations";/.test(shareImage));
check("SHARE_ASPECTS 三個槽", /export const SHARE_ASPECTS = \["post", "wa", "frame"\] as const;/.test(destinations));
check("heatmap 分享 GET /api/og/heatmap", /heatmapOgPath\(/.test(heatmap) && /fetch\(path/.test(heatmap));
/* 2026-08-21：timeout 由寫死 45 秒改成跟清晰度（4K 實測 50–57 秒，45 秒會次次自斬）。
   同日再收窄：真站量到 4K 一定俾 gateway 喺 60 秒斬，所以除咗**單次** timeout，
   仲要有**成個 retry 迴圈**嘅預算。兩個數都要喺度 —— 淨得一個嗰種寫法就係
   「等一次然後放棄」或者「一次過等十分鐘」，兩樣都錯。呢條比原本嚴，冇放鬆。 */
check("heatmap 分享 timeout 跟清晰度", /AbortSignal\.timeout\(per\)/.test(heatmap)
  && /RESOLUTION_TIMEOUT_MS\[res\]/.test(heatmap)
  && /RESOLUTION_RETRY_BUDGET_MS\[res\]/.test(heatmap));
check("heatmap warm cache key 連語言 period scope updown res",
  /`\$\{format\}\|\$\{imageLang\}\|\$\{activePeriod\}\|\$\{visibleCount\}\|\$\{scope\}\|\$\{ogTheme\}\|\$\{upDown\}\|\$\{res\}`/.test(heatmap));
check("exportHeatmap 冇預設 target", /const exportHeatmap = useCallback\(async \(target: ShareTarget\) =>/.test(heatmap));
check("heatmap onWarm 預先 fetch", /onWarm=\{\(target\) => warmShareImage\(target\.format\)\}/.test(heatmap));
check("filename 比例喺 heatmap-og", /9x16/.test(read("apps/web/src/lib/heatmap-og.ts")));


/* ═════════════════════════════════════════════════════════════════════════
 * ⑦ 2026-08-20 上街之後嘅紅隊審計捉返嚟嗰批。每條都係「照出 200、冇 error、
 *    冇 log」嗰種靜默錯，所以一定要有 test，唔可以改完就算。
 * ═════════════════════════════════════════════════════════════════════════ */
const { FORMAT_SIZES, SHARE_TARGETS: RUNTIME_TARGETS, readShareFormat: runtimeReadFormat } =
  await import(pathToFileURL(join(ROOT, DEST_REL)).href);

/* ⑦a 尺寸真身得一份：route.tsx 唔准再自己寫多組（AGENTS.md 規矩 13） */
check("FORMAT_SIZES wide = 1200×630",
  FORMAT_SIZES.wide.width === 1200 && FORMAT_SIZES.wide.height === 630, JSON.stringify(FORMAT_SIZES.wide));
check("FORMAT_SIZES post = 1080×1350",
  FORMAT_SIZES.post.width === 1080 && FORMAT_SIZES.post.height === 1350, JSON.stringify(FORMAT_SIZES.post));
check("FORMAT_SIZES status = 1080×1920",
  FORMAT_SIZES.status.width === 1080 && FORMAT_SIZES.status.height === 1920, JSON.stringify(FORMAT_SIZES.status));
check("og route 讀 FORMAT_SIZES 唔自己開表",
  /FORMAT_SIZES, readShareFormat/.test(ogRoute) && /const spec = FORMAT_SIZES\[format\];/.test(ogRoute));

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
    const mutated = destinations.replace('ratio: "1.91:1", frameOnHeatmap: true', 'ratio: "16:9", frameOnHeatmap: true');
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
check("熱力圖 OG 失敗唔准靜靜扮成功（要掟）",
  /throw new Error\(`heatmap OG HTTP \$\{response\.status\}`\)/.test(heatmap)
  && /throw new Error\(`heatmap OG 攞唔到/.test(heatmap));

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

if (failed.length) {
  console.error("FAIL share destinations:\n" + failed.map((item) => ` - ${item}`).join("\n"));
  process.exit(1);
}
console.log(`PASS share destinations (${targets.size} 個目的地 → 3 個尺寸，兩個面共用一個 picker)`);
