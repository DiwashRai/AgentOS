// Pure selection and display helpers for the Compact view (DESIGN §6). Selection is over server
// fields only (`attention`, `repo_name`, `attention_since`, `owner`); the counts come from
// src/domain/counts.ts, so a pill's count always equals its group's count. Display helpers pick
// and combine `now`, `rail[].gate`, `latest_run` and `source`; they never derive board state.
import type { Schemas } from "../../api/client";
import { type CountKey, counts, shippedThisWeek } from "../../domain/counts";
import { age, duration, firstName, repoShort, runSeconds } from "../../domain/format";
import { since } from "../../domain/groups";
import { type Outcome, outcomeOf, outcomeWord } from "../../domain/outcome";
import { planText } from "../../domain/plan";
import {
  type Attention,
  isMine,
  type MeRef,
  statusKind,
  type StatusKind,
} from "../../domain/status";
import { attentionWords, outcomeWords, sourceNames, statusWords, words } from "../../domain/words";
import { projectPrefix } from "../../ui/identity";
import { meterOfText } from "../../domain/meter";
import {
  type MarkModel,
  meterOf,
  type MeterModel,
  parkedGate,
  runNumber,
  statusMark,
  waitingPerson,
} from "../queue/model";
import { statusText } from "../ticket/model";

type Ticket = Schemas["TicketSummary"];

/** `?f=`: one attention group, or everything. */
export type Filter = Attention | "all";

export const FILTER_PARAM = "f";
export const REPO_PARAM = "repo";

/** Group order (DESIGN §6), with Inbox before Done as in the design's Idle group. */
export const GROUP_ORDER: readonly Attention[] = [
  "needs_you",
  "blocked",
  "running",
  "queued",
  "waiting",
  "inbox",
  "done",
];

/** Group heads and pills share one title per group. Done reads "Done recently", as in My queue
 *  (there is no merge time yet, so not "Merged today"). */
export const GROUP_TITLE: Record<Attention, string> = {
  ...attentionWords,
  done: words.doneRecent,
};

/** Which shared count each group's pill shows (src/domain/counts.ts). */
const COUNT_KEY: Record<Attention, CountKey> = {
  needs_you: "needYou",
  blocked: "blocked",
  running: "running",
  queued: "queued",
  waiting: "waiting",
  inbox: "inbox",
  done: "done",
};

export function parseFilter(f: string | null): Filter {
  return GROUP_ORDER.find((a) => a === f) ?? "all";
}

/** The search params with `f` set to `filter` (dropped for everything); the others are kept. */
export function withFilter(p: URLSearchParams, filter: Filter): URLSearchParams {
  const next = new URLSearchParams(p);
  if (filter === "all") next.delete(FILTER_PARAM);
  else next.set(FILTER_PARAM, filter);
  return next;
}

/** `?repo=`: only tickets whose `repo_name` is exactly `repo` (null = no repo filter). */
export function inRepo<T extends Pick<Ticket, "repo_name">>(
  tickets: readonly T[],
  repo: string | null,
): readonly T[] {
  return repo === null ? tickets : tickets.filter((t) => t.repo_name === repo);
}

export function byFilter<T extends Pick<Ticket, "attention">>(
  tickets: readonly T[],
  filter: Filter,
): readonly T[] {
  return filter === "all" ? tickets : tickets.filter((t) => t.attention === filter);
}

// ------------------------------------------------------------------ pills

export interface Pill {
  filter: Filter;
  label: string;
  /** The 7px glyph; none on Everything. */
  mark: StatusKind | null;
  count: number;
}

type CountTicket = Pick<Ticket, "attention" | "owner">;

/**
 * The top-bar pills (DESIGN §6) over `tickets` (already repo-scoped): Everything, then each group
 * but Done. Done is reached from My queue's "Show all", so its pill appears only while active.
 */
export function pills(tickets: readonly CountTicket[], me: MeRef, active: Filter): Pill[] {
  const c = counts(tickets, me);
  const shown = GROUP_ORDER.filter((a) => a !== "done" || active === "done");
  return [
    { filter: "all", label: "Everything", mark: null, count: tickets.length },
    ...shown.map((a): Pill => ({
      filter: a,
      label: GROUP_TITLE[a],
      mark: statusKind({ attention: a }),
      count: c[COUNT_KEY[a]],
    })),
  ];
}

// ------------------------------------------------------------------ groups

/** A group-head stat: plain text runs, `{b}` emphasised runs and `{age}` ages, in order. */
export type Summary = (string | { b: string } | { age: string })[];

