import { loadMarketSnapshot, scopeSnapshot } from "@/lib/server-snapshot";

export const dynamic = "force-dynamic";

const emptySurfaces = {
  tcgTop100: 0,
  pokemonTop100: 0,
  onePieceTop100: 0,
  tcg101To300: 0,
};

export async function GET(): Promise<Response> {
  const build = process.env.CARDZ_PUBLIC_BUILD_ID?.trim() || "unknown";
  try {
    const snapshot = await loadMarketSnapshot();
    const surfaces = {
      tcgTop100: scopeSnapshot(snapshot, "all").top100.length,
      pokemonTop100: scopeSnapshot(snapshot, "pokemon").top100.length,
      onePieceTop100: scopeSnapshot(snapshot, "one-piece").top100.length,
      tcg101To300: scopeSnapshot(snapshot, "watchlist").top100.length,
    };
    return Response.json(
      {
        status: "ok",
        generation: snapshot.generation,
        cards: snapshot.top100.length + snapshot.watchlist.length,
        surfaces,
        build,
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
