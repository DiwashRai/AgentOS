// An inline edit on the Ticket page (the description, the base branch, the repo): a draft while
// editing, null at rest. The caller passes its patch mutation in. Start and cancel reset the
// mutation; a save closes the edit on success (or, with `closeOnError`, once it settles either
// way). Escape inside the edit cancels it and stops there, so outer Escape handlers (a dialog, a
// row) never see it; `onBlur` on the edit's root cancels when focus leaves it. A lock (a run
// started) closes the edit, unless a save is in flight.
import { useState, type FocusEvent, type KeyboardEvent } from "react";

/** The parts of a TanStack mutation the edit uses. */
interface PatchMutation<B> {
  mutate: (body: B, options?: { onSuccess?: () => void; onSettled?: () => void }) => void;
  reset: () => void;
  isPending: boolean;
}

export function useInlineEdit<T, B>(
  mutation: PatchMutation<B>,
  { locked = false, closeOnError = false }: { locked?: boolean; closeOnError?: boolean } = {},
) {
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
