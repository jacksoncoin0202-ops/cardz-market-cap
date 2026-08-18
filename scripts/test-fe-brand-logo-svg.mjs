#!/usr/bin/env node
/*
 * Brand wordmark SVG 契約（fe05(logo-svg)，2026-08-18）—— 純靜態，唔開瀏覽器、唔使 dev server，
 * 由 run_all_tests.py glob `scripts/test-*.mjs` 入 `npm test`。
 *
 * 呢個檔守嘅全部係「一錯就靜靜出錯圖、冇 error 冇 warning、CI 照綠」嗰種：
 *  ① 兩個 SVG 要喺度（OG route + share-image 兩個出口都直接讀佢，冇 fallback 落 PNG）
 *  ② viewBox 同 width/height 一定要對得住 —— 呢個係最靜嘅陷阱：淨改 viewBox 唔改 width/height，
 *     satori（resvg）同 Chrome 都會照舊 intrinsic ratio 去 fit，出嚟係 letterbox（logo 縮細 + 四邊留白）
 *  ③ 零 <text>／<script>／xlink:href／外連 URL —— <text> 等於「字體冇裝就出第二隻字」，
 *     其餘三樣喺 CSP（`img-src 'self' data: blob:`、`script-src` 冇 unsafe-inline）之下係靜靜唔畫
 *  ④ <defs> 每條有 id 嘅 path 一定要被 <use> 引用過；每個 <use href="#x"> 亦一定要指到真嘅 id。
 *     前者防「有人第日再貼一份帶死 path 嘅原稿返嚟」（dark 版嗰條死 path 自己就 62KB）；
 *     後者防改名改半路 —— dangling ref 唔會報錯，只係嗰層唔畫，logo 缺一截色
 *  ⑤ OG route 一定要用 `image/svg+xml;base64`。`svg+xml;charset=utf-8,` + encodeURIComponent
 *     行到 satori 內部個 `btoa` 會 `InvalidCharacterError` → OG 端點 500（實測）
 *  ⑥ share-image 兩個 skin 都指 SVG；header **仍然**指 `-h100.png`。呢個係刻意決定，唔係漏咗：
 *     header 最大只顯示 50px 高，231×100 嘅 PNG 已經係 2× 以上，轉 SVG 一粒銳度都賺唔到，
 *     但 light 版 first-paint 資產 brotli(q11) 由 9,098 → 17,016 B（**+7,918 B**）。
 *     ⚠️ dark 版方向係**相反**嘅（7,304 → 4,994，慳 2,310 B）—— 但 light 係默認面，
 *     而且要轉就兩版一齊轉先唔會半新半舊，所以整體照計唔抵。
 *  ⑦ 檔頭 provenance 個 `minified body sha256` 要對得返檔案真身 —— SVG 係人手加工過嘅衍生檔，
 *     冇呢個 stamp 就冇人知手上呢份係咪嗰份
 */
