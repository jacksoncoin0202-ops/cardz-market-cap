import { getCatalogData } from "@/lib/data/market";

export async function GET(): Promise<Response> {
  const payload = await getCatalogData();
  return Response.json(payload, { headers: { "Cache-Control": "public, max-age=60" } });
}