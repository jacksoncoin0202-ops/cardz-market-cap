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
 * **2026-08-17 fix-lint 之後：0 error / 8 warning**，`npm run lint` exit 0。
 * 一格 rule 都冇放鬆（`no-img-element` 嗰個 off 係 fix-tooling 之前就有）：7 個 error
 * 用 `eslint-disable-next-line` + 一行理由逐個標返（改行為嘅代價大過收益），
 * 3 個喺 `heatmap.tsx` 用 globalIgnores（另一個 session 揸住嗰個檔，見下面）。
 * 要令佢再綠 = 修 src 或者寫明理由，唔准調 rule severity。
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
  globalIgnores([
    ".next/**",
    ".open-next/**",
    "data/runtime/**",
    "next-env.d.ts",
    /*
     * 2026-08-17（fix-lint）：`heatmap.tsx` 出 3 個 `react-hooks/refs`
     * （`mountedEntriesRef.current` / `prevEntries.get()` 喺 render 期間讀）——
     * 嗰 3 個唔係手民之誤，係檔入面 :444–446 明文寫住嘅設計（「記憶只喺 layout
     * effect 寫，render 淨係讀」）。呢個檔喺 FE05 期間**由另一個 session 揸住**
     * （heatmap / heatmap-tile 嘅 owner；DESIGN.md WS2 決定 5 亦有記：「heatmap tile
     * 唔掂——另一 session 揸住」），所以 fix-lint 唔准改佢，亦唔可以喺佢入面加
     * disable 註（一樣係改嗰個檔）。
     *
     * 呢個 ignore 係**欠單，唔係結論**：owner session 交返 `heatmap.tsx` 之後要
     * (1) 由呢個 list 剷走呢行、(2) 決定嗰 3 個 refs error 係修定係喺檔內落
     * disable + 理由。剷走之前 `heatmap.tsx` 完全冇 lint 覆蓋。
     * `heatmap-tile.tsx` **冇** ignore —— 佢今日 0 個 problem，冇理由拆佢個覆蓋。
     */
    "src/components/heatmap.tsx",
  ]),
]);
