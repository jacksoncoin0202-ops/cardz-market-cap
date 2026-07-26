import { describe, expect, it } from "vitest";
import { createRobotsPolicy } from "./robots";

describe("robots discovery policy", () => {
  it.each(["canary", "staging"])("blocks all crawling and omits sitemap discovery on %s", (environment) => {
    const policy = createRobotsPolicy(environment);
    expect(policy.rules).toEqual([{ userAgent: "*", disallow: "/" }]);
    expect(policy).not.toHaveProperty("sitemap");
  });

  it("keeps approved discovery and private-path exclusions in production", () => {
    const policy = createRobotsPolicy("production");
    expect(policy).toHaveProperty("sitemap");
    expect(JSON.stringify(policy.rules)).toContain("/data/private/");
    expect(JSON.stringify(policy.rules)).toContain("OAI-SearchBot");
  });

  it("excludes the internal tuning lab from every crawler in production", () => {
    const rules = createRobotsPolicy("production").rules;
    const entries = Array.isArray(rules) ? rules : [rules];
    for (const rule of entries) {
      const disallow = rule.disallow;
      const paths = Array.isArray(disallow) ? disallow : disallow ? [disallow] : [];
      expect(paths.includes("/tune") || paths.includes("/")).toBe(true);
    }
  });
});
