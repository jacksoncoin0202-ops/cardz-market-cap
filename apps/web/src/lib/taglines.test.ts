/* Prestige taglines contract test：100 句/locale、非空、冇重複；picker deterministic + fallback */
import { describe, expect, it } from "vitest";
import { locales } from "./types";
import { pickRandomTagline, pickTagline, TAGLINE_COUNT, TAGLINES } from "./taglines";

describe("TAGLINES", () => {
  it("has exactly 100 non-empty, unique taglines per locale", () => {
    for (const locale of locales) {
      const list = TAGLINES[locale];
      expect(list, locale).toHaveLength(TAGLINE_COUNT);
      for (const line of list) {
        expect(typeof line).toBe("string");
        expect(line.trim().length, `${locale}: empty tagline`).toBeGreaterThan(0);
        expect(line, `${locale}: untrimmed tagline`).toBe(line.trim());
      }
      expect(new Set(list).size, `${locale}: duplicate taglines`).toBe(TAGLINE_COUNT);
    }
  });
});

describe("pickTagline", () => {
  it("is deterministic for the same slot and seed", () => {
    expect(pickTagline("en", "footer", "2026-07-31")).toBe(pickTagline("en", "footer", "2026-07-31"));
    expect(pickTagline("ja", "not-found", "2026-07-31")).toBe(pickTagline("ja", "not-found", "2026-07-31"));
  });

  it("returns a member of the locale's list", () => {
    for (const locale of locales) {
      expect(TAGLINES[locale]).toContain(pickTagline(locale, "footer", "2026-07-31"));
    }
  });

  it("gives a stable index for a fixed seed (same slot+day, everyone same line)", () => {
    const a = pickTagline("en", "footer", "2030-01-01");
    const b = pickTagline("en", "footer", "2030-01-01");
    expect(a).toBe(b);
    // 唔同 slot / 唔同日 → 條 key 唔同（index 由 hash 決定，唔強求一定唔同句，但 key 有分別）
    expect(typeof a).toBe("string");
  });

  it("varies across days and slots", () => {
    const days = Array.from({ length: 30 }, (_, i) => pickTagline("en", "footer", `2026-07-${String(i + 1).padStart(2, "0")}`));
    expect(new Set(days).size).toBeGreaterThan(1);
    const slots = Array.from({ length: 10 }, (_, i) => pickTagline("en", `slot-${i}`, "2026-07-31"));
    expect(new Set(slots).size).toBeGreaterThan(1);
  });

  it("falls back to the en list for an unknown locale", () => {
    const picked = pickTagline("fr" as never, "footer", "2026-07-31");
    expect(TAGLINES.en).toContain(picked);
  });
});

describe("pickRandomTagline", () => {
  it("returns in-list strings for boundary random values", () => {
    expect(TAGLINES.en).toContain(pickRandomTagline("en", () => 0));
    expect(TAGLINES.en).toContain(pickRandomTagline("en", () => 0.999999999));
    for (const locale of locales) {
      expect(TAGLINES[locale]).toContain(pickRandomTagline(locale, () => 0.5));
    }
  });

  it("falls back to the en list for an unknown locale", () => {
    expect(TAGLINES.en).toContain(pickRandomTagline("fr" as never, () => 0.42));
  });
});
