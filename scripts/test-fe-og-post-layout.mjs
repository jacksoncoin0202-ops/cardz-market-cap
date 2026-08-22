#!/usr/bin/env node
/*
 * 分享圖排版契約（`api/og/card/[id]`）—— 2026-08-19。
 * 預設純靜態：唔開瀏覽器、唔使 dev server，由 run_all_tests.py glob `scripts/test-*.mjs` 收。
 * 加 `--live` 先會真係打條 route、逐 px 掃返張 PNG（要 dev server，見底部）。
 *
 * 背景：owner 睇住出街嗰張 4:5 講 ——「入面啲字大大細細、字體不一，感覺好奇怪」。
 * 拆開係兩件事，兩件都係**冇 error、冇 warning、CI 照綠**嗰種：
 *
 *  ① **同一個角色，兩個字級。** 四個數擺同一行、同一個 label 級、同一條分隔線之下，
 *     偏偏係 46/36/36/36；三個「大寫 tracked 細標籤」係 22/24/22。22 同 24 差 9% ——
 *     肉眼分唔出係有意定係手滑，但排埋一齊就係「大大細細」。守法唔係叫人小心啲，
 *     係**唔准喺 layout 入面撒 fontSize 數字**：一律行 `POST_TYPE` 三級（T1/T2/T3）。
 *
 *  ② **垂直預算靠估。** 舊註寫住「最壞情況 = 2 行 38px 標題……剩 92px」，但 `clampTitle`
 *     封嘅係 96 **字**唔係行數，38px 喺 968 闊度一行得 ~38 字 —— 96 字係 **3 行**。
 *     實測 3 行嗰啲卡 ink 去到 y=1313，衝穿底 padding 20px，張圖照 200 出街。
 *     所以呢度唔信任何加減數：`--live` 直接掃 PNG 嘅 ink bbox，四邊都要 ≥ padding。
 *     （T1–T4 靜態守「唔好再手滑」，T5 live 守「真係入唔入得晒」——兩層唔可以互相代替。）
 */
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const failed = [];
const check = (label, condition, detail) => { if (!condition) failed.push(detail ? `${label}: ${detail}` : label); };
const read = (rel) => readFileSync(join(ROOT, rel), "utf8");

const ROUTE_REL = "apps/web/src/app/api/og/card/[id]/route.tsx";
const route = read(ROUTE_REL);

