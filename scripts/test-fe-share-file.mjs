#!/usr/bin/env node
/*
 * 分享圖交付路徑嘅行為契約（owner 2026-08-19 報：「分享圖好耐都未 download 到，
 * upload 咗好耐跟住直頭冇反應，撳幾多次都冇用」，熱力圖同每張卡都一樣）。
 *
 * 三條 bug 加埋先砌得成嗰個症狀，所以三條都要有閘：
 *   1. 桌面照行 Web Share —— Windows 11 三隻瀏覽器 share `files` 一律
 *      NotAllowedError（mdn/browser-compat-data#21312），而 canShare 之前仲回 true。
 *   2. `URL.revokeObjectURL()` 緊接住 `anchor.click()` 同步行 —— 落載係 async 開始，
 *      條 URL 喺瀏覽器攞之前就冇咗，表現係「乜都冇發生」，冇 error 冇 log。
 *   3. 冇任何 timeout —— 條 promise 唔 settle，個掣就永遠 busy + disabled，
 *      連再撳嘅機會都冇。
 *
 * 呢個 test **真係行** `shareImageBlob()`（Node 24 原生 strip types，直接 import .ts），
 * 唔係 grep 個 source 算數。run_all_tests.py 自動 glob，冇參數。
 */
import { readFileSync } from "node:fs";
import { registerHooks } from "node:module";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

/* Node 24 識直接行 .ts（原生 strip types），但唔識 TS 嗰種「唔寫副檔名」嘅相對
   import（`./tile-style`）。補返個 resolve hook，之後所有 src .ts 都 import 得。 */
registerHooks({
  resolve(specifier, context, next) {
    if (specifier.startsWith(".") && !/\.[a-z]+$/i.test(specifier)) {
      try { return next(`${specifier}.ts`, context); } catch { /* 跌返原本 specifier */ }
    }
    return next(specifier, context);
  },
});

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const read = (path) => readFileSync(join(ROOT, path), "utf8");
const failed = [];
const check = (label, condition, detail = "") => {
  if (!condition) failed.push(detail ? `${label} —— ${detail}` : label);
};

/* ── 假 DOM ───────────────────────────────────────────────────────────────
   淨係做 shareImageBlob 掂得到嘅嘢。`log` 記低次序，因為「append 咗未先 click」
   同「revoke 咗未先 return」兩條都係次序問題，唔係「有冇 call」問題。 */
function makeEnv({ mobile, share, canShare = () => true, supportsDownload = true, openReturns = {} } = {}) {
  const log = [];
  const live = new Set();
  const anchors = [];
  /* Node 24 個 `navigator` 係 getter-only accessor，直接賦值會掟 TypeError。 */
  const define = (name, value) => Object.defineProperty(globalThis, name, { value, configurable: true, writable: true });
  define("navigator", {
    userAgent: mobile ? "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Safari/605.1" : "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/148.0",
    userAgentData: { mobile },
    maxTouchPoints: mobile ? 5 : 0,
    clipboard: { writeText: async () => { log.push("clipboard"); } },
    ...(share ? { share: (data) => { log.push("share"); return share(data); }, canShare: (data) => canShare(data) } : {}),
  });
  define("document", {
    body: {
      appendChild: (node) => { log.push("append"); node.parent = "body"; },
    },
    createElement: () => {
      const anchor = {
        style: {},
        parent: null,
        click() { log.push(`click(parent=${this.parent})`); },
        remove() { log.push("remove"); this.parent = null; },
      };
      if (supportsDownload) anchor.download = "";
      anchors.push(anchor);
      return anchor;
    },
  });
  define("window", { open: (...args) => { log.push("open"); return openReturns ? { args } : null; } });
  const realCreate = URL.createObjectURL.bind(URL);
  const realRevoke = URL.revokeObjectURL.bind(URL);
  URL.createObjectURL = (blob) => { const url = realCreate(blob); live.add(url); log.push("createObjectURL"); return url; };
  URL.revokeObjectURL = (url) => { live.delete(url); realRevoke(url); log.push("revokeObjectURL"); };
  return { log, live, anchors, restore: () => { URL.createObjectURL = realCreate; URL.revokeObjectURL = realRevoke; } };
}

const { shareImageBlob, filenameFor } = await import(`file://${join(ROOT, "apps/web/src/lib/share-file.ts").replaceAll("\\", "/")}`);
const blob = (type = "image/png") => new Blob([new Uint8Array(64)], { type });
const opts = { filenameBase: "cardz-x", title: "t", text: "t\nhttps://x", clipboardFallbackText: "https://x" };

/* ── B1：桌面唔准掂 Web Share ──
   呢條就係 owner 部機嗰條路。舊 code 喺呢個環境會行 share() 等 Windows flyout。 */
{
  const env = makeEnv({ mobile: false, share: async () => undefined });
  const outcome = await shareImageBlob(blob(), opts);
  check("B1: 桌面唔准 call navigator.share", !env.log.includes("share"), `log=${env.log.join(",")}`);
  check("B1: 桌面要直接落載", outcome === "downloaded", `outcome=${outcome}`);
  env.restore();
}

