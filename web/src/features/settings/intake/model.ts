// Settings › Intake, pure: the draft list of queries (each with a client-side `key` until the
// server gives it an id), the PUT body, validation that mirrors the server (models.py
// IntakeQueryIn / IntakeSettingsUpdate), and what a row's "Test query" line says.
import type { Schemas } from "../../../api/client";
import { describeSaveError } from "../../../editor/save";

export type IntakeSettings = Schemas["IntakeSettings"];
export type IntakeSettingsUpdate = Schemas["IntakeSettingsUpdate"];
export type IntakePreview = Schemas["IntakePreview"];
export type PreviewItem = Schemas["IntakeItem"];

/** Server limits (models.py MAX_INTAKE_QUERIES, INTAKE_NAME_MAX, JQL_MAX). */
export const MAX_QUERIES = 50;
export const NAME_MAX = 100;
export const JQL_MAX = 4000;
/** The preview lists at most this many issues (the server sends ≤ 5). */
export const PREVIEW_ITEMS = 5;

/** One query in the draft. `key` is the React key and preview identity: `id:<id>` for a saved
 *  query, `new:<n>` for one added here (its `id` stays null; the server makes one from the name). */
export interface QueryRow {
  key: string;
  id: string | null;
  name: string;
  jql: string;
}

export type IntakeForm = readonly QueryRow[];

export function formOf(settings: Pick<IntakeSettings, "queries">): IntakeForm {
  return settings.queries.map((q) => ({ key: `id:${q.id}`, id: q.id, name: q.name, jql: q.jql }));
}

/** Same content, ignoring the client keys (so undoing an edit makes the draft clean again). */
export function sameForm(a: IntakeForm, b: IntakeForm): boolean {
  return (
    a.length === b.length &&
    a.every((r, i) => {
      const o = b[i];
      return r.id === o?.id && r.name === o.name && r.jql === o.jql;
    })
  );
}

/** The PUT body: in tab order, trimmed (the server strips too), without the client keys. */
export function toBody(form: IntakeForm, baseVersion: number): IntakeSettingsUpdate {
  return {
    queries: form.map((r) => ({ id: r.id, name: r.name.trim(), jql: r.jql.trim() })),
    base_version: baseVersion,
  };
}

/** Append an empty new query with the next free `new:<n>` key. */
export function addQuery(form: IntakeForm): IntakeForm {
  let n = 0;
  for (const r of form) {
    const m = /^new:(\d+)$/.exec(r.key);
    if (m) n = Math.max(n, Number(m[1]));
  }
  return [...form, { key: `new:${String(n + 1)}`, id: null, name: "", jql: "" }];
}

export function removeQuery(form: IntakeForm, index: number): IntakeForm {
  return form.filter((_, i) => i !== index);
}

/** Swap with the neighbour above (-1) or below (1); out of range = unchanged. */
export function moveQuery(form: IntakeForm, index: number, delta: -1 | 1): IntakeForm {
  const to = index + delta;
  const a = form[index];
  const b = form[to];
  if (a === undefined || b === undefined) return form;
  const next = [...form];
  next[index] = b;
  next[to] = a;
  return next;
}

export function patchQuery(
  form: IntakeForm,
  index: number,
  patch: Partial<Pick<QueryRow, "name" | "jql">>,
): IntakeForm {
  return form.map((r, i) => (i === index ? { ...r, ...patch } : r));
}

// ------------------------------------------------------------------ validation

export interface RowIssues {
  name?: string;
  jql?: string;
}

export interface Issues {
  form: string[];
  /** By index, one entry per row (empty when the row is fine). */
  rows: RowIssues[];
}

/** The JQL problem the server would reject (blank or too long), or undefined. Also gates Test. */
export function jqlIssue(jql: string): string | undefined {
  const v = jql.trim();
  if (v === "") return "Write a JQL query";
  if (v.length > JQL_MAX) return `At most ${String(JQL_MAX)} characters`;
  return undefined;
}

function nameIssue(name: string): string | undefined {
  const v = name.trim();
  if (v === "") return "Name the query (it heads its Intake group)";
  if (v.length > NAME_MAX) return `At most ${String(NAME_MAX)} characters`;
  return undefined;
}

export function validate(form: IntakeForm): Issues {
  const rows = form.map((r) => {
    const issues: RowIssues = {};
    const name = nameIssue(r.name);
    const jql = jqlIssue(r.jql);
    if (name !== undefined) issues.name = name;
    if (jql !== undefined) issues.jql = jql;
    return issues;
  });
  const formIssues =
    form.length > MAX_QUERIES ? [`At most ${String(MAX_QUERIES)} queries`] : ([] as string[]);
  return { form: formIssues, rows };
}

export function issueCount(issues: Issues): number {
  return issues.form.length + issues.rows.reduce((n, r) => n + Object.keys(r).length, 0);
}

// ------------------------------------------------------------------ Test query

/** What a row shows under its JQL after "Test query". */
export type PreviewLine =
  | { kind: "none" }
  | { kind: "pending" }
  | { kind: "error"; text: string }
  | { kind: "empty" }
  | { kind: "items"; count: string; items: readonly PreviewItem[]; more: string | null };

export interface PreviewState {
  /** The JQL the last Test ran (the mutation's variables), if any. */
  ran: string | undefined;
  pending: boolean;
  data: IntakePreview | undefined;
  error: unknown;
}

/**
 * The row's preview line. A result only counts while the row's JQL is still the one tested, so an
 * edit (or Discard, or Reload) hides it at once, even before the row resets it.
 */
export function previewLine(state: PreviewState, jql: string): PreviewLine {
  if (state.ran === undefined || state.ran !== jql.trim()) return { kind: "none" };
  if (state.pending) return { kind: "pending" };
  if (state.error !== null && state.error !== undefined) {
    return { kind: "error", text: describeSaveError(state.error).text };
  }
  const data = state.data;
  if (!data) return { kind: "none" };
  if (data.error) return { kind: "error", text: data.error };
  if (data.total === 0 && data.items.length === 0) return { kind: "empty" };
  const items = data.items.slice(0, PREVIEW_ITEMS);
  const total = Math.max(data.total, items.length);
  return {
    kind: "items",
    count: `${String(total)} to take in`,
    items,
    more: total > items.length ? `first ${String(items.length)} shown` : null,
  };
}
