import { createHash } from "node:crypto";

export function canonicalPublicId(parts: readonly string[]): string {
  const canonical = parts.map((part) => part.trim().toLowerCase()).join("\u001f");
  return `cmc_${createHash("sha256").update(canonical).digest("hex").slice(0, 24)}`;
}

export function isOpaquePublicId(value: string): boolean {
  return /^cmc_[0-9a-f]{24}$/.test(value);
}
