// Keyboard helpers (DESIGN §15): single-letter keys never fire while typing in a field or with
// Cmd/Ctrl/Alt held.

export function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  if (target.isContentEditable) return true;
  return (
    target instanceof HTMLInputElement ||
    target instanceof HTMLTextAreaElement ||
    target instanceof HTMLSelectElement
  );
}

/** True when a plain single-key shortcut may run for this event. */
export function isShortcut(e: KeyboardEvent): boolean {
  return !e.metaKey && !e.ctrlKey && !e.altKey && !isTypingTarget(e.target);
}
