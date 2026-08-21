#!/usr/bin/env node
/*
 * favicon 一致性閘。
 *
 * 呢條 test 存在嘅原因（真事）：2026-08-16 換 brand 嗰粒 commit 改咗
 * `icon.png` / `apple-icon.png` / `public/brand/*` 一共 23 個檔，**淨係漏咗
 * `favicon.ico`**。冇 error、冇 warning、build 照過、test 照綠。結果由 08-16
 * 到 08-21，Google SERP 嗰三行 cardzmarketcap.com 一直出緊上一代嘅舊 mark，
 * 直到用戶自己 search 先發現。
 *
 * 手砌 binary 散喺幾個位就一定會再漂，所以：
 *   兩個細 size 全部由 `apps/web/scripts/gen-favicon.mjs` 一個 grid 生出嚟，
 *   下面 ② 逐個 byte assert 佢哋真係同一份。再漏一次就即刻紅。
 *
 * 驗嘅嘢：
 *   ① favicon.ico 係正常 ICO，而且 16 / 32 / 48 三個 size 齊
 *      —— 48 唔可以少，Google 攞 SERP favicon 就係認 `sizes="48x48"` 嗰個。
 *   ② icon.png 同 ico 入面個 32×32 entry 逐 byte 一樣（← 08-16 嗰單就係呢條捉）
 *   ③ 每個 payload 都係真 PNG、RGBA（colortype 6）、IHDR 尺寸同 directory 對得上。
 *      RGBA 唔係品味問題：Turbopack 個 ICO decoder 淨係食 RGBA，塞 palette PNG
 *      入去係 build 直接死（`The PNG is not in RGBA format!`）。
 *   ④ 全張圖淨係得兩隻 brand 色、而且全不透明。有人擺返個 wordmark 或者
 *      第三隻色入嚟就紅。
 *   ⑤ 16×16 真係讀得到嘢：四隻角係底色、上下有留白、有條 >=10px 嘅實心橫棒。
 *      個 wordmark 縮到 16px 係一嚿糊，過唔到呢幾條。
 *
 * 大 size（apple-icon 180、manifest 192/512）**故意**唔喺呢度管 —— 嗰啲留返個
 * wordmark，係分工，唔係漏。
 */
import { inflateSync } from "node:zlib";
import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const APP = join(ROOT, "apps", "web", "src", "app");

const ORANGE = [249, 74, 1];
const WHITE = [253, 253, 253];
const WANT_SIZES = [16, 32, 48];

let failed = 0;
function check(name, ok, detail = "") {
  if (ok) console.log(`  ok   ${name}`);
  else {
    failed++;
    console.log(`  FAIL ${name}${detail ? `  — ${detail}` : ""}`);
  }
}

/* ---- ① ICO container ---- */
const ico = readFileSync(join(APP, "favicon.ico"));
check("favicon.ico 唔係 LFS pointer / 空檔", ico.length > 200, `${ico.length}b`);
check("ICO header type = 1", ico.length >= 6 && ico.readUInt16LE(0) === 0 && ico.readUInt16LE(2) === 1);

const count = ico.readUInt16LE(4);
const entries = [];
for (let i = 0; i < count; i++) {
  const o = 6 + i * 16;
  entries.push({
    size: ico[o] === 0 ? 256 : ico[o],
    height: ico[o + 1] === 0 ? 256 : ico[o + 1],
    len: ico.readUInt32LE(o + 8),
    off: ico.readUInt32LE(o + 12),
  });
}
const sizes = entries.map((e) => e.size);
check("ICO 有齊 16 / 32 / 48（48 = Google SERP 攞嗰個）",
  WANT_SIZES.every((s) => sizes.includes(s)) && sizes.length === WANT_SIZES.length,
  `而家係 ${sizes.join("/") || "(冇 entry)"}`);
check("每個 entry 都係正方形", entries.every((e) => e.size === e.height));
check("每個 payload 都喺檔案範圍入面",
  entries.every((e) => e.off >= 6 + count * 16 && e.off + e.len <= ico.length));

for (const e of entries) e.data = ico.subarray(e.off, e.off + e.len);

/* ---- ② drift gate：icon.png 必須 === ico 嘅 32 entry ---- */
const iconPng = readFileSync(join(APP, "icon.png"));
const e32 = entries.find((e) => e.size === 32);
check("icon.png 同 favicon.ico 嘅 32×32 逐 byte 一樣（改咗一邊漏另一邊就係呢條紅）",
  !!e32 && iconPng.equals(e32.data),
  e32 ? `icon.png ${iconPng.length}b vs ico 32 entry ${e32.data.length}b` : "ICO 冇 32×32 entry");

/* ---- PNG 解碼（RGBA / colortype 6 / 8-bit，即係 gen-favicon 出嗰種） ---- */
function readPng(buf) {
  if (!buf.subarray(0, 4).equals(Buffer.from([0x89, 0x50, 0x4e, 0x47]))) return null;
  const idat = [];
  let ihdr = null;
  let i = 8;
  while (i + 8 <= buf.length) {
    const len = buf.readUInt32BE(i);
    const type = buf.toString("ascii", i + 4, i + 8);
    const data = buf.subarray(i + 8, i + 8 + len);
    if (type === "IHDR") ihdr = data;
    /* IDAT 可以拆開幾嚿，要接返晒一齊先 inflate 得。 */
    if (type === "IDAT") idat.push(data);
    if (type === "IEND") break;
    i += 12 + len;
  }
  if (!ihdr || ihdr.length < 13) return null;
  return {
    width: ihdr.readUInt32BE(0),
    height: ihdr.readUInt32BE(4),
    bitDepth: ihdr[8],
    colorType: ihdr[9],
    interlace: ihdr[12],
    idat: Buffer.concat(idat),
  };
}

