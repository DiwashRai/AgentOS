// Pure display helpers for the TASKS.md plan (`TicketDetail.plan`). Display only: they never
// change what is saved, and never derive board state.

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
