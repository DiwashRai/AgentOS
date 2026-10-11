// A draft over a saved copy, shared by the Pipeline tab (a pipeline) and the Template page (the
// pipeline plus the Space defaults). `base` is the version the draft started from, so a save never
// silently overwrites someone else's change (the server answers 409 `stale_version` instead).
import { useEffect, useState } from "react";
import { useBlocker } from "react-router";

import { samePipeline, type Pipeline } from "./model";

const LEAVE = "Leave without saving your pipeline changes?";

export interface Draft<T> {
  /** What the editor shows: the draft while there are changes, else the saved copy. */
  value: T | undefined;
  dirty: boolean;
  /** The `base_version` to save with. */
  baseVersion: number | undefined;
  edit: (value: T) => void;
  discard: () => void;
}

export type PipelineDraft = Draft<Pipeline>;

export function usePipelineDraft(
  saved: Pipeline | undefined,
  version: number | undefined,
): PipelineDraft {
  return useDraft(saved, version, samePipeline);
}

export function useDraft<T>(
  saved: T | undefined,
  version: number | undefined,
  same: (a: T, b: T) => boolean,
): Draft<T> {
  const [draft, setDraft] = useState<{ value: T; base: number | undefined } | null>(null);
  const dirty = draft !== null && saved !== undefined && !same(draft.value, saved);
  useUnsavedWarning(dirty);
  return {
    value: dirty ? draft.value : saved,
    dirty,
    baseVersion: dirty ? draft.base : version,
    // A draft that matches the saved copy again starts over from the current version.
    edit: (value) => {
      setDraft({ value, base: dirty ? draft.base : version });
    },
    discard: () => {
      setDraft(null);
    },
  };
}

/** Warn before leaving with unsaved changes: closing the tab, or navigating to another page. */
function useUnsavedWarning(dirty: boolean) {
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
    if (window.confirm(LEAVE)) blocker.proceed();
    else blocker.reset();
  }, [blocker]);
}
