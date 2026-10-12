// Pure display helpers for StageGlyph and Stepper. They only lay out the GlyphModel the domain
// built (src/domain/stage.ts); they never decide a stage's or gate's state.
import type { GateState, GlyphGate, GlyphModel, GlyphStage } from "../domain/stage";

/** A stage the stepper can caption "ADDED". GlyphModel has no `added` flag yet; extra field is optional. */
export type StepperStage = GlyphStage & { added?: boolean | undefined };
export type StepperModel = Omit<GlyphModel, "stages"> & { stages: StepperStage[] };

export type GlyphItem<S extends GlyphStage = GlyphStage> =
  | { type: "stage"; stage: S; index: number }
  | { type: "gate"; gate: GlyphGate; after: S; index: number };

/** Stages in order, each followed by its gate diamond if it has one. `index` is the stage's. */
export function glyphItems<S extends GlyphStage>(stages: S[]): GlyphItem<S>[] {
  const items: GlyphItem<S>[] = [];
  stages.forEach((stage, index) => {
    items.push({ type: "stage", stage, index });
    if (stage.gate) items.push({ type: "gate", gate: stage.gate, after: stage, index });
  });
  return items;
}

/** A gate is active when the ticket is parked at it (on you or on someone). */
export function isActiveGate(state: GateState): boolean {
  return state === "you" || state === "waiting";
}

/** The gate the ticket is parked at, if any. */
export function parkedGate(model: Pick<GlyphModel, "stages">): GlyphGate | undefined {
  return model.stages.find((st) => st.gate && isActiveGate(st.gate.state))?.gate;
}

const STAGE_SUFFIX: Partial<Record<GlyphStage["state"], string>> = {
  skipped: " (skipped)",
  failed: " (blocked)",
};

/** The glyph's `title`: "Inbox → Plan → ◆ Plan sign-off → Build → … → Done", "(skipped)" on skipped stages. */
export function glyphTitle(model: Pick<GlyphModel, "stages">): string {
  return glyphItems(model.stages)
    .map((it) =>
      it.type === "gate"
        ? `◆ ${it.gate.label}`
        : it.stage.name + (STAGE_SUFFIX[it.stage.state] ?? ""),
    )
    .join(" → ");
}

/** The stage glyph's fixed slot (refresh HANDOFF §5): 81px, so the label beside it (9px gap) sits
 *  90px into its cell in every row. */
export const GLYPH_SLOT = 81;

// Segment geometry (StageGlyph.module.css): stage width, current / stopped width, gate box incl. its
// 1px side margins, gap. Keep these in step with the CSS.
const GEOMETRY = {
  sm: { stage: 8, now: 11, gate: 8, gap: 2 },
  xs: { stage: 7, now: 10, gate: 7, gap: 2 },
} as const;

/** The glyph's natural width in px at a size (no label). */
export function glyphWidth(model: Pick<GlyphModel, "stages">, size: "sm" | "xs"): number {
  const g = GEOMETRY[size];
  const items = glyphItems(model.stages);
  const widths = items.map((it) => {
    if (it.type === "gate") return g.gate;
    const big = it.stage.state === "current" || it.stage.state === "failed";
    return big ? g.now : g.stage;
  });
  return widths.reduce((a, b) => a + b, 0) + Math.max(items.length - 1, 0) * g.gap;
}

/** The size a row glyph draws at: sm when it fits the 81px slot, else xs (longer pipelines). */
export function fitGlyphSize(model: Pick<GlyphModel, "stages">): "sm" | "xs" {
  return glyphWidth(model, "sm") <= GLYPH_SLOT ? "sm" : "xs";
}

/**
 * The ticket is finished: its last stage (the Done bookend) is reached and nothing failed. The glyph
 * then draws all green and the stepper captions the last stage "Complete".
 */
export function isFinished(model: Pick<GlyphModel, "stages">): boolean {
  const last = model.stages[model.stages.length - 1];
  if (!last || (last.state !== "current" && last.state !== "done")) return false;
  return !model.stages.some((st) => st.state === "failed");
}

/** A skipped stage still ahead of the ticket (nothing after it has been reached). */
export function isAhead(model: Pick<GlyphModel, "stages">, index: number): boolean {
  return !model.stages
    .slice(index + 1)
    .some((st) => st.state === "done" || st.state === "current" || st.state === "failed");
}

/** Stepper: with more than 2 gates, or in the compact variant, inactive gates collapse to a bare diamond. */
export function collapsesGates(model: Pick<GlyphModel, "stages">, compact: boolean): boolean {
  return compact || model.stages.filter((st) => st.gate).length > 2;
}

export function isBareGate(gate: GlyphGate, collapse: boolean): boolean {
  return collapse && !isActiveGate(gate.state);
}

/** The `title` of a gate in the stepper: its label, plus who or "passed" when it is shown bare. */
export function gateTitle(gate: GlyphGate, bare: boolean): string {
  if (!bare) return gate.label;
  if (gate.state === "passed") return `${gate.label} · passed`;
  return gate.person ? `${gate.label} · ${gate.person}` : gate.label;
}

/** The caption under a gate the ticket is parked at on you (refresh stepper). */
export const YOUR_MOVE = "Your move";

/**
 * The stepper's caption under a stage (sentence case). The final stage (the Done bookend) is reached
 * only when the ticket is done; then it reads "Complete" (refresh), unless something failed.
 */
export function stageCaption(
  stage: StepperStage,
  isLast: boolean,
  live: boolean,
  finished = false,
): string {
  if (isLast && finished) return "Complete";
  switch (stage.state) {
    case "skipped":
      return "Skipped";
    case "failed":
      return "Blocked";
    case "current":
      if (live) return "Live";
      return isLast ? "" : "Now";
    default:
      return stage.added ? "Added" : "";
  }
}
