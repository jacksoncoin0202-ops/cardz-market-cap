#!/usr/bin/env node
/*
 * Link-preview（unfurl）契約 —— 2026-08-19。
 * 預設純靜態（run_all_tests.py 會 `node <path>` 裸跑）；加 `--live` 先打真 server。
 *
 * 點解要有呢個 test：unfurl 成條鏈**冇一個環節會出 error**。
 *
 *  ① **600KB 靜默閘。** WhatsApp 文檔寫明 og:image 上限 600KB，超咗就唔出圖 ——
 *     唔會 4xx、唔會 log、頁面照 200。2026-08-19 量返出街嗰三張 PNG：594 / 623 / 648KB，
 *     即係當時三張入面**兩張已經冇圖**，而 CI 全綠、owner 亦冇收過任何訊號。
 *     所以真 bytes 要當場磅返，唔可以信「route 有得出圖」。
 *
 *  ② **declared MIME vs 真 bytes。** `og:image:type` 係 metadata 度寫死嘅字串，
 *     route 出咩 bytes 係另一段 code。兩邊各自改都唔會炸 —— Slack／舊 FB scraper
 *     見到對唔上就靜靜噉退做細卡。呢度攞真 header + magic bytes 對返 declared 值。
 *
 *  ③ **爬蟲攞到嘅 head ≠ 瀏覽器攞到嘅 head。** 幾隻 bot 嘅 UA 唔同、唔行 JS、
 *     部分只讀頭幾百 KB。所以要逐隻 UA 打一次，對返 og 區塊 byte-identical，
 *     再驗 `</head>` 喺 300KB 之內。
 */
import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const failed = [];
const check = (label, condition, detail) => { if (!condition) failed.push(detail ? `${label}: ${detail}` : label); };
const read = (rel) => readFileSync(join(ROOT, rel), "utf8");

const META_REL = "apps/web/src/lib/route-metadata.ts";
const PAGE_REL = "apps/web/src/app/card/[id]/page.tsx";
const ROUTE_REL = "apps/web/src/app/api/og/card/[id]/route.tsx";
const meta = read(META_REL);
const page = read(PAGE_REL);
const route = read(ROUTE_REL);

/* ─────────────────────────────────────────────────────────────
 * S1 — declared MIME 要同 route 真係出嗰隻對得住（守 ②，靜態半邊）
 *
 * 靜態只證到「兩邊寫同一個字」；真 bytes 要 --live 先磅得到。兩層都要。
 * ───────────────────────────────────────────────────────────── */
check("S1: marketMetadata 個 type 係參數唔係寫死",
  /type:\s*imageType/.test(meta) && /imageType:\s*"image\/png"\s*\|\s*"image\/jpeg"/.test(meta),
  "route-metadata.ts 仲係寫死 og:image:type");
check("S1: card 頁遞 image/jpeg", /"image\/jpeg",/.test(page), "page.tsx 冇遞 imageType");
check("S1: OG route 真係出 image/jpeg", /respond\(jpeg,\s*"image\/jpeg"\)/.test(route), "route.tsx 搵唔到 JPEG 出口");
/* fail-open 嗰條路出返 PNG，嗰陣 declared 就會錯 —— 所以 x-og-bytes 一定要講真數，
   CI 先至捉到。呢條守住個 header 冇被人「順手」寫成固定值。 */
check("S1: x-og-bytes 由真 buffer 出", /"x-og-bytes",\s*String\(body\.length\)/.test(route), "x-og-bytes 唔係量返 body");

/* ─────────────────────────────────────────────────────────────
 * S2 — 長度上限要有明數，唔可以靠「應該唔會咁長」
 * ───────────────────────────────────────────────────────────── */
const titleMax = Number(page.match(/const TITLE_MAX\s*=\s*(\d+)/)?.[1]);
check("S2: card 頁有 TITLE_MAX 且 ≤ 60", titleMax > 0 && titleMax <= 60, `TITLE_MAX=${titleMax}`);
const descMax = Number(read("apps/web/src/lib/related-cards.ts").match(/const MAX\s*=\s*(\d+)/)?.[1]);
check("S2: cardShareLine 有 160 字上限", descMax === 160, `MAX=${descMax}`);

/* ─────────────────────────────────────────────────────────────
 * S3 — twitter:card 一定要 summary_large_image（細卡唔會出大圖）
 * ───────────────────────────────────────────────────────────── */
check("S3: twitter:card = summary_large_image", /card:\s*"summary_large_image"/.test(meta));

/* ─────────────────────────────────────────────────────────────
 * L — （opt-in）`--live`：逐隻 bot UA 打真 server，再磅真 bytes
 * ───────────────────────────────────────────────────────────── */
