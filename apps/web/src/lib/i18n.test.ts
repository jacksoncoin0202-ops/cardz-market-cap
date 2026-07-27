import { describe, expect, it } from "vitest";
import { cardLanguages, copy, localizedCardLanguage } from "./i18n";
import { locales } from "./types";

describe("localizedCardLanguage", () => {
  it("never renders a raw ISO code for a known printing language", () => {
    for (const locale of locales) {
      for (const language of cardLanguages) {
        const label = localizedCardLanguage(language, locale);
        expect(label).not.toBe(language);
        expect(label.length).toBeGreaterThan(0);
      }
    }
  });

  it("covers the two languages the published snapshot actually ships", () => {
    expect(localizedCardLanguage("ja", "zh-TW")).toBe("日文");
    expect(localizedCardLanguage("en", "zh-TW")).toBe("英文");
    expect(localizedCardLanguage("ja", "zh-CN")).toBe("日文");
    expect(localizedCardLanguage("ja", "ja")).toBe("日本語");
    expect(localizedCardLanguage("ja", "en")).toBe("Japanese");
    expect(localizedCardLanguage("ja", "ko")).toBe("일본어");
  });

  it("accepts the ingest layer's alternate spellings", () => {
    expect(localizedCardLanguage("zh-TW", "en")).toBe("Traditional Chinese");
    expect(localizedCardLanguage("zhcn", "en")).toBe("Simplified Chinese");
    expect(localizedCardLanguage("JP", "en")).toBe("Japanese");
    expect(localizedCardLanguage("English", "en")).toBe("English");
  });

  it("keeps an unrecognised upstream code rather than inventing a label", () => {
    expect(localizedCardLanguage("xx", "zh-TW")).toBe("xx");
    expect(localizedCardLanguage("", "zh-TW")).toBe("");
  });

  it("keeps every locale's product copy in written form", () => {
    for (const locale of ["zh-TW", "zh-CN"] as const) {
      const written = Object.values(copy[locale].languages).join(" ");
      for (const colloquial of ["嘅", "咗", "喺", "唔係"]) {
        expect(written).not.toContain(colloquial);
      }
    }
  });
});
