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
    ...over,
  };
}

export function ticket(over: Partial<Ticket> = {}): Ticket {
  return {
    key: "AOS-1",
    title: "A ticket",
    attention: "up_next",
    state: "open",
    type: "task",
    priority: "normal",
    labels: [],
    chat_count: 0,
    owner: null,
    repo_name: null,
    source: null,
    latest_run: null,
    tasks: null,
    phase: "inbox",
    phase_since: null,
    phase_track: [],
    rail: [],
    now: { actor: null, person: null, progress: null, run_id: null, text: "" },
    updated_at: "2026-10-11T10:00:00Z",
    ...over,
  };
}
