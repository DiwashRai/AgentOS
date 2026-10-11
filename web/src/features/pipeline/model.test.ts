import { describe, expect, it } from "vitest";

import { ApiError } from "../../api/client";
import {
  describeSaveError,
  draftFrom,
  formatTimeout,
  freshId,
  idEditable,
  issueCount,
  MAX_STAGES,
  newStage,
  optional,
  previewModel,
  reduce,
  runnableStages,
  samePipeline,
  toPipeline,
  validatePipeline,
  type Draft,
  type Pipeline,
  type Stage,
} from "./model";

function stage(id: string, over: Partial<Stage> = {}): Stage {
  return {
    ...newStage("agent", []),
    id,
    name: id.charAt(0).toUpperCase() + id.slice(1),
    ...over,
  };
}

const SEED: Pipeline = {
  library_sets: [],
  stages: [
    stage("plan", { phase: "plan", gate_after: true }),
    stage("implement"),
    stage("lint", { kind: "shell", phase: "verify", enabled: false }),
    stage("review", { phase: "review" }),
    stage("open-pr", { phase: "review", requires_repo: true }),
  ],
};

const ids = (d: Draft) => d.rows.map((r) => r.stage.id);
const uidOf = (d: Draft, id: string) => {
  const row = d.rows.find((r) => r.stage.id === id);
  if (!row) throw new Error(`no row ${id}`);
  return row.uid;
};

describe("draftFrom / toPipeline", () => {
  it("round-trips a pipeline, every stage saved", () => {
    const d = draftFrom(SEED);
    expect(toPipeline(d)).toEqual(SEED);
    expect(d.rows.every((r) => !r.fresh)).toBe(true);
    expect(new Set(d.rows.map((r) => r.uid)).size).toBe(5);
  });

  it("keeps uids by id across a reload and marks every stage saved", () => {
    let d = draftFrom(SEED);
    d = reduce(d, { type: "add", kind: "agent" });
    const added = d.rows.at(-1);
    const planUid = uidOf(d, "plan");
    const after = draftFrom(toPipeline(d), d);
    expect(uidOf(after, "plan")).toBe(planUid);
    expect(after.rows.at(-1)?.uid).toBe(added?.uid);
    expect(after.rows.every((r) => !r.fresh)).toBe(true);
  });

  it("carries library_sets through", () => {
    const d = draftFrom({ ...SEED, library_sets: [3] });
    expect(toPipeline(reduce(d, { type: "add", kind: "shell" })).library_sets).toEqual([3]);
  });
});

describe("reduce: move", () => {
  it("moves up and down", () => {
    const d = draftFrom(SEED);
    const up = reduce(d, { type: "move", uid: uidOf(d, "review"), delta: -1 });
    expect(ids(up)).toEqual(["plan", "implement", "review", "lint", "open-pr"]);
    const down = reduce(d, { type: "move", uid: uidOf(d, "plan"), delta: 1 });
    expect(ids(down)).toEqual(["implement", "plan", "lint", "review", "open-pr"]);
  });

  it("is a no-op past either end", () => {
    const d = draftFrom(SEED);
    expect(reduce(d, { type: "move", uid: uidOf(d, "plan"), delta: -1 })).toBe(d);
    expect(reduce(d, { type: "move", uid: uidOf(d, "open-pr"), delta: 1 })).toBe(d);
    expect(reduce(d, { type: "move", uid: 999, delta: 1 })).toBe(d);
  });
});

