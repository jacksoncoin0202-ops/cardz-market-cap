#!/usr/bin/env node
/*
 * 桌面榜單表頭真係黐頂（FE05 A1）。
 *
 * 根因：`.desktop-ranking-table { overflow: hidden }` 令個框變 scroll container，
 * `thead th { position: sticky; top: 0 }` 就只係對住個框黐 —— 框本身唔會捲，表頭跟住成張表碌走
 * （2026-09-23 線上實測：1440 闊碌入表 1500px，th top = -1499）。就算黐到，top: 0 都會匿喺
 * sticky 嘅 site header 同 .ranking-heading 後面。
 *
 * 修法：框改 `overflow: clip`（一樣裁圓角，唔係 scroll container）；th 釘喺
 * `--header-height + --ranking-heading-h`，後者由 lib/use-ranking-heading-height.ts 量標題實高寫落 section。
 * scroll-restoration 揀 anchor 要連表頭遮住嗰截都避開。
 *
 * 全部係 source 契約（sticky 要 Next + browser 先行得）。run_all_tests.py glob `scripts/test-*.mjs`。
 */
import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const read = (rel) => readFileSync(join(ROOT, rel), "utf8");
const failed = [];
const check = (label, condition, detail = "") => {
  if (!condition) failed.push(detail ? `${label} —— ${detail}` : label);
};

