// Pure display helpers for My queue and the Peek. They pick and combine server fields for
// display (`now`, `rail[].gate`, `latest_run`, `repo_name`, `owner`); they never derive board
// state. Selection beyond `useBoard().groups` (the team rack) is over `attention` + ownership only.
import type { Schemas } from "../../api/client";
import type { Counts } from "../../domain/counts";
import { firstName } from "../../domain/format";
import type { Groups } from "../../domain/groups";
import { isMine, type MeRef, statusKind, type StatusKind } from "../../domain/status";
import { statusWords } from "../../domain/words";
import type { MeterState } from "../../ui";

type Ticket = Schemas["TicketSummary"];
type Person = Schemas["Person"];

/** Caps from DESIGN §18 (My queue). */
export const CAP = { needYou: 5, tiles: 5, quiet: 3 } as const;

/** The `/compact` filter each "Show all N" link opens (`/compact?f=<group>`). */
export const SHOW_ALL_FILTER = {
  needYou: "needs_you",
  waitingMine: "waiting",
  blocked: "blocked",
  doneRecent: "done",
} as const;

/** The search (`q`) rides along, so "Show all N" lists the same N. */
export function showAllHref(group: keyof typeof SHOW_ALL_FILTER, q = ""): string {
  return compactHref({ f: SHOW_ALL_FILTER[group], q });
}

/** `/compact` with the given params (empty ones dropped). */
export function compactHref(params: Record<string, string>): string {
  const p = new URLSearchParams();
  for (const [name, value] of Object.entries(params)) if (value !== "") p.set(name, value);
  const qs = p.toString();
  return qs ? `/compact?${qs}` : "/compact";
}

/** True when My queue lists anything: one of its groups, or the team rack. Inbox tickets and
 *  the team's waiting tickets are never on it. */
export function listsAnything(g: Groups<unknown>, rack: readonly RackGroup<unknown>[]): boolean {
  const lists = [g.needYou, g.atWork, g.waitingMine, g.blocked, g.doneRecent];
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

/**
 * The item row's sub-line: `repo · Agent · run #N` when there is a run, else
 * `repo · <Owner>'s ticket` (someone else's), else `repo`.
 */
export function subLine(
  t: Pick<Ticket, "repo_name" | "now" | "latest_run" | "owner">,
  me: MeRef,
): string {
  const parts: string[] = [];
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
  kind: StatusKind;
  name?: string | undefined;
  ask?: string | undefined;
}

/** The status mark with its ask, as the quiet rows and the peek's Status section show it. */
export function statusMark(t: Pick<Ticket, "attention" | "rail" | "now">): MarkModel {
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
    case "done":
    case "queued":
      return { kind, name: statusWords[kind] };
    case "run":
      return { kind, name: t.now.actor ?? statusWords.run };
    case "idle":
      return { kind, name: statusWords.idle };
  }
}

// ------------------------------------------------------------------ the rack

export interface RackGroup<T> {
  /** Stable id: `mine-queued` or `repo:<name>`. */
  id: string;
  /** Mono caps head: the repo name, or "Yours, waiting for a runner". */
  name: string;
  items: T[];
}

type RackTicket = Pick<Ticket, "key" | "attention" | "owner" | "repo_name">;

export const NO_REPO = "No repo";
export const MINE_QUEUED = "Yours, waiting for a runner";

const runningFirst = (a: RackTicket, b: RackTicket) =>
  Number(a.attention === "queued") - Number(b.attention === "queued") || a.key.localeCompare(b.key);

/**
 * The rack's groups: my queued runs first ("Yours, waiting for a runner", from `atWork`), then
 * the team's other running/queued tickets (not mine) grouped by `repo_name` (alphabetical, no
 * repo last), running before queued within a repo.
 */
export function rackGroups<T extends RackTicket>(
  tickets: readonly T[],
  atWork: readonly T[],
  me: MeRef,
): RackGroup<T>[] {
  const out: RackGroup<T>[] = [];
  const mineQueued = atWork.filter((t) => statusKind(t) === "queued");
  if (mineQueued.length > 0) out.push({ id: "mine-queued", name: MINE_QUEUED, items: mineQueued });

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
