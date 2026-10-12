import { describe, expect, it } from "vitest";
import { HUES, hueOf, keyHue, personHue, projectPrefix } from "./identity";

describe("projectPrefix", () => {
  it("takes the project part of a key", () => {
    expect(projectPrefix("AOS-14")).toBe("AOS");
    expect(projectPrefix("scrum-3")).toBe("SCRUM");
    expect(projectPrefix("MY-PROJ-12")).toBe("MY-PROJ");
  });
  it("treats a bare prefix as its own project", () => {
    expect(projectPrefix("AOS")).toBe("AOS");
    expect(projectPrefix(" SCRUM ")).toBe("SCRUM");
  });
});

describe("keyHue", () => {
  it("is stable, and the same for every key of one project", () => {
    expect(keyHue("AOS-14")).toBe(keyHue("AOS-14"));
    expect(keyHue("AOS-14")).toBe(keyHue("AOS-9"));
    expect(keyHue("AOS-14")).toBe(keyHue("AOS"));
    expect(keyHue("SCRUM-21")).toBe(keyHue("scrum-1"));
  });
  it("spreads projects across the hues", () => {
    const prefixes = ["AOS", "SCRUM", "PLAT", "WEB", "OPS", "DATA", "API", "INFRA", "MOB", "SEC"];
    const seen = new Set(prefixes.map(keyHue));
    expect(seen.size).toBeGreaterThanOrEqual(4);
  });
});

describe("personHue", () => {
  it("is stable per id", () => {
    expect(personHue(7)).toBe(personHue(7));
  });
  it("spreads sequential ids across all five hues", () => {
    const ids = Array.from({ length: 30 }, (_, i) => i + 1);
    const counts = new Map<string, number>();
    for (const id of ids) counts.set(personHue(id), (counts.get(personHue(id)) ?? 0) + 1);
    expect(counts.size).toBe(HUES.length);
    for (const n of counts.values()) expect(n).toBeLessThanOrEqual(12);
  });
});

describe("hueOf", () => {
  it("always returns one of the five hues", () => {
    for (const seed of ["", "a", "zz", "日本", "AOS"]) expect(HUES).toContain(hueOf(seed));
  });
});
