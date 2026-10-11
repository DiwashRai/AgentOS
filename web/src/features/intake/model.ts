// Pure selection and display helpers for Intake (contract request 14, design/intake-mock.html):
// the Space's JQL queries with their Jira candidates, each a "Take in" away from the Inbox. Over
// server fields only (`IntakeQuery`, `IntakeItem`) plus which keys this page has taken in.
import type { Schemas } from "../../api/client";
import { age, type Instant } from "../../domain/format";
import type { Query } from "../../domain/search";

export type IntakeQuery = Schemas["IntakeQuery"];
export type IntakeItem = Schemas["IntakeItem"];

/** Rows a group shows before "Show all" (which expands it in place). */
export const ROW_CAP = 8;

const TYPE_WORD: Record<IntakeItem["type"], string> = {
  feature: "Feature",
  bug: "Bug",
  improvement: "Improvement",
  task: "Task",
};

/** Every term is a case-insensitive substring of the key or the title (the top-bar `?q=`). */
export function matchesItem(item: Pick<IntakeItem, "key" | "title">, query: Query): boolean {
  if (query.length === 0) return true;
  const hay = `${item.key}\n${item.title}`.toLowerCase();
  return query.every((term) => hay.includes(term));
}

/** A group's rows under the search; the same array when there is no query. */
export function searchItems<T extends Pick<IntakeItem, "key" | "title">>(
  items: readonly T[],
  query: Query,
): readonly T[] {
  return query.length === 0 ? items : items.filter((i) => matchesItem(i, query));
}

/** A group's count: its listed candidates not taken in yet (a failing query has none). */
export function groupCount(
  items: readonly Pick<IntakeItem, "key">[],
  taken: ReadonlySet<string>,
): number {
  return items.filter((i) => !taken.has(i.key)).length;
}

/** The top bar's "N to take in": distinct listed candidates across every query, minus the ones
 *  taken in here (a taken-in row stops counting at once, even while it is still shown). An issue
 *  that matches two queries counts once. */
export function toTakeIn(queries: readonly IntakeQuery[], taken: ReadonlySet<string>): number {
  const keys = new Set<string>();
  for (const q of queries) for (const i of q.items) if (!taken.has(i.key)) keys.add(i.key);
  return keys.size;
}

/** The rows to draw: the first ROW_CAP unless expanded, and how many more "Show all" reveals. */
export function visibleRows<T>(
  items: readonly T[],
  expanded: boolean,
): { shown: readonly T[]; rest: number } {
  if (expanded || items.length <= ROW_CAP) return { shown: items, rest: 0 };
  return { shown: items.slice(0, ROW_CAP), rest: items.length - ROW_CAP };
}

/** When Jira has more candidates than the server listed: "73 match in Jira, newest 50 listed". */
export function capText(q: Pick<IntakeQuery, "total" | "items">): string | null {
  const listed = q.items.length;
  return q.total > listed
    ? `${String(q.total)} match in Jira, newest ${String(listed)} listed`
    : null;
}

/** The group head's summary, under the same condition: "50 of 73 match". */
export function headSummary(q: Pick<IntakeQuery, "total" | "items" | "error">): string | null {
  if (q.error) return null;
  return q.total > q.items.length ? `${String(q.items.length)} of ${String(q.total)} match` : null;
}

/** "Open in Jira ↗" for a query's JQL, or null when the server has no Jira URL. */
export function jqlHref(jiraUrl: string | null | undefined, jql: string): string | null {
  if (!jiraUrl) return null;
  return `${jiraUrl.replace(/\/+$/, "")}/issues/?jql=${encodeURIComponent(jql)}`;
}

/** The type word ("Bug"), and the priority only when it is high. */
export function typeText(item: Pick<IntakeItem, "type" | "priority">): {
  type: string;
  priority: string | null;
} {
  return { type: TYPE_WORD[item.type], priority: item.priority === "high" ? "High" : null };
}

/** Age since Jira last updated the issue: `now`, `25m`, `3h 10m`, `2d`. */
export function ageText(item: Pick<IntakeItem, "updated_at">, now: Instant): string {
  return age(item.updated_at, now);
}

/** A stable avatar id for a Jira assignee (no AgentOS id): a small hash of the name. */
export function nameId(name: string): number {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) | 0;
  return Math.abs(h);
}

/** The footer: "4 queries · 1 didn't answer · checked 2m ago". */
export function footNote(
  queries: readonly Pick<IntakeQuery, "error">[],
  checkedAt: Instant,
  now: Instant,
): string {
  const n = queries.length;
  const failing = queries.filter((q) => q.error).length;
  const a = age(checkedAt, now);
  return [
    `${String(n)} ${n === 1 ? "query" : "queries"}`,
    failing > 0 ? `${String(failing)} didn't answer` : null,
    a === "now" ? "checked just now" : `checked ${a} ago`,
  ]
    .filter(Boolean)
    .join(" · ");
}

/** What a group's body says when it has no rows to draw. */
export function emptyGroupText(q: Pick<IntakeQuery, "items">, searching: boolean): string {
  return searching && q.items.length > 0
    ? "Nothing here matches the search"
    : "Nothing new matches";
}
