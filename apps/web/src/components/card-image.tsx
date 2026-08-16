"use client";

import type { SyntheticEvent } from "react";
import type { MarketCardView } from "@/lib/types";

type CardImageImage = MarketCardView["image"];

function srcSet(image: CardImageImage): string | undefined {
  if (!image.variants) return undefined;
  const entries: string[] = [];
  if (image.variants["200"]) entries.push(`${image.variants["200"]} 200w`);
  if (image.variants["600"]) entries.push(`${image.variants["600"]} 600w`);
  return entries.length ? entries.join(", ") : undefined;
}

export function handleCardImageError(event: SyntheticEvent<HTMLImageElement>): void {
  const target = event.currentTarget;
  if (target.dataset.cardzFallback === "true") return;
  target.dataset.cardzFallback = "true";
  target.removeAttribute("srcset");
  target.src = "/card-placeholder.svg";
}

export function CardImage({ image, sizes, loading = "lazy", alt, className }: {
  image: CardImageImage;
  sizes: string;
  loading?: "eager" | "lazy";
  alt: string;
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
      onError={handleCardImageError}
      onLoad={(event) => event.currentTarget.classList.add("tile-img-ready")}
      ref={(el) => {
        // 瀏覽器 cache 命中會直接 complete、onLoad 唔再觸發，mount 時補一刀
        if (el && el.complete && el.naturalWidth > 0) el.classList.add("tile-img-ready");
      }}
    />
  );
}
