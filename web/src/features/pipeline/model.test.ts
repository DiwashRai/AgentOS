import { describe, expect, it } from "vitest";

import { ApiError } from "../../api/client";
import {
  blockRoles,
  CLI_DEFAULT,
  configDefaults,
  defaultsBody,
  describeSaveError,
  draftFrom,
  drawnBlocks,
  formatTimeout,
  freshId,
  idEditable,
  isCheck,
  issueCount,
  loopLabel,
  loopRole,
  loopStageOptions,
  MAX_STAGES,
  newStage,
  optional,
  pipelineIssues,
  previewModel,
  reduce,
  agentHint,
  resumeChoice,
  resumeFromChoice,
  resumeOptions,
  resumes,
  runnableStages,
  runtimeOptions,
  sameDefaults,
  samePipeline,
  segments,
  serverRuntimeLabel,
  stageEffortPlaceholder,
  stageModelPlaceholder,
  stageRuntimeOptions,
  suggestLoop,
  toPipeline,
  unsetIfEmpty,
  validateDefaults,
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
      loops: [],
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

  it("checks agent, model, effort, runtime and prompt size", () => {
    const ok = stage("a", {
      agent: "planner",
      model: "claude-opus-4.1",
      effort: "high",
      runtime: "copilot",
    });
    expect(validatePipeline([ok], RT).stages[0]).toEqual({});
    const bad = stage("a", {
      agent: "has space",
      model: "x".repeat(101),
      effort: "very high",
      runtime: "gemini",
      prompt: "x".repeat(65537),
    });
    expect(Object.keys(validatePipeline([bad], RT).stages[0] ?? {}).sort()).toEqual([
      "agent",
      "effort",
      "model",
      "prompt",
      "runtime",
    ]);
  });

  it("checks the effort pattern: a free token of letters, digits and . _ -, at most 32", () => {
    const effortIssue = (effort: string | null) =>
      validatePipeline([stage("a", { effort })], RT).stages[0]?.effort;
    for (const ok of [null, "low", "xhigh", "Max", "v1.2_x-y", "x".repeat(32)]) {
      expect(effortIssue(ok), String(ok)).toBeUndefined();
    }
    for (const bad of ["", "very high", "a:b", "a/b", "x".repeat(33)]) {
      expect(effortIssue(bad), bad).toBe("Letters, digits and . _ - only; at most 32.");
    }
  });

  it("ignores effort on a shell stage, like model", () => {
    const shell = stage("lint", { kind: "shell", command: "make", effort: "not valid" });
    expect(validatePipeline([shell], RT).stages[0]).toEqual({});
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
    delete bare.effort;
    expect(samePipeline(a, { stages: [bare] })).toBe(true);
    expect(samePipeline(a, { stages: [stage("a", { enabled: false })] })).toBe(false);
    expect(samePipeline(a, { stages: [stage("a", { effort: "high" })] })).toBe(false);
  });

  it("a new agent stage has no effort (the Space default)", () => {
    expect(newStage("agent", []).effort).toBeNull();
  });

  it("optional and unsetIfEmpty blank to null", () => {
    expect(unsetIfEmpty("")).toBeNull();
    expect(unsetIfEmpty("claude")).toBe("claude");
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

describe("Space defaults", () => {
  const RT = ["claude", "copilot"];
  const space = { runtime: "claude", model: "claude-sonnet-4", effort: "high" };

  it("stage runtime: the unset option names the Space runtime", () => {
    expect(stageRuntimeOptions(RT, space, null)).toEqual([
      { value: "", label: "Space default (claude)" },
      { value: "claude", label: "claude" },
      { value: "copilot", label: "copilot" },
    ]);
    expect(
      stageRuntimeOptions(RT, { runtime: null, model: null, effort: null }, null)[0]?.label,
    ).toBe("Space default");
    // An unknown current runtime stays selectable (and fails validation).
    expect(stageRuntimeOptions(RT, space, "gemini").map((o) => o.value)).toEqual([
      "",
      "claude",
      "copilot",
      "gemini",
    ]);
    expect(runtimeOptions(RT, "Server default (claude)", undefined)[0]).toEqual({
      value: "",
      label: "Server default (claude)",
    });
  });

  it("agent hint: says which file the named agent needs and where it can come from", () => {
    expect(agentHint(null)).toBe("Optional. Claude Code reads it from .claude/agents/<name>.md.");
    expect(agentHint(" reviewer ")).toBe(
      "Needs .claude/agents/reviewer.md: in the repo, or added on the ticket's Context tab.",
    );
  });

  it("stage model: the Space model only on the Space runtime", () => {
    expect(stageModelPlaceholder(null, space)).toBe("Space default (claude-sonnet-4)");
    expect(stageModelPlaceholder(undefined, space)).toBe("Space default (claude-sonnet-4)");
    expect(stageModelPlaceholder("claude", space)).toBe("Space default (claude-sonnet-4)");
    expect(stageModelPlaceholder("copilot", space)).toBe(CLI_DEFAULT);
    expect(stageModelPlaceholder(null, { ...space, model: null })).toBe(CLI_DEFAULT);
    expect(CLI_DEFAULT).toBe("The CLI's default");
  });

  it("stage effort: the Space effort only on the Space runtime, else the CLI's default", () => {
    expect(stageEffortPlaceholder(null, space)).toBe("Space default (high)");
    expect(stageEffortPlaceholder(undefined, space)).toBe("Space default (high)");
    expect(stageEffortPlaceholder("claude", space)).toBe("Space default (high)");
    expect(stageEffortPlaceholder("copilot", space)).toBe(CLI_DEFAULT);
    expect(stageEffortPlaceholder(null, { ...space, effort: null })).toBe(CLI_DEFAULT);
    // Independent of the model: a Space effort with no Space model, and the other way round.
    expect(stageEffortPlaceholder(null, { ...space, model: null })).toBe("Space default (high)");
    expect(stageModelPlaceholder(null, { ...space, effort: null })).toBe(
      "Space default (claude-sonnet-4)",
    );
  });

  it("configDefaults reads /api/config's effective defaults", () => {
    expect(
      configDefaults({ default_runtime: "claude", default_model: null, default_effort: null }),
    ).toEqual({ runtime: "claude", model: null, effort: null });
    expect(
      configDefaults({
        default_runtime: "copilot",
        default_model: "gpt-5",
        default_effort: "xhigh",
      }),
    ).toEqual({ runtime: "copilot", model: "gpt-5", effort: "xhigh" });
  });

  it("serverRuntimeLabel names the server's own runtime", () => {
    expect(serverRuntimeLabel({ server_default_runtime: "claude" })).toBe(
      "Server default (claude)",
    );
  });

  it("validateDefaults mirrors TemplateDefaults", () => {
    expect(validateDefaults({}, RT)).toEqual({});
    expect(
      validateDefaults({ runtime: "copilot", model: "claude-opus-4.1", effort: "medium" }, RT),
    ).toEqual({});
    expect(
      Object.keys(
        validateDefaults({ runtime: "gemini", model: "has space", effort: "x".repeat(33) }, RT),
      ),
    ).toEqual(["runtime", "model", "effort"]);
    expect(validateDefaults({ effort: "a b" }, RT).effort).toBe(
      "Letters, digits and . _ - only; at most 32.",
    );
  });

  it("sameDefaults treats missing as null and compares the effort", () => {
    expect(sameDefaults({}, { runtime: null, model: null, effort: null })).toBe(true);
    expect(sameDefaults({ model: "x" }, { model: null })).toBe(false);
    expect(sameDefaults({ effort: "high" }, {})).toBe(false);
    expect(sameDefaults({ effort: "high" }, { effort: "high", runtime: null })).toBe(true);
  });
});

// ------------------------------------------------------------------ loops

/** implement → lint (shell) → review, inside plan … open-pr. */
const LOOPY: Pipeline = {
  stages: [
    stage("plan", { gate_after: true }),
    stage("implement"),
    stage("lint", { kind: "shell", command: "make lint" }),
    stage("review", { check: true }),
    stage("open-pr"),
  ],
  loops: [{ start: "implement", end: "review", max_rounds: 3 }],
};

const loopsOf = (d: Draft) => d.loops.map((l) => `${l.start}..${l.end}`);
const loopIssues = (p: Pipeline) => validatePipeline(p.stages, ["claude"], p.loops ?? []);

describe("loop roles", () => {
  it("a shell stage is always a check; an agent stage only with check", () => {
    expect(isCheck(stage("lint", { kind: "shell" }))).toBe(true);
    expect(isCheck(stage("lint", { kind: "shell", check: false }))).toBe(true);
    expect(isCheck(stage("review", { check: true }))).toBe(true);
    expect(loopRole(stage("implement"))).toBe("maker");
    expect(loopRole(stage("review", { check: true }))).toBe("check");
  });

  it("resume is the stage's choice, else by role: makers resume, checks start fresh", () => {
    expect(resumes(stage("implement"))).toBe(true);
    expect(resumes(stage("review", { check: true }))).toBe(false);
    expect(resumes(stage("implement", { resume: false }))).toBe(false);
    expect(resumes(stage("review", { check: true, resume: true }))).toBe(true);
    expect(resumeOptions(stage("implement"))[0]?.label).toContain("resume");
    expect(resumeOptions(stage("review", { check: true }))[0]?.label).toContain("start fresh");
  });

  it("the resume select maps to null / true / false and back", () => {
    for (const v of [null, true, false]) expect(resumeFromChoice(resumeChoice(v))).toBe(v);
    expect(resumeChoice(undefined)).toBe("");
  });

  it("blockRoles splits a block's stages, disabled ones included", () => {
    const p = { ...LOOPY, stages: LOOPY.stages.map((s) => ({ ...s, enabled: false })) };
    expect(blockRoles(p.stages, { start: "implement", end: "review", max_rounds: 3 })).toEqual({
      makers: ["Implement"],
      checks: ["Lint", "Review"],
    });
    expect(blockRoles(p.stages, { start: "review", end: "implement", max_rounds: 3 })).toBeNull();
    expect(blockRoles(p.stages, { start: "nope", end: "review", max_rounds: 3 })).toBeNull();
  });

  it("new stages are plain makers with the default repairs and resume by role", () => {
    expect(newStage("agent", [])).toMatchObject({ check: false, verdict_repairs: 2, resume: null });
  });
});

describe("loops: draft", () => {
  it("round-trips loops, and leaves the key out when there were none", () => {
    expect(toPipeline(draftFrom(LOOPY))).toEqual(LOOPY);
    expect("loops" in toPipeline(draftFrom(SEED))).toBe(false);
    const added = reduce(draftFrom(SEED), { type: "addLoop" });
    expect(toPipeline(added).loops).toHaveLength(1);
  });

  it("samePipeline compares loops (missing = none) and the loop stage fields", () => {
    expect(samePipeline(SEED, { ...SEED, loops: [] })).toBe(true);
    expect(samePipeline(LOOPY, { ...LOOPY, loops: [] })).toBe(false);
    expect(
      samePipeline(LOOPY, {
        ...LOOPY,
        loops: [{ start: "implement", end: "review", max_rounds: 4 }],
      }),
    ).toBe(false);
    const withStage = (over: Partial<Stage>) => ({
      ...LOOPY,
      stages: LOOPY.stages.map((s) => (s.id === "review" ? { ...s, ...over } : s)),
    });
    expect(samePipeline(LOOPY, withStage({ check: false }))).toBe(false);
    expect(samePipeline(LOOPY, withStage({ verdict_repairs: 0 }))).toBe(false);
    expect(samePipeline(LOOPY, withStage({ resume: true }))).toBe(false);
    expect(samePipeline(LOOPY, withStage({ resume: null }))).toBe(true);
  });

  it("adds, patches and removes loops", () => {
    let d = draftFrom({ ...LOOPY, loops: [] });
    d = reduce(d, { type: "addLoop" });
    expect(d.loops).toEqual([{ start: "implement", end: "lint", max_rounds: 3 }]);
    d = reduce(d, { type: "patchLoop", index: 0, patch: { end: "review", max_rounds: 5 } });
    expect(d.loops).toEqual([{ start: "implement", end: "review", max_rounds: 5 }]);
    expect(reduce(d, { type: "patchLoop", index: 3, patch: { max_rounds: 1 } })).toBe(d);
    expect(reduce(d, { type: "removeLoop", index: 0 }).loops).toEqual([]);
    expect(reduce(d, { type: "removeLoop", index: 1 })).toBe(d);
  });

  it("a move keeps each block's positions: a stage crosses its edge, never drags it", () => {
    const d = draftFrom(LOOPY);
    // Within the block: still implement's slot .. review's slot.
    const inside = reduce(d, { type: "move", uid: uidOf(d, "lint"), delta: -1 });
    expect(ids(inside)).toEqual(["plan", "lint", "implement", "review", "open-pr"]);
    expect(loopsOf(inside)).toEqual(["lint..review"]);
    // The last stage moves out, and the one after it moves in.
    const out = reduce(d, { type: "move", uid: uidOf(d, "review"), delta: 1 });
    expect(loopsOf(out)).toEqual(["implement..open-pr"]);
    // A stage from before moves in at the top.
    const into = reduce(d, { type: "move", uid: uidOf(d, "plan"), delta: 1 });
    expect(loopsOf(into)).toEqual(["plan..review"]);
  });

  it("removing a block's edge stage shrinks it; removing its only stage drops it", () => {
    const d = draftFrom(LOOPY);
    expect(loopsOf(reduce(d, { type: "remove", uid: uidOf(d, "implement") }))).toEqual([
      "lint..review",
    ]);
    expect(loopsOf(reduce(d, { type: "remove", uid: uidOf(d, "review") }))).toEqual([
      "implement..lint",
    ]);
    expect(loopsOf(reduce(d, { type: "remove", uid: uidOf(d, "lint") }))).toEqual([
      "implement..review",
    ]);
    expect(loopsOf(reduce(d, { type: "remove", uid: uidOf(d, "plan") }))).toEqual([
      "implement..review",
    ]);
    const single = draftFrom({ ...LOOPY, loops: [{ start: "lint", end: "lint", max_rounds: 3 }] });
    expect(reduce(single, { type: "remove", uid: uidOf(single, "lint") }).loops).toEqual([]);
  });

  it("a new stage's loop follows its id while it is typed", () => {
    let d = draftFrom(LOOPY);
    d = reduce(d, { type: "add", kind: "agent" });
    const uid = d.rows.at(-1)?.uid ?? 0;
    d = reduce(d, { type: "patchLoop", index: 0, patch: { end: "stage" } });
    d = reduce(d, { type: "setId", uid, id: "fix" });
    expect(loopsOf(d)).toEqual(["implement..fix"]);
    // Typing an id another stage has, then away from it, leaves that stage's loop alone.
    d = reduce(d, { type: "patchLoop", index: 0, patch: { end: "review" } });
    d = reduce(d, { type: "setId", uid, id: "review" });
    d = reduce(d, { type: "setId", uid, id: "reviewx" });
    expect(loopsOf(d)).toEqual(["implement..review"]);
  });
});

describe("loops: suggestLoop", () => {
  it("starts at the first free, ungated maker and ends at the next enabled check", () => {
    expect(suggestLoop(LOOPY.stages, [])).toEqual({
      start: "implement",
      end: "lint",
      max_rounds: 3,
    });
  });

  it("with no enabled check, ends at the next enabled agent stage (the default pipeline)", () => {
    expect(suggestLoop(SEED.stages, [])).toMatchObject({ start: "implement", end: "review" });
  });

  it("skips stages already in a loop, and gives up when no two free stages are adjacent", () => {
    // Free: plan (gated), review (a check), open-pr. No free maker leads a run, so any free pair.
    expect(
      suggestLoop(LOOPY.stages, [{ start: "implement", end: "lint", max_rounds: 3 }]),
    ).toMatchObject({ start: "review", end: "open-pr" });
    expect(suggestLoop(LOOPY.stages, LOOPY.loops ?? [])).toBeNull();
    const two = [stage("a"), stage("b")];
    expect(suggestLoop(two, [{ start: "a", end: "b", max_rounds: 3 }])).toBeNull();
    expect(suggestLoop([stage("a")], [])).toBeNull();
  });
});

describe("loops: blocks to draw", () => {
  it("cuts the rows into single rows and blocks", () => {
    expect(segments(LOOPY.stages, LOOPY.loops ?? [])).toEqual([
      { kind: "row", index: 0 },
      { kind: "block", block: { loop: 0, from: 1, to: 3 }, indexes: [1, 2, 3] },
      { kind: "row", index: 4 },
    ]);
  });

  it("draws only blocks with known ids in order, and the first of overlapping ones", () => {
    const loops = [
      { start: "review", end: "open-pr", max_rounds: 3 },
      { start: "plan", end: "lint", max_rounds: 3 },
      { start: "lint", end: "review", max_rounds: 3 }, // overlaps the one above
      { start: "nope", end: "plan", max_rounds: 3 },
      { start: "lint", end: "implement", max_rounds: 3 }, // backwards
    ];
    expect(drawnBlocks(LOOPY.stages, loops)).toEqual([
      { loop: 1, from: 0, to: 2 },
      { loop: 0, from: 3, to: 4 },
    ]);
  });

  it("loopLabel says the cap", () => {
    expect(loopLabel(3)).toContain("3");
    expect(loopLabel(1)).not.toBe(loopLabel(2));
  });

  it("loopStageOptions keeps a missing stage selectable and lists a duplicate id once", () => {
    const stages = [stage("a"), stage("a"), stage("b")];
    expect(loopStageOptions(stages, "a").map((o) => o.value)).toEqual(["a", "b"]);
    expect(loopStageOptions(stages, "gone").map((o) => o.value)).toEqual(["a", "b", "gone"]);
  });
});

describe("loops: validation mirrors the server", () => {
  it("accepts a block with a maker and a check (a shell stage counts as one)", () => {
    expect(issueCount(loopIssues(LOOPY))).toBe(0);
    const shellOnly = { ...LOOPY, stages: LOOPY.stages.map((s) => ({ ...s, check: false })) };
    expect(issueCount(loopIssues(shellOnly))).toBe(0);
  });

  it("names unknown stages", () => {
    const issues = loopIssues({ ...LOOPY, loops: [{ start: "x", end: "y", max_rounds: 3 }] });
    expect(Object.keys(issues.loops[0] ?? {}).sort()).toEqual(["end", "start"]);
  });

  it("start after end", () => {
    const issues = loopIssues({
      ...LOOPY,
      loops: [{ start: "review", end: "implement", max_rounds: 3 }],
    });
    expect(Object.keys(issues.loops[0] ?? {})).toEqual(["end"]);
  });

  it("needs a check, and a maker", () => {
    const noCheck = loopIssues({
      ...LOOPY,
      loops: [{ start: "plan", end: "implement", max_rounds: 3 }],
    });
    const allChecks = loopIssues({
      ...LOOPY,
      loops: [{ start: "lint", end: "review", max_rounds: 3 }],
    });
    expect(noCheck.loops[0]?.block).toBeDefined();
    expect(allChecks.loops[0]?.block).toBeDefined();
    expect(noCheck.loops[0]?.block).not.toBe(allChecks.loops[0]?.block);
  });

  it("counts disabled stages, like a save", () => {
    const p = {
      ...LOOPY,
      stages: LOOPY.stages.map((s) =>
        s.id === "lint" || s.id === "review" ? { ...s, enabled: false } : s,
      ),
    };
    expect(issueCount(loopIssues(p))).toBe(0);
  });

  it("only the block's last stage may have a sign-off (flagged on the stage)", () => {
    const gated = (id: string) => ({
      ...LOOPY,
      stages: LOOPY.stages.map((s) => (s.id === id ? { ...s, gate_after: true } : s)),
    });
    const inside = loopIssues(gated("lint"));
    expect(inside.stages[2]?.gate_after).toBeDefined();
    expect(issueCount(inside)).toBe(1);
    expect(issueCount(loopIssues(gated("review")))).toBe(0);
    // plan's own gate is outside the block.
    expect(loopIssues(LOOPY).stages[0]?.gate_after).toBeUndefined();
  });

  it("blocks can't overlap (the later one is flagged)", () => {
    const issues = loopIssues({
      ...LOOPY,
      loops: [
        { start: "lint", end: "open-pr", max_rounds: 3 },
        { start: "implement", end: "review", max_rounds: 3 },
      ],
    });
    expect(issues.loops[0]?.block).toBeDefined();
    expect(issues.loops[1]).toEqual({});
  });

  it("max_rounds 1–5, verdict_repairs 0–3", () => {
    for (const [n, ok] of [
      [0, false],
      [1, true],
      [5, true],
      [6, false],
      [2.5, false],
    ] as const) {
      const issues = loopIssues({
        ...LOOPY,
        loops: [{ start: "implement", end: "review", max_rounds: n }],
      });
      expect(issues.loops[0]?.max_rounds === undefined, String(n)).toBe(ok);
    }
    for (const [n, ok] of [
      [-1, false],
      [0, true],
      [3, true],
      [4, false],
    ] as const) {
      const issues = validatePipeline([stage("a", { verdict_repairs: n })], []);
      expect(issues.stages[0]?.verdict_repairs === undefined, String(n)).toBe(ok);
    }
  });

  it("pipelineIssues reads the pipeline's own loops", () => {
    const bad = { ...LOOPY, loops: [{ start: "plan", end: "implement", max_rounds: 3 }] };
    expect(issueCount(pipelineIssues(bad, ["claude"]))).toBeGreaterThan(0);
    expect(issueCount(validatePipeline(bad.stages, ["claude"]))).toBe(0);
  });
});

describe("unset is null in PUT bodies", () => {
  it("defaultsBody always sends every key, unset as null", () => {
    expect(defaultsBody({})).toEqual({ runtime: null, model: null, effort: null });
    expect(defaultsBody({ runtime: "claude", model: null, effort: "high" })).toEqual({
      runtime: "claude",
      model: null,
      effort: "high",
    });
    expect(JSON.stringify(defaultsBody({ model: "opus" }))).toBe(
      '{"runtime":null,"model":"opus","effort":null}',
    );
  });

  it("a stage's cleared runtime/model/effort/agent are null in the pipeline, never empty", () => {
    let d = draftFrom({
      stages: [
        stage("a", { runtime: "copilot", model: "gpt-5", effort: "high", agent: "planner" }),
      ],
    });
    const uid = d.rows[0]?.uid ?? 0;
    // What the fields send when blanked: the select's "" and an empty text input.
    d = reduce(d, {
      type: "patch",
      uid,
      patch: {
        runtime: unsetIfEmpty(""),
        model: optional(""),
        effort: optional("  "),
        agent: optional("   "),
      },
    });
    const body = JSON.parse(JSON.stringify(toPipeline(d))) as Pipeline;
    expect(body.stages[0]).toMatchObject({ runtime: null, model: null, effort: null, agent: null });
  });

  it("a stage's effort round-trips through the draft", () => {
    const p: Pipeline = { stages: [stage("a", { effort: "xhigh" }), stage("b")] };
    const d = draftFrom(p);
    expect(toPipeline(d)).toEqual(p);
    const set = reduce(d, { type: "patch", uid: d.rows[1]?.uid ?? 0, patch: { effort: "low" } });
    expect(toPipeline(set).stages.map((st) => st.effort)).toEqual(["xhigh", "low"]);
  });
});
