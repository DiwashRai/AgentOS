import { describe, expect, it } from "vitest";

import { run } from "./fixtures";
import {
  age,
  ageParts,
  ago,
  duration,
  isOld,
  plural,
  prLabel,
  repoShort,
  runSeconds,
} from "./format";

const now = Date.parse("2026-10-11T12:00:00Z");
const minAgo = (min: number) => new Date(now - min * 60_000).toISOString();

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
    expect(age(minAgo(min), now)).toBe(expected);
  });

  it("never goes negative for a clock skewed into the future", () => {
    expect(age(minAgo(-5), now)).toBe("now");
  });

  it("accepts a Date for now", () => {
    expect(age(minAgo(25), new Date(now))).toBe("25m");
  });
});

describe("ageParts", () => {
  it("splits each number from its unit", () => {
    expect(ageParts("38m")).toEqual([{ n: "38", unit: "m" }]);
    expect(ageParts("1h 05m")).toEqual([
      { n: "1", unit: "h" },
      { n: "05", unit: "m" },
    ]);
    expect(ageParts("2d")).toEqual([{ n: "2", unit: "d" }]);
  });

  it("keeps text without a number+unit pair whole", () => {
    expect(ageParts("now")).toEqual([{ n: "now", unit: "" }]);
    expect(ageParts("3h ago")).toEqual([{ n: "3h ago", unit: "" }]);
  });
});

describe("isOld", () => {
  it("turns on at 90 minutes", () => {
    expect(isOld(minAgo(89), now)).toBe(false);
    expect(isOld(minAgo(90), now)).toBe(true);
  });
});

describe("ago", () => {
  it.each([
    [0.5, "just now"],
    [5, "5m ago"],
    [190, "3h 10m ago"],
    [-5, "just now"],
  ])("%s minutes ago is %s", (min, expected) => {
    expect(ago(minAgo(min), now)).toBe(expected);
  });
});

describe("duration", () => {
  it.each([
    [0, "0:00"],
    [42.9, "0:42"],
    [59, "0:59"],
    [725, "12:05"],
    [3599, "59:59"],
    [3600, "1h 0m"],
    [3600 + 4 * 60 + 59, "1h 4m"],
    [26 * 3600, "26h 0m"],
    [-5, "0:00"],
    [Number.NaN, "0:00"],
  ])("%s seconds is %s", (s, expected) => {
    expect(duration(s)).toBe(expected);
  });
});

describe("runSeconds", () => {
  const started = run("running", { started_at: "2026-10-11T11:58:55Z" });

  it("counts whole seconds from started_at", () => {
    expect(runSeconds(started, now + 500)).toBe(65);
  });

  it("is null before the run starts", () => {
    expect(runSeconds(run("queued", { started_at: null }), now)).toBeNull();
    expect(runSeconds(null, now)).toBeNull();
    expect(runSeconds(run("running", { started_at: "not a time" }), now)).toBeNull();
  });

  it("never goes negative", () => {
    expect(runSeconds(started, Date.parse("2026-10-11T11:58:00Z"))).toBe(0);
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

describe("prLabel", () => {
  it.each([
    ["https://github.com/DiwashRai/agentos-scratch/pull/2", "PR #2"],
    ["https://github.com/acme/api/pull/123/", "PR #123"],
    ["https://gitlab.example/acme/api/-/merge_requests/7", "PR"],
  ])("%s → %s", (url, expected) => {
    expect(prLabel(url)).toBe(expected);
  });
});

it("pluralises the word after a count", () => {
  expect(plural(1, "ticket")).toBe("ticket");
  expect(plural(0, "repo")).toBe("repos");
});