/* 逐條 rule：{ selectors, body, atRules }。注釋先剝（有啲註寫咗 `{…}`），at-rule 用 stack 記住。 */
function cssRules(raw) {
  const css = raw.replace(/\/\*[\s\S]*?\*\//g, "");
  const rules = [];
  const stack = [];
  let buf = "";
  for (let i = 0; i < css.length; i++) {
    const ch = css[i];
    if (ch === "{") {
      const head = buf.trim();
      buf = "";
      if (head.startsWith("@")) { stack.push(head); continue; }
      const end = css.indexOf("}", i);
      rules.push({ selectors: head.split(",").map((s) => s.trim()), body: css.slice(i + 1, end), atRules: [...stack] });
      i = end;
    } else if (ch === "}") {
      stack.pop();
      buf = "";
    } else if (ch === ";" && !buf.includes("{")) {
      buf = "";
    } else {
      buf += ch;
    }
  }
  return rules;
}
/* 同一個 property 寫兩次（fallback）→ 攞最後嗰個，同 browser 一樣 */
const decl = (body, prop) => {
  const all = [...body.matchAll(new RegExp(`(?:^|[;\\s])${prop}\\s*:\\s*([^;]+);`, "g"))].map((m) => m[1].trim());
  return all.length ? all[all.length - 1] : null;
};
const declAll = (body, prop) => [...body.matchAll(new RegExp(`(?:^|[;\\s])${prop}\\s*:\\s*([^;]+);`, "g"))].map((m) => m[1].trim());

const rules = cssRules(read("apps/web/src/app/globals.css"));
const baseRule = (selector) => rules.find((r) => !r.atRules.length && r.selectors.includes(selector));

/* ① 框：clip 係生效值，hidden 只准做舊 Safari fallback（寫喺 clip 前面） */
const wrap = baseRule(".desktop-ranking-table");
check("有 .desktop-ranking-table base rule", Boolean(wrap));
if (wrap) {
  check("框 overflow 生效值 = clip", decl(wrap.body, "overflow") === "clip", `而家係 ${decl(wrap.body, "overflow")}`);
  check("框 overflow 只係 hidden → clip 兩行", declAll(wrap.body, "overflow").join(" → ") === "hidden → clip", declAll(wrap.body, "overflow").join(" → "));
}
/* 任何 media 都唔准再將個框（或者包住佢嘅 section）改返做 scroll container */
const SCROLLER = /^(hidden|auto|scroll)\b/;
for (const r of rules) {
  const hits = r.selectors.filter((s) => s === ".desktop-ranking-table" || s === ".rankings-section");
  if (!hits.length) continue;
  for (const prop of ["overflow", "overflow-y", "overflow-x"]) {
    for (const value of declAll(r.body, prop)) {
      if (r === wrap && prop === "overflow" && value === "hidden") continue;
      check(`${hits.join(", ")} 唔准 ${prop}: ${value}`, !SCROLLER.test(value), r.atRules.join(" ") || "base");
    }
  }
}

/* ② 表頭：sticky，top = header + 標題實高；z-index 要低過標題（標題未量到嗰陣表頭匿喺佢後面） */
const th = baseRule(".desktop-ranking-table thead th");
const heading = baseRule(".ranking-heading");
check("有 .desktop-ranking-table thead th base rule", Boolean(th));
check("有 .ranking-heading base rule", Boolean(heading));
const VAR = "--ranking-heading-h";
if (th) {
  check("表頭 position: sticky", decl(th.body, "position") === "sticky");
  check("表頭 top = header + 標題實高", decl(th.body, "top") === `calc(var(--header-height) + var(${VAR}, 0px))`, `而家係 ${decl(th.body, "top")}`);
}
if (heading) {
  check("標題 sticky 喺 --header-height", decl(heading.body, "position") === "sticky" && decl(heading.body, "top") === "var(--header-height)");
}
if (th && heading) {
  const zTh = Number(decl(th.body, "z-index"));
  const zHead = Number(decl(heading.body, "z-index"));
  check("標題 z-index 高過表頭", Number.isFinite(zTh) && Number.isFinite(zHead) && zHead > zTh, `heading ${zHead} / th ${zTh}`);
}
/* media 入面唔准改走表頭 top（改咗就同量出嚟嗰個數脫鈎） */
for (const r of rules) {
  if (r === th || !r.selectors.some((s) => /\.desktop-ranking-table\b.*\bth\b/.test(s))) continue;
  check(`media 唔准改表頭 top（${r.atRules.join(" ")} ${r.selectors.join(", ")}）`, decl(r.body, "top") === null);
}

/* ③ hook：量 border-box 實高（getBoundingClientRect，唔准 offsetHeight round），寫落 parent，跟住 resize */
/* 剝走注釋先查（註入面會講「唔用 offsetHeight」） */
const hook = read("apps/web/src/lib/use-ranking-heading-height.ts").replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, "");
check("hook export useRankingHeadingHeight", /export function useRankingHeadingHeight\(/.test(hook));
check("hook 寫同一個 CSS 變數", hook.includes(`.style.setProperty("${VAR}",`));
check("hook 寫落 parentElement", /parentElement/.test(hook));
check("hook 用 getBoundingClientRect().height", /getBoundingClientRect\(\)\.height/.test(hook));
check("hook 唔用 offsetHeight / clientHeight（會 round）", !/\b(offsetHeight|clientHeight)\b/.test(hook));
check("hook 跟住 ResizeObserver 更新", /new ResizeObserver\(/.test(hook) && /\.observe\(/.test(hook));
check("hook cleanup disconnect", /\.disconnect\(\)/.test(hook));

/* ④ 兩個榜都接好：ref 掛喺 .ranking-heading，而佢係 section 嘅直屬仔（變數寫落 parent，表頭要喺 parent 入面） */
for (const rel of ["apps/web/src/components/rankings.tsx", "apps/web/src/components/box-rankings.tsx"]) {
  const src = read(rel);
  const name = rel.split("/").pop();
  check(`${name} import hook`, /import \{ useRankingHeadingHeight \} from "@\/lib\/use-ranking-heading-height";/.test(src));
  check(`${name} call hook`, /useRankingHeadingHeight\(headingRef\);/.test(src) && /const headingRef = useRef<HTMLDivElement>\(null\);/.test(src));
  check(
    `${name} .ranking-heading 係 section 直屬仔兼掛 ref`,
    /<section className="rankings-section"[^>]*>\s*(?:\{\/\*[\s\S]*?\*\/\}\s*)?<div className="ranking-heading" ref=\{headingRef\}/.test(src),
  );
  check(`${name} 表喺同一個 section 入面`, src.indexOf('className="desktop-ranking-table"') > src.indexOf('className="ranking-heading"'));
}

/* ⑤ back 返嚟揀 anchor：表頭遮住嗰截都要避開 */
const restore = read("apps/web/src/components/scroll-restoration.tsx");
const stickySel = /function stickyBottom\(\)[\s\S]*?querySelectorAll<HTMLElement>\("([^"]+)"\)/.exec(restore);
check("scroll-restoration stickyBottom 計埋表頭", Boolean(stickySel) && /\.desktop-ranking-table thead th\b/.test(stickySel[1]), stickySel?.[1] ?? "搵唔到 selector");

if (failed.length) {
  console.error(`FAIL test-fe-ranking-sticky-thead (${failed.length})`);
  for (const line of failed) console.error(`  - ${line}`);
  process.exit(1);
}
console.log("PASS test-fe-ranking-sticky-thead — 框 overflow clip（hidden 只做 fallback）；表頭釘喺 header + 標題實高；兩個榜接好 hook；scroll-restoration 避開表頭");
process.exit(0);
