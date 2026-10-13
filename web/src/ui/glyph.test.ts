import { describe, expect, it } from "vitest";
import type { GlyphGate, GlyphModel } from "../domain/stage";
import {
  collapsesGates,
  fitGlyphSize,
  gateTitle,
  GLYPH_SLOT,
  glyphItems,
  glyphTitle,
  glyphWidth,
  isAhead,
  isBareGate,
  isFinished,
  parkedGate,
  roundCaption,
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

  it("calls an abandoned ticket's stopped stage closed", () => {
    expect(
      glyphTitle({
        closed: true,
        stages: [
          { name: "Plan", state: "done" },
          { name: "Build", state: "failed" },
          { name: "Done", state: "upcoming" },
        ],
      }),
    ).toBe("Plan → Build (closed) → Done");
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

  it("captions an abandoned ticket's stopped stage Closed, not Blocked", () => {
    expect(stageCaption({ name: "Build", state: "failed" }, false, false, false, true)).toBe(
      "Closed",
    );
  });
});

describe("roundCaption", () => {
  const round = { n: 2, of: 3 };

  it("captions the round on the stage the ticket is at, current or stopped", () => {
    expect(roundCaption({ name: "Review", state: "current", round })).toMatch(/2.*3/);
    expect(roundCaption({ name: "Review", state: "failed", round })).toMatch(/2.*3/);
  });

  it("is empty on other stages and on stages without a round", () => {
    expect(roundCaption({ name: "Implement", state: "done", round })).toBe("");
    expect(roundCaption({ name: "Implement", state: "upcoming", round })).toBe("");
    expect(roundCaption({ name: "Build", state: "current" })).toBe("");
  });
});

describe("glyph geometry (refresh: 81px slot)", () => {
  const pipe = (n: number, current: number): Pick<GlyphModel, "stages"> => ({
    stages: Array.from({ length: n }, (_, i) => ({
      name: `S${String(i)}`,
      state: i < current ? "done" : i === current ? "current" : "upcoming",
      gate: i === 1 ? gate("Plan sign-off", "Sign-off", "passed") : undefined,
    })),
  });

  it("7 stages + 1 gate with a current stage fill the slot exactly at sm", () => {
    expect(glyphWidth(pipe(7, 2), "sm")).toBe(GLYPH_SLOT);
    expect(fitGlyphSize(pipe(7, 2))).toBe("sm");
  });

  it("longer pipelines drop to xs", () => {
    expect(glyphWidth(pipe(9, 2), "sm")).toBeGreaterThan(GLYPH_SLOT);
    expect(fitGlyphSize(pipe(9, 2))).toBe("xs");
    expect(glyphWidth(pipe(9, 2), "xs")).toBeLessThan(glyphWidth(pipe(9, 2), "sm"));
  });
});

describe("isFinished / isAhead", () => {
  it("is finished when the last stage is reached and nothing failed", () => {
    const done: Pick<GlyphModel, "stages"> = {
      stages: [
        { name: "Plan", state: "done" },
        { name: "Lint", state: "skipped" },
        { name: "Done", state: "current" },
      ],
    };
    expect(isFinished(done)).toBe(true);
    expect(isFinished(parked)).toBe(false);
    expect(
      isFinished({
        stages: [
          { name: "Plan", state: "failed" },
          { name: "Done", state: "current" },
        ],
      }),
    ).toBe(false);
    expect(isAhead(done, 1)).toBe(false);
    expect(isAhead(parked, 3)).toBe(true);
  });

  it("captions the finished last stage Complete", () => {
    expect(stageCaption({ name: "Done", state: "current" }, true, false, true)).toBe("Complete");
  });
});
