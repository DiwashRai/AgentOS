// Pure helpers for the Peek: the Tab trap.

/**
 * Tab trap: given the focused element's index among the panel's focusables (-1 = the panel
 * itself or outside), return the index to move focus to, or null to let the browser move it.
 */
export function trapIndex(current: number, count: number, shift: boolean): number | null {
  if (count === 0) return -1;
  if (current < 0) return shift ? count - 1 : 0;
  if (!shift && current === count - 1) return 0;
  if (shift && current === 0) return count - 1;
  return null;
}
