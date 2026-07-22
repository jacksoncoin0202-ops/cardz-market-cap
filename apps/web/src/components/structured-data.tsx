const siteBase = process.env.NEXT_PUBLIC_SITE_URL ?? "https://cardz-beta.jacksoncoin0202.workers.dev";

export function absolutePublicUrl(path: string): string {
  return new URL(path, siteBase).toString();
}

export function StructuredData({ value }: { value: object }) {
  const json = JSON.stringify(value).replace(/</g, "\\u003c");
  return <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: json }} />;
}
