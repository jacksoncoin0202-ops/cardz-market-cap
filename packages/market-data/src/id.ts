import { createHash } from "node:crypto";

export function canonicalPublicId(parts: readonly string[]): string {
  const canonical = parts.map((part) => part.trim().toLowerCase()).join("\u001f");
  return `cmc_${createHash("sha256").update(canonical).digest("hex").slice(0, 24)}`;
}

export function isOpaquePublicId(value: string): boolean {
  // Three locked legacy cards already shipped with the original 80-bit form.
  // Keep both immutable public-id generations valid; never rewrite a live card id.
  return /^cmc_(?:[0-9a-f]{20}|[0-9a-f]{24})$/.test(value);
}