/** The group head's right slot (refresh HANDOFF §5 "Group head"). */
export type GroupRight =
  /** "Longest wait 38m", "1 yours", "web 2 · api 1" */
  | { kind: "stat"; parts: Summary }
  /** The Inbox instruction, once, in the head only. */
  | { kind: "note"; text: string }
  /** Done recently: the tick badge + "N shipped this week" (only when N > 0). */
  | { kind: "shipped"; n: number }
  | null;

export interface CompactGroup<T> {
  id: Attention;
  title: string;
  mark: StatusKind;
  items: T[];
  right: GroupRight;
}

/** The Inbox group head's note. */
export const INBOX_NOTE = "Add context, then dispatch";

type GroupTicket = Pick<
  Ticket,
  "key" | "attention" | "attention_since" | "owner" | "repo_name" | "state" | "resolution"
>;

function sinceMs(t: GroupTicket): number {
  const ms = Date.parse(since(t));
  return Number.isNaN(ms) ? 0 : ms;
}

const byKey = (a: GroupTicket, b: GroupTicket) => a.key.localeCompare(b.key);
const longestFirst = (a: GroupTicket, b: GroupTicket) => sinceMs(a) - sinceMs(b) || byKey(a, b);
const newestFirst = (a: GroupTicket, b: GroupTicket) => sinceMs(b) - sinceMs(a) || byKey(a, b);
/** Alphabetical by repo (no repo last), then longest wait first. */
const byRepo = (a: GroupTicket, b: GroupTicket) => {
  const ra = a.repo_name ?? "";
  const rb = b.repo_name ?? "";
  if (ra !== rb) return ra === "" ? 1 : rb === "" ? -1 : ra.localeCompare(rb);
  return longestFirst(a, b);
};

/** Order inside a group (DESIGN §6): waits longest first, done newest first, the rest by repo. */
const ORDER: Record<Attention, (a: GroupTicket, b: GroupTicket) => number> = {
  needs_you: longestFirst,
  blocked: longestFirst,
  waiting: longestFirst,
  running: byRepo,
  queued: byRepo,
  inbox: byRepo,
  done: newestFirst,
};

export const NO_REPO = "No repo";

/** "web 7 · api 3": where the work is, by repo (most first). Only for groups of 4 or more. */
function repoSummary(items: readonly GroupTicket[]): Summary {
  if (items.length < 4) return [];
  const n = new Map<string, number>();
  for (const t of items) {
    const name = repoShort(t.repo_name) || NO_REPO;
    n.set(name, (n.get(name) ?? 0) + 1);
  }
  const out: Summary = [];
  [...n.entries()]
    .sort(([a, x], [b, y]) => y - x || a.localeCompare(b))
    .forEach(([name, count], i) => {
      out.push(`${i > 0 ? " · " : ""}${name} `, { b: String(count) });
    });
  return out;
}

const stat = (parts: Summary): GroupRight => (parts.length > 0 ? { kind: "stat", parts } : null);

function right(id: Attention, items: readonly GroupTicket[], me: MeRef, now: number): GroupRight {
  switch (id) {
    case "needs_you": {
      const oldest = Math.min(...items.map(sinceMs));
      return stat(Number.isFinite(oldest) ? ["Longest wait ", { age: age(oldest, now) }] : []);
    }
    case "blocked": {
      const mine = items.filter((t) => isMine(t, me)).length;
      return stat(mine > 0 ? [{ b: String(mine) }, " yours"] : []);
    }
    case "inbox":
      return { kind: "note", text: INBOX_NOTE };
    case "done": {
      const n = shippedThisWeek(items, now);
      return n > 0 ? { kind: "shipped", n } : null;
    }
    default:
      return stat(repoSummary(items));
  }
}

/** The non-empty groups of `tickets`, in GROUP_ORDER, each sorted and summarised. */
export function compactGroups<T extends GroupTicket>(
  tickets: readonly T[],
  me: MeRef,
  now: number,
): CompactGroup<T>[] {
  return GROUP_ORDER.flatMap((id): CompactGroup<T>[] => {
    const items = tickets.filter((t) => t.attention === id).sort(ORDER[id]);
    if (items.length === 0) return [];
    return [
      {
        id,
        title: GROUP_TITLE[id],
        mark: statusKind({ attention: id }),
        items,
        right: right(id, items, me, now),
      },
    ];
  });
}

