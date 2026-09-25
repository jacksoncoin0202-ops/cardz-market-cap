#!/usr/bin/env node
/*
 * heatmap tile **盒**唔准俾人 transform —— pixel-snap 契約嘅 render 側。
 * 預設純靜態：唔開瀏覽器、唔使 dev server，由 run_all_tests.py glob `scripts/test-*.mjs` 收。
 * 加 `--live` 先會開 Chromium 逐幀量真隙（要 dev server，見底部 T6）。
 *
 * ── 點解要有呢個檔 ───────────────────────────────────────────────────────
 * `test-fe-pixel-snap.mjs` 守嘅係 `lib/pixel-snap.ts` 條**數**：tile 邊界釘落絕對 device
 * px 格、gap 拆 p/q、相鄰兩格共用同一條線，所以每條隙都係 exactly `gap` 粒 device px。
 * 但條數啱**唔代表**畫出嚟啱：只要有人喺 `.heatmap-tile` 個**盒**上面加 transform，
 * 個盒就會離開嗰條釘死嘅線，每格向外食走自己嗰條隙 —— 而 pixel-snap 嗰個 test 照綠，
 * tsc 照綠、eslint 照綠、截圖靜態睇落都似冇事。呢個就係「檢查窄過寫入」嘅形狀。
 *
 * 實際出過兩次事（同一個 owner 投訴兩次）：
 *   2026-08-18  kiosk 入場 burst `scale(0.62)` → owner：「啲卡全部走晒位」
 *   2026-08-21  縮細到 `scale(0.94)` 之後**照樣走位** → owner：「一入到去都係走位」
 * 逐幀量到（live app.cardzmarketcap.com，headed Chromium）：入 kiosk 前全版一律 5.00
 * device px（dpr 1.5），Enter 後 185ms 變 8.3…16.93px、100 格凍喺 scale(0.94)，最後一幀
 * 花嘢 851ms，105 幀入面 30 幀花；dpr 1 一樣中招（3 → 2.2…9.85px）。最後 owner 拍板
 * 剷走成個入場動畫（commit c6abdcf0）。
 *
 * 「縮細幅度」唔係修法 —— 0.94 已經係第二次縮細，一樣走位。**唯一**修法係唔好 scale
 * 個盒：要郁就 scale 入面嗰張 `.tile-card`（`kiosk-breathe` 就係咁做，佢冇事），
 * 或者淡 frame 底色。呢個檔就係將呢句話由註釋變成一條會嗌嘅檢查。
 *
 * ── 條規矩（T1）─────────────────────────────────────────────────────────
 * 任何「subject 係 `.heatmap-tile`」而且會 introduce transform 嘅 rule（直接寫
 * `transform:`，或者 `animation` 指去一個掂 transform 嘅 @keyframes），一定要屬於
 * 下面其中一類，否則紅：
 *   NEUTRALISER   `transform: none` / `animation: none` —— 佢係拆嘢嗰條，唔係加嘢
 *   SINGLE_TILE   subject 帶 per-tile 判別 attribute（`[data-kiosk-star]`）——
 *                 一次淨係一格，而且嗰格係特登飛起／升起離開個格網，唔關 gap 事
 *   INTERACTION   `:active` / `:hover` / `:focus-visible` —— 用戶自己撳嗰一下，
 *                 一格、瞬間、有人手喺度（globals.css §4.5 明文批咗「hover 一次性」）
 *   KNOWN_DEBT    下面 KNOWN_DEBT 逐條寫明嘅**既有**債，凍結咗，每次跑都會印出嚟
 * 即係話：**新加**一條掃全部 tile 嘅 transform 動畫 = 即刻紅。呢個係 ratchet，
 * 唔係一次性檢查 —— KNOWN_DEBT 只准減唔准加。
 *
 * ── T2…T5 ──────────────────────────────────────────────────────────────
 *  T2  入場 burst 嘅名唔准返嚟（`@keyframes kiosk-burst` / `[data-kiosk-enter]` /
 *      `--kiosk-burst-*` / `fxRadius` / `--fx-r`）。**要剝走註釋先掃** —— 嗰幾個檔嘅
 *      註釋特登寫住呢啲名去講「唔准加返」，唔剝就永遠假紅。
 *  T3  `--fx-i` 一定要仲喺度：`kiosk-breathe` 靠佢錯開相位。剷 burst 嗰陣順手剷埋
 *      `--fx-r`，好易連 `--fx-i` 一齊剷（兩個名爭一個字）。
 *  T4  `kiosk-breathe` 一定要 scale `.tile-card` 唔係 `.heatmap-tile`，而且個 keyframe
 *      一定要**帶返** `translate(-50%, -50%)`（`.tile-card` 靠佢置中，寫漏就飛去右下角）。
 *  T5  負控制：五條種返 bug 逐條證實會紅 + 一條「唔應該紅」嘅對照。
 *      冇呢一步，上面四條可以係「永遠揀唔中嘢所以永遠綠」。
 */
