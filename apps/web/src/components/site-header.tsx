import { Header } from "./header";
import { availableCurrencies } from "@/lib/server-snapshot";

/*
 * `<Header>` 係 client component，攞唔到 snapshot。呢層 server wrapper 淨係做一件事：
 * 喺 server 側問「今日 baked snapshot 有邊幾隻貨幣有匯率」，再當 prop 落去。
 * 冇匯率嗰隻唔會入選單，用戶就唔會揀到一個成頁「暫無資料」嘅貨幣。
 */
export async function SiteHeader() {
  const available = await availableCurrencies();
  return <Header availableCurrencies={available} />;
}
