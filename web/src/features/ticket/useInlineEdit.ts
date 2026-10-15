// An inline edit on the Ticket page (the description, the base branch, the repo): a draft while
// editing, null at rest. The caller passes its patch mutation in. Start and cancel reset the
// mutation; a save closes the edit on success (or, with `closeOnError`, once it settles either
// way). Escape inside the edit cancels it and stops there, so outer Escape handlers (a dialog, a
// row) never see it; `onBlur` on the edit's root cancels when focus leaves it. A lock (a run
// started) closes the edit, unless a save is in flight.
import { useState, type FocusEvent, type KeyboardEvent } from "react";

/** The parts of a TanStack mutation the edit uses. */
export interface PatchMutation<B> {
  mutate: (body: B, options?: { onSuccess?: () => void; onSettled?: () => void }) => void;
  reset: () => void;
  isPending: boolean;
}

export interface InlineEditOptions {
  /** A lock holds (a run is active): an open edit closes. */
  locked?: boolean | undefined;
  /** Close after a failed save too, so the error shows at rest (default: stay open). */
  closeOnError?: boolean | undefined;
}

export interface InlineEdit<T, B> {
  /** The value being edited; null at rest. */
  draft: T | null;
  /** Open the edit on a value. */
  start: (value: T) => void;
  /** Replace the draft as the user types. */
  change: (value: T) => void;
  /** Back to rest, dropping the draft and the last save's error. */
  cancel: () => void;
  /** Send the patch; the edit closes when it succeeds. */
  save: (body: B) => void;
  /** Escape cancels and stops there; put it on the edit's controls. */
  onKeyDown: (e: KeyboardEvent<HTMLElement>) => void;
  /** Cancels when focus moves outside the element it sits on (the edit's root). */
  onBlur: (e: FocusEvent<HTMLElement>) => void;
}

export function useInlineEdit<T, B>(
  mutation: PatchMutation<B>,
  { locked = false, closeOnError = false }: InlineEditOptions = {},
): InlineEdit<T, B> {
  const [draft, setDraft] = useState<T | null>(null);

  // A lock that appears mid-edit closes it; a save already sent settles first.
  if (locked && draft !== null && !mutation.isPending) setDraft(null);

  const close = () => {
    setDraft(null);
  };

  const start = (value: T) => {
    mutation.reset();
    setDraft(value);
  };

  const cancel = () => {
    mutation.reset();
    setDraft(null);
  };

  const save = (body: B) => {
    mutation.mutate(body, closeOnError ? { onSettled: close } : { onSuccess: close });
  };

  const onKeyDown = (e: KeyboardEvent<HTMLElement>) => {
    if (e.key !== "Escape") return;
    e.preventDefault();
    e.stopPropagation();
    cancel();
  };

  const onBlur = (e: FocusEvent<HTMLElement>) => {
    if (!e.currentTarget.contains(e.relatedTarget)) cancel();
  };

  return { draft, start, change: setDraft, cancel, save, onKeyDown, onBlur };
}
