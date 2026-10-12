import { describe, expect, it } from "vitest";

import { person, run, ticket } from "./fixtures";
import { groups, since } from "./groups";

const me = { id: 1 };
const mine = person(1);
const theirs = person(2);
const at = (hh: string) => `2026-10-11T${hh}:00:00Z`;
const keys = (ts: { key: string }[]) => ts.map((t) => t.key);

describe("since", () => {
  it("is the server's attention_since, never updated_at", () => {
    expect(since(ticket({ attention_since: at("09"), updated_at: at("10") }))).toBe(at("09"));
  });
});

describe("groups", () => {
  it("puts needs-you tickets longest wait first, whoever owns them", () => {
    const g = groups(
      [
        ticket({ key: "A", attention: "needs_you", owner: mine, attention_since: at("10") }),
        ticket({ key: "B", attention: "needs_you", owner: theirs, attention_since: at("08") }),
        ticket({ key: "C", attention: "needs_you", owner: mine, attention_since: at("09") }),
      ],
      me,
    );
    expect(keys(g.needYou)).toEqual(["B", "C", "A"]);
  });

  it("lists my running runs before my queued ones, most recent first", () => {
    const g = groups(
      [
        ticket({ key: "Q1", attention: "queued", owner: mine, attention_since: at("11") }),
        ticket({ key: "Q2", attention: "queued", owner: mine, attention_since: at("12") }),
        ticket({ key: "R1", attention: "running", owner: mine, attention_since: at("08") }),
        ticket({ key: "R2", attention: "running", owner: mine, attention_since: at("09") }),
        ticket({ key: "X", attention: "running", owner: theirs }),
      ],
      me,
    );
    expect(keys(g.atWork)).toEqual(["R2", "R1", "Q2", "Q1"]);
  });

  it("splits running and queued on attention alone, not the run status", () => {
    const g = groups(
      [
        ticket({ key: "Q", attention: "queued", owner: mine, latest_run: run("running") }),
        ticket({ key: "R", attention: "running", owner: mine, latest_run: run("queued") }),
      ],
      me,
    );
    expect(keys(g.atWork)).toEqual(["R", "Q"]);
  });

  it("folds blocked tickets that are mine or nobody's into needs-you, by longest wait", () => {
    const g = groups(
      [
        ticket({ key: "Y", attention: "needs_you", owner: mine, attention_since: at("09") }),
        ticket({ key: "M", attention: "blocked", owner: mine, attention_since: at("08") }),
        ticket({ key: "N", attention: "blocked", owner: null, attention_since: at("10") }),
        ticket({ key: "T", attention: "blocked", owner: theirs, attention_since: at("05") }),
      ],
      me,
    );
    expect(keys(g.needYou)).toEqual(["M", "Y", "N"]);
  });

  it("lists my inbox tickets as ready to start, longest wait first; nobody's stay out", () => {
    const g = groups(
      [
        ticket({ key: "I1", attention: "inbox", owner: mine, attention_since: at("10") }),
        ticket({ key: "I2", attention: "inbox", owner: mine, attention_since: at("07") }),
        ticket({ key: "I3", attention: "inbox", owner: null, attention_since: at("06") }),
        ticket({ key: "I4", attention: "inbox", owner: theirs, attention_since: at("06") }),
      ],
      me,
    );
    expect(keys(g.readyMine)).toEqual(["I2", "I1"]);
  });

  it("lists my done tickets most recent first, and nobody else's", () => {
    const g = groups(
      [
        ticket({ key: "D1", attention: "done", owner: mine, attention_since: at("08") }),
        ticket({ key: "D2", attention: "done", owner: mine, attention_since: at("10") }),
        ticket({ key: "D3", attention: "done", owner: theirs, attention_since: at("11") }),
      ],
      me,
    );
    expect(keys(g.doneMine)).toEqual(["D2", "D1"]);
  });

  it("leaves waiting tickets out: one I own is never waiting, and others' aren't mine", () => {
    const g = groups(
      [
        ticket({ key: "W1", attention: "waiting", owner: theirs }),
        ticket({ key: "W2", attention: "waiting", owner: mine }),
      ],
      me,
    );
    const all = [...g.needYou, ...g.atWork, ...g.readyMine, ...g.doneMine];
    expect(all).toEqual([]);
  });

  it("breaks ties by key so the order is stable", () => {
    const g = groups(
      [
        ticket({ key: "B", attention: "needs_you", attention_since: at("09") }),
        ticket({ key: "A", attention: "needs_you", attention_since: at("09") }),
      ],
      me,
    );
    expect(keys(g.needYou)).toEqual(["A", "B"]);
  });
});