import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const failed = [];
const notes = [];
const check = (label, condition, detail) => { if (!condition) failed.push(detail ? `${label}: ${detail}` : label); };
const read = (rel) => readFileSync(join(ROOT, rel), "utf8");

const GLOBALS_REL = "apps/web/src/app/globals.css";
const KIOSK_REL = "apps/web/src/app/styles/heatmap-kiosk.css";
const HEATMAP_REL = "apps/web/src/components/heatmap.tsx";
const FX_REL = "apps/web/src/components/heatmap-kiosk-fx.tsx";

/* CSS 註釋一定要喺任何掃描之前剝走。呢個 repo 嘅 CSS 註釋本身就寫住成段
   「唔准 scale .heatmap-tile」嘅解釋，連埋 selector 原文；唔剝就次次假紅。 */
const stripCssComments = (s) => s.replace(/\/\*[\s\S]*?\*\//g, "");
/* JS/TS：先剝 block 再剝行註釋。字串入面嘅 `//` 唔關事 —— 下面只攞嚟做「有冇呢個
   identifier」嘅存在性判斷，寧枉毋縱嗰邊係「多咗當有」，唔會靜靜漏。 */
const stripJsComments = (s) => s.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^[ \t]*\/\/.*$/gm, "");

/* ─────────────────────────────────────────────────────────────
 * 掃描器：搵出「subject 係 .heatmap-tile 而且 introduce transform」嗰批 rule
 *
 * 唔用 regex 一嘢捉「有冇 scale 呢隻字」—— 嗰種寫法喺 transform 搬入 @keyframes
 * 之後就靜靜綠（burst 就係咁寫）。呢度行真 brace 掃描，維持 at-rule stack，
 * 而且會跟住 `animation` 追去 @keyframes 入面睇佢掂唔掂 transform。
 * ───────────────────────────────────────────────────────────── */
const SINGLE_TILE_MARKERS = ["[data-kiosk-star]"];
const INTERACTION_MARKERS = [":active", ":hover", ":focus-visible"];

