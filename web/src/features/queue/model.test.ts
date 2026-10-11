import { describe, expect, it } from "vitest";

import { counts } from "../../domain/counts";
import { gate, person, run, step, ticket } from "../../domain/fixtures";
import { groups } from "../../domain/groups";
import {
  askLabel,
  atWorkNote,
  compactHref,
  listsAnything,
  meterOf,
  MINE_QUEUED,
  myRunning,
  NO_REPO,
  rackColumns,
  rackGroups,
  runName,
  runSeconds,
  showAllHref,
  statusMark,
  subLine,
  waitingPerson,
} from "./model";

const me = { id: 1 };
const sam = person(2, "Sam Lee");
const now = (over: Partial<ReturnType<typeof ticket>["now"]> = {}) => ({
  actor: null,
  person: null,
  progress: null,
  run_id: null,
  text: "",
  ...over,
});

describe("subLine", () => {
  it("names the agent and run when there is one", () => {
    const t = ticket({ repo_name: "org/web", now: now({ actor: "Implement", run_id: 42 }) });
    expect(subLine(t, me)).toBe("org/web · Implement · run #42");
  });
  it("falls back to the latest run's id and drops a missing actor", () => {
    const t = ticket({ repo_name: "org/web", latest_run: run("succeeded", { id: 7 }) });
    expect(subLine(t, me)).toBe("org/web · run #7");
  });
  it("names someone else's ticket, not mine", () => {
    expect(subLine(ticket({ repo_name: "web", owner: sam }), me)).toBe("web · Sam's ticket");
    expect(subLine(ticket({ repo_name: "web", owner: person(1) }), me)).toBe("web");
    expect(subLine(ticket(), me)).toBe("");
  });
});

describe("asks and people", () => {
  const parked = ticket({
    attention: "needs_you",
    rail: [
      step("Plan", "done", { gate: gate("you", { label: "Plan sign-off" }) }),
      step("Build", "pending"),
    ],
  });
  it("asks for the parked gate, else 'Your move'", () => {
    expect(askLabel(parked)).toBe("Plan sign-off");
    expect(askLabel(ticket({ attention: "needs_you" }))).toBe("Your move");
  });
  it("waits on the gate's person, else now.person", () => {
    const g = ticket({
      rail: [step("Review", "done", { gate: gate("waiting", { person: sam }) })],
    });
    expect(waitingPerson(g)?.display_name).toBe("Sam Lee");
    expect(waitingPerson(ticket({ now: now({ person: sam }) }))?.id).toBe(2);
    expect(waitingPerson(ticket())).toBeNull();
  });
  it("builds the status mark per attention", () => {
    expect(statusMark(parked)).toEqual({ kind: "you", ask: "Plan sign-off" });
    const w = ticket({
      attention: "waiting",
      rail: [
        step("Review", "done", { gate: gate("waiting", { label: "PR review", person: sam }) }),
      ],
    });
    expect(statusMark(w)).toEqual({ kind: "wait", name: "Sam", ask: "PR review" });
    expect(statusMark(ticket({ attention: "blocked" }))).toEqual({ kind: "blk", name: "Blocked" });
    expect(statusMark(ticket({ attention: "done" }))).toEqual({ kind: "done", name: "Merged" });
  });
});

