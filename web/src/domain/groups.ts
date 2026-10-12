// My queue's lists (DESIGN §12.5, narrowed to what is the viewer's): every view that shows "my"
// lists uses these. Selection and ordering only, over server fields. Generic, so callers keep their
// full ticket type.
import type { Schemas } from "../api/client";
import { isMine, type MeRef, statusKind } from "./status";

export type GroupTicket = Pick<
  Schemas["TicketSummary"],
  "key" | "attention" | "owner" | "attention_since"
>;

export interface Groups<T> {
  /** your move: status you, plus blocked tickets that are mine or nobody's (the server's
   *  needs_you rule: you own it or nobody does); longest wait first */
  needYou: T[];
  /** mine, running or queued; running first, then most recent first */
  atWork: T[];
  /** mine, in the inbox (taken in or created, not run yet); longest wait first */
  readyMine: T[];
  /** mine, done; most recent first (My queue shows the last 7 days: `doneWithin`) */
  doneMine: T[];
}

/** When the ticket entered its current attention: the one place "how long has it waited" is read. */
export function since(t: Pick<GroupTicket, "attention_since">): string {
  return t.attention_since;
}

function sinceMs(t: GroupTicket): number {
  const ms = Date.parse(since(t));
  return Number.isNaN(ms) ? 0 : ms;
}

const byKey = (a: GroupTicket, b: GroupTicket) => a.key.localeCompare(b.key);
const longestFirst = (a: GroupTicket, b: GroupTicket) => sinceMs(a) - sinceMs(b) || byKey(a, b);
const newestFirst = (a: GroupTicket, b: GroupTicket) => sinceMs(b) - sinceMs(a) || byKey(a, b);

export function groups<T extends GroupTicket>(tickets: readonly T[], me: MeRef): Groups<T> {
  const g: Groups<T> = { needYou: [], atWork: [], readyMine: [], doneMine: [] };
  for (const t of tickets) {
    const kind = statusKind(t);
    const mine = isMine(t, me);
    if (kind === "you" || (kind === "blk" && (mine || t.owner === null))) g.needYou.push(t);
    else if (!mine) continue;
    else if (kind === "run" || kind === "queued") g.atWork.push(t);
    else if (kind === "idle") g.readyMine.push(t);
    else if (kind === "done") g.doneMine.push(t);
  }
  g.needYou.sort(longestFirst);
  g.atWork.sort(
    (a, b) =>
      Number(statusKind(a) === "queued") - Number(statusKind(b) === "queued") || newestFirst(a, b),
  );
  g.readyMine.sort(longestFirst);
  g.doneMine.sort(newestFirst);
  return g;
}
