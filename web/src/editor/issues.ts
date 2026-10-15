// Where a 422's issues show. Each issue's `loc` (relative to the request body) becomes a dotted
// field path the page addresses, e.g. "space_name", "repos.<rowKey>.url" or
// "mappings.<rowKey>.rules.<rowKey>.match". List indices become the row keys of the snapshot that
// was sent, so an issue stays on its row after the user moves rows. Issues that name no place the
// page shows are form level.
import { ApiError, type ValidationIssue } from "../api/client";

/** A dotted field path. Row keys stand in for list indices, so they must not contain a dot. */
export type FieldPath = string;

/**
 * The row keys of every list in the value that was sent, in the order sent, by the list's path:
 * `{ repos: ["k1", "k2"] }`, and a list inside a row under its parent row's key,
 * `{ mappings: ["m1"], "mappings.m1.rules": ["r1", "r2"] }`.
 */
export type SentKeys = Readonly<Record<FieldPath, readonly string[]>>;

export interface Issues {
  /** Messages by field path. A list's own path holds its list-level issues (e.g. too many). */
  readonly fields: ReadonlyMap<FieldPath, readonly string[]>;
  /** Messages about the request as a whole, or about a place the page doesn't show. */
  readonly form: readonly string[];
}

export interface PlaceOptions {
  /** The loc prefix of the document in the body; dropped. Default `["value"]` (`{value, ...}`). */
  root?: readonly (string | number)[];
  keys?: SentKeys;
  /**
   * The paths the page shows, `*` matching one segment (`"repos.*.url"`). An issue lands on the
   * deepest shown path that contains it, else it is form level. Omitted: every path is shown.
   */
  shown?: readonly FieldPath[];
}

export const NO_ISSUES: Issues = { fields: new Map(), form: [] };

/** A field path from its segments: `pathOf("repos", row.key, "url")`. */
export function pathOf(...segments: readonly (string | number)[]): FieldPath {
  return segments.map(String).join(".");
}

/** The issues of a failed call, placed; none for anything but an `ApiError`. */
export function issuesOf(error: unknown, options: PlaceOptions = {}): Issues {
  if (!(error instanceof ApiError) || error.issues.length === 0) return NO_ISSUES;
  return placeIssues(error.issues, options);
}

export function placeIssues(
  issues: readonly ValidationIssue[],
  { root = ["value"], keys = {}, shown }: PlaceOptions = {},
): Issues {
  const fields = new Map<FieldPath, string[]>();
  const form: string[] = [];

  for (const issue of issues) {
    const segments = fieldSegments(issue.loc, root, keys);
    const placed =
      segments === null || shown === undefined ? segments : deepestShown(segments, shown);

    if (placed !== null && placed.length > 0) {
      const path = pathOf(...placed);
      fields.set(path, [...(fields.get(path) ?? []), issue.message]);
    } else {
      form.push(formText(issue, root));
    }
  }

  return { fields, form };
}

/** The loc below the root with list indices swapped for row keys; null if it can't be placed. */
function fieldSegments(
  loc: readonly (string | number)[],
  root: readonly (string | number)[],
  keys: SentKeys,
): string[] | null {
  if (!startsWith(loc, root)) return null;

  const out: string[] = [];
  for (const segment of loc.slice(root.length)) {
    const rows = keys[pathOf(...out)];
    if (typeof segment === "number" && rows !== undefined) {
      const key = rows[segment];
      if (key === undefined) return null;
      out.push(key);
    } else {
      out.push(String(segment));
    }
  }
  return out;
}

function startsWith(loc: readonly (string | number)[], prefix: readonly (string | number)[]) {
  return prefix.length <= loc.length && prefix.every((segment, i) => loc[i] === segment);
}

function deepestShown(segments: readonly string[], shown: readonly FieldPath[]): string[] | null {
  const patterns = shown.map((p) => p.split("."));
  for (let n = segments.length; n > 0; n--) {
    const prefix = segments.slice(0, n);
    if (patterns.some((p) => matches(prefix, p))) return prefix;
  }
  return null;
}

function matches(segments: readonly string[], pattern: readonly string[]): boolean {
  return (
    segments.length === pattern.length && pattern.every((p, i) => p === "*" || p === segments[i])
  );
}

/** A form-level line: the message, after its loc below the root when it has one. */
function formText(issue: ValidationIssue, root: readonly (string | number)[]): string {
  const loc = startsWith(issue.loc, root) ? issue.loc.slice(root.length) : issue.loc;
  return loc.length === 0 ? issue.message : `${pathOf(...loc)}: ${issue.message}`;
}

/** The messages at exactly `path` (for a list's path: its list-level issues). */
export function messagesAt(issues: Issues, path: FieldPath): readonly string[] {
  return issues.fields.get(path) ?? [];
}

/** The messages at `path` as one line for a Field's `error`, or undefined when there are none. */
export function errorAt(issues: Issues, path: FieldPath): string | undefined {
  const messages = messagesAt(issues, path);
  return messages.length === 0 ? undefined : messages.join(" ");
}

/** How many issues sit at `path` or below it (e.g. on a collapsed row). */
export function countWithin(issues: Issues, path: FieldPath): number {
  let n = 0;
  for (const [at, messages] of issues.fields) {
    if (at === path || at.startsWith(`${path}.`)) n += messages.length;
  }
  return n;
}

/** Every issue, placed or form level: what `fixFirst` counts. */
export function issueCount(issues: Issues): number {
  let n = issues.form.length;
  for (const messages of issues.fields.values()) n += messages.length;
  return n;
}
