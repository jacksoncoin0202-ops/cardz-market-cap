import { getCatalogData } from "@/lib/data/market";
import {
  parsePublicSearchParams,
  publicSearchBody,
  PUBLIC_API_CORS,
  runPublicSearch,
} from "@/lib/public-search";

export const revalidate = 60;

export async function GET(request: Request): Promise<Response> {
  const parsed = parsePublicSearchParams(new URL(request.url).searchParams);
  if ("error" in parsed) {
    return Response.json({ error: parsed.error }, { status: 400, headers: PUBLIC_API_CORS });
  }
  const catalog = await getCatalogData();
  const page = runPublicSearch(catalog.entries, parsed);
  return Response.json(
    publicSearchBody(
      {
        generation: { id: catalog.generation },
        generatedAt: catalog.generatedAt,
        effectiveAt: catalog.effectiveAt,
      },
      parsed,
      page,
    ),
    { headers: PUBLIC_API_CORS },
  );
}

export async function OPTIONS(): Promise<Response> {
  return new Response(null, { status: 204, headers: PUBLIC_API_CORS });
}
