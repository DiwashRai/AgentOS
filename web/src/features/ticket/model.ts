// Pure display helpers for the Ticket page (DESIGN §7): tabs and paths, the strip slot, the runner,
// action errors, chips and the owner select. The ticket's own labels come from
// src/domain/ticketView.ts. They pick and combine server fields; they never derive board state.
import { ApiError, type Schemas } from "../../api/client";
import { gate, type GateView } from "../../domain/ticketView";

type Detail = Schemas["TicketDetail"];

export type TicketTab = "overview" | "runs" | "context" | "pipeline";
export const TABS: readonly TicketTab[] = ["overview", "runs", "context", "pipeline"];

/** `:tab` from the URL; anything unknown (or missing) is the Overview. */
export function parseTab(tab: string | undefined): TicketTab {
  return TABS.find((t) => t === tab) ?? "overview";
}

/** `/t/AOS-1/runs?x=1`: the Overview drops its segment; the query string is kept. */
export function ticketPath(key: string, tab: TicketTab, search = ""): string {
  const base = `/t/${encodeURIComponent(key)}`;
  return (tab === "overview" ? base : `${base}/${tab}`) + search;
}

// ------------------------------------------------------------------ strip slot

export type StripKind = "run" | "queued" | "gate" | "status";

/** Exactly one strip under the header: a live run, a parked gate, else the status line. */
export function stripKind(t: Pick<Detail, "attention" | "rail" | "now">): StripKind {
  if (t.attention === "running") return "run";
  if (t.attention === "queued") return "queued";
  if (gate(t)) return "gate";
  return "status";
}

/** "Plan · run #12 passed" under the gate mark. */
export function gateMeta(t: Pick<Detail, "latest_run">, parked: Pick<GateView, "stage">): string {
  const run = t.latest_run ? ` · run #${String(t.latest_run.id)} passed` : "";
  return parked.stage + run;
}

/** "THEN Build": only while parked at a gate, naming the server's `next_stage`. */
export function thenStage(
  t: Pick<Detail, "rail" | "now" | "next_stage" | "pipeline">,
): string | null {
  if (!gate(t) || !t.next_stage) return null;
  return t.pipeline.stages.find((s) => s.id === t.next_stage)?.name ?? t.next_stage;
}

// ------------------------------------------------------------------ header

/** The runner while a run is on one (`launch_ref`), else "none". */
export function runnerText(t: Pick<Detail, "attention" | "latest_run">): string {
  const ref = t.attention === "running" ? t.latest_run?.launch_ref : null;
  return ref ?? "none";
}

/** The quiet line under the header when an action fails. */
export function actionErrorText(e: unknown): string {
  if (!(e instanceof ApiError)) return e instanceof Error ? e.message : "Something went wrong";
  switch (e.code) {
    case "run_active":
      return "A run is already active";
    case "stale_version":
      return "Changed by someone else · reload and try again";
    case "base_branch_missing":
      return "Set a base branch first";
    case "forbidden":
      return "You don't have permission to do that";
    case "unauthorized":
      return "Signed out · sign in again";
    default:
      return e.message;
  }
}

// ------------------------------------------------------------------ context & owner

/** A context chip shows the path's last segment. */
export function chipName(path: string): string {
  return path.replace(/\/+$/, "").split("/").pop() ?? path;
}

/** The owner select's value: a person id, or "" for Unassigned. */
export function ownerValue(owner: Schemas["Person"] | null): string {
  return owner ? String(owner.id) : "";
}

/** The PATCH body for an owner select change. */
export function ownerPatch(value: string): Schemas["TicketPatch"] {
  return { owner_id: value === "" ? null : Number(value) };
}
