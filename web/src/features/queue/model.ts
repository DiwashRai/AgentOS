// Pure display helpers for My queue and the Peek. They pick and combine server fields for
// display (`now`, `rail[].gate`, `latest_run`, `repo_name`, `owner`); they never derive board
// state. Selection beyond `useBoard().groups` (the team rack) is over `attention` + ownership only.
import type { Schemas } from "../../api/client";
import { clientWait, type OnlineKey } from "../../domain/clients";
import type { Counts } from "../../domain/counts";
import { firstName } from "../../domain/format";
import type { Groups } from "../../domain/groups";
import { isMine, type MeRef, statusKind } from "../../domain/status";
import { type DisplayKind, OUTCOME_KIND, outcomeOf } from "../../domain/outcome";
import { outcomeWords, statusWords } from "../../domain/words";
import type { MeterState } from "../../ui";

type Ticket = Schemas["TicketSummary"];
type Person = Schemas["Person"];

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

/** The gate the server says the ticket is parked at (on you or on someone), if any. */
export function parkedGate(t: Pick<Ticket, "rail">): Schemas["GateInfo"] | null {
  for (const step of t.rail) {
    if (step.gate && (step.gate.state === "you" || step.gate.state === "waiting")) return step.gate;
  }
  return null;
}

/** The ask on a Needs-you row: the parked gate's label ("Plan sign-off"), else "Your move". */
export function askLabel(t: Pick<Ticket, "rail">): string {
  const gate = parkedGate(t);
  return gate?.state === "you" ? gate.label : "Your move";
}

/** Who a waiting ticket waits on: the parked gate's person, else `now.person`. */
export function waitingPerson(t: Pick<Ticket, "rail" | "now">): Person | null {
  return parkedGate(t)?.person ?? t.now.person;
}

/** The run number to show: `now.run_id`, else the latest run's id. */
export function runNumber(t: Pick<Ticket, "now" | "latest_run">): number | null {
  return t.now.run_id ?? t.latest_run?.id ?? null;
}

const SOURCE_NAME: Record<Schemas["SourceLink"]["source"], string> = {
  jira: "Jira",
  github: "GitHub",
  todoist: "Todoist",
};

/** Where the ticket came from, in sentence case beside the key chip: `Jira PLAT-2231`; null for
 *  our own tickets (the key chip says it all). */
export function sourceWord(t: Pick<Ticket, "source">): string | null {
  if (!t.source) return null;
  return `${SOURCE_NAME[t.source.source]} ${t.source.external_id}`;
}

/**
 * The item row's sub-line: `[Jira PLAT-1 ·] repo · Agent · run #N` when there is a run, else
 * `repo · <Owner>'s ticket` (someone else's), else `repo`.
 */
export function subLine(
  t: Pick<Ticket, "repo_name" | "now" | "latest_run" | "owner" | "source">,
  me: MeRef,
): string {
  const parts: string[] = [];
  const src = sourceWord(t);
  if (src) parts.push(src);
  if (t.repo_name) parts.push(t.repo_name);
  const run = runNumber(t);
  if (run !== null) {
    if (t.now.actor) parts.push(t.now.actor);
    parts.push(`run #${String(run)}`);
  } else if (t.owner && !isMine(t, me)) {
    parts.push(`${firstName(t.owner)}'s ticket`);
  }
  return parts.join(" · ");
}

/** The run tile / rack header name: `IMPLEMENT #42` (mono caps by CSS). */
export function runName(t: Pick<Ticket, "now" | "latest_run">): string {
  const n = runNumber(t);
  const actor = t.now.actor ?? "Run";
  return n === null ? actor : `${actor} #${String(n)}`;
}

export interface MeterModel {
  k: number;
  n: number;
  state: MeterState;
}

/**
 * The run meter from `now.progress`: k = done + 1 capped at total while running (DESIGN §13.4);
 * queued = all outlined. Null when the server sends no progress (the meter is hidden).
 */
export function meterOf(t: Pick<Ticket, "now" | "attention">): MeterModel | null {
  const p = t.now.progress;
  if (!p || p.total <= 0) return null;
  if (t.attention === "queued") return { k: 0, n: p.total, state: "queued" };
  if (t.attention === "running")
    return { k: Math.min(p.done + 1, p.total), n: p.total, state: "running" };
  return null;
}

