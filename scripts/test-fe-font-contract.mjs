#!/usr/bin/env node
/*
 * FE05 webfont 契約（DESIGN.md §1.3.1）—— 純靜態、唔使 dev server，由 run_all_tests.py glob 入 npm test。
 *
 * 守嘅係五件「一錯就靜靜全站死、CI 照綠」嘅事：
 *  ① 全 apps/web/src 只准有一個 next/font 呼叫（src/fonts/index.ts）—— 唔准第二隻字體 / display face
 *  ② 零手寫 @font-face、零 fonts.googleapis / fonts.gstatic（CSP font-src 'self'；webhook build 唔准出外網）
 *  ③ globals.css --f-latin = var(--font-inter)（唔准硬寫 "Inter"，next/font 出嘅係 hash family）；每條 --font-sans 宣告
 *     （:root 默認 + 四條 [lang]:lang() 覆蓋）第一項都係 var(--f-latin)、覆蓋條條再寫 font-family、第二項係自己 script token
 *  ④ layout.tsx 個 inter.variable 必須落 <html>，唔係 <body>：--font-sans 住喺 :root，
 *     --font-inter 淨係喺 body 定義嘅話 :root 度 var() 解唔到 → 整條 --font-sans invalid → 全站跌 serif
 *  ⑤ 兩處 binary（src/fonts、public/fonts/og）各自傍住一份 OFL.txt（OFL 1.1 派發義務）；woff2 sha256 對得返 index.ts 寫嗰個
 */
import { createHash } from "node:crypto";
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const SRC = join(ROOT, "apps/web/src");
const failed = [];
const check = (label, condition, detail) => { if (!condition) failed.push(detail ? `${label}: ${detail}` : label); };
const read = (path) => readFileSync(join(ROOT, path), "utf8");

function walk(dir, out = []) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) walk(p, out);
    else if (/\.(tsx?|css|mjs|js)$/.test(name)) out.push(p);
  }
  return out;
}
const files = walk(SRC);
const rel = (p) => relative(ROOT, p).replace(/\\/g, "/");

