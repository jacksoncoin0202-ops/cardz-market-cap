"use client";

import type { SyntheticEvent } from "react";
import type { SealedProductView } from "@/lib/types";

function srcSet(image: SealedProductView["image"]): string | undefined {
  if (!image.variants) return undefined;
  const entries: string[] = [];
  if (image.variants["200"]) entries.push(`${image.variants["200"]} 200w`);
  if (image.variants["600"]) entries.push(`${image.variants["600"]} 600w`);
  return entries.length ? entries.join(", ") : undefined;
}

function handleError(event: SyntheticEvent<HTMLImageElement>): void {
  const target = event.currentTarget;
  if (target.dataset.cardzFallback === "true") return;
  target.dataset.cardzFallback = "true";
  target.removeAttribute("srcset");
  target.src = "/card-placeholder.svg";
}

export function BoxImage({ image, sizes, alt = "", loading = "lazy", className }: {
  image: SealedProductView["image"];
  sizes: string;
  alt?: string;
  loading?: "eager" | "lazy";
  className?: string;
}) {
  /*
   * `width`/`height` 係 baked intrinsic pixels（`SealedProductView["image"]`），只係攞嚟開盒佔位。
   * **唔知就唔准填**（type 個註釋寫得好清楚）：placeholder 冇尺寸、live-db 冇量到就係 0，
   * 兩種都要 omit —— 借另一件貨嘅比例會令個框一開始就錯，比冇框仲差。
   * `.detail-art` 高度寫死，所以佢唔會撐開周圍嘅版面；郁嘅係張圖自己由 0×0 彈到實際尺寸
   * （flex 置中，向兩邊擴），呢個一樣計 layout shift。
   */
  const dims = image.width && image.height ? { width: image.width, height: image.height } : {};
  return (
    <img
      src={image.url}
      srcSet={srcSet(image)}
      sizes={sizes}
      alt={alt}
      loading={loading}
      decoding="async"
      className={className}
      onError={handleError}
      {...dims}
    />
  );
}