/* 剝走註釋先掃 —— 唔剝嘅話上面成段中文註提到嘅「46/36」會當咗係 code。 */
const code = route.replace(/\/\*[\s\S]*?\*\//g, " ").replace(/(^|[^:])\/\/[^\n]*/g, "$1 ");

/**
 * `function Name(` 開始，回傳個 body（唔含註釋）。
 *
 * ⚠️ 唔可以攞 `indexOf("{", head)` 做開頭：呢批 component 個簽名係
 * `function PostLayout({ card, art, … }: { card: MarketCardView; … })` ——
 * 第一個 `{` 係參數解構，數大括號會喺型別註解收口嗰度提早收，攞到一段唔係 body 嘅嘢，
 * 跟住所有「body 入面搵唔到 X」嘅 assert 就會**假綠**（第一版就係噉：T2 報「得 0 個 Stat」
 * 但 T1 照過）。所以要等 paren depth 歸零之後嗰個 `{` 先算 body。
 */
function fnBody(src, name) {
  const head = src.indexOf(`function ${name}(`);
  if (head < 0) return null;
  let paren = 0;
  let i = src.indexOf("(", head);
  for (; i < src.length; i++) {
    if (src[i] === "(") paren++;
    else if (src[i] === ")" && --paren === 0) break;
  }
  const open = src.indexOf("{", i);
  if (open < 0) return null;
  let depth = 0;
  for (let j = open; j < src.length; j++) {
    if (src[j] === "{") depth++;
    else if (src[j] === "}" && --depth === 0) return src.slice(open, j + 1);
  }
  return null;
}

const post = fnBody(code, "PostLayout");
const wide = fnBody(code, "WideLayout");
/* 唔可以淨係 `!!post`：攞錯咗一段唔係 body 嘅嘢一樣係 truthy，跟住下面每條「搵唔到就過」
   嘅 assert 全部假綠。要驗返個 body 真係載住我哋要守嗰啲嘢。 */
check("T0: 攞到 PostLayout body", !!post && post.includes("<Stat") && post.includes("ArtStage"), post ? `${post.length} 字元` : "null");
check("T0: 攞到 WideLayout body", !!wide && wide.includes("<Stat") && wide.includes("ArtStage"), wide ? `${wide.length} 字元` : "null");

/* ─────────────────────────────────────────────────────────────
 * T1 — layout 入面唔准出現裸 font size 數字（守 ①）
 *
 * 呢條先係真正嘅閘。「四個數要同級」單獨守唔住 ——下一次手滑會係 caption、kicker、
 * 頁腳。所以規矩係：字級一律由檔頭嘅 `POST_TYPE` / `WIDE_TYPE` / `postTitleSize`
 * 出，layout body 入面一個 magic number 都唔准有。
 *
 * 兩種寫法都要掃：inline style 係 `fontSize: 18`，傳落 component 係 `fontSize={18}`。
 * 第一版淨係掃前者，`<ChartCaption fontSize={18} />` 就靜靜噉溜咗過。
 * ───────────────────────────────────────────────────────────── */
const NUMERIC_FS = /fontSize[:=]\s*\{?\s*\d/g;
const NUMERIC_VS = /valueSize=\{\s*\d/g;
for (const [name, body] of [["PostLayout", post], ["WideLayout", wide]]) {
  if (!body) continue;
  const fs = body.match(NUMERIC_FS) ?? [];
  const vs = body.match(NUMERIC_VS) ?? [];
  check(`T1: ${name} 唔准撒 fontSize 數字`, fs.length === 0, `搵到 ${fs.length} 個（要行 POST_TYPE / WIDE_TYPE / postTitleSize）`);
  check(`T1: ${name} 唔准撒 valueSize 數字`, vs.length === 0, `搵到 ${vs.length} 個（要行 POST_TYPE.stat / WIDE_TYPE.stat）`);
}

/* ─────────────────────────────────────────────────────────────
 * T2 — 同一行嘅 `<Stat>` 一定要同一個字級（守 ①，owner 直接指住嗰個病）
 * ───────────────────────────────────────────────────────────── */
/* wide 由 3 個 Stat（cap/price/pop 一行）改成 2 個（price/pop 佐證行）——
   cap 升咗做 88px hero，唔再係 `<Stat>`。所以最少數目一個 layout 一個。 */
for (const [name, body, expectMin] of [["PostLayout", post, 3], ["WideLayout", wide, 2]]) {
  if (!body) continue;
  const sizes = [...body.matchAll(/<Stat\b[^/>]*?valueSize=\{([^}]+)\}/g)].map((m) => m[1].trim());
  check(`T2: ${name} 有 ${expectMin}+ 個 Stat`, sizes.length >= expectMin, `得 ${sizes.length} 個`);
  check(`T2: ${name} 同一行嘅數同一個字級`, new Set(sizes).size <= 1, `搵到 ${JSON.stringify([...new Set(sizes)])}`);
}

/* ─────────────────────────────────────────────────────────────
 * T3 — `POST_TYPE` 三級要真係三級（唔可以兩級撞埋，撞埋就等於冇分過角色）
 * ───────────────────────────────────────────────────────────── */
const SCALES = Object.fromEntries(
  [...code.matchAll(/const (POST_TYPE|WIDE_TYPE)\s*=\s*\{([^}]+)\}/g)].map((m) => [m[1], m[2]]),
);
for (const scaleName of ["POST_TYPE", "WIDE_TYPE"]) {
  const decl = SCALES[scaleName];
  check(`T3: 搵到 ${scaleName}`, !!decl);
  if (!decl) continue;
  const scale = Object.fromEntries([...decl.matchAll(/(\w+):\s*(\d+)/g)].map((m) => [m[1], Number(m[2])]));
  check(`T3: ${scaleName} 三級齊`, ["micro", "meta", "stat"].every((k) => Number.isFinite(scale[k])), JSON.stringify(scale));
  check(`T3: ${scaleName} micro < meta < stat`, scale.micro < scale.meta && scale.meta < scale.stat, JSON.stringify(scale));
  /* 兩級太近＝肉眼分唔出，就係 owner 講嗰種「大大細細」。要分就分得夠開。 */
  check(`T3: ${scaleName} meta 至少大過 micro 15%`, scale.meta >= scale.micro * 1.15, `${scale.micro} → ${scale.meta}`);
}
if (post) {
  const micro = (post.match(/POST_TYPE\.micro/g) ?? []).length;
  check("T3: micro 覆蓋齊所有細標籤角色", micro >= 4, `得 ${micro} 個（kicker / caption / 頁腳 ×2 最少）`);
}

/* ─────────────────────────────────────────────────────────────
 * T3b — wide 個 hero 要真係捱得住縮圖（守 unfurl CTR）
 *
 * wide 唔係畀人全屏睇嘅：WhatsApp 個 unfurl 縮圖闊 ~330pt，即係 1200px 嘅 **0.275×**。
 * 舊版 stat 36px → 落到 9.9pt，即係張圖入面**一個數都睇唔到**，凈係見到卡圖同一撻灰字。
 * 所以市值升做 hero，而 hero 要有一條底線：0.275× 之後至少 22pt（≈ 手機正文 1.5 倍）。
 * 呢條唔係美學偏好，係「縮到咁細仲讀唔讀到」嘅物理下限，所以要用數守住。
 * ───────────────────────────────────────────────────────────── */
const WHATSAPP_SCALE = 330 / 1200;
const HERO_MIN_PT = 22;
if (SCALES.WIDE_TYPE) {
  const wideScale = Object.fromEntries([...SCALES.WIDE_TYPE.matchAll(/(\w+):\s*(\d+)/g)].map((m) => [m[1], Number(m[2])]));
  check("T3b: WIDE_TYPE 有 hero", Number.isFinite(wideScale.hero), JSON.stringify(wideScale));
  if (Number.isFinite(wideScale.hero)) {
    const pt = wideScale.hero * WHATSAPP_SCALE;
    check("T3b: hero 縮到 WhatsApp 尺寸仲讀到", pt >= HERO_MIN_PT,
      `${wideScale.hero}px × ${WHATSAPP_SCALE.toFixed(3)} = ${pt.toFixed(1)}pt，要 ≥ ${HERO_MIN_PT}pt`);
    check("T3b: hero 明顯大過 stat", wideScale.hero >= wideScale.stat * 2,
      `hero=${wideScale.hero} stat=${wideScale.stat}（唔夠 2× 就分唔出主次）`);
  }
}
if (wide) {
  check("T3b: WideLayout 真係用咗 hero", /WIDE_TYPE\.hero/.test(wide), "搵唔到 WIDE_TYPE.hero");
}

/* ─────────────────────────────────────────────────────────────
 * T4 — 走勢圖下面唔准再有第二行日期（守 ②）
 *
 * `ChartAxis` 係嗰行日期軸。收咗埋 caption 右邊之後佢就係死 code；一旦有人「順手加返」，
 * 3 行卡名嗰個 case 會即刻衝返穿底邊 —— 而且係靜靜噉衝，睇唔到 error。
 * ───────────────────────────────────────────────────────────── */
check("T4: ChartAxis 已刪，唔准加返", !/ChartAxis/.test(code), "route.tsx 仲有 ChartAxis");
for (const [name, body] of [["PostLayout", post], ["WideLayout", wide]]) {
  if (!body) continue;
  const dateRows = (body.match(/chartRange\(/g) ?? []).length;
  check(`T4: ${name} 日期範圍出一次`, dateRows === 1, `出咗 ${dateRows} 次`);
}

/* ─────────────────────────────────────────────────────────────
 * T5 —（opt-in）`--live`：真係打條 route，逐 px 掃 ink bbox（守 ②）
 *
 * 唔用 headless Chromium —— OG 係 server 側渲染，攞返個 PNG 直接掃就係最貼近出街嗰張。
 * 挑卡係自維護嘅：由 seed-snapshot 揀「卡名最長」（行數最壞）＋「市值最大」（stat 行最闊）
 * ＋「卡名最短」（另一極端），唔寫死 id。
 * ───────────────────────────────────────────────────────────── */
if (process.argv.includes("--live")) {
  const base = process.env.OG_BASE_URL ?? "http://localhost:3901";
  const sharp = await import("sharp").then((m) => m.default).catch(() => null);
  if (!sharp) {
    failed.push("T5: --live 要 sharp，但 import 唔到");
  } else {
    const snap = JSON.parse(read("data/public/seed-snapshot.json"));
    const cards = (snap.top100 ?? []).filter((c) => c.id && c.officialName);
    check("T5: seed-snapshot 有卡", cards.length > 0);
    const byLen = [...cards].sort((a, b) => b.officialName.length - a.officialName.length);
    const byCap = [...cards].sort((a, b) => (b.marketCap?.value ?? 0) - (a.marketCap?.value ?? 0));
    const picks = [...new Set([byLen[0]?.id, byCap[0]?.id, byLen[byLen.length - 1]?.id].filter(Boolean))];

    /* 底色喺**同一行、同一個面板**嘅 padding 區取樣 —— 咁 vignette 喺該高度嘅亮度已經
       計咗入去，唔會將漸變本身當成墨。
       ⚠️ `bgX` 一定要同掃描範圍同一個面板：wide 左邊成條係卡圖漸變、右邊係 paper，
       攞左邊做底色去掃右邊，成條右欄都會當咗係墨（right=1199，假紅）。 */
    async function inkBox(url, { bgX = 3, xFrom = 0, xTo = null } = {}) {
      /* dev server 冧咗要報一行紅，唔好掟 stack 出嚟 —— 呢個 test 會由 run_all_tests 收。 */
      const res = await fetch(url).catch((e) => ({ ok: false, status: `連唔到 ${base}（${e?.cause?.code ?? e?.message ?? e}）` }));
      if (!res.ok) return { error: `${res.status}` };
      const art = res.headers.get("x-og-art");
      const bytes = Buffer.from(await res.arrayBuffer());
      const mime = res.headers.get("content-type");
      const { data, info } = await sharp(bytes).ensureAlpha().raw().toBuffer({ resolveWithObject: true });
      const { width: W, height: H, channels: C } = info;
      const at = (x, y) => { const i = (y * W + x) * C; return [data[i], data[i + 1], data[i + 2]]; };
      const hi = xTo === null ? W - 1 : Math.min(xTo, W - 1);
      let top = -1, bottom = -1, left = W, right = -1;
      for (let y = 0; y < H; y++) {
        const [br, bg, bb] = at(bgX, y);
        let first = -1, last = -1, n = 0;
        for (let x = xFrom; x <= hi; x++) {
          const [r, g, b] = at(x, y);
          if (Math.abs(r - br) + Math.abs(g - bg) + Math.abs(b - bb) > 54) { if (first < 0) first = x; last = x; n++; }
        }
        if (n > 2) { if (top < 0) top = y; bottom = y; left = Math.min(left, first); right = Math.max(right, last); }
      }
      return { W, H, art, mime, bytes: bytes.length, top, bottom, left, right };
    }

    const POST_PAD = 56;
    for (const id of picks) {
      const m = await inkBox(`${base}/api/og/card/${encodeURIComponent(id)}?format=post`);
      if (m.error) { failed.push(`T5: ${id} post ${m.error}`); continue; }
      check(`T5: ${id} post 尺寸`, m.W === 1080 && m.H === 1350, `${m.W}×${m.H}`);
      check(`T5: ${id} post 有卡圖`, m.art === "1", `x-og-art=${m.art}`);
      const margins = { 上: m.top, 下: m.H - 1 - m.bottom, 左: m.left, 右: m.W - 1 - m.right };
      /* −2 係抗鋸齒容差：文字邊緣會滲出一格淡墨。 */
      const bust = Object.entries(margins).filter(([, v]) => v < POST_PAD - 2);
      check(`T5: ${id} post 冇衝穿 padding`, bust.length === 0,
        `${bust.map(([k, v]) => `${k}=${v}`).join(" ")}（padding=${POST_PAD}，全部邊距 ${JSON.stringify(margins)}）`);
      /* post 2026-08-20 由 PNG 轉埋 JPEG：呢張圖而家係條 HERMES cron 鏈 download
         完再 upload 去 WhatsApp／X／Threads，而三家收貨之後一律自己再壓做 JPEG ——
         我哋條 PNG 邊（實測 654,912 bytes）一個 pixel 都保唔到落最終讀者度。
         `===` 唔准改做 `.includes`（見下面 wide 嗰條個註）。 */
      check(`T5: ${id} post 出 JPEG`, m.mime === "image/jpeg", `content-type=${m.mime}`);
      check(`T5: ${id} post 細過原本張 PNG`, m.bytes < 400_000,
        `${(m.bytes / 1024).toFixed(0)}KB（原本 PNG 640KB；post 冇 600KB 硬閘，呢條淨係防有人靜靜改返 PNG）`);
    }

    /*
     * square 1080×1080（owner 2026-08-22：IG feed 出正方形 POST）。
     *
     * ⚠️ 三個直度 format 入面**得呢個真係會塞唔落** —— 佢用同一套排版角色（kicker /
     * 卡名 / 卡圖 / 走勢圖 / 統計行 / logo），但高度比 post 少 270px。所以呢度唔係
     * 抄多份 post：`TALL_GEO.square` 收咗幾多，就靠呢條逐 px 掃返出嚟。收得唔夠，
     * flex 會壓縮／溢出，靜靜出街，冇 error 冇 log。
     *
     * padding 由 route 讀返，唔喺呢度另寫一個數 —— route 改咗 48 而 test 仲寫住 48
     * 就係「兩邊各寫一次」嗰種永遠對唔上嘅債。
     *
     * ⚠️ 要連 `artStage:` 一齊夾住先認 —— 淨係寫 `square: { padX:` 會撞埋
     * `TEXT_ONLY_GEO.square`（padX 72），度緊 A 表用住 B 表個數，冇 error。
     */
    const squarePadRaw = code.match(/square: \{ padX: (\d+), padY: (\d+), artStage:/);
    check("T5: 由 route 度到 square padding", !!squarePadRaw, "route.tsx 搵唔到 TALL_GEO.square 個 padX/padY");
    if (squarePadRaw) {
      const SQUARE_PAD_X = Number(squarePadRaw[1]);
      const SQUARE_PAD_Y = Number(squarePadRaw[2]);
      for (const id of picks) {
        const m = await inkBox(`${base}/api/og/card/${encodeURIComponent(id)}?format=square`);
        if (m.error) { failed.push(`T5: ${id} square ${m.error}`); continue; }
        check(`T5: ${id} square 尺寸`, m.W === 1080 && m.H === 1080, `${m.W}×${m.H}`);
        check(`T5: ${id} square 有卡圖`, m.art === "1", `x-og-art=${m.art}`);
        const sm = { 上: m.top, 下: m.H - 1 - m.bottom, 左: m.left, 右: m.W - 1 - m.right };
        /* 上下對 padY、左右對 padX —— 而家兩個都係 48，夾埋一個數就會喺
           將來改單邊嗰日靜靜度錯嗰邊。 */
        const sfloor = { 上: SQUARE_PAD_Y, 下: SQUARE_PAD_Y, 左: SQUARE_PAD_X, 右: SQUARE_PAD_X };
        const sbust = Object.entries(sm).filter(([k, v]) => v < sfloor[k] - 2);
        check(`T5: ${id} square 冇衝穿 padding`, sbust.length === 0,
          `${sbust.map(([k, v]) => `${k}=${v}(要≥${sfloor[k]})`).join(" ")}（padX=${SQUARE_PAD_X} padY=${SQUARE_PAD_Y}，全部邊距 ${JSON.stringify(sm)}）`);
        check(`T5: ${id} square 出 JPEG`, m.mime === "image/jpeg", `content-type=${m.mime}`);
        check(`T5: ${id} square 大細合理`, m.bytes < 400_000, `${(m.bytes / 1024).toFixed(0)}KB`);
      }
    }

    /*
     * status 1080×1920（owner 2026-08-20 加分享目的地選單，WhatsApp Status 揀呢個）。
     *
     * ⚠️ 呢度守嘅**唔係**「有冇衝穿 padding」咁簡單 —— 上下 250 唔係留白，係 IG Story /
     * WhatsApp Status 個平台 UI（頭像／進度條／回覆列）實蓋住嗰兩條。ink 走入去
     * = logo 同 AS OF 俾人哋介面食咗，而張圖照 200 出街、冇 error、冇人會 report。
     * 左右照 56，同 post 一樣。
     */
    const STATUS_PAD_Y = 250;
    for (const id of picks) {
      const m = await inkBox(`${base}/api/og/card/${encodeURIComponent(id)}?format=status`);
      if (m.error) { failed.push(`T5: ${id} status ${m.error}`); continue; }
      check(`T5: ${id} status 尺寸`, m.W === 1080 && m.H === 1920, `${m.W}×${m.H}`);
      check(`T5: ${id} status 有卡圖`, m.art === "1", `x-og-art=${m.art}`);
      const margins = { 上: m.top, 下: m.H - 1 - m.bottom, 左: m.left, 右: m.W - 1 - m.right };
      const bust = Object.entries(margins)
        .filter(([k, v]) => v < (k === "上" || k === "下" ? STATUS_PAD_Y : POST_PAD) - 2);
      check(`T5: ${id} status ink 留喺 story 安全區`, bust.length === 0,
        `${bust.map(([k, v]) => `${k}=${v}`).join(" ")}（上下閘 ${STATUS_PAD_Y}、左右 ${POST_PAD}，全部邊距 ${JSON.stringify(margins)}）`);
      check(`T5: ${id} status 出 JPEG`, m.mime === "image/jpeg", `content-type=${m.mime}`);
      check(`T5: ${id} status 大細合理`, m.bytes < 500_000, `${(m.bytes / 1024).toFixed(0)}KB`);
    }

    /*
     * wide 右欄嘅幾何**由 route.tsx 度返**，唔再喺呢度抄一次。
     * 上一版寫死 `468 + 56`，跟住 route 改咗做 430 板 + 48/44/40 padding，
     * 呢條 test 就變咗喺度守一個唔存在嘅版面 —— 綠燈但守緊空氣。
     */
    const artPanel = Number(code.match(/const ART_PANEL_WIDTH\s*=\s*(\d+)/)?.[1]);
    /* ⚠️ 2026-08-22 真 4K：padding 由 CSS 字串變咗 `${S(n, scale)}px`（每個 px 常數
       都要過 `S()`，唔係咁就喺 4K 度細一半）。呢條 regex 咬嘅係 1× 嗰個常數。 */
    const padRaw = wide?.match(/padding:\s*`\$\{S\((\d+), scale\)\}px \$\{S\((\d+), scale\)\}px \$\{S\((\d+), scale\)\}px \$\{S\((\d+), scale\)\}px`/);
    check("T5: 由 route 度到 wide 幾何", Number.isFinite(artPanel) && !!padRaw,
      `ART_PANEL_WIDTH=${artPanel} padding=${padRaw?.[0] ?? "搵唔到"}`);
    if (Number.isFinite(artPanel) && padRaw) {
      const [padT, padR, padB, padL] = padRaw.slice(1, 5).map(Number);
      const WIDE_COL_L = artPanel + padL;
      const WIDE_COL_R = 1200 - padR;
      /* wide 同 post 一樣要掃齊三個極端 —— 淨掃「市值最大」嗰張會漏咗「卡名最長」，
         而卡名先係整組推穿底邊嗰個變數（2026-08-19 就係噉衝咗 35px）。 */
      for (const id of picks) {
        const w = await inkBox(`${base}/api/og/card/${encodeURIComponent(id)}`, {
          bgX: artPanel + 6, xFrom: WIDE_COL_L - 16,
        });
        if (w.error) { failed.push(`T5: ${id} wide ${w.error}`); continue; }
        check(`T5: ${id} wide 尺寸`, w.W === 1200 && w.H === 630, `${w.W}×${w.H}`);
        check(`T5: ${id} wide 右欄右邊冇出界`, w.right <= WIDE_COL_R + 2, `ink 去到 x=${w.right}，欄右邊 ${WIDE_COL_R}`);
        check(`T5: ${id} wide 右欄左邊冇撞卡圖`, w.left >= WIDE_COL_L - 2, `ink 由 x=${w.left} 起，欄左邊 ${WIDE_COL_L}`);
        check(`T5: ${id} wide 上下冇衝穿 ${padT}/${padB} padding`, w.top >= padT - 2 && 630 - 1 - w.bottom >= padB - 2,
          `上=${w.top} 下=${630 - 1 - w.bottom}`);
        /*
         * WhatsApp 文檔寫明 og:image 上限 600KB，超咗**唔會報錯**，直接唔出圖。
         * 實測舊版 PNG 係 594/623/648KB —— 三張入面兩張已經默默噉爆咗。
         * 所以 wide 轉咗 JPEG；呢條就係嗰個閘，唔准將來有人改返 PNG 又冇人知。
         */
        /* 一定要 `===`：第一版寫 `.includes("jpeg")`，於是 `image/png, image/jpeg`
           （headers spread 撞 key，見 route.tsx `respond()` 個註）照樣綠燈。 */
        check(`T5: ${id} wide 出 JPEG`, w.mime === "image/jpeg", `content-type=${w.mime}`);
        check(`T5: ${id} wide 細過 WhatsApp 600KB 閘`, w.bytes < 550_000,
          `${(w.bytes / 1024).toFixed(0)}KB（閘 550KB，WhatsApp 硬上限 600KB）`);
      }
    }
  }
}

/* ─────────────────────────────────────────────────────────────
 * T6 — 目的地表（`?format=whatsapp` 嗰啲）
 *
 * owner 2026-08-20：條 HERMES cron 鏈做完自動鏈就 download 張圖再 upload 去唔同
 * 平台。條鏈**唔喺呢個 repo**，所以佢寫嘅係目的地名，「邊個平台用邊個尺寸」呢個
 * 決定留喺 `lib/share-destinations.ts`。呢一組守住嗰張表唔會靜靜噉壞：
 *   · 對去一個 route 冇 layout 嘅 format → import 即刻炸（T6e 真係逼佢炸一次）
 *   · 打錯字／新平台未加 → 跌返 wide，唔准 500（條鏈今日冇圖出 好過 攞到橫圖？
 *     唔係 —— 攞到橫圖好過冇圖，所以係 fail-open）
 * ───────────────────────────────────────────────────────────── */
{
  const DEST_REL = "apps/web/src/lib/share-destinations.ts";
  const { readShareFormat, shareDestinations, SHARE_FORMATS, FORMAT_SIZES } =
    await import(pathToFileURL(join(ROOT, DEST_REL)).href);

  /* T6a：每個目的地都要對到一個 route 真係有 layout 嘅 format。
     唔係信 lib 自己個 guard —— 係攞 route.tsx 個 FORMATS 真身嚟對。 */
  /* ⚠️ 2026-08-20 起尺寸真身喺 `FORMAT_SIZES`（route.tsx 讀返佢，自己只留 theme）——
     本來 route 同選單各寫一組數字，就係噉出咗「標 16:9 但實際 1200×630」嗰單。 */
  const routeFormats = Object.keys(FORMAT_SIZES);
  /* 2026-08-23 由「啱啱四個」改成「呢批一定要喺度」：加格式（IG 直向 3:4／橫向
     16:9）唔會假紅，但剷走任何一個舊格式一樣即刻紅。長度改為對 SHARE_FORMATS，
     兩張表仍然唔准分家。 */
  for (const format of ["wide", "square", "post", "status", "portrait", "widescreen"]) {
    check(`T6a: FORMAT_SIZES 有 ${format}`, routeFormats.includes(format), JSON.stringify(routeFormats));
  }
  check("T6a: FORMAT_SIZES 同 SHARE_FORMATS 一樣長",
    routeFormats.length === SHARE_FORMATS.length, JSON.stringify(routeFormats));
  check("T6a: route 讀 FORMAT_SIZES", /import \{[^}]*\bFORMAT_SIZES\b[^}]*\breadShareFormat\b[^}]*\} from "@\/lib\/share-destinations"/.test(code), "route.tsx 冇 import FORMAT_SIZES");
  check("T6a: route 冇再自己開一張尺寸表",
    !/Record<ShareFormat, \{ width: number; height: number/.test(code), "route.tsx 仲有第二組尺寸");
  check("T6a: route 出圖用 FORMAT_SIZES", /const spec = FORMAT_SIZES\[format\];/.test(code));
  for (const dest of shareDestinations()) {
    const format = readShareFormat(dest);
    check(`T6a: 目的地 ${dest} 對到 route 有嘅 format`, routeFormats.includes(format), `${dest} → ${format}`);
  }

  /* T6b：貼圖目的地嘅比例。IG 1:1，其餘 4:5。
     ⚠️⚠️ **`whatsapp` 係呢條入面最貴嗰個**：條 HERMES cron 鏈（唔喺呢個 repo）每日
     download `?format=whatsapp` 再 upload 去 WhatsApp **對話／群組**，owner 2026-08-20
     （e61c1aa9）明講「氣泡保持比例唔裁」= 4:5。2026-08-20 加目的地選單嗰陣一度將佢
     改成 `status`（9:16），條鏈個 URL 一個字都冇變、response 照 200、`x-og-format`
     照出，即係當晚會靜靜 upload 咗一批高瘦圖，冇 error 冇 log 冇人知。全屏 9:16 要
     明寫 `?format=status`，唔准掛喺公司名上面。 */
  for (const dest of ["x", "twitter", "threads", "line", "other", "whatsapp"]) {
    check(`T6b: ${dest} 用 4:5（post）`, readShareFormat(dest) === "post", `${dest} → ${readShareFormat(dest)}`);
  }
  check("T6b: 大細楷都認", readShareFormat("WhatsApp") === "post", readShareFormat("WhatsApp"));
  /* ⚠️ IG 兩個名（同條 cron 鏈可能用嘅 `?format=instagram`）一齊要 1:1 —— owner
     2026-08-22：「IG 原來係正方形出 POST，所以唔係 4:5」。同上面 whatsapp 嗰個
     道理一模一樣：URL 一個字都唔使改就會靜靜出錯比例，所以要喺呢度釘死。 */
  for (const dest of ["instagram", "ig", "IG", "Instagram"]) {
    check(`T6b: ${dest} 用 1:1（square）`, readShareFormat(dest) === "square", `${dest} → ${readShareFormat(dest)}`);
  }
  check("T6b: square 真係 1080×1080", FORMAT_SIZES.square?.width === 1080 && FORMAT_SIZES.square?.height === 1080,
    JSON.stringify(FORMAT_SIZES.square));
  /* 全屏面自己一個 key —— 選單嗰行「限時動態／狀態」行呢個，唔關公司名事 */
  check("T6b: status 用 9:16（全屏面）", readShareFormat("status") === "status", readShareFormat("status"));

  /* T6c：認唔到唔准炸、唔准 500 —— 跌返 wide。 */
  /* ⚠️ `constructor` / `__proto__` 呢啲 `Object.prototype` key **一定要**喺呢個 list：
     直接 index 一個普通 object literal 會攞到繼承嚟嘅嘢（truthy，`??` 接唔到手），route 就會
     攞住個 undefined spec 喺 request 度炸。2026-08-20 上街實測 `?format=constructor` → **HTTP 500**，
     而當時呢個 list 一個 prototype key 都冇，所以 CI 一路綠燈。修法係 `Object.hasOwn`。 */
  const BAD_FORMATS = ["", "  ", "mastodon", "POST_", "9x16", null, undefined,
    "constructor", "__proto__", "CONSTRUCTOR", "prototype", "toString", "valueOf", "hasOwnProperty"];
  for (const bad of BAD_FORMATS) {
    check(`T6c: ${JSON.stringify(bad)} 跌返 wide`, readShareFormat(bad) === "wide", `→ ${readShareFormat(bad)}`);
  }
  /* 同一個洞喺 share-copy 個 `readShareLang`：`?lang=constructor` 上街實測一樣 500。 */
  const { readShareLang } = await import(pathToFileURL(join(ROOT, "apps/web/src/lib/share-copy.ts")).href);
  for (const bad of ["constructor", "__proto__", "CONSTRUCTOR", "toString", "valueOf", "mastodon", "", null, undefined]) {
    check(`T6c: lang ${JSON.stringify(bad)} 跌返 en`, readShareLang(bad) === "en", `→ ${readShareLang(bad)}`);
  }

  /* T6d：張表唔係死 code —— route 真係行佢（有檢查但零 call site 就當冇檢查）。 */
  check("T6d: route 行 readShareFormat", /readShareFormat\(query\.get\("format"\)\)/.test(code),
    "route.tsx 冇用 readShareFormat");
  check("T6d: route 冇再自己開一張 alias 表", !/FORMAT_ALIASES/.test(code), "route.tsx 仲有第二張表");

  /* T6e：個 guard 真係會 fire —— 整一份「對去一個唔存在嘅 format」嘅 copy 落 temp
     再 import 佢。炸唔起 = 呢條防線唔存在。 */
  const dir = mkdtempSync(join(tmpdir(), "cardz-share-dest-guard-"));
  try {
    const mutated = read(DEST_REL).replace('x: "post",', 'x: "reel" as ShareFormat,');
    check("T6e: 改得到嗰行（改咗張表寫法就要順手更新呢個 test）", mutated.includes('"reel"'));
    const probe = join(dir, "share-destinations.probe.ts");
    writeFileSync(probe, mutated, "utf8");
    let message = "";
    try { await import(pathToFileURL(probe).href); } catch (error) { message = String(error?.message ?? error); }
    check("T6e: 對去一個唔存在嘅 format 會即刻炸", message.includes("唔存在嘅 format"), `掟嘅係：${message || "(乜都冇掟)"}`);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }

  /* T6f：三個 format 都要出 JPEG —— 舊 code 有條 `format !== "post"` 閘，拆咗，唔准返嚟。 */
  check("T6f: JPEG 轉換冇再淨係做 wide", !/format\s*!==\s*"post"/.test(code), "route.tsx 仲有 post 唔轉嗰個閘");
  check("T6f: quality 逐個 format 出", /JPEG_QUALITY\[format\]/.test(code), "route.tsx 冇用 JPEG_QUALITY[format]");
  check("T6f: SHARE_FORMATS 同 route 對得住", [...SHARE_FORMATS].sort().join() === [...routeFormats].sort().join(),
    `lib=${[...SHARE_FORMATS].join()} route=${[...routeFormats].join()}`);
}

if (failed.length) {
  console.error(`FAIL ${failed.length}`);
  for (const line of failed) console.error(`  - ${line}`);
  process.exit(1);
}
console.log(`PASS test-fe-og-post-layout${process.argv.includes("--live") ? " (+live)" : " (static)"}`);