function scanTileTransforms(rawCss) {
  const css = stripCssComments(rawCss);

  /* ① 邊個 @keyframes 會郁 transform */
  const kfTouchesTransform = new Map();
  for (const m of css.matchAll(/@keyframes\s+([\w-]+)\s*\{/g)) {
    let depth = 1, j = m.index + m[0].length;
    while (j < css.length && depth) {
      if (css[j] === "{") depth++;
      else if (css[j] === "}") depth--;
      j++;
    }
    const body = css.slice(m.index + m[0].length, j - 1);
    kfTouchesTransform.set(m[1], /(^|[;{\s])transform\s*:/.test(body));
  }

  /* ② 逐條 rule；只留 subject（selector 最後嗰個 compound）係 .heatmap-tile 嗰啲 */
  const found = [];
  const stack = [];
  let i = 0, buf = "";
  while (i < css.length) {
    const ch = css[i];
    if (ch === "{") {
      const head = buf.trim();
      buf = "";
      if (head.startsWith("@")) { stack.push(head); i++; continue; }
      stack.push(null);
      let depth = 1, j = i + 1;
      while (j < css.length && depth) {
        if (css[j] === "{") depth++;
        else if (css[j] === "}") depth--;
        j++;
      }
      const body = css.slice(i + 1, j - 1);
      /* 唔准當 `transform: none` 係「加 transform」—— 佢係 reduced-motion 側拆嘢嗰條 */
      const tv = /(^|[;{\s])transform\s*:\s*([^;]+)/.exec(body);
      const addsTransform = !!tv && tv[2].trim() !== "none";
      const animNames = [...body.matchAll(/(^|[;{\s])animation(?:-name)?\s*:\s*([^;]+)/g)].map((x) => x[2]);
      const animTransform = [...kfTouchesTransform.entries()]
        .filter(([name, touches]) => touches && animNames.some((v) => new RegExp(`\\b${name}\\b`).test(v)))
        .map(([name]) => name);
      if (addsTransform || animTransform.length) {
        for (const part of head.split(",")) {
          const sel = part.trim();
          if (!sel) continue;
          /* subject = 最後一個 compound（跟返 CSS「subject 係最右嗰個」語義）。
             `>` / `+` / `~` 都當 combinator 切開。 */
          const subject = sel.split(/\s*[>+~]\s*|\s+/).filter(Boolean).pop() ?? "";
          if (!subject.includes("heatmap-tile")) continue;
          found.push({
            selector: sel,
            subject,
            atRules: stack.filter(Boolean).slice(),
            transform: tv ? tv[2].trim() : null,
            animations: animTransform,
          });
        }
      }
      i++; continue;
    }
    if (ch === "}") { stack.pop(); buf = ""; i++; continue; }
    if (ch === ";") { buf = ""; i++; continue; }
    buf += ch;
    i++;
  }
  return found;
}

function classify(hit) {
  if (SINGLE_TILE_MARKERS.some((m) => hit.subject.includes(m))) return "SINGLE_TILE";
  if (INTERACTION_MARKERS.some((m) => hit.subject.includes(m))) return "INTERACTION";
  return null;
}

/*
 * 凍結咗嘅既有債。**只准減唔准加。**
 * 每一條要寫明：邊個檔、subject、點解未修、修法係咩。加新一條之前先諗清楚 ——
 * 呢度多一行 = 熱力圖多一種走位方式。
 */
const KNOWN_DEBT = [
  {
    file: GLOBALS_REL,
    subject: ".heatmap-tile[data-late]",
    why:
      "拖 slider 加出嚟／離場再入場嘅 tile 行 `tile-pop`（0% scale(0.9) → 55% scale(1.02) → 100% scale(1)），" +
      "scale 嘅係 tile 個盒，即係同入場 burst 一模一樣嘅形狀，嗰 200ms 之內嗰幾格會食走自己條隙。" +
      "同 burst 唔同嘅係佢一次淨係打中新加嗰批（唔係成版 100 格）、只有 200ms、而且係 owner " +
      "2026-08-16 親自要嘅「一拉就即刻飛出嚟」。所以呢度**唔擅自改**，等 owner 拍板。",
    fix:
      "同 `kiosk-breathe` 一樣搬位：`opacity` 留喺 `.heatmap-tile`（opacity 唔影響 layout，" +
      "唔會食隙），`transform: scale(...)` 搬去 `.heatmap-tile[data-late] .tile-card`，" +
      "而個 keyframe 要帶返 `translate(-50%, -50%)`（見 T4）。",
  },
];

/* ─────────────────────────────────────────────────────────────
 * T1 — tile 盒 transform 白名單
 * ───────────────────────────────────────────────────────────── */
const SCAN_TARGETS = [GLOBALS_REL, KIOSK_REL];
const allHits = [];
for (const rel of SCAN_TARGETS) {
  for (const hit of scanTileTransforms(read(rel))) allHits.push({ ...hit, file: rel });
}

check("T1: 掃到 tile 盒 transform rule（一條都冇 = 掃描器揀唔中嘢，唔係真係乾淨）",
  allHits.length > 0, "0 條 —— selector 改咗形狀？掃描器要跟住改");

const debtSeen = new Set();
for (const hit of allHits) {
  if (classify(hit)) continue;
  const debt = KNOWN_DEBT.find((d) => d.file === hit.file && d.subject === hit.subject);
  if (debt) {
    debtSeen.add(`${debt.file}|${debt.subject}`);
    notes.push(`KNOWN-DEBT ${hit.file} ${hit.subject}（${hit.animations.join(",") || hit.transform}）—— ${debt.why} 修法：${debt.fix}`);
    continue;
  }
  failed.push(
    `T1: ${hit.file} 有一條掃全部 tile 嘅盒 transform：\`${hit.selector}\`` +
    `${hit.atRules.length ? ` @ ${hit.atRules.join(" / ")}` : ""}` +
    `（${hit.animations.length ? `animation ${hit.animations.join(",")}` : `transform ${hit.transform}`}）。` +
    " tile 盒係釘死喺絕對 device px 格上面嘅（lib/pixel-snap.ts），一 scale 就每格食走自己條隙 → 成版走位。" +
    " 要郁就 scale 入面嗰張 `.tile-card`（跟 kiosk-breathe），或者淡 frame 底色。",
  );
}

/* 債還咗要記得喺呢度刪返 —— 唔准留一條指去唔存在嘅 selector 嘅「債」扮有人管住 */
for (const d of KNOWN_DEBT) {
  check("T1: KNOWN_DEBT 有一條已經冇對應 rule，請刪走佢",
    debtSeen.has(`${d.file}|${d.subject}`), `${d.file} ${d.subject}`);
}

/* ─────────────────────────────────────────────────────────────
 * T2 — 剷走咗嘅入場 burst，個名唔准返嚟（剝註釋之後先掃）
 * ───────────────────────────────────────────────────────────── */
const GONE = [
  { rel: KIOSK_REL, strip: stripCssComments, needles: ["@keyframes kiosk-burst", "data-kiosk-enter", "--kiosk-burst-delay", "--kiosk-burst-dur"] },
  { rel: FX_REL, strip: stripJsComments, needles: ["data-kiosk-enter", "BURST_MAX_DELAY", "BURST_DUR", "BURST_TAIL"] },
  { rel: HEATMAP_REL, strip: stripJsComments, needles: ["fxRadius", "--fx-r"] },
];
for (const g of GONE) {
  const body = g.strip(read(g.rel));
  for (const needle of g.needles) {
    check(`T2: \`${needle}\` 唔准返嚟（${g.rel}）`, !body.includes(needle),
      "入場 burst 2026-08-21 剷咗（commit c6abdcf0）。想要入場動效就淡 frame 底色，或者 scale `.tile-card`");
  }
}

/* ─────────────────────────────────────────────────────────────
 * T3 — `--fx-i` 一定要仲喺度（kiosk-breathe 靠佢）
 * ───────────────────────────────────────────────────────────── */
const heatmapSrc = stripJsComments(read(HEATMAP_REL));
const kioskCssStripped = stripCssComments(read(KIOSK_REL));
check("T3: heatmap.tsx 仲有寫 `--fx-i`（剷 --fx-r 嗰陣好易連佢一齊剷）",
  /setProperty\(\s*["']--fx-i["']/.test(heatmapSrc));
check("T3: heatmap-kiosk.css 仲有讀 `--fx-i`", kioskCssStripped.includes("--fx-i"));

/* ─────────────────────────────────────────────────────────────
 * T4 — kiosk-breathe 係「正確做法」嘅參考實現，唔准俾人改壞
 * ───────────────────────────────────────────────────────────── */
{
  const m = /@keyframes\s+kiosk-breathe\s*\{([\s\S]*?)\n\}/.exec(kioskCssStripped);
  check("T4: 搵到 @keyframes kiosk-breathe", !!m);
  if (m) {
    check("T4: kiosk-breathe 每格 transform 都要帶返 translate(-50%, -50%)（.tile-card 靠佢置中）",
      [...m[1].matchAll(/transform\s*:\s*([^;]+)/g)].every((x) => /translate\(\s*-50%\s*,\s*-50%\s*\)/.test(x[1])),
      m[1].replace(/\s+/g, " ").trim());
  }
  const applied = [...kioskCssStripped.matchAll(/([^{}]*)\{[^{}]*animation:\s*kiosk-breathe[^;]*;/g)].map((x) => x[1].trim());
  check("T4: kiosk-breathe 一定要落喺 `.tile-card`，唔准落喺 `.heatmap-tile` 個盒",
    applied.length > 0 && applied.every((sel) => sel.trimEnd().endsWith(".tile-card")),
    applied.join(" | ") || "搵唔到 call site");
}

/* ─────────────────────────────────────────────────────────────
 * T5 — 負控制：種返 bug 逐條要紅（規矩 9）
 *
 * 呢度全部喺 process 入面種，唔改 source、唔洗瀏覽器。
 * 冇呢段，上面成個檔可以係「掃描器根本揀唔中嘢」而永遠綠。
 * ───────────────────────────────────────────────────────────── */
{
  const fired = [];
  const missed = [];
  const probe = (label, css, want) => {
    const hits = scanTileTransforms(css).filter((h) => !classify(h));
    (hits.length > 0 === want ? fired : missed).push(label);
  };

  /* P1 舊 burst 原文（animation → @keyframes 入面先至有 transform）—— 一定要紅 */
  probe("P1 burst 原文", `
    @keyframes kiosk-burst { 0% { transform: scale(0.94); } 100% { transform: none; } }
    .heatmap-section[data-kiosk="true"][data-kiosk-enter] .heatmap-tile {
      animation: kiosk-burst 380ms var(--ease-spring) backwards;
    }`, true);

  /* P2 換個名一樣要紅（唔准淨係認 "kiosk-burst" 呢隻字） */
  probe("P2 換名嘅入場動畫", `
    @keyframes shiny-entrance { from { transform: scale(0.98) rotate(1deg); } to { transform: none; } }
    .heatmap-section[data-kiosk="true"] .heatmap-tile { animation: shiny-entrance 300ms ease; }`, true);

  /* P3 直接寫 transform（唔經 keyframes）—— 一樣要紅 */
  probe("P3 直接寫 transform", `
    .heatmap-section[data-kiosk="true"] .heatmap-tile { transform: scale(0.97); }`, true);

  /* P4 transition 帶住 transform 落盒 —— 一樣要紅 */
  probe("P4 hover 以外嘅 transform 狀態", `
    .heatmap-frame[data-x] .heatmap-tile { transform: translateY(-2px); transition: transform 200ms ease; }`, true);

  /* P5 subject 唔係 tile（tile 只係祖先）都要捉 —— 呢個係反向陷阱：
     `.heatmap-tile .tile-card` 嘅 subject 係 .tile-card，唔應該紅（見 P6）；
     但 `.tile-card .heatmap-tile` 咁寫 subject 就係 tile，要紅。 */
  probe("P5 subject 係 tile 但寫喺後面", `
    .kiosk-stage .heatmap-tile { transform: scale(1.2); }`, true);

  /* P6 對照組：**唔應該**紅 —— 正確做法（scale 入面張卡）唔准俾人當 bug 捉 */
  probe("P6 對照組：scale .tile-card 唔應該紅", `
    @keyframes ok-pop { 0% { transform: translate(-50%, -50%) scale(0.9); } 100% { transform: translate(-50%, -50%) scale(1); } }
    .heatmap-section[data-kiosk="true"] .heatmap-tile .tile-card { animation: ok-pop 300ms ease; }`, false);

  /* P7 對照組：reduced-motion 嗰啲 `transform: none` 係拆嘢，唔准當加嘢 */
  probe("P7 對照組：transform: none 唔應該紅", `
    .heatmap-section[data-kiosk] .heatmap-tile { transform: none; animation: none; }`, false);

  check(`T5: 負控制 —— 七條全部要跟預期（${fired.length}/${fired.length + missed.length} 中）`,
    missed.length === 0, `FAILED TO FIRE: ${missed.join(", ")}`);

  /* T2 個「名唔准返嚟」都要證明佢會 fire */
  check("T5: T2 嘅字串檢查真係會 fire",
    stripCssComments("/* 呢度講 @keyframes kiosk-burst 唔准返嚟 */\n@keyframes kiosk-burst { }").includes("@keyframes kiosk-burst")
    && !stripCssComments("/* 呢度講 @keyframes kiosk-burst 唔准返嚟 */\n.x { color: red; }").includes("@keyframes kiosk-burst"),
    "剝註釋之後應該淨低真嘢；註釋入面提個名唔應該紅");
}

/* ─────────────────────────────────────────────────────────────
 * T6 — （opt-in）`--live`：開 Chromium 逐幀量真隙
 *
 * 上面 T1…T5 守嘅係「source 入面有冇人寫錯」；呢度守嘅係「render 出嚟真係啱」。
 * 兩者唔可以互相取代：靜態嗰邊掃唔到 inline style / JS 寫落去嘅 transform，
 * live 嗰邊又要 dev server 同 playwright（呢個 repo 兩樣都唔喺預設路徑）。
 *
 * 跑法：node scripts/test-fe-heatmap-tile-transform.mjs --live
 *       TILE_BASE_URL=https://app.cardzmarketcap.com node … --live
 * 量法：入 kiosk 之後連續逐幀（唔跳格 —— 走位窗口可以窄到 200ms）攞每一格
 *       gBCR（**計 transform**），搵右邊／下邊最近而且有重疊嗰格量條隙，×dpr。
 *       pixel-snap 保證條隙係一個定值，所以「min ≠ max」或者「≠ 入 kiosk 前個基準」
 *       就係走位。SEED=1 會喺 page 側種返舊 burst（**唔改 source**）證明條判斷會紅。
 * ───────────────────────────────────────────────────────────── */
if (process.argv.includes("--live")) {
  const base = process.env.TILE_BASE_URL ?? "http://localhost:3901";
  const span = Number(process.env.TILE_SPAN_MS ?? 1600);
  const { chromium } = await import("playwright").catch(() => ({ chromium: null }));
  if (!chromium) {
    failed.push("T6: --live 要 playwright，但 import 唔到");
  } else {
    const GAPS = () => {
      window.__gaps = () => {
        const dpr = devicePixelRatio;
        const els = [...document.querySelectorAll(".heatmap-tile")];
        const boxes = els.map((el) => {
          const r = el.getBoundingClientRect();
          return { l: r.left, t: r.top, r: r.right, b: r.bottom };
        });
        const out = [];
        for (let i = 0; i < boxes.length; i++) {
          const a = boxes[i];
          let right = Infinity, below = Infinity;
          for (let j = 0; j < boxes.length; j++) {
            if (i === j) continue;
            const b = boxes[j];
            if (Math.min(a.b, b.b) - Math.max(a.t, b.t) > 2 && b.l >= a.r - 1) right = Math.min(right, b.l - a.r);
            if (Math.min(a.r, b.r) - Math.max(a.l, b.l) > 2 && b.t >= a.b - 1) below = Math.min(below, b.t - a.b);
          }
          if (Number.isFinite(right)) out.push(right * dpr);
          if (Number.isFinite(below)) out.push(below * dpr);
        }
        out.sort((x, y) => x - y);
        return {
          n: out.length,
          min: out.length ? +out[0].toFixed(2) : null,
          max: out.length ? +out[out.length - 1].toFixed(2) : null,
          med: out.length ? +out[Math.floor(out.length / 2)].toFixed(2) : null,
          tiles: els.length,
          xf: els.filter((el) => {
            const t = getComputedStyle(el).transform;
            return t && t !== "none" && t !== "matrix(1, 0, 0, 1, 0, 0)";
          }).length,
        };
      };
    };
    /* 舊 burst 嘅形狀，喺 page 側注入 —— source 一個 byte 都唔改 */
    const SEED_CSS = `
      @keyframes seed-burst { 0% { transform: scale(0.94); } 100% { transform: none; } }
      .heatmap-section[data-kiosk="true"] .heatmap-tile {
        animation: seed-burst 380ms cubic-bezier(0.2, 0.9, 0.3, 1.3) backwards;
        animation-delay: calc(var(--fx-i, 0) * 2ms);
      }`;

    const measure = async (seed) => {
      const browser = await chromium.launch({
        headless: false,   /* kiosk 要 requestFullscreen，headless 落去成個 FX 層行為唔同 */
        args: ["--force-device-scale-factor=1.5", "--window-size=1500,900", "--window-position=0,0"],
      });
      try {
        const ctx = await browser.newContext({ viewport: null });   /* 唔落 device metrics override，用真窗口尺寸 */
        await ctx.addInitScript(GAPS);
        const page = await ctx.newPage();
        await page.goto(base, { waitUntil: "domcontentloaded" });
        await page.waitForSelector(".heatmap-tile", { timeout: 90000 });
        await page.waitForTimeout(2500);
        const before = await page.evaluate(() => window.__gaps());
        if (seed) await page.addStyleTag({ content: SEED_CSS });
        await page.evaluate(() => { window.__t0 = undefined; });
        await page.locator(".heatmap-kiosk-toggle").click();
        const frames = await page.evaluate(async (ms) => {
          const t0 = performance.now();
          const rows = [];
          while (performance.now() - t0 < ms) {
            await new Promise((r) => requestAnimationFrame(r));
            rows.push(window.__gaps());
          }
          return rows;
        }, span);
        return { before, frames };
      } finally {
        await browser.close();
      }
    };

    const verdict = ({ before, frames }) => {
      const baseline = before.med;
      const bad = frames.filter((f) => f.min !== baseline || f.max !== baseline);
      return {
        baseline,
        tiles: before.tiles,
        frames: frames.length,
        bad: bad.length,
        lo: Math.min(...frames.map((f) => f.min)),
        hi: Math.max(...frames.map((f) => f.max)),
        xf: Math.max(...frames.map((f) => f.xf)),
      };
    };

    const clean = verdict(await measure(false));
    check("T6: 入 kiosk 之前量到隙（0 = 揀唔中 tile，唔係乾淨）", clean.tiles > 0 && clean.baseline > 0, JSON.stringify(clean));
    check("T6: 入 kiosk 之後每一幀條隙都鎖死喺基準",
      clean.bad === 0, `${clean.bad}/${clean.frames} 幀走位，範圍 ${clean.lo}…${clean.hi}（基準 ${clean.baseline}）`);
    check("T6: 入場期間冇一格 tile 盒帶 transform", clean.xf === 0, `最多 ${clean.xf} 格`);
    notes.push(`T6 clean: ${clean.tiles} 格、基準 ${clean.baseline} device px、${clean.frames} 幀連續、走位 ${clean.bad}、盒 transform ${clean.xf}`);

    /* 負控制：種返舊 burst 一定要紅。冇呢步，「0 走位幀」可以係量錯嘢量到零。 */
    const seeded = verdict(await measure(true));
    check("T6: 負控制 —— 種返舊 burst 之後一定要量到走位（規矩 9）",
      seeded.bad > 0 && seeded.xf > 0,
      `FAILED TO FIRE：種咗 burst 但量到 ${seeded.bad}/${seeded.frames} 幀走位、${seeded.xf} 格 transform`);
    notes.push(`T6 seeded: 走位 ${seeded.bad}/${seeded.frames} 幀、範圍 ${seeded.lo}…${seeded.hi}、盒 transform ${seeded.xf} 格`);
  }
}

for (const n of notes) console.log(` · ${n}`);
if (failed.length) {
  console.error(`FAIL test-fe-heatmap-tile-transform (${failed.length})`);
  for (const f of failed) console.error(` - ${f}`);
  process.exit(1);
}
console.log(
  `PASS test-fe-heatmap-tile-transform（掃到 ${allHits.length} 條 tile 盒 transform rule，` +
  `${KNOWN_DEBT.length} 條凍結債；負控制七條全部 fire${process.argv.includes("--live") ? "；--live 逐幀量隙已跑" : "，加 --live 可以逐幀量真隙"}）`,
);