/** What the list renders, in order: the groups, plus a dashed empty row where an empty group is
 *  worth saying out loud (refresh HANDOFF §3.10: Needs you, Running, Queued; others stay hidden). */
export type Section<T> =
  | { kind: "group"; group: CompactGroup<T> }
  | { kind: "empty"; id: "needs_you" | "running" | "queued"; lead: string; note: string | null };

/**
 * The groups with their empty rows. `showEmpty` is false while a filter or a search narrows the
 * list (an "All quiet" there would be about the filter, not the board). An empty Running and Queued
 * share one row, at Running's place.
 */
export function compactSections<T>(
  groups: readonly CompactGroup<T>[],
  showEmpty: boolean,
): Section<T>[] {
  const has = (id: Attention) => groups.some((g) => g.id === id);
  const out: Section<T>[] = [];
  for (const id of GROUP_ORDER) {
    const group = groups.find((g) => g.id === id);
    if (group) {
      out.push({ kind: "group", group });
      continue;
    }
    if (!showEmpty) continue;
    if (id === "needs_you") {
      out.push({ kind: "empty", id, lead: "All quiet", note: "nothing needs you" });
    } else if (id === "running") {
      out.push(
        has("queued")
          ? { kind: "empty", id, lead: "Nothing running", note: null }
          : { kind: "empty", id, lead: "All quiet", note: "nothing running, nothing queued" },
      );
    } else if (id === "queued" && has("running")) {
      out.push({ kind: "empty", id, lead: "Nothing queued", note: null });
    }
  }
  return out;
}

/** Done starts collapsed (DESIGN §6), unless it is what the filter asks for. */
export function collapsedByDefault(id: Attention, filter: Filter): boolean {
  return id === "done" && filter !== "done";
}

export interface FootSummary {
  tickets: number;
  /** One key chip per project prefix, alphabetical ("AOS", "SCRUM"). */
  projects: string[];
  repos: number;
}

/** "7 tickets · [AOS] [SCRUM] · 3 repos" in the footer, over what is listed. */
export function footSummary(tickets: readonly Pick<Ticket, "key" | "repo_name">[]): FootSummary {
  return {
    tickets: tickets.length,
    projects: [...new Set(tickets.map((t) => projectPrefix(t.key)))].sort(),
    repos: new Set(tickets.map((t) => t.repo_name).filter((r) => r !== null)).size,
  };
}

/** "1 ticket", "3 repos": the word after a footer count. */
export function plural(n: number, one: string): string {
  return n === 1 ? one : `${one}s`;
}

const EMPTY: Record<Filter, string> = {
  all: "No tickets",
  needs_you: "Nothing needs you",
  blocked: "Nothing blocked",
  running: "Nothing running",
  queued: "Nothing queued",
  waiting: "Nothing waiting on others",
  inbox: "Nothing in the inbox",
  done: "Nothing done yet",
};

/** The empty line when the filter matches nothing. */
export function emptyText(filter: Filter, repo: string | null): string {
  return repo ? `${EMPTY[filter]} in ${repoShort(repo) || repo}` : EMPTY[filter];
}

// ------------------------------------------------------------------ the row

/** The Source column: `Jira · web`, `web` for our own tickets, "" with neither. Sentence case. */
export function sourceText(t: Pick<Ticket, "source" | "repo_name">): string {
  const name = t.source ? sourceNames[t.source.source] : "";
  return [name, repoShort(t.repo_name)].filter(Boolean).join(" · ");
}

/** The 3px status edge (refresh HANDOFF §4): you blue, running vermilion, blocked and abandoned
 *  crimson, complete green; inbox, queued and waiting a quiet rule. */
export type Edge = "you" | "live" | "blocked" | "done" | "quiet";

export function edgeOf(t: Pick<Ticket, "attention" | "state" | "resolution">): Edge {
  switch (t.attention) {
    case "needs_you":
      return "you";
    case "running":
      return "live";
    case "blocked":
      return "blocked";
    case "done":
      return outcomeOf(t) === "abandoned" ? "blocked" : "done";
    default:
      return "quiet";
  }
}

/** Raised = in flight or needs action; flat = settled (done, closed). */
export function isFlat(t: Pick<Ticket, "attention">): boolean {
  return t.attention === "done";
}

/** The Upd column: `now` while a run is on a runner, else how long it has been in this state. */
export function updText(t: Pick<Ticket, "attention" | "attention_since">, now: number): string {
  return t.attention === "running" ? "now" : age(since(t), now);
}

