// How a done ticket ended: Complete (green) or Abandoned (red). Selection over server fields only:
// `resolution.outcome`, falling back to `state` (closed → abandoned, done → complete) for tickets
// marked done before resolutions existed. Only tickets the server says are `done` have an outcome.
import type { Schemas } from "../api/client";
import { age, firstName } from "./format";
import { statusKind, type StatusKind } from "./status";
import { outcomeWords } from "./words";

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

/** The ticket's mark: `statusKind`, except a done ticket shows its outcome. */
export function displayKind(t: OutcomeTicket): DisplayKind {
  const o = outcomeOf(t);
  return o ? OUTCOME_KIND[o] : statusKind(t);
}

/** "Complete" / "Abandoned" for a done ticket, else null. */
export function outcomeWord(t: OutcomeTicket): string | null {
  const o = outcomeOf(t);
  return o ? outcomeWords[o] : null;
}

/** "Diwash · 2h ago" / "Diwash · just now": who resolved it and when. */
export function resolvedBy(r: Pick<Schemas["Resolution"], "by" | "at">, nowMs: number): string {
  const a = age(r.at, nowMs);
  return `${firstName(r.by)} · ${a === "now" ? "just now" : `${a} ago`}`;
}