describe("reduce: add / remove", () => {
  it("adds a fresh stage with a unique id at the end", () => {
    let d = draftFrom(SEED);
    d = reduce(d, { type: "add", kind: "agent" });
    d = reduce(d, { type: "add", kind: "agent" });
    d = reduce(d, { type: "add", kind: "shell" });
    expect(ids(d).slice(5)).toEqual(["stage", "stage-2", "shell"]);
    expect(d.rows.slice(5).every((r) => r.fresh)).toBe(true);
    const shell = d.rows.at(-1)?.stage;
    expect(shell?.kind).toBe("shell");
    expect(shell?.enabled).toBe(true);
  });

  it("stops at the stage limit", () => {
    let d = draftFrom(SEED);
    for (let i = 0; i < 30; i++) d = reduce(d, { type: "add", kind: "agent" });
    expect(d.rows).toHaveLength(MAX_STAGES);
  });

  it("removes a stage but never the last one", () => {
    let d = draftFrom(SEED);
    d = reduce(d, { type: "remove", uid: uidOf(d, "lint") });
    expect(ids(d)).toEqual(["plan", "implement", "review", "open-pr"]);
    for (const id of ["plan", "implement", "review"]) {
      d = reduce(d, { type: "remove", uid: uidOf(d, id) });
    }
    expect(ids(d)).toEqual(["open-pr"]);
    expect(reduce(d, { type: "remove", uid: uidOf(d, "open-pr") })).toBe(d);
  });

  it("freshId skips taken ids", () => {
    expect(freshId("agent", [])).toBe("stage");
    expect(freshId("agent", ["stage", "stage-2"])).toBe("stage-3");
    expect(freshId("shell", ["stage"])).toBe("shell");
  });
});

describe("reduce: patch and the id rule", () => {
  it("patches fields", () => {
    const d = draftFrom(SEED);
    const p = reduce(d, {
      type: "patch",
      uid: uidOf(d, "lint"),
      patch: { enabled: true, command: "npm test" },
    });
    expect(p.rows[2]?.stage).toMatchObject({ id: "lint", enabled: true, command: "npm test" });
    expect(d.rows[2]?.stage.enabled).toBe(false); // immutable
  });

  it("only lets an unsaved stage change its id", () => {
    let d = draftFrom(SEED);
    const plan = uidOf(d, "plan");
    expect(reduce(d, { type: "setId", uid: plan, id: "planning" })).toBe(d);
    expect(d.rows.some(idEditable)).toBe(false);

    d = reduce(d, { type: "add", kind: "agent" });
    const added = d.rows.at(-1);
    expect(added && idEditable(added)).toBe(true);
    d = reduce(d, { type: "setId", uid: added?.uid ?? 0, id: "security" });
    expect(ids(d).at(-1)).toBe("security");

    // Once saved (the server's copy comes back), the id is fixed.
    const saved = draftFrom(toPipeline(d), d);
    const row = saved.rows.at(-1);
    expect(row && idEditable(row)).toBe(false);
    expect(reduce(saved, { type: "setId", uid: row?.uid ?? 0, id: "other" })).toBe(saved);
  });
});

describe("validatePipeline", () => {
  const RT = ["claude", "copilot"];

  it("accepts the seed", () => {
    const issues = validatePipeline(SEED.stages, RT);
    expect(issueCount(issues)).toBe(0);
    expect(issues.stages).toHaveLength(5);
  });

  it("checks the id pattern and uniqueness", () => {
    const bad = ["Plan", "1st", "-x", "a_b", "x".repeat(33), ""];
    for (const id of bad) {
      expect(validatePipeline([stage(id)], RT).stages[0]?.id, id).toBeDefined();
    }
    expect(validatePipeline([stage("a"), stage("a-1"), stage("x".repeat(32))], RT)).toEqual({
      form: [],
      stages: [{}, {}, {}],
    });
    const dup = validatePipeline([stage("plan"), stage("build"), stage("plan")], RT);
    expect(dup.stages[0]?.id).toMatch(/already/);
    expect(dup.stages[1]?.id).toBeUndefined();
    expect(dup.stages[2]?.id).toMatch(/already/);
  });

  it("checks the name length", () => {
    expect(validatePipeline([stage("a", { name: "  " })], RT).stages[0]?.name).toBeDefined();
    expect(
      validatePipeline([stage("a", { name: "x".repeat(61) })], RT).stages[0]?.name,
    ).toBeDefined();
    expect(
      validatePipeline([stage("a", { name: "x".repeat(60) })], RT).stages[0]?.name,
    ).toBeUndefined();
  });

  it("checks the stage count", () => {
    expect(validatePipeline([], RT).form).toHaveLength(1);
    const many = Array.from({ length: 21 }, (_, i) => stage(`s${String(i)}`));
    expect(validatePipeline(many, RT).form).toHaveLength(1);
    expect(validatePipeline(many.slice(0, 20), RT).form).toHaveLength(0);
  });

  it("checks the timeout range", () => {
    for (const t of [59, 21601, 0, Number.NaN, 90.5]) {
      expect(
        validatePipeline([stage("a", { timeout_s: t })], RT).stages[0]?.timeout_s,
      ).toBeDefined();
    }
    for (const t of [60, 21600]) {
      expect(
        validatePipeline([stage("a", { timeout_s: t })], RT).stages[0]?.timeout_s,
      ).toBeUndefined();
    }
  });

  it("needs a command on an enabled shell stage only", () => {
    const shell = stage("lint", { kind: "shell", command: " " });
    expect(validatePipeline([shell], RT).stages[0]?.command).toBeDefined();
    expect(validatePipeline([{ ...shell, enabled: false }], RT).stages[0]?.command).toBeUndefined();
    expect(
      validatePipeline([{ ...shell, command: "make" }], RT).stages[0]?.command,
    ).toBeUndefined();
  });

  it("checks agent, model, runtime and prompt size", () => {
    const ok = stage("a", { agent: "planner", model: "claude-opus-4.1", runtime: "copilot" });
    expect(validatePipeline([ok], RT).stages[0]).toEqual({});
    const bad = stage("a", {
      agent: "has space",
      model: "x".repeat(101),
      runtime: "gemini",
      prompt: "x".repeat(65537),
    });
    expect(Object.keys(validatePipeline([bad], RT).stages[0] ?? {}).sort()).toEqual([
      "agent",
      "model",
      "prompt",
      "runtime",
    ]);
  });
});

