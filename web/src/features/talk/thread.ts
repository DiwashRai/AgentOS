// Pure Talk helpers: day separators, author blocks, times, faces and the composer placeholder.
// Grouping follows SPEC §13 "Chat pane": consecutive messages by one author share a block when
// each follows the previous one within 5 minutes (the pack groups by author only).
import type { Schemas } from "../../api/client";
import { firstName } from "../../domain/format";

type ChatMessage = Schemas["ChatMessage"];
type Person = Schemas["Person"];

/** Max gap between two consecutive messages by one author for them to share a block. */
export const GROUP_GAP_MS = 5 * 60_000;

export interface DayItem {
  kind: "day";
  key: string;
  label: string;
}

export interface BlockItem {
  kind: "block";
  /** Stable across refetches: the first message's id. */
  key: string;
  author: Person;
  mine: boolean;
  /** The first message's `created_at` (the block's time). */
  at: string;
  messages: ChatMessage[];
}

export type ThreadItem = DayItem | BlockItem;

const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function pad2(n: number): string {
  return String(n).padStart(2, "0");
}

/** Local calendar day, e.g. `2026-10-11`. */
function dayKey(d: Date): string {
  return `${String(d.getFullYear())}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
}

function startOfDay(d: Date): number {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
}

/** `Thu 8 Oct`, or `Thu 8 Oct 2026` with the year. */
function dateWords(d: Date, withYear: boolean): string {
  const base = `${WEEKDAYS[d.getDay()] ?? ""} ${String(d.getDate())} ${MONTHS[d.getMonth()] ?? ""}`;
  return withYear ? `${base} ${String(d.getFullYear())}` : base;
}

/** Day separator text (CSS sets it in mono caps): `Today`, `Yesterday`, else `Thu 8 Oct`. */
export function dayLabel(at: Date, now: Date): string {
  // Round, not floor: a DST change makes one calendar day 23 or 25 hours long.
  const days = Math.round((startOfDay(now) - startOfDay(at)) / 86_400_000);
  if (days === 0) return "Today";
  if (days === 1) return "Yesterday";
  return dateWords(at, at.getFullYear() !== now.getFullYear());
}

/** `HH:MM`, local, 24-hour. */
export function clockTime(at: Date): string {
  return `${pad2(at.getHours())}:${pad2(at.getMinutes())}`;
}

/** Full date and time for a `title`: `Sun 11 Oct 2026, 10:15`. */
export function fullTime(at: Date): string {
  return `${dateWords(at, true)}, ${clockTime(at)}`;
}

/** Day separators + author blocks, oldest first (the API returns oldest first). */
export function groupThread(
  messages: readonly ChatMessage[],
  myId: number | null,
  now: Date,
): ThreadItem[] {
  const out: ThreadItem[] = [];
  let lastDay = "";
  let lastMs = Number.NaN;
  for (const m of messages) {
    const at = new Date(m.created_at);
    const ms = at.getTime();
    const day = dayKey(at);
    if (day !== lastDay) {
      out.push({ kind: "day", key: `d${day}`, label: dayLabel(at, now) });
      lastDay = day;
    }
    // A day separator in between always starts a new block.
    const prev = out[out.length - 1];
    if (prev?.kind === "block" && prev.author.id === m.author.id && ms - lastMs <= GROUP_GAP_MS) {
      prev.messages.push(m);
    } else {
      out.push({
        kind: "block",
        key: `m${String(m.id)}`,
        author: m.author,
        mine: myId !== null && m.author.id === myId,
        at: m.created_at,
        messages: [m],
      });
    }
    lastMs = ms;
  }
  return out;
}

/** The other people who have spoken, in order of first message (for Faces and the placeholder). */
export function speakers(messages: readonly ChatMessage[], myId: number | null): Person[] {
  const seen = new Map<number, Person>();
  for (const m of messages) {
    if (m.author.id !== myId && !seen.has(m.author.id)) seen.set(m.author.id, m.author);
  }
  return [...seen.values()];
}

/** `Say something to Maya, Tom and Priya` (first three people), else `… to the team`. */
export function placeholder(people: readonly Person[]): string {
  const names = people.map(firstName).filter(Boolean).slice(0, 3);
  if (names.length === 0) return "Say something to the team";
  const last = names.pop() ?? "";
  return `Say something to ${names.length ? `${names.join(", ")} and ${last}` : last}`;
}
