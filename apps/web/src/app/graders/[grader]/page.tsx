import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { GraderPage } from "@/components/grader-page";
import { copy } from "@/lib/i18n";
import { localeFromSearchParams, marketMetadata, type PageSearchParams } from "@/lib/route-metadata";
import { graderSnapshot, loadMarketSnapshot } from "@/lib/server-snapshot";
import { graders, type Grader } from "@/lib/types";

interface GraderRouteProps {
  params: Promise<{ grader: string }>;
  searchParams: PageSearchParams;
}

function parseGrader(value: string): Grader | null {
  const grader = value.toUpperCase() as Grader;
  return graders.includes(grader) ? grader : null;
}

export async function generateMetadata({ params, searchParams }: GraderRouteProps): Promise<Metadata> {
  const [{ grader: raw }, locale] = await Promise.all([params, localeFromSearchParams(searchParams)]);
  const grader = parseGrader(raw);
  if (!grader) return {};
  const t = copy[locale];
  return marketMetadata(locale, t.grader.title.replace("{grader}", grader), t.grader.body, `/graders/${raw.toLowerCase()}`);
}

export default async function GraderRoute({ params }: GraderRouteProps) {
  const grader = parseGrader((await params).grader);
  if (!grader) notFound();
  return <GraderPage grader={grader} snapshot={graderSnapshot(await loadMarketSnapshot(), grader)} />;
}
