import type { Metadata } from "next";
import Link from "next/link";
import {
  ContentPage,
  ContentTable,
  FilledSection,
  QaList,
  contentFacts,
  contentMetadata,
  faqPageNode,
  fillAll,
} from "@/components/content-page";
import { canonicalPublicUrl, siteOrganization } from "@/components/structured-data";
import { localeFromSearchParams, type PageSearchParams } from "@/lib/route-metadata";
import { INDEX_PATH, contentPagePaths, fill, fillProse, siteCopy, withLang } from "@/lib/site-copy";

/* 用到 snapshot 嘅實數（第一名卡、總市值、最低 POP），所以跟榜頁同一個 300 秒 ISR。 */
export const revalidate = 300;

export async function generateMetadata({ searchParams }: { searchParams: PageSearchParams }): Promise<Metadata> {
  const locale = await localeFromSearchParams(searchParams);
  return await contentMetadata(locale, "methodology");
}

export default async function MethodologyPage({ searchParams }: { searchParams: PageSearchParams }) {
  const locale = await localeFromSearchParams(searchParams);
  const t = siteCopy[locale].methodology;
  const shell = siteCopy[locale].shell;
  const { asOf, vars } = await contentFacts(locale);

  const answer = fillProse(t.answer, vars);
  const workedIntro = fill(t.worked.intro, vars);
  const workedRows = t.worked.rows
    .map((row) => {
      const value = fill(row.value, vars);
      return value ? [row.label, value] : null;
    })
    .filter((row): row is string[] => row !== null);
  const showWorked = Boolean(workedIntro) && workedRows.length === t.worked.rows.length;
  const citation = fill(t.cite.template, vars);

  const url = canonicalPublicUrl(contentPagePaths.methodology);
  const graph: object[] = [
    {
      "@type": "TechArticle",
      "@id": `${url}#article`,
      headline: t.h1,
      description: answer,
      inLanguage: locale,
      mainEntityOfPage: url,
      isAccessibleForFree: true,
      ...(asOf ? { dateModified: asOf } : {}),
      publisher: siteOrganization(),
    },
    faqPageNode(t.faq),
  ];

  const toc = [
    { id: "formula", label: t.formula.heading },
    ...(showWorked ? [{ id: "worked", label: t.worked.heading }] : []),
    { id: "sources", label: t.sources.heading },
    { id: "eligibility", label: t.eligibility.heading },
    { id: "graded", label: t.gradedVsRaw.heading },
    { id: "limits", label: t.limits.heading },
    { id: "compare", label: t.compare.heading },
    { id: "versioning", label: t.versioning.heading },
    { id: "cite", label: t.cite.heading },
    { id: "faq", label: t.faqHeading },
  ];

  return (
    <ContentPage
      locale={locale}
      pageKey="methodology"
      eyebrow={t.eyebrow}
      h1={t.h1}
      answer={answer}
      asOf={asOf || null}
      toc={toc}
      graph={graph}
    >
      <p>{t.scope}</p>

      <FilledSection id="formula" prose={t.formula} vars={vars} />

      {showWorked && (
        <section id="worked">
          <h2>{t.worked.heading}</h2>
          <p>{workedIntro}</p>
          <ContentTable
            table={{ caption: t.worked.heading, head: t.worked.head, rows: workedRows }}
          />
          <p className="content-note">{t.worked.note}</p>
        </section>
      )}

      <FilledSection id="sources" prose={t.sources} vars={vars} />
      <FilledSection id="eligibility" prose={t.eligibility} vars={vars} />
      <FilledSection id="graded" prose={t.gradedVsRaw} vars={vars} />

      <section id="limits">
        <h2>{t.limits.heading}</h2>
        <ul>
          {fillAll(t.limits.body, vars).map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      </section>

      <section id="compare">
        <h2>{t.compare.heading}</h2>
        <p>{t.compare.intro}</p>
        <ContentTable table={t.compare.table} />
        <p>{t.compare.note}</p>
        {/* 「唔係加密貨幣」嘅澄清每個重點頁講一次，唔重複刷。 */}
        <p className="content-note">{shell.notCrypto}</p>
      </section>

      <FilledSection id="versioning" prose={t.versioning} vars={vars} />

      <section id="cite">
        <h2>{t.cite.heading}</h2>
        <p>{t.cite.intro}</p>
        {citation && (
          <div className="content-callout">
            <p>{citation}</p>
          </div>
        )}
        <p className="content-note">{t.cite.note}</p>
      </section>

      <section id="faq">
        <h2>{t.faqHeading}</h2>
        <QaList items={t.faq} />
        <p className="content-note">
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
