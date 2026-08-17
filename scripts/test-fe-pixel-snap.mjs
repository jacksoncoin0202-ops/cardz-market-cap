#!/usr/bin/env node
/*
 * heatmap tile 幾何契約（DESIGN.md fix-heatmap-align）—— 純靜態、唔使 dev server，
 * 由 run_all_tests.py glob `scripts/test-*.mjs` 入 npm test。
 *
 * 守嘅係一件「一錯就成塊板睇落挨左挨右、但 tsc / eslint / 截圖全部照過」嘅事：
 * squarified treemap 出浮點 x/y/w/h，render 時每格自己加 gap/2 → 每條邊各自落喺半粒
 * device px 上，Chrome 逐個 box 獨立 snap → 名義 3px 嘅 gap 實際 render 2 / 3 / 4px。
 * `snapTileBox()` 將**邊界線**（唔係每格）釘落 device px 格，gap 拆做 p（左／上）+ q（右／下）
 * 兩個整數，相鄰兩格共用同一條線 → 任何 dpr 之下 gap 一律 exactly round(gap × dpr) 粒 device px。
 *
 * 呢度斷言三樣：① 每條 tile 邊都喺 device px 整數格 ② 每個 inter-tile gap 完全相等
 * ③ frame 左右／上下 margin 都等於 q（唔會左邊 1.5px 右邊 1.14px）。
 * 另加 snapCardBox（卡圖置中偏移要係整數 device px）同 snapFrameSize（floor，唔准 round 出界）。
 */
import assert from "node:assert/strict";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { pathToFileURL } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const { snapCardBox, snapFrameSize, snapTileBox } = await import(
  pathToFileURL(resolve(ROOT, "apps/web/src/lib/pixel-snap.ts")).href
);

const failed = [];
const check = (label, fn) => {
  try {
    fn();
  } catch (error) {
    failed.push(`${label}: ${error.message}`);
  }
};
const nearInt = (v, eps = 1e-6) => Math.abs(v - Math.round(v)) < eps;

/* 模擬 treemap 一行 n 格：浮點闊度連續 accumulate（同 layoutTreemapRow 一樣） */
function row(widths, height, y = 0) {
  let x = 0;
  return widths.map((w) => {
    const t = { x, y, width: w, height };
    x += w;
    return t;
  });
}

for (const dpr of [1, 1.25, 1.5, 2, 3]) {
  for (const gap of [0, 1, 2, 3, 4, 5]) {
    check(`snapTileBox gap ${gap} @ dpr ${dpr}`, () => {
      const W = 1338.625;
      const H = 593.703125;
      const size = snapFrameSize(W, H, dpr);
      const used = 123.4567 + 88.1 + 401.99 + 30.3 + 205.55;
      const tiles = row([123.4567, 88.1, 401.99, 30.3, 205.55, size.width - used], size.height);
      const boxes = tiles.map((t) => snapTileBox(t.x, t.y, t.width, t.height, gap, dpr));
      const g = Math.round(gap * dpr);
      const q = g - Math.floor(g / 2);
      for (const b of boxes) {
        for (const v of [b.x, b.y, b.x + b.w, b.y + b.h]) {
          assert.ok(nearInt(v * dpr), `edge ${v} not on device grid`);
        }
        assert.ok(b.w >= 0 && b.h >= 0, "negative box");
      }
      for (let i = 1; i < boxes.length; i++) {
        const gapPx = (boxes[i].x - (boxes[i - 1].x + boxes[i - 1].w)) * dpr;
        assert.ok(Math.abs(gapPx - g) < 1e-6, `gap ${i - 1}→${i} = ${gapPx}, want ${g}`);
      }
      const first = boxes[0];
      const last = boxes[boxes.length - 1];
      assert.ok(Math.abs(first.x * dpr - q) < 1e-6, `left margin ${first.x * dpr} ≠ ${q}`);
      assert.ok(Math.abs((size.width - (last.x + last.w)) * dpr - q) < 1e-6, "right margin ≠ left margin");
      assert.ok(Math.abs(first.y * dpr - q) < 1e-6, `top margin ${first.y * dpr} ≠ ${q}`);
      assert.ok(Math.abs((size.height - (first.y + first.h)) * dpr - q) < 1e-6, "bottom margin ≠ top margin");
    });
  }
}

check("snapTileBox degenerate: 細過 gap 嘅格唔會出負數", () => {
  const b = snapTileBox(10.2, 10.2, 1.1, 0.4, 3, 1);
  assert.equal(b.w, 0);
  assert.equal(b.h, 0);
});

check("snapTileBox 壞 dpr 跌返 1", () => {
  assert.deepEqual(snapTileBox(0, 0, 100, 50, 2, 0), snapTileBox(0, 0, 100, 50, 2, 1));
  assert.deepEqual(snapTileBox(0, 0, 100, 50, 2, NaN), snapTileBox(0, 0, 100, 50, 2, 1));
});

for (const dpr of [1, 1.25, 2]) {
  check(`snapCardBox @ dpr ${dpr}: 卡圖置中偏移係整數 device px`, () => {
    for (const [tw, th, cw, ch] of [
      [101, 57, 43.7, 61.2],
      [12, 9, 7.9, 8.4],
      [300, 180, 111.1, 155.6],
      [5, 7, 5, 7],
    ]) {
      const box = snapTileBox(0, 0, tw + 3, th + 3, 3, dpr);
      const c = snapCardBox(box.w, box.h, cw, ch, dpr);
      const offX = ((box.w - c.cardW) / 2) * dpr;
      const offY = ((box.h - c.cardH) / 2) * dpr;
      assert.ok(nearInt(offX) && nearInt(offY), `offset ${offX},${offY} not integer`);
      assert.ok(nearInt(c.cardW * dpr) && nearInt(c.cardH * dpr), "card size off device grid");
      assert.ok(c.cardW <= cw + 0.5 / dpr + 1e-9 && c.cardH <= ch + 0.5 / dpr + 1e-9, "card grew past request");
      assert.ok(c.cardW >= 0 && c.cardH >= 0, "negative card");
    }
  });
}

check("snapFrameSize floor 落 device px（唔准 round 出界）", () => {
  assert.deepEqual(snapFrameSize(1338.625, 593.703125, 1), { width: 1338, height: 593, dpr: 1 });
  const s = snapFrameSize(1338.625, 593.703125, 1.25);
  assert.ok(nearInt(s.width * 1.25) && s.width <= 1338.625 && s.width > 1338.625 - 0.8, `width ${s.width}`);
});

if (failed.length) {
  console.error("FAIL heatmap tile 幾何契約:\n" + failed.map((item) => ` - ${item}`).join("\n"));
  process.exit(1);
}
console.log(
  "PASS heatmap tile 幾何契約（dpr 1/1.25/1.5/2/3 × gap 0–5：邊界落 device px 格、gap 全等、frame margin 對稱；snapCardBox 整數偏移；snapFrameSize floor）",
);
