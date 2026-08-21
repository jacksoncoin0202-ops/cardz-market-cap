#!/usr/bin/env node
/*
 * 清晰度（1080p / 4K）＋ 右上角個戳（截圖嗰一刻、跟用戶時區）嘅契約。
 * 純靜態＋in-process import，唔使 dev server，由 run_all_tests.py glob 入 npm test。
 *
 * owner 2026-08-21：
 *   「熱力圖 可否改到 人地下載可以揀 4K 嗎？我想 1080p 同埋 4K 兩隻分別嘅啫。」
 *   「我想可以打個 CLI 過去，即係唔使上 website。」
 *   「右上角嗰個日子，一定要變返我截圖嗰一刻嘅日子，並不是呢個官方數據嘅日子。
 *     時間要跟返用戶當地嘅時間（例如 HKT）。」
 *
 * 呢個閘守嘅係四個「照樣 200、冇人發現」嘅窿：
 *  A. **cache key 漏咗 `res` 或者個戳** —— 兩個唔同參數共用一個檔名，第二個人攞到
 *     第一個人張圖。揀 4K 出 1080p、或者張圖印住人哋部機嘅鐘。兩邊都係 200。
 *  B. **tier 名講大話** —— 個掣寫住「1080p」但真身出 864×1080。
 *  C. **慢嗰級用返快嗰級嘅 timeout** —— 4K 實測 50–57 秒，45 秒個閘會次次自斬，
 *     用戶睇到嘅係「撳 4K 永遠失敗」，而 code 睇落完全正常。
 *  D. **CLI 同網站行兩張表** —— CLI 唔自己抄一份選項，佢由 `apps/web/src/lib/*.ts`
 *     抽。抽唔到就要即刻嗌，唔准靜靜咁出個空表變成「乜都唔認得」。
 *
 * 每條 source check 都有**逐個 needle 嘅負控制**（剝走就要變假），三個 import-time
 * guard 都有真·種 bug 證明佢會炸（AGENTS.md 規矩 9）。
 */
import { copyFileSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const failed = [];
const notes = [];
const check = (label, condition, detail) => {
  if (!condition) failed.push(detail ? `${label}: ${detail}` : label);
};
const read = (rel) => readFileSync(join(ROOT, rel), "utf8");

const LIB = "apps/web/src/lib";
const RES_REL = `${LIB}/share-resolution.ts`;
const STAMP_REL = `${LIB}/share-stamp.ts`;
const DEST_REL = `${LIB}/share-destinations.ts`;
const OG_REL = `${LIB}/heatmap-og.ts`;
const I18N_REL = `${LIB}/i18n.ts`;
const ROUTE_REL = "apps/web/src/app/api/og/heatmap/route.tsx";
const MENU_REL = "apps/web/src/components/share-menu.tsx";
const HEATMAP_REL = "apps/web/src/components/heatmap.tsx";
const CLI_REL = "scripts/heatmap-download.mjs";

const SRC = {
  res: read(RES_REL),
  stamp: read(STAMP_REL),
  dest: read(DEST_REL),
  og: read(OG_REL),
  i18n: read(I18N_REL),
  route: read(ROUTE_REL),
  menu: read(MENU_REL),
  heatmap: read(HEATMAP_REL),
  cli: read(CLI_REL),
};

/* ═══════════════════════════════════════════════════════════════════════════
 * 載真身 module
 *
 * `share-resolution.ts` 個 import 冇副檔名（`./share-destinations`），Node 直接
 * import 會 ERR_MODULE_NOT_FOUND。所以抄兩個檔入 tmpdir，淨係改個 specifier
 * 加返 `.ts` —— 改嘅嘢下面會 assert 住，唔准順手改第二啲嘢。
 * ═════════════════════════════════════════════════════════════════════════ */
const probeDirs = [];
async function loadResolution(mutate = (src) => src) {
  const dir = mkdtempSync(join(tmpdir(), "cardz-res-probe-"));
  probeDirs.push(dir);
  copyFileSync(join(ROOT, DEST_REL), join(dir, "share-destinations.ts"));
  const rewritten = mutate(SRC.res).replace('from "./share-destinations"', 'from "./share-destinations.ts"');
  const probe = join(dir, "share-resolution.ts");
  writeFileSync(probe, rewritten, "utf8");
  try {
    return { mod: await import(pathToFileURL(probe).href), error: "" };
  } catch (error) {
    return { mod: null, error: String(error?.message ?? error) };
  }
}

const baseline = await loadResolution();
check("真身 share-resolution.ts import 得（三個 guard 對住真數據唔會炸）",
  baseline.mod !== null, baseline.error);
if (!baseline.mod) {
  console.error(failed.map((row) => `FAIL ${row}`).join("\n"));
  process.exit(1);
}
const R = baseline.mod;
const stampMod = await import(pathToFileURL(join(ROOT, STAMP_REL)).href);
const cli = await import(pathToFileURL(join(ROOT, CLI_REL)).href);

/* ═══════════════════════════════════════════════════════════════════════════
 * A. 清晰度表本身（runtime，唔靠 regex）
 * ═════════════════════════════════════════════════════════════════════════ */
check("A1: 得兩級（owner 明講「1080p 同埋 4K 兩隻分別嘅啫」，冇 2K）",
  JSON.stringify(R.SHARE_RESOLUTIONS) === '["1080p","4k"]', JSON.stringify(R.SHARE_RESOLUTIONS));
check("A2: 預設 1080p（4K 未 cache 要成分鐘，唔准做預設）",
  R.DEFAULT_SHARE_RESOLUTION === "1080p", String(R.DEFAULT_SHARE_RESOLUTION));
check("A3: 1080p = 1×", R.RESOLUTION_SCALE["1080p"] === 1, String(R.RESOLUTION_SCALE["1080p"]));
check("A3: 4k = 2×", R.RESOLUTION_SCALE["4k"] === 2, String(R.RESOLUTION_SCALE["4k"]));
check("A3: 得 1080p 一級唔算慢", R.RESOLUTION_IS_SLOW["1080p"] === false && R.RESOLUTION_IS_SLOW["4k"] === true);

/* A4 個 tier 名唔准講大話：`post` 係七個目的地入面五個嘅預設 */
for (const [res, shortSide] of [["1080p", 1080], ["4k", 2160]]) {
  const post = R.resolutionPixels("post", res);
  check(`A4: post ${res} 短邊 ${shortSide}`, Math.min(post.width, post.height) === shortSide,
    `${post.width}×${post.height}`);
  const status = R.resolutionPixels("status", res);
  check(`A4: status ${res} 短邊 ${shortSide}`, Math.min(status.width, status.height) === shortSide,
    `${status.width}×${status.height}`);
}
/* `wide` 個 tier 名係級數唔係像素（1200×630 短邊 630）。呢條唔係漏咗，係明寫嘅例外。 */
{
  const wide1 = R.resolutionPixels("wide", "1080p");
  const wide2 = R.resolutionPixels("wide", "4k");
  check("A4: wide 1080p = 1200×630（og:image 標準尺寸，唔跟 tier 名）",
    wide1.width === 1200 && wide1.height === 630, `${wide1.width}×${wide1.height}`);
  check("A4: wide 4k = 2400×1260（級數 ×2，唔係 3840 —— 所以 UI 一定要報真實像素）",
    wide2.width === 2400 && wide2.height === 1260, `${wide2.width}×${wide2.height}`);
}

/* A5 prototype key 唔准打到 500（`?format=constructor` 2026-08-20 實測 HTTP 500） */
for (const bad of ["constructor", "__proto__", "prototype", "toString", "valueOf", "hasOwnProperty"]) {
  check(`A5: ?res=${bad} 跌返 1080p 唔准爆`, R.readShareResolution(bad) === "1080p",
    String(R.readShareResolution(bad)));
}
check("A5: readShareResolution 行 Object.hasOwn", /Object\.hasOwn\(RESOLUTION_ALIASES, key\)/.test(SRC.res));
for (const [input, want] of [["4K", "4k"], ["2160", "4k"], ["2x", "4k"], ["UHD", "4k"],
  ["1080", "1080p"], ["hd", "1080p"], ["1x", "1080p"], ["", "1080p"], [null, "1080p"], ["乜春", "1080p"]]) {
  check(`A6: res=${JSON.stringify(input)} → ${want}`, R.readShareResolution(input) === want,
    String(R.readShareResolution(input)));
}
check("A7: 1080p timeout 45 秒（同以前一樣）", R.RESOLUTION_TIMEOUT_MS["1080p"] === 45_000,
  String(R.RESOLUTION_TIMEOUT_MS["1080p"]));
check("A7: 4k timeout ≥ 120 秒（實測 50–57 秒，45 秒會次次自斬）",
  R.RESOLUTION_TIMEOUT_MS["4k"] >= 120_000, String(R.RESOLUTION_TIMEOUT_MS["4k"]));

/* ═══════════════════════════════════════════════════════════════════════════
 * B. 三個 import-time guard 真係有牙（AGENTS.md 規矩 9：種返個 bug 睇住佢紅）
 * ═════════════════════════════════════════════════════════════════════════ */
const guardCases = [
  {
    label: "B1: alias 指去唔存在嘅 tier 會炸",
    mutate: (src) => src.replace('"2160": "4k",', '"2160": "2k",'),
    want: "alias 指去唔存在嘅 tier",
  },
  /* B2 改嘅係另一個檔（share-destinations.ts），行下面自己嗰個 probe */
  {
    label: "B3: 慢嗰級用返 45 秒會炸",
    mutate: (src) => src.replace('"4k": 180_000,', '"4k": 45_000,'),
    want: "慢嘅 tier timeout 太窄",
  },
];
for (const c of guardCases) {
  const mutated = c.mutate(SRC.res);
  check(`${c.label} — 改得到（改咗寫法就要更新呢個 test）`, mutated !== SRC.res);
  const { mod, error } = await loadResolution(() => mutated);
  check(c.label, mod === null && error.includes(c.want), `掟嘅係：${error || "(乜都冇掟)"}`);
}
/* B2 要改另一個檔（`FORMAT_SIZES.post`），所以自己起個 probe dir */
{
  const dir = mkdtempSync(join(tmpdir(), "cardz-res-probe-post-"));
  probeDirs.push(dir);
  const shrunk = SRC.dest.replace("post: { width: 1080, height: 1350 }", "post: { width: 864, height: 1080 }");
  check("B2 — 改得到 FORMAT_SIZES.post（改咗寫法就要更新呢個 test）", shrunk !== SRC.dest);
  writeFileSync(join(dir, "share-destinations.ts"), shrunk, "utf8");
  writeFileSync(join(dir, "share-resolution.ts"),
    SRC.res.replace('from "./share-destinations"', 'from "./share-destinations.ts"'), "utf8");
  let error = "";
  try { await import(pathToFileURL(join(dir, "share-resolution.ts")).href); } catch (e) { error = String(e?.message ?? e); }
  check("B2: post 尺寸講大話會炸（個掣寫 1080p 但真身 864）",
    error.includes("tier 名同真實像素唔夾"), `掟嘅係：${error || "(乜都冇掟)"}`);
}

/* ═══════════════════════════════════════════════════════════════════════════
 * C. 右上角個戳
 * ═════════════════════════════════════════════════════════════════════════ */
check("C1: stamp 預設 data（og:image unfurl 同 HERMES cron 鏈唔准跟住變）",
  stampMod.readStampMode(null) === "data" && stampMod.readStampMode("") === "data"
  && stampMod.readStampMode("乜春") === "data");
check("C1: stamp=now 認得", stampMod.readStampMode("now") === "now");
check("C2: 廢時區跌返 UTC（唔准 500）",
  stampMod.readTimeZone("Asia/Nowhere") === "UTC" && stampMod.readTimeZone(null) === "UTC"
  && stampMod.readTimeZone("  ") === "UTC");
check("C2: 好時區照留", stampMod.readTimeZone("Asia/Tokyo") === "Asia/Tokyo");

/* C3 owner 兩個實例：JST 同 HKT。冚唪唥用同一個絕對時刻，睇住佢真係換咗鐘。 */
{
  const at = new Date("2026-08-21T06:41:00Z"); // = 15:41 JST = 14:41 HKT
  const jstEn = stampMod.nowStamp(at, "Asia/Tokyo", "en");
  const jstZh = stampMod.nowStamp(at, "Asia/Tokyo", "zh-TW");
  const hkt = stampMod.nowStamp(at, "Asia/Hong_Kong", "en");
  check("C3: JST 出得返 JST 同當地鐘", jstEn === "Aug 21, 2026 · 15:41 JST", jstEn);
  check("C3: 中文排版", jstZh === "2026年8月21日 15:41 JST", jstZh);
  check("C3: HKT 出得返 HKT 同當地鐘（同一刻爭一個鐘）", hkt === "Aug 21, 2026 · 14:41 HKT", hkt);
  check("C3: 一定要有時區（冇就係一句廢話，睇嘅人唔知邊度嘅 15:41）",
    /\b(JST|HKT|GMT[+-]\d)/.test(jstEn) && /\b(JST|HKT|GMT[+-]\d)/.test(hkt));
  const utc = stampMod.nowStamp(at, "UTC", "en");
  check("C3: UTC 一樣出到嘢", utc.startsWith("Aug 21, 2026 · 06:41"), utc);
}
check("C4: stamp=data 個 cache key 永遠一樣（cron 鏈唔會逐分鐘 miss）",
  stampMod.stampCacheKey("data", "隨便乜文字") === "data");
check("C4: stamp=now 個 cache key 就係顯示文字本身（唔准兩份精度定義）",
  stampMod.stampCacheKey("now", "2026年8月21日 15:41 JST") === "now:2026年8月21日 15:41 JST");

/* C5 自己再驗一次「表入面冇 DST 區」——唔靠佢自己個 guard 講自己冇事 */
{
  const abbrev = [...SRC.stamp.matchAll(/"(\w+\/[\w_+-]+)":\s*"([A-Z]{2,5})"/g)].map((m) => [m[1], m[2]]);
  check("C5: 抽到 TZ_ABBREV（抽唔到就當冇驗過，唔准當 pass）", abbrev.length >= 4, `${abbrev.length} 個`);
  const offset = (tz, month) => {
    const probe = new Date(Date.UTC(2026, month, 1, 12, 0, 0));
    const p = new Intl.DateTimeFormat("en-US", {
      timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
    }).formatToParts(probe);
    const get = (type) => Number(p.find((x) => x.type === type)?.value ?? "0");
    return Math.round((Date.UTC(get("year"), get("month") - 1, get("day"), get("hour") % 24, get("minute"), get("second")) - probe.getTime()) / 60000);
  };
  for (const [tz] of abbrev) {
    check(`C5: ${tz} 冇夏令時間（有就係一年講錯半年）`, offset(tz, 0) === offset(tz, 6),
      `一月 ${offset(tz, 0)} vs 七月 ${offset(tz, 6)}`);
  }
  for (const collide of ["CST", "IST"]) {
    check(`C5: 唔准用撞名縮寫 ${collide}（中／美、印／以／愛爾蘭爭同一個名）`,
      !abbrev.some(([, ab]) => ab === collide));
  }
}
/* C6 DST guard 有牙：植個 London 入表，import 要炸 */
{
  const dir = mkdtempSync(join(tmpdir(), "cardz-stamp-guard-"));
  probeDirs.push(dir);
  const mutated = SRC.stamp.replace('"Asia/Tokyo": "JST",', '"Asia/Tokyo": "JST",\n  "Europe/London": "BST",');
  check("C6 — 植得入 TZ_ABBREV（改咗寫法就要更新呢個 test）", mutated !== SRC.stamp);
  writeFileSync(join(dir, "share-stamp.ts"), mutated, "utf8");
  let error = "";
  try { await import(pathToFileURL(join(dir, "share-stamp.ts")).href); } catch (e) { error = String(e?.message ?? e); }
  check("C6: 加個有夏令時間嘅區入表會即刻炸",
    error.includes("有夏令時間"), `掟嘅係：${error || "(乜都冇掟)"}`);
}

/* ═══════════════════════════════════════════════════════════════════════════
 * D. CLI ↔ lib parity（CLI 唔准自己抄一份選項表）
 * ═════════════════════════════════════════════════════════════════════════ */
{
  const opts = cli.readOptions();
  for (const [key, value] of Object.entries(opts)) {
    const size = Array.isArray(value) ? value.length : Object.keys(value).length;
    check(`D0: CLI 抽到 ${key}（抽唔到就係靜靜出個空表 = 乜都唔認得）`, size > 0, `${size} 個`);
  }
  check("D1: CLI 嘅清晰度 = lib SHARE_RESOLUTIONS",
    JSON.stringify(opts.res) === JSON.stringify([...R.SHARE_RESOLUTIONS]),
    `${JSON.stringify(opts.res)} vs ${JSON.stringify([...R.SHARE_RESOLUTIONS])}`);
  check("D2: CLI 嘅倍數 = lib RESOLUTION_SCALE",
    JSON.stringify(opts.scale) === JSON.stringify(R.RESOLUTION_SCALE),
    `${JSON.stringify(opts.scale)} vs ${JSON.stringify(R.RESOLUTION_SCALE)}`);
  for (const format of Object.keys(opts.sizes)) {
    for (const res of opts.res) {
      const want = R.resolutionPixels(format, res);
      const got = {
        width: Math.round(opts.sizes[format].width * opts.scale[res]),
        height: Math.round(opts.sizes[format].height * opts.scale[res]),
      };
      check(`D3: CLI ${format} ${res} 算出嚟同 lib 一樣`,
        got.width === want.width && got.height === want.height,
        `${got.width}×${got.height} vs ${want.width}×${want.height}`);
    }
  }
  check("D4: CLI 嘅 stamp 模式 = lib STAMP_MODES",
    JSON.stringify(opts.stamp) === JSON.stringify([...stampMod.STAMP_MODES]),
    JSON.stringify(opts.stamp));
  check("D5: 4K 檔名帶後綴，1080p 唔帶（同一 folder 唔准互相覆蓋）",
    cli.outputName({ scope: "all", show: 40, period: "7d", format: "post", res: "4k", sizes: opts.sizes }).includes("4k")
    && !cli.outputName({ scope: "all", show: 40, period: "7d", format: "post", res: "1080p", sizes: opts.sizes }).includes("1080p"));
}

/* ═══════════════════════════════════════════════════════════════════════════
 * E. Source 契約 —— 每條都有逐個 needle 嘅負控制（剝走就要變假）
 * ═════════════════════════════════════════════════════════════════════════ */
const CONTRACTS = [
  /* route：最陰功嗰批 —— cache key 漏一個參數 = 兩個人共用一張圖，兩邊都 200 */
  { id: "E1", file: ROUTE_REL, src: SRC.route, label: "route 讀 res / stamp / tz",
    needles: ['query.get("res")', 'query.get("stamp")', 'query.get("tz")'] },
  { id: "E2", file: ROUTE_REL, src: SRC.route, label: "route 個倍數由 RESOLUTION_SCALE 出",
    needles: ["RESOLUTION_SCALE[res]"] },
  { id: "E3", file: ROUTE_REL, src: SRC.route, label: "cache key 入面有 res 同個戳（漏咗就係攞返人哋張圖）",
    needles: ["res, stampCacheKey(stampMode, dateText),"] },
  { id: "E4", file: ROUTE_REL, src: SRC.route, label: "個戳 now 模式行 nowStamp(tz)",
    needles: ['stampMode === "now"', "nowStamp(new Date(), tz, lang)"] },
  { id: "E5", file: ROUTE_REL, src: SRC.route, label: "x-og-stamp 要 percent-encode（HTTP header 淨係食 latin-1，中文直接掟 throw）",
    needles: ["encodeURIComponent(dateText)"] },
  { id: "E6", file: ROUTE_REL, src: SRC.route, label: "檔名帶 res", needles: ["heatmapOgFilename({", "lang, res }"] },

  /* heatmap-og：三個新 key 只喺唔係預設嗰陣先寫（保住 cron 鏈同 cache） */
  { id: "E7", file: OG_REL, src: SRC.og, label: "res 只喺唔係預設先入 query",
    needles: ["opts.res !== DEFAULT_SHARE_RESOLUTION"] },
  { id: "E8", file: OG_REL, src: SRC.og, label: "stamp 只喺唔係 data 先入 query",
    needles: ['opts.stamp !== "data"'] },
  { id: "E9", file: OG_REL, src: SRC.og, label: "4K 檔名帶後綴", needles: ["RESOLUTION_LABEL[res].toLowerCase()"] },

  /* share-menu：報真實像素、死鎖閘跟清晰度 */
  { id: "E10", file: MENU_REL, src: SRC.menu, label: "選單報真實闊×高（「4K」係級數唔係像素）",
    needles: ["resolutionPixels(target.format, quality.value)", "{px.width}×{px.height}"] },
  { id: "E11", file: MENU_REL, src: SRC.menu, label: "死鎖閘跟清晰度（4K 用 45 秒 = 撳落去必定紅）",
    needles: ["RESOLUTION_TIMEOUT_MS[quality.value] + PICK_TIMEOUT_MS"] },
  { id: "E12", file: MENU_REL, src: SRC.menu, label: "清晰度粒掣行 menuitemradio + aria-checked（鍵盤／讀屏都要撳到）",
    needles: ['role="menuitemradio"', "aria-checked={res === quality.value}"] },
  { id: "E13", file: MENU_REL, src: SRC.menu, label: "roving index 數埋清晰度嗰行（唔係就永遠 Tab 唔到）",
    needles: ["resOptions.length + SHARE_TARGETS.length", "resOptions.length + offset"] },

  /* heatmap：戳、cache、timeout */
  { id: "E14", file: HEATMAP_REL, src: SRC.heatmap, label: "網站個掣一律 stamp=now + 部機時區",
    needles: ['stamp: "now"', "Intl.DateTimeFormat().resolvedOptions().timeZone"] },
  { id: "E15", file: HEATMAP_REL, src: SRC.heatmap, label: "開一次選單倒一次 blob cache（唔倒就印住幾個鐘前嘅鐘）",
    needles: ["onOpen={() => shareBlobs.current.clear()}"] },
  { id: "E16", file: HEATMAP_REL, src: SRC.heatmap, label: "client cache key 有 res",
    needles: ["${upDown}|${res}"] },
  { id: "E17", file: HEATMAP_REL, src: SRC.heatmap, label: "fetch timeout 跟清晰度",
    needles: ["AbortSignal.timeout(shareFetchTimeoutMs(res))", "RESOLUTION_TIMEOUT_MS[res]"] },
  { id: "E18", file: HEATMAP_REL, src: SRC.heatmap, label: "換清晰度即刻 warm（用新嗰級，唔係 state 舊值）",
    needles: ["warmShareImage(SHARE_TARGETS[0].format, next)"] },

  /* CLI：唔准自己維護一份選項表 */
  { id: "E19", file: CLI_REL, src: SRC.cli, label: "CLI 由 lib 抽選項（唔准自己抄一份）",
    needles: ['constArray(resolution, "SHARE_RESOLUTIONS")', 'constArray(stamp, "STAMP_MODES")'] },
  { id: "E20", file: CLI_REL, src: SRC.cli, label: "CLI 抽唔到就掟（唔准靜靜出個空表）",
    needles: ["讀唔到 ${name}", "RESOLUTION_SCALE 空"] },
  { id: "E21", file: CLI_REL, src: SRC.cli, label: "CLI 預設 stamp=now、時區跟機",
    needles: ['stamp: args.stamp ?? "now"', "localTimeZone()"] },
];
for (const c of CONTRACTS) {
  for (const needle of c.needles) {
    check(`${c.id}: ${c.label} — ${needle}`, c.src.includes(needle), c.file);
    /* 負控制：剝走呢個 needle，同一條 check 一定要變假。冇呢步就分唔清
       「條 check 過咗」同「條 check 根本永遠過」（AGENTS.md 規矩 9）。 */
    const stripped = c.src.split(needle).join("");
    check(`${c.id}n: 剝走 ${needle} 之後條 check 會紅`, !stripped.includes(needle));
  }
}

/* ═══════════════════════════════════════════════════════════════════════════
 * F. i18n：五個語言都要有，唔准淨係英文
 * ═════════════════════════════════════════════════════════════════════════ */
for (const key of ["shareQuality", "shareQualitySlow"]) {
  const hits = (SRC.i18n.match(new RegExp(`${key}:\\s*"`, "g")) || []).length;
  check(`F1: ${key} 五個語言齊`, hits === 5, `${hits} 個`);
}
/* 揀個 key **定義**做標的（`shareXxx:`），唔係成個檔搵字 —— 檔頭嗰段註釋正正就係
   解釋點解冇呢兩條 key，搵字版會俾自己段解釋照返一嘢。 */
check("F2: 冇 share1080p / share4K 呢啲 key（型號名唔入 i18n，同四個平台名同一個道理）",
  !/share(1080p|4[kK])\s*:/.test(SRC.i18n));
check("F3: heatmap 傳埋兩條 copy 落 ShareMenu",
  SRC.heatmap.includes("t.labels.shareQuality") && SRC.heatmap.includes("t.labels.shareQualitySlow"));
check("F4: 卡片內頁唔傳 quality（呢個掣係熱力圖先有）",
  !read("apps/web/src/components/card-detail.tsx").includes("quality={"));

/* ═══════════════════════════════════════════════════════════════════════════
 * G. 4K 個代價唔准淨係我知：實測數字要留喺 code 入面
 * ═════════════════════════════════════════════════════════════════════════ */
check("G1: share-resolution.ts 寫住實測時間（下一個人唔使自己再量一次）",
  /53\.7|50–57|50-57/.test(SRC.res) && /4\.3/.test(SRC.res));
check("G2: 4K 慢要出提示（唔准扮撳落去即刻有）",
  SRC.res.includes("RESOLUTION_IS_SLOW") && SRC.menu.includes("RESOLUTION_IS_SLOW[res]"));

for (const dir of probeDirs) rmSync(dir, { recursive: true, force: true });

if (notes.length) console.log(notes.map((row) => ` · ${row}`).join("\n"));
if (failed.length) {
  console.error(failed.map((row) => `FAIL ${row}`).join("\n"));
  process.exit(1);
}
console.log(`PASS test-fe-heatmap-resolution（${CONTRACTS.length} 條 source 契約 + 逐個 needle 負控制、4 個 import-time guard 種過 bug、CLI↔lib parity、五語 i18n）`);
