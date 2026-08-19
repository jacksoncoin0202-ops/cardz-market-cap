#!/usr/bin/env node
import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const failed = [];
const check = (label, condition, detail) => { if (!condition) failed.push(detail ? `${label}: ${detail}` : label); };
const read = (path) => readFileSync(join(ROOT, path), "utf8");

const shareImage = read("apps/web/src/lib/share-image.ts");
const heatmap = read("apps/web/src/components/heatmap.tsx");
const i18n = read("apps/web/src/lib/i18n.ts");

check("SHARE_WA_ASPECT is 9/16", /export const SHARE_WA_ASPECT = 9 \/ 16;/.test(shareImage));
check("ShareAspect includes wa", /export type ShareAspect = "post" \| "wa" \| "frame";/.test(shareImage));
check("shareTargetAspect exists", /export function shareTargetAspect\(/.test(shareImage));
check("renderHeatmapShare uses per-slot minAspect", /const minAspect = shareTargetAspect\(opts\.aspect \?\? "post"\) \?\? SHARE_MIN_ASPECT;/.test(shareImage));
check("padX uses minAspect not only 4:5", /canvasH \* minAspect/.test(shareImage));
check("post export button class", /className="heatmap-export heatmap-export-post"/.test(heatmap));
check("wa export button class", /className="heatmap-export heatmap-export-wa"/.test(heatmap));
check("post button exports post", /onCopy=\{\(\) => exportHeatmap\("post"\)\}/.test(heatmap));
check("wa button exports wa", /onCopy=\{\(\) => exportHeatmap\("wa"\)\}/.test(heatmap));
check("render passes aspect slot", /aspect: slot/.test(heatmap));
check("filename has 9x16", /9x16/.test(heatmap));
const localeText = i18n.slice(i18n.indexOf("export const copy"));
check("all locales shareImagePost", (localeText.match(/shareImagePost:/g) || []).length === 5);
check("all locales shareImageWa", (localeText.match(/shareImageWa:/g) || []).length === 5);

if (failed.length) {
  console.error("FAIL heatmap share slots:\n" + failed.map((item) => ` - ${item}`).join("\n"));
  process.exit(1);
}
console.log("PASS heatmap share slots (post 4:5 + wa 9:16, two buttons, no letterbox lock)");
