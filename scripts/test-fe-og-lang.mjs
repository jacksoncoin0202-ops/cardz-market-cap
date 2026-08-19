#!/usr/bin/env node
/*
 * OG 分享圖語言（`?lang=`）—— 純靜態，唔使 dev server，由 run_all_tests.py glob 入 npm test。
 *
 * owner 2026-08-20：「X.com 我哋有 CARDZGame 中文簡體，所以你中文都要出多一次。」
 * 同日「繁中點解無?」→ 加埋繁體。即係同一張卡出三次圖：英文、簡體、繁體。
 *
 * 呢個 feature 唯一嘅 failure mode **唔會報錯**：satori 遇到一個載咗嘅字體畫唔到嘅 glyph
 * （例如餵中文落 latin-only 嘅 Inter），出嘅係**空位** —— 唔 throw、唔 log、照回 200、
 * 檔案大細正常。即係話「壞咗」同「好地地」喺 CI 眼中一模一樣，除非有人專登去驗。
 * 呢份 test 就係嗰個人。守四件事：
 *   L1 `SHARE_LANG_FONTS` 講咗要嘅字體檔真係喺磁碟（sha256／sfnt 由 test-fe-font-contract ⑦ 驗）
 *   L2 `readShareLang` 認得 HERMES 鏈可能寫嘅幾種寫法，認唔到跌返 en（唔准 500）
 *   L3 張表唔係死 code —— route 真係行 `readShareLang`、真係將 `copy.fontFamily` 落 layout、
 *      真係載 `SHARE_LANG_FONTS`，同埋冇留返 hardcode 死嘅英文標籤
 *   L4 `share-copy.ts` 個 module guard 真係會炸（喺 temp 度整份「加咗語言但冇字體」嘅 copy 逼佢炸）
 */
import { createHash } from "node:crypto";
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const failed = [];
const check = (label, condition, detail) => { if (!condition) failed.push(detail ? `${label}: ${detail}` : label); };
const read = (path) => readFileSync(join(ROOT, path), "utf8");

const COPY_REL = "apps/web/src/lib/share-copy.ts";
const ROUTE_REL = "apps/web/src/app/api/og/card/[id]/route.tsx";
const FONT_DIR = join(ROOT, "apps/web/public/fonts/og");
const copySrc = read(COPY_REL);
const routeSrc = read(ROUTE_REL);

const { SHARE_LANGS, SHARE_LANG_FONTS, SHARE_FONT_FAMILY, readShareLang, shareCopy } =
  await import(pathToFileURL(join(ROOT, COPY_REL)).href);

/* ── L1：字體真係喺磁碟 ─────────────────────────────────────────────────── */
check("L1: 三個語言（en + zh-CN + zh-TW）", [...SHARE_LANGS].join() === "en,zh-CN,zh-TW", [...SHARE_LANGS].join());
for (const lang of SHARE_LANGS) {
  const entries = SHARE_LANG_FONTS[lang];
  if (lang === "en") {
    check("L1: en 唔使額外字體（Inter 夠）", entries === null, JSON.stringify(entries));
    continue;
  }
  check(`L1: ${lang} 有指定字體`, Array.isArray(entries) && entries.length > 0, JSON.stringify(entries));
  for (const [file, weight, family] of entries ?? []) {
    const path = join(FONT_DIR, file);
    check(`L1: ${lang} 個 ${file} 真係喺 public/fonts/og`, existsSync(path));
    if (!existsSync(path)) continue;
    const sha = createHash("sha256").update(readFileSync(path)).digest("hex");
    check(`L1: ${file} sha256 有寫落 ${COPY_REL}`, copySrc.includes(sha), sha);
    /* satori 只 register 400/600/700 —— 語言字體跳出呢三個數會靜靜跌返最近嗰個 face。 */
    check(`L1: ${file} weight ∈ {400,700}`, weight === 400 || weight === 700, String(weight));
    /* register 個名要真係喺 family 串入面，唔係 satori 當你冇載過（空位、冇 error）。 */
    check(`L1: ${file} 個 family 名寫咗落 SHARE_FONT_FAMILY.${lang}`,
      SHARE_FONT_FAMILY[lang].includes(family), `${family} 唔喺「${SHARE_FONT_FAMILY[lang]}」`);
  }
  /* family 串一定要**兩隻都寫**：淨寫 Noto 就連數字／$／% 都行返 Noto（同網站對唔上重心），
     淨寫 Inter 就等於冇加過字體（中文出空位）。Inter 要排頭，CJK 排後做 fallback。 */
  const family = SHARE_FONT_FAMILY[lang];
  check(`L1: ${lang} family 串 Inter 行頭、CJK 做 fallback`, /^Inter,\s*\S/.test(family), family);
}

