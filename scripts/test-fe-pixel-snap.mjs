#!/usr/bin/env node
/*
 * heatmap tile 幾何契約（DESIGN.md fix-heatmap-align）—— 純靜態、唔使 dev server，
 * 由 run_all_tests.py glob `scripts/test-*.mjs` 入 npm test。
 *
 * 守嘅係兩件「一錯就成塊板睇落挨左挨右、但 tsc / eslint / 截圖全部照過」嘅事：
 *  ① tile 之間：squarified treemap 出浮點 x/y/w/h，render 時每格自己加 gap/2 → 每條邊各自落喺半粒
 *     device px 上，Chrome 逐個 box 獨立 snap → 名義 3px 嘅 gap 實際 render 2 / 3 / 4px。
 *     `snapTileBox()` 釘**邊界線**（唔係每格），gap 拆做 p（左／上）+ q（右／下），相鄰兩格共用同一條線。
 *  ② frame 四邊：Chrome snap 嘅係**絕對**座標，而 frame 個 origin 係浮點（實測 left 43.1875）。
 *     淨係喺本地座標 round，右／下會多一粒（live 量到 1440 render 左2 上2 右3 下3）。
 *     `snapFrameGrid()` 收 absLeft/absTop，成套嘢喺絕對 device 格度計。
 *
 * 所以下面每個斷言都係用「render 出嚟嘅絕對 device px」做單位：
 * round((absLeft + cssValue) × dpr) —— 即係 Chrome 真正會畫嗰條線，唔係 CSS 浮點值。
 */
