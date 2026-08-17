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
 *
 * 「裝咗 eslint」本身仲未算有 call site（呢個 repo 冇 CI、冇 husky、冇 git hook）。
 * 真正嘅 call site 係 **`scripts/test-eslint-ratchet.mjs`**：`scripts/run_all_tests.py`
 * 自動 glob `scripts/test-*.mjs`，所以 `npm test` 一跑就會數返 error / warning，
 * 多過基線紅、少過基線都紅（逼你調低基線）。改 rule 或者修 src 之後記住同步嗰個基線。
 *
 * 代價：呢兩個 devDeps 令 lockfile 由 78 個 `packages{}` 變 423 個（全部 `dev:true`）。
 * `apps/web/Dockerfile:17` 係裸 `npm ci`，所以 Docker **deps layer** 大咗；runner stage
 * 只 copy `.next/standalone`，**runtime image 唔受影響**。唔可以加 `--omit=dev` 嚟慳
 * —— `next build` 要 `typescript` 同 `@types/*`，佢哋一樣係 devDeps。個 `@babel/*`
 * subtree 係 `eslint-plugin-react-hooks@6` → `@babel/core` 拉入嚟，唔係 eslint-config-next
 * 自己，所以「改用兩個 plugin」都省唔到（除非放棄 rules-of-hooks，唔值）。
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
