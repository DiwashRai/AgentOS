import { describe, expect, it } from "vitest";

import { ApiError, type Schemas } from "../../api/client";
import { gate, person, run, step, ticket } from "../../domain/fixtures";
import { stageModel } from "../../domain/stage";
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
  pipelineSummary,
  planProgress,
  progressStep,
  runLabel,
  runnerText,
  runSeconds,
  sourceText,
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

  it("counts the pipeline from the stepper's model: bookends in, gates and skipped apart", () => {
    // The mock's AOS-14: Inbox, Plan ◆ Sign-off, Implement, Lint (skipped), Review, Open PR, Done.
    const t = detail({
      rail: [
        step("Plan", "done", { gate: gate("you") }),
        step("Implement", "pending"),
        step("Lint", "skipped"),
        step("Review", "pending"),
        step("Open PR", "pending"),
      ],
    });
    const model = stageModel(t);
    expect(model.stages).toHaveLength(7);
    expect(pipelineSummary(model.stages)).toBe("7 stages · 1 gate · 1 skipped");
  });

  it("leaves out zero parts and pluralises", () => {
    const one = stageModel(detail({ rail: [step("Build", "pending")] }));
    expect(pipelineSummary(one.stages)).toBe("3 stages");
    const two = [
      step("Plan", "done", { gate: gate("passed") }),
      step("Build", "skipped"),
      step("Review", "skipped", { gate: gate("pending") }),
    ];
    expect(pipelineSummary(stageModel(detail({ rail: two })).stages)).toBe(
      "5 stages · 2 gates · 2 skipped",
    );
    expect(pipelineSummary([{ state: "current" }])).toBe("1 stage");
  });

  it("keeps the count for an abandoned ticket (the stop changes states, not stages)", () => {
    const t = detail({
      attention: "done",
      state: "done",
      resolution: { outcome: "abandoned", note: null, at: "2026-10-11T12:00:00Z", by: person(1) },
      rail: [step("Plan", "done"), step("Lint", "skipped"), step("Build", "pending")],
    });
    expect(pipelineSummary(stageModel(t).stages)).toBe("5 stages · 1 skipped");
  });

  it("names the source, adding the external id only when it isn't the key", () => {
    const jira = { source: "jira" as const, external_id: "SCRUM-6", url: "https://x/SCRUM-6" };
    expect(sourceText(detail({ key: "SCRUM-6", source: jira }))).toBe("Jira");
    expect(sourceText(detail({ key: "AOS-3", source: jira }))).toBe("Jira SCRUM-6");
    expect(
      sourceText(detail({ source: { source: "github", external_id: "#12", url: "https://g" } })),
    ).toBe("GitHub #12");
    expect(sourceText(detail())).toBeNull();
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
    expect(actionErrorText(new ApiError(409, "base_branch_missing", "AOS-1 has none"))).toBe(
      "Set a base branch first",
    );
    expect(actionErrorText(new Error("boom"))).toBe("boom");
    expect(actionErrorText("?")).toBe("Something went wrong");
  });
});

describe("plan, chips and owner", () => {
  it("counts the plan from its items, with the bar's fill", () => {
    const plan = [true, true, false, false, false].map((done, i) => ({ text: String(i), done }));
    expect(planProgress(plan)).toEqual({ done: 2, total: 5, pct: 40 });
    expect(planProgress([{ done: true }, { done: false }, { done: false }])).toEqual({
      done: 1,
      total: 3,
      pct: 33,
    });
    expect(planProgress([])).toEqual({ done: 0, total: 0, pct: 0 });
    expect(planProgress([{ done: true }])).toEqual({ done: 1, total: 1, pct: 100 });
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
