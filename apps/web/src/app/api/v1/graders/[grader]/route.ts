import { getGraderData } from "@/lib/data/market";
import { graders, type Grader } from "@/lib/types";

interface GraderRouteContext {
  params: Promise<{ grader: string }>;
}

export async function GET(_request: Request, context: GraderRouteContext): Promise<Response> {
  const { grader } = await context.params;
  const normalised = grader.toUpperCase();
  if (!graders.includes(normalised as Grader)) {
    return Response.json({ error: `Unknown grader. Use one of: ${graders.join(", ")}` }, { status: 400 });
  }
  const payload = await getGraderData(normalised as Grader);
  return Response.json(payload, { headers: { "Cache-Control": "public, max-age=60" } });
}
