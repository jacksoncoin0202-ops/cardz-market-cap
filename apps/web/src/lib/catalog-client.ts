import type { CatalogEntry } from "./types";

export interface CatalogPayload {
  generation: string;
  count: number;
  entries: CatalogEntry[];
}

let cached: CatalogPayload | null = null;
let inflight: Promise<CatalogPayload> | null = null;

export async function loadCatalog(): Promise<CatalogPayload> {
  if (cached) return cached;
  inflight ??= fetch("/api/v1/catalog")
    .then((response) => {
      if (!response.ok) throw new Error(`catalog ${response.status}`);
      return response.json() as Promise<CatalogPayload>;
    })
    .then((payload) => {
      cached = payload;
      return payload;
    })
    .finally(() => {
      inflight = null;
    });
  return inflight;
}

export function prefetchCatalog(): void {
  void loadCatalog().catch(() => {
    /* 第一次失敗唔 cache；下次 focus / 打字再試。 */
  });
}
