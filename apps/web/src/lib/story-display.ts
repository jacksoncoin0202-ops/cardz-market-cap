/*
 * Visible story formatting. Same strip set as plain-text.ts (ATX / bullets / stars),
 * but newlines stay — the detail panel renders paragraphs. Never strip `_`.
 */

const ATX = /^\s{0,3}#{1,6}\s+/gm;
const BULLET = /^\s{0,3}([-*+]|\d{1,9}[.)])\s+/gm;

export function stripVisibleMarkdown(text: string): string {
  return text
    .replace(ATX, "")
    .replace(BULLET, "")
    .replace(/\*\*/g, "")
    .replace(/\*/g, "");
}

export function formatStoryForDisplay(text: string | null | undefined): string | null {
  if (typeof text !== "string") return null;
  const cleaned = stripVisibleMarkdown(text).replace(/[ \t]+\n/g, "\n").trim();
  return cleaned.length > 0 ? cleaned : null;
}

export function storyParagraphs(text: string | null | undefined): string[] {
  const formatted = formatStoryForDisplay(text);
  if (!formatted) return [];
  return formatted.split(/\n\s*\n/).map((part) => part.trim()).filter(Boolean);
}
