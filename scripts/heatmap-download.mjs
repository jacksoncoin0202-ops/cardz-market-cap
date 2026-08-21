#!/usr/bin/env node
/*
 * 熱力圖落載 CLI —— 唔使開網站、唔使撳掣。
 *
 * owner 2026-08-21：「我想可以打個 CLI 過去，即係唔使上 website。我直接 call CLI
 * 可以攞到呢一堆參數，跟住去下載對應嘅圖（4K 或者 1080p）。」
 *
 *   node scripts/heatmap-download.mjs --res 4k --period 7d --scope pokemon --lang zh-TW
 *   node scripts/heatmap-download.mjs --list          # 有邊啲值可以揀
 *   node scripts/heatmap-download.mjs --help
 *
 * 出嚟嘅係一個 JPEG 檔 + 一行講清楚你實際攞到咩（真實闊×高、幾多 bytes、個戳寫住
 * 幾點、行咗幾耐）。
 *
 * ── 兩件唔明講就會咬親人嘅事 ─────────────────────────────────────────────
 * 1. **4K 慢。** 真 2× render，唔係放大。實測（40 格、post）：1080p 4–5 秒、
 *    4K 50–57 秒。所以呢度 default timeout 係 180 秒，同瀏覽器嗰邊唔同 ——
 *    瀏覽器有 gateway 睇實，CLI 冇，可以慢慢等。同一組參數第二次會撞 server cache
 *    （~0.1 秒），但 `--stamp now`（預設）個戳精確到分鐘，過咗一分鐘就係新一張。
 * 2. **個戳預設係「而家」。** CLI 落載嘅圖右上角寫嘅係你 call 佢嗰一刻 + 你部機個
 *    時區（`--tz` 改）。想要資料日就 `--stamp data` —— 嗰個先係 og:image 同 HERMES
 *    條 cron 鏈用嘅模式。
 *
 * ── 邊個係權威 ───────────────────────────────────────────────────────────
 * 呢個檔**冇自己一份選項表**。所有可揀嘅值都係開機嗰陣由 `apps/web/src/lib/*.ts`
 * 讀返嚟（`readOptions()`）。改咗嗰邊，`--list` 同 `--help` 即刻跟；改到讀唔到，
 * `readOptions()` 會掟錯，唔會靜靜出一張空表（AGENTS.md 規矩 13）。
 * `scripts/test-fe-heatmap-resolution.mjs` 守住呢件事。
 */
