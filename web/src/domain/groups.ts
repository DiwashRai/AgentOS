// The personal lists (DESIGN §12.5): every view that shows "my" lists uses these. Selection and
// ordering only, over server fields. Generic, so callers keep their full ticket type.
import type { Schemas } from "../api/client";
import { isMine, type MeRef, statusKind } from "./status";

export type GroupTicket = Pick<
  Schemas["TicketSummary"],
  "key" | "attention" | "latest_run" | "owner" | "phase_since" | "updated_at"
>;

export interface Groups<T> {
  /** status you; longest wait first */
  needYou: T[];
  /** mine, running or queued; running first, then most recent first */
  atWork: T[];
  /** mine, waiting on someone else; longest wait first */
  waitingMine: T[];
  /** all blocked; mine first, then longest wait first */
  blocked: T[];
  /** done; most recent first */
  doneRecent: T[];
}

/**
 * When the ticket entered its current state: the one place "how long has it waited" is read.
 * Switch to `attention_since` when CONTRACT_REQUESTS #3 lands (it doesn't exist yet); until then
 * `phase_since`, else `updated_at`.
 */
export function since(t: Pick<GroupTicket, "phase_since" | "updated_at">): string {
  return t.phase_since ?? t.updated_at;
}

function sinceMs(t: GroupTicket): number {
  const ms = Date.parse(since(t));
  return Number.isNaN(ms) ? 0 : ms;
}

const byKey = (a: GroupTicket, b: GroupTicket) => a.key.localeCompare(b.key);
const longestFirst = (a: GroupTicket, b: GroupTicket) => sinceMs(a) - sinceMs(b) || byKey(a, b);
const newestFirst = (a: GroupTicket, b: GroupTicket) => sinceMs(b) - sinceMs(a) || byKey(a, b);

export function groups<T extends GroupTicket>(tickets: readonly T[], me: MeRef): Groups<T> {
  const g: Groups<T> = { needYou: [], atWork: [], waitingMine: [], blocked: [], doneRecent: [] };
  for (const t of tickets) {
    const kind = statusKind(t);
    const mine = isMine(t, me);
    if (kind === "you") g.needYou.push(t);
    else if (kind === "blk") g.blocked.push(t);
    else if (kind === "done") g.doneRecent.push(t);
    else if (mine && (kind === "run" || kind === "queued")) g.atWork.push(t);
    else if (mine && kind === "wait") g.waitingMine.push(t);
  }
  g.needYou.sort(longestFirst);
  g.atWork.sort(
    (a, b) =>
      Number(statusKind(a) === "queued") - Number(statusKind(b) === "queued") || newestFirst(a, b),
  );
  g.waitingMine.sort(longestFirst);
  g.blocked.sort((a, b) => Number(isMine(b, me)) - Number(isMine(a, me)) || longestFirst(a, b));
  g.doneRecent.sort(newestFirst);
  return g;
}
