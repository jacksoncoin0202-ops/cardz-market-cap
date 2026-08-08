import { getMarketData, type MarketScope } from "@/lib/data/market";

const scopes: MarketScope[] = ["all", "pokemon", "one-piece", "watchlist"];

function positiveInt(value: string | null): number | undefined {
  if (value === null) return undefined;
  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) && parsed >= 1 ? parsed : undefined;
}

export async function GET(request: Request): Promise<Response> {
  const { searchParams } = new URL(request.url);
  const scope = searchParams.get("scope") ?? "all";
  if (!scopes.includes(scope as MarketScope)) {
    return Response.json({ error: `Unknown scope. Use one of: ${scopes.join(", ")}` }, { status: 400 });
  }
  const payload = await getMarketData(scope as MarketScope, {
    page: positiveInt(searchParams.get("page")),
    pageSize: positiveInt(searchParams.get("pageSize")),
  });
  return Response.json(payload, { headers: { "Cache-Control": "public, max-age=60" } });
}
