import Link from "next/link";
import { canonicalPublicUrl, StructuredData } from "./structured-data";
import "@/app/styles/card-links.css";

export interface Crumb {
  label: string;
  /** 冇 href = 當前頁（最後一層）。JSON-LD 嗰邊照出 name，唔出 item。 */
  href?: string;
}

/*
 * 通用麵包屑（owner 2026-08-16）。
 *
 * BreadcrumbList JSON-LD 特登喺呢度出，唔留返畀每個頁面自己砌：schema 要同睇得見
 * 嘅文字一致，兩邊各寫一次遲早會歪（卡頁舊版個 BreadcrumbList 就係得 2 層、
 * 而且頁面上面根本冇麵包屑）。所以叫方只要畫呢個組件，兩樣一齊有、永遠對得上。
 * ⚠️ 用咗呢個組件嘅頁面，唔准喺自己個 @graph 再放多一個 BreadcrumbList。
 *
 * `item` 一律行 canonicalPublicUrl：`href` 帶住 ?lang/currency/period 係畀人撳嘅，
 * 入 schema 就會令同一個節點出五個 URL。
 */
export function Breadcrumbs({ items, label = "Breadcrumb" }: { items: Crumb[]; label?: string }) {
  const trail = items.filter((item) => item.label.trim().length > 0);
  if (trail.length < 2) return null;
  const structuredData = {
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    itemListElement: trail.map((item, index) => ({
      "@type": "ListItem",
      position: index + 1,
      name: item.label,
      ...(item.href ? { item: canonicalPublicUrl(item.href) } : {}),
    })),
  };
  return (
    <nav className="crumbs" aria-label={label}>
      <StructuredData value={structuredData} />
      <ol>
        {trail.map((item, index) => {
          const last = index === trail.length - 1;
          return (
            <li key={`${item.label}-${index}`}>
              {item.href && !last
                ? <Link href={item.href}>{item.label}</Link>
                : <span aria-current={last ? "page" : undefined}>{item.label}</span>}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
