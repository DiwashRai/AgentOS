// Pure display helpers for the TASKS.md plan (`TicketDetail.plan`) and the description's text.
// Display only: they never change what is saved, and never derive board state.

/** A plan item as the views read it. `section` is the nearest markdown heading above the item
 *  (contract request 8); optional so today's `Schemas["PlanItem"]` fits and it starts working
 *  when the field lands. */
export interface PlanLike {
  text: string;
  done: boolean;
  section?: string | null;
}

/** The item's text without the agent's own leading number ("1. Baseline" → "Baseline"): the
 *  views number items themselves. `1.`, `1)`, `12.` followed by whitespace. */
export function planText(text: string): string {
  return text.replace(/^\s*\d+[.)]\s+/, "");
}

export interface PlanSection<T extends PlanLike> {
  /** The heading, or null for items above the first heading. */
  section: string | null;
  /** Each item with its index in the whole plan (for the `01` numbering). */
  items: { item: T; index: number }[];
  done: number;
  total: number;
}

/** Consecutive items under the same heading, in plan order. A heading that recurs later starts
 *  a new section. */
export function planSections<T extends PlanLike>(items: readonly T[]): PlanSection<T>[] {
  const out: PlanSection<T>[] = [];
  items.forEach((item, index) => {
    const section = item.section ?? null;
    let cur = out.at(-1);
    if (cur?.section !== section) {
      cur = { section, items: [], done: 0, total: 0 };
      out.push(cur);
    }
    cur.items.push({ item, index });
    cur.total += 1;
    if (item.done) cur.done += 1;
  });
  return out;
}

/** Section headers show only when the plan has 2+ sections; otherwise it reads as one list. */
export function showsSections(sections: readonly PlanSection<PlanLike>[]): boolean {
  return sections.length > 1;
}

/** "2 of 5": a section's own count. */
export function sectionCount(s: Pick<PlanSection<PlanLike>, "done" | "total">): string {
  return `${String(s.done)} of ${String(s.total)}`;
}

// ------------------------------------------------------------------ the checklist view

/** A plan item as every checklist draws it. */
export interface PlanItemView extends PlanLike {
  /** Plain text, without the agent's number or markdown. */
  text: string;
  /** The item without the agent's number, markdown kept (for a title). */
  raw: string;
}

export interface PlanView {
  /** The items shown, in plan order. */
  items: PlanItemView[];
  /** The shown items under their headings; `headed` says whether to draw the heads. */
  sections: PlanSection<PlanItemView>[];
  headed: boolean;
  /** Items not shown ("+N more"). */
  more: number;
  /** Done and total over the whole plan (the count matches its items). */
  done: number;
  total: number;
  /** The bar's fill, 0–100 (whole percent). */
  pct: number;
}

/** The plan as a checklist: the first `max` items (all without one), counted over the whole plan.
 *  Null for no plan, an empty one included, so no list says "0 of 0 done". */
export function planView(
  plan: readonly PlanLike[] | null | undefined,
  max?: number,
): PlanView | null {
  if (!plan || plan.length === 0) return null;

  // "+1 more" would take a line anyway, so show the item instead.
  const shown = max === undefined || plan.length <= max + 1 ? plan.length : max;
  const items = plan.slice(0, shown).map((p): PlanItemView => ({
    text: plainText(planText(p.text)),
    raw: planText(p.text),
    done: p.done,
    section: p.section ?? null,
  }));
  const sections = planSections(items);

  const done = plan.filter((p) => p.done).length;
  const total = plan.length;
  return {
    items,
    sections,
    headed: showsSections(sections),
    more: total - shown,
    done,
    total,
    pct: Math.round((done / total) * 100),
  };
}

// ------------------------------------------------------------------ description text

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

/** Paragraphs split on blank lines; single newlines stay inside a paragraph. */
export function paragraphs(text: string): string[] {
  return text
    .replace(/\r\n?/g, "\n")
    .split(/\n\s*\n/)
    .map((p) => p.trim())
    .filter(Boolean);
}

/** The description's lead: its first paragraph as plain text ("" when there is none). The body
 *  continues from `paragraphs(text)[1]`. */
export function firstParagraph(text: string): string {
  return plainText(paragraphs(text)[0] ?? "");
}
