import type { Metadata } from "next";
import Link from "next/link";
import {
  ContentPage,
  ContentTable,
  ProseSection,
  contentFacts,
  contentMetadata,
} from "@/components/content-page";
import { absolutePublicUrl, canonicalPublicUrl, datasetId, siteOrganization } from "@/components/structured-data";
import { copy } from "@/lib/i18n";
import { localeFromSearchParams, type PageSearchParams } from "@/lib/route-metadata";
import { INDEX_PATH, contentPagePaths, fill, fillProse, siteCopy, withLang } from "@/lib/site-copy";

/* API 文檔本身唔跟價格郁，只有 as-of 一行要新，所以一個鐘一次。 */
export const revalidate = 3600;

export async function generateMetadata({ searchParams }: { searchParams: PageSearchParams }): Promise<Metadata> {
  const locale = await localeFromSearchParams(searchParams);
  return await contentMetadata(locale, "data");
}

export default async function DataPage({ searchParams }: { searchParams: PageSearchParams }) {
  const locale = await localeFromSearchParams(searchParams);
  const t = siteCopy[locale].data;
  const shell = siteCopy[locale].shell;
  const { asOf, vars } = await contentFacts(locale);

  const answer = fillProse(t.answer, vars);
  const url = canonicalPublicUrl(contentPagePaths.data);
  const marketEndpoint = absolutePublicUrl("/api/v1/market");
  const searchEndpoint = absolutePublicUrl("/api/v1/search");
  const resolveEndpoint = absolutePublicUrl("/api/v1/resolve");
  const curl = `curl -s "${marketEndpoint}?scope=pokemon&page=1"`;
  /* attribution 要同 /api/v1 同 /llms-full.txt 出一模一樣嘅字串，所以 site origin 要填入去；
     fill() 見到解唔到嘅 placeholder 會回 null 成句唔出，唔補 `site` 個 attribution 就會消失。 */
  const attribution = fill(t.license.attributionTemplate, { ...vars, site: canonicalPublicUrl("/").replace(/\/$/, "") });

  const graph: object[] = [
    {
      "@type": "Dataset",
      /*
       * 同首頁／卡頁指嘅係同一個 dataset，所以用同一個 @id（verify pass 2026-08-16）。
       * 本來自己出 `<url>#dataset`，變咗同一份數據喺 schema 入面有兩個身分；而 `license`
       * 本來指住本頁嘅 `#license` 錨點 —— 錨點唔係授權條款，引擎讀唔出許可。
       * /data 係呢個 dataset 嘅文檔頁，所以用 mainEntityOfPage，唔搶 `url`。
       */
      "@id": datasetId(),
      /* name/description 行 copy[locale].seo.dataset（全站唯一來源），唔喺呢度另寫一份。 */
      name: copy[locale].seo.dataset.name,
      description: copy[locale].seo.dataset.description,
      mainEntityOfPage: url,
      inLanguage: locale,
      license: "https://creativecommons.org/licenses/by/4.0/",
      isAccessibleForFree: true,
      creator: siteOrganization(),
      measurementTechnique: "PSA 10 reference price multiplied by verified PSA 10 population",
      ...(asOf ? { dateModified: asOf, temporalCoverage: asOf } : {}),
      distribution: [
        {
          "@type": "DataDownload",
          encodingFormat: "application/json",
          contentUrl: marketEndpoint,
        },
      ],
    },
    /*
     * 只入 JSON-LD：人睇嘅 /data 版面唔准郁。Agent／爬蟲由呢度同 /llms.txt、/api/v1 學 lookup。
     */
    {
      "@type": "SearchAction",
      "@id": `${absolutePublicUrl("/api/v1/search")}#lookup`,
      target: `${searchEndpoint}?q={query}`,
      "query-input": "required name=query",
      description:
        "Look up a CardZ Marketcap card by name, nickname or collector number in en, zh-TW, zh-CN, ja or ko. Folded substring, not typo-fuzzy. Also GET /api/v1/resolve?q= for a single hit when unique.",
    },
    {
      "@type": "EntryPoint",
      "@id": resolveEndpoint,
      url: resolveEndpoint,
      httpMethod: "GET",
      contentType: "application/json",
      description: "Resolve one card when the top search hit is uniquely strong; otherwise resolved is null.",
    },
  ];

  const toc = [
    { id: "endpoints", label: t.endpoints.heading },
    { id: "params", label: t.params.heading },
    { id: "fields", label: t.fields.heading },
    { id: "example", label: t.example.heading },
    { id: "cadence", label: t.cadence.heading },
    { id: "fair-use", label: t.fairUse.heading },
    { id: "license", label: t.license.heading },
    { id: "files", label: t.files.heading },
  ];

  return (
    <ContentPage
      locale={locale}
      pageKey="data"
      eyebrow={t.eyebrow}
      h1={t.h1}
      answer={answer}
      asOf={asOf || null}
      toc={toc}
      graph={graph}
    >
      <section id="endpoints">
        <h2>{t.endpoints.heading}</h2>
        <p>{t.endpoints.intro}</p>
        <ContentTable table={t.endpoints.table} />
      </section>

      <section id="params">
        <h2>{t.params.heading}</h2>
        <p>{t.params.intro}</p>
        <ContentTable table={t.params.table} />
      </section>

      <section id="fields">
        <h2>{t.fields.heading}</h2>
        <p>{t.fields.intro}</p>
        <ContentTable table={t.fields.table} />
      </section>

      <section id="example">
        <h2>{t.example.heading}</h2>
        <p>{t.example.intro}</p>
        <code className="content-code">{curl}</code>
        <p className="content-note">{t.example.note}</p>
      </section>

      <ProseSection id="cadence" prose={t.cadence} />
      <ProseSection id="fair-use" prose={t.fairUse} />

      <section id="license">
        <h2>{t.license.heading}</h2>
        {t.license.body.map((paragraph) => (
          <p key={paragraph}>{paragraph}</p>
        ))}
        {attribution && (
          <div className="content-callout">
            <p className="content-note">{t.license.attributionLabel}</p>
            <p>{attribution}</p>
          </div>
        )}
      </section>

      <section id="files">
        <h2>{t.files.heading}</h2>
        <p>{t.files.intro}</p>
        <ul>
          {t.files.items.map((item) => (
            <li key={item.href}>
              <a href={item.href}>{item.label}</a>
              {` — ${item.note}`}
            </li>
          ))}
        </ul>
      </section>

      <section id="more">
        <p className="content-note">{shell.notCrypto}</p>
        <p>
          <Link href={withLang(contentPagePaths.methodology, locale)}>{shell.nav.methodology}</Link>
          {" · "}
          <Link href={withLang(contentPagePaths.faq, locale)}>{shell.nav.faq}</Link>
          {" · "}
          <Link href={withLang(contentPagePaths.glossary, locale)}>{shell.nav.glossary}</Link>
          {" · "}
          <Link href={withLang(contentPagePaths.about, locale)}>{shell.nav.about}</Link>
          {" · "}
          <Link href={withLang(INDEX_PATH, locale)}>{shell.indexLink}</Link>
        </p>
      </section>
    </ContentPage>
  );
}