describe("previewModel", () => {
  it("draws Inbox, each stage, Done, and a sign-off after enabled gated stages", () => {
    const m = previewModel({
      stages: [
        stage("plan", { gate_after: true }),
        stage("build"),
        stage("lint", { enabled: false, gate_after: true }),
        stage("review", { name: "  ", gate_after: true }),
      ],
    });
    expect(m.live).toBe(false);
    expect(m.stages.map((s) => [s.name, s.state])).toEqual([
      ["Inbox", "upcoming"],
      ["Plan", "upcoming"],
      ["Build", "upcoming"],
      ["Lint", "skipped"],
      ["review", "upcoming"],
      ["Done", "upcoming"],
    ]);
    expect(m.stages[1]?.gate).toEqual({
      label: "Plan sign-off",
      short: "Sign-off",
      state: "pending",
    });
    expect(m.stages[2]?.gate).toBeUndefined();
    expect(m.stages[3]?.gate).toBeUndefined(); // disabled: its gate disappears
    expect(m.stages[4]?.gate?.label).toBe("review sign-off");
  });
});

describe("helpers", () => {
  it("runnableStages keeps enabled stages in order", () => {
    expect(runnableStages(SEED).map((s) => s.id)).toEqual([
      "plan",
      "implement",
      "review",
      "open-pr",
    ]);
  });

  it("samePipeline treats missing optionals as null", () => {
    const a: Pipeline = { stages: [stage("a")] };
    const bare: Stage = stage("a");
    delete bare.agent;
    delete bare.model;
    delete bare.runtime;
    expect(samePipeline(a, { stages: [bare] })).toBe(true);
    expect(samePipeline(a, { stages: [stage("a", { enabled: false })] })).toBe(false);
  });

  it("optional blanks to null", () => {
    expect(optional("  ")).toBeNull();
    expect(optional(" opus ")).toBe("opus");
  });

  it("formatTimeout", () => {
    expect(formatTimeout(3600)).toBe("1 h");
    expect(formatTimeout(5400)).toBe("1 h 30 min");
    expect(formatTimeout(900)).toBe("15 min");
    expect(formatTimeout(90)).toBe("90 s");
    expect(formatTimeout(Number.NaN)).toBe("");
  });

  it("describeSaveError maps the 409s", () => {
    expect(describeSaveError(new ApiError(409, "stale_version", "x"))).toEqual({
      text: "Changed by someone else. Reload (your draft will be lost).",
      reload: true,
    });
    expect(describeSaveError(new ApiError(409, "run_active", "x")).text).toMatch(/run is active/);
    expect(
      describeSaveError(new ApiError(422, "invalid_pipeline", "stage lint: needs a command")),
    ).toEqual({ text: "stage lint: needs a command", reload: false });
  });
});