import { existsSync, mkdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { dirname, isAbsolute, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

export const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const LIB = join(ROOT, "apps/web/src/lib");

const DEFAULT_BASE = "https://app.cardzmarketcap.com";
const DEFAULT_TIMEOUT_MS = 180_000;

const read = (rel) => readFileSync(join(LIB, rel), "utf8");

/** `export const NAME = ["a", "b"] as const;` → ["a","b"] */
function constArray(src, name) {
  const m = new RegExp(`export const ${name}\\s*=\\s*\\[([^\\]]*)\\]\\s*as const`).exec(src);
  if (!m) throw new Error(`heatmap-download: 讀唔到 ${name}（改咗名？改埋呢度同 test）`);
  return [...m[1].matchAll(/"([^"]+)"/g)].map((x) => x[1]);
}

/** `export type Name = "a" | "b";` → ["a","b"] */
function unionType(src, name) {
  const m = new RegExp(`export type ${name}\\s*=\\s*([^;]+);`).exec(src);
  if (!m) throw new Error(`heatmap-download: 讀唔到 type ${name}（改咗名？改埋呢度同 test）`);
  const vals = [...m[1].matchAll(/"([^"]+)"/g)].map((x) => x[1]);
  if (vals.length === 0) throw new Error(`heatmap-download: type ${name} 度攞唔到任何值`);
  return vals;
}

/** `wide: { width: 1200, height: 630 },` → {wide:{width,height}} */
function formatSizes(src) {
  const out = {};
  for (const m of src.matchAll(/(\w+):\s*\{\s*width:\s*(\d+),\s*height:\s*(\d+)\s*\}/g)) {
    out[m[1]] = { width: Number(m[2]), height: Number(m[3]) };
  }
  if (Object.keys(out).length === 0) throw new Error("heatmap-download: 讀唔到 FORMAT_SIZES");
  return out;
}

/** `"1080p": 1,` 喺 RESOLUTION_SCALE 入面 → {"1080p":1,"4k":2} */
function resolutionScale(src) {
  const block = /RESOLUTION_SCALE[^=]*=\s*\{([^}]*)\}/.exec(src);
  if (!block) throw new Error("heatmap-download: 讀唔到 RESOLUTION_SCALE");
  const out = {};
  for (const m of block[1].matchAll(/"([^"]+)":\s*([\d.]+)/g)) out[m[1]] = Number(m[2]);
  if (Object.keys(out).length === 0) throw new Error("heatmap-download: RESOLUTION_SCALE 空");
  return out;
}

export function readOptions() {
  const destinations = read("share-destinations.ts");
  const resolution = read("share-resolution.ts");
  const heatmapOg = read("heatmap-og.ts");
  const stamp = read("share-stamp.ts");
  const copy = read("share-copy.ts");
  const types = read("types.ts");
  return {
    period: constArray(types, "marketWindows"),
    format: constArray(destinations, "SHARE_FORMATS"),
    res: constArray(resolution, "SHARE_RESOLUTIONS"),
    lang: constArray(copy, "SHARE_LANGS"),
    stamp: constArray(stamp, "STAMP_MODES"),
    scope: unionType(heatmapOg, "HeatmapOgScope"),
    theme: unionType(heatmapOg, "HeatmapOgTheme"),
    updown: unionType(heatmapOg, "HeatmapOgUpDown"),
    sizes: formatSizes(destinations),
    scale: resolutionScale(resolution),
  };
}

function localTimeZone() {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

function parseArgs(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (!arg.startsWith("--")) throw new Error(`唔明呢個參數：${arg}（行 --help）`);
    const key = arg.slice(2);
    if (key === "help" || key === "list") { out[key] = true; continue; }
    const value = argv[i + 1];
    if (value === undefined || value.startsWith("--")) throw new Error(`--${key} 後面冇值`);
    out[key] = value;
    i += 1;
  }
  return out;
}

function help(opts) {
  const tz = localTimeZone();
  return [
    "熱力圖落載 CLI —— 一句攞張圖，唔使開網站。",
    "",
    "  node scripts/heatmap-download.mjs [選項]",
    "",
    `  --res       ${opts.res.join(" | ")}   （預設 ${opts.res[0]}）`,
    `  --period    ${opts.period.join(" | ")}   （預設 7d）`,
    `  --show      10–100 格   （預設 40）`,
    `  --scope     ${opts.scope.join(" | ")}   （預設 all）`,
    `  --format    ${opts.format.join(" | ")}   （預設 post＝4:5）`,
    `  --theme     ${opts.theme.join(" | ")}   （預設 dark）`,
    `  --updown    ${opts.updown.join(" | ")}   （預設 green-up）`,
    `  --lang      ${opts.lang.join(" | ")}   （預設 zh-TW）`,
    `  --stamp     ${opts.stamp.join(" | ")}   （預設 now＝你 call 嗰一刻）`,
    `  --tz        IANA 時區   （預設跟部機：${tz}）`,
    "  --out       出邊度：檔案路徑，或者一個資料夾（入面用自動檔名）   （預設 ./<自動檔名>.jpg）",
    `  --base      邊個站   （預設 ${DEFAULT_BASE}）`,
    `  --timeout   幾多毫秒   （預設 ${DEFAULT_TIMEOUT_MS}，4K 要 50 秒以上）`,
    "  --list      印晒所有可揀嘅值同真實像素",
    "",
    "  例：",
    "    node scripts/heatmap-download.mjs --res 4k --period 30d --scope pokemon",
    "    node scripts/heatmap-download.mjs --res 4k --format status --out ~/story.jpg",
    "    node scripts/heatmap-download.mjs --base http://localhost:3901 --res 4k",
    "",
    "  ⚠️ 4K 係真 2× render 唔係放大：實測 50–57 秒（1080p 4–5 秒）。",
  ].join("\n");
}

function list(opts) {
  const lines = ["可揀嘅值（由 apps/web/src/lib 讀返嚟，唔係呢個檔自己寫）：", ""];
  for (const key of ["res", "format", "period", "scope", "theme", "updown", "lang", "stamp"]) {
    lines.push(`  --${key.padEnd(8)} ${opts[key].join(" | ")}`);
  }
  lines.push("", "  每個比例 × 每個清晰度 真實出幾多像素：", "");
  const pad = Math.max(...opts.format.map((f) => f.length));
  for (const format of opts.format) {
    const spec = opts.sizes[format];
    if (!spec) continue;
    const cells = opts.res.map((res) => {
      const s = opts.scale[res] ?? 1;
      return `${res}=${Math.round(spec.width * s)}×${Math.round(spec.height * s)}`;
    });
    lines.push(`    ${format.padEnd(pad)}  ${cells.join("   ")}`);
  }
  return lines.join("\n");
}

/** 檔名跟 `lib/heatmap-og.ts heatmapOgFilename()` 同一條規矩（4K 加後綴，1080p 唔加）。 */
export function outputName({ scope, show, period, format, res, sizes }) {
  const spec = sizes[format] ?? { width: 1080, height: 1350 };
  const ratio = spec.width > spec.height ? "wide" : spec.height / spec.width > 1.5 ? "9x16" : "4x5";
  const suffix = res === "1080p" ? "" : `-${res.toLowerCase()}`;
  return `cardz-heatmap-${scope}-top${show}-${period}-${ratio}${suffix}.jpg`;
}

async function main() {
  const opts = readOptions();
  let args;
  try {
    args = parseArgs(process.argv.slice(2));
  } catch (err) {
    console.error(err.message);
    process.exit(2);
  }
  if (args.help) { console.log(help(opts)); return; }
  if (args.list) { console.log(list(opts)); return; }

  const q = {
    period: args.period ?? "7d",
    show: String(args.show ?? 40),
    scope: args.scope ?? "all",
    format: args.format ?? "post",
    theme: args.theme ?? "dark",
    updown: args.updown ?? "green-up",
    lang: args.lang ?? "zh-TW",
    res: args.res ?? opts.res[0],
    stamp: args.stamp ?? "now",
  };
  if (q.stamp === "now") q.tz = args.tz ?? localTimeZone();

  /* 打錯字唔係死罪（server 有 fallback，唔會 500），但一定要嘈 —— 靜靜攞到另一張圖
     先係最難查嗰種。照送出去，等 server 講返佢實際行咗咩。 */
  for (const key of ["period", "scope", "format", "theme", "updown", "lang", "res", "stamp"]) {
    if (!opts[key].includes(q[key])) {
      console.warn(`⚠️  --${key} ${q[key]} 唔喺認得嘅名單（${opts[key].join(" | ")}）—— 照送，睇下面 server 講返行咗咩`);
    }
  }

  const base = (args.base ?? DEFAULT_BASE).replace(/\/+$/, "");
  const url = `${base}/api/og/heatmap?${new URLSearchParams(q).toString()}`;
  const timeout = Number(args.timeout ?? DEFAULT_TIMEOUT_MS);
  const scale = opts.scale[q.res] ?? 1;
  const spec = opts.sizes[q.format];
  const expect = spec ? `${Math.round(spec.width * scale)}×${Math.round(spec.height * scale)}` : "?";

  console.log(`→ ${url}`);
  console.log(`  預期 ${q.res}（${expect}）${scale > 1 ? "，真 2× render，可能要 50 秒以上" : ""}`);

  const started = Date.now();
  let response;
  try {
    response = await fetch(url, { signal: AbortSignal.timeout(timeout) });
  } catch (err) {
    const why = err?.name === "TimeoutError" ? `等咗 ${timeout}ms 都未返` : err?.message ?? String(err);
    console.error(`✗ 攞唔到：${why}`);
    if (scale > 1) console.error("  4K 慢，如果係 timeout：加 --timeout 300000，或者 --base 指去本地 dev server。");
    process.exit(1);
  }
  if (!response.ok) {
    console.error(`✗ HTTP ${response.status} ${response.statusText}`);
    process.exit(1);
  }
  const bytes = Buffer.from(await response.arrayBuffer());
  const elapsed = ((Date.now() - started) / 1000).toFixed(1);

  const h = (name) => response.headers.get(name) ?? "";
  const stampText = h("x-og-stamp") ? decodeURIComponent(h("x-og-stamp")) : "";
  const gotW = h("x-og-width");
  const gotH = h("x-og-height");

  const defaultName = outputName({ scope: h("x-og-scope") || q.scope, show: h("x-og-show") || q.show,
    period: h("x-og-period") || q.period, format: h("x-og-format") || q.format,
    res: h("x-og-res") || q.res, sizes: opts.sizes });
  /*
   * `--out` 收檔案路徑**或者**資料夾。指住個 folder 係好自然嘅寫法（`--out ~/Desktop`），
   * 唔認就係一句 `EISDIR: illegal operation on a directory` 掉出嚟 —— 睇嘅人淨係知
   * 「炸咗」，唔知自己差咗個檔名。指住 folder 就攞返自動檔名擺入去。
   */
  const requested = args.out
    ? (isAbsolute(args.out) ? args.out : resolve(process.cwd(), args.out))
    : resolve(process.cwd(), defaultName);
  const intoDir = args.out
    ? (existsSync(requested) && statSync(requested).isDirectory()) || /[\\/]$/.test(args.out)
    : false;
  const outPath = intoDir ? join(requested, defaultName) : requested;
  mkdirSync(dirname(outPath), { recursive: true });
  writeFileSync(outPath, bytes);

  console.log(`✓ ${gotW}×${gotH}  ${h("x-og-res")}  ${(bytes.length / 1024).toFixed(0)} KB  ${elapsed}s  (cache ${h("x-og-cache")})`);
  console.log(`  戳：${stampText}${h("x-og-tz") ? `（${h("x-og-tz")}）` : "（資料日）"}`);
  console.log(`  generation ${h("x-og-generation")}`);
  console.log(`  ${outPath}`);

  /* 攞到嘅同要求嘅唔同 = server 幫你 fallback 咗。唔准當冇事發生。 */
  if (h("x-og-res") && h("x-og-res") !== q.res) {
    console.warn(`⚠️  你要 ${q.res}，server 出咗 ${h("x-og-res")}`);
  }
  if (spec && (Number(gotW) !== Math.round(spec.width * scale) || Number(gotH) !== Math.round(spec.height * scale))) {
    console.warn(`⚠️  預期 ${expect}，實際 ${gotW}×${gotH}`);
  }
}

const isMain = process.argv[1] && resolve(process.argv[1]) === resolve(fileURLToPath(import.meta.url));
if (isMain) {
  main().catch((err) => {
    console.error(`heatmap-download: ${err?.message ?? err}`);
    process.exit(1);
  });
}
