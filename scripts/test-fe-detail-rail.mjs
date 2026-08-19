#!/usr/bin/env node
/*
 * 卡內頁桌面 rail（`.detail-grid-rail`）契約 —— 2026-08-19。
 * 預設純靜態：唔開瀏覽器、唔使 dev server，由 run_all_tests.py glob `scripts/test-*.mjs` 收。
 * 加 `--live` 先會開 headless Chromium 去量真嘢（要 dev server，見底部）。
 *
 * 背景：owner 睇 1920 桌面內頁 ——「你睇下呢度咁大個空位，你唔好好利用佢……而家成件事好核突」。
 * 改法係 `.detail-art` 由 row 撐高（死空間結構性歸零）+ metrics／資料時間升做全版 KPI 條。
 * 呢個檔守嘅係「一錯就靜靜出錯版、冇 error 冇 warning、CI 照綠」嗰幾種：
 *
 *  ① **手機唔准郁一個 px。** owner 明講「手機版內頁係 OK 嘅」。保證方法唔係靠人手覆檢，
 *     係「每一條 rail rule 都一定要喺 `@media (min-width: 981px)` 入面」——
 *     結構上 ≤980 揀唔中，唔係「量過冇郁」。
 *  ② **`/box/[id]` 唔准郁一個 px。** `.detail-grid` / `.detail-art` / `.detail-content` /
 *     `.detail-metrics` / `.data-time` 五個 class 同 box-detail.tsx 共用，所以每一條 rail
 *     selector 都一定要有 `.detail-grid-rail` 收口。漏一條 = 原盒內頁一齊變樣，冇人會即刻發現。
 *  ③ **放大倍數唔准靠 `naturalWidth` 量。** 呢個係最貴嗰個陷阱，實測過：
 *     `<img>` 有 `srcset "…_200.webp 200w, …_600.webp 600w"` + `sizes "…, 560px"`。
 *     HTML spec 對 `w` descriptor 會做 density correction（density = 600 ÷ 560），
 *     於是 429×600 嘅真身喺 `naturalWidth` 度變 **400×560**，喺 375 度更加變 **241**。
 *     跟住個數行事就會當咗自己放大緊 1.25× 其實得 1.16×，白白將卡圖縮細 36px。
 *     `<img width>` attribute 一樣信唔過 —— 佢寫嘅係 **master** 尺寸，
 *     但桌面實際載落嚟嘅係 `_600` variant，兩者可以唔同（見 T5 嗰個 719×1000 實例）。
 *     唯一算數嘅係 `_600.webp` 檔頭（下面 T5 逐個掃 RIFF chunk）。
 *  ④ **DOM 次序唔准變、唔准用 CSS `order`。** 呢次改動只係換 parent（`.detail-content` 提早收口），
 *     讀屏同 Tab 序照舊。用 `order` 就會令視覺次序同 DOM 次序分家。
 *  ⑤ **KPI 條唔准出孤兒行。** 原本個病就係 `.wide-metric:last-child { span 4 }` 喺右欄整咗
 *     一格 1062px 闊嘅孤兒行。5 格同 6 格兩種卡都要啱啱鋪滿（永遠 6 個 unit）。
 */
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const failed = [];
const check = (label, condition, detail) => { if (!condition) failed.push(detail ? `${label}: ${detail}` : label); };
const read = (rel) => readFileSync(join(ROOT, rel), "utf8");

const CSS_REL = "apps/web/src/app/globals.css";
const TSX_REL = "apps/web/src/components/card-detail.tsx";
const BOX_REL = "apps/web/src/components/box-detail.tsx";
const ASSET_DIR = "data/public/market-assets";

const css = read(CSS_REL);
const tsx = read(TSX_REL);

/* ─────────────────────────────────────────────────────────────
 * T1 — 每一條 `.detail-grid-rail` rule 都要喺 `min-width: 981px` 之下（保證 ①）
 *
 * 唔用 regex 掃「有冇 981 呢隻字」——嗰種寫法喺 rule 搬咗出 media query 之後照樣綠。
 * 呢度真係行一次 brace 掃描，維持緊 at-rule stack，逐條 selector 問「你頭頂有冇 981」。
 * ───────────────────────────────────────────────────────────── */
