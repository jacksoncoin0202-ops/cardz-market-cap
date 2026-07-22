# Visual audit baseline

Captured from the current production-beta page on 2026-07-22 before the clean-room rebuild.

- Reference: `before-live-1440x900.png`
- Mobile reference: `before-live-390x844.png`
- Style reference: `reference-design-md-1440x900.png`
- URL: `https://cardz-beta.jacksoncoin0202.workers.dev/`
- Viewport: 1440 x 900

## Visible defects to remove

- The summary card consumes first-screen space even though the heatmap is the primary product.
- The heatmap is fixed to 30d and does not expose the shared 1d/7d/30d state.
- Tiles are visually grouped by a squarified layout, so ranks cannot be read continuously left-to-right and top-to-bottom.
- Tiles permanently show collector number, name, market cap and change, making the smallest cells noisy.
- Several tiles use slab/label imagery instead of a full `raw_front` card image.
- The heatmap does not occupy the first viewport and does not create a clear handoff into the Top 100 ranking.
- At 390 px the desktop columns compress into a narrow strip: the brand, navigation, headline, body copy and summary card wrap word-by-word instead of becoming a deliberate mobile composition.

## Acceptance comparison

Capture the staging implementation at 390, 768, 960 and 1440 widths. Compare the 1440 image with this baseline and confirm that the summary card, slab imagery and permanent tile metadata are gone; rank order remains continuous; the selected period changes only tint/metrics, never tile area or rank.

## Adopted visual language

The supplied Figma DESIGN.md reference uses a strict black-and-white editorial frame, 1px hairlines, off-white utility surfaces, large but restrained headings, and occasional soft pastel blocks. CARDZ applies that hierarchy without copying Figma branding: market red/green stays semantic, controls use pill geometry and clear 44px touch targets, and the card art remains the dominant color field.
