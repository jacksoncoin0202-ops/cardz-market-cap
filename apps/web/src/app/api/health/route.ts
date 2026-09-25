import {
  FALLBACK_GENERATION,
  FALLBACK_PRESENTATION,
  PRESENTATION,
  PRODUCT_GENERATION,
  PRODUCT_GENERATION_ALIAS,
} from "@/lib/product-generation";
import { boxSidecarHealth, loadMarketSnapshot, scopeSnapshot } from "@/lib/server-snapshot";

export const dynamic = "force-dynamic";

const emptySurfaces = {
  tcgTop100: 0,
  pokemonTop100: 0,
  onePieceTop100: 0,
  /** ranks 101+ only (full membership beyond top 100, excluding rank-0) */
  tcg101Plus: 0,
  /** legacy alias: historical probe expected ranks 101-300 window size, not full watchlist */
  tcg101To300: 0,
  rankedBeyondTop100: 0,
  awaitingFreshPrice: 0,
};

export async function GET(): Promise<Response> {
  const build = process.env.CARDZ_PUBLIC_BUILD_ID?.trim() || "unknown";
  try {
    const snapshot = await loadMarketSnapshot();
    const beyond = snapshot.watchlist;
    const rankedBeyond = beyond.filter((card) => card.marketRank >= 101);
    const awaiting = beyond.filter((card) => !(card.marketRank >= 1));
    // legacy tcg101To300: first 200 of ranks 101+ only (old page-1 window), not full 1222
    const legacy101To300 = rankedBeyond.slice(0, 200).length;
    const surfaces = {
      tcgTop100: scopeSnapshot(snapshot, "all").top100.length,
      pokemonTop100: scopeSnapshot(snapshot, "pokemon").top100.length,
      onePieceTop100: scopeSnapshot(snapshot, "one-piece").top100.length,
      tcg101Plus: rankedBeyond.length + awaiting.length,
      tcg101To300: legacy101To300,
      rankedBeyondTop100: rankedBeyond.length,
      awaitingFreshPrice: awaiting.length,
    };
    return Response.json(
      {
        status: "ok",
        generation: snapshot.generation,
        generatedAt: snapshot.generatedAt,
        cards: snapshot.top100.length + snapshot.watchlist.length,
        surfaces,
        coverage: {
          changeReady: snapshot.coverage?.changeReady ?? null,
          salesReady: snapshot.coverage?.salesReady ?? null,
          completeIdentity: snapshot.coverage?.completeIdentityCount ?? null,
          localizedStories: snapshot.coverage?.localizedStoryCount ?? null,
        },
        build,
        product: PRODUCT_GENERATION,
        alias: PRODUCT_GENERATION_ALIAS,
        presentation: PRESENTATION,
        fallback: { product: FALLBACK_GENERATION, presentation: FALLBACK_PRESENTATION },
        box: (() => {
          const health = boxSidecarHealth();
          return snapshot.sealed
            ? { path: "/box", ...snapshot.sealed.coverage, asOf: snapshot.sealed.asOf, ageHours: health.ageHours, status: health.status }
            : { path: "/box", total: 0, priced: 0, imaged: 0, asOf: null, ageHours: null, status: health.status, error: health.error };
        })(),
        dataMode: process.env.CARDZ_DATA_MODE?.trim() === "live-db" ? "windows-db-3308" : "baked-snapshot",
        databasePort: process.env.CARDZ_DATA_MODE?.trim() === "live-db" ? 3308 : null,
      },
      { headers: { "Cache-Control": "no-store" } },
    );
  } catch {
    return Response.json(
      {
        status: "error",
        generation: null,
        cards: 0,
        surfaces: emptySurfaces,
        build,
      },
      { status: 503, headers: { "Cache-Control": "no-store" } },
    );
  }
}