import { createHash } from "node:crypto";
import { existsSync, readFileSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const failed = [];
const check = (label, condition, detail) => { if (!condition) failed.push(detail ? `${label}: ${detail}` : label); };
const read = (path) => readFileSync(join(ROOT, path), "utf8");

const LOGOS = [
  { rel: "apps/web/public/brand/logo-cardz-marketcap.svg", label: "light" },
  { rel: "apps/web/public/brand/logo-cardz-marketcap-dark.svg", label: "dark" },
];

for (const { rel, label } of LOGOS) {
  const abs = join(ROOT, rel);
  // ① 兩個檔都要喺度：OG route 搵唔到就回 500、share-image 搵唔到就冇 logo 靜靜出圖
  check(`${label}: ${rel} present`, existsSync(abs));
  if (!existsSync(abs)) continue;

  const text = readFileSync(abs, "utf8");
  /* 檔頭嗰段廣東話 provenance 註釋入面本身寫住 `<text>`、`<use>` 呢啲字，掃描一定要由
     `<svg` 開始，否則掃到自己嘅註釋，永遠紅。 */
  const body = text.slice(text.indexOf("<svg"));
  check(`${label}: has an <svg> root`, body.startsWith("<svg"));

  // ⑦ provenance stamp：sha256 = 由 `<svg` 起、trim 過嘅 body
  const declaredSha = (text.match(/minified body sha256:\s*([0-9a-f]{64})/) || [])[1];
  const actualSha = createHash("sha256").update(body.trim()).digest("hex");
  check(`${label}: 檔頭有 minified body sha256`, Boolean(declaredSha));
  check(`${label}: body sha256 對得返檔頭（改過 SVG 就要順手改埋個 stamp）`,
    declaredSha === actualSha, `file=${actualSha} declared=${declaredSha ?? "(none)"}`);

  /* ② 最靜嗰個陷阱：viewBox 後兩個數 = width/height。唔一致嘅話 resvg / Chrome 用嘅
     intrinsic ratio 同真實畫布唔同 → letterbox，logo 縮細、四邊多咗留白，零 warning。 */
  const viewBox = (body.match(/\sviewBox="([^"]+)"/) || [])[1];
  const width = (body.match(/<svg[^>]*\swidth="([^"]+)"/) || [])[1];
  const height = (body.match(/<svg[^>]*\sheight="([^"]+)"/) || [])[1];
  check(`${label}: <svg> 有 viewBox`, Boolean(viewBox));
  check(`${label}: <svg> 有 width 屬性（冇 → Chrome 當 300×150 默認，share-image 個 logoW 即刻錯）`, Boolean(width));
  check(`${label}: <svg> 有 height 屬性`, Boolean(height));
  if (viewBox && width && height) {
    const vb = viewBox.trim().split(/[\s,]+/).map(Number);
    check(`${label}: viewBox 係 4 個數`, vb.length === 4 && vb.every(Number.isFinite), viewBox);
    check(`${label}: width == viewBox[2]`, Number(width) === vb[2], `width=${width} viewBox[2]=${vb[2]}`);
    check(`${label}: height == viewBox[3]`, Number(height) === vb[3], `height=${height} viewBox[3]=${vb[3]}`);
  }

  // ③ 零 <text>：SVG 入面嘅 <text> 要靠 render 環境有嗰隻字，冇就靜靜出第二隻字
  check(`${label}: zero <text>（wordmark 要 outlined，唔准靠字體）`, !/<text[\s>/]/.test(body));
  check(`${label}: zero <script>`, !/<script[\s>/]/i.test(body));
  // xlink:href 係 SVG 1.1 舊寫法，resvg 版本之間支援唔一致 —— 統一只准 SVG2 `href`
  check(`${label}: zero xlink:href（只准 SVG2 href）`, !/xlink:href/i.test(body));
  /* 外連 URL：`xmlns="http://www.w3.org/2000/svg"` 係必須嘅 namespace 宣告，唔算外連，
     所以先剝走所有 xmlns 宣告再掃。剩返任何 http / // = 真係想去攞外部資源，
     CSP `img-src 'self' data: blob:` 之下佢係靜靜唔畫，唔會有 error。 */
  const noNs = body.replace(/xmlns(:[\w-]+)?="[^"]*"/g, "");
  check(`${label}: zero external http(s) reference`, !/https?:/i.test(noNs));
  check(`${label}: zero protocol-relative //`, !noNs.includes("//"));

  /* ④ <defs> 入面每條有 id 嘅 path 都要真係被 <use> 過。原稿 dark 版有條 62KB 黑描邊 path
     坐喺 <defs> 度冇人用 —— 佢唔會 render，但每個請求都要派多 62KB。 */
  /* 一定要 matchAll 掃晒**每個** <defs> block，唔可以淨係攞第一個：覆核 2026-08-18 種過
     一條 fault —— 喺 </svg> 之前另開第二個 <defs> 藏一條死 path，單 match 版本照綠。
     現行兩個檔各只得一個 defs，所以呢句而家係防守性，唔係喺度修 live bug。 */
  const defs = [...body.matchAll(/<defs>([\s\S]*?)<\/defs>/g)].map((m) => m[1]).join("\n");
  check(`${label}: 有 <defs> block`, defs.length > 0);
  const defIds = [...defs.matchAll(/\sid="([^"]+)"/g)].map((m) => m[1]);
  const useRefs = [...body.matchAll(/<use[^>]*\shref="#([^"]+)"/g)].map((m) => m[1]);
  check(`${label}: <defs> 至少有一條有 id 嘅 path（唔好變空掃描）`, defIds.length > 0);
  check(`${label}: 至少有一個 <use>`, useRefs.length > 0);
  for (const id of defIds) {
    check(`${label}: <defs> #${id} 被 <use> 引用過（死 path = 白派 bytes）`,
      useRefs.includes(id), `<use> 只 reference 咗: ${useRefs.join(", ") || "(none)"}`);
  }
  for (const ref of useRefs) {
    // dangling <use> 唔會報錯，只係嗰層唔畫 —— logo 缺一截色而已，好易走漏眼
    check(`${label}: <use href="#${ref}"> 指到真嘅 id`, defIds.includes(ref), `已有 id: ${defIds.join(", ") || "(none)"}`);
  }

  console.error(`[info] ${rel} ${statSync(abs).size} bytes, viewBox="${viewBox}", ${defIds.length} def paths, ${useRefs.length} uses`);
}

/* ⑤ OG route（satori）。`;base64,` 係硬條件：換成 `svg+xml;charset=utf-8,` + encodeURIComponent
   會喺 satori 內部個 btoa 度炸 InvalidCharacterError，成個 OG 端點 500。 */
const OG_ROUTE = "apps/web/src/app/api/og/card/[id]/route.tsx";
/* 剝走 block comment 先掃：route 檔頭嗰段警告本身就要引用「唔准寫」嗰串字
   （`svg+xml;charset=…`），唔剝就會捉住自己嘅註釋，永遠紅。 */
const ogRoute = read(OG_ROUTE).replace(/\/\*[\s\S]*?\*\//g, "");
check("OG route 用 data:image/svg+xml;base64", ogRoute.includes("data:image/svg+xml;base64,"));
check("OG route 冇用 svg+xml;charset（btoa 會炸 InvalidCharacterError）", !/svg\+xml;\s*charset/i.test(ogRoute));
/* 驗**完整檔名**唔係驗 `.svg` 後綴：覆核 2026-08-18 種過 fault —— OG route 改指
   `-dark.svg`（白 wordmark 畫落 #f7f7f5 panel = 隱形），舊版後綴檢查照綠。
   OG panel 永遠係淺色底，所以呢兩路一定係 light skin。 */
const OG_LOGO_WANT = "brand/logo-cardz-marketcap.svg";
const ogLogoPaths = [...ogRoute.matchAll(/brand\/logo-cardz-marketcap[\w-]*\.(\w+)/g)].map((m) => m[0]);
check(`OG route 兩路 existsSync 都指 ${OG_LOGO_WANT}（light skin，唔准 -dark：白字畫落淺底 = 隱形）`,
  ogLogoPaths.length === 2 && ogLogoPaths.every((p) => p === OG_LOGO_WANT),
  ogLogoPaths.join(", ") || "(none)");

/* satori 唔理 SVG 自己嗰個 width/height（覆核實測：剝走都逐 px 一樣），所以呢兩個 declared
   數就係 OG wordmark 嘅**唯一**尺寸來源。原本零 assert —— 種 fault 改成 100×43，wordmark
   靜靜細一半，test 照綠。寫死喺度：要改版面就連呢行一齊改，改動先至被人睇見。 */
const OG_IMG_DIMS = [[280, 121], [200, 86]];
const ogImgs = [...ogRoute.matchAll(/<img\s+src=\{logoSrc\}[^>]*?width=\{(\d+)\}\s+height=\{(\d+)\}/g)]
  .map((m) => [Number(m[1]), Number(m[2])]);
check("OG route 有兩個 wordmark <img src={logoSrc}>", ogImgs.length === 2, JSON.stringify(ogImgs));
check(`OG wordmark declared 尺寸 = TextOnly 280×121 / Art 200×86`,
  JSON.stringify(ogImgs) === JSON.stringify(OG_IMG_DIMS),
  `實際 ${JSON.stringify(ogImgs)}`);

/* ⑥ share-image 轉 SVG（2496px canvas 上採樣 PNG 會糊）；header **唔准**轉 —— light 版
   first-paint 資產 brotli(q11) 9,098 → 17,016 B（+7,918 B）而視覺上零得着（header 最大 50px 高，
   PNG 已經 2×+）。呢個係刻意決定，唔係漏咗。 */
const shareImage = read("apps/web/src/lib/share-image.ts");
/* 同 OG 一樣要驗**邊個 skin 配邊個檔**，唔係淨驗 .svg：覆核種過 fault 將兩個 skin 掉轉
   （深色底用黑描邊版、淺色底用白版 = 兩邊都睇唔到），舊版後綴檢查照綠。 */
const SHARE_LOGO_WANT = { dark: "/brand/logo-cardz-marketcap-dark.svg", light: "/brand/logo-cardz-marketcap.svg" };
const shareSkins = [...shareImage.matchAll(/\b(dark|light):\s*\{[^}]*?logo:\s*"([^"]+)"/g)].map((m) => [m[1], m[2]]);
check("share-image 有兩個 skin.logo", shareSkins.length === 2, JSON.stringify(shareSkins));
for (const [skin, path] of shareSkins) {
  check(`share-image ${skin} skin 用 ${SHARE_LOGO_WANT[skin]}（掉轉 = 白字畫白底／黑描邊畫黑底）`,
    path === SHARE_LOGO_WANT[skin], `實際 ${path}`);
}

const header = read("apps/web/src/components/header.tsx");
const headerLogos = [...header.matchAll(/src:\s*"(\/brand\/[^"]+)"/g)].map((m) => m[1]);
check("header 兩個 logo src", headerLogos.length === 2, headerLogos.join(", "));
for (const path of headerLogos) {
  check(`header logo ${path} 仍然係 -h100.png（轉 SVG = first-paint +7.4 KB，刻意唔轉）`,
    path.endsWith("-h100.png"));
}

if (failed.length) {
  console.error("FAIL brand wordmark SVG contract:\n" + failed.map((item) => ` - ${item}`).join("\n"));
  process.exit(1);
}
console.log("PASS brand wordmark SVG contract (2 SVG, viewBox==width/height, no text/script/xlink/external, defs⇄use 兩邊對得晒, OG base64 svg+xml, share-image .svg, header 仍然 -h100.png, sha256 stamp 對得返)");
