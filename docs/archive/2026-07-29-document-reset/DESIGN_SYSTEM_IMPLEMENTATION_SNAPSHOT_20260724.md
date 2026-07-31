# Historical front-end implementation snapshot — 2026-07-24

> Archived volatile implementation evidence. Re-read the current source and
> tests before making any implementation or release claim.

Stack: Next.js 16 App Router (Turbopack), React 19, TypeScript, vitest (57 tests), ESLint. Single theme token layer in `src/app/globals.css` (`:root` light + `[data-theme="dark"]` overrides; `--control-radius` 12px / `--section-radius` 24px radius family; 4px reserved for images and table cells).

- `src/components/heatmap.tsx` — treemap hero, tile slider (with text label), period selector, share-image export, hover preview, touch bottom sheet.
- `src/lib/tile-style.ts` — tile layout params baked into `DEFAULT_TILE`; card image shown whenever the tile fits ≥ 8×11 px so the default Top 100 always shows all 100 images.
- `src/components/rankings.tsx`, `grader-page.tsx` — desktop tables and dedicated mobile lists share the same column language; grader tabs and share buttons meet the 44 px target.
- `src/components/heatmap.tsx` mobile H1 unified at 36px/1.1 across home, grader, and detail pages.
- `src/components/canvasui/ParticleReveal.tsx` — installed, not yet wired into any route (requires Chromium experimental HTML-in-Canvas; ships a plain fallback via `supportsHtmlInCanvas()`).
- Known data gap (not a UI issue): TAG grader page shows no rows until the pipeline `pokedexId ↔ seed id` join lands.
