import { describe, expect, it } from "vitest";

import type { Schemas } from "../api/client";
import { run, ticket } from "./fixtures";
import { stageModel } from "./stage";

type Phase = Schemas["PhaseStep"]["phase"];
type PhaseState = Schemas["PhaseStep"]["state"];

const PHASES: Phase[] = ["inbox", "plan", "build", "verify", "review", "done"];
const track = (states: Partial<Record<Phase, PhaseState>>) =>
  PHASES.map((phase) => ({ phase, state: states[phase] ?? "pending" }));

const rail = (...steps: [string, Schemas["RailStep"]["state"]][]): Schemas["RailStep"][] =>
  steps.map(([name, state]) => ({ name, state, stage_id: name.toLowerCase(), phase: "build" }));

describe("stageModel", () => {
  it("wraps the rail in Inbox and Done bookends from the phase track", () => {
    const m = stageModel(
      ticket({
        phase_track: track({ inbox: "done", plan: "done", build: "current" }),
        rail: rail(["Plan", "done"], ["Implement", "current"], ["Review", "pending"]),
      }),
    );
    expect(m.stages.map((s) => s.name)).toEqual(["Inbox", "Plan", "Implement", "Review", "Done"]);
    expect(m.stages[0]?.state).toBe("done");
    expect(m.stages.at(-1)?.state).toBe("upcoming");
  });

  it("maps every rail state", () => {
    const m = stageModel(
      ticket({
        rail: rail(
          ["A", "done"],
          ["B", "current"],
          ["C", "pending"],
          ["D", "failed"],
          ["E", "stale"],
        ),
      }),
    );
    expect(m.stages.slice(1, -1).map((s) => s.state)).toEqual([
      "done",
      "current",
      "upcoming",
      "failed",
      "upcoming", // stale has no form in the design
    ]);
  });

  it("maps skipped bookends and defaults a missing bookend to upcoming", () => {
    const m = stageModel(ticket({ phase_track: [{ phase: "inbox", state: "skipped" }] }));
    expect(m.stages.map((s) => s.state)).toEqual(["skipped", "upcoming"]);
  });

  it("labels with the current stage, a failed stage, or the last done stage", () => {
    expect(stageModel(ticket({ rail: rail(["Plan", "done"], ["Build", "current"]) })).label).toBe(
      "Build",
    );
    expect(stageModel(ticket({ rail: rail(["Plan", "done"], ["Build", "failed"]) })).label).toBe(
      "Build",
    );
    expect(
      stageModel(
        ticket({
          phase_track: track({ inbox: "done", done: "done" }),
          rail: rail(["Plan", "done"], ["Build", "done"]),
        }),
      ).label,
    ).toBe("Done");
  });

  it("labels an untouched inbox ticket with its current bookend", () => {
    const m = stageModel(ticket({ phase_track: track({ inbox: "current" }) }));
    expect(m.label).toBe("Inbox");
  });

  it("draws no gates until the server sends them (CONTRACT_REQUESTS #1)", () => {
    const m = stageModel(ticket({ rail: rail(["Plan", "done"], ["Build", "current"]) }));
    expect(m.stages.every((s) => s.gate === undefined)).toBe(true);
  });

  it("is live only while a moving ticket's run is running", () => {
    expect(stageModel(ticket({ attention: "moving", latest_run: run("running") })).live).toBe(true);
    expect(stageModel(ticket({ attention: "moving", latest_run: run("queued") })).live).toBe(false);
    expect(stageModel(ticket({ attention: "needs_you", latest_run: run("running") })).live).toBe(
      false,
    );
  });
});
