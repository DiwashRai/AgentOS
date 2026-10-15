// Pure helpers for My queue: the row sub-line, the at-work note and the team rack. The ticket's own
// labels come from src/domain/ticketView.ts. Selection beyond `useBoard().groups` (the team rack)
// is over `attention` + ownership only; they never derive board state.
import type { Schemas } from "../../api/client";
import { clientWait, type OnlineKey } from "../../domain/clients";
import type { Counts } from "../../domain/counts";
import { firstName } from "../../domain/format";
import type { Groups } from "../../domain/groups";
import { isMine, type MeRef, statusKind } from "../../domain/status";
import { runLine, sourceRef } from "../../domain/ticketView";

type Ticket = Schemas["TicketSummary"];

/** Caps (DESIGN §18, adapted): Needs you and Ready to start list everything, because each row is
 *  something to do; Done recently shows the newest few. */
export const CAP = { tiles: 5, done: 3 } as const;

/** `/compact` with the given params (empty ones dropped). */
export function compactHref(params: Record<string, string>): string {
  const p = new URLSearchParams();
  for (const [name, value] of Object.entries(params)) if (value !== "") p.set(name, value);
  const qs = p.toString();
  return qs ? `/compact?${qs}` : "/compact";
}

/** True when My queue lists anything: one of its groups, or the team rack. Other people's
 *  inbox, waiting, blocked and done tickets are never on it; Compact shows the whole team. */
export function listsAnything(g: Groups<unknown>, rack: readonly RackGroup<unknown>[]): boolean {
  const lists = [g.needYou, g.atWork, g.readyMine, g.doneMine];
  return lists.some((list) => list.length > 0) || rack.length > 0;
}

/**
 * The item row's sub-line: `[Jira PLAT-1 ·] repo · Build · run #N` when there is a run, else
 * `repo · <Owner>'s ticket` (someone else's), else `repo`.
 */
export function subLine(
  t: Pick<Ticket, "key" | "attention" | "repo_name" | "now" | "latest_run" | "owner" | "source">,
  me: MeRef,
): string {
  const parts: string[] = [];
  const src = sourceRef(t);
  if (src) parts.push(src);
  if (t.repo_name) parts.push(t.repo_name);

  const run = runLine(t, null);
  if (run.run !== null) parts.push(run.label);
  else if (t.owner && !isMine(t, me)) parts.push(`${firstName(t.owner)}'s ticket`);

  return parts.join(" · ");
}

/** "2 yours · 5 across the team": the Agents-at-work note, from the shared counts. */
export function atWorkNote(c: Pick<Counts, "mine" | "running" | "queued">): string {
  const team = Math.max(0, c.running + c.queued - c.mine);
  return `${String(c.mine)} yours · ${String(team)} across the team`;
}

// ------------------------------------------------------------------ the rack

export interface RackGroup<T> {
  /** Stable id: `mine-queued`, `mine-client:<wait>` or `repo:<name>`. */
  id: string;
  /** The head: the repo name, "Yours, waiting for a runner", or "Yours, waiting for Sam's client". */
  name: string;
  /** A client group's client state, shown beside the head. */
  online?: OnlineKey | undefined;
  items: T[];
}

type RackTicket = Pick<Ticket, "key" | "attention" | "owner" | "repo_name" | "now" | "latest_run">;

export const NO_REPO = "No repo";
export const MINE_QUEUED = "Yours, waiting for a runner";

const runningFirst = (a: RackTicket, b: RackTicket) =>
  Number(a.attention === "queued") - Number(b.attention === "queued") || a.key.localeCompare(b.key);

/**
 * My queued runs (from `atWork`): the ones waiting for the server's runner under MINE_QUEUED, then
 * the ones waiting for a client, one group per wait (the server's "waiting for Sam's client" and
 * whether that client is online).
 */
function mineQueuedGroups<T extends RackTicket>(atWork: readonly T[]): RackGroup<T>[] {
  const runner: T[] = [];
  const clients = new Map<string, RackGroup<T>>();
  for (const t of atWork) {
    if (statusKind(t) !== "queued") continue;
    const wait = clientWait(t);
    if (!wait) {
      runner.push(t);
      continue;
    }
    // One creator's client runs share the wait and the online state.
    const id = `mine-client:${wait.text}`;
    const group = clients.get(id);
    if (group) group.items.push(t);
    else clients.set(id, { id, name: `Yours, ${wait.text}`, online: wait.online, items: [t] });
  }

  const out: RackGroup<T>[] = [];
  if (runner.length > 0) out.push({ id: "mine-queued", name: MINE_QUEUED, items: runner });
  return [...out, ...clients.values()];
}

/**
 * The rack's groups: my queued runs first (mineQueuedGroups), then the team's other running/queued
 * tickets (not mine) grouped by `repo_name` (alphabetical, no repo last), running before queued
 * within a repo.
 */
export function rackGroups<T extends RackTicket>(
  tickets: readonly T[],
  atWork: readonly T[],
  me: MeRef,
): RackGroup<T>[] {
  const out: RackGroup<T>[] = mineQueuedGroups(atWork);

  const byRepo = new Map<string, T[]>();
  for (const t of tickets) {
    const kind = statusKind(t);
    if ((kind !== "run" && kind !== "queued") || isMine(t, me)) continue;
    const repo = t.repo_name ?? "";
    const list = byRepo.get(repo);
    if (list) list.push(t);
    else byRepo.set(repo, [t]);
  }
  const repos = [...byRepo.keys()].sort((a, b) =>
    a === "" ? 1 : b === "" ? -1 : a.localeCompare(b),
  );
  for (const repo of repos) {
    const items = [...(byRepo.get(repo) ?? [])].sort(runningFirst);
    out.push({ id: `repo:${repo}`, name: repo || NO_REPO, items });
  }
  return out;
}

/**
 * Lay the groups out over `columns` columns, in order, each group into the currently shortest
 * column (a group head counts as one line). Groups never split.
 */
export function rackColumns<T>(groups: readonly RackGroup<T>[], columns = 3): RackGroup<T>[][] {
  const cols: RackGroup<T>[][] = Array.from({ length: Math.max(1, columns) }, () => []);
  const heights = cols.map(() => 0);
  for (const g of groups) {
    let best = 0;
    heights.forEach((h, i) => {
      if (h < (heights[best] ?? 0)) best = i;
    });
    cols[best]?.push(g);
    heights[best] = (heights[best] ?? 0) + g.items.length + 1;
  }
  return cols;
}

/** My running tiles (capped) from `atWork`: running only; queued ones go to the rack. */
export function myRunning<T extends Pick<Ticket, "attention">>(atWork: readonly T[]): T[] {
  return atWork.filter((t) => statusKind(t) === "run").slice(0, CAP.tiles);
}
