import { describe, expect, it } from "vitest";

import {
  emptyLogText,
  isActiveRun,
  launchRef,
  runMark,
  spanSeconds,
  stageMark,
  syncBackLink,
  type RunStatus,
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
    const mark = (status: RunStatus) => runMark({ status, queue_reason: null });
    expect(mark("running")).toEqual({ kind: "run", word: "Running" });
    expect(mark("queued").kind).toBe("queued");
    expect(mark("dispatched").kind).toBe("queued");
    expect(mark("succeeded")).toEqual({ kind: "done", word: "Passed" });
    expect(mark("gated")).toEqual({ kind: "you", word: "Gated" });
    expect(mark("failed").kind).toBe("blk");
    expect(mark("cancelled").kind).toBe("blk");
    expect(stageMark("pending").kind).toBe("idle");
  });

  it("a queued run's mark carries the server's queue reason", () => {
    const reason = "waiting for run #3's worker to stop";
    const m = runMark({ status: "queued", queue_reason: reason });
    expect(m.kind).toBe("queued");
    expect(m.word).toContain(reason);
  });

  it("active = queued, dispatched, running", () => {
    expect(isActiveRun("queued")).toBe(true);
    expect(isActiveRun("dispatched")).toBe(true);
    expect(isActiveRun("running")).toBe(true);
    expect(isActiveRun("gated")).toBe(false);
    expect(isActiveRun(undefined)).toBe(false);
  });
});

describe("syncBackLink", () => {
  it.each([
    [1, 812, "sync_back.zip · 1 file · 812 B"],
    [3, 1229, "sync_back.zip · 3 files · 1.2 KB"],
  ])("%s files, %s bytes → %s", (files, bytes, text) => {
    expect(syncBackLink(7, { files, bytes })).toEqual({ href: "/api/runs/7/sync-back", text });
  });
});

describe("launchRef", () => {
  const at = (launch_ref: string | null, launch_url: string | null = null) =>
    launchRef({ launch_ref, launch_url, target: "server" });

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

  it("names the client of a client run, even an all-digit name; nothing before it takes the run", () => {
    const client = (launch_ref: string | null) =>
      launchRef({ launch_ref, launch_url: null, target: "client" });
    expect(client("work laptop")?.href).toBeNull();
    expect(client("work laptop")?.text).toContain("work laptop");
    expect(client("4242")?.text).not.toContain("pid");
    expect(client(null)).toBeNull();
  });
});
