import { describe, expect, it } from "vitest";
import type { GlyphGate, GlyphModel } from "../domain/stage";
import {
  collapsesGates,
  gateTitle,
  glyphItems,
  glyphTitle,
  isBareGate,
  parkedGate,
  stageCaption,
} from "./glyph";

const gate = (label: string, short: string, state: GlyphGate["state"], person?: string) => ({
  label,
  short,
  state,
  person,
});

const parked: GlyphModel = {
  live: false,
  label: "Sign-off",
  stages: [
    { name: "Inbox", state: "done" },
    { name: "Plan", state: "done", gate: gate("Plan sign-off", "Sign-off", "you") },
    { name: "Build", state: "upcoming" },
    { name: "Verify", state: "skipped" },
    { name: "Review", state: "upcoming", gate: gate("PR review", "PR review", "pending", "Tom") },
    { name: "Done", state: "upcoming" },
  ],
};

describe("glyphItems", () => {
  it("puts each gate right after its stage", () => {
    const kinds = glyphItems(parked.stages).map((it) =>
      it.type === "gate" ? `◆${it.gate.short}` : it.stage.name,
    );
    expect(kinds).toEqual([
      "Inbox",
      "Plan",
      "◆Sign-off",
      "Build",
      "Verify",
      "Review",
      "◆PR review",
      "Done",
    ]);
  });
});

describe("glyphTitle", () => {
  it("spells the full path with gates and skipped stages", () => {
    expect(glyphTitle(parked)).toBe(
      "Inbox → Plan → ◆ Plan sign-off → Build → Verify (skipped) → Review → ◆ PR review → Done",
    );
  });

  it("marks failed stages too", () => {
    expect(
      glyphTitle({
        stages: [
          { name: "Plan", state: "done" },
          { name: "Verify", state: "failed" },
        ],
      }),
    ).toBe("Plan → Verify (blocked)");
  });
});

describe("parkedGate", () => {
  it("finds the gate waiting on you or someone", () => {
    expect(parkedGate(parked)?.short).toBe("Sign-off");
    expect(parkedGate({ stages: [{ name: "Build", state: "current" }] })).toBeUndefined();
  });
});

describe("gate collapsing", () => {
  const twoGates = parked;
  const threeGates: Pick<GlyphModel, "stages"> = {
    stages: [
      { name: "Plan", state: "done", gate: gate("Plan sign-off", "Sign-off", "passed") },
      { name: "Build", state: "done", gate: gate("Build check", "Check", "waiting", "Sam") },
      { name: "Review", state: "upcoming", gate: gate("PR review", "PR review", "pending") },
    ],
  };

  it("collapses with more than 2 gates or when compact", () => {
    expect(collapsesGates(twoGates, false)).toBe(false);
    expect(collapsesGates(twoGates, true)).toBe(true);
    expect(collapsesGates(threeGates, false)).toBe(true);
  });

  it("keeps the active gate named; inactive ones go bare", () => {
    const [passed, waiting, pending] = threeGates.stages.map((st) => st.gate);
    expect(passed && isBareGate(passed, true)).toBe(true);
    expect(waiting && isBareGate(waiting, true)).toBe(false);
    expect(pending && isBareGate(pending, true)).toBe(true);
    expect(pending && isBareGate(pending, false)).toBe(false);
  });

  it("titles a bare gate with who or 'passed'", () => {
    expect(gateTitle(gate("Plan sign-off", "Sign-off", "passed"), true)).toBe(
      "Plan sign-off · passed",
    );
    expect(gateTitle(gate("PR review", "PR review", "pending", "Tom"), true)).toBe(
      "PR review · Tom",
    );
    expect(gateTitle(gate("PR review", "PR review", "pending"), true)).toBe("PR review");
    expect(gateTitle(gate("PR review", "PR review", "pending", "Tom"), false)).toBe("PR review");
  });
});

describe("stageCaption", () => {
  it("captions current, skipped, added and failed stages", () => {
    expect(stageCaption({ name: "Build", state: "current" }, false, false)).toBe("Now");
    expect(stageCaption({ name: "Build", state: "current" }, false, true)).toBe("Live");
    expect(stageCaption({ name: "Done", state: "current" }, true, false)).toBe("");
    expect(stageCaption({ name: "Verify", state: "skipped" }, false, false)).toBe("Skipped");
    expect(stageCaption({ name: "Backfill", state: "upcoming", added: true }, false, false)).toBe(
      "Added",
    );
    expect(stageCaption({ name: "Build", state: "failed" }, false, true)).toBe("Blocked");
    expect(stageCaption({ name: "Plan", state: "done" }, false, false)).toBe("");
  });
});
