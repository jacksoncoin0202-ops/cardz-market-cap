import { PUBLIC_SITE_URL, siteOrganization as baseSiteOrganization } from "@/lib/public-site";

export function absolutePublicUrl(path: string): string {
  return new URL(path, PUBLIC_SITE_URL).toString();
}

/*
 * JSON-LD 入面嘅頁面 URL 一律行呢個：掉晒 ?lang/currency/period 同 #hash，
 * 只留 canonical path。href() 帶住 UI 狀態，畀人撳就啱、畀 schema.org 就唔啱 ——
 * 同一張卡出五個「唔同」URL 會被當五個 entity。圖片 URL 照用 absolutePublicUrl。
 */
export function canonicalPublicUrl(path: string): string {
  const url = new URL(path, PUBLIC_SITE_URL);
  url.search = "";
  url.hash = "";
  return url.toString();
}

/*
 * Organization.url 永遠係 site origin，唔跟頁面／locale 行。
 * 之前 caller 傳 href("/")（可能係 "/?lang=ja"），public-site 個 sameAs 自我過濾
 * 用成串 URL 對比，永遠對唔中；而家先 normalise 做 origin 再入去。
 */
export function siteOrganization(url: string = PUBLIC_SITE_URL) {
  return baseSiteOrganization(new URL(url, PUBLIC_SITE_URL).origin);
}

/*
 * 全站 Dataset / Organization 只准有一個 @id（verify pass 2026-08-16 修）。
 *
 * 之前三個 owner 各寫各嘅：market-page 出一個冇 @id 嘅 Dataset、seo-routes 用
 * `#psa10-index`、而 card-detail / box-detail 嘅 subjectOf 指住 `#dataset-top100`
 * —— 即係成 1,900 版卡頁／BOX 頁嘅 subjectOf 指住一個冇人定義過嘅節點，同時首頁
 * 嗰個 Dataset 冇 @id 認唔返。JSON-LD 靠 @id merge，所以收埋做呢兩個 function，
 * 邊個檔要就 import，唔准再喺 component 入面手砌 hash（AGENTS 規矩 13）。
 */
export function datasetId(): string {
  return `${canonicalPublicUrl("/")}#dataset-top100`;
}

export function organizationId(): string {
  return `${canonicalPublicUrl("/")}#organization`;
}

export function StructuredData({ value }: { value: object }) {
  const json = JSON.stringify(value).replace(/</g, "\\u003c");
  return <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: json }} />;
}