// ① 只准一個 next/font 呼叫，而且係 src/fonts/index.ts
const nextFontFiles = files.filter((p) => /from\s+["']next\/font/.test(readFileSync(p, "utf8")));
check("exactly one next/font import", nextFontFiles.length === 1 && rel(nextFontFiles[0]) === "apps/web/src/fonts/index.ts",
  `found in: ${nextFontFiles.map(rel).join(", ") || "(none)"}`);
const fontsIndex = read("apps/web/src/fonts/index.ts");
check("single localFont() call", (fontsIndex.match(/localFont\(/g) || []).length === 1);
check("localFont exposes --font-inter", /variable:\s*"--font-inter"/.test(fontsIndex));
check("localFont display swap (optional 會令第一次訪問照睇 fallback，修唔到投訴)", /display:\s*"swap"/.test(fontsIndex));
check("localFont has metric fallback", /adjustFontFallback:\s*"Arial"/.test(fontsIndex));

// ② 零 @font-face / 外部字體 host
for (const p of files) {
  const text = readFileSync(p, "utf8");
  check(`no hand-written @font-face in ${rel(p)}`, !/@font-face\s*\{/.test(text));
  check(`no external font host in ${rel(p)}`, !/fonts\.googleapis\.com|fonts\.gstatic\.com/.test(text));
}

// ③ 字體 stack 鏈（fe05(cjk) 起 --font-sans 由 per-script token 砌）：
//    --f-latin = var(--font-inter)；每一條 --font-sans 宣告（:root 默認 + 每個 [lang]:lang() 覆蓋）第一項都係 var(--f-latin)；
//    每條 [lang]:lang(x) 覆蓋一定要同時再寫 font-family: var(--font-sans)（body 只 compute 一次 family，後代 <span lang> 繼承 computed，
//    淨改 token 唔會生效）並且第二項係自己 script 嘅 token（ja→--f-jp、zh-Hant→--f-tc、zh-Hans→--f-sc、ko→--f-kr）。
const globals = read("apps/web/src/app/globals.css");
const globalsFlat = globals.replace(/\/\*[\s\S]*?\*\//g, ""); // 剝走 comment，免得註釋入面嘅例子當真
const fLatin = globalsFlat.match(/^\s*--f-latin:\s*([^;]+);/m);
check("--f-latin defined in globals.css", Boolean(fLatin));
check("--f-latin = var(--font-inter)", Boolean(fLatin) && fLatin[1].trim() === "var(--font-inter)", fLatin ? fLatin[1].trim() : "");
/* 每個 --f-* token 都要真係宣告過。CSS custom property 解唔到（`var(--f-jp)` 冇對應宣告）
   會令**成條** --font-sans 變 guaranteed-invalid → font-family 跌返 initial serif，五語言一齊中，
   而 build / tsc / 呢個 test 原本全部照綠。所以：引用集合 ⊆ 宣告集合，而且冇死 token。 */
const fTokenDecls = new Map();
for (const m of globalsFlat.matchAll(/(--f-[a-z0-9-]+)\s*:\s*([^;]+);/g)) {
  fTokenDecls.set(m[1], (fTokenDecls.get(m[1]) || 0) + 1);
  check(`${m[1]} declaration non-empty`, m[2].trim().length > 0);
}
const fTokenRefs = new Set([...globalsFlat.matchAll(/var\((--f-[a-z0-9-]+)\)/g)].map((m) => m[1]));
check("--f-* tokens declared", fTokenDecls.size >= 6, `declared: ${[...fTokenDecls.keys()].join(" ") || "(none)"}`);
for (const ref of fTokenRefs) {
  check(`var(${ref}) has a declaration`, fTokenDecls.has(ref), `引用咗但冇宣告 → 成條 --font-sans invalid → 全站 serif`);
}
for (const [tok, n] of fTokenDecls) {
  check(`${tok} declared exactly once`, n === 1, `${n} 次`);
  check(`${tok} actually used`, fTokenRefs.has(tok), "死 token");
}

const fontSansDecls = [...globalsFlat.matchAll(/--font-sans:\s*([^;]+);/g)].map((m) => m[1].trim());
check("--font-sans defined in globals.css", fontSansDecls.length >= 1);
for (const decl of fontSansDecls) {
  check("every --font-sans declaration leads with var(--f-latin)", /^var\(--f-latin\)\s*,/.test(decl), decl.slice(0, 60));
}
/* ⚠️ 上面個 `>= 1` 自己一個係空洞：四條 [lang]:lang() 覆蓋已經夠數，刪咗 :root 條默認一樣照綠，
   而 lang="en" 嘅英文站冇任何規則 set --font-sans → body{font-family:var(--font-sans)} computed-value
   time invalid → 全站 UA serif。所以要分開驗「:root 有默認」同「body 真係用咗佢」。 */
const rootBlock = globalsFlat.match(/(^|\})\s*:root\s*\{([\s\S]*?)\}/);
check(":root block present in globals.css", Boolean(rootBlock));
check(":root declares the default --font-sans (lang=en 冇 :lang() 覆蓋，冇佢 = 全站 serif)",
  Boolean(rootBlock) && /--font-sans:\s*var\(--f-latin\)\s*,/.test(rootBlock[2]));
const bodyBlock = globalsFlat.match(/(^|\})\s*body\s*\{([\s\S]*?)\}/);
check("body block present in globals.css", Boolean(bodyBlock));
check("body { font-family: var(--font-sans) }（冇佢 = --font-sans 冇 call site，全站跌 UA 字體）",
  Boolean(bodyBlock) && /font-family:\s*var\(--font-sans\)\s*;/.test(bodyBlock[2]));

/* ⑥ CJK letter-spacing 收編掃全部 CSS：globals.css 嗰句「其餘硬寫嘅 letter-spacing 全部 |≤ 0.04em|」
   要係真嘅。凡係硬寫 > 0.04em 嘅 selector，一定要出現喺 CJK 收編規則個 :is() 名單入面，
   否則 ja/ko/zh 會食住 0.05–0.1em tracking（漢字／假名之間拉開，睇落散）。 */
const cssFiles = files.filter((p) => p.endsWith(".css"));
/* selector 入面有 nested `:lang(ja)` 括號，regex 夾唔住 —— 由 declaration 位置行返轉頭切 selector，
   再攞最後一個 :is(…) 入面嗰批（第一個係語言條件，第二個先係被收編嘅 label 名單）。 */
const trackIdx = globalsFlat.indexOf("letter-spacing: var(--track-kicker)");
let covered = [];
if (trackIdx > 0) {
  const braceIdx = globalsFlat.lastIndexOf("{", trackIdx);
  const selector = globalsFlat.slice(globalsFlat.lastIndexOf("}", braceIdx) + 1, braceIdx).trim();
  const lastIs = selector.lastIndexOf(":is(");
  if (lastIs >= 0) covered = selector.slice(lastIs + 4, selector.lastIndexOf(")")).split(",").map((s) => s.trim());
}
check("CJK letter-spacing 收編規則存在", covered.length > 0);
for (const p of cssFiles) {
  const text = readFileSync(p, "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
  for (const m of text.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    const ls = m[2].match(/letter-spacing:\s*(-?[\d.]+)em\s*;/);
    if (!ls || Math.abs(parseFloat(ls[1])) <= 0.04) continue;
    const selector = m[1].split("\n").pop().trim();
    check(`hardcoded letter-spacing ${ls[1]}em on \`${selector}\` (${rel(p)}) 有落 CJK 收編名單`,
      covered.some((c) => selector.includes(c)), `名單: ${covered.join(", ")}`);
  }
}
const langStacks = { ja: "--f-jp", "zh-Hant": "--f-tc", "zh-Hans": "--f-sc", ko: "--f-kr" };
for (const [lang, token] of Object.entries(langStacks)) {
  const rule = globalsFlat.match(new RegExp(`\\[lang\\]:lang\\(${lang}\\)\\s*\\{([^}]*)\\}`));
  check(`[lang]:lang(${lang}) stack rule present`, Boolean(rule));
  if (!rule) continue;
  const body = rule[1];
  const sans = body.match(/--font-sans:\s*([^;]+);/);
  check(`[lang]:lang(${lang}) puts ${token} second`, Boolean(sans) && new RegExp(`^var\\(--f-latin\\)\\s*,\\s*var\\(${token}\\)\\s*,`).test(sans[1].trim()), sans ? sans[1].trim().slice(0, 60) : "(no --font-sans)");
  check(`[lang]:lang(${lang}) re-declares font-family: var(--font-sans)`, /font-family:\s*var\(--font-sans\)\s*;/.test(body));
}
check("no hardcoded \"Inter\" family in any font-family / --font-* declaration",
  !files.some((p) => /(?:font-family|--font-[a-z-]+|font)\s*:[^;{}\n]*(?:["']Inter["']|,\s*Inter\s*[,;])/.test(readFileSync(p, "utf8"))));

// ④ inter.variable 落 <html>
const layout = read("apps/web/src/app/layout.tsx");
check("layout imports inter from @/fonts", /import\s*\{\s*inter\s*\}\s*from\s*"@\/fonts"/.test(layout));
check("inter.variable on <html>", /<html[^>]*className=\{inter\.variable\}/.test(layout));
check("inter.variable NOT on <body>", !/<body[^>]*inter\.variable/.test(layout));

// ⑤ OFL + sha256
check("src/fonts/OFL.txt present", existsSync(join(SRC, "fonts/OFL.txt")));
const woff2 = join(SRC, "fonts/InterVariable-latin.woff2");
check("InterVariable-latin.woff2 present", existsSync(woff2));
if (existsSync(woff2)) {
  const sha = createHash("sha256").update(readFileSync(woff2)).digest("hex");
  const declared = (fontsIndex.match(/sha256\s+([0-9a-f]{64})/) || [])[1];
  check("woff2 sha256 matches index.ts note", declared === sha, `file=${sha} declared=${declared}`);
  const size = statSync(woff2).size;
  check("woff2 ≤ 60 kB (DESIGN.md §4.1 預算)", size <= 60 * 1024, `${size} bytes`);
}
const ogDir = join(ROOT, "apps/web/public/fonts/og");
if (existsSync(ogDir)) {
  check("public/fonts/og/OFL.txt present (OG TTF 都要傍住 license)", existsSync(join(ogDir, "OFL.txt")));
}

if (failed.length) {
  console.error("FAIL FE05 font contract:\n" + failed.map((item) => ` - ${item}`).join("\n"));
  process.exit(1);
}
console.log(`PASS FE05 font contract (next/font single source, no external font host, --f-latin = Inter, ${fontSansDecls.length} --font-sans decls lead with --f-latin, 4 [lang]:lang() stacks re-declare font-family, variable on <html>, OFL + sha256)`);
