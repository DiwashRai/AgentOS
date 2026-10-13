import { describe, expect, it } from "vitest";

import { ApiError } from "../../api/client";
import { newStage, type Pipeline, type Stage } from "../pipeline/model";
import {
  createBody,
  deleteBlock,
  makeDefaultErrorText,
  newWorkflowForm,
  NEW,
  parseSelection,
  sameWorkflowForm,
  sortWorkflows,
  stageCount,
  switchBlock,
  switchConfirmText,
  switchErrorText,
  updateBody,
  validateWorkflowForm,
  workflowErrorText,
  workflowForm,
  workflowHint,
  workflowHref,
  workflowIdOf,
  workflowLine,
  workflowMeta,
  workflowOptions,
  type Workflow,
} from "./model";

function pipeline(n: number): Pipeline {
  const stages: Stage[] = [];
  for (let i = 0; i < n; i++) {
    stages.push(
      newStage(
        "agent",
        stages.map((s) => s.id),
      ),
    );
  }
  return { stages, library_sets: [] };
}

function wf(id: number, name: string, extra: Partial<Workflow> = {}): Workflow {
  return {
    id,
    name,
    description: "",
    pipeline: pipeline(2),
    version: 1,
    updated_at: "2026-10-13T10:00:00Z",
    updated_by: null,
    default: false,
    ...extra,
  };
}

const LIST = [
  wf(1, "helper script"),
  wf(2, "Standard dev", { default: true, pipeline: pipeline(4), description: "Plan, build" }),
  wf(3, "Research only", { version: 3 }),
];

describe("list", () => {
  it("puts the default first, then sorts by name ignoring case", () => {
    expect(sortWorkflows(LIST).map((w) => w.name)).toEqual([
      "Standard dev",
      "helper script",
      "Research only",
    ]);
  });

  it("labels stages and versions", () => {
    expect(stageCount(pipeline(1))).toBe("1 stage");
    expect(workflowMeta(LIST[2] ?? wf(0, "x"))).toBe("2 stages · v3");
  });

  it("offers the default first, marked, with its description as the hint", () => {
    expect(workflowOptions(LIST)).toEqual([
      { value: "2", label: "Standard dev (default)" },
      { value: "1", label: "helper script" },
      { value: "3", label: "Research only" },
    ]);
    expect(workflowHint(LIST, "2")).toBe("Plan, build");
    expect(workflowHint(LIST, "1")).toBeUndefined();
    expect(workflowIdOf("3")).toBe(3);
    expect(workflowIdOf("")).toBeNull();
  });

  it("reads the selection from the URL", () => {
    expect(parseSelection("12")).toBe(12);
    expect(parseSelection(NEW)).toBe(NEW);
    expect(parseSelection("abc")).toBeNull();
    expect(parseSelection(null)).toBeNull();
    expect(workflowHref(7)).toBe("/settings/workflows?w=7");
  });
});

describe("form", () => {
  it("round-trips a workflow through the form to trimmed bodies", () => {
    const w = wf(5, "Careful", { description: "Verify twice" });
    const f = workflowForm(w);
    expect(sameWorkflowForm(f, workflowForm(w))).toBe(true);
    const edited = { ...f, name: "  Careful v2 ", description: " Twice " };
    expect(sameWorkflowForm(edited, f)).toBe(false);
    expect(createBody(edited)).toEqual({
      name: "Careful v2",
      description: "Twice",
      pipeline: w.pipeline,
    });
    expect(updateBody(edited, 4)).toEqual({ ...createBody(edited), base_version: 4 });
  });

  it("starts a new workflow from the default's pipeline", () => {
    const f = newWorkflowForm(LIST);
    expect(f?.name).toBe("");
    expect(f?.pipeline.stages).toHaveLength(4);
    expect(newWorkflowForm([])).toBeUndefined();
  });

  it("checks the name and description", () => {
    expect(validateWorkflowForm({ name: " ", description: "" }, LIST, null).name).toBe(
      "Give the workflow a name.",
    );
    expect(validateWorkflowForm({ name: "x".repeat(61), description: "" }, LIST, null).name).toBe(
      "At most 60 characters.",
    );
    expect(validateWorkflowForm({ name: "STANDARD DEV", description: "" }, LIST, null).name).toBe(
      "Another workflow already has this name.",
    );
    // Its own name is not a clash.
    expect(validateWorkflowForm({ name: "standard dev", description: "" }, LIST, 2)).toEqual({});
    expect(
      validateWorkflowForm({ name: "New", description: "d".repeat(501) }, LIST, null).description,
    ).toBe("At most 500 characters.");
  });

  it("blocks deleting the default", () => {
    expect(deleteBlock({ default: true })).toMatch(/can't be deleted/);
    expect(deleteBlock({ default: false })).toBeUndefined();
  });
});

describe("errors", () => {
  it("words each code", () => {
    expect(
      workflowErrorText(
        new ApiError(422, "validation_error", "name: a workflow named 'X' exists already"),
      ),
    ).toEqual({ text: "A workflow named 'X' exists already", reload: false });
    expect(workflowErrorText(new ApiError(409, "stale_version", "x")).reload).toBe(true);
    expect(workflowErrorText(new ApiError(409, "in_use", "x")).text).toMatch(/default workflow/);
    expect(workflowErrorText(new ApiError(422, "invalid_pipeline", "bad id")).text).toBe(
      "The pipeline isn't valid · bad id",
    );
    expect(workflowErrorText(new ApiError(422, "render_error", "plan: {x")).text).toBe(
      "A stage prompt doesn't render · plan: {x",
    );
    expect(workflowErrorText(new Error("boom"))).toEqual({ text: "boom", reload: false });
    expect(makeDefaultErrorText(new ApiError(409, "stale_version", "x"))).toMatch(/Space settings/);
    expect(
      makeDefaultErrorText(new ApiError(422, "validation_error", "default_workflow_id: no")),
    ).toBe("No");
  });

  it("words a failed switch", () => {
    expect(switchErrorText(new ApiError(409, "run_active", "x"), "Careful")).toMatch(
      /run is active/,
    );
    expect(switchErrorText(new ApiError(409, "stale_version", "x"), "Careful")).toMatch(/Reload/);
    expect(switchErrorText(new ApiError(404, "not_found", "x"), "Careful")).toBe(
      "Careful no longer exists.",
    );
    expect(switchErrorText(new ApiError(422, "render_error", "msg"), "Careful")).toBe(
      "Careful can't be applied to this ticket · msg",
    );
  });
});

describe("pipeline tab", () => {
  it("says where the pipeline came from", () => {
    expect(workflowLine(null)).toBeNull();
    expect(workflowLine({ id: 2, name: "Standard dev", version: 3 })).toBe(
      "From Standard dev · v3",
    );
    expect(workflowLine({ id: null, name: "Old", version: 1 })).toBe("From Old · v1 (deleted)");
  });

  it("blocks a switch during a run or with unsaved edits", () => {
    expect(switchBlock({ active: true, dirty: true })).toBe("A run is active");
    expect(switchBlock({ active: false, dirty: true })).toBe("Save or discard your changes first");
    expect(switchBlock({ active: false, dirty: false })).toBeUndefined();
  });

  it("warns about the branch and history once the ticket has run", () => {
    const fresh = switchConfirmText("Careful", false);
    expect(fresh).toMatch(/becomes a copy of Careful/);
    expect(fresh).not.toMatch(/branch/);
    const ran = switchConfirmText("Careful", true);
    expect(ran).toMatch(/branch carries on/);
    expect(ran).toMatch(/only where their ids match/);
  });
});
