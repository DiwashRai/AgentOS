// Keyed rows for list editors: a client key per row that survives moves and removes, so React
// keeps each row's state on it and issues from a sent snapshot land on the row they were about.

/** A row of a list editor: the server item plus its client key. */
export interface Keyed<T> {
  key: string;
  item: T;
}

let last = 0;

/** A key no other row in this page's lifetime has had. */
export function newKey(): string {
  last += 1;
  return `row-${String(last)}`;
}

/** Wrap a server list into keyed rows, one fresh key each. */
export function keyed<T>(items: readonly T[]): Keyed<T>[] {
  return items.map((item) => ({ key: newKey(), item }));
}

/** The server list again, in row order, without the keys. */
export function unkeyed<T>(rows: readonly Keyed<T>[]): T[] {
  return rows.map((row) => row.item);
}

/** The row keys in order: snapshot them with the value you send, so its issues map back by key. */
export function keysOf(rows: readonly { key: string }[]): string[] {
  return rows.map((row) => row.key);
}

/** Swap the row at `index` with its neighbour above (-1) or below (1); out of range = unchanged. */
export function moveRow<T>(rows: readonly T[], index: number, delta: -1 | 1): T[] {
  const to = index + delta;
  const a = rows[index];
  const b = rows[to];
  if (a === undefined || b === undefined) return [...rows];
  const next = [...rows];
  next[index] = b;
  next[to] = a;
  return next;
}
