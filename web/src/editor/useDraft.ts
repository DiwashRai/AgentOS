// A draft over a saved copy, shared by the Pipeline tab (a pipeline), the Template page,
// Workflows, the runtimes editor and `useDocument` (one versioned document each). `base` is
// the version the draft started from, so a save never silently overwrites someone else's change
// (the server answers 409 `stale_version` instead).
import { useEffect, useState } from "react";
import { useBlocker } from "react-router";

const LEAVE = "Leave without saving your changes?";

export interface Draft<T> {
  /** What the editor shows: the draft while there are changes, else the saved copy. */
  value: T | undefined;
  dirty: boolean;
  /** The `base_version` to save with. */
  baseVersion: number | undefined;
  edit: (value: T) => void;
  discard: () => void;
  /** Keep the draft but save it over `version` (after a conflict, the copy just reloaded). */
  rebase: (version: number) => void;
}

/**
 * A draft with its own leave warning. `warn: false` turns the warning off for a page with two
 * drafts, which calls `useUnsavedWarning(a.dirty || b.dirty)` once itself, after both drafts. The
 * off warning still holds a router blocker that never blocks; react-router consults only the
 * blocker registered last, so the page's own must come after. `useDraftState` holds none.
 */
export function useDraft<T>(
  saved: T | undefined,
  version: number | undefined,
  same: (a: T, b: T) => boolean,
  options: { warn?: boolean } = {},
): Draft<T> {
  const draft = useDraftState(saved, version, same);
  useUnsavedWarning(draft.dirty && options.warn !== false);
  return draft;
}

/** The draft alone, with no leave warning: the page (or `DocumentPage`) warns once for all. */
export function useDraftState<T>(
  saved: T | undefined,
  version: number | undefined,
  same: (a: T, b: T) => boolean,
): Draft<T> {
  const [draft, setDraft] = useState<{ value: T; base: number | undefined } | null>(null);
  const dirty = draft !== null && saved !== undefined && !same(draft.value, saved);
  return {
    value: dirty ? draft.value : saved,
    dirty,
    baseVersion: dirty ? draft.base : version,
    // A draft that matches the saved copy is dropped, so the next edit starts over from the version
    // current then (even if the saved copy has moved on meanwhile).
    edit: (value) => {
      if (saved !== undefined && same(value, saved)) setDraft(null);
      else setDraft({ value, base: dirty ? draft.base : version });
    },
    discard: () => {
      setDraft(null);
    },
    rebase: (base) => {
      setDraft((d) => (d === null ? null : { ...d, base }));
    },
  };
}

/**
 * Warn before leaving with unsaved changes: closing the tab, or navigating to another page. It
 * holds a router blocker, and the router allows one at a time: call it once per page. `message`
 * is the in-app leave question.
 */
export function useUnsavedWarning(dirty: boolean, message = LEAVE) {
  useEffect(() => {
    if (!dirty) return;
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => {
      window.removeEventListener("beforeunload", onBeforeUnload);
    };
  }, [dirty]);

  // Query-string changes (a peek opening) keep the page, so only path changes ask.
  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) =>
      dirty && currentLocation.pathname !== nextLocation.pathname,
  );
  useEffect(() => {
    if (blocker.state !== "blocked") return;
    if (window.confirm(message)) blocker.proceed();
    else blocker.reset();
  }, [blocker, message]);
}
