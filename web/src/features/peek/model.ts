// Pure helpers for the Peek: description and plan display, the stage count, and the Tab trap.
import type { Schemas } from "../../api/client";

/** Markdown-ish text → plain text: drops code ticks, emphasis, heading marks and link targets. */
export function plainText(md: string): string {
  return md
    .replace(/!?\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/`+([^`]*)`+/g, "$1")
    .replace(/(\*\*|__)(.+?)\1/g, "$2")
    .replace(/(^|[^\w*])\*(?!\s)([^*]+?)\*/g, "$1$2")
    .replace(/^\s{0,3}(#{1,6}|>|[-*+]|\d+\.)\s+/gm, "")
    .replace(/\s+/g, " ")
    .trim();
}

/** The description's first paragraph, as plain text ("" when there is none). */
export function firstParagraph(description: string): string {
  for (const para of description.split(/\n\s*\n/)) {
    const text = plainText(para);
    if (text) return text;
  }
  return "";
}

export interface PlanView {
  items: Schemas["PlanItem"][];
  /** Items not shown ("+N more"). */
  more: number;
  done: number;
  total: number;
}

/** The plan as the peek lists it: the first `max` items, plus how many are done in all. */
export function planView(plan: readonly Schemas["PlanItem"][], max = 8): PlanView {
  const done = plan.filter((p) => p.done).length;
  // "+1 more" would take a line anyway, so show the item instead.
  const shown = plan.length <= max + 1 ? plan.length : max;
  return { items: plan.slice(0, shown), more: plan.length - shown, done, total: plan.length };
}

/** "5 stages", "5 stages · 1 skipped": the same wording as the Ticket page's byline. */
export function stagesText(rail: readonly Pick<Schemas["RailStep"], "state">[]): string {
  const n = rail.length;
  const skipped = rail.filter((s) => s.state === "skipped").length;
  const stages = `${String(n)} ${n === 1 ? "stage" : "stages"}`;
  return skipped ? `${stages} · ${String(skipped)} skipped` : stages;
}

/**
 * Tab trap: given the focused element's index among the panel's focusables (-1 = the panel
 * itself or outside), return the index to move focus to, or null to let the browser move it.
 */
export function trapIndex(current: number, count: number, shift: boolean): number | null {
  if (count === 0) return -1;
  if (current < 0) return shift ? count - 1 : 0;
  if (!shift && current === count - 1) return 0;
  if (shift && current === 0) return count - 1;
  return null;
}
