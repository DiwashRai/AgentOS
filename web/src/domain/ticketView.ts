// The ticket view-model: the one place queue, compact, peek and the Ticket page read a ticket's run
// line, gate, stage count, source word, row look, status mark and line, outcome and wait age. Pure
// selection over server fields (`attention`, `rail`, `now`, `latest_run`, `attention_since`,
// `dispatch_block`, `resolution`, `source`); it never derives board state. Each view places the
// parts; the same ticket gives the same strings everywhere.
import type { Schemas } from "../api/client";
import { type ClientWait, clientWait } from "./clients";
import { age, duration, firstName, runSeconds } from "./format";
import { since } from "./groups";
import { type MeterState, meterOfText } from "./meter";
import { type DisplayKind, OUTCOME_KIND, type Outcome, outcomeOf } from "./outcome";
import { planText } from "./plan";
import { stageModel } from "./stage";
import { statusKind } from "./status";
import { outcomeWords, sourceNames, statusWords } from "./words";

type Ticket = Schemas["TicketSummary"];
type Person = Schemas["Person"];

const plural = (n: number, one: string, many: string) => `${String(n)} ${n === 1 ? one : many}`;

// ------------------------------------------------------------------ run line

export interface MeterModel {
  k: number;
  n: number;
  state: MeterState;
  /** "3/5" */
  text: string;
}

export interface RunLine {
  /** The running stage (`now.actor`), else "Run". */
  stage: string;
  /** `now.run_id`, else the latest run's id. */
  run: number | null;
  /** "Build · run #42"; "Run #42" without a stage; "Run" with neither. */
  label: string;
  /** Whole seconds since the run started; null before it has. */
  seconds: number | null;
  /** "3:12"; null before the run has started. */
  elapsed: string | null;
  meter: MeterModel | null;
  /** The step in progress (`now.text` without the agent's own number). */
  step: string;
  /** A client run not started yet: whose client it waits for, online or not. */
  wait: ClientWait | null;
  /** The label with the parts the server sent: "Build · run #42 · 3:12 · 3/5". */
  line: string;
}

type RunTicket = Pick<Ticket, "attention" | "now" | "latest_run">;

/** The run meter from `now.progress`: k = done + 1 capped at total while running (DESIGN §13.4);
 *  queued = all outlined. Null when the server sends no progress, or nothing runs. */
function meterOf(t: RunTicket): MeterModel | null {
  const p = t.now.progress;
  if (!p || p.total <= 0) return null;

  let meter: Omit<MeterModel, "text">;
  if (t.attention === "queued") meter = { k: 0, n: p.total, state: "queued" };
  else if (t.attention === "running")
    meter = { k: Math.min(p.done + 1, p.total), n: p.total, state: "running" };
  else return null;

  return { ...meter, text: meterOfText(meter.k, meter.n, meter.state) };
}

/** The run as every view shows it. `now` is the clock in ms; null leaves the elapsed time out. */
export function runLine(t: RunTicket, now: number | null): RunLine {
  const actor = t.now.actor;
  const run = t.now.run_id ?? t.latest_run?.id ?? null;
  const label =
    run === null
      ? (actor ?? "Run")
      : actor
        ? `${actor} · run #${String(run)}`
        : `Run #${String(run)}`;

  const seconds = now === null ? null : runSeconds(t.latest_run, now);
  const elapsed = seconds === null ? null : duration(seconds);
  const meter = meterOf(t);

  return {
    stage: actor ?? "Run",
    run,
    label,
    seconds,
    elapsed,
    meter,
    step: planText(t.now.text),
    wait: clientWait(t),
    line: [label, elapsed, meter?.text].filter(Boolean).join(" · "),
  };
}

// ------------------------------------------------------------------ gate

export interface GateView {
  /** "Plan sign-off" */
  label: string;
  /** "Sign-off" */
  short: string;
  state: "you" | "waiting";
  /** Who it waits on: the gate's person, else `now.person`. */
  who: Person | null;
  /** That person's first name, else "Someone". */
  person: string;
  /** The rail stage the gate sits after. */
  stage: string;
  /** That stage's index in `rail`. */
  index: number;
}

/** The gate the server says the ticket is parked at (on you or on someone), if any. */
export function gate(t: Pick<Ticket, "rail" | "now">): GateView | null {
  const index = t.rail.findIndex((s) => s.gate?.state === "you" || s.gate?.state === "waiting");
  const step = t.rail[index];
  const g = step?.gate;
  if (!step || !g || (g.state !== "you" && g.state !== "waiting")) return null;

  const who = g.person ?? t.now.person;
  return {
    label: g.label,
    short: g.short,
    state: g.state,
    who,
    person: who ? firstName(who) : "Someone",
    stage: step.name,
    index,
  };
}

// ------------------------------------------------------------------ stage count

export interface StageCount {
  stages: number;
  gates: number;
  skipped: number;
  /** "7 stages · 1 gate · 1 skipped"; zero parts left out. */
  text: string;
}

