import Link from "next/link";
import type { Metadata } from "next";
import { StructuredData, canonicalPublicUrl } from "@/components/structured-data";
import { displayCardName } from "@/lib/card-name";
import { formatInteger, formatMoney } from "@/lib/format";
import { copy } from "@/lib/i18n";
import { marketMetadata } from "@/lib/route-metadata";
import { loadMarketSnapshot } from "@/lib/server-snapshot";
import {
  INDEX_PATH,
  contentPagePaths,
  fillProse,
  siteCopy,
  withLang,
  type ContentPageKey,
  type QaItem,
  type Prose,
  type TableCopy,
} from "@/lib/site-copy";
import type { Currency, Locale } from "@/lib/types";

/* 內容頁自己 import 返 CSS：唔靠 layout.tsx 記得加。同一個解析路徑 import 兩次會被
   bundler 去重，所以 metadata owner 喺 layout 再 import 一次都唔會出事。 */
import "@/app/styles/content-pages.css";

/*
 * 五版內容頁嘅共用外殼（owner 2026-08-16）。
 * 麵包屑 JSON-LD 喺呢度自己砌，唔 import 其他 owner 嘅 breadcrumbs component ——
 * 平行開發，跨 owner 依賴會互相 block。
 */

type PageCopyExtension = {
  pageTitles?: Partial<Record<ContentPageKey, string>>;
  pageDescriptions?: Partial<Record<ContentPageKey, string>>;
};

/*
 * <title>/description 由 i18n.ts 嘅 pageTitles / pageDescriptions 出（metadata owner 平行加緊）。
 * 未 merge 之前 Copy type 未有呢兩個 key，所以用 optional 結構型讀，讀唔到就退返
 * site-copy 嘅 H1／答案首句 —— 半邊 rollout 都唔會 500，亦唔會出空 title。
 */
export async function contentMetadata(locale: Locale, key: ContentPageKey): Promise<Metadata> {
  const page = copy[locale] as (typeof copy)[Locale] & PageCopyExtension;
  const local = siteCopy[locale][key];
  const title = page.pageTitles?.[key] ?? local.h1;
  /* 退返 site-copy 個答案時要先填數：raw `{asOf}` 走入 meta description 就直接見街。 */
  const { vars } = await contentFacts(locale);
  const description = page.pageDescriptions?.[key] ?? fillProse(local.answer, vars);
  return marketMetadata(locale, title, description, contentPagePaths[key]);
}

export function breadcrumbNode(locale: Locale, key: ContentPageKey) {
  const shell = siteCopy[locale].shell;
  return {
    "@type": "BreadcrumbList",
    itemListElement: [
      { "@type": "ListItem", position: 1, name: shell.home, item: canonicalPublicUrl("/") },
      { "@type": "ListItem", position: 2, name: shell.nav[key], item: canonicalPublicUrl(contentPagePaths[key]) },
    ],
  };
}

/*
 * 內容頁引用嘅每一個數，全部喺呢度由 loadMarketSnapshot() 現算（owner 2026-08-16）：
 * 文案檔一個數都唔准寫死，snapshot 一換，五版嘅數字同 as-of 自動跟住郁。
 * 攞唔到嘅 key 留 null，fill() 會將成句丟走，唔會出半截句或者假零。
 */
export async function contentFacts(locale: Locale): Promise<{
  asOf: string;
  vars: Record<string, string | null>;
}> {
  const snapshot = await loadMarketSnapshot();
  const ranked = [...snapshot.top100, ...snapshot.watchlist];
  const asOf = (snapshot.effectiveAt ?? "").slice(0, 10);
  const currency: Currency = "USD";

  const top = snapshot.top100[0] ?? null;
  const pops = ranked
    .map((card) => card.populationPsa10.value)
    .filter((value): value is number => typeof value === "number" && Number.isFinite(value) && value > 0);
  const caps = ranked
    .map((card) => card.marketCap.value)
    .filter((value): value is number => typeof value === "number" && Number.isFinite(value));
  const totalCap = caps.length > 0 ? caps.reduce((sum, value) => sum + value, 0) : null;

  const money = (value: number | null | undefined) =>
    typeof value === "number" && Number.isFinite(value)
      ? formatMoney(value, currency, snapshot.rates, locale)
      : null;
  const count = (value: number | null | undefined) =>
    typeof value === "number" && Number.isFinite(value) ? formatInteger(value, locale) : null;

  return {
    asOf,
    vars: {
      asOf: asOf || null,
      generation: snapshot.generation || null,
      ranked: count(ranked.length),
      pokemon: count(ranked.filter((card) => card.tcg === "pokemon").length),
      onePiece: count(ranked.filter((card) => card.tcg === "one-piece").length),
      totalCap: money(totalCap),
      minPop: pops.length > 0 ? count(Math.min(...pops)) : null,
      topName: top ? displayCardName(top, locale) || null : null,
      topPrice: top ? money(top.pricePsa10.value) : null,
      topPop: top ? count(top.populationPsa10.value) : null,
      topCap: top ? money(top.marketCap.value) : null,
    },
  };
}

