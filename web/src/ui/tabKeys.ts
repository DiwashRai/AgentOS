/** Roving focus for a tablist: the tab index a key moves to, or null if the key isn't a tab key. */
export function tabTarget(index: number, key: string, count: number): number | null {
  if (count <= 0) return null;
  switch (key) {
    case "ArrowRight":
      return (index + 1) % count;
    case "ArrowLeft":
      return (index - 1 + count) % count;
    case "Home":
      return 0;
    case "End":
      return count - 1;
    default:
      return null;
  }
}
