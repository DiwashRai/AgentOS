import { describe, expect, it } from "vitest";

import type { Schemas } from "../api/client";
import { gate as gateInfo, person, run, step, ticket } from "./fixtures";
import {
  gate,
  outcomeLine,
  rowLook,
  runLine,
  sourceLabel,
  sourceWord,
  stageCount,
  statusLine,
  statusMark,
  waitAge,
} from "./ticketView";

type Ticket = Schemas["TicketSummary"];

const sam = person(2, "Sam Lee");
const me = person(1, "Me Myself");
const NOW = Date.parse("2026-10-11T11:03:12Z");
const STARTED = "2026-10-11T11:00:00Z";
const now = (over: Partial<Ticket["now"]> = {}): Ticket["now"] => ({
  actor: null,
  person: null,
  progress: null,
  run_id: null,
  text: "",
  ...over,
});
const resolution = (outcome: "completed" | "abandoned", note: string | null) => ({
  outcome,
  note,
  by: me,
  at: "2026-10-11T10:00:00Z",
});

/** Plan ◆ Build Review, with the gate after Plan in `state`. */
const rail = (planGate: Schemas["GateInfo"] | null, build: Schemas["RailStep"]["state"]) => [
  step("Plan", "done", { gate: planGate }),
  step("Build", build),
  step("Review", "pending"),
];

const TICKETS: Record<string, Ticket> = {
  running: ticket({
    attention: "running",
    rail: rail(gateInfo("passed"), "current"),
    now: now({ actor: "Build", run_id: 42, progress: { done: 2, total: 5 }, text: "2. Run tests" }),
    latest_run: run("running", { id: 42, started_at: STARTED }),
  }),
  queued: ticket({
    attention: "queued",
    rail: rail(null, "pending"),
    now: now({ progress: { done: 0, total: 4 } }),
    latest_run: run("queued", { id: 7 }),
  }),
  client: ticket({
    attention: "queued",
    rail: rail(null, "pending"),
    now: now({ text: "waiting for Sam's client" }),
    latest_run: run("queued", { id: 8, target: "client", client_online: false }),
  }),
  you: ticket({
    attention: "needs_you",
    rail: rail(gateInfo("you"), "pending"),
    now: now({ text: "3 tasks" }),
    latest_run: run("gated", { id: 12 }),
  }),
  someone: ticket({
    attention: "waiting",
    rail: rail(
      gateInfo("waiting", { label: "PR review", short: "Review", person: sam }),
      "pending",
    ),
    latest_run: run("gated", { id: 13 }),
  }),
  blocked: ticket({
    attention: "blocked",
    rail: rail(null, "failed"),
    now: now({ text: "Build failed" }),
    latest_run: run("failed", { id: 14, error: "exit 1" }),
  }),
  inbox: ticket({
    key: "SCRUM-8",
    source: { source: "jira", external_id: "SCRUM-8", url: "https://x/SCRUM-8" },
    dispatch_block: { code: "base_branch_missing", text: "Set a base branch first" },
  }),
  done: ticket({
    attention: "done",
    state: "done",
    rail: [step("Plan", "done"), step("Lint", "skipped"), step("Build", "done")],
    resolution: resolution("completed", " merged "),
    source: { source: "jira", external_id: "PLAT-1", url: "https://x/PLAT-1" },
  }),
  abandoned: ticket({
    attention: "done",
    state: "closed",
    rail: rail(null, "pending"),
    resolution: resolution("abandoned", null),
  }),
};

describe("one ticket, every field", () => {
  // name: [run label, elapsed, meter text, client wait, gate state·person·stage, stage count,
  //        source, edge (flat), mark kind·name·ask, status line (flag), outcome, age]
  const table: [string, ...unknown[]][] = [
    [
      "running",
      "Build · run #42",
      "3:12",
      "3/5",
      null,
      null,
      "5 stages · 1 gate",
      null,
      "live",
      "run·Build·",
      "2. Run tests",
      null,
      "now",
    ],
    [
      "queued",
      "Run #7",
      null,
      "0/4",
      null,
      null,
      "5 stages",
      null,
      "quiet",
      "queued·Queued·",
      "",
      null,
      "1h 03m",
    ],
    [
      "client",
      "Run #8",
      null,
      null,
      "offline",
      null,
      "5 stages",
      null,
      "quiet",
      "queued·Queued·",
      "waiting for Sam's client",
      null,
      "1h 03m",
    ],
    [
      "you",
      "Run #12",
      null,
      null,
      null,
      "you·Someone·Plan",
      "5 stages · 1 gate",
      null,
      "you",
      "you··Plan sign-off",
      "3 tasks",
      null,
      "1h 03m",
    ],
    [
      "someone",
      "Run #13",
      null,
      null,
      null,
      "waiting·Sam·Plan",
      "5 stages · 1 gate",
      null,
      "quiet",
      "wait·Sam·PR review",
      "",
      null,
      "1h 03m",
    ],
    [
      "blocked",
      "Run #14",
      null,
      null,
      null,
      null,
      "5 stages",
      null,
      "blocked",
      "blk·Blocked·",
      "Build failed · exit 1",
      null,
      "1h 03m",
    ],
    [
      "inbox",
      "Run",
      null,
      null,
      null,
      null,
      "2 stages",
      "Jira",
      "quiet",
      "idle·Inbox·",
      "Set a base branch first!",
      null,
      "1h 03m",
    ],
    [
      "done",
      "Run",
      null,
      null,
      null,
      null,
      "5 stages · 1 skipped",
      "Jira PLAT-1",
      "done flat",
      "done·Complete·",
      "",
      "Complete·merged",
      "1h 03m",
    ],
    [
      "abandoned",
      "Run",
      null,
      null,
      null,
      null,
      "5 stages",
      null,
      "blocked flat",
      "abandoned·Abandoned·",
      "",
      "Abandoned·",
      "1h 03m",
    ],
  ];

  it.each(table)("%s", (name, ...expected) => {
    const t = TICKETS[name];
    if (!t) throw new Error(name);

    const line = runLine(t, NOW);
    const g = gate(t);
    const look = rowLook(t);
    const mark = statusMark(t);
    const status = statusLine(t);
    const out = outcomeLine(t);

    expect([
      line.label,
      line.elapsed,
      line.meter?.text ?? null,
      line.wait?.online ?? null,
      g && `${g.state}·${g.person}·${g.stage}`,
      stageCount(t).text,
      sourceWord(t),
      look.flat ? `${look.edge} flat` : look.edge,
      `${mark.kind}·${mark.name ?? ""}·${mark.ask ?? ""}`,
      status.flag ? `${status.text}!` : status.text,
      out && `${out.word}·${out.note ?? ""}`,
      waitAge(t, NOW),
    ]).toEqual(expected);
  });
});