if (process.argv.includes("--live")) {
  /* 預設 127.0.0.1 唔用 `localhost`：Windows 度 `localhost` 會先試 ::1，
     dev server 綁 IPv4 嘅話就食足一個 connect timeout 先 fallback。 */
  const base = process.env.OG_BASE_URL ?? "http://127.0.0.1:3901";
  const snap = JSON.parse(read("data/public/seed-snapshot.json"));
  const cards = (snap.top100 ?? []).filter((c) => c.id && c.officialName);
  const byLen = [...cards].sort((a, b) => b.officialName.length - a.officialName.length);
  const byCap = [...cards].sort((a, b) => (b.marketCap?.value ?? 0) - (a.marketCap?.value ?? 0));
  const picks = [...new Set([byLen[0]?.id, byCap[0]?.id].filter(Boolean))];
  check("L: seed-snapshot 有卡", picks.length > 0);

  /* 真 UA 字串。唔用 `curl/8` 之類 —— 部分站（同埋我哋自己嘅 middleware）會按 UA 分流，
     用假 UA 測出嚟嘅嘢證明唔到爬蟲收到咩。 */
  const BOTS = {
    facebook: "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)",
    telegram: "TelegramBot (like TwitterBot)",
    slack: "Slackbot-LinkExpanding 1.0 (+https://api.slack.com/robots)",
    whatsapp: "WhatsApp/2.2338.9 N",
  };
  const OG_TAG = /<meta[^>]+(?:property|name)="(og:[^"]+|twitter:[^"]+)"[^>]*>/g;
  const contentOf = (tag) => tag.match(/content="([^"]*)"/i)?.[1] ?? "";
  const decode = (s) => s
    .replace(/&#x27;/g, "'").replace(/&quot;/g, '"').replace(/&#(\d+);/g, (_, n) => String.fromCharCode(Number(n)))
    .replace(/&amp;/g, "&");

  /* dev server 第一次砌某條 route 可以行好耐，期間新 connection 會 timeout。
     retry 唔係遮醜：呢個 test 守嘅係 og 內容，唔係 dev server 嘅冷啟動速度。 */
  const fetchRetry = async (url, init, tries = 3) => {
    for (let i = 0; i < tries; i += 1) {
      const res = await fetch(url, init).catch((e) => ({ ok: false, err: e?.cause?.code ?? e?.message ?? e }));
      if (res.ok) return res;
      if (i === tries - 1) return { ok: false, status: `連唔到 ${base}（${res.err ?? res.status}）` };
      await new Promise((r) => setTimeout(r, 3000));
    }
    return { ok: false, status: "unreachable" };
  };
  const fetchText = async (url, ua) => {
    const res = await fetchRetry(url, { headers: { "user-agent": ua } });
    if (!res.ok) return { error: `${res.status}` };
    return { html: await res.text() };
  };

  for (const id of picks) {
    const url = `${base}/card/${encodeURIComponent(id)}`;
    /* 每隻 bot 收到嘅 og 區塊要一模一樣 —— 唔同就代表有嘢按 UA 分咗流。 */
    const blocks = {};
    for (const [name, ua] of Object.entries(BOTS)) {
      const r = await fetchText(url, ua);
      if (r.error) { failed.push(`L: ${id} ${name} ${r.error}`); continue; }
      blocks[name] = { html: r.html, tags: r.html.match(OG_TAG) ?? [] };
    }
    const names = Object.keys(blocks);
    if (names.length < 2) continue;
    const ref = blocks[names[0]];
    for (const name of names.slice(1)) {
      check(`L: ${id} ${name} 同 ${names[0]} 個 og 區塊一樣`,
        blocks[name].tags.join("\n") === ref.tags.join("\n"),
        `${blocks[name].tags.length} vs ${ref.tags.length} 個 tag`);
    }

    const pick = (prop) => {
      const hits = ref.tags.filter((t) => new RegExp(`"${prop}"`).test(t));
      return { n: hits.length, value: hits.length ? decode(contentOf(hits[0])) : null };
    };
    const image = pick("og:image");
    const declaredType = pick("og:image:type");
    const title = pick("og:title");
    const desc = pick("og:description");

    check(`L: ${id} 得一粒 og:image`, image.n === 1, `${image.n} 粒`);
    check(`L: ${id} 得一粒 twitter:card`, pick("twitter:card").n === 1);
    /* 爬蟲多數只讀頭幾百 KB；head 推到後面 = og 區塊有機會冇人讀到。 */
    const headEnd = Buffer.byteLength(ref.html.slice(0, ref.html.indexOf("</head>") + 7), "utf8");
    check(`L: ${id} </head> 喺 300KB 之內`, headEnd > 0 && headEnd < 307_200, `${(headEnd / 1024).toFixed(0)}KB`);
    check(`L: ${id} og:title ≤ ${titleMax}`, (title.value ?? "").length <= titleMax, `${(title.value ?? "").length} 字：${title.value}`);
    check(`L: ${id} og:description ≤ 160`, (desc.value ?? "").length <= 160, `${(desc.value ?? "").length} 字`);
    /*
     * 描述頭 40 字要有數字。氣泡入面描述會被裁，裁剩嗰段就係大部分人唯一讀到嗰句 ——
     * 開頭係一串形容詞就等於乜都冇講。呢條逼住排名／市值行最前。
     */
    const head40 = (desc.value ?? "").slice(0, 40);
    check(`L: ${id} 描述頭 40 字有數字`, /\d/.test(head40), JSON.stringify(head40));

    /*
     * og:image / og:image:secure_url 兩粒都要係絕對 https URL。
     * 2026-08-19 捉到：`url` 有 metadataBase 解到絕對，`secureUrl` 就原封不動出咗
     * 一條相對路徑 —— 頁面照 200，冇 error，但 spec 上係錯，HTTPS-only 嘅 scraper
     * 當冇 secure_url 處理。所以呢條要分開驗，唔可以淨驗 `og:image`。
     */
    const secure = pick("og:image:secure_url");
    for (const [tag, value] of [["og:image", image.value], ["og:image:secure_url", secure.value]]) {
      check(`L: ${id} ${tag} 係絕對 https URL`, /^https:\/\//.test(value ?? ""), `${value}`);
    }

    if (!image.value) continue;
    /*
     * 攞 declared URL 嘅 path+query，掛返落**測緊嗰個 base**。
     * 唔可以直接 fetch `image.value`：佢係絕對 production URL，噉樣 --live 對住 dev
     * server 跑就會靜靜噉去磅咗出街嗰張舊圖（第一次跑就係噉：dev 已經出 JPEG 136KB，
     * 但 test 報 PNG 618KB —— 佢根本冇掂過 dev server）。
     */
    const declared = new URL(image.value, base);
    const imageUrl = new URL(`${declared.pathname}${declared.search}`, base).toString();
    const res = await fetchRetry(imageUrl, {});
    if (!res.ok) { failed.push(`L: ${id} og:image 攞唔到（${res.status}）`); continue; }
    const bytes = Buffer.from(await res.arrayBuffer());
    const mime = res.headers.get("content-type");

    check(`L: ${id} og:image 細過 WhatsApp 閘`, bytes.length < 550_000,
      `${(bytes.length / 1024).toFixed(0)}KB（閘 550KB，WhatsApp 硬上限 600KB）`);
    check(`L: ${id} declared type 對得住 header`, declaredType.value === mime,
      `og:image:type=${declaredType.value} vs content-type=${mime}`);
    /*
     * 再對埋 magic bytes：header 同 declared 一齊寫錯咗（copy-paste）嘅話，
     * 上面嗰條會兩邊一致噉綠燈。真 bytes 先係唯一唔識講大話嗰個。
     */
    const magic = bytes[0] === 0xff && bytes[1] === 0xd8 ? "image/jpeg"
      : bytes.slice(0, 8).toString("hex") === "89504e470d0a1a0a" ? "image/png"
        : `unknown(${bytes.slice(0, 4).toString("hex")})`;
    check(`L: ${id} 真 bytes 對得住 declared type`, magic === declaredType.value,
      `magic=${magic} vs declared=${declaredType.value}`);
    check(`L: ${id} og:image 係 wide + 有卡圖`,
      res.headers.get("x-og-format") === "wide" && res.headers.get("x-og-art") === "1",
      `format=${res.headers.get("x-og-format")} art=${res.headers.get("x-og-art")}`);
    check(`L: ${id} x-og-bytes 講真數`, Number(res.headers.get("x-og-bytes")) === bytes.length,
      `header=${res.headers.get("x-og-bytes")} 真=${bytes.length}`);

    /* declared 尺寸錯 = Twitter/Slack 排版預留錯位。攞返真尺寸對。 */
    const sharp = await import("sharp").then((m) => m.default).catch(() => null);
    if (sharp) {
      const { width, height } = await sharp(bytes).metadata();
      check(`L: ${id} og:image:width/height 對得住真圖`,
        String(width) === pick("og:image:width").value && String(height) === pick("og:image:height").value,
        `真 ${width}×${height} vs declared ${pick("og:image:width").value}×${pick("og:image:height").value}`);
    }
  }
}

if (failed.length) {
  console.error(`FAIL ${failed.length}`);
  for (const line of failed) console.error(`  - ${line}`);
  process.exit(1);
}
console.log(`PASS test-fe-og-unfurl${process.argv.includes("--live") ? " (+live)" : " (static)"}`);
