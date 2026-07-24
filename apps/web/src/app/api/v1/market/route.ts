import { getMarketData, type MarketScope } from "@/lib/data/market";

const scopes: MarketScope[] = ["all", "pokemon", "one-piece", "watchlist"];

export async function GET(request: Request): Promise<Response> {
  const scope = new URL(request.url).searchParams.get("scope") ?? "all";
  if (!scopes.includes(scope as MarketScope)) {
    return Response.json({ error: `Unknown scope. Use one of: ${scopes.join(", ")}` }, { status: 400 });
  }
  const payload = await getMarketData(scope as MarketScope);
  return Response.json(payload, { headers: { "Cache-Control": "public, max-age=60" } });
}
