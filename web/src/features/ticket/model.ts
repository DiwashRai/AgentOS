// Pure display helpers for the Ticket page (DESIGN §7). They pick and combine server fields
// (`attention`, `rail[].gate`, `now`, `latest_run`, `plan`, `next_stage`, `pipeline`); they never
// derive board state.
import { ApiError, type Schemas } from "../../api/client";
import { firstName } from "../../domain/format";
import type { GlyphStage } from "../../domain/stage";
import { sourceNames } from "../../domain/words";

type Detail = Schemas["TicketDetail"];
type Gate = Schemas["GateInfo"];

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

export interface ParkedGate {
  gate: Gate;
  /** The rail stage the gate sits after. */
  stageName: string;
}

/** The rail gate the server says is parked (on you or on someone), if any. */
export function parkedGate(t: Pick<Detail, "rail">): ParkedGate | null {
  const step = t.rail.find((s) => s.gate?.state === "you" || s.gate?.state === "waiting");
  return step?.gate ? { gate: step.gate, stageName: step.name } : null;
}

export type StripKind = "run" | "queued" | "gate" | "status";

/** Exactly one strip under the header: a live run, a parked gate, else the status line. */
export function stripKind(t: Pick<Detail, "attention" | "rail">): StripKind {
  if (t.attention === "running") return "run";
  if (t.attention === "queued") return "queued";
  if (parkedGate(t)) return "gate";
  return "status";
}

/** "Plan · run #12": the run strip's mark label (`now.actor` is the running stage's name). */
export function runLabel(t: Pick<Detail, "now" | "latest_run">): string {
  const id = t.now.run_id ?? t.latest_run?.id ?? null;
  const run = id === null ? "" : `run #${String(id)}`;
  return [t.now.actor, run].filter(Boolean).join(" · ") || "Run";
}

/** The strip meter's k/n while running: k = done + 1 (the step in progress), capped at total. */
export function progressStep(p: Schemas["TaskProgress"] | null): { k: number; n: number } | null {
  if (!p || p.total <= 0) return null;
  return { k: Math.min(p.done + 1, p.total), n: p.total };
}

/** "Plan · run #12 passed" under the gate mark. */
export function gateMeta(t: Pick<Detail, "latest_run">, parked: ParkedGate): string {
  const run = t.latest_run ? ` · run #${String(t.latest_run.id)} passed` : "";
  return parked.stageName + run;
}

/** The status strip's text: `now.text`, plus the run's error when blocked; inbox has its own line. */
export function statusText(t: Pick<Detail, "attention" | "now" | "latest_run">): string {
  if (t.attention === "inbox") return "Nobody on it yet · add context, then dispatch";
  const err = t.attention === "blocked" ? t.latest_run?.error : null;
  return [t.now.text, err].filter(Boolean).join(" · ");
}

/** "THEN Build": only while parked at a gate, naming the server's `next_stage`. */
export function thenStage(t: Pick<Detail, "rail" | "next_stage" | "pipeline">): string | null {
  if (!parkedGate(t) || !t.next_stage) return null;
  return t.pipeline.stages.find((s) => s.id === t.next_stage)?.name ?? t.next_stage;
}

// ------------------------------------------------------------------ header

/** The lead: the description's first paragraph (blank-line separated). */
export function firstParagraph(text: string): string {
  return paragraphs(text)[0] ?? "";
}

/** Paragraphs split on blank lines; single newlines stay inside a paragraph. */
export function paragraphs(text: string): string[] {
  return text
    .replace(/\r\n?/g, "\n")
    .split(/\n\s*\n/)
    .map((p) => p.trim())
    .filter(Boolean);
}

const plural = (n: number, one: string, many: string) => `${String(n)} ${n === 1 ? one : many}`;

/** The byline's "7 stages · 1 gate · 1 skipped", counted from the stepper's own model (Inbox and
 *  Done bookends included, skipped stages still stages, gates apart), so the glyph, the stepper and
 *  the byline describe one pipeline (refresh HANDOFF §5 "Pipeline byline"). Zero parts are left out. */
export function pipelineSummary(stages: readonly Pick<GlyphStage, "state" | "gate">[]): string {
  const gates = stages.filter((s) => s.gate).length;
  const skipped = stages.filter((s) => s.state === "skipped").length;
  return [
    plural(stages.length, "stage", "stages"),
    gates ? plural(gates, "gate", "gates") : "",
    skipped ? `${String(skipped)} skipped` : "",
  ]
    .filter(Boolean)
    .join(" · ");
}

/** The kicker's source tag: "Jira", plus the external id only when it differs from our key
 *  (an imported Jira ticket keeps its Jira key, so the key chip already says it). */
export function sourceText(t: Pick<Detail, "key" | "source">): string | null {
  if (!t.source) return null;
  const word = sourceNames[t.source.source];
  return t.source.external_id === t.key ? word : `${word} ${t.source.external_id}`;
}

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

/** Who a waiting gate waits on, as a short name. */
export function gatePerson(g: Gate): string {
  return g.person ? firstName(g.person) : "Someone";
}

// ------------------------------------------------------------------ plan & context

export interface PlanProgress {
  done: number;
  total: number;
  /** The bar's fill, 0–100 (whole percent). */
  pct: number;
}

/** The plan head's "k of n done" and bar, counted from the items themselves (HANDOFF §8: the count
 *  matches its items). */
export function planProgress(plan: readonly Pick<Schemas["PlanItem"], "done">[]): PlanProgress {
  const done = plan.filter((p) => p.done).length;
  const total = plan.length;
  return { done, total, pct: total ? Math.round((done / total) * 100) : 0 };
}

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
