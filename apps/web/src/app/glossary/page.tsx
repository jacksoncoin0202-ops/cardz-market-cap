import type { Metadata } from "next";
import Link from "next/link";
import { ContentPage, contentFacts, contentMetadata } from "@/components/content-page";
import { canonicalPublicUrl } from "@/components/structured-data";
import { localeFromSearchParams, type PageSearchParams } from "@/lib/route-metadata";
import { INDEX_PATH, contentPagePaths, fillProse, siteCopy, withLang } from "@/lib/site-copy";

/* 純定義，唔跟住價格郁，所以一個鐘先重生成一次。 */
export const revalidate = 3600;

export async function generateMetadata({ searchParams }: { searchParams: PageSearchParams }): Promise<Metadata> {
  const locale = await localeFromSearchParams(searchParams);
  return await contentMetadata(locale, "glossary");
}

export default async function GlossaryPage({ searchParams }: { searchParams: PageSearchParams }) {
  const locale = await localeFromSearchParams(searchParams);
  const t = siteCopy[locale].glossary;
  const shell = siteCopy[locale].shell;
  const { asOf, vars } = await contentFacts(locale);

  const answer = fillProse(t.answer, vars);
  const url = canonicalPublicUrl(contentPagePaths.glossary);
  const setId = `${url}#termset`;

  /* 每個 DefinedTerm 嘅 @id 就係頁內錨點，引用得返單一條定義。 */
  const graph: object[] = [
    {
      "@type": "DefinedTermSet",
      "@id": setId,
      name: t.setName,
      description: t.setDescription,
      url,
      inLanguage: locale,
      hasDefinedTerm: t.terms.map((term) => ({
        "@type": "DefinedTerm",
        "@id": `${url}#${term.id}`,
        name: term.term,
        description: term.def,
        inDefinedTermSet: { "@id": setId },
      })),
    },
  ];

  return (
    <ContentPage
      locale={locale}
      pageKey="glossary"
      eyebrow={t.eyebrow}
      h1={t.h1}
      answer={answer}
      asOf={asOf || null}
      graph={graph}
    >
      <section id="terms">
        <h2>{t.setName}</h2>
        <dl className="content-glossary">
          {t.terms.map((term) => (
            <div id={term.id} key={term.id}>
              <dt>{term.term}</dt>
              <dd>{term.def}</dd>
            </div>
          ))}
        </dl>
      </section>

      <section id="more">
        <p className="content-note">{shell.notCrypto}</p>
        <p>
          <Link href={withLang(contentPagePaths.methodology, locale)}>{shell.nav.methodology}</Link>
          {" · "}
          <Link href={withLang(contentPagePaths.faq, locale)}>{shell.nav.faq}</Link>
          {" · "}
          <Link href={withLang(contentPagePaths.data, locale)}>{shell.nav.data}</Link>
          {" · "}
          <Link href={withLang(INDEX_PATH, locale)}>{shell.indexLink}</Link>
        </p>
      </section>
    </ContentPage>
  );
}
