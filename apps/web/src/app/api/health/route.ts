import { loadMarketSnapshot, scopeSnapshot } from "@/lib/server-snapshot";

export const dynamic = "force-dynamic";

const emptySurfaces = {
  tcgTop100: 0,
  pokemonTop100: 0,
  onePieceTop100: 0,
  /** ranks 101+ plus rank-0 awaiting section (post-043 full membership visibility) */
  tcg101Plus: 0,
  /** legacy alias kept so older deploy probes still parse the health JSON */
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
      tcg101Plus: scopeSnapshot(snapshot, "watchlist").top100.length,
      tcg101To300: scopeSnapshot(snapshot, "watchlist").top100.length,
    };
    return Response.json(
      {
        status: "ok",
        generation: snapshot.generation,
        // 每次 bake 都會變嘅值。`build` 靠部署方 set CARDZ_PUBLIC_BUILD_ID，
        // 而實際跑緊嘅部署根本冇 set（永遠回 "local"），所以發佈鏈用佢做
        // 「新 bundle 上到未」嘅判斷永遠等唔到，白等 10 分鐘再 fail。
        // generatedAt 由 snapshot 本身帶出嚟，冇人需要記得 set。
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
