import { loadMarketSnapshot } from "@/lib/server-snapshot";

export const dynamic = "force-dynamic";

/*
 * Load balancer / container health check。
 * 200 = 進程起到而且真係載到 snapshot；503 = 載唔到數據，唔好收流量。
 */
export async function GET(): Promise<Response> {
  try {
    const snapshot = await loadMarketSnapshot();
    return Response.json(
      {
        status: "ok",
        generation: snapshot.generation,
        effectiveAt: snapshot.effectiveAt,
        mode: snapshot.mode,
        cards: snapshot.top100.length + snapshot.watchlist.length,
      },
      { headers: { "Cache-Control": "no-store" } },
    );
  } catch (error) {
    return Response.json(
      { status: "error", reason: error instanceof Error ? error.message : "snapshot unavailable" },
      { status: 503, headers: { "Cache-Control": "no-store" } },
    );
  }
}
