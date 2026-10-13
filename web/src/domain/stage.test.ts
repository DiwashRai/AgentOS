import { describe, expect, it } from "vitest";

import type { Schemas } from "../api/client";
import { gate, person, run, step, ticket } from "./fixtures";
import { roundText, stageModel } from "./stage";

type Phase = Schemas["PhaseStep"]["phase"];
type PhaseState = Schemas["PhaseStep"]["state"];

const PHASES: Phase[] = ["inbox", "plan", "build", "verify", "review", "done"];
const track = (states: Partial<Record<Phase, PhaseState>>) =>
  PHASES.map((phase) => ({ phase, state: states[phase] ?? "pending" }));

describe("stageModel", () => {
  it("wraps the rail in Inbox and Done bookends from the phase track", () => {
    const m = stageModel(
      ticket({
        phase_track: track({ inbox: "done", plan: "done", build: "current" }),
        rail: [step("Plan", "done"), step("Implement", "current"), step("Review", "pending")],
      }),
    );
    expect(m.stages.map((s) => s.name)).toEqual(["Inbox", "Plan", "Implement", "Review", "Done"]);
    expect(m.stages[0]?.state).toBe("done");
    expect(m.stages.at(-1)?.state).toBe("upcoming");
  });

  it("maps every rail state", () => {
    const m = stageModel(
      ticket({
        rail: [
          step("A", "done"),
          step("B", "current"),
          step("C", "pending"),
          step("D", "failed"),
          step("E", "stale"),
          step("F", "skipped"),
        ],
      }),
    );
    expect(m.stages.slice(1, -1).map((s) => s.state)).toEqual([
      "done",
      "current",
      "upcoming",
      "failed",
      "upcoming", // stale has no form in the design
      "skipped", // a disabled stage stays in the rail
    ]);
  });

  it("stops an abandoned ticket at the first stage it didn't finish, never at Done", () => {
    const m = stageModel(
      ticket({
        attention: "done",
        state: "closed",
        phase_track: track({ inbox: "done", plan: "done", done: "done" }),
        rail: [step("Plan", "done"), step("Lint", "skipped"), step("Build", "pending")],
      }),
    );
    expect(m.stages.map((s) => s.state)).toEqual(["done", "done", "skipped", "failed", "upcoming"]);
    expect(m.label).toBe("Closed");
    expect(m.closed).toBe(true);
    expect(m.live).toBe(false);
  });

  it("keeps skipped stages in place and never labels with one", () => {
    const m = stageModel(
      ticket({
        rail: [step("Plan", "done"), step("Spike", "skipped"), step("Build", "current")],
      }),
    );
    expect(m.stages.map((s) => s.name)).toEqual(["Inbox", "Plan", "Spike", "Build", "Done"]);
    expect(m.label).toBe("Build");
  });

  it("maps skipped bookends and defaults a missing bookend to upcoming", () => {
    const m = stageModel(ticket({ phase_track: [{ phase: "inbox", state: "skipped" }] }));
    expect(m.stages.map((s) => s.state)).toEqual(["skipped", "upcoming"]);
  });

  it("labels with the current stage, a failed stage, or the last done stage", () => {
    expect(
      stageModel(ticket({ rail: [step("Plan", "done"), step("Build", "current")] })).label,
    ).toBe("Build");
    expect(
      stageModel(ticket({ rail: [step("Plan", "done"), step("Build", "failed")] })).label,
    ).toBe("Build");
    expect(
      stageModel(
        ticket({
          phase_track: track({ inbox: "done", done: "done" }),
          rail: [step("Plan", "done"), step("Build", "done")],
        }),
      ).label,
    ).toBe("Done");
  });

  it("labels an untouched inbox ticket with its current bookend", () => {
    const m = stageModel(ticket({ phase_track: track({ inbox: "current" }) }));
    expect(m.label).toBe("Inbox");
  });

  it("copies each server gate onto its stage, with the person's first name", () => {
    const m = stageModel(
      ticket({
        rail: [
          step("Plan", "done", { gate: gate("passed") }),
          step("Build", "done", {
            gate: gate("waiting", {
              label: "Build sign-off",
              person: person(2, "Sam Lee"),
            }),
          }),
          step("Review", "pending"),
        ],
      }),
    );
    const [, plan, build, review] = m.stages;
    expect(plan?.gate).toEqual({
      label: "Plan sign-off",
      short: "Sign-off",
      state: "passed",
      person: undefined,
    });
    expect(build?.gate).toEqual({
      label: "Build sign-off",
      short: "Sign-off",
      state: "waiting",
      person: "Sam",
    });
    expect(review?.gate).toBeUndefined();
  });

  it("never puts a gate on a bookend", () => {
    const m = stageModel(
      ticket({
        phase_track: track({ inbox: "done" }),
        rail: [step("Plan", "done", { gate: gate("you") })],
      }),
    );
    expect(m.stages[0]?.gate).toBeUndefined();
    expect(m.stages.at(-1)?.gate).toBeUndefined();
  });

  it("labels with the parked gate's short name, on you or on someone else", () => {
    for (const state of ["you", "waiting"] as const) {
      const m = stageModel(
        ticket({
          rail: [
            step("Plan", "done", { gate: gate(state, { short: "Plan OK" }) }),
            step("Build", "pending"),
          ],
        }),
      );
      expect(m.label).toBe("Plan OK");
    }
  });

  it("ignores pending and passed gates for the label", () => {
    const m = stageModel(
      ticket({
        rail: [
          step("Plan", "done", { gate: gate("passed") }),
          step("Build", "current", { gate: gate("pending") }),
        ],
      }),
    );
    expect(m.label).toBe("Build");
  });

  it("copies a loop stage's round from the server, only for loops of more than one round", () => {
    const m = stageModel(
      ticket({
        rail: [
          step("Implement", "done", { round: 2, max_rounds: 3 }),
          step("Review", "current", { round: 2, max_rounds: 3 }),
          step("Lint", "pending", { round: 1, max_rounds: 1 }),
          step("Pr", "pending"),
        ],
      }),
    );
    const [, implement, review, lint, pr] = m.stages;
    expect(implement?.round).toEqual({ n: 2, of: 3 });
    expect(review?.round).toEqual({ n: 2, of: 3 });
    expect(lint?.round).toBeUndefined();
    expect(pr?.round).toBeUndefined();
    // the label's round is the labelled (current) stage's
    expect(m.label).toBe("Review");
    expect(m.round).toEqual({ n: 2, of: 3 });
  });

  it("gives the label a round only when it names a current or stopped loop stage", () => {
    const blocked = stageModel(
      ticket({ rail: [step("Review", "failed", { round: 3, max_rounds: 3 })] }),
    );
    expect(blocked.round).toEqual({ n: 3, of: 3 });
    const passed = stageModel(
      ticket({
        rail: [step("Review", "done", { round: 2, max_rounds: 3 }), step("Pr", "current")],
      }),
    );
    expect(passed.round).toBeUndefined();
    const parked = stageModel(
      ticket({
        rail: [
          step("Plan", "done", { gate: gate("you") }),
          step("Review", "current", { round: 1, max_rounds: 3 }),
        ],
      }),
    );
    expect(parked.round).toBeUndefined();
  });

  it("writes a round long and short", () => {
    expect(roundText({ n: 2, of: 3 })).toMatch(/2.*3/);
    expect(roundText({ n: 2, of: 3 }, true)).toBe("2/3");
  });

  it("is live only while the server says running", () => {
    expect(stageModel(ticket({ attention: "running", latest_run: run("running") })).live).toBe(
      true,
    );
    expect(stageModel(ticket({ attention: "queued", latest_run: run("queued") })).live).toBe(false);
    expect(stageModel(ticket({ attention: "needs_you", latest_run: run("running") })).live).toBe(
      false,
    );
  });
});
