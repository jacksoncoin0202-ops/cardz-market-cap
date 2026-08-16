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

export function StructuredData({ value }: { value: object }) {
  const json = JSON.stringify(value).replace(/</g, "\\u003c");
  return <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: json }} />;
}
