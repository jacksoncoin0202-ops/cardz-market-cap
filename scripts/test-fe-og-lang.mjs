#!/usr/bin/env node
/*
 * OG 分享圖語言（`?lang=`）—— 純靜態，唔使 dev server，由 run_all_tests.py glob 入 npm test。
 *
 * owner 2026-08-20：「X.com 我哋有 CARDZGame 中文簡體，所以你中文都要出多一次。」
 * 即係同一張卡要出兩次圖：英文一張、簡體一張。
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
check("L1: 兩個語言（en + zh-CN）", [...SHARE_LANGS].join() === "en,zh-CN", [...SHARE_LANGS].join());
for (const lang of SHARE_LANGS) {
  const entries = SHARE_LANG_FONTS[lang];
  if (lang === "en") {
    check("L1: en 唔使額外字體（Inter 夠）", entries === null, JSON.stringify(entries));
    continue;
  }
  check(`L1: ${lang} 有指定字體`, Array.isArray(entries) && entries.length > 0, JSON.stringify(entries));
  for (const [file, weight] of entries ?? []) {
    const path = join(FONT_DIR, file);
    check(`L1: ${lang} 個 ${file} 真係喺 public/fonts/og`, existsSync(path));
    if (!existsSync(path)) continue;
    const sha = createHash("sha256").update(readFileSync(path)).digest("hex");
    check(`L1: ${file} sha256 有寫落 ${COPY_REL}`, copySrc.includes(sha), sha);
    /* satori 只 register 400/600/700 —— 語言字體跳出呢三個數會靜靜跌返最近嗰個 face。 */
    check(`L1: ${file} weight ∈ {400,700}`, weight === 400 || weight === 700, String(weight));
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
for (const value of ["en", "EN", "", "  ", null, undefined, "zh-TW", "ja", "ko", "fr", "post"]) {
  check(`L2: ${JSON.stringify(value)} → en`, readShareLang(value) === "en", `→ ${readShareLang(value)}`);
}
/* zh-TW 特別點名：網站有繁體，但張圖冇 ship 繁體字體。跌返 en 出英文圖係啱嘅
   —— 唔准為咗「睇落有支援」而用簡體字體出繁體，亦唔准出一堆空位。 */
check("L2: zh-TW 未 ship 就唔准扮支援", readShareLang("zh-TW") === "en", readShareLang("zh-TW"));

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
    const mutated = copySrc
      .replace('export const SHARE_LANGS = ["en", "zh-CN"] as const;', 'export const SHARE_LANGS = ["en", "zh-CN", "zh-TW"] as const;')
      .replace('  "zh-CN": [["NotoSansSC-Regular.otf", 400], ["NotoSansSC-Bold.otf", 700]],',
        '  "zh-CN": [["NotoSansSC-Regular.otf", 400], ["NotoSansSC-Bold.otf", 700]],\n  "zh-TW": [],')
      .replace('  "zh-CN": "Inter, Noto Sans SC",', '  "zh-CN": "Inter, Noto Sans SC",\n  "zh-TW": "Inter",')
      .replace('const COPY: Record<ShareLang, ShareCopy> = { en: EN, "zh-CN": ZH_CN };',
        'const COPY: Record<ShareLang, ShareCopy> = { en: EN, "zh-CN": ZH_CN, "zh-TW": ZH_CN };');
    check("L4: 四處都改到（改咗 share-copy 寫法就要順手更新呢個 test）",
      mutated.includes('"zh-TW"') && mutated.includes('"zh-TW": []') && mutated.includes('"zh-TW": "Inter"'));
    const probe = join(dir, "share-copy.probe.ts");
    writeFileSync(probe, mutated, "utf8");
    let message = "";
    try { await import(pathToFileURL(probe).href); } catch (error) { message = String(error?.message ?? error); }
    check("L4: 加語言但冇 CJK 字體會即刻炸", message.includes("冇指定 CJK 字體"), `掟嘅係：${message || "(乜都冇掟)"}`);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

/* 順手驗返兩份 copy 真係唔同 —— 兩個語言指去同一份就係「出咗兩次英文圖」。 */
{
  const en = shareCopy("en");
  const zh = shareCopy("zh-CN");
  check("L5: 兩個語言唔係同一份 copy", en.psa10Price !== zh.psa10Price, `${en.psa10Price} vs ${zh.psa10Price}`);
  check("L5: zh-CN 唔准 toUpperCase", zh.upper("ex 超級電擊") === "ex 超級電擊", zh.upper("ex 超級電擊"));
  const day = new Date(Date.UTC(2026, 7, 18));
  check("L5: zh-CN 日期係中文寫法", zh.shortDate(day) === "2026年8月18日", zh.shortDate(day));
  check("L5: en 日期冇變", en.shortDate(day) === "AUG 18, 2026", en.shortDate(day));
  check("L5: zh-CN 印刷語言出中文", zh.printLanguage("ja") === "日文版", String(zh.printLanguage("ja")));
  check("L5: 認唔到嘅 TCG 照出原字（唔准亂 fallback）", zh.tcg("Yu-Gi-Oh!") === "Yu-Gi-Oh!", zh.tcg("Yu-Gi-Oh!"));
}

if (failed.length) {
  console.error(`FAIL OG lang (${failed.length}):\n` + failed.map((item) => ` - ${item}`).join("\n"));
  process.exit(1);
}
console.log(`PASS OG lang (${[...SHARE_LANGS].join("/")}：字體落磁碟、alias 認寬跌返 en、route 真係行、冇字體即刻炸)`);
