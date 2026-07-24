import type { MarketCardView } from "@/lib/types";

type CardImageImage = MarketCardView["image"];

function srcSet(image: CardImageImage): string | undefined {
  if (!image.variants) return undefined;
  const entries: string[] = [];
  if (image.variants["200"]) entries.push(`${image.variants["200"]} 200w`);
  if (image.variants["600"]) entries.push(`${image.variants["600"]} 600w`);
  return entries.length ? entries.join(", ") : undefined;
}

export function CardImage({ image, sizes, loading = "lazy", alt = "", className }: {
  image: CardImageImage;
  sizes: string;
  loading?: "eager" | "lazy";
  alt?: string;
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
    />
  );
}
