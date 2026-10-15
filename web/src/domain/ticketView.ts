// The ticket view-model: the run line, gate, stage count, source, row look, status mark and line,
// outcome and wait age every view reads, so the same ticket gives the same strings everywhere.
// Selection over server fields only; it never derives board state.
import type { Schemas } from "../api/client";
import { type ClientWait, clientWait } from "./clients";
import { age, duration, firstName, plural, runSeconds } from "./format";
import { since } from "./groups";
import { type MeterState, meterOfText } from "./meter";
import { type DisplayKind, OUTCOME_KIND, type Outcome, outcomeOf } from "./outcome";
import { planText } from "./plan";
import type { GlyphModel } from "./stage";
import { statusKind } from "./status";
import { outcomeWords, sourceNames, statusWords } from "./words";

type Ticket = Schemas["TicketSummary"];
type Person = Schemas["Person"];

// ------------------------------------------------------------------ run line

interface MeterModel {
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
  /** "3:12"; null before the run has started. */
  elapsed: string | null;
  meter: MeterModel | null;
  /** `now.text` without the agent's own number. */
  step: string;
  wait: ClientWait | null;
  /** "Build · run #42 · 3:12 · 3/5" */
  line: string;
}

type RunTicket = Pick<Ticket, "attention" | "now" | "latest_run">;

/** The run meter from `now.progress`: k = done + 1 capped at total while running (DESIGN §13.4),
 *  all outlined while queued, null otherwise. */
export function meterOf(t: RunTicket): MeterModel | null {
  const p = t.now.progress;
  if (!p || p.total <= 0) return null;

  let meter: Omit<MeterModel, "text">;
  if (t.attention === "queued") meter = { k: 0, n: p.total, state: "queued" };
  else if (t.attention === "running")
    meter = { k: Math.min(p.done + 1, p.total), n: p.total, state: "running" };
  else return null;

  return { ...meter, text: meterOfText(meter.k, meter.n, meter.state) };
}

/** `now` is the clock in ms; null leaves the elapsed time out. */
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
  state: "you" | "waiting";
  /** The gate's person, else `now.person`. */
  who: Person | null;
  /** That person's first name, else "Someone". */
  person: string;
  /** The rail stage the gate sits after, and its index in `rail`. */
  stage: string;
  index: number;
}

/** The gate the ticket is parked at (on you or on someone), if any. */
export function gate(t: Pick<Ticket, "rail" | "now">): GateView | null {
  const index = t.rail.findIndex((s) => s.gate?.state === "you" || s.gate?.state === "waiting");
  const step = t.rail[index];
  const g = step?.gate;
  if (!step || !g || (g.state !== "you" && g.state !== "waiting")) return null;

  const who = g.person ?? t.now.person;
  return {
    label: g.label,
    state: g.state,
    who,
    person: who ? firstName(who) : "Someone",
    stage: step.name,
    index,
  };
}

// ------------------------------------------------------------------ stage count

/** "7 stages · 1 gate · 1 skipped" from the stepper's own model, so the glyph, the stepper and
 *  the count describe one pipeline (refresh HANDOFF §5 "Pipeline byline"). */
export function stageCount({ stages }: Pick<GlyphModel, "stages">): string {
  const gates = stages.filter((s) => s.gate).length;
  const skipped = stages.filter((s) => s.state === "skipped").length;
  return [
    `${String(stages.length)} ${plural(stages.length, "stage")}`,
    gates ? `${String(gates)} ${plural(gates, "gate")}` : "",
    skipped ? `${String(skipped)} skipped` : "",
  ]
    .filter(Boolean)
    .join(" · ");
}

// ------------------------------------------------------------------ source

/** "Jira", plus the external id only when it differs from our key; null for our own tickets. */
export function sourceWord(t: Pick<Ticket, "key" | "source">): string | null {
  if (!t.source) return null;
  const name = sourceNames[t.source.source];
  return t.source.external_id === t.key ? name : `${name} ${t.source.external_id}`;
}

/** "Jira SCRUM-8", always with the external id; null for our own tickets. */
export function sourceRef(t: Pick<Ticket, "source">): string | null {
  if (!t.source) return null;
  return `${sourceNames[t.source.source]} ${t.source.external_id}`;
}

// ------------------------------------------------------------------ row look

/** The 3px status edge (refresh HANDOFF §4). */
export type RowEdge = "you" | "live" | "blocked" | "done" | "quiet";

/** Raised for in flight or needing action, flat for settled (abandoned included). */
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
  /** Who a waiting ticket waits on. */
  person?: Person | null | undefined;
}

type MarkTicket = Pick<Ticket, "attention" | "rail" | "now" | "state" | "resolution">;

/** The status mark with its ask; a done ticket shows its outcome. */
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

/** The line under a status mark: the server's dispatch block first, flagged, else the reason
 *  (`now.text`, with the run's error when blocked). */
export function statusLine(
  t: Pick<Ticket, "attention" | "now" | "latest_run" | "dispatch_block">,
): {
  text: string;
  flag: boolean;
  reason: string;
} {
  const err = t.attention === "blocked" ? t.latest_run?.error : null;
  const reason = [t.now.text, err].filter(Boolean).join(" · ");

  if (t.dispatch_block) return { text: t.dispatch_block.text, flag: true, reason };
  return { text: reason, flag: false, reason };
}

export interface OutcomeLine {
  outcome: Outcome;
  /** "Complete" / "Abandoned" */
  word: string;
  /** The resolution's note, trimmed; null without one. */
  note: string | null;
}

/** How a done ticket ended; null for a ticket that isn't done. */
export function outcomeLine(
  t: Pick<Ticket, "attention" | "state" | "resolution">,
): OutcomeLine | null {
  const outcome = outcomeOf(t);
  if (!outcome) return null;

  const note = t.resolution?.note?.trim();
  return {
    outcome,
    word: outcomeWords[outcome],
    note: note === undefined || note === "" ? null : note,
  };
}

/** "now" while a run is on a runner, else how long the ticket has been in its attention. */
export function waitAge(t: Pick<Ticket, "attention" | "attention_since">, now: number): string {
  return t.attention === "running" ? "now" : age(since(t), now);
}
