/*
 * 2026-08-17（FE05 tooling）：呢個檔一直喺度，但 `eslint` / `eslint-config-next` 從來冇裝，
 * `apps/web/package.json` 亦冇 `lint` script —— 即係「有檢查、零 call site」＝ 冇檢查
 * （AGENTS.md 規矩 9）。而家 devDeps 補返（`eslint@9` + `eslint-config-next@16`，
 * 跟 `next@16` 同一個 major），入口係 `npm run lint --workspace @cardz/web`。
 *
 * flat config 本身**一格都唔使改**：eslint 9 認得 `eslint/config`，而
 * eslint-config-next 16 兩個 subpath 出嘅就係 flat array。
 *
 * ⚠ 首次基線係 **10 error / 9 warning**（全部 pre-existing src 問題，未修）。
 * 即係話 `npm run lint` 而家一定 exit 1 —— 唔准為咗令佢綠而放鬆規則，要修就修 src。
 */
import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTypescript from "eslint-config-next/typescript";

export default defineConfig([
  ...nextVitals,
  ...nextTypescript,
  { rules: { "@next/next/no-img-element": "off" } },
  globalIgnores([".next/**", ".open-next/**", "data/runtime/**", "next-env.d.ts"]),
]);
