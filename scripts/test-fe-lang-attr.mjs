#!/usr/bin/env node
/*
 * FE05 `lang` 屬性契約（fe05(cjk)，2026-08-17）。
 *
 * globals.css 嘅 per-script 字體 stack 同 CJK 排版覆蓋，靠嘅係 `[lang]:lang(x)` —— 而 `:lang(zh-Hant)`
 * **唔會** match `lang="zh-TW"`。即係話：邊個地方寫錯一個 lang 值，成頁 CJK 就靜靜跌返 Latin token
 * （字距負、微標 10px、日文字形出喺中文頁），CI 一樣綠、截圖一樣「有字」。所以 lang 值要當契約守。
 *
 * 全站 locale → BCP47 有三個出口，呢個 test 逼佢哋三個同一個答案：
 *   ① lib/card-name.ts `htmlLang()`（server render 嘅 <html lang> 同卡名 lang）
 *   ② components/document-language.tsx 個 LangScript（paint 前喺瀏覽器改 <html lang> 嘅 inline string）
 *   ③ components/header.tsx `localeLang`（語言選單每個選項自己嘅 lang）
 * ② 係一串字，唔會有 type check —— 呢度真係 eval 佢，用 stub document/location 行五次。
 *
 * 由 run_all_tests.py 自動 glob 入 npm test。
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const WEB = join(ROOT, "apps/web/src");
const read = (p) => readFileSync(join(ROOT, p), "utf8");
const { htmlLang, displayCardName, displayCardNameLang, cardNameLangAttr } = await import(
  pathToFileURL(resolve(ROOT, "apps/web/src/lib/card-name.ts")).href
);

const failed = [];
const check = (label, condition, detail) => { if (!condition) failed.push(detail ? `${label}: ${detail}` : label); };

const LOCALES = ["en", "zh-TW", "zh-CN", "ja", "ko"];
const EXPECT = { en: "en", "zh-TW": "zh-Hant", "zh-CN": "zh-Hans", ja: "ja", ko: "ko" };
const ALLOWED = new Set(Object.values(EXPECT));

// ① htmlLang()：五個 locale 逐個對，輸出只准落喺五個 BCP47 值入面
for (const loc of LOCALES) check(`htmlLang(${loc})`, htmlLang(loc) === EXPECT[loc], `got ${htmlLang(loc)}`);
check("htmlLang range", LOCALES.every((l) => ALLOWED.has(htmlLang(l))));

// ② LangScript：由 document-language.tsx 抽返個 string 出嚟真係行，唔係讀住當佢啱
const docLangSrc = read("apps/web/src/components/document-language.tsx");
const scriptMatch = docLangSrc.match(/const langScript = `([\s\S]*?)`;/);
check("LangScript string found in document-language.tsx", !!scriptMatch);
if (scriptMatch) {
  const runScript = (search) => {
    const el = { lang: "" };
    const stubDoc = { documentElement: el };
    const stubLoc = { search };
    // eslint-disable-next-line no-new-func
    new Function("document", "location", "URLSearchParams", scriptMatch[1])(stubDoc, stubLoc, URLSearchParams);
    return el.lang;
  };
  for (const loc of LOCALES) {
    const got = runScript(`?lang=${encodeURIComponent(loc)}`);
    check(`LangScript ?lang=${loc}`, got === EXPECT[loc], `got ${got}`);
  }
  for (const bad of ["", "?lang=", "?lang=zh", "?lang=fr", "?currency=JPY"]) {
    const got = runScript(bad);
    check(`LangScript fallback "${bad}" → en`, got === "en", `got ${got}`);
  }
}

// ③ header.tsx 個語言選單：唔准自己抄一份對照表，一定要由 htmlLang() 出
const headerSrc = read("apps/web/src/components/header.tsx");
const localeLangBlock = headerSrc.match(/const localeLang[\s\S]*?\n};/);
check("header.tsx localeLang block found", !!localeLangBlock);
if (localeLangBlock) {
  const block = localeLangBlock[0];
  check("header.tsx localeLang derives from htmlLang()", (block.match(/htmlLang\(/g) || []).length === LOCALES.length,
    `htmlLang() calls = ${(block.match(/htmlLang\(/g) || []).length}`);
  check("header.tsx localeLang has no hardcoded BCP47", !/"zh-Hant"|"zh-Hans"/.test(block));
}

// ④ 全站 JSX：唔准出現 lang="zh-TW" / lang="zh-CN"，亦唔准 lang={locale}（會出 zh-TW，:lang(zh-Hant) 收唔到）
const tsxFiles = [];
(function walk(dir) {
  for (const entry of readdirSync(dir)) {
    const p = join(dir, entry);
    if (statSync(p).isDirectory()) walk(p);
    else if (/\.tsx?$/.test(p)) tsxFiles.push(p);
  }
})(WEB);
check("tsx files scanned", tsxFiles.length > 20, `${tsxFiles.length}`);

/* 註釋同 template literal 入面唔算數（LangScript 本身就係一串 `…lang="en"…`，
   card-name.ts 個註釋亦有講「唔准硬寫 lang="zh-TW"」）。用空格頂返走，行號同 index 唔變。 */