/** The pipeline count from the stepper's own model (Inbox and Done bookends included, skipped
 *  stages still stages, gates apart), so the glyph, the stepper and every count describe one
 *  pipeline (refresh HANDOFF §5 "Pipeline byline"). */
export function stageCount(
  t: Pick<Ticket, "rail" | "phase_track" | "attention" | "state" | "resolution">,
): StageCount {
  const { stages } = stageModel(t);
  const gates = stages.filter((s) => s.gate).length;
  const skipped = stages.filter((s) => s.state === "skipped").length;

  const text = [
    plural(stages.length, "stage", "stages"),
    gates ? plural(gates, "gate", "gates") : "",
    skipped ? `${String(skipped)} skipped` : "",
  ]
    .filter(Boolean)
    .join(" · ");
  return { stages: stages.length, gates, skipped, text };
}

// ------------------------------------------------------------------ source

/** Where the ticket came from: "Jira", plus the external id only when it differs from our key (an
 *  imported Jira ticket keeps its Jira key, so the key beside it already says it). Null for our own
 *  tickets. */
export function sourceWord(t: Pick<Ticket, "key" | "source">): string | null {
  if (!t.source) return null;
  const name = sourceNames[t.source.source];
  return t.source.external_id === t.key ? name : `${name} ${t.source.external_id}`;
}

// ------------------------------------------------------------------ row look

/** The 3px status edge (refresh HANDOFF §4): you blue, running vermilion, blocked and abandoned
 *  crimson, complete green; inbox, queued and waiting a quiet rule. */
export type RowEdge = "you" | "live" | "blocked" | "done" | "quiet";

/** A row's surface and edge: raised for in flight or needing action, flat for settled (done,
 *  abandoned included). Over `attention` and the outcome only. */
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
      return { flat: false, edge: "quiet" };
  }
}

// ------------------------------------------------------------------ status

export interface MarkModel {
  kind: DisplayKind;
  name?: string | undefined;
  ask?: string | undefined;
  /** Who a waiting ticket waits on (for a face beside the mark). */
  person?: Person | null | undefined;
}

type MarkTicket = Pick<Ticket, "attention" | "rail" | "now" | "state" | "resolution">;

/** The status mark with its ask. A done ticket shows its outcome: Complete or Abandoned. */
export function statusMark(t: MarkTicket): MarkModel {
  const outcome = outcomeOf(t);
  if (outcome) return { kind: OUTCOME_KIND[outcome], name: outcomeWords[outcome] };

  const kind = statusKind(t);
  const parked = gate(t);
  switch (kind) {
    case "you":
      return { kind, ask: parked?.state === "you" ? parked.label : "Your move" };
    case "wait": {
      const who = parked ? parked.who : t.now.person;
      return { kind, name: who ? firstName(who) : "Someone", ask: parked?.label, person: who };
    }
    case "run":
      return { kind, name: t.now.actor ?? statusWords.run };
    case "blk":
    case "done": // unreachable: a done ticket always has an outcome
    case "queued":
    case "idle":
      return { kind, name: statusWords[kind] };
  }
}

export interface StatusLine {
  /** What the line says: the dispatch block, else the reason. */
  text: string;
  /** The text is the server's dispatch block ("Set a base branch first"), which needs action. */
  flag: boolean;
  /** The status line without the block: `now.text`, joined with the run's error when blocked. */
  reason: string;
}

/** The line under a status mark. When the server says a dispatch would be refused
 *  (`dispatch_block`) that comes first, flagged; otherwise the reason. */
export function statusLine(
  t: Pick<Ticket, "attention" | "now" | "latest_run" | "dispatch_block">,
): StatusLine {
  const err = t.attention === "blocked" ? t.latest_run?.error : null;
  const reason = [t.now.text, err].filter(Boolean).join(" · ");

  if (t.dispatch_block) return { text: t.dispatch_block.text, flag: true, reason };
  return { text: reason, flag: false, reason };
}

export interface OutcomeLine {
  outcome: Outcome;
  /** "Complete" / "Abandoned" */
  word: string;
  abandoned: boolean;
  /** The resolution's note, trimmed; null without one. */
  note: string | null;
}

/** How a done ticket ended, with its note. Null for a ticket that isn't done. */
export function outcomeLine(
  t: Pick<Ticket, "attention" | "state" | "resolution">,
): OutcomeLine | null {
  const outcome = outcomeOf(t);
  if (!outcome) return null;

  const note = t.resolution?.note?.trim();
  return {
    outcome,
    word: outcomeWords[outcome],
    abandoned: outcome === "abandoned",
    note: note === undefined || note === "" ? null : note,
  };
}

/** The age text: "now" while a run is on a runner, else how long the ticket has been in its
 *  attention ("38m", "3h 05m"). */
export function waitAge(t: Pick<Ticket, "attention" | "attention_since">, now: number): string {
  return t.attention === "running" ? "now" : age(since(t), now);
}
