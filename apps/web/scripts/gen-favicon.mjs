#!/usr/bin/env node
/*
 * 生成 `src/app/favicon.ico`（16/32/48）同 `src/app/icon.png`（32）。
 *
 * 點解要有呢個 script、點解唔縮個 wordmark：
 *
 *  ① 2026-08-16 換 brand（7b54ea0c）改晒 `icon.png` / `apple-icon.png` / `public/brand/*`，
 *     **漏咗 favicon.ico**，之後 c283a1b1 revert 身份又只改 text。結果由 08-16 到 08-21
 *     Google SERP 一直出緊上一代嘅橙色舊 mark —— 冇 error、冇 warning、冇人知。
 *     一堆手砌 binary 散喺唔同位就一定會再漂。而家兩個細 size 由呢一個 grid 生出嚟，
 *     `scripts/test-fe-favicon-set.mjs` 再 assert 佢哋真係同一份。
 *
 *  ② favicon 唔可以用個 wordmark 縮。SERP 個 icon 得 16×16 = 256 粒 pixel，
 *     「CARDZ / MARKETCAP」兩行字縮落去係一嚿糊（實測 32px 嘅舊 icon.png 已經讀唔到字）。
 *     細 size 用 mark、大 size（apple-icon 180、manifest 192/512）留 wordmark，
 *     係刻意分工，唔係漏咗對齊。
 *
 *  ③ master 畫喺 16×16 —— 即係最細嗰個目標本身。32 = ×2、48 = ×3，全部整數倍
 *     nearest 放大，所以三個 size 都係 pixel-perfect，冇半透明邊、冇 anti-alias 糊邊。
 *     由大縮細先會出糊邊，所以方向一定要係細畫大。
 *
 * 改完個 mark 要行：`node scripts/gen-favicon.mjs`（喺 apps/web 入面），再 `npm test`。
 */
import { writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import sharp from "sharp";

const WEB = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const APP = join(WEB, "src", "app");

/* brand 真身色，由 public/brand/icon-transparent.png 數出嚟嘅 dominant 色 */
const ORANGE = [249, 74, 1];
const WHITE = [253, 253, 253];

/*
 * 「Z」——CardZ 同 cards 分身份就係呢個字母，亦都係 16px 之下唯一讀得到嘅選擇
 * （兩個字母以上，每個得 6px 闊，一定糊）。
 * 12×12 字身、四邊留 2px；上下橫棒各 3px 厚，中間 4px 闊嘅階梯斜線。
 */
const GRID = [
  "................",
  "................",
  "..############..",
  "..############..",
  "..############..",
  ".........####...",
  "........####....",
  ".......####.....",
  "......####......",
  ".....####.......",
  "....####........",
  "..############..",
  "..############..",
  "..############..",
  "................",
  "................",
];

function png(size) {
  const scale = size / GRID.length;
  if (!Number.isInteger(scale)) throw new Error(`gen-favicon: ${size} 唔係 16 嘅整數倍，放大會出糊邊`);
  const buf = Buffer.alloc(size * size * 4);
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const c = GRID[Math.floor(y / scale)][Math.floor(x / scale)] === "#" ? WHITE : ORANGE;
      const i = (y * size + x) * 4;
      buf[i] = c[0];
      buf[i + 1] = c[1];
      buf[i + 2] = c[2];
      buf[i + 3] = 255;
    }
  }
  /* 一定要 RGBA（colortype 6）。Turbopack 個 ICO decoder 淨係食 RGBA 嘅
     PNG payload，出 palette PNG 會直接 build error：
     `Format error decoding Ico: The PNG is not in RGBA format!`
     `palette: false` 唔可以慳返 —— sharp 見兩隻色會自動轉 palette。 */
  return sharp(buf, { raw: { width: size, height: size, channels: 4 } })
    .png({ compressionLevel: 9, palette: false })
    .toBuffer();
}

/*
 * ICO container。三個 entry 都係 PNG payload（Vista 之後通用，而且係現行
 * favicon.ico 本身用緊嘅寫法）。48 唔可以少 —— Google 攞 SERP favicon 就係睇
 * `<link rel="icon" sizes="48x48">` 嗰個。
 */
function ico(entries) {
  const head = Buffer.alloc(6 + entries.length * 16);
  head.writeUInt16LE(0, 0);
  head.writeUInt16LE(1, 2);
  head.writeUInt16LE(entries.length, 4);
  let offset = head.length;
  entries.forEach(({ size, data }, i) => {
    const o = 6 + i * 16;
    head[o] = size === 256 ? 0 : size;
    head[o + 1] = size === 256 ? 0 : size;
    head[o + 2] = 0;
    head[o + 3] = 0;
    head.writeUInt16LE(1, o + 4);
    head.writeUInt16LE(32, o + 6);
    head.writeUInt32LE(data.length, o + 8);
    head.writeUInt32LE(offset, o + 12);
    offset += data.length;
  });
  return Buffer.concat([head, ...entries.map((e) => e.data)]);
}

const SIZES = [16, 32, 48];
const made = await Promise.all(SIZES.map(async (size) => ({ size, data: await png(size) })));

writeFileSync(join(APP, "favicon.ico"), ico(made));
/* icon.png 同 ico 入面個 32 entry 逐個 byte 一樣 —— test 就係 assert 呢件事 */
writeFileSync(join(APP, "icon.png"), made.find((m) => m.size === 32).data);

console.log(`favicon.ico  ${made.map((m) => `${m.size}:${m.data.length}b`).join("  ")}`);
console.log(`icon.png     32×32 ${made.find((m) => m.size === 32).data.length}b`);