import assert from "node:assert/strict";
import { dirname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const { snapCardBox, snapFrameGrid, snapTileBox } = await import(
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

/* 模擬 treemap 一行 n 格：浮點闊度連續 accumulate（同 layoutTreemapRow 一樣） */
function row(widths, height, y = 0) {
  let x = 0;
  return widths.map((w) => {
    const t = { x, y, width: w, height };
    x += w;
    return t;
  });
}

/* live 真實量到嘅 frame 位置／尺寸（1440 / 1920 / 390@3x）+ 幾個刻意刁鑽嘅浮點 origin */
const FRAMES = [
  { absLeft: 43.1875, absTop: 229.296875, w: 1338.625, h: 593.703125 },
  { absLeft: 57.59375, absTop: 236.0625, w: 1789.8125, h: 686.9375 },
  { absLeft: 16, absTop: 250.984375, w: 358, h: 488.015625 },
  { absLeft: 0, absTop: 0, w: 1200, h: 500 },
  { absLeft: 0.5, absTop: 0.5, w: 1000.5, h: 400.5 },
  { absLeft: 100.7, absTop: 33.3, w: 777.77, h: 321.13 },
];

for (const dpr of [1, 1.25, 1.5, 2, 3]) {
  for (const gap of [0, 1, 2, 3, 4, 5]) {
    for (const f of FRAMES) {
      check(`gap ${gap} @ dpr ${dpr} @ frame(${f.absLeft},${f.absTop},${f.w}×${f.h})`, () => {
        const grid = snapFrameGrid(f.w, f.h, dpr, f.absLeft, f.absTop);
        /* 按比例切，最後一格食返餘數 —— 同 treemap 一樣係浮點連續 accumulate，窄 frame 都切得到 */
        const cuts = [0.0922, 0.0658, 0.3003, 0.0226, 0.1536];
        const widths = cuts.map((r) => grid.width * r);
        widths.push(grid.width - widths.reduce((s, v) => s + v, 0));
        assert.ok(widths.every((w) => w > 0), "fixture 切壞咗");
        const tiles = row(widths, grid.height);
        const boxes = tiles.map((t) => snapTileBox(t.x, t.y, t.width, t.height, gap, grid));
        const g = Math.round(gap * dpr);
        const q = g - Math.floor(g / 2);
        /* Chrome 畫邊界嗰條線：絕對 CSS px × dpr 四捨五入 */
        const devX = (v) => Math.round((f.absLeft + v) * dpr);
        const devY = (v) => Math.round((f.absTop + v) * dpr);
        /* frame 自己 render 出嚟嘅內邊（絕對 device px） */
        const frameL = Math.round(f.absLeft * dpr);
        const frameT = Math.round(f.absTop * dpr);
        const frameR = Math.round((f.absLeft + f.w) * dpr);
        const frameB = Math.round((f.absTop + f.h) * dpr);

        for (const b of boxes) {
          assert.ok(b.w >= 0 && b.h >= 0, "negative box");
          /* 每條邊都要 exact 落喺格上：×dpr 之後同 round 完一樣（唔准靠 Chrome 幫手 round） */
          for (const [v, off] of [[b.x, f.absLeft], [b.x + b.w, f.absLeft]]) {
            assert.ok(Math.abs((off + v) * dpr - Math.round((off + v) * dpr)) < 1e-6, `x edge ${v} 唔喺 device 格`);
          }
          for (const v of [b.y, b.y + b.h]) {
            assert.ok(Math.abs((f.absTop + v) * dpr - Math.round((f.absTop + v) * dpr)) < 1e-6, `y edge ${v} 唔喺 device 格`);
          }
        }
        /* ① 每條 inter-tile gap 都係 exactly g 粒 device px */
        for (let i = 1; i < boxes.length; i++) {
          const got = devX(boxes[i].x) - devX(boxes[i - 1].x + boxes[i - 1].w);
          assert.equal(got, g, `gap ${i - 1}→${i} = ${got}, want ${g}`);
        }
        /* ② frame 四邊 margin 全部等於 q（左右、上下都對稱） */
        const first = boxes[0];
        const last = boxes[boxes.length - 1];
        assert.equal(devX(first.x) - frameL, q, "left margin");
        assert.equal(frameR - devX(last.x + last.w), q, "right margin");
        assert.equal(devY(first.y) - frameT, q, "top margin");
        assert.equal(frameB - devY(first.y + first.h), q, "bottom margin");
      });
    }
  }
}

check("snapTileBox degenerate: 細過 gap 嘅格唔會出負數", () => {
  const grid = snapFrameGrid(100, 100, 1, 0, 0);
  const b = snapTileBox(10.2, 10.2, 1.1, 0.4, 3, grid);
  assert.equal(b.w, 0);
  assert.equal(b.h, 0);
});

check("壞 dpr 跌返 1、壞座標當 0", () => {
  assert.equal(snapFrameGrid(100, 50, 0, 10.3, 4.7).dpr, 1);
  assert.equal(snapFrameGrid(100, 50, NaN, 10.3, 4.7).dpr, 1);
  assert.deepEqual(snapFrameGrid(100, 50, 2, NaN, NaN), snapFrameGrid(100, 50, 2, 0, 0));
});

check("snapFrameGrid 冇 absLeft/absTop 就退化返本地格（round 到 device px）", () => {
  assert.deepEqual(snapFrameGrid(1338.625, 593.703125, 1), { width: 1339, height: 594, dpr: 1, originX: 0, originY: 0 });
});

for (const dpr of [1, 1.25, 2]) {
  check(`snapCardBox @ dpr ${dpr}: 卡圖置中偏移係整數 device px`, () => {
    const grid = snapFrameGrid(400, 300, dpr, 43.1875, 229.296875);
    for (const [tw, th, cw, ch] of [
      [101, 57, 43.7, 61.2],
      [12, 9, 7.9, 8.4],
      [300, 180, 111.1, 155.6],
      [5, 7, 5, 7],
    ]) {
      const box = snapTileBox(0, 0, tw + 3, th + 3, 3, grid);
      const c = snapCardBox(box.w, box.h, cw, ch, dpr);
      const offX = ((box.w - c.cardW) / 2) * dpr;
      const offY = ((box.h - c.cardH) / 2) * dpr;
      const nearInt = (v) => Math.abs(v - Math.round(v)) < 1e-6;
      assert.ok(nearInt(offX) && nearInt(offY), `offset ${offX},${offY} not integer`);
      assert.ok(nearInt(c.cardW * dpr) && nearInt(c.cardH * dpr), "card size off device grid");
      assert.ok(c.cardW <= cw + 0.5 / dpr + 1e-9 && c.cardH <= ch + 0.5 / dpr + 1e-9, "card grew past request");
      assert.ok(c.cardW >= 0 && c.cardH >= 0, "negative card");
    }
  });
}

if (failed.length) {
  console.error(`FAIL heatmap tile 幾何契約（${failed.length} 條）:\n` + failed.slice(0, 12).map((item) => ` - ${item}`).join("\n"));
  process.exit(1);
}
console.log(
  "PASS heatmap tile 幾何契約（dpr 1/1.25/1.5/2/3 × gap 0–5 × 6 個真實／刁鑽 frame origin：邊界落絕對 device 格、gap 全等、四邊 margin 全等於 q；snapCardBox 整數偏移）",
);
