// Settings › Intake queries, pure: the form the page edits (the server's queries as keyed rows, so
// a row keeps its issues and its Test result across moves) and what a row's "Test query" line says.
import { ApiError, type Schemas } from "../../../api/client";
import type { FieldPath, SentKeys } from "../../../editor/issues";
import { keyed, keysOf, newKey, unkeyed, type Keyed } from "../../../editor/list";
import { describeSaveError } from "../../../editor/save";

export type IntakeRead = Schemas["IntakeSettings"];
export type IntakeDoc = Schemas["IntakeDoc"];
export type QueryEntry = Schemas["IntakeQueryEntry"];
export type IntakePreview = Schemas["IntakePreview"];
export type PreviewItem = Schemas["IntakeItem"];

/** One query in the draft. A new one has `id: null`; the server makes its id from the name. */
export type QueryRow = Keyed<QueryEntry>;

export interface IntakeForm {
  queries: QueryRow[];
}

export function toForm(value: IntakeDoc): IntakeForm {
  return { queries: keyed(value.queries) };
}

export function fromForm(form: IntakeForm): IntakeDoc {
  return { queries: unkeyed(form.queries) };
}

/** Same content, ignoring the row keys (so undoing an edit makes the draft clean again). */
export function sameForm(a: IntakeForm, b: IntakeForm): boolean {
  return JSON.stringify(fromForm(a)) === JSON.stringify(fromForm(b));
}

/** The row keys of the form as sent, so a 422's issues land on their row. */
export function sentKeys(form: IntakeForm): SentKeys {
  return { queries: keysOf(form.queries) };
}

/**
 * Where the page shows issues: on the list itself, on a row as a whole, on its id (which has no
 * field of its own, so it shows with the row's) and on its name and JQL.
 */
export const SHOWN: readonly FieldPath[] = [
  "queries",
  "queries.*",
  "queries.*.id",
  "queries.*.name",
  "queries.*.jql",
];

export function newQuery(): QueryRow {
  return { key: newKey(), item: { id: null, name: "", jql: "" } };
}

export function isBlank(row: QueryRow): boolean {
  return row.item.name.trim() === "" && row.item.jql.trim() === "";
}

/**
 * How each row's controls name it, unique per row: its name; its place for one not named yet; and
 * both for a name another row shares ("Ready (query 2)").
 */
export function queryLabels(rows: readonly QueryRow[]): string[] {
  const names = rows.map((row) => row.item.name.trim());
  const seen = new Map<string, number>();
  for (const name of names) seen.set(name.toLowerCase(), (seen.get(name.toLowerCase()) ?? 0) + 1);

  return names.map((name, index) => {
    const place = `query ${String(index + 1)}`;
    if (name === "") return place;
    return (seen.get(name.toLowerCase()) ?? 0) > 1 ? `${name} (${place})` : name;
  });
}

export function patchQuery(
  form: IntakeForm,
  key: string,
  patch: Partial<Pick<QueryEntry, "name" | "jql">>,
): IntakeForm {
  return {
    ...form,
    queries: form.queries.map((r) => (r.key === key ? { ...r, item: { ...r.item, ...patch } } : r)),
  };
}

// ------------------------------------------------------------------ Test query

/** What a row shows under its JQL after "Test query". */
export type PreviewLine =
  | { kind: "none" }
  | { kind: "pending" }
  | { kind: "error"; text: string }
  | { kind: "empty" }
  | { kind: "items"; count: string; items: readonly PreviewItem[]; more: string | null };

/** The row's preview for its current JQL (`usePreview`: only a run of exactly this JQL counts). */
export interface PreviewState {
  pending: boolean;
  result: IntakePreview | undefined;
  error: unknown;
}

export function previewLine({ pending, result, error }: PreviewState): PreviewLine {
  if (pending) return { kind: "pending" };
  if (error !== undefined && error !== null) return { kind: "error", text: failureText(error) };
  if (!result) return { kind: "none" };
  if (result.error) return { kind: "error", text: result.error };
  if (result.total === 0 && result.items.length === 0) return { kind: "empty" };

  const total = Math.max(result.total, result.items.length);
  return {
    kind: "items",
    count: `${String(total)} to take in`,
    items: result.items,
    more: total > result.items.length ? `first ${String(result.items.length)} shown` : null,
  };
}

/** A refused preview says what the server found wrong with the JQL; any other failure, its text. */
function failureText(error: unknown): string {
  if (error instanceof ApiError && error.issues.length > 0) {
    return error.issues.map((issue) => issue.message).join(" ");
  }
  return describeSaveError(error).text;
}