const railRules = [];   // { selector, atRules: string[] , index }
{
  const stack = [];
  let i = 0;
  let buf = "";
  while (i < css.length) {
    const ch = css[i];
    if (ch === "/" && css[i + 1] === "*") { const end = css.indexOf("*/", i + 2); i = end < 0 ? css.length : end + 2; continue; }
    if (ch === "{") {
      const head = buf.trim();
      buf = "";
      if (head.startsWith("@")) stack.push(head);
      else { stack.push(null); if (head.includes("detail-grid-rail")) railRules.push({ selector: head, atRules: stack.filter(Boolean).slice(), index: i }); }
      i++; continue;
    }
    if (ch === "}") { stack.pop(); buf = ""; i++; continue; }
    if (ch === ";") { buf = ""; i++; continue; }
    buf += ch;
    i++;
  }
}

check("T1: globals.css 入面搵到 rail rule", railRules.length > 0,
  "一條 `.detail-grid-rail` 都冇 —— 個 rail block 俾人刪咗？");

for (const r of railRules) {
  const guarded = r.atRules.some((a) => /@media[^{]*min-width:\s*(9[89]\d|[1-9]\d{3,})px/.test(a));
  check("T1: rail rule 一定要包喺 @media (min-width: 981px) 入面（手機由構造保證唔郁）",
    guarded, `\`${r.selector.slice(0, 90)}\` 嘅 at-rule stack = ${JSON.stringify(r.atRules) || "[]"}`);
}

/* ─────────────────────────────────────────────────────────────
 * T2 — 冇一條 rail rule 揀得中 `/box/[id]`（保證 ②）
 *
 * 判法：selector 逐個 comma 分支拆開，每個分支都一定要提到 `.detail-grid-rail`。
 * 只要有一個分支漏咗（例如順手寫 `.detail-grid-rail > .detail-metrics, .detail-metrics strong`），
 * 原盒內頁就會一齊中招。
 * ───────────────────────────────────────────────────────────── */
for (const r of railRules) {
  for (const branch of r.selector.split(",")) {
    const b = branch.trim();
    if (!b) continue;
    check("T2: 每個 selector 分支都要有 `.detail-grid-rail`（唔准漏，一漏就拖埋 /box 落水）",
      b.includes(".detail-grid-rail"), `分支 \`${b}\`（喺 \`${r.selector.slice(0, 70)}\` 入面）`);
  }
}
check("T2: box-detail.tsx 唔准帶 rail class", !read(BOX_REL).includes("detail-grid-rail"));

/* ─────────────────────────────────────────────────────────────
 * T3 — 唔准用 CSS `order`（保證 ④）
 * ───────────────────────────────────────────────────────────── */
{
  const railBlockStart = css.indexOf("卡內頁桌面 rail");
  const railBlock = railBlockStart < 0 ? "" : css.slice(railBlockStart);
  check("T3: rail block 唔准出現 `order:`（視覺次序要同 DOM 次序一致）",
    !/(^|[;{\s])order\s*:/m.test(railBlock.replace(/\/\*[\s\S]*?\*\//g, "")));
}

/* ─────────────────────────────────────────────────────────────
 * T4 — token 要由出處推出嚟，唔准係度身砌嘅魔術數
 * ───────────────────────────────────────────────────────────── */
const token = (name) => (css.match(new RegExp(`--${name}:\\s*([^;]+);`)) || [])[1]?.trim();
const railSrcW = token("rail-src-w");
const railUpscale = Number(token("rail-upscale"));
const railCardW = token("rail-card-w");
const railFrame = token("rail-frame");
const railArW = Number(token("rail-ar-w"));
const railArH = Number(token("rail-ar-h"));
const kpiMax = token("kpi-fs-max");
const kpiMin = token("kpi-fs-min");

check("T4: `--rail-card-w` 一定要由 `--rail-src-w × --rail-upscale` 解出嚟（唔准寫死一個 px）",
  railCardW === "calc(var(--rail-src-w) * var(--rail-upscale))", `而家係 \`${railCardW}\``);
check("T4: `--rail-upscale` ≤ 1.25（owner「唔好蒙查糊」硬規矩）",
  Number.isFinite(railUpscale) && railUpscale <= 1.25, `而家係 ${railUpscale}`);

/* `--rail-frame` = `.detail-art img` 左右 padding 加埋。唔跟就會出現兩套幾何：
   一套用嚟計欄闊上限、一套真係畫落去，張卡靜靜爆出可視高度。 */
{
  const pad = Number((css.match(/\.detail-art img\s*\{[^}]*padding:\s*(\d+)px/) || [])[1]);
  check("T4: `--rail-frame` = 2 × `.detail-art img` padding",
    Number.isFinite(pad) && railFrame === `${pad * 2}px`, `frame=${railFrame} img padding=${pad}px`);
}

check("T4: `--kpi-fs-max` = `.detail-metrics strong` base font-size（唔准喺 rail 度偷偷調細）",
  kpiMax === (css.match(/\.detail-metrics strong\s*\{[^}]*font-size:\s*([^;]+);/) || [])[1]?.trim(),
  `kpi-fs-max=${kpiMax}`);
check("T4: `--kpi-fs-min` ≥ 19px（19 = 2026-08-17 加大之前出過街嘅桌面字級，再細寧願退兩行）",
  parseFloat(kpiMin) >= 19, `而家係 ${kpiMin}`);

/* ─────────────────────────────────────────────────────────────
 * T5 — `--rail-src-w` / aspect 要對得返真身 bitmap（保證 ③）
 *
 * 逐個 `_600.webp` 讀 RIFF chunk 攞真尺寸。**特登唔用 seed-snapshot 個 width/height**：
 * 嗰兩個數係 payload 自己講嘅，正正就係要驗嘅嘢。實測過至少有一種情況兩者可以唔同源。
 * ───────────────────────────────────────────────────────────── */
const webpSize = (buf) => {
  if (buf.length < 30 || buf.toString("ascii", 0, 4) !== "RIFF") return null;
  let off = 12;
  let canvas = null;
  while (off + 8 <= buf.length) {
    const tag = buf.toString("ascii", off, off + 4);
    const size = buf.readUInt32LE(off + 4);
    const body = off + 8;
    if (tag === "VP8X" && body + 10 <= buf.length) canvas = { w: buf.readUIntLE(body + 4, 3) + 1, h: buf.readUIntLE(body + 7, 3) + 1 };
    if (tag === "VP8 " && body + 10 <= buf.length) return { w: buf.readUInt16LE(body + 6) & 0x3fff, h: buf.readUInt16LE(body + 8) & 0x3fff };
    if (tag === "VP8L" && body + 5 <= buf.length) { const b = buf.readUInt32LE(body + 1); return { w: (b & 0x3fff) + 1, h: ((b >> 14) & 0x3fff) + 1 }; }
    off = body + size + (size % 2);
    if (size === 0) break;
  }
  return canvas;
};

{
  const dir = join(ROOT, ASSET_DIR);
  const snapshot = JSON.parse(read("data/public/seed-snapshot.json"));
  const cards = snapshot.top100 ?? [];
  check("T5: seed-snapshot 有卡", cards.length > 0);

  /* ⚠️ **唔准攞 `image.width/height` 做呢個數。** 嗰兩個講嘅係 **master**（`<sha>.webp`），
     `<img width height>` attribute 亦係佢；但桌面 `srcset` 實揀 `_600.webp`，
     兩者可以唔同源 —— 實例：`cmc_7dfda19a20c0544df9c8d85b` master 719×1000，
     `_600` 係 429×600（variant 全部正規化落同一個框）。放大倍數要用**真係載落嚟嗰張**做分母。
     另外 `market-assets/` 仲有原盒／產品圖（`box-subset.json` 307 件，多數 600×438 橫圖），
     所以只認 seed-snapshot 有 sha 對得返嘅卡。 */
  const wantW = parseFloat(railSrcW);
  const wantH = Math.round((wantW * railArH) / railArW);
  let checkedFiles = 0;
  for (const c of cards) {
    const sha = c.image?.sha256;
    if (!sha) { failed.push(`T5: ${c.id ?? "?"} 冇 image sha`); continue; }
    const file = join(dir, `${sha}_600.webp`);
    if (!existsSync(file)) continue;                 // 冇同步落嚟就唔喺呢個檔嘅守備範圍
    const d = webpSize(readFileSync(file).subarray(0, 64));
    checkedFiles += 1;
    check("T5: 桌面實際載嗰張 `_600.webp` = `--rail-src-w` × `--rail-ar-h/--rail-ar-w`",
      d && d.w === wantW && d.h === wantH,
      `${c.id} 檔頭=${d ? `${d.w}x${d.h}` : "讀唔到"} token 講=${wantW}x${wantH}`);
  }
  check("T5: 真係開過檔驗（一個檔都冇開就唔准當佢過）", checkedFiles > 0, `開咗 ${checkedFiles} 個`);

  /* top100 只係 1604 分之 100。用一條「數量對得返」嘅身份式覆蓋埋其餘嗰 1504：
     卡面 variant 全部同一個尺寸，所以呢個尺寸嘅 `_600.webp` 應該啱啱好 = universe 成員數。
     有人重 bake 成另一個尺寸嘅話呢個數即刻散，唔使等人肉眼睇到卡圖糊咗。 */
  const files = existsSync(dir) ? readdirSync(dir).filter((f) => f.endsWith("_600.webp")) : [];
  let cohort = 0;
  for (const f of files) {
    const d = webpSize(readFileSync(join(dir, f)).subarray(0, 64));
    if (d && d.w === wantW && d.h === wantH) cohort += 1;
  }
  const members = Number(snapshot.universe?.memberCount);
  check("T5: 卡面尺寸嘅 `_600.webp` 數量 = universe 成員數（覆蓋 top100 以外嗰 1504 張）",
    Number.isFinite(members) && cohort === members, `${wantW}×${wantH} 有 ${cohort} 個，universe 有 ${members}`);
}

/* ─────────────────────────────────────────────────────────────
 * T6 — JSX：class 落咗、DOM 次序冇變、metrics/資料時間係 `<article>` 直屬 child
 * ───────────────────────────────────────────────────────────── */
check("T6: `<article>` 帶 `detail-grid detail-grid-rail`",
  /<article className="detail-grid detail-grid-rail">/.test(tsx));

{
  const at = (needle) => tsx.indexOf(needle);
  const iArticle = at('<article className="detail-grid detail-grid-rail">');
  const iContent = at('className="detail-content"');
  const iChart = at("<HistoryChart");
  const iMetrics = at('className="detail-metrics"');
  const iTime = at('className="data-time"');
  const iProse = at('className="detail-prose"');
  const order = [iArticle, iContent, iChart, iMetrics, iTime, iProse];
  check("T6: 六個定位點都搵得返", order.every((n) => n >= 0), JSON.stringify(order));
  check("T6: DOM 次序 = 卡圖 → 內容 → 走勢圖 → 市值/數量 → 資料時間 → 簡介（一個字都唔准掉轉）",
    order.every((n, k) => k === 0 || n > order[k - 1]), JSON.stringify(order));

  /* `.detail-content` 一定要喺走勢圖之後、metrics 之前收口 —— 呢個就係「metrics 升做
     `<article>` 直屬 child」嘅唯一實現。收口漏咗嘅話 metrics 仲喺右欄，
     `.detail-grid-rail > .detail-metrics` 揀唔中，全版 KPI 條靜靜唔生效。 */
  if (iChart > 0 && iMetrics > iChart) {
    const between = tsx.slice(iChart, iMetrics);
    check("T6: `.detail-content` 要喺 `<HistoryChart>` 之後、metrics 之前收口（`</div>`）",
      (between.match(/<\/div>/g) || []).length === 1,
      `走勢圖同 metrics 之間有 ${(between.match(/<\/div>/g) || []).length} 個 </div>`);
  }
}

check("T6: 唔准喺 card-detail.tsx 度用 inline `order`",
  !/style=\{\{[^}]*\border\b/.test(tsx) && !/\border:\s*-?\d/.test(tsx));

/* ─────────────────────────────────────────────────────────────
 * T7 — （opt-in）`--live`：開 headless Chromium 量真嘢
 * ───────────────────────────────────────────────────────────── */
if (process.argv.includes("--live")) {
  const base = process.env.RAIL_BASE_URL ?? "http://localhost:3901";
  const exe = process.env.RAIL_CHROME ?? "";
  const { chromium } = await import("playwright").catch(() => ({ chromium: null }));
  if (!chromium) {
    failed.push("T7: --live 要 playwright，但 import 唔到");
  } else {
    const browser = await chromium.launch(exe ? { executablePath: exe } : {});
    const page = await browser.newPage({ viewport: { width: 1920, height: 1000 }, deviceScaleFactor: 1 });
    const ids = (process.env.RAIL_CARD_IDS ?? "").split(",").filter(Boolean);
    if (!ids.length) {
      const list = JSON.parse(read("data/public/seed-snapshot.json"));
      ids.push(...(list.top100 ?? []).slice(0, 4).map((c) => c.id ?? c.cardId).filter(Boolean));
    }
    for (const id of ids) {
      for (const w of [981, 1280, 1440, 1920]) {
        await page.setViewportSize({ width: w, height: 1000 });
        await page.goto(`${base}/card/${encodeURIComponent(id)}?lang=zh-TW`, { waitUntil: "networkidle" });
        await page.addStyleTag({ content: ".reveal-pending{opacity:1 !important;animation:none !important}" });
        await page.waitForTimeout(200);
        const m = await page.evaluate(() => {
          const root = document.querySelector(".detail-page");
          const art = root.querySelector(".detail-art").getBoundingClientRect();
          const content = root.querySelector(".detail-content").getBoundingClientRect();
          const img = root.querySelector(".detail-art img");
          const metrics = root.querySelector(".detail-metrics");
          const cells = [...metrics.children].map((k) => ({ y: Math.round(k.getBoundingClientRect().y * 10) / 10 }));
          const rows = [...new Set(cells.map((c) => c.y))];
          const cs = getComputedStyle(img);
          const r = img.getBoundingClientRect();
          const cw = r.width - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
          const ch = r.height - parseFloat(cs.paddingTop) - parseFloat(cs.paddingBottom);
          // 分母用 `--rail-src-w`（= 桌面實際載嗰張 `_600` 嘅闊度）。
          // 唔用 naturalWidth（srcset density correction）亦唔用 attribute（master 尺寸）—— 見檔頭 ③。
          const cssRoot = getComputedStyle(root.querySelector(".detail-grid-rail"));
          const srcW = parseFloat(cssRoot.getPropertyValue("--rail-src-w"));
          const arW = parseFloat(cssRoot.getPropertyValue("--rail-ar-w"));
          const arH = parseFloat(cssRoot.getPropertyValue("--rail-ar-h"));
          const s = Math.min(cw / srcW, ch / ((srcW * arH) / arW));
          return {
            dead: +(content.bottom - art.bottom).toFixed(2),
            upscale: +s.toFixed(4),
            rows: rows.length,
            lastRow: cells.filter((c) => c.y === rows[rows.length - 1]).length,
            cells: cells.length,
            clipped: [...metrics.querySelectorAll("span,strong")].filter((e) => e.scrollWidth > e.clientWidth + 0.5).length,
            fonts: [...new Set([...metrics.querySelectorAll("strong")].map((e) => getComputedStyle(e).fontSize))].length,
          };
        });
        const tag = `${id}@${w}`;
        check(`T7 ${tag}: 死空間 ≈ 0`, Math.abs(m.dead) <= 1, `${m.dead}px`);
        check(`T7 ${tag}: 放大倍數 ≤ --rail-upscale`, m.upscale <= railUpscale + 1e-3, `${m.upscale}`);
        check(`T7 ${tag}: KPI 條 1 或 2 行`, m.rows === 1 || m.rows === 2, `${m.rows} 行`);
        check(`T7 ${tag}: 最尾一行冇孤兒格`, m.rows === 1 || m.lastRow >= 2, `最尾行得 ${m.lastRow}/${m.cells} 格`);
        check(`T7 ${tag}: 冇數值被切走`, m.clipped === 0, `${m.clipped} 個`);
        check(`T7 ${tag}: 一行入面得一隻字級`, m.fonts === 1, `${m.fonts} 隻`);
      }
    }
    await browser.close();
  }
}

if (failed.length) {
  console.error(`FAIL test-fe-detail-rail (${failed.length})`);
  for (const f of failed) console.error(` - ${f}`);
  process.exit(1);
}
console.log(`PASS test-fe-detail-rail (${railRules.length} 條 rail rule)`);
