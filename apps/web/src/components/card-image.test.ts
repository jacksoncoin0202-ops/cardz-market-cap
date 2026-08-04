import { describe, expect, it, vi } from "vitest";
import { handleCardImageError } from "./card-image";

describe("CardImage", () => {
  it("replaces a failed market asset with the local placeholder", () => {
    const target = {
      dataset: {} as Record<string, string>,
      removeAttribute: vi.fn(),
      src: "/market-assets/broken.webp",
    };

    handleCardImageError({ currentTarget: target } as never);

    expect(target.removeAttribute).toHaveBeenCalledWith("srcset");
    expect(target.src).toBe("/card-placeholder.svg");
    expect(target.dataset.cardzFallback).toBe("true");
  });
});
