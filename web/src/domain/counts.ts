// Every number on screen comes from here (DESIGN §12.5, adapted). Selection only: counts over
// server fields (`attention`, `owner.id`). "Mine" = I own the ticket.
// Dropped from the design: `watching` (no watchers in our model), `atWork`/`mineQueued` (same as
// `mine` / derivable from groups), `merged` today (no merge time yet; `done` counts all done).
import type { Schemas } from "../api/client";
import { outcomeOf } from "./outcome";
import { isMine, type MeRef, statusKind, type StatusKind } from "./status";

type CountTicket = Pick<Schemas["TicketSummary"], "attention" | "owner">;

export interface Counts {
  /** status you */
  needYou: number;
  /** blocked and mine */
  blockedMine: number;
  /** running or queued runs on my tickets */
  mine: number;
  /** all running runs */
  running: number;
  /** all runs waiting for a runner */
  queued: number;
  /** all blocked tickets */
  blocked: number;
  /** mine, waiting on someone else */
  waitingMine: number;
  /** all tickets waiting on someone */
  waiting: number;
  /** in the inbox, nobody on it */
  inbox: number;
  /** not done and not inbox */
  inFlight: number;
  /** done */
  done: number;
}

export type CountKey = keyof Counts;

/** How each count is shown as a top-bar counter: its mark (null = none) and label. */
export const COUNTERS: Record<CountKey, { mark: StatusKind | null; label: string }> = {
  needYou: { mark: "you", label: "need you" },
  blockedMine: { mark: "blk", label: "blocked, yours" },
  mine: { mark: "run", label: "runs yours" },
  running: { mark: "run", label: "running" },
  queued: { mark: "queued", label: "queued" },
  blocked: { mark: "blk", label: "blocked" },
  waitingMine: { mark: "wait", label: "waiting on others" },
  waiting: { mark: "wait", label: "waiting on someone" },
  inbox: { mark: "idle", label: "in inbox" },
  inFlight: { mark: null, label: "in flight" },
  done: { mark: "done", label: "done" },
};

const WEEK_MS = 7 * 24 * 60 * 60 * 1000;

type ShippedTicket = Pick<
  Schemas["TicketSummary"],
  "attention" | "state" | "resolution" | "attention_since"
>;

/** "N shipped this week": done tickets that ended Complete in the last 7 days (by when they were
 *  resolved, else when they became done). Counts only what the board returned. */
export function shippedThisWeek(tickets: readonly ShippedTicket[], nowMs: number): number {
  return tickets.filter((t) => {
    if (outcomeOf(t) !== "completed") return false;
    const at = Date.parse(t.resolution?.at ?? t.attention_since);
    return nowMs - at <= WEEK_MS;
  }).length;
}

export function counts(tickets: readonly CountTicket[], me: MeRef): Counts {
  const c: Counts = {
    needYou: 0,
    blockedMine: 0,
    mine: 0,
    running: 0,
    queued: 0,
    blocked: 0,
    waitingMine: 0,
    waiting: 0,
    inbox: 0,
    inFlight: 0,
    done: 0,
  };
  for (const t of tickets) {
    const kind = statusKind(t);
    const mine = isMine(t, me);
    switch (kind) {
      case "you":
        c.needYou++;
        break;
      case "blk":
        c.blocked++;
        if (mine) c.blockedMine++;
        break;
      case "run":
        c.running++;
        if (mine) c.mine++;
        break;
      case "queued":
        c.queued++;
        if (mine) c.mine++;
        break;
      case "wait":
        c.waiting++;
        if (mine) c.waitingMine++;
        break;
      case "idle":
        c.inbox++;
        break;
      case "done":
        c.done++;
        break;
    }
    if (kind !== "done" && kind !== "idle") c.inFlight++;
  }
  return c;
}
