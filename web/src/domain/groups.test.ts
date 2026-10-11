import { describe, expect, it } from "vitest";

import { person, run, ticket } from "./fixtures";
import { groups, since } from "./groups";

const me = { id: 1 };
const mine = person(1);
const theirs = person(2);
const at = (hh: string) => `2026-10-11T${hh}:00:00Z`;
const keys = (ts: { key: string }[]) => ts.map((t) => t.key);

describe("since", () => {
  it("uses phase_since, else updated_at", () => {
    expect(since(ticket({ phase_since: at("09"), updated_at: at("10") }))).toBe(at("09"));
    expect(since(ticket({ phase_since: null, updated_at: at("10") }))).toBe(at("10"));
  });
});

describe("groups", () => {
  it("puts needs-you tickets longest wait first, whoever owns them", () => {
    const g = groups(
      [
        ticket({ key: "A", attention: "needs_you", owner: mine, phase_since: at("10") }),
        ticket({ key: "B", attention: "needs_you", owner: theirs, phase_since: at("08") }),
        ticket({ key: "C", attention: "needs_you", owner: mine, phase_since: at("09") }),
      ],
      me,
    );
    expect(keys(g.needYou)).toEqual(["B", "C", "A"]);
  });

  it("lists my running runs before my queued ones, most recent first", () => {
    const g = groups(
      [
        ticket({ key: "Q", attention: "moving", owner: mine, latest_run: run("queued") }),
        ticket({
          key: "R1",
          attention: "moving",
          owner: mine,
          latest_run: run("running"),
          phase_since: at("08"),
        }),
        ticket({
          key: "R2",
          attention: "moving",
          owner: mine,
          latest_run: run("running"),
          phase_since: at("09"),
        }),
        ticket({ key: "X", attention: "moving", owner: theirs, latest_run: run("running") }),
      ],
      me,
    );
    expect(keys(g.atWork)).toEqual(["R2", "R1", "Q"]);
  });

  it("lists only my waiting tickets, longest first", () => {
    const g = groups(
      [
        ticket({ key: "W1", attention: "waiting", owner: mine, phase_since: at("10") }),
        ticket({ key: "W2", attention: "waiting", owner: mine, phase_since: at("07") }),
        ticket({ key: "W3", attention: "waiting", owner: theirs, phase_since: at("06") }),
      ],
      me,
    );
    expect(keys(g.waitingMine)).toEqual(["W2", "W1"]);
  });

  it("lists every blocked ticket, mine first, then longest", () => {
    const g = groups(
      [
        ticket({ key: "T1", attention: "stuck", owner: theirs, phase_since: at("06") }),
        ticket({ key: "M1", attention: "stuck", owner: mine, phase_since: at("09") }),
        ticket({ key: "M2", attention: "stuck", owner: mine, phase_since: at("08") }),
        ticket({ key: "T2", attention: "stuck", owner: null, phase_since: at("05") }),
      ],
      me,
    );
    expect(keys(g.blocked)).toEqual(["M2", "M1", "T2", "T1"]);
  });

  it("lists done tickets most recent first and leaves the inbox out", () => {
    const g = groups(
      [
        ticket({ key: "D1", attention: "done", updated_at: at("08") }),
        ticket({ key: "D2", attention: "done", updated_at: at("10") }),
        ticket({ key: "I", attention: "up_next", owner: mine }),
      ],
      me,
    );
    expect(keys(g.doneRecent)).toEqual(["D2", "D1"]);
    const all = [...g.needYou, ...g.atWork, ...g.waitingMine, ...g.blocked, ...g.doneRecent];
    expect(keys(all)).not.toContain("I");
  });

  it("breaks ties by key so the order is stable", () => {
    const g = groups(
      [
        ticket({ key: "B", attention: "needs_you", phase_since: at("09") }),
        ticket({ key: "A", attention: "needs_you", phase_since: at("09") }),
      ],
      me,
    );
    expect(keys(g.needYou)).toEqual(["A", "B"]);
  });
});
