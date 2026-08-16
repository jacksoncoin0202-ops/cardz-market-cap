import type { Metadata } from "next";
import Link from "next/link";
import { ContentPage, FilledSection, contentFacts, contentMetadata } from "@/components/content-page";
import { canonicalPublicUrl, siteOrganization } from "@/components/structured-data";
import { localeFromSearchParams, type PageSearchParams } from "@/lib/route-metadata";
import { INDEX_PATH, contentPagePaths, fillProse, siteCopy, withLang } from "@/lib/site-copy";

/* 只有一個 as-of 日期跟 snapshot 郁，其餘係固定敘述，所以一個鐘一次已經夠。 */
export const revalidate = 3600;

export async function generateMetadata({ searchParams }: { searchParams: PageSearchParams }): Promise<Metadata> {
  const locale = await localeFromSearchParams(searchParams);
  return await contentMetadata(locale, "about");
}

export default async function AboutPage({ searchParams }: { searchParams: PageSearchParams }) {
  const locale = await localeFromSearchParams(searchParams);
  const t = siteCopy[locale].about;
  const shell = siteCopy[locale].shell;
  const { asOf, vars } = await contentFacts(locale);

  const answer = fillProse(t.answer, vars);
  const url = canonicalPublicUrl(contentPagePaths.about);
  const organisationId = `${canonicalPublicUrl("/")}#organization`;

  const graph: object[] = [
    { ...siteOrganization(), "@id": organisationId, description: answer },
    {
      "@type": "AboutPage",
      "@id": `${url}#webpage`,
      name: t.h1,
      description: answer,
      url,
      inLanguage: locale,
      mainEntity: { "@id": organisationId },
      ...(asOf ? { dateModified: asOf } : {}),
    },
  ];

  const sectionIds = ["publishes", "standards", "cadence", "independence", "cite"];

  return (
    <ContentPage
      locale={locale}
      pageKey="about"
      eyebrow={t.eyebrow}
      h1={t.h1}
      answer={answer}
      asOf={asOf || null}
      toc={t.sections.map((section, index) => ({ id: sectionIds[index] ?? `s${index}`, label: section.heading }))}
      graph={graph}
    >
      <p>{t.descriptor}</p>
      {/* 澄清放喺開頭：搵「CardZ」嘅人有一半係搵緊個加密資產。 */}
      <p className="content-note">{shell.notCrypto}</p>

      {t.sections.map((section, index) => (
        <FilledSection key={section.heading} id={sectionIds[index] ?? `s${index}`} prose={section} vars={vars} />
      ))}

      <section id="more">
        <p>
          <Link href={withLang(contentPagePaths.methodology, locale)}>{shell.nav.methodology}</Link>
          {" · "}
          <Link href={withLang(contentPagePaths.faq, locale)}>{shell.nav.faq}</Link>
          {" · "}
          <Link href={withLang(contentPagePaths.glossary, locale)}>{shell.nav.glossary}</Link>
          {" · "}
          <Link href={withLang(contentPagePaths.data, locale)}>{shell.nav.data}</Link>
          {" · "}
          <Link href={withLang(INDEX_PATH, locale)}>{shell.indexLink}</Link>
        </p>
      </section>
    </ContentPage>
  );
}
