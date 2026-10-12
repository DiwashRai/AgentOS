import { describe, expect, it } from "vitest";

import {
  duration,
  emptyLogText,
  isActiveRun,
  launchRef,
  runMark,
  spanSeconds,
  stageMark,
} from "./runFormat";

describe("log delivery", () => {
  it("says when an empty log will fill", () => {
    const text = (queued: boolean, active: boolean, delivery: "live" | "at_end") =>
      emptyLogText({ queued, active, delivery });
    expect(text(false, true, "at_end")).toBe("Logs arrive when the GitHub job finishes.");
    expect(text(true, true, "at_end")).toBe("Queued. Logs arrive when the GitHub job finishes.");
    expect(text(false, false, "at_end")).toBe("No output");
    expect(text(true, true, "live")).toBe("Queued. The log starts when a worker picks it up.");
    expect(text(false, true, "live")).toBe("Waiting for output");
    expect(text(false, false, "live")).toBe("No output");
  });
});

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
  const at = (launch_ref: string | null, launch_url: string | null = null) =>
    launchRef({ launch_ref, launch_url });

  it("links the launcher's URL: one Actions run, or the workflow's runs page", () => {
    expect(at("123", "https://github.com/o/r/actions/runs/1")).toEqual({
      href: "https://github.com/o/r/actions/runs/1",
      text: "Actions run ↗",
    });
    expect(at(null, "https://github.com/o/r/actions/workflows/w.yml")).toEqual({
      href: "https://github.com/o/r/actions/workflows/w.yml",
      text: "Actions ↗",
    });
  });

  it("without a URL: digits → pid, else text; never a link", () => {
    expect(at("48120")).toEqual({ href: null, text: "pid 48120" });
    expect(at("runner-7")).toEqual({ href: null, text: "runner-7" });
    expect(at("https://example.com/x")).toEqual({ href: null, text: "https://example.com/x" });
    expect(at(null)).toBeNull();
    expect(at("  ")).toBeNull();
  });
});
