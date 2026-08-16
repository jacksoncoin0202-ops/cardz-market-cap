import Link from "next/link";
import { StructuredData } from "./structured-data";
import type { HubStat, HubView, SeoTableColumn, SeoTableRow } from "@/lib/seo-routes";
/* CSS 跟 repo 慣例由用佢嘅 component 自己 import（同 card-links.css 一樣），
   唔使 layout.tsx 幫手；hub 頁唔行呢個殼就唔會落 CSS。 */
import "@/app/styles/hubs.css";

/*
 * SEO hub 頁嘅展示層（server component，冇 client state）。
 *
 * 兩個 export：
 *  - <SeoTable>：純表格，`.table-scroll` 包住 `.seo-table`，行動裝置橫向捲喺表格入面
 *    發生，唔會令成頁左右捲。
 *  - <HubShell>：set hub / rankings / market-report 共用嘅版式（breadcrumb → hero →
 *    quotable answer → stats → 表格 → 相關連結 → JSON-LD）。四條 route 共用一個殼，
 *    唔准逐頁抄一次 —— 抄咗就會有版加咗 disambiguation、有版冇。
 *
 * 所有字（含表頭）由 view model 帶入嚟，呢個檔一句硬編碼文案都冇。
 */

export function SeoTable({ columns, rows, caption }: {
  columns: SeoTableColumn[];
  rows: SeoTableRow[];
  caption: string;
}) {
  if (rows.length === 0) return null;
  return (
    <div className="table-scroll">
      <table className="seo-table">
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column.key} scope="col" className={column.numeric ? "numeric" : undefined}>
                {column.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.key}>
              {row.cells.map((cell, index) => {
                const column = columns[index];
                const classes = [
                  column?.numeric ? "numeric" : "",
                  cell.tone && cell.tone !== "neutral" ? `metric-${cell.tone}` : "",
                ].filter(Boolean).join(" ");
                return (
                  <td key={column?.key ?? String(index)} className={classes || undefined}>
                    {cell.href ? <Link href={cell.href}>{cell.text}</Link> : cell.text}
                    {cell.sub ? <span className="seo-table-sub">{cell.sub}</span> : null}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function HubStats({ stats }: { stats: HubStat[] }) {
  return (
    <dl className="hub-stats">
      {stats.map((stat) => (
        <div className="hub-stat" key={stat.label}>
          <dt>{stat.label}</dt>
          <dd>{stat.value}</dd>
        </div>
      ))}
    </dl>
  );
}

export function HubShell({ view }: { view: HubView }) {
  return (
    <div className="page-shell hub-page">
      <StructuredData value={view.jsonLd} />
      <nav className="hub-breadcrumb" aria-label={view.breadcrumbLabel}>
        <ol>
          {view.crumbs.map((crumb, index) => (
            <li key={`${crumb.label}-${index}`}>
              {crumb.href
                ? <Link href={crumb.href}>{crumb.label}</Link>
                : <span aria-current="page">{crumb.label}</span>}
            </li>
          ))}
        </ol>
      </nav>

      <header className="hero-section hub-hero">
        <p className="section-kicker">{view.kicker}</p>
        <h1>{view.h1}</h1>
        {/* GEO：第一段係自成一句嘅可引用答案（entity + 計法 + 日期），唔准拆散。 */}
        <p className="hero-copy hub-answer">{view.answer}</p>
        {view.notes.map((note) => <p className="hub-note" key={note}>{note}</p>)}
        <p className="hub-note">
          {view.methodNote}{" "}
          <Link className="hub-method-link" href={view.methodHref}>{view.methodLabel}</Link>
        </p>
      </header>

      {view.stats.length > 0 && <HubStats stats={view.stats} />}

      {view.tables.map((block) => (
        <section className="hub-section" id={block.id} key={block.id}>
          {block.heading ? <h2>{block.heading}</h2> : null}
          {block.intro ? <p className="hub-lead">{block.intro}</p> : null}
          <SeoTable columns={block.columns} rows={block.rows} caption={block.caption} />
          {block.note ? <p className="hub-note hub-table-note">{block.note}</p> : null}
        </section>
      ))}

      {view.emptyNote ? <p className="hub-note">{view.emptyNote}</p> : null}

      {view.linkGroups.map((group) => (
        <section className="hub-section hub-links" key={group.heading}>
          <h2>{group.heading}</h2>
          <ul>
            {group.links.map((link) => (
              <li key={link.href}>
                <Link href={link.href}>{link.label}</Link>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}
