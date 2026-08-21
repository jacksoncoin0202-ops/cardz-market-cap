#!/usr/bin/env node
/*
 * 熱力圖 download API 契約（`GET /api/og/heatmap`）。
 * 純靜態，唔使 dev server，由 run_all_tests.py glob 入 npm test。
 *
 * 守嘅係「錯咗照 200、冇人發現」嗰批：
 *  ① 預設 period 係 7d，唔准繼承網站 `defaultMarketWindow`（180d）—— 嗰個係錯日趨勢
 *  ② query 七個鍵齊：period / show / scope / format / theme / updown / lang
 *  ③ route 真係讀呢七個鍵，scope 認得 pokemon / one-piece，updown 認得 red-up
 *  ④ 人手分享同 cron 行同一條 URL builder（heatmap.tsx fetch heatmapOgPath）
 *  ⑤ 預設 7d 唔係死註釋：runtime 值要真係 "7d"；植 180d 嘅 probe 要紅
 */
import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const failed = [];
const check = (label, condition, detail) => { if (!condition) failed.push(detail ? `${label}: ${detail}` : label); };
const read = (path) => readFileSync(join(ROOT, path), "utf8");

const OG_REL = "apps/web/src/lib/heatmap-og.ts";
const ROUTE_REL = "apps/web/src/app/api/og/heatmap/route.tsx";
const HEATMAP_REL = "apps/web/src/components/heatmap.tsx";
const TYPES_REL = "apps/web/src/lib/types.ts";
const ogSrc = read(OG_REL);
const routeSrc = read(ROUTE_REL);
const heatmapSrc = read(HEATMAP_REL);
const typesSrc = read(TYPES_REL);

const { readShareFormat } = await import(pathToFileURL(join(ROOT, "apps/web/src/lib/share-destinations.ts")).href);
const { readShareLang } = await import(pathToFileURL(join(ROOT, "apps/web/src/lib/share-copy.ts")).href);

check("path 係 /api/og/heatmap", /export const HEATMAP_OG_PATH = "\/api\/og\/heatmap"/.test(ogSrc));
check("source 預設 period 7d", /HEATMAP_OG_DEFAULT_PERIOD: MarketWindow = "7d"/.test(ogSrc));
check("source 預設 show 40", /HEATMAP_OG_DEFAULT_SHOW = 40/.test(ogSrc));
check("網站 defaultMarketWindow 仍然係 180d（對照組）",
  /export const defaultMarketWindow: MarketWindow = "180d";/.test(typesSrc));
const stripComments = (src) => src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
check("heatmap-og 唔 import defaultMarketWindow", !/\bdefaultMarketWindow\b/.test(stripComments(ogSrc)));
check("heatmap route 唔 import defaultMarketWindow", !/\bdefaultMarketWindow\b/.test(stripComments(routeSrc)));

/* 2026-08-21 加咗 res / stamp / tz（4K + 「截圖嗰一刻」個戳）。十個 key 都要三處齊：
   呢張表、heatmapOgSearch()、route 讀返。漏咗任何一處都係靜靜咁 200 但參數冇效。 */
const keys = ["period", "show", "scope", "format", "theme", "updown", "lang", "res", "stamp", "tz"];
check("十個 query key 寫死",
  /HEATMAP_OG_QUERY_KEYS = \[\s*"period", "show", "scope", "format", "theme", "updown", "lang", "res", "stamp", "tz",\s*\]/.test(ogSrc));
for (const key of keys) {
  check(`heatmapOgSearch 寫 ${key}`, new RegExp(`q\\.set\\("${key}"`).test(ogSrc));
  check(`route 讀 ${key}`, new RegExp(`query\\.get\\("${key}"\\)`).test(routeSrc));
}
check("heatmapOgSearch period 跌返 HEATMAP_OG_DEFAULT_PERIOD",
  /q\.set\("period", opts\.period \?\? HEATMAP_OG_DEFAULT_PERIOD\)/.test(ogSrc));