describe("runs", () => {
  it("names the run", () => {
    expect(runName(ticket({ now: now({ actor: "Plan", run_id: 3 }) }))).toBe("Plan #3");
    expect(runName(ticket())).toBe("Run");
  });
  it("meters k = done + 1 capped at total while running; hides without progress", () => {
    const p = (done: number, total: number) => now({ progress: { done, total } });
    expect(meterOf(ticket({ attention: "running", now: p(2, 5) }))).toEqual({
      k: 3,
      n: 5,
      state: "running",
    });
    expect(meterOf(ticket({ attention: "running", now: p(5, 5) }))?.k).toBe(5);
    expect(meterOf(ticket({ attention: "queued", now: p(0, 4) }))?.state).toBe("queued");
    expect(meterOf(ticket({ attention: "running" }))).toBeNull();
    expect(meterOf(ticket({ attention: "needs_you", now: p(5, 5) }))).toBeNull();
  });
  it("counts elapsed seconds from started_at", () => {
    const t = ticket({ latest_run: run("running", { started_at: "2026-10-11T10:00:00Z" }) });
    expect(runSeconds(t, Date.parse("2026-10-11T10:01:05Z"))).toBe(65);
    expect(runSeconds(ticket({ latest_run: run("queued") }), 0)).toBeNull();
  });
  it("splits mine from the team in the note", () => {
    expect(atWorkNote({ mine: 2, running: 4, queued: 1 })).toBe("2 yours · 3 across the team");
  });
});

describe("the rack", () => {
  const mine = person(1, "Me");
  const tickets = [
    ticket({ key: "A-1", attention: "running", owner: mine, repo_name: "web" }),
    ticket({ key: "A-2", attention: "queued", owner: mine, repo_name: "web" }),
    ticket({ key: "B-1", attention: "queued", owner: sam, repo_name: "web" }),
    ticket({ key: "B-2", attention: "running", owner: sam, repo_name: "web" }),
    ticket({ key: "B-3", attention: "running", owner: null, repo_name: "api" }),
    ticket({ key: "B-4", attention: "running", owner: sam, repo_name: null }),
    ticket({ key: "C-1", attention: "needs_you", owner: sam, repo_name: "web" }),
  ];
  const g = groups(tickets, me);

  it("puts my queued first, then the team's others by repo, running before queued", () => {
    const rack = rackGroups(tickets, g.atWork, me);
    expect(rack.map((x) => [x.name, x.items.map((t) => t.key)])).toEqual([
      [MINE_QUEUED, ["A-2"]],
      ["api", ["B-3"]],
      ["web", ["B-2", "B-1"]],
      [NO_REPO, ["B-4"]],
    ]);
  });
  it("tiles only my running runs", () => {
    expect(myRunning(g.atWork).map((t) => t.key)).toEqual(["A-1"]);
  });
  it("lays groups into the shortest column, in order", () => {
    const grp = (id: string, n: number) => ({
      id,
      name: id,
      items: Array.from({ length: n }, (_, i) => i),
    });
    const cols = rackColumns([grp("a", 4), grp("b", 1), grp("c", 1), grp("d", 1)], 3);
    expect(cols.map((c) => c.map((x) => x.id))).toEqual([["a"], ["b", "d"], ["c"]]);
    expect(rackColumns([], 3)).toEqual([[], [], []]);
  });
  it("counts agree with the groups", () => {
    const c = counts(tickets, me);
    expect(c.mine).toBe(g.atWork.length);
  });
});

it("links Show all to the compact filter, keeping the search", () => {
  expect(showAllHref("needYou")).toBe("/compact?f=needs_you");
  expect(showAllHref("doneRecent")).toBe("/compact?f=done");
  expect(showAllHref("blocked", "talk pane")).toBe("/compact?f=blocked&q=talk+pane");
  expect(compactHref({ q: "" })).toBe("/compact");
});

it("knows when My queue lists nothing (inbox and the team's waiting tickets are not on it)", () => {
  const inbox = ticket({ key: "AOS-1", attention: "inbox" });
  const theirs = ticket({ key: "AOS-2", attention: "waiting", owner: sam });
  const quiet = [inbox, theirs];
  expect(listsAnything(groups(quiet, me), rackGroups(quiet, [], me))).toBe(false);
  const blocked = [ticket({ key: "AOS-3", attention: "blocked" })];
  expect(listsAnything(groups(blocked, me), [])).toBe(true);
  const running = [ticket({ key: "AOS-4", attention: "running", owner: sam })];
  expect(listsAnything(groups(running, me), rackGroups(running, [], me))).toBe(true);
});