/** Elapsed seconds since the run started (null before it has started). */
export function runSeconds(t: Pick<Ticket, "latest_run">, now: number): number | null {
  const started = t.latest_run?.started_at;
  if (!started) return null;
  const ms = Date.parse(started);
  return Number.isNaN(ms) ? null : Math.max(0, (now - ms) / 1000);
}

/** "2 yours · 5 across the team": the Agents-at-work note, from the shared counts. */
export function atWorkNote(c: Pick<Counts, "mine" | "running" | "queued">): string {
  const team = Math.max(0, c.running + c.queued - c.mine);
  return `${String(c.mine)} yours · ${String(team)} across the team`;
}

export interface MarkModel {
  kind: DisplayKind;
  name?: string | undefined;
  ask?: string | undefined;
}

/** The status mark with its ask, as the quiet rows and the peek's Status section show it. A done
 *  ticket shows its outcome: Complete (green) or Abandoned (red). */
export function statusMark(
  t: Pick<Ticket, "attention" | "rail" | "now" | "state" | "resolution">,
): MarkModel {
  const outcome = outcomeOf(t);
  if (outcome) return { kind: OUTCOME_KIND[outcome], name: outcomeWords[outcome] };
  const kind = statusKind(t);
  switch (kind) {
    case "you":
      return { kind, ask: askLabel(t) };
    case "wait": {
      const who = waitingPerson(t);
      const gate = parkedGate(t);
      return { kind, name: who ? firstName(who) : "Someone", ask: gate?.label };
    }
    case "blk":
    case "done": // unreachable: a done ticket always has an outcome
    case "queued":
      return { kind, name: statusWords[kind] };
    case "run":
      return { kind, name: t.now.actor ?? statusWords.run };
    case "idle":
      return { kind, name: statusWords.idle };
  }
}

/** The 3px status edge of a My queue row (refresh HANDOFF §4). */
export type RowEdge = "you" | "live" | "blocked" | "done" | "neutral";

/** A row's surface and edge: raised for in flight / needs action, flat for settled (done, incl.
 *  abandoned, whose edge is crimson). Over `attention` and the outcome only. */
export function rowLook(t: Pick<Ticket, "attention" | "state" | "resolution">): {
  flat: boolean;
  edge: RowEdge;
} {
  const outcome = outcomeOf(t);
  if (outcome) return { flat: true, edge: outcome === "abandoned" ? "blocked" : "done" };
  switch (t.attention) {
    case "needs_you":
      return { flat: false, edge: "you" };
    case "running":
      return { flat: false, edge: "live" };
    case "blocked":
      return { flat: false, edge: "blocked" };
    default:
      return { flat: false, edge: "neutral" };
  }
}

/** A done row's right-aligned outcome: "Complete · merged", "Abandoned · duplicate of AOS-8";
 *  the word alone without a note. Null for a ticket that isn't done. */
export function outcomeLine(
  t: Pick<Ticket, "attention" | "state" | "resolution">,
): { word: string; abandoned: boolean; note: string | null } | null {
  const outcome = outcomeOf(t);
  if (!outcome) return null;
  const note = t.resolution?.note?.trim();
  return {
    word: outcomeWords[outcome],
    abandoned: outcome === "abandoned",
    note: note === undefined || note === "" ? null : note,
  };
}

/** A blocked row's reason: the server's status line, else the run's error. */
export function blockedReason(t: Pick<Ticket, "now" | "latest_run">): string {
  return t.now.text !== "" ? t.now.text : (t.latest_run?.error ?? "");
}

/** A row's second line under its status mark. When the server says a dispatch would be refused
 *  (`dispatch_block`, e.g. "Set a base branch first"), that comes first and is flagged as needing
 *  action; otherwise a blocked row's reason, else the status line. */
export function stateLine(t: Pick<Ticket, "attention" | "now" | "latest_run" | "dispatch_block">): {
  text: string;
  flag: boolean;
} {
  if (t.dispatch_block) return { text: t.dispatch_block.text, flag: true };
  return { text: t.attention === "blocked" ? blockedReason(t) : t.now.text, flag: false };
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
