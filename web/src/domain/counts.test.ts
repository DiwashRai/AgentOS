import { describe, expect, it } from "vitest";

import { COUNTERS, counts, shippedThisWeek } from "./counts";
import { person, run, ticket } from "./fixtures";

const me = { id: 1 };
const mine = person(1);
const theirs = person(2);

describe("counts", () => {
  it("is all zero for no tickets", () => {
    expect(Object.values(counts([], me)).every((n) => n === 0)).toBe(true);
  });

  it("counts every bucket from the same tickets", () => {
    const c = counts(
      [
        ticket({ attention: "needs_you", owner: mine }),
        ticket({ attention: "needs_you", owner: theirs }),
        ticket({ attention: "blocked", owner: mine }),
        ticket({ attention: "blocked", owner: theirs }),
        ticket({ attention: "running", owner: mine, latest_run: run("running") }),
        ticket({ attention: "running", owner: theirs, latest_run: run("running") }),
        ticket({ attention: "queued", owner: mine, latest_run: run("queued") }),
        ticket({ attention: "queued", owner: theirs, latest_run: run("dispatched") }),
        ticket({ attention: "waiting", owner: mine }),
        ticket({ attention: "waiting", owner: theirs }),
        ticket({ attention: "inbox", owner: mine }),
        ticket({ attention: "done", owner: mine }),
      ],
      me,
    );
    expect(c).toEqual({
      needYou: 2,
      blockedMine: 1,
      mine: 2,
      running: 2,
      queued: 2,
      blocked: 2,
      waitingMine: 1,
      waiting: 2,
      inbox: 1,
      inFlight: 10,
      done: 1,
    });
  });

  it("counts nothing as mine when signed out", () => {
    const c = counts([ticket({ attention: "blocked", owner: mine })], null);
    expect(c.blocked).toBe(1);
    expect(c.blockedMine).toBe(0);
  });

  it("has a counter label for every count", () => {
    expect(Object.keys(COUNTERS).sort()).toEqual(Object.keys(counts([], me)).sort());
  });
});

describe("shippedThisWeek", () => {
  const now = Date.parse("2026-10-12T12:00:00Z");
  const done = (over: Parameters<typeof ticket>[0]) =>
    ticket({ attention: "done", state: "done", ...over });

  it("counts Complete tickets resolved in the last 7 days", () => {
    const by = person(1);
    const tickets = [
      done({ resolution: { outcome: "completed", note: null, by, at: "2026-10-11T09:00:00Z" } }),
      done({ resolution: { outcome: "completed", note: null, by, at: "2026-10-01T09:00:00Z" } }),
      done({ resolution: { outcome: "abandoned", note: null, by, at: "2026-10-11T09:00:00Z" } }),
      done({ attention_since: "2026-10-10T09:00:00Z" }), // marked done before resolutions existed
      ticket({ attention: "needs_you", attention_since: "2026-10-11T09:00:00Z" }),
    ];
    expect(shippedThisWeek(tickets, now)).toBe(2);
  });
});
