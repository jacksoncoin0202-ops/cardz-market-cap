"use client";

import { CapTicker } from "./cap-ticker";
import { formatInteger, formatMoney } from "@/lib/format";
import type { HubStatTick } from "@/lib/seo-routes";

/*
 * FE05 WS3 —— hub 頁 stat 嘅 count-up。
 * <HubShell> 係 server component，傳唔到 function 落 client，所以 seo-routes.ts
 * 帶落嚟嘅係可序列化嘅 HubStatTick，format 喺呢度砌返 —— 而且要同
 * seo-routes.ts 個 `money()` / `formatInteger()` 一模一樣（USD + compact），
 * 唔係 SSR 出嘅字同 hydrate 之後第一個 frame 會唔同。
 * 只落 hub stat block（一頁最多 4 個）；表格行同 ranking row 一律唔掂。
 */
export function HubStatTicker({ tick }: { tick: HubStatTick }) {
  const format = tick.kind === "money"
    ? (n: number) => formatMoney(n, "USD", tick.rates, tick.locale, true)
    /* 中途值係小數，數量類一定要 round 先出街 */
    : (n: number) => formatInteger(Math.round(n), tick.locale);
  return <CapTicker value={tick.value} format={format} />;
}
