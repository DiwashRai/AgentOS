// One versioned document on the server ({value, version, updated_at, updated_by}), edited as a
// draft. The page passes its query and mutations in (the kit never imports api/queries.ts):
// - the draft sits on `useDraftState`, so a draft equal to the saved copy starts over from the current
//   version;
// - Save sends `{value, base_version}`. A 409 `stale_version` offers Reload, which refetches and
//   keeps the draft on the fresh version, so the next save overwrites the other change on purpose;
//   Discard drops the draft and shows the other admin's copy. An edit made while a save is in
//   flight stays a draft, over the version that save wrote;
// - a 422's issues, from the last save or from the validate-only call (run after a pause while the
//   draft is dirty), show only while the draft still equals what was checked. Their list indices
//   become the row keys of the form as it was sent;
// - the hook holds no leave warning: `DocumentPage` warns once for every document on the page (the
//   router allows one blocker), and a page without it calls `useUnsavedWarning` itself.
import { useEffect, useMemo, useRef, useState } from "react";

import { ApiError } from "../api/client";
import { NO_ISSUES, placeIssues, type FieldPath, type Issues, type SentKeys } from "./issues";
import { describeDocumentError, type DocumentMeta, type SaveProblem } from "./save";
import { useDraftState } from "./useDraft";

/** How long the draft must sit still before the validate-only call runs. */
export const VALIDATE_DELAY_MS = 400;

/** A document as the server reads it out. Read-only extras sit beside `value`. */
export interface DocumentRead<V = unknown> extends DocumentMeta {
  value: V;
}

/** The body of a save: the edited value over the version the draft started from. */
export interface DocumentSave<V> {
  value: V;
  base_version: number;
}

/** The parts of a TanStack `useQuery` result the hook reads. */
export interface DocumentQuery<R> {
  data: R | undefined;
  error: Error | null;
  refetch: () => Promise<{ data?: R | undefined; error: Error | null }>;
}

/** The parts of a TanStack `useMutation` result the hook calls. */
export interface DocumentMutation<Vars, Data> {
  mutateAsync: (vars: Vars) => Promise<Data>;
  isPending: boolean;
}

interface BaseOptions<R extends DocumentRead, F> {
  query: DocumentQuery<R>;
  save: DocumentMutation<DocumentSave<R["value"]>, R>;
  /** The validate-only call (`{value}` → 204 or 422 with the save's issues). */
  validate?: DocumentMutation<{ value: R["value"] }, unknown> | undefined;
  /** Whether two forms are the same edit. Default: JSON equality. */
  same?: ((a: F, b: F) => boolean) | undefined;
  /** The row keys of the form's lists (`{repos: keysOf(form.repos)}`), read when it is sent. */
  keys?: ((form: F) => SentKeys) | undefined;
  /** The field paths the page shows (see `placeIssues`); omitted, every path is shown. */
  shown?: readonly FieldPath[] | undefined;
}

/** How the page's form maps to the document's value. Keep both functions stable (module level). */
export interface FormConversion<R extends DocumentRead, F> {
  toForm: (value: R["value"], read: R) => F;
  fromForm: (form: F) => R["value"];
}

export type DocumentOptions<R extends DocumentRead, F> = BaseOptions<R, F> & FormConversion<R, F>;

export interface DocumentState<R extends DocumentRead, F> {
  /** The saved document as last read, and why reading it failed (for `DocumentPage`). */
  data: R | undefined;
  error: unknown;
  /** What the editor shows: the draft while there are changes, else the saved copy. */
  form: F | undefined;
  dirty: boolean;
  saving: boolean;
  /** The issues of the last check, while the form still equals what was checked. */
  issues: Issues;
  /** A failed save or reload that isn't a list of issues (409s, network). */
  problem: SaveProblem | null;
  edit: (form: F) => void;
  save: () => void;
  discard: () => void;
  reload: () => void;
}

interface Checked<F> {
  form: F;
  issues: Issues;
}

export function sameJson(a: unknown, b: unknown): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

/** The issues of a failed save or validate placed on the form that was sent; null if it has none. */
function issuesFrom(
  e: unknown,
  keys: SentKeys,
  shown: readonly FieldPath[] | undefined,
): Issues | null {
  if (!(e instanceof ApiError) || e.code !== "validation_error" || e.issues.length === 0) {
    return null;
  }
  return placeIssues(e.issues, { keys, shown });
}

function isStale(e: unknown): boolean {
  return e instanceof ApiError && e.code === "stale_version";
}

