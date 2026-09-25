import { getCatalogData } from "@/lib/data/market";
import {
  parsePublicSearchParams,
  publicResolveBody,
  PUBLIC_API_CORS,
  runPublicResolve,
} from "@/lib/public-search";

export const revalidate = 60;

export async function GET(request: Request): Promise<Response> {
  const parsed = parsePublicSearchParams(new URL(request.url).searchParams, { defaultLimit: 5 });
  if ("error" in parsed) {
    return Response.json({ error: parsed.error }, { status: 400, headers: PUBLIC_API_CORS });
  }
  const catalog = await getCatalogData();
  const result = runPublicResolve(catalog.entries, parsed);
  return Response.json(
    publicResolveBody(
      {
        generation: { id: catalog.generation },
        generatedAt: catalog.generatedAt,
        effectiveAt: catalog.effectiveAt,
      },
      parsed,
      result,
    ),
    { headers: PUBLIC_API_CORS },
  );
}

export async function OPTIONS(): Promise<Response> {
  return new Response(null, { status: 204, headers: PUBLIC_API_CORS });
}
