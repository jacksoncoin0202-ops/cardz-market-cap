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
 *  ⑥ OG（satori）三個 static TTF 齊、sha256 對得返 route.tsx 檔頭、係真 sfnt，而且 OG layout 冇用過
 *     400/600/700 以外嘅 fontWeight（satori 唔合成字重，跳出去就靜靜跌返最近嗰個 face）
 *  ⑦ `share-copy.ts` `SHARE_LANG_FONTS` 每個語言字體檔真係喺 public/fonts/og、sha256 對得返
 *     嗰度檔頭抄低嗰個、係真 sfnt。呢條就係「CJK 出空位」嗰個 failure mode 嘅執行點：
 *     餵 CJK 落一個唔存在／換咗版嘅字體，satori 出嘅係空位 —— 唔報錯、唔 log、照出 200。
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
/* ⑥ OG（satori）字體。**唔准寫成 `if (existsSync(ogDir))`** —— 咁樣人哋一 `rm -rf` 個 folder，
   成段 check 就靜靜消失，route 跌返 satori 內置 Geist，PNG 同網頁字體again 唔同而 CI 全綠。
   所以由 route.tsx **有冇 reference `public/fonts/og`** 反推：有 reference = 檔案必須齊。 */
const OG_ROUTE = "apps/web/src/app/api/og/card/[id]/route.tsx";
const ogRoute = read(OG_ROUTE);
const ogDir = join(ROOT, "apps/web/public/fonts/og");
if (ogRoute.includes("public/fonts/og")) {
  check("public/fonts/og/OFL.txt present (OG TTF 都要傍住 license)", existsSync(join(ogDir, "OFL.txt")));
  // 三個檔要齊，而且 sha256 對得返 route.tsx 檔頭抄低嗰個（satori 唔食 woff2／variable，只可以 static TTF）
  for (const [file, weight] of [["Inter-Regular.ttf", 400], ["Inter-SemiBold.ttf", 600], ["Inter-Bold.ttf", 700]]) {
    const path = join(ogDir, file);
    check(`public/fonts/og/${file} present`, existsSync(path));
    if (!existsSync(path)) continue;
    const sha = createHash("sha256").update(readFileSync(path)).digest("hex");
    const declared = (ogRoute.match(new RegExp(`${file}\\s+w${weight}\\s+[\\d,]+ bytes\\s+sha256\\s+([0-9a-f]{64})`)) || [])[1];
    check(`${file} sha256 對得返 route.tsx 檔頭`, declared === sha, `file=${sha} declared=${declared}`);
    check(`${file} 係真 TTF（sfnt 00010000）`, readFileSync(path).readUInt32BE(0) === 0x00010000);
  }
  // satori 唔會合成字重：register 咗 400/600/700，layout 就一個 weight 都唔准跳出呢三個數
  const OG_WEIGHTS = new Set(["400", "600", "700"]);
  const badWeights = [...ogRoute.matchAll(/fontWeight:\s*(\d+)/g)].map((m) => m[1]).filter((w) => !OG_WEIGHTS.has(w));
  check("OG layout 只用 register 咗嘅 400/600/700", badWeights.length === 0, `見到 ${[...new Set(badWeights)].join(",")}`);

  /* ⑦ 語言字體（CJK）。同 ⑥ 一樣由**宣告**反推：`SHARE_LANG_FONTS` 講咗要邊個檔，
     嗰個檔就必須喺磁碟、必須係宣告嗰個版本。少一隻 = 該語言成張圖變一格格空白，
     而 satori 唔會報錯 —— 呢條 check 就係嗰個 failure mode 唯一會嗌嘅地方。 */
  const shareCopySrc = read("apps/web/src/lib/share-copy.ts");
  const langFonts = [...shareCopySrc.matchAll(/\["([\w.-]+\.(?:otf|ttf))",\s*(\d{3})\]/g)].map((m) => [m[1], Number(m[2])]);
  check("share-copy.ts 讀得到 SHARE_LANG_FONTS 個清單（改咗寫法就要更新呢個 test）", langFonts.length > 0,
    `搵到 ${langFonts.length} 個`);
  /* OFL 1.1 §2：派 binary 就要同時派 licence + **嗰隻字體自己嘅版權聲明**。傍住 Inter 嗰份
     OFL.txt 入面寫嘅係 Inter Project Authors，唔 cover Noto，所以要各有各嗰份。 */
  if (langFonts.length > 0) {
    check("public/fonts/og/OFL-NotoSansSC.txt present（Noto 唔 cover 喺 Inter 嗰份 OFL.txt）",
      existsSync(join(ogDir, "OFL-NotoSansSC.txt")));
  }
  for (const [file, weight] of langFonts) {
    const path = join(ogDir, file);
    check(`public/fonts/og/${file} present`, existsSync(path));
    if (!existsSync(path)) continue;
    const bytes = readFileSync(path);
    const sha = createHash("sha256").update(bytes).digest("hex");
    /* 唔用 `new RegExp` 拼字串 —— 一路 escape 落去 template literal 度 `\s` 會俾當成 `s`，
       個 check 就會永遠「檔頭冇寫」噉樣自己綠／自己紅。認行、再喺行入面攞 sha 直接啲。 */
    const note = shareCopySrc.split("\n").find((line) => line.includes(file) && line.includes(`w${weight}`));
    const declared = (note?.match(/sha256 ([0-9a-f]{64})/) || [])[1];
    check(`${file} 檔頭有寫低 bytes 數`, note?.includes(`${bytes.length.toLocaleString("en-US")} bytes`) ?? false,
      `磁碟 ${bytes.length} bytes，檔頭嗰行：${note?.trim() ?? "(搵唔到)"}`);
    check(`${file} sha256 對得返 share-copy.ts 檔頭`, declared === sha, `file=${sha} declared=${declared ?? "(檔頭冇寫)"}`);
    /* OTF = `OTTO`（CFF outline），TTF = 0x00010000。satori 兩種都食，woff2 唔食。 */
    const magic = bytes.readUInt32BE(0);
    check(`${file} 係真 sfnt（OTTO 或 00010000，唔係 woff2）`, magic === 0x4f54544f || magic === 0x00010000,
      `magic=0x${magic.toString(16)}`);
  }
}

if (failed.length) {
  console.error("FAIL FE05 font contract:\n" + failed.map((item) => ` - ${item}`).join("\n"));
  process.exit(1);
}
console.log(`PASS FE05 font contract (next/font single source, no external font host, --f-latin = Inter, ${fontSansDecls.length} --font-sans decls lead with --f-latin, 4 [lang]:lang() stacks re-declare font-family, variable on <html>, OFL + sha256, OG 3 TTF sha256 + weight ⊆ {400,600,700}, SHARE_LANG_FONTS 語言字體 sha256)`);
