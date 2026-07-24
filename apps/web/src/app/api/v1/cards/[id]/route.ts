import { getCardData } from "@/lib/data/market";

interface CardRouteContext {
  params: Promise<{ id: string }>;
}

export async function GET(_request: Request, context: CardRouteContext): Promise<Response> {
  const { id } = await context.params;
  const card = await getCardData(id);
  if (!card) return Response.json({ error: "Card not found" }, { status: 404 });
  return Response.json(card, { headers: { "Cache-Control": "public, max-age=60" } });
}
