/* QR 編碼器 contract test：對照 Nayuki reference JS implementation（CDN fetch，offline 即 skip）*/
import { describe, expect, it } from "vitest";
import { qrMatrix } from "./qr";

async function loadNayuki(): Promise<{ toMatrix: (text: string, ecl: unknown) => boolean[][]; Ecc: { MEDIUM: unknown } } | null> {
  const res = await fetch("https://cdn.jsdelivr.net/npm/qrcodegen@1.8.0/qrcodegen.js").catch(() => null);
  if (!res || !res.ok) return null;
  const source = await res.text();
  const factory = new Function(`${source}; return qrcodegen;`);
  const qrcodegen = factory() as {
    QrCode: { encodeText: (text: string, ecl: unknown) => { size: number; getModule: (x: number, y: number) => boolean } };
    QrSegment: { encodeSegments: unknown };
    Ecc: { MEDIUM: unknown };
  };
  return {
    Ecc: qrcodegen.Ecc,
    toMatrix: (text, ecl) => {
      const qr = qrcodegen.QrCode.encodeText(text, ecl);
      return Array.from({ length: qr.size }, (_, y) => Array.from({ length: qr.size }, (_, x) => qr.getModule(x, y)));
    },
  };
}

describe("qrMatrix", () => {
  it("rejects text too long for version 10-M", () => {
    expect(qrMatrix("x".repeat(500))).toBeNull();
  });

  it("produces a valid-size matrix for the site URL", () => {
    const m = qrMatrix("https://cardsmarketcap.com");
    expect(m).not.toBeNull();
    expect(m!.length).toBe((m![0]).length);
    expect(m!.length % 4).toBe(1); // 17 + 4v
  });

  it("matches Nayuki reference implementation exactly (EC level M)", async () => {
    const nayuki = await loadNayuki();
    if (!nayuki) return; // offline：skip，唔當 fail
    for (const text of ["https://cardsmarketcap.com", "https://cardsmarketcap.com/", "HELLO WORLD", "A", "https://cardsmarketcap.com/card/abc123?period=7d"]) {
      const expected = nayuki.toMatrix(text, nayuki.Ecc.MEDIUM);
      const actual = qrMatrix(text);
      expect(actual, `matrix for ${text}`).toEqual(expected);
    }
  });
});
