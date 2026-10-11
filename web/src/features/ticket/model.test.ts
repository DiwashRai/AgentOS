import { describe, expect, it } from "vitest";

import { ApiError, type Schemas } from "../../api/client";
import { gate, person, run, step, ticket } from "../../domain/fixtures";
import {
  actionErrorText,
  chipName,
  firstParagraph,
  gateMeta,
  openedText,
  ownerPatch,
  ownerValue,
  paragraphs,
  parkedGate,
  parseTab,
  pipelineText,
  planNumber,
  planSummary,
  progressStep,
  runLabel,
  runnerText,
  runSeconds,
  statusText,
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
    ...over,
  };
}

function detail(over: Partial<Detail> = {}): Detail {
  return {
    ...ticket(),
    active_run_id: null,
    base_branch: null,
    branch: null,
    created_at: "2026-10-11T10:00:00Z",
    description: "",
    next_stage: null,
    pipeline: { stages: [stage("plan"), stage("build"), stage("review")] },
    pipeline_version: 1,
    plan: null,
    repo_url: null,
    skill_name: "aos-1",
    slug: "a-ticket",
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

  it("finds the parked gate and the stage it follows", () => {
    expect(parkedGate(detail({ rail: parkedRail("you") }))?.stageName).toBe("Plan");
    expect(parkedGate(detail())).toBeNull();
  });
});

describe("run strip", () => {
  it("labels the run with the actor and run id", () => {
    const t = detail({
      now: { actor: "Build", person: null, progress: null, run_id: 42, text: "" },
    });
    expect(runLabel(t)).toBe("Build · run #42");
    expect(runLabel(detail({ latest_run: run("queued", { id: 7 }) }))).toBe("run #7");
    expect(runLabel(detail())).toBe("Run");
  });

  it("maps progress to k/n: the step in progress, capped at the total", () => {
    expect(progressStep({ done: 2, total: 5 })).toEqual({ k: 3, n: 5 });
    expect(progressStep({ done: 5, total: 5 })).toEqual({ k: 5, n: 5 });
    expect(progressStep({ done: 0, total: 0 })).toBeNull();
    expect(progressStep(null)).toBeNull();
  });

  it("counts elapsed seconds from the start", () => {
    const start = "2026-10-11T10:00:00Z";
    expect(runSeconds(start, Date.parse(start) + 75_500)).toBe(75);
    expect(runSeconds(null, 0)).toBe(0);
    expect(runSeconds(start, Date.parse(start) - 5000)).toBe(0);
  });
});

describe("gate and status strip text", () => {
  it("says which run passed before the gate", () => {
    const t = detail({ latest_run: run("gated", { id: 12 }) });
    const parked = parkedGate(detail({ rail: parkedRail("you") }));
    expect(parked && gateMeta(t, parked)).toBe("Plan · run #12 passed");
  });

  it("uses now.text, adds the run error when blocked, and has an inbox line", () => {
    const now = { actor: null, person: null, progress: null, run_id: 3, text: "Build failed" };
    const blocked = detail({
      attention: "blocked",
      now,
      latest_run: run("failed", { error: "exit 1" }),
    });
    expect(statusText(blocked)).toBe("Build failed · exit 1");
    expect(statusText(detail({ attention: "done", now: { ...now, text: "Done" } }))).toBe("Done");
    expect(statusText(detail({ attention: "inbox" }))).toBe(
      "Nobody on it yet · add context, then dispatch",
    );
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
  it("splits paragraphs on blank lines and takes the first as the lead", () => {
    const text = "First line\nstill first.\r\n\r\n  Second.  \n\n\n";
    expect(paragraphs(text)).toEqual(["First line\nstill first.", "Second."]);
    expect(firstParagraph(text)).toBe("First line\nstill first.");
    expect(firstParagraph("   ")).toBe("");
  });

  it("describes the pipeline as stages, with skipped ones", () => {
    expect(pipelineText({ stages: [stage("plan"), stage("build")] })).toBe("2 stages");
    expect(pipelineText({ stages: [stage("plan"), stage("verify", { enabled: false })] })).toBe(
      "2 stages · 1 skipped",
    );
    expect(pipelineText({ stages: [stage("build")] })).toBe("1 stage");
  });

  it("shows the runner only while running", () => {
    const r = run("running", { launch_ref: "pid 4242" });
    expect(runnerText(detail({ attention: "running", latest_run: r }))).toBe("pid 4242");
    expect(runnerText(detail({ attention: "needs_you", latest_run: r }))).toBe("none");
    expect(runnerText(detail({ attention: "running", latest_run: run("running") }))).toBe("none");
  });

  it("says how long ago it opened", () => {
    const t = detail({ created_at: "2026-10-11T10:00:00Z" });
    expect(openedText(t, Date.parse("2026-10-11T12:30:00Z"))).toBe("2h 30m ago");
    expect(openedText(t, Date.parse("2026-10-11T10:00:20Z"))).toBe("just now");
  });

  it("maps action errors to quiet words", () => {
    expect(actionErrorText(new ApiError(409, "run_active", "x"))).toBe("A run is already active");
    expect(actionErrorText(new ApiError(422, "invalid_stage", "No stage after review"))).toBe(
      "No stage after review",
    );
    expect(actionErrorText(new Error("boom"))).toBe("boom");
    expect(actionErrorText("?")).toBe("Something went wrong");
  });
});

describe("plan, chips and owner", () => {
  it("numbers plan lines and sums them up", () => {
    expect(planNumber(0)).toBe("01");
    expect(planNumber(11)).toBe("12");
    const plan = [
      { text: "a", done: true },
      { text: "b", done: false },
    ];
    expect(planSummary(plan)).toBe("1 of 2 done");
  });

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
