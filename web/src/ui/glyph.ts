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

/**
 * The stepper's mono caption under a stage. The final stage (the Done bookend) is current only
 * when the ticket is done, and then carries no caption, as in the kit.
 */
export function stageCaption(stage: StepperStage, isLast: boolean, live: boolean): string {
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
