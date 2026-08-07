import type { Metadata } from "next";
import { TuneLab } from "@/components/tune-lab";
import { loadMarketSnapshot, scopeSnapshot } from "@/lib/server-snapshot";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Heatmap Tuning Lab",
  robots: { index: false, follow: false },
};

export default async function TunePage() {
  const snapshot = scopeSnapshot(await loadMarketSnapshot(), "all");
  return <TuneLab cards={snapshot.top100} />;
}
