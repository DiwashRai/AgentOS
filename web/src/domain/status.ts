// The design's status kind for a ticket: a display mapping of the server's `attention` (DESIGN
// §12.4). Selection only: `moving` splits on `latest_run.status` until CONTRACT_REQUESTS #2 gives
// the server's own running/queued split. Never derives attention itself.
import type { Schemas } from "../api/client";

export type StatusKind = "you" | "wait" | "blk" | "run" | "queued" | "done" | "idle";
export type Attention = Schemas["TicketSummary"]["attention"];

type StatusTicket = Pick<Schemas["TicketSummary"], "attention" | "latest_run">;
type OwnedTicket = Pick<Schemas["TicketSummary"], "owner">;

/** Who "me" is for selection: only the id matters. Null = signed out (nothing is mine). */
export type MeRef = Pick<Schemas["Me"], "id"> | null;

const BY_ATTENTION: Record<Exclude<Attention, "moving">, StatusKind> = {
  needs_you: "you",
  waiting: "wait",
  stuck: "blk",
  up_next: "idle",
  done: "done",
};

export function statusKind(t: StatusTicket): StatusKind {
  if (t.attention !== "moving") return BY_ATTENTION[t.attention];
  // A moving ticket has an active run; it waits for a runner while queued or dispatched.
  const s = t.latest_run?.status;
  return s === "queued" || s === "dispatched" ? "queued" : "run";
}

export function isMine(t: OwnedTicket, me: MeRef): boolean {
  return me !== null && t.owner?.id === me.id;
}
