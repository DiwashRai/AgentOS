// The stage glyph / stepper model: one segment per stage of THIS ticket's pipeline (disabled
// stages included, as skipped), with Inbox and Done bookends, and a diamond for each gate
// (DESIGN §13.2). Built only from server fields (`rail` incl. `RailStep.gate`, `phase_track`,
// `attention`); this module maps them, never derives them.
import type { Schemas } from "../api/client";
import { firstName } from "./format";
import { outcomeOf } from "./outcome";

export type StepState = "done" | "current" | "upcoming" | "skipped" | "failed";
export type GateState = Schemas["GateInfo"]["state"];

export interface GlyphGate {
  label: string; // "Plan sign-off"
  short: string; // "Sign-off": used in glyph labels and mono captions
  state: GateState;
  person?: string | undefined; // short name of who it waits on (state "waiting")
}

export interface GlyphStage {
  name: string;
  state: StepState;
  /** Gate after this stage, if any. */
  gate?: GlyphGate | undefined;
}

export interface GlyphModel {
  stages: GlyphStage[];
  /** The mono label: the parked gate's short name, else the current stage's name. */
  label: string;
  /** A run is on a runner: the current segment and label use the live colour. */
  live: boolean;
}

type Ticket = Pick<Schemas["TicketSummary"], "rail" | "phase_track" | "attention"> &
  Partial<Pick<Schemas["TicketSummary"], "state" | "resolution">>;

// The server's `stale` (done before, but after the latest run's segment, so it will run again)
// has no form in the design: it draws as upcoming.
const RAIL: Record<Schemas["RailStep"]["state"], StepState> = {
  done: "done",
  current: "current",
  pending: "upcoming",
  failed: "failed",
  stale: "upcoming",
  skipped: "skipped",
};

const BOOKEND: Record<Schemas["PhaseStep"]["state"], StepState> = {
  done: "done",
  current: "current",
  pending: "upcoming",
  failed: "failed",
  stale: "upcoming",
  skipped: "skipped",
};

function glyphGate(g: Schemas["GateInfo"]): GlyphGate {
  return {
    label: g.label,
    short: g.short,
    state: g.state,
    person: g.person ? firstName(g.person) : undefined,
  };
}

function bookend(t: Ticket, phase: "inbox" | "done", name: string): GlyphStage {
  const step = t.phase_track.find((p) => p.phase === phase);
  return { name, state: step ? BOOKEND[step.state] : "upcoming" };
}

export function stageModel(t: Ticket): GlyphModel {
  const stages: GlyphStage[] = [
    bookend(t, "inbox", "Inbox"),
    ...t.rail.map((s): GlyphStage => ({
      name: s.name,
      state: RAIL[s.state],
      gate: s.gate ? glyphGate(s.gate) : undefined,
    })),
    bookend(t, "done", "Done"),
  ];
  // An abandoned ticket never reached Done: it stops (crimson) at the first stage it didn't finish.
  const abandoned =
    outcomeOf({
      attention: t.attention,
      state: t.state ?? "open",
      resolution: t.resolution ?? null,
    }) === "abandoned";
  if (abandoned) {
    const last = stages.length - 1;
    const open = stages.map((s, i): GlyphStage => (i === last ? { ...s, state: "upcoming" } : s));
    const stop = open.findIndex((s, i) => i > 0 && s.state !== "done" && s.state !== "skipped");
    return {
      stages: open.map((s, i): GlyphStage => (i === stop ? { ...s, state: "failed" } : s)),
      label: "Closed",
      live: false,
    };
  }
  const parked = stages.find((s) => s.gate?.state === "you" || s.gate?.state === "waiting");
  const current = stages.find((s) => s.state === "current" || s.state === "failed");
  return {
    stages,
    label:
      parked?.gate?.short ??
      current?.name ??
      stages.findLast((s) => s.state === "done")?.name ??
      "",
    live: t.attention === "running",
  };
}
