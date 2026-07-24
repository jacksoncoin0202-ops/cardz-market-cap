import { describe, expect, it } from "vitest";
import { localizedCardName } from "./card-names";
import type { Locale } from "./types";

const locales: Locale[] = ["zh-TW", "zh-CN", "ja"];

describe("localizedCardName", () => {
  it("keeps upstream translated names untouched", () => {
    expect(localizedCardName("Pikachu", "比卡超", "zh-TW")).toBe("比卡超");
  });

  it("keeps English in the en locale", () => {
    expect(localizedCardName("Pikachu", null, "en")).toBe("Pikachu");
  });

  it.each(locales)("translates a simple species name for %s", (locale) => {
    expect(localizedCardName("Charizard", null, locale)).not.toBe("Charizard");
  });

  it("composes suffix-preserving names", () => {
    expect(localizedCardName("Mega Charizard X ex", null, "zh-TW")).toBe("超級噴火龍Xex");
    expect(localizedCardName("Mewtwo VSTAR", null, "ja")).toBe("ミュウツーVSTAR");
    expect(localizedCardName("Umbreon VMAX", null, "zh-CN")).toBe("月亮伊布VMAX");
  });

  it("handles exact special printings", () => {
    expect(localizedCardName("Mario Pikachu", null, "zh-TW")).toBe("瑪利歐皮卡丘");
    expect(localizedCardName("Mario Pikachu", null, "zh-CN")).toBe("马力欧皮卡丘");
    expect(localizedCardName("Mario Pikachu", null, "ja")).toBe("マリオピカチュウ");
  });

  it("translates One Piece characters", () => {
    expect(localizedCardName("Monkey D Luffy", null, "zh-TW")).toBe("蒙其・D・魯夫");
    expect(localizedCardName("Roronoa Zoro", null, "ja")).toBe("ロロノア・ゾロ");
  });

  it("uses community-consensus names for the Van Gogh and poncho Pikachu promos", () => {
    expect(localizedCardName("Pikachu with Grey Felt Hat", null, "zh-TW")).toBe("梵高皮卡丘");
    expect(localizedCardName("Pikachu with Grey Felt Hat", null, "zh-CN")).toBe("梵高皮卡丘");
    expect(localizedCardName("Poncho", null, "zh-TW")).toBe("斗篷皮卡丘");
    expect(localizedCardName("Poncho", null, "ja")).toBe("ポンチョを着たピカチュウ");
  });

  it("keeps booster-pack jargon out of One Piece parallel names", () => {
    expect(localizedCardName("Monkey.D.Luffy SEC-SP Booster Pack Emperors In The New World", null, "zh-TW")).toBe("蒙其・D・魯夫SEC-SP");
    expect(localizedCardName("Shanks SEC-SP Booster Pack ROMANCE DAWN", null, "zh-CN")).toBe("香克斯SEC-SP");
    expect(localizedCardName("Boa Hancock SR-SP Booster Pack The Future After 500 years", null, "ja")).toBe("ボア・ハンコックSR-SP");
  });

  it("falls back to English when any token is unknown", () => {
    expect(localizedCardName("Totally Unknown Card ex", null, "zh-TW")).toBe("Totally Unknown Card ex");
  });

  it("trims whitespace for exact matching", () => {
    expect(localizedCardName("  Pikachu  ", null, "zh-TW")).toBe("皮卡丘");
  });
});
