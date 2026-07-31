# CARDS Market Cap Design System

> QUARANTINED — non-executable design evidence. Brand spelling, public URL,
> heatmap clamp/layout, and link-vs-dialog behavior conflict with the current
> implementation. Do not use this file as target-state authority; return
> `CONTRACT_GAP` to MAIN for an explicit product decision.

## Design intent

CARDS uses a cold-gallery editorial system: white and near-black structure, soft neutral fields, restrained CARDS orange, and semantic market tints. It should feel like an art index with credible market evidence, not a generic finance dashboard or a card-shop catalog.

The process is audit-first. Before adding decoration, review information priority, content density, image quality, interaction states, responsive behavior, and accessibility. Apply the [TasteSkill](https://www.tasteskill.dev/) method to the editorial shell, heatmap, and motion. Apply dense-product accessibility rules to tables and numeric views.

The reference direction is [Figma DESIGN.md](https://getdesign.md/figma/design-md): clear hierarchy, graphic blocks, disciplined spacing, and intentional editorial composition.

## Visual hierarchy

1. Artwork and market movement establish the page.
2. Rank and core values support scanning.
3. Detailed identity and evidence appear on interaction or in the detail page.
4. Explanatory copy stays short and specific.

Do not expose every available metric at once. Additional information belongs in hover, keyboard focus, mobile bottom sheet, ranking rows, or the detail page.

## Typography

Use the Apple system stack without remote font downloads:

```css
font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display",
  "SF Pro Text", "Helvetica Neue", "PingFang TC", "PingFang SC",
  "Hiragino Sans", "Yu Gothic", Arial, sans-serif;
```

Use tabular numerals for prices, population, market cap, percentages, and rank. Table headers remain on one line. Body copy uses comfortable line length and consistent line height across all four languages.

## Color

- Background: cool white.
- Primary text: near black.
- Secondary text: cool gray with WCAG-compliant contrast.
- Brand accent: CARDS orange (`--accent`), used sparingly for active brand moments. Light mode uses `#b85416` (≥ 4.5:1 on paper/white); dark mode `#e8823f` (≈ 7:1 on near-black).
- Positive market movement: soft green tint.
- Negative market movement: soft red tint.
- Insufficient data: neutral gray tint.

Market red and green are semantic, not decorative brand colors. Never rely on hue alone. Pair movement with a signed percentage, label, or status when details are shown.

The heatmap color scale clamps at -10% and +10% so one extreme card does not flatten the rest of the market.

## Shape and spacing

Use a small, consistent radius family. Avoid excessive nested cards and pill-shaped containers. Group content with spacing, alignment, and typography before adding borders. Borders should be quiet and structural.

The desktop content grid uses generous outer space while the ranking surface may be denser. Mobile removes nonessential columns rather than compressing desktop layout.

## Heatmap

The heatmap is the first-screen hero. A shared `1d / 7d / 30d` control defaults to 30 days and changes only movement color and detail metrics; tile area and rank remain based on current PSA 10 market cap.

Resting tiles show only a small rank in the top-left corner. Do not persistently show collector number, card name, price, market cap, or percentage. The card image is the primary content.

Tile rules:

- Area represents PSA 10 market cap.
- Placement follows a deterministic rank-ordered strip layout: #1 begins at the upper left and consecutive ranks remain readable left-to-right, top-to-bottom.
- Only a semantic-QC-passed `raw_front` is allowed. A crop that still contains slab casing, a label, a barcode, or a certification number is not a raw front.
- Use `object-contain` and preserve the entire card face.
- Apply a soft movement tint while retaining artwork detail.
- Use neutral gray for accumulating, stale without a publishable result, or unavailable selected-window change.
- Every tile remains a real link to its canonical detail route.

Desktop hover and keyboard focus reveal a bounded preview with the larger card image, full localized name, set, complete collector number, PSA 10 price, population, market cap, selected-window change, tracked sales value, data status, and timestamp. The preview must remain inside the viewport.

On touch devices, the first tap opens an accessible bottom sheet with the same information. The detail link is explicit so tapping does not cause accidental navigation.

## Motion

Use a 160 to 180 ms lift, scale, and fade for direct feedback. Keep movement subtle and reversible. Do not animate page layout, large background fields, or every metric simultaneously.

When `prefers-reduced-motion: reduce` is active, remove scaling and movement while preserving state changes through color, outline, and visibility.

## Rankings

Desktop columns:

1. Rank.
2. Card identity and safe image.
3. PSA 10 price.
4. PSA 10 population.
5. Market cap.
6. Selected-window tracked sales value.
7. Selected-window change.

Column headers stay on one line. Full collector number belongs in the row or detail view, not the resting heatmap.

Mobile uses a dedicated ranking list. It may not use horizontal page scrolling. Each item prioritizes rank, artwork, localized name, complete collector number, market cap, and selected-window state. Secondary metrics expand or move to the detail page.

## Detail pages

Separate the page into artwork, identity, market story, and evidence. The image must never push the data chart beyond the viewport. Price charts use a responsive view box and a clipped container.

The story section is titled with the localized equivalent of "Why the market cares". It is unique to the printing and must not use a generic trading template.

## Localization and currency

English with USD is the default. Language, currency, and selected market window remain present across `/`, `/pokemon`, `/one-piece`, `/watchlist`, `/graders/:grader`, and `/card/:id` navigation.

Supported locales: English, Traditional Chinese, Simplified Chinese, Japanese, Korean.

Supported currencies: USD, HKD, CNY, GBP, TWD, JPY, KRW.

Do not use flag emoji as language controls. Use a globe icon with a labeled language menu and a separate labeled currency menu. Controls require visible focus, keyboard operation, and sufficient touch targets.

## Accessibility and quality gates

- Semantic links and buttons, never clickable generic containers.
- Logical heading order and landmarks.
- Visible `:focus-visible` state.
- Keyboard-accessible heatmap tiles and previews.
- Minimum 44 px touch targets for primary controls.
- Color contrast verified in all heatmap states.
- Alternative text names the exact localized printing.
- No horizontal document overflow at 390, 768, 960, or 1440 px.
- No card image cropping caused by `object-cover`.
- No 1-hour UI or data placeholder zero.
- No unbounded tooltip, sheet, chart, or image overflow.
- Donut / segment charts use the grader palette tokens (`--grader-psa` etc.); dark variants are lightened to stay ≥ 4.5:1 on near-black (`#e8874f` for PSA).
- Slider controls carry a visible text label (e.g. 顯示格數 / Tiles), not a bare number.
- Stock-type metrics (population, total graded count) never display negative deltas — they are cumulative by nature.

## Share image (Fujifilm frame)

The heatmap export renders a Fuji-film-style framed PNG via canvas:

- White (light) / near-black (dark) frame with title, date stamp, and `CARDS Market Cap` brand stamp.
- Every visible tile is redrawn with its card image, movement color, and signed percentage.
- Footer carries the methodology line plus a scannable QR code pointing to `https://cardsmarketcap.com`, generated by a self-contained encoder (`src/lib/qr.ts`, byte mode, EC level M, verified matrix-for-matrix against the Nayuki reference and decode-verified with OpenCV in both themes).
- Download filename: `cards-heatmap-top{count}-{date}.png`.

## Branding

User-visible brand is **CARDS Market Cap** (logo, metadata, i18n in all five locales, share-image stamp, filenames). Infrastructure identifiers (`@cardz/market-data`, `CARDZ_ENVIRONMENT`, worker/bucket names) intentionally keep the legacy spelling until the backend rename is scheduled.