/* ── L2：認得幾種寫法，認唔到跌返 en ────────────────────────────────────── */
for (const value of ["zh-CN", "zh-cn", "ZH-CN", "zh_CN", "zhcn", "cn", "zh", "zh-Hans", " zh-cn "]) {
  check(`L2: ${JSON.stringify(value)} → zh-CN`, readShareLang(value) === "zh-CN", `→ ${readShareLang(value)}`);
}
for (const value of ["zh-TW", "zh-tw", "ZH-TW", "zh_TW", "zhtw", "tw", "zh-Hant", "zh-HK"]) {
  check(`L2: ${JSON.stringify(value)} → zh-TW`, readShareLang(value) === "zh-TW", `→ ${readShareLang(value)}`);
}
for (const value of ["en", "EN", "", "  ", null, undefined, "ja", "ko", "fr", "post"]) {
  check(`L2: ${JSON.stringify(value)} → en`, readShareLang(value) === "en", `→ ${readShareLang(value)}`);
}
/* 冇地區碼嘅 `zh` 當簡體（CLDR：`zh` 預設 script 係 Hans）。要繁體就要講明。 */
check("L2: 淨係 zh 當簡體", readShareLang("zh") === "zh-CN", readShareLang("zh"));
/* ja / ko 特別點名：網站有日韓頁，但張圖冇 ship 日韓字體。跌返 en 出英文圖係啱嘅
   —— 唔准為咗「睇落有支援」而借中文字體出日韓，亦唔准出一堆空位。 */
for (const unshipped of ["ja", "ko"]) {
  check(`L2: ${unshipped} 未 ship 就唔准扮支援`, readShareLang(unshipped) === "en", readShareLang(unshipped));
}

/* ── L3：route 真係行呢張表（有檢查但零 call site = 冇檢查）─────────────── */
check("L3: route 行 readShareLang", /readShareLang\(query\.get\("lang"\)\)/.test(routeSrc));
check("L3: route 攞 copy", /shareCopy\(lang\)/.test(routeSrc));
check("L3: route 逐個語言載字體", /loadOgFonts\(lang\)/.test(routeSrc) && /SHARE_LANG_FONTS\[lang\]/.test(routeSrc));
check("L3: response 講返實際出咗邊個語言", /"x-og-lang":\s*lang/.test(routeSrc));
/* 三個 layout root div 都要寫 fontFamily —— 唔寫 satori 淨係用**第一隻** register 咗嘅
   face（Inter），中文就算載咗 Noto 都一樣出空位。 */
const familyUses = [...routeSrc.matchAll(/fontFamily:\s*copy\.fontFamily/g)].length;
check("L3: 三個 layout 都寫咗 fontFamily", familyUses === 3, `見到 ${familyUses} 個`);
/* 舊嗰批寫死嘅英文標籤要全部搬晒去 share-copy —— 留一個喺 route 就係中文圖入面
   夾硬有一行英文。 */
const routeCode = routeSrc.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
/* family 名一定要由 SHARE_LANG_FONTS 帶落嚟，唔准喺 route 度 hardcode —— 加語言照抄
   就會將 TC 掛住 SC 個名，satori 搵唔到，出空位又唔報錯。（掃 comment-stripped 嗰份：
   註解入面攞 family 名做例子係啱嘅，寫落 code 先係錯。） */