/* ── B2：落載次序 —— 要入咗 DOM 先 click（detached <a> 喺 Firefox 唔會落載） ── */
{
  const env = makeEnv({ mobile: false });
  await shareImageBlob(blob(), opts);
  check("B2: append 喺 click 之前", env.log.indexOf("append") < env.log.indexOf("click(parent=body)"), `log=${env.log.join(",")}`);
  check("B2: click 嗰刻 anchor 喺 body 入面", env.log.includes("click(parent=body)"), `log=${env.log.join(",")}`);
  check("B2: download 檔名有落格", env.anchors[0]?.download === "cardz-x.png", `download=${env.anchors[0]?.download}`);
  env.restore();
}

/* ── B3：**唔准**同步 revoke（呢條就係「撳完乜都冇發生」嘅根因） ── */
{
  const env = makeEnv({ mobile: false });
  await shareImageBlob(blob(), opts);
  check("B3: shareImageBlob 返嗰刻 objectURL 仲生勾勾", env.live.size === 1, `live=${env.live.size} log=${env.log.join(",")}`);
  check("B3: 冇喺同一個 task 入面 revoke", !env.log.includes("revokeObjectURL"), `log=${env.log.join(",")}`);
  env.restore();
}

/* ── B4：手機先至行 share sheet ── */
{
  const env = makeEnv({ mobile: true, share: async () => undefined });
  const outcome = await shareImageBlob(blob(), opts);
  check("B4: 手機要出 share sheet", outcome === "shared" && env.log.includes("share"), `outcome=${outcome} log=${env.log.join(",")}`);
  check("B4: share 成功就唔好再落載", !env.log.includes("append"), `log=${env.log.join(",")}`);
  env.restore();
}

/* ── B5：用戶撳走 share sheet 唔算錯，亦唔准偷偷落載 ── */
{
  const env = makeEnv({ mobile: true, share: async () => { throw new DOMException("x", "AbortError"); } });
  const outcome = await shareImageBlob(blob(), opts);
  check("B5: AbortError = dismissed", outcome === "dismissed", `outcome=${outcome}`);
  check("B5: dismissed 唔准落載", !env.log.includes("append"), `log=${env.log.join(",")}`);
  env.restore();
}

/* ── B6：share 炒（NotAllowedError）要跌落落載，唔准靜靜死 ── */
{
  const env = makeEnv({ mobile: true, share: async () => { throw new DOMException("Permission denied", "NotAllowedError") } });
  const outcome = await shareImageBlob(blob(), opts);
  check("B6: NotAllowedError 跌返落載", outcome === "downloaded" && env.log.includes("click(parent=body)"), `outcome=${outcome} log=${env.log.join(",")}`);
  env.restore();
}

/* ── B7：老 webview 冇 `<a download>` → 開新分頁；彈窗被擋 → 要嗌，唔准扮成功 ── */
{
  const env = makeEnv({ mobile: false, supportsDownload: false });
  const outcome = await shareImageBlob(blob(), opts);
  check("B7: 冇 download 屬性就開新分頁", outcome === "opened" && env.log.includes("open"), `outcome=${outcome} log=${env.log.join(",")}`);
  env.restore();
}
{
  const env = makeEnv({ mobile: false, supportsDownload: false, openReturns: null });
  let threw = false;
  try { await shareImageBlob(blob(), opts); } catch { threw = true; }
  check("B7: 彈窗被擋要掟 error（唔准靜靜當交咗貨）", threw);
  check("B7: 掟之前要 revoke 返條 URL", env.live.size === 0, `live=${env.live.size}`);
  env.restore();
}

/* ── B8：熱力圖嗰條 —— 一張永遠唔 load 嘅圖唔准拖死成個 export ──
   舊 defaultLoadImage 得 onload/onerror，stall 咗嘅 request 兩個都唔 fire。 */
{
  class DeadImage { set src(_value) { /* 永遠唔 fire */ } }
  globalThis.Image = DeadImage;
  const { defaultLoadImage } = await import(`file://${join(ROOT, "apps/web/src/lib/share-image.ts").replaceAll("\\", "/")}`);
  const started = Date.now();
  const settled = await Promise.race([
    defaultLoadImage("https://example.invalid/never.png").then(() => "settled"),
    new Promise((r) => setTimeout(() => r("hung"), 12_000)),
  ]);
  check("B8: 吊死嘅圖會逾時回 null，唔會 pending 到天光", settled === "settled", `settled=${settled} after ${Date.now() - started}ms`);
  delete globalThis.Image;
}

