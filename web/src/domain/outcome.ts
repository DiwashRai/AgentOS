// How a done ticket ended: Complete (green) or Abandoned (red). Selection over server fields only:
// `resolution.outcome`, falling back to `state` (closed → abandoned, done → complete) for tickets
// marked done before resolutions existed. Only tickets the server says are `done` have an outcome.
import type { Schemas } from "../api/client";
import { ago, firstName } from "./format";
import type { StatusKind } from "./status";

export type Outcome = Schemas["Resolution"]["outcome"];

type OutcomeTicket = Pick<Schemas["TicketSummary"], "attention" | "state" | "resolution">;

/** The status mark kind for a done ticket: the green check, or the red cross. */
export type DoneKind = "done" | "abandoned";
/** Every mark a ticket can show: its status kind, with done split by outcome. */
export type DisplayKind = StatusKind | "abandoned";

export const OUTCOME_KIND: Record<Outcome, DoneKind> = {
  completed: "done",
  abandoned: "abandoned",
};

export function outcomeOf(t: OutcomeTicket): Outcome | null {
  if (t.attention !== "done") return null;
  return t.resolution?.outcome ?? (t.state === "closed" ? "abandoned" : "completed");
}

/** "Diwash · 2h ago" / "Diwash · just now": who resolved it and when. */
export function resolvedBy(r: Pick<Schemas["Resolution"], "by" | "at">, nowMs: number): string {
  return `${firstName(r.by)} · ${ago(r.at, nowMs)}`;
}
