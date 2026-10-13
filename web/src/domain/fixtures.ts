// Test fixtures for domain tests: complete, typed TicketSummary values with sensible defaults.
import type { Schemas } from "../api/client";

type Ticket = Schemas["TicketSummary"];
type Run = Schemas["RunSummary"];

export function person(id: number, displayName = `Person ${String(id)}`): Schemas["Person"] {
  return { id, display_name: displayName, avatar_url: null };
}

export function run(status: Run["status"], over: Partial<Run> = {}): Run {
  return {
    id: 1,
    ticket_key: "AOS-1",
    status,
    from_stage: "plan",
    stage_ids: ["plan"],
    created_at: "2026-10-11T10:00:00Z",
    created_by: null,
    started_at: null,
    finished_at: null,
    cancel_requested_at: null,
    error: null,
    launch_ref: null,
    launch_url: null,
    log_delivery: "live",
    ...over,
  };
}

type Step = Schemas["RailStep"];
type Gate = Schemas["GateInfo"];

/** A gate as the server sends it: "{Stage} sign-off" / "Sign-off". */
export function gate(state: Gate["state"], over: Partial<Gate> = {}): Gate {
  return { label: "Plan sign-off", short: "Sign-off", state, person: null, ...over };
}

/** A rail step; the stage id is the lowercased name. */
export function step(name: string, state: Step["state"], over: Partial<Step> = {}): Step {
  return { name, state, stage_id: name.toLowerCase(), phase: "build", gate: null, ...over };
}

export function ticket(over: Partial<Ticket> = {}): Ticket {
  return {
    key: "AOS-1",
    title: "A ticket",
    attention: "inbox",
    attention_since: "2026-10-11T10:00:00Z",
    state: "open",
    type: "task",
    priority: "normal",
    labels: [],
    chat_count: 0,
    owner: null,
    repo_name: null,
    source: null,
    latest_run: null,
    pr_url: null,
    dispatch_block: null,
    resolution: null,
    tasks: null,
    phase: "inbox",
    phase_track: [],
    rail: [],
    now: { actor: null, person: null, progress: null, run_id: null, text: "" },
    updated_at: "2026-10-11T10:00:00Z",
    ...over,
  };
}
