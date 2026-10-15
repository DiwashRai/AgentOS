import { describe, expect, it } from "vitest";

import { ApiError, type Schemas } from "../../api/client";
import { gate, person, run, step, ticket } from "../../domain/fixtures";
import { gate as parkedAt } from "../../domain/ticketView";
import {
  actionErrorText,
  chipName,
  gateMeta,
  ownerPatch,
  ownerValue,
  parseTab,
  runnerText,
  stripKind,
  thenStage,
  ticketPath,
} from "./model";

type Detail = Schemas["TicketDetail"];
type Stage = Schemas["Stage"];

function stage(id: string, over: Partial<Stage> = {}): Stage {
  return {
    id,
    name: id.charAt(0).toUpperCase() + id.slice(1),
    kind: "agent",
    phase: "build",
    enabled: true,
    gate_after: false,
    prompt: "",
    command: "",
    requires_repo: false,
    timeout_s: 3600,
    check: false,
    verdict_repairs: 2,
    ...over,
  };
}

function detail(over: Partial<Detail> = {}): Detail {
  return {
    ...ticket(),
    active_run_id: null,
    base_branch: null,
    base_branch_info: null,
    workflow: null,
    branch: null,
    created_at: "2026-10-11T10:00:00Z",
    description: "",
    next_stage: null,
    pipeline: { stages: [stage("plan"), stage("build"), stage("review")] },
    pipeline_version: 1,
    plan: null,
    repo_url: null,
    skill_name: "aos-1",
    workspace_mode: "repo",
    ...over,
  };
}

const parkedRail = (state: "you" | "waiting") => [
  step("Plan", "done", {
    gate: gate(state, state === "waiting" ? { person: person(2, "Sam Lee") } : {}),
  }),
  step("Build", "pending"),
];

describe("tabs and paths", () => {
  it("parses known tabs and defaults to overview", () => {
    expect(parseTab("runs")).toBe("runs");
    expect(parseTab("pipeline")).toBe("pipeline");
    expect(parseTab(undefined)).toBe("overview");
    expect(parseTab("activity")).toBe("overview");
  });

  it("builds ticket paths, keeping the query string", () => {
    expect(ticketPath("AOS-1", "overview")).toBe("/t/AOS-1");
    expect(ticketPath("AOS-1", "context", "?file=TASKS.md")).toBe("/t/AOS-1/context?file=TASKS.md");
  });
});

describe("strip kind", () => {
  it("is the run strip while running or queued", () => {
    expect(stripKind(detail({ attention: "running" }))).toBe("run");
    expect(stripKind(detail({ attention: "queued" }))).toBe("queued");
  });

  it("is the gate strip when a gate is parked on you or someone", () => {
    expect(stripKind(detail({ attention: "needs_you", rail: parkedRail("you") }))).toBe("gate");
    expect(stripKind(detail({ attention: "waiting", rail: parkedRail("waiting") }))).toBe("gate");
  });

  it("is the status strip otherwise (incl. a passed gate)", () => {
    expect(stripKind(detail({ attention: "inbox" }))).toBe("status");
    const passed = [step("Plan", "done", { gate: gate("passed") }), step("Build", "failed")];
    expect(stripKind(detail({ attention: "blocked", rail: passed }))).toBe("status");
    expect(stripKind(detail({ attention: "needs_you" }))).toBe("status");
  });
});

describe("gate strip text", () => {
  it("says which run passed before the gate", () => {
    const t = detail({ latest_run: run("gated", { id: 12 }) });
    const parked = parkedAt(detail({ rail: parkedRail("you") }));
    expect(parked && gateMeta(t, parked)).toBe("Plan · run #12 passed");
  });
});

describe("THEN line", () => {
  it("names next_stage only while parked", () => {
    const parked = detail({ rail: parkedRail("you"), next_stage: "build" });
    expect(thenStage(parked)).toBe("Build");
    expect(thenStage(detail({ next_stage: "build" }))).toBeNull();
    expect(thenStage(detail({ rail: parkedRail("you"), next_stage: null }))).toBeNull();
  });

  it("falls back to the id when the stage isn't in the pipeline", () => {
    expect(thenStage(detail({ rail: parkedRail("waiting"), next_stage: "deploy" }))).toBe("deploy");
  });
});

describe("header text", () => {
  it("shows the runner only while running", () => {
    const r = run("running", { launch_ref: "pid 4242" });
    expect(runnerText(detail({ attention: "running", latest_run: r }))).toBe("pid 4242");
    expect(runnerText(detail({ attention: "needs_you", latest_run: r }))).toBe("none");
    expect(runnerText(detail({ attention: "running", latest_run: run("running") }))).toBe("none");
  });

  it("maps action errors to quiet words", () => {
    expect(actionErrorText(new ApiError(409, "run_active", "x"))).toBe("A run is already active");
    expect(actionErrorText(new ApiError(422, "invalid_stage", "No stage after review"))).toBe(
      "No stage after review",
    );
    expect(actionErrorText(new ApiError(409, "base_branch_missing", "AOS-1 has none"))).toBe(
      "Set a base branch first",
    );
    expect(actionErrorText(new Error("boom"))).toBe("boom");
    expect(actionErrorText("?")).toBe("Something went wrong");
  });
});

describe("chips and owner", () => {
  it("names a chip by the path's last segment", () => {
    expect(chipName(".claude/skills/aos-1/TASKS.md")).toBe("TASKS.md");
    expect(chipName("AGENTS.md")).toBe("AGENTS.md");
    expect(chipName("notes/")).toBe("notes");
  });

  it("maps the owner select both ways", () => {
    expect(ownerValue(person(3))).toBe("3");
    expect(ownerValue(null)).toBe("");
    expect(ownerPatch("3")).toEqual({ owner_id: 3 });
    expect(ownerPatch("")).toEqual({ owner_id: null });
  });
});