describe("run line", () => {
  it("joins the parts the server sent into one line", () => {
    const running = TICKETS.running;
    if (!running) throw new Error("fixture");
    expect(runLine(running, NOW).line).toBe("Build · run #42 · 3:12 · 3/5");
    expect(runLine(running, null).line).toBe("Build · run #42 · 3/5");
  });
  it("caps the meter at the total and hides it without progress or a run", () => {
    const at = (attention: Ticket["attention"], done: number, total: number) =>
      runLine(ticket({ attention, now: now({ progress: { done, total } }) }), NOW).meter;
    expect(at("running", 5, 5)?.k).toBe(5);
    expect(at("running", 0, 0)).toBeNull();
    expect(at("needs_you", 5, 5)).toBeNull();
  });
  it("drops the agent's number from the step", () => {
    expect(runLine(ticket({ now: now({ text: "12. Wire it" }) }), NOW).step).toBe("Wire it");
  });
});

describe("gate", () => {
  it("waits on the gate's person, else now.person, else Someone", () => {
    const parked = (p: Schemas["Person"] | null, nowPerson: Schemas["Person"] | null) =>
      gate(
        ticket({
          rail: [step("Review", "done", { gate: gateInfo("waiting", { person: p }) })],
          now: now({ person: nowPerson }),
        }),
      );
    expect(parked(sam, me)?.person).toBe("Sam");
    expect(parked(null, me)?.person).toBe("Me");
    expect(parked(null, null)?.person).toBe("Someone");
  });
  it("ignores passed and pending gates", () => {
    const t = ticket({ rail: [step("Plan", "done", { gate: gateInfo("passed") })] });
    expect(gate(t)).toBeNull();
    expect(gate(ticket())).toBeNull();
  });
  it("names the stage and its index", () => {
    const t = ticket({
      rail: [step("Plan", "done"), step("Build", "done", { gate: gateInfo("you") })],
    });
    expect([gate(t)?.stage, gate(t)?.index]).toEqual(["Build", 1]);
  });
});

it("counts every stage, gate and skip from the stepper's model", () => {
  const t = ticket({
    rail: [
      step("Plan", "done", { gate: gateInfo("passed") }),
      step("Build", "skipped"),
      step("Review", "skipped", { gate: gateInfo("pending") }),
    ],
  });
  expect(stageCount(t)).toMatchObject({ stages: 5, gates: 2, skipped: 2 });
  expect(stageCount(ticket({ rail: [step("Build", "pending")] })).text).toBe("3 stages");
});

it("puts the external id beside the source name only when it isn't the key", () => {
  const jira = { source: "jira" as const, external_id: "SCRUM-6", url: "" };
  expect(sourceWord(ticket({ key: "SCRUM-6", source: jira }))).toBe("Jira");
  expect(sourceWord(ticket({ key: "AOS-3", source: jira }))).toBe("Jira SCRUM-6");
});

it("labels an imported ticket by its external id and our own by its key", () => {
  const jira = { source: "jira" as const, external_id: "PLAT-1", url: "" };
  expect(sourceLabel(ticket({ key: "AOS-5", source: jira }))).toBe("Jira PLAT-1");
  expect(sourceLabel(ticket({ key: "AOS-5", source: null }))).toBe("AOS-5");
});

it("keeps the reason under the dispatch block", () => {
  const t = ticket({
    attention: "blocked",
    now: now({ text: "Build failed" }),
    latest_run: run("failed", { error: "exit 1" }),
    dispatch_block: { code: "base_branch_missing", text: "Set a base branch first" },
  });
  expect(statusLine(t)).toMatchObject({ flag: true, reason: "Build failed · exit 1" });
  const waiting = ticket({ attention: "waiting", latest_run: run("failed", { error: "x" }) });
  expect(statusLine(waiting).reason).toBe("");
});
