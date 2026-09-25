import type { Metadata } from "next";
import Link from "next/link";
import {
  ContentPage,
  QaList,
  contentFacts,
  contentMetadata,
  faqPageNode,
} from "@/components/content-page";
import { canonicalPublicUrl, siteOrganization } from "@/components/structured-data";
import { localeFromSearchParams, type PageSearchParams } from "@/lib/route-metadata";
import { INDEX_PATH, contentPagePaths, fill, fillProse, siteCopy, withLang, type QaItem } from "@/lib/site-copy";

/* 答案入面有 snapshot 實數（總市值、卡數），所以同榜頁一樣 300 秒。 */
export const revalidate = 300;

export async function generateMetadata({ searchParams }: { searchParams: PageSearchParams }): Promise<Metadata> {
  const locale = await localeFromSearchParams(searchParams);
  return await contentMetadata(locale, "faq");
}

export default async function FaqPage({ searchParams }: { searchParams: PageSearchParams }) {
  const locale = await localeFromSearchParams(searchParams);
  const t = siteCopy[locale].faq;
  const shell = siteCopy[locale].shell;
  const { asOf, vars } = await contentFacts(locale);

  /*
   * 一條問答係一個整體：答案填唔到就成條唔出。
   * 出得嘅先入 JSON-LD，所以 FAQPage schema 同畫面上見到嘅字永遠一模一樣。
   */
  const groups = t.groups
    .map((group) => ({
      heading: group.heading,
      items: group.items
        .map((item) => {
          const a = fill(item.a, vars);
          return a ? ({ q: item.q, a } satisfies QaItem) : null;
        })
        .filter((item): item is QaItem => item !== null),
    }))
    .filter((group) => group.items.length > 0);

  const allItems = groups.flatMap((group) => group.items);
  const answer = fillProse(t.answer, vars);
  const url = canonicalPublicUrl(contentPagePaths.faq);

  const graph: object[] = [
    faqPageNode(allItems),
    {
      "@type": "WebPage",
      "@id": `${url}#webpage`,
      name: t.h1,
      description: answer,
      inLanguage: locale,
      isPartOf: siteOrganization(),
      ...(asOf ? { dateModified: asOf } : {}),
    },
  ];

  return (
    <ContentPage
      locale={locale}
      pageKey="faq"
      eyebrow={t.eyebrow}
      h1={t.h1}
      answer={answer}
      asOf={asOf || null}
      toc={groups.map((group, index) => ({ id: `g${index + 1}`, label: group.heading }))}
      graph={graph}
    >
      {groups.map((group, index) => (
        <section id={`g${index + 1}`} key={group.heading}>
          <h2>{group.heading}</h2>
          <QaList items={group.items} />
        </section>
      ))}

      <section id="more">
        <p className="content-note">{shell.notCrypto}</p>
        <p>
          <Link href={withLang(contentPagePaths.methodology, locale)}>{shell.nav.methodology}</Link>
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
