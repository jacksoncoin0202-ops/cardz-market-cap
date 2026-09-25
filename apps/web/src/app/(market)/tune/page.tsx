import type { Metadata } from "next";
import { TuneLab } from "@/components/tune-lab";
import { loadMarketSnapshot, scopeSnapshot } from "@/lib/server-snapshot";

export const revalidate = 300;

export const metadata: Metadata = {
  title: "Heatmap Tuning Lab",
  robots: { index: false, follow: false },
};

export default async function TunePage() {
  const snapshot = scopeSnapshot(await loadMarketSnapshot(), "all");
  return (
    <>
      {/* 內部工具頁本身冇 h1；讀屏 / a11y 審計要一個，視覺上唔出。 */}
      <h1 className="sr-only">Heatmap Tuning Lab</h1>
      <TuneLab cards={snapshot.top100} />
    </>
  );
}