/** The page edits the value itself. */
export function useDocument<R extends DocumentRead>(
  options: BaseOptions<R, R["value"]>,
): DocumentState<R, R["value"]>;
/** The page edits a form shape (keyed rows, "" for null) that `toForm` / `fromForm` convert. */
export function useDocument<R extends DocumentRead, F>(
  options: DocumentOptions<R, F>,
): DocumentState<R, F>;
export function useDocument<R extends DocumentRead, F>(
  options: BaseOptions<R, F> & Partial<FormConversion<R, F>>,
): DocumentState<R, F> {
  const { query, save, validate } = options;
  const data = query.data;
  // The first overload is the only way to omit the conversion, and it pins F to the value type.
  const conversion: FormConversion<R, F> = {
    toForm: options.toForm ?? (asIs as unknown as FormConversion<R, F>["toForm"]),
    fromForm: options.fromForm ?? asIs,
  };
  const { toForm, fromForm } = conversion;
  const same = options.same ?? sameJson;

  const saved = useMemo(
    () => (data === undefined ? undefined : toForm(data.value, data)),
    [data, toForm],
  );
  const draft = useDraftState(saved, data?.version, same);

  const [checked, setChecked] = useState<Checked<F> | null>(null);
  const [failure, setFailure] = useState<unknown>(null);
  const checkSeq = useRef(0);

  // The validate-only call reads the latest conversion and placement without re-arming its timer;
  // a save that comes back reads the form as it is now, not as it was sent.
  const latest = useRef({ fromForm, keys: options.keys, shown: options.shown, form: draft.value });
  useEffect(() => {
    latest.current = { fromForm, keys: options.keys, shown: options.shown, form: draft.value };
  });

  const runValidate = validate?.mutateAsync;
  const unchecked = draft.dirty ? draft.value : undefined;
  useEffect(() => {
    if (runValidate === undefined || unchecked === undefined) return;
    const timer = window.setTimeout(() => {
      checkSeq.current += 1;
      const mine = checkSeq.current;
      const { fromForm: toValue, keys, shown } = latest.current;
      const sentKeys = keys?.(unchecked) ?? {};
      runValidate({ value: toValue(unchecked) }).then(
        () => {
          if (checkSeq.current === mine) setChecked({ form: unchecked, issues: NO_ISSUES });
        },
        (e: unknown) => {
          // A check that failed for another reason says nothing about the draft.
          const issues = issuesFrom(e, sentKeys, shown);
          if (checkSeq.current === mine)
            setChecked({ form: unchecked, issues: issues ?? NO_ISSUES });
        },
      );
    }, VALIDATE_DELAY_MS);
    return () => {
      window.clearTimeout(timer);
    };
  }, [runValidate, unchecked]);

  const form = draft.value;
  const issues =
    checked !== null && form !== undefined && same(checked.form, form) ? checked.issues : NO_ISSUES;

  return {
    data,
    error: query.error,
    form,
    dirty: draft.dirty,
    saving: save.isPending,
    issues,
    problem: failure === null ? null : describeDocumentError(failure),
    edit: draft.edit,
    save: () => {
      if (form === undefined || draft.baseVersion === undefined) return;
      const sent = form;
      const sentKeys = options.keys?.(sent) ?? {};
      // A save answers for the draft as sent: a validate still in flight is for an older one, and
      // one started after the save (the draft changed meanwhile) answers for a newer one, so the
      // save's answer only replaces the issues while no check has started since.
      checkSeq.current += 1;
      const mine = checkSeq.current;
      setFailure(null);
      save.mutateAsync({ value: fromForm(sent), base_version: draft.baseVersion }).then(
        (fresh) => {
          if (checkSeq.current === mine) setChecked(null);

          // An edit made while the save was in flight stays, now over the version just saved.
          const now = latest.current.form;
          if (now !== undefined && !same(now, sent)) draft.rebase(fresh.version);
          else draft.discard();
        },
        (e: unknown) => {
          const placed = issuesFrom(e, sentKeys, options.shown);
          if (placed === null) setFailure(e);
          else if (checkSeq.current === mine) setChecked({ form: sent, issues: placed });
        },
      );
    },
    discard: () => {
      // After a conflict the cached copy is old: fetch the one that won.
      if (isStale(failure)) void query.refetch();
      draft.discard();
      setFailure(null);
      setChecked(null);
    },
    reload: () => {
      setFailure(null);
      void query.refetch().then((fresh) => {
        if (fresh.error !== null) setFailure(fresh.error);
        else if (fresh.data !== undefined) draft.rebase(fresh.data.version);
      });
    },
  };
}

function asIs<T>(value: T): T {
  return value;
}