check("L3: route 冇 hardcode CJK family 名", !/"Noto Sans (?:SC|TC|JP|KR|HK)"/.test(routeCode),
  (routeCode.match(/"Noto Sans (?:SC|TC|JP|KR|HK)"/) || [])[0]);
for (const dead of ["PSA 10 MARKET CAP", "PSA 10 PRICE", "PSA 10 POP", "MARKET CAP", "AS OF ", "PRINT_LANGUAGE_EN", "RANKED "]) {
  const hits = routeCode.split("\n").filter((line) => line.includes(dead));
  check(`L3: route 冇再 hardcode「${dead}」`, hits.length === 0, hits.map((l) => l.trim()).join(" | "));
}
/* CJK 一個字闊過 latin 一倍：字級 ramp／clamp 全部係度住英文量返嚟嘅，直接數 `.length`
   就會俾中文卡名衝穿底 padding（見 PostLayout 個垂直預算）。 */
check("L3: clamp／ramp 行 displayWidth 唔係 .length", /function displayWidth\(/.test(routeSrc)
  && !/name\.length <= (?:20|30|34|60|96)/.test(routeSrc), "route 仲有度緊 name.length 嘅門檻");

/* ── L4：module guard 真係會炸 ──────────────────────────────────────────── */
{
  const dir = mkdtempSync(join(tmpdir(), "cardz-share-lang-guard-"));
  try {
    /* 加一個語言落 SHARE_LANGS、copy 同 family 都補齊，**淨係漏咗字體** ——
       呢個就係「圖出咗街但入面一格格空白」嗰個 case。TypeScript 捉唔到（key 齊晒）。 */
    const addLang = (src, fonts, family) => src
      .replace('export const SHARE_LANGS = ["en", "zh-CN", "zh-TW"] as const;', 'export const SHARE_LANGS = ["en", "zh-CN", "zh-TW", "ja"] as const;')
      .replace('  "zh-TW": [["NotoSansTC-Regular.otf", 400, "Noto Sans TC"], ["NotoSansTC-Bold.otf", 700, "Noto Sans TC"]],',
        `  "zh-TW": [["NotoSansTC-Regular.otf", 400, "Noto Sans TC"], ["NotoSansTC-Bold.otf", 700, "Noto Sans TC"]],\n  ja: ${fonts},`)
      .replace('  "zh-TW": "Inter, Noto Sans TC",', `  "zh-TW": "Inter, Noto Sans TC",\n  ja: ${JSON.stringify(family)},`)
      .replace('const COPY: Record<ShareLang, ShareCopy> = { en: EN, "zh-CN": ZH_CN, "zh-TW": ZH_TW };',
        'const COPY: Record<ShareLang, ShareCopy> = { en: EN, "zh-CN": ZH_CN, "zh-TW": ZH_TW, ja: ZH_CN };');
    const runProbe = async (src, name) => {
      const probe = join(dir, name);
      writeFileSync(probe, src, "utf8");
      try { await import(pathToFileURL(probe).href); return ""; } catch (error) { return String(error?.message ?? error); }
    };
    /* ① 加語言但完全冇 ship 字體 */
    const noFont = addLang(copySrc, "[]", "Inter");
    check("L4: 四處都改到（改咗 share-copy 寫法就要順手更新呢個 test）",
      noFont.includes("  ja: [],") && noFont.includes('ja: "Inter"') && noFont.includes("ja: ZH_CN };"));
    check("L4: 加語言但冇 CJK 字體會即刻炸",
      (await runProbe(noFont, "probe-nofont.ts")).includes("冇指定 CJK 字體"), "(乜都冇掟)");
    /* ② 有字體，但 register 個 family 名同 `SHARE_FONT_FAMILY` 對唔上 —— 抄上一行改檔名、
       family 名照抄，就係呢個。滿足到 ① 個 guard，但 satori 一樣搵唔到。 */
    const wrongName = addLang(copySrc, '[["NotoSansJP-Regular.otf", 400, "Noto Sans JP"]]', "Inter, Noto Sans SC");
    check("L4: family 名對唔上都要炸",
      (await runProbe(wrongName, "probe-wrongname.ts")).includes("family 名唔喺 SHARE_FONT_FAMILY"), "(乜都冇掟)");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

/* 順手驗返兩份 copy 真係唔同 —— 兩個語言指去同一份就係「出咗兩次英文圖」。 */
{
  const en = shareCopy("en");
  const zh = shareCopy("zh-CN");
  const tw = shareCopy("zh-TW");
  check("L5: 三個語言唔係同一份 copy", new Set([en.psa10Price, zh.psa10Price, tw.psa10Price]).size === 3,
    [en.psa10Price, zh.psa10Price, tw.psa10Price].join(" / "));
  check("L5: zh-CN 唔准 toUpperCase", zh.upper("ex 超級電擊") === "ex 超級電擊", zh.upper("ex 超級電擊"));
  check("L5: zh-TW 唔准 toUpperCase", tw.upper("ex 超級電擊") === "ex 超級電擊", tw.upper("ex 超級電擊"));
  const day = new Date(Date.UTC(2026, 7, 18));
  check("L5: zh-CN 日期係中文寫法", zh.shortDate(day) === "2026年8月18日", zh.shortDate(day));
  check("L5: zh-TW 日期係中文寫法", tw.shortDate(day) === "2026年8月18日", tw.shortDate(day));
  check("L5: en 日期冇變", en.shortDate(day) === "AUG 18, 2026", en.shortDate(day));
  check("L5: zh-CN 印刷語言出中文", zh.printLanguage("ja") === "日文版", String(zh.printLanguage("ja")));
  check("L5: zh-TW 印刷語言出繁體", tw.printLanguage("ko") === "韓文版", String(tw.printLanguage("ko")));
  check("L5: 認唔到嘅 TCG 照出原字（唔准亂 fallback）", zh.tcg("Yu-Gi-Oh!") === "Yu-Gi-Oh!", zh.tcg("Yu-Gi-Oh!"));
  /* 繁簡唔准照抄：`鑑定數量`（site-copy zhTW）vs `评级数量`（site-copy zhCN），
     TCG 名亦係 `寶可夢` vs `宝可梦`。抄錯就係「繁體頁出簡體字」。 */
  check("L5: zh-TW 用返 site-copy 個「鑑定數量」", tw.psa10Pop === "PSA 10 鑑定數量", tw.psa10Pop);
  check("L5: zh-CN 用返 site-copy 個「评级数量」", zh.psa10Pop === "PSA 10 评级数量", zh.psa10Pop);
  check("L5: 繁簡 TCG 名唔同", tw.tcg("Pokémon") === "寶可夢" && zh.tcg("Pokémon") === "宝可梦",
    `${tw.tcg("Pokémon")} vs ${zh.tcg("Pokémon")}`);
  /* 成份 zh-TW copy 唔准出現簡化字（抄 zh-CN 改一半嘅典型手尾）。 */
  const twText = [tw.marketCap, tw.psa10MarketCap, tw.psa10Price, tw.psa10Pop, tw.change("180D"),
    tw.priceWindow("180D"), tw.asOf("2026年8月18日"), tw.ranked("1,307", tw.tcg("Pokémon")),
    tw.printLanguage("zhCN"), tw.printLanguage("zhTW")].join("");
  const simplified = [...new Set([...twText].filter((ch) => "参价评级变数据简体贝宝梦贼韩".includes(ch)))];
  check("L5: zh-TW copy 冇混入簡化字", simplified.length === 0, simplified.join(""));
}

if (failed.length) {
  console.error(`FAIL OG lang (${failed.length}):\n` + failed.map((item) => ` - ${item}`).join("\n"));
  process.exit(1);
}
console.log(`PASS OG lang (${[...SHARE_LANGS].join("/")}：字體落磁碟、alias 認寬跌返 en、route 真係行、冇字體／family 名唔啱即刻炸)`);