function stripNonCode(src) {
  const out = src.split("");
  const blank = (a, b) => { for (let i = a; i < b && i < out.length; i++) if (out[i] !== "\n") out[i] = " "; };
  let i = 0;
  while (i < src.length) {
    const two = src.slice(i, i + 2);
    if (two === "/*") { const end = src.indexOf("*/", i + 2); const e = end === -1 ? src.length : end + 2; blank(i, e); i = e; continue; }
    if (two === "//") { const end = src.indexOf("\n", i); const e = end === -1 ? src.length : end; blank(i, e); i = e; continue; }
    if (src[i] === "`") {
      let j = i + 1;
      while (j < src.length && !(src[j] === "`" && src[j - 1] !== "\\")) j++;
      blank(i, j + 1); i = j + 1; continue;
    }
    i++;
  }
  return out.join("");
}

const LANG_ATTR = /(?<![\w-])lang=(?:"([^"]*)"|\{([^}]*)\})/g;
const ALLOWED_EXPR = [/^cardNameLangAttr\(/, /^htmlLang\(/, /^optionLang\?\.\[/];
for (const file of tsxFiles) {
  const rel = relative(ROOT, file).replace(/\\/g, "/");
  const src = stripNonCode(readFileSync(file, "utf8"));
  for (const m of src.matchAll(LANG_ATTR)) {
    const line = src.slice(0, m.index).split("\n").length;
    const where = `${rel}:${line}`;
    if (m[1] !== undefined) {
      // 字面值：只准 layout.tsx 個 SSR 預設 lang="en"（LangScript 之後會改）
      check(`${where} literal lang="${m[1]}"`, m[1] === "en" && rel.endsWith("app/layout.tsx"),
        `唔准喺 JSX 硬寫 lang 值（zh-TW/zh-CN 尤其會令 :lang(zh-Hant) 收唔到）`);
    } else {
      const expr = m[2].trim();
      check(`${where} lang={${expr.slice(0, 40)}}`, ALLOWED_EXPR.some((re) => re.test(expr)),
        `只准 cardNameLangAttr() / htmlLang() / optionLang?.[]；lang={locale} 會出 zh-TW`);
    }
  }
}

// ⑤ 卡名 lang：同 displayCardName 完全同一條分支 —— 顯示嘅字係邊隻語言，屬性就標邊隻
const NAMES = {
  full: { officialName: "Van Gogh Pikachu 085/SVP", name: { en: "Van Gogh Pikachu", ja: "ゴッホのピカチュウ", ko: "반 고흐 피카츄", "zh-TW": "梵高皮卡丘", "zh-CN": "梵高皮卡丘" } },
  jaOnly: { officialName: "Umbreon VMAX 215/203", name: { en: "Umbreon VMAX", ja: "ブラッキーVMAX" } },
  noneLocalized: { officialName: "Charizard ex 199/165", name: { en: "Charizard ex" } },
  bareOfficial: { officialName: "Pikachu Promo 001", name: undefined },
  emptyString: { officialName: "Snorlax GX", name: { en: "Snorlax GX", ja: "" } },
};
for (const [key, card] of Object.entries(NAMES)) {
  for (const loc of LOCALES) {
    const shown = displayCardName(card, loc, "—");
    const lang = displayCardNameLang(card, loc);
    const localized = card.name?.[loc];
    const expectLang = loc !== "en" && localized ? htmlLang(loc) : "en";
    check(`${key}/${loc} displayCardNameLang`, lang === expectLang, `got ${lang}, shown "${shown}"`);
    // 顯示緊嘅字真係嗰隻語言：有譯名就等於譯名，冇就等於英文來源
    check(`${key}/${loc} lang matches shown text`,
      lang === "en" ? shown === (card.officialName || card.name?.en || "—") : shown === localized,
      `lang=${lang} shown="${shown}"`);
    const attr = cardNameLangAttr(card, loc);
    check(`${key}/${loc} attr omitted iff same as page lang`,
      attr === (lang === htmlLang(loc) ? undefined : lang), `attr=${String(attr)} lang=${lang} page=${htmlLang(loc)}`);
    if (attr !== undefined) check(`${key}/${loc} attr value in range`, ALLOWED.has(attr), String(attr));
  }
}
// 反例守門：CJK 頁 + 冇譯名 → 一定要出 lang="en"（呢條路真係行到 —— sitemap 1604 張卡有 155 張冇譯名）
for (const loc of ["ja", "ko", "zh-TW", "zh-CN"]) {
  check(`fallback emits lang="en" (${loc})`, cardNameLangAttr(NAMES.noneLocalized, loc) === "en",
    String(cardNameLangAttr(NAMES.noneLocalized, loc)));
  check(`localized emits no attr (${loc})`, cardNameLangAttr(NAMES.full, loc) === undefined,
    String(cardNameLangAttr(NAMES.full, loc)));
}

/* 個 lang="en" 屬性喺 CSS 上要真係有嘢做。CJK 頁嘅 ko keep-all 係**繼承**落嚟、CJK line-break: strict
   係按 tag/class 直接命中 <h1> / .mobile-card-name —— 兩條都唔會因為元素自己標咗 lang="en" 而唔 match。
   所以 globals.css 一定要有明寫嘅解除規則，而且要排喺嗰兩條之後（specificity 打和，靠 source order 贏）。
   冇呢條 = card-name.ts 個註釋同 DESIGN.md §1.3.3 講緊一個唔存在嘅 gate。 */
const globals = readFileSync(resolve(ROOT, "apps/web/src/app/globals.css"), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
const optOutIdx = globals.indexOf('[lang="en"]');
check('globals.css 有 CJK 頁 [lang="en"] 斷行解除規則', optOutIdx > 0);
if (optOutIdx > 0) {
  const rule = globals.slice(optOutIdx, globals.indexOf("}", optOutIdx) + 1);
  check('[lang="en"] 解除 word-break', /word-break:\s*normal/.test(rule), rule.slice(0, 120));
  check('[lang="en"] 解除 line-break', /line-break:\s*auto/.test(rule), rule.slice(0, 120));
  check('[lang="en"] 解除規則排喺 keep-all / line-break: strict 之後', optOutIdx > globals.lastIndexOf("line-break: strict"),
    `optOut@${optOutIdx} strict@${globals.lastIndexOf("line-break: strict")}`);
}

/* ⑥ 日文和欧混植：ja 文案入面拉丁／數字同漢字假名之間要留半形空格（`PSA 10`、`未開封 BOX`、
   `BOX 市場`、`トップ 100`）。呢個規則之前只做咗 i18n.ts + site-copy.ts，hub-copy.ts（63 處）同
   related-cards.ts（12 處）漏咗成個月都冇人發現 —— 因為當時只有 DESIGN.md 一句文，冇 call site。
   四個檔全部係 live ja 文案，一齊守。註釋唔算（全 repo 剩低嘅命中都係廣東話註釋，見 types.ts:115）。
   ⚠️ 只掃已知四個 pattern，唔做「漢字貼住拉丁就紅」嘅通用偵測 —— `101位以降` 係故意保留嘅正常寫法。 */
function stripComments(src) {
  let out = "", i = 0, q = null;
  while (i < src.length) {
    const c = src[i];
    if (q) { if (c === "\\") { out += src.slice(i, i + 2); i += 2; continue; } if (c === q) q = null; out += c; i++; continue; }
    if (c === '"' || c === "'" || c === "`") { q = c; out += c; i++; continue; }
    if (c === "/" && src[i + 1] === "*") { const e = src.indexOf("*/", i + 2); const end = e === -1 ? src.length : e + 2; out += src.slice(i, end).replace(/[^\n]/g, " "); i = end; continue; }
    if (c === "/" && src[i + 1] === "/") { const e = src.indexOf("\n", i); const end = e === -1 ? src.length : e; out += " ".repeat(end - i); i = end; continue; }
    out += c; i++;
  }
  return out;
}
const JA_COPY_FILES = ["apps/web/src/lib/i18n.ts", "apps/web/src/lib/site-copy.ts", "apps/web/src/lib/hub-copy.ts", "apps/web/src/lib/related-cards.ts"];
const JA_SPACING = /PSA10|未開封BOX|BOX市場|トップ100/g;
let jaScanned = 0;
for (const rel of JA_COPY_FILES) {
  const code = stripComments(read(rel));
  jaScanned++;
  for (const m of code.matchAll(JA_SPACING)) {
    const line = code.slice(0, m.index).split("\n").length;
    check(`${rel}:${line} ja 和欧混植`, false, `"${m[0]}" 要留半形空格（PSA 10 / 未開封 BOX / BOX 市場 / トップ 100）`);
  }
}
check("ja 文案檔全部掃到", jaScanned === JA_COPY_FILES.length, `${jaScanned}`);

if (failed.length) {
  console.error(`FAIL test-fe-lang-attr (${failed.length}):\n - ` + failed.slice(0, 30).join("\n - ") + (failed.length > 30 ? `\n - … +${failed.length - 30}` : ""));
  process.exit(1);
}
console.log(`PASS test-fe-lang-attr (htmlLang × ${LOCALES.length}, LangScript eval × ${LOCALES.length + 5}, header localeLang, ${tsxFiles.length} tsx lang= 屬性, 卡名 ${Object.keys(NAMES).length} × ${LOCALES.length} 分支, [lang="en"] 斷行解除規則, ${JA_COPY_FILES.length} 個 ja 文案檔和欧混植)`);