/* ── B9：副檔名一定要跟返 blob 個 MIME ──
   2026-08-20 卡片分享圖由 PNG 轉 JPEG（og route `JPEG_QUALITY`），而 card-detail
   嗰句檔名本來寫死 `.png`。一個 `.png` 入面裝住 JPEG bytes 唔會即刻爆 —— 爆喺
   iOS 相簿匯入嗰刻，用戶見到係「張圖存唔到落相簿」。所以副檔名冇得由叫方講。 */
{
  check("B9: image/jpeg → .jpg（唔係 .jpeg）", filenameFor("a", "image/jpeg") === "a.jpg", filenameFor("a", "image/jpeg"));
  check("B9: image/png → .png", filenameFor("a", "image/png") === "a.png", filenameFor("a", "image/png"));
  check("B9: image/webp → .webp", filenameFor("a", "image/webp") === "a.webp", filenameFor("a", "image/webp"));
  check("B9: 帶參數嘅 MIME 照拆得開", filenameFor("a", "image/jpeg; charset=binary") === "a.jpg", filenameFor("a", "image/jpeg; charset=binary"));
  check("B9: 空 MIME 跌返 .png", filenameFor("a", "") === "a.png", filenameFor("a", ""));

  /* 唔係淨係驗個 helper —— 驗佢真係接返落落載路徑（有檢查但零 call site 就當冇檢查）。 */
  const env = makeEnv({ mobile: false });
  await shareImageBlob(blob("image/jpeg"), opts);
  check("B9: JPEG blob 落載出 .jpg", env.anchors[0]?.download === "cardz-x.jpg", `download=${env.anchors[0]?.download}`);
  env.restore();

  /* 叫方唔准喺 base 度自己寫副檔名（會變 `x.png.jpg`）。tsc 攔唔到呢種。 */
  for (const file of ["apps/web/src/components/card-detail.tsx", "apps/web/src/components/heatmap.tsx"]) {
    const source = read(file);
    const bases = [...source.matchAll(/filenameBase[:\s=]+`([^`]*)`/g)].map((m) => m[1]);
    const usesFilenameHelper = /const\s+filenameBase\s*=\s*heatmapOgFilename\s*\(/.test(source)
      && /shareImageBlob\([\s\S]{0,800}?\{[\s\S]{0,200}?\bfilenameBase\s*,/.test(source);
    check(`B9: ${file} 有傳 filenameBase`, bases.length > 0 || usesFilenameHelper);
    for (const base of bases) {
      check(`B9: ${file} 個 filenameBase 冇自己加副檔名`, !/[.](png|jpe?g|webp|gif|avif)$/i.test(base), `base=${base}`);
    }
  }
  const { heatmapOgFilename } = await import(`file://${join(ROOT, "apps/web/src/lib/heatmap-og.ts").replaceAll("\\", "/")}`);
  const heatmapBase = heatmapOgFilename();
  check("B9: heatmap helper 個 filenameBase 冇自己加副檔名", !/[.](png|jpe?g|webp|gif|avif)$/i.test(heatmapBase), `base=${heatmapBase}`);
}

/* ── S1/S2：行唔到嘅兩處（React component / fetch）用 call site 驗 ──
   「有檢查但零 call site 就當冇檢查」——所以驗嘅係佢真係包住 onCopy，唔係得個常數。 */
{
  const copyButton = read("apps/web/src/components/copy-button.tsx");
  check("S1: CopyButton 有死鎖閘常數", /COPY_TIMEOUT_MS\s*=\s*[\d_]+/.test(copyButton));
  check("S1: 死鎖閘真係包住 onCopy()", /await\s+withTimeout\(\s*Promise\.resolve\(onCopy\(\)\)\s*,\s*COPY_TIMEOUT_MS\s*\)/.test(copyButton));
  const cardDetail = read("apps/web/src/components/card-detail.tsx");
  /* 2026-08-22：條 fetch 抽咗落 `lib/share-fetch.ts`（熱力圖一齊用，規矩 13）。
     守嘅嘢冇鬆：卡片內頁砌完條 path 要真係餵入去，而個 signal 要喺共用嗰邊。
     順帶釘死「卡片內頁唔准自己再寫一個 timeout」—— 舊嗰個 20 秒喺 4K（實測
     100–126 秒）就係「揀 4K 一定 fail」，冇 error 冇 log。 */
  const shareFetch = read("apps/web/src/lib/share-fetch.ts");
  check("S2: 分享圖 fetch 有 abort signal",
    /format=\$\{FORMAT_QUERY_NAME\[format\]\}&res=\$\{res\}[^`]{0,80}`;/.test(cardDetail)
    && /fetchShareBlob\(path, res, "card OG"\)/.test(cardDetail)
    && /AbortSignal\.timeout\(per\)/.test(shareFetch));
  check("S2a: 卡片內頁冇自己再寫一份 fetch/timeout",
    !/AbortSignal\.timeout/.test(cardDetail) && !/SHARE_FETCH_TIMEOUT_MS/.test(cardDetail));
}

if (failed.length) {
  console.error(`FAIL test-fe-share-file (${failed.length})`);
  for (const line of failed) console.error(`  - ${line}`);
  process.exit(1);
}
console.log("PASS test-fe-share-file — 桌面直落載、objectURL 延後 revoke、share sheet 只喺手機、圖逾時唔拖死 export");
/* 落載路徑排低咗條 60 秒 revoke timer（REVOKE_DELAY_MS）——瀏覽器要，但喺 Node 度
   會拖住 event loop 唔收工，成個 test 由 <10s 變 60s。驗完就直接收線。 */
process.exit(0);
