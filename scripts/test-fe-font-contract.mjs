#!/usr/bin/env node
/*
 * FE05 webfont 契約（DESIGN.md §1.3.1）—— 純靜態、唔使 dev server，由 run_all_tests.py glob 入 npm test。
 *
 * 守嘅係五件「一錯就靜靜全站死、CI 照綠」嘅事：
 *  ① 全 apps/web/src 只准有一個 next/font 呼叫（src/fonts/index.ts）—— 唔准第二隻字體 / display face
 *  ② 零手寫 @font-face、零 fonts.googleapis / fonts.gstatic（CSP font-src 'self'；webhook build 唔准出外網）
 *  ③ globals.css --font-sans 第一項必須係 var(--font-inter)（唔准硬寫 "Inter"，next/font 出嘅係 hash family）
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

// ③ --font-sans 第一項 = var(--font-inter)
const globals = read("apps/web/src/app/globals.css");
const fontSans = globals.match(/^\s*--font-sans:\s*([^;]+);/m);
check("--font-sans defined in globals.css", Boolean(fontSans));
check("--font-sans leads with var(--font-inter)", Boolean(fontSans) && /^var\(--font-inter\)\s*,/.test(fontSans[1].trim()), fontSans ? fontSans[1].trim().slice(0, 60) : "");
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
console.log("PASS FE05 font contract (next/font single source, no external font host, --font-sans leads with Inter, variable on <html>, OFL + sha256)");
