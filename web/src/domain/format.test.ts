import { describe, expect, it } from "vitest";

import { ticket } from "./fixtures";
import { age, avatarTone, elapsed, initials, isOld, repoShort, srcLabel } from "./format";

const now = Date.parse("2026-10-11T12:00:00Z");
const ago = (min: number) => new Date(now - min * 60_000).toISOString();

describe("age", () => {
  it.each([
    [0, "now"],
    [0.5, "now"],
    [25, "25m"],
    [59.4, "59m"],
    [59.6, "1h"],
    [65, "1h 05m"],
    [190, "3h 10m"],
    [180, "3h"],
    [2 * 1440 + 30, "2d"],
  ])("%s minutes ago is %s", (min, expected) => {
    expect(age(ago(min), now)).toBe(expected);
  });

  it("never goes negative for a clock skewed into the future", () => {
    expect(age(ago(-5), now)).toBe("now");
  });

  it("accepts a Date for now", () => {
    expect(age(ago(25), new Date(now))).toBe("25m");
  });
});

describe("isOld", () => {
  it("turns on at 90 minutes", () => {
    expect(isOld(ago(89), now)).toBe(false);
    expect(isOld(ago(90), now)).toBe(true);
  });
});

describe("elapsed", () => {
  it.each([
    [0, "0:00"],
    [9, "0:09"],
    [192, "3:12"],
    [3725, "62:05"],
    [-3, "0:00"],
  ])("%s seconds is %s", (s, expected) => {
    expect(elapsed(s)).toBe(expected);
  });
});

describe("srcLabel", () => {
  it("shows the source and its id", () => {
    const t = ticket({
      key: "AOS-3",
      source: { source: "jira", external_id: "PLAT-2231", url: "https://x/PLAT-2231" },
    });
    expect(srcLabel(t)).toBe("JIRA PLAT-2231");
  });

  it("falls back to our key", () => {
    expect(srcLabel(ticket({ key: "AOS-42" }))).toBe("AOS-42");
  });
});

describe("repoShort", () => {
  it.each([
    ["constellation/web", "web"],
    ["web", "web"],
    ["https://github.com/acme/api.git", "api"],
    ["acme/runner/", "runner"],
    [null, ""],
  ])("%s → %s", (name, expected) => {
    expect(repoShort(name)).toBe(expected);
  });
});

describe("initials", () => {
  it.each([
    ["Diwash Rai", "DR"],
    ["Maya van der Berg", "MB"],
    ["dev", "DE"],
    ["  ", "?"],
  ])("%s → %s", (name, expected) => {
    expect(initials(name)).toBe(expected);
  });
});

describe("avatarTone", () => {
  it("cycles through the four fills", () => {
    expect([0, 1, 2, 3, 4].map(avatarTone)).toEqual([1, 2, 3, 4, 1]);
  });
});
