// The design's status kind for a ticket: a straight display mapping of the server's `attention`
// (DESIGN §12.4). Selection only: it never derives attention itself.
import type { Schemas } from "../api/client";

export type StatusKind = "you" | "wait" | "blk" | "run" | "queued" | "done" | "idle";
export type Attention = Schemas["TicketSummary"]["attention"];

type StatusTicket = Pick<Schemas["TicketSummary"], "attention">;
interface OwnedTicket {
  owner: Pick<Schemas["Person"], "id"> | null;
}

/** Who "me" is for selection: only the id matters. Null = signed out (nothing is mine). */
export type MeRef = Pick<Schemas["Me"], "id"> | null;

const STATUS_KIND: Record<Attention, StatusKind> = {
  needs_you: "you",
  waiting: "wait",
  running: "run",
  queued: "queued",
  blocked: "blk",
  inbox: "idle",
  done: "done",
};

export function statusKind(t: StatusTicket): StatusKind {
  return STATUS_KIND[t.attention];
}

export function isMine(t: OwnedTicket, me: MeRef): boolean {
  return me !== null && t.owner?.id === me.id;
}