type NowTicket = Pick<
  Ticket,
  "attention" | "rail" | "now" | "latest_run" | "state" | "resolution" | "dispatch_block"
>;

export type NowCell =
  /** `BUILDER · step · meter · 3/5` */
  | { kind: "run"; actor: string; text: string; meter: MeterModel | null }
  /** `YOU Plan sign-off` + ask, `○ Sam · PR review` + text, `■ Blocked` + reason, … The text is
   *  `flag`ged when it is the server's dispatch block ("Set a base branch first"). */
  | { kind: "mark"; mark: MarkModel; text: string; flag: boolean }
  /** An Inbox row the server won't dispatch yet: just the block's text ("Set a base branch
   *  first"), flagged. Add context covers it on hover / focus / open. */
  | { kind: "block"; text: string }
  /** A done row: `Complete · merged`, `Abandoned · duplicate of AOS-8` (no glyph: the stage cell
   *  already carries the tick or the crimson stop). */
  | { kind: "outcome"; outcome: Outcome; word: string; text: string }
  /** An Inbox row at rest: nothing yet (no context summary; contract request 20). Add context
   *  shows here on hover / focus / open. */
  | { kind: "empty" };

/** The Now column (DESIGN §6 "Now/next"; there is no "next" in our model). */
export function nowCell(t: NowTicket): NowCell {
  const outcome = outcomeOf(t);
  if (outcome) {
    return {
      kind: "outcome",
      outcome,
      word: outcomeWords[outcome],
      text: t.resolution?.note ?? t.now.text,
    };
  }
  if (t.attention === "inbox") {
    return t.dispatch_block ? { kind: "block", text: t.dispatch_block.text } : { kind: "empty" };
  }
  if (t.attention === "running") {
    return {
      kind: "run",
      actor: t.now.actor ?? statusWords.run,
      // While running, now.text is the TASKS.md item in progress: drop the agent's own number.
      text: planText(t.now.text),
      meter: meterOf(t),
    };
  }
  const mark = statusMark(t);
  if (t.dispatch_block) return { kind: "mark", mark, text: t.dispatch_block.text, flag: true };
  const text = t.attention === "blocked" ? statusText(t) : t.now.text;
  return { kind: "mark", mark, text, flag: false };
}

// ------------------------------------------------------------------ the expansion

/** The expansion's first column: its label and the one line that says what happens now. */
export function moveText(t: NowTicket): { label: string; lead: string } {
  const gate = parkedGate(t);
  switch (t.attention) {
    case "needs_you":
      return {
        label: "Your move",
        lead: gate
          ? [gate.label, t.now.text].filter(Boolean).join(": ")
          : t.now.text || "Your move",
      };
    case "waiting": {
      const who = waitingPerson(t);
      const lead = [who ? firstName(who) : "Someone", gate?.label ?? t.now.text].filter(Boolean);
      return { label: "Waiting on", lead: lead.join(" · ") };
    }
    case "done": {
      // "Abandoned" over the note (or the server's status line).
      const label = outcomeWord(t) ?? attentionWords.done;
      return { label, lead: t.resolution?.note ?? (statusText(t) || label) };
    }
    // "Add context, then dispatch" is said once, in the Inbox group head.
    case "inbox":
      return { label: "Now", lead: t.dispatch_block?.text ?? (t.now.text || words.inboxNote) };
    default:
      return { label: "Now", lead: statusText(t) || attentionWords[t.attention] };
  }
}

/** "Owner Sam · 5 stages" under the lead. */
export function ownerLine(t: Pick<Ticket, "owner">, stages: string): string {
  return `Owner ${t.owner ? t.owner.display_name : "nobody"} · ${stages}`;
}

/** The live label over the run: "Build · Run #42 · 3:12 · 3/5" (parts the server hasn't sent are dropped). */
export function runLine(t: NowTicket, nowMs: number): string {
  const meter = meterOf(t);
  const secs = runSeconds(t.latest_run, nowMs);
  const run = runNumber(t);
  return [
    t.now.actor ?? "Run",
    run !== null ? `Run #${String(run)}` : null,
    secs !== null ? duration(secs) : null,
    meter ? meterOfText(meter.k, meter.n, meter.state) : null,
  ]
    .filter(Boolean)
    .join(" · ");
}

/** "Talk · 1 message", "Talk · 5 messages". */
export function talkLabel(n: number): string {
  return `${words.talk} · ${String(n)} ${n === 1 ? "message" : "messages"}`;
}
