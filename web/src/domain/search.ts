// The top-bar search (`?q=`): which tickets a query matches, and where Enter jumps. Selection
// only, over server fields (`key`, `title`, `source`); views filter through here and never match
// for themselves.
import type { Schemas } from "../api/client";
import { srcLabel } from "./format";

type SearchTicket = Pick<Schemas["TicketSummary"], "key" | "title" | "source">;

/** A parsed query: its lowercased, whitespace-separated terms (none = match everything). */
export type Query = readonly string[];

/** Something that looks like a ticket key (`aos-4`, `PLAT-2231`): Enter jumps straight to it. */
const KEY_RE = /^[A-Za-z][A-Za-z0-9]*-\d+$/;

export function parseQuery(q: string | null | undefined): Query {
  return (q ?? "").toLowerCase().split(/\s+/).filter(Boolean);
}

/** Every term is a case-insensitive substring of the key, the title or the source label. */
export function matchesQuery(t: SearchTicket, query: Query): boolean {
  if (query.length === 0) return true;
  const hay = [t.key, t.title, srcLabel(t)].join("\n").toLowerCase();
  return query.every((term) => hay.includes(term));
}

/** The tickets `query` matches, in order; the same array when there is no query. */
export function bySearch<T extends SearchTicket>(
  tickets: readonly T[],
  query: Query,
): readonly T[] {
  return query.length === 0 ? tickets : tickets.filter((t) => matchesQuery(t, query));
}

/**
 * Where Enter in the search box goes: a key-shaped query is that ticket (uppercased, even if it is
 * not on the board; the ticket page handles a 404), else the one ticket that matches, else nowhere.
 */
export function jumpTarget(q: string, tickets: readonly SearchTicket[]): string | null {
  const trimmed = q.trim();
  if (KEY_RE.test(trimmed)) return trimmed.toUpperCase();
  const query = parseQuery(trimmed);
  if (query.length === 0) return null;
  const hits = bySearch(tickets, query);
  return hits.length === 1 ? (hits[0]?.key ?? null) : null;
}
