// The stage glyph / stepper model: one segment per stage of THIS ticket's pipeline, with Inbox and
// Done bookends, and a diamond for each gate (DESIGN §13.2). Built only from server fields
// (`rail`, `phase_track`, `attention`, `latest_run`); this module maps them, never derives them.
// Gate diamonds arrive with RailStep.gate (CONTRACT_REQUESTS #1); until then `gate` stays unset.
import type { Schemas } from "../api/client";

export type StepState = "done" | "current" | "upcoming" | "skipped" | "failed" | "stale";
export type GateState = "pending" | "passed" | "waiting" | "you";

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

type Ticket = Pick<Schemas["TicketSummary"], "rail" | "phase_track" | "attention" | "latest_run">;

const RAIL: Record<Schemas["RailStep"]["state"], StepState> = {
  done: "done",
  current: "current",
  pending: "upcoming",
  failed: "failed",
  stale: "stale",
};

const BOOKEND: Record<Schemas["PhaseStep"]["state"], StepState> = {
  done: "done",
  current: "current",
  pending: "upcoming",
  failed: "failed",
  stale: "stale",
  skipped: "skipped",
};

function bookend(t: Ticket, phase: "inbox" | "done", name: string): GlyphStage {
  const step = t.phase_track.find((p) => p.phase === phase);
  return { name, state: step ? BOOKEND[step.state] : "upcoming" };
}

export function stageModel(t: Ticket): GlyphModel {
  const stages: GlyphStage[] = [
    bookend(t, "inbox", "Inbox"),
    ...t.rail.map((s) => ({ name: s.name, state: RAIL[s.state] })),
    bookend(t, "done", "Done"),
  ];
  const parked = stages.find((s) => s.gate?.state === "you" || s.gate?.state === "waiting");
  const current = stages.find((s) => s.state === "current" || s.state === "failed");
  return {
    stages,
    label:
      parked?.gate?.short ??
      current?.name ??
      stages.findLast((s) => s.state === "done")?.name ??
      "",
    live: t.attention === "moving" && t.latest_run?.status === "running",
  };
}