/* 一段一段填：解唔到 placeholder 嗰句丟走，成段變空就連段都唔出。 */
export function fillAll(lines: string[], vars: Record<string, string | null>): string[] {
  return lines.map((line) => fillProse(line, vars)).filter((line) => line.length > 0);
}

export function FilledSection({
  id,
  prose,
  vars,
}: {
  id: string;
  prose: Prose;
  vars: Record<string, string | null>;
}) {
  const body = fillAll(prose.body, vars);
  if (body.length === 0) return null;
  return (
    <section id={id}>
      <h2>{prose.heading}</h2>
      {body.map((paragraph) => (
        <p key={paragraph}>{paragraph}</p>
      ))}
    </section>
  );
}

export function ContentTable({ table }: { table: TableCopy }) {
  return (
    <div className="content-table-wrap">
      <table className="content-table">
        <caption>{table.caption}</caption>
        <thead>
          <tr>
            {table.head.map((cell) => (
              <th key={cell} scope="col">{cell}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {table.rows.map((row) => (
            <tr key={row[0]}>
              {row.map((cell, index) => (
                index === 0
                  ? <th key={cell} scope="row">{cell}</th>
                  : <td key={`${row[0]}-${index}`}>{cell}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ProseSection({ id, prose }: { id: string; prose: Prose }) {
  return (
    <section id={id}>
      <h2>{prose.heading}</h2>
      {prose.body.map((paragraph) => (
        <p key={paragraph}>{paragraph}</p>
      ))}
    </section>
  );
}

/* FAQPage JSON-LD 一定要同呢度 render 出嚟嘅字一模一樣，所以問答只有一個來源。 */
export function QaList({ items }: { items: QaItem[] }) {
  return (
    <div className="content-faq">
      {items.map((item) => (
        <div className="content-faq-item" key={item.q}>
          <h3>{item.q}</h3>
          <p>{item.a}</p>
        </div>
      ))}
    </div>
  );
}

export function faqPageNode(items: QaItem[]) {
  return {
    "@type": "FAQPage",
    mainEntity: items.map((item) => ({
      "@type": "Question",
      name: item.q,
      acceptedAnswer: { "@type": "Answer", text: item.a },
    })),
  };
}

export function ContentPage({
  locale,
  pageKey,
  eyebrow,
  h1,
  answer,
  asOf,
  toc,
  graph,
  children,
}: {
  locale: Locale;
  pageKey: ContentPageKey;
  eyebrow: string;
  h1: string;
  answer: string;
  asOf?: string | null;
  toc?: { id: string; label: string }[];
  graph: object[];
  children: React.ReactNode;
}) {
  const shell = siteCopy[locale].shell;
  const siblings = (Object.keys(contentPagePaths) as ContentPageKey[]).filter((key) => key !== pageKey);

  return (
    <div className="page-shell content-page">
      <StructuredData value={{ "@context": "https://schema.org", "@graph": [breadcrumbNode(locale, pageKey), ...graph] }} />

      {/* label 同 components/breadcrumbs.tsx 嘅預設一致，唔另開一套。 */}
      <nav className="crumbs" aria-label="Breadcrumb">
        <ol>
          <li><Link href={withLang(INDEX_PATH, locale)}>{shell.home}</Link></li>
          <li><span aria-current="page">{shell.nav[pageKey]}</span></li>
        </ol>
      </nav>

      <header className="content-hero">
        <p className="section-kicker">{eyebrow}</p>
        <h1>{h1}</h1>
      </header>

      <p className="content-answer">{answer}</p>

      {/* ISO 日期同 label 要喺同一個 text node（repo 慣例）：分開兩個 node 會被剪貼／抽字時甩開。 */}
      {asOf && (
        <p className="content-asof">
          <time dateTime={asOf}>{`${shell.asOfLabel} ${asOf}`}</time>
        </p>
      )}

      {toc && toc.length > 0 && (
        <nav className="content-toc" aria-label={shell.onThisPage}>
          <h2>{shell.onThisPage}</h2>
          <ol>
            {toc.map((item) => (
              <li key={item.id}><a href={`#${item.id}`}>{item.label}</a></li>
            ))}
          </ol>
        </nav>
      )}

      <article className="content-body">{children}</article>

      <nav className="content-more" aria-label={shell.moreHeading}>
        <h2>{shell.moreHeading}</h2>
        <ul>
          <li><Link href={withLang(INDEX_PATH, locale)}>{shell.indexLink}</Link></li>
          {siblings.map((key) => (
            <li key={key}>
              <Link href={withLang(contentPagePaths[key], locale)}>{shell.nav[key]}</Link>
            </li>
          ))}
        </ul>
      </nav>
    </div>
  );
}