/* 解成 RGBA buffer。bpp = 4，所以 filter 嘅 a 係前面第 4 個 byte。 */
function decode(png) {
  if (png.colorType !== 6 || png.bitDepth !== 8 || png.interlace !== 0) return null;
  const bpp = 4;
  const { width: w, height: h } = png;
  const stride = w * bpp;
  const raw = inflateSync(png.idat);
  if (raw.length < h * (stride + 1)) return null;
  const out = Buffer.alloc(h * stride);
  let prev = Buffer.alloc(stride);
  for (let y = 0; y < h; y++) {
    const ft = raw[y * (stride + 1)];
    const line = Buffer.from(raw.subarray(y * (stride + 1) + 1, y * (stride + 1) + 1 + stride));
    for (let x = 0; x < stride; x++) {
      const a = x >= bpp ? line[x - bpp] : 0;
      const b = prev[x];
      const c = x >= bpp ? prev[x - bpp] : 0;
      if (ft === 1) line[x] = (line[x] + a) & 0xff;
      else if (ft === 2) line[x] = (line[x] + b) & 0xff;
      else if (ft === 3) line[x] = (line[x] + ((a + b) >> 1)) & 0xff;
      else if (ft === 4) {
        const p = a + b - c;
        const pa = Math.abs(p - a), pb = Math.abs(p - b), pc = Math.abs(p - c);
        line[x] = (line[x] + (pa <= pb && pa <= pc ? a : pb <= pc ? b : c)) & 0xff;
      }
    }
    line.copy(out, y * stride);
    prev = line;
  }
  return out;
}

/* ---- ③ 每個 payload：真 PNG、RGBA、尺寸對得返 directory ---- */
const decoded = new Map();
for (const e of entries) {
  const png = readPng(e.data);
  check(`${e.size}×${e.size} payload 係 RGBA PNG 而且 IHDR 尺寸對得返 directory`,
    !!png && png.width === e.size && png.height === e.size
    && png.colorType === 6 && png.bitDepth === 8,
    png ? `IHDR ${png.width}×${png.height} colortype=${png.colorType} bd=${png.bitDepth}` : "唔係 PNG");
  if (png) {
    const px = decode(png);
    check(`${e.size}×${e.size} 解得到 pixel`, !!px);
    if (px) decoded.set(e.size, { png, px });
  }
}

/* ---- ④ 全張圖淨係兩隻 brand 色、全不透明 ---- */
const key = (c) => c.join(",");
const ORANGE_K = key(ORANGE), WHITE_K = key(WHITE);
for (const [size, { px }] of decoded) {
  const seen = new Set();
  let translucent = 0;
  for (let i = 0; i < px.length; i += 4) {
    seen.add(`${px[i]},${px[i + 1]},${px[i + 2]}`);
    if (px[i + 3] !== 255) translucent++;
  }
  check(`${size}×${size} 淨係 brand 橙 + 白（唔准偷偷換返個 wordmark）`,
    seen.size === 2 && seen.has(ORANGE_K) && seen.has(WHITE_K),
    `${seen.size} 隻色：${[...seen].slice(0, 5).join(" / ")}`);
  check(`${size}×${size} 全不透明（SERP 底色唔會透過嚟）`, translucent === 0, `${translucent} 粒半透明`);
}

/* ---- ⑤ 16×16 讀唔讀得到 ---- */
const m16 = decoded.get(16);
check("有得驗 16×16", !!m16);
if (m16) {
  const { px } = m16;
  const isBg = (x, y) => {
    const i = (y * 16 + x) * 4;
    return px[i] === ORANGE[0] && px[i + 1] === ORANGE[1] && px[i + 2] === ORANGE[2];
  };
  let inkCount = 0;
  for (let y = 0; y < 16; y++) for (let x = 0; x < 16; x++) if (!isBg(x, y)) inkCount++;

  check("四隻角係底色（個 mark 冇頂爆邊）",
    [[0, 0], [15, 0], [0, 15], [15, 15]].every(([x, y]) => isBg(x, y)));
  check("最上同最下一行全底色（上下有留白）",
    Array.from({ length: 16 }, (_, x) => x).every((x) => isBg(x, 0) && isBg(x, 15)));
  check("墨水覆蓋率喺 20%–45%（太少 = 睇唔到，太多 = 一嚿）",
    inkCount >= 51 && inkCount <= 115, `${inkCount}/256 = ${Math.round(inkCount / 2.56)}%`);
  check("有條 >=10px 嘅實心橫棒（個 wordmark 縮到 16px 過唔到呢條）",
    Array.from({ length: 16 }, (_, y) => y).some((y) => {
      let run = 0, best = 0;
      for (let x = 0; x < 16; x++) { run = isBg(x, y) ? 0 : run + 1; best = Math.max(best, run); }
      return best >= 10;
    }));
}

console.log(failed === 0 ? "\nPASS test-fe-favicon-set" : `\nFAIL test-fe-favicon-set (${failed})`);
process.exit(failed === 0 ? 0 : 1);
