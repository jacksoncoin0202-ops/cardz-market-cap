#!/usr/bin/env node
/*
 * 故事段首 emoji 記號喺顯示層剝走（2026-09-23 A7）。live 故事差唔多段段都用 📦 🎨 🗂️ 🔎 📎
 * 開頭，同「克制嘅金融終端」唔夾。剝嘅係 story-panel 顯示層；DB 內容同 story-display.ts
 * （bake input）唔郁。
 * 鎖死：段首 emoji（連 FE0F、膚色、ZWJ 組合、連續幾個）剝走；段中間、數字、#、©®™ 唔准郁；
 * story-panel.tsx 真係行 stripStoryMarker，剝完係空嘅段唔出。
 * run_all_tests.py glob `scripts/test-*.mjs`。
 */
import { readFileSync } from "node:fs";
import { registerHooks } from "node:module";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

registerHooks({
  resolve(specifier, context, next) {
    if (specifier.startsWith(".") && !/\.[a-z]+$/i.test(specifier)) {
      try { return next(`${specifier}.ts`, context); } catch { /* 跌返原本 specifier */ }
    }
    return next(specifier, context);
  },
});

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const { stripStoryMarker } = await import(pathToFileURL(join(ROOT, "apps/web/src/lib/plain-text.ts")).href);

const CASES = [
  ["📦 編號落在 Pokémon SVP EN-SV Black Star Promo", "編號落在 Pokémon SVP EN-SV Black Star Promo"],
  ["🗂️ 來源筆記把發行寫成 2026 年 7 月", "來源筆記把發行寫成 2026 年 7 月"],
  ["🔎📎 連住兩個", "連住兩個"],
  ["👍🏽 skin tone", "skin tone"],
  ["👨‍👩‍👧 family", "family"],
  ["📦", ""],
  ["No marker 📦 inside", "No marker 📦 inside"],
  ["1. numbered", "1. numbered"],
  ["#1 rank", "#1 rank"],
  ["© 2026 Pokémon", "© 2026 Pokémon"],
  ["™ mark", "™ mark"],
];

const failed = [];
for (const [input, want] of CASES) {
  const got = stripStoryMarker(input);
  if (got !== want) failed.push(`stripStoryMarker(${JSON.stringify(input)}) = ${JSON.stringify(got)}, want ${JSON.stringify(want)}`);
}
const panel = readFileSync(join(ROOT, "apps/web/src/components/story-panel.tsx"), "utf8");
if (!panel.includes("storyParagraphs(story).map(stripStoryMarker).filter(Boolean)")) {
  failed.push("story-panel.tsx: paragraphs must go through stripStoryMarker and drop empties");
}

if (failed.length) {
  console.error("FAIL test-fe-story-markers\n" + failed.map((item) => ` - ${item}`).join("\n"));
  process.exit(1);
}
console.log(`PASS test-fe-story-markers (${CASES.length} cases: leading emoji stripped incl. FE0F/skin tone/ZWJ; mid-text, digits, #, ©™ kept; story-panel wired)`);