check("scopeFromKind 認得 pokemon / one-piece",
  /kind === "pokemon" \|\| kind === "one-piece" \? kind : "all"/.test(ogSrc));
check("ja/ko OG 字跌返 en", readShareLang("ja") === "en" && readShareLang("ko") === "en");
check("zh-TW / zh-CN 跟住走", readShareLang("zh-TW") === "zh-TW" && readShareLang("zh-CN") === "zh-CN");
check("route scope 認得 pokemon / one-piece",
  /raw === "pokemon" \|\| raw === "one-piece"/.test(routeSrc));
check("route updown 認得 red-up", /raw === "red-up" \? "red-up"/.test(routeSrc));
check("route 寫 x-og-period/scope/updown/lang",
  /x-og-period/.test(routeSrc) && /x-og-scope/.test(routeSrc)
  && /x-og-updown/.test(routeSrc) && /x-og-lang/.test(routeSrc));
check("heatmap route 逐語言載字體", /loadOgFonts\(lang\)/.test(routeSrc) && /SHARE_LANG_FONTS\[lang\]/.test(routeSrc));
check("heatmap route fontFamily 跟語言", /SHARE_FONT_FAMILY\[lang\]/.test(routeSrc));

check("heatmap.tsx fetch heatmapOgPath", /heatmapOgPath\(/.test(heatmapSrc) && /fetch\(path/.test(heatmapSrc));
check("卡圖優先 _600（唔好用 200 放大糊）", /variants\?\.\["600"\] \?\? card\.image\.variants\?\.\["200"\]/.test(routeSrc));
/*
 * 2026-08-21：owner 要 4K，route 唔再永遠 `return 1`。守嘅嘢冇鬆 —— 要守嘅由來都係
 * 「**冇寫 `res` 嗰條 URL 一定出 1×**」（og:image unfurl、HERMES cron 鏈、CLI 第一次
 * GET 全部行嗰條），而唔係「呢個 repo 唔准有 2×」。所以改成釘預設同倍數表。
 */
const resSrc = read("apps/web/src/lib/share-resolution.ts");
check("route 個倍數由 RESOLUTION_SCALE 出（唔准 route 自己寫死）", /return RESOLUTION_SCALE\[res\];/.test(routeSrc));
check("預設清晰度 1080p", /DEFAULT_SHARE_RESOLUTION: ShareResolution = "1080p"/.test(resSrc));
check("1080p 就係 1×（冇 res 嗰條 URL 唔准變大）", /"1080p": 1,/.test(resSrc));
check("cache 檔名分開清晰度同個戳", /cardz-og-heatmap/.test(routeSrc)
  && /res, stampCacheKey\(stampMode, dateText\),/.test(routeSrc));
check("heatmap.tsx 唔再 toBlob 出分享圖", !/canvas\.toBlob/.test(heatmapSrc));
check("landscape → wide", readShareFormat("landscape") === "wide");
check("portrait → post", readShareFormat("portrait") === "post");
check("tall → status", readShareFormat("tall") === "status");

{
  /* 唔 import 變種 module（heatmap-og.ts 有相對 import，tmpdir 會爆）。
     閘係「值唔係 7d 就紅」——種 180d 入 predicate，證明呢條 check 會 fire。 */
  const dailyOk = (period) => period === "7d";
  const sourcePeriod = (ogSrc.match(/HEATMAP_OG_DEFAULT_PERIOD: MarketWindow = "([^"]+)"/) || [])[1];
  check("⑤ 真身 7d", dailyOk(sourcePeriod), sourcePeriod);
  check("⑤ 180d 會紅（證明閘有牙）", dailyOk("180d") === false);
}

if (failed.length) {
  console.error(failed.map((row) => `FAIL ${row}`).join("\n"));
  process.exit(1);
}
console.log("PASS test-fe-heatmap-og");
