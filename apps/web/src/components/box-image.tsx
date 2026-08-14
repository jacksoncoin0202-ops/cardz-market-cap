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
    />
  );
}
