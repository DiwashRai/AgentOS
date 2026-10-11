import { describe, expect, it } from "vitest";

import { duration, isActiveRun, launchRef, runMark, spanSeconds, stageMark } from "./runFormat";

describe("duration", () => {
  it("m:ss under an hour", () => {
    expect(duration(0)).toBe("0:00");
    expect(duration(42.9)).toBe("0:42");
    expect(duration(725)).toBe("12:05");
    expect(duration(3599)).toBe("59:59");
  });

  it("h m from an hour", () => {
    expect(duration(3600)).toBe("1h 0m");
    expect(duration(3600 + 4 * 60 + 59)).toBe("1h 4m");
    expect(duration(26 * 3600)).toBe("26h 0m");
  });

  it("never negative", () => {
    expect(duration(-5)).toBe("0:00");
  });
});

describe("spanSeconds", () => {
  const start = "2026-10-11T10:00:00Z";
  it("finished span", () => {
    expect(spanSeconds(start, "2026-10-11T10:01:30Z", 0)).toBe(90);
  });
  it("open span runs to now", () => {
    expect(spanSeconds(start, null, Date.parse("2026-10-11T10:00:10Z"))).toBe(10);
  });
  it("null when never started", () => {
    expect(spanSeconds(null, null, 0)).toBeNull();
  });
});

describe("marks", () => {
  it("map run statuses to kit marks", () => {
    expect(runMark("running")).toEqual({ kind: "run", word: "Running" });
    expect(runMark("queued").kind).toBe("queued");
    expect(runMark("dispatched").kind).toBe("queued");
    expect(runMark("succeeded")).toEqual({ kind: "done", word: "Passed" });
    expect(runMark("gated")).toEqual({ kind: "you", word: "Gated" });
    expect(runMark("failed").kind).toBe("blk");
    expect(runMark("cancelled").kind).toBe("blk");
    expect(stageMark("pending").kind).toBe("idle");
  });

  it("active = queued, dispatched, running", () => {
    expect(isActiveRun("queued")).toBe(true);
    expect(isActiveRun("dispatched")).toBe(true);
    expect(isActiveRun("running")).toBe(true);
    expect(isActiveRun("gated")).toBe(false);
    expect(isActiveRun(undefined)).toBe(false);
  });
});

describe("launchRef", () => {
  it("URL → link, digits → pid, else text", () => {
    expect(launchRef("https://github.com/o/r/actions/runs/1")).toEqual({
      href: "https://github.com/o/r/actions/runs/1",
      text: "Job ↗",
    });
    expect(launchRef("48120")).toEqual({ href: null, text: "pid 48120" });
    expect(launchRef("runner-7")).toEqual({ href: null, text: "runner-7" });
    expect(launchRef(null)).toBeNull();
    expect(launchRef("  ")).toBeNull();
  });
});
