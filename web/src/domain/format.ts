// Pure display helpers. They may combine server fields for display; they never derive board state.
import type { Schemas } from "../api/client";

/** An instant: ISO string (as the API sends), epoch ms, or Date. */
export type Instant = string | number | Date;

function toMs(v: Instant): number {
  if (typeof v === "number") return v;
  if (typeof v === "string") return Date.parse(v);
  return v.getTime();
}

/** Whole minutes from `from` to `now` (never negative). */
export function minutesBetween(from: Instant, now: Instant): number {
  const d = (toMs(now) - toMs(from)) / 60_000;
  return Number.isFinite(d) ? Math.max(0, d) : 0;
}

/** `now`, `25m`, `3h 10m` (`1h 05m`, `2h`), `2d`. */
export function age(from: Instant, now: Instant): string {
  const min = minutesBetween(from, now);
  if (min < 1) return "now";
  const total = Math.round(min);
  if (total < 60) return `${String(total)}m`;
  if (total < 1440) {
    const h = Math.floor(total / 60);
    const m = total % 60;
    return m ? `${String(h)}h ${String(m).padStart(2, "0")}m` : `${String(h)}h`;
  }
  return `${String(Math.floor(total / 1440))}d`;
}

/** An age of 90 minutes or more is shown strong (`.is-old`). */
export function isOld(from: Instant, now: Instant): boolean {
  return minutesBetween(from, now) >= 90;
}

/** Run elapsed time as `m:ss`. */
export function elapsed(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  return `${String(Math.floor(s / 60))}:${String(s % 60).padStart(2, "0")}`;
}

/** `JIRA PLAT-2231` when the ticket came from a source, else our own key (`AOS-42`). */
export function srcLabel(t: Pick<Schemas["TicketSummary"], "key" | "source">): string {
  return t.source ? `${t.source.source.toUpperCase()} ${t.source.external_id}` : t.key;
}

/** `constellation/web` → `web`; also strips a trailing `.git` and slash from URLs. */
export function repoShort(name: string | null | undefined): string {
  if (!name) return "";
  const last = name.replace(/\/+$/, "").split("/").pop() ?? "";
  return last.replace(/\.git$/, "");
}

/** A person's short name for captions and titles: `Sam Lee` → `Sam`. */
export function firstName(p: Pick<Schemas["Person"], "display_name">): string {
  return p.display_name.trim().split(/\s+/)[0] ?? p.display_name;
}

/** Avatar initials: `Diwash Rai` → `DR`, `dev` → `DE`. */
export function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  const first = parts[0] ?? "";
  const last = parts.length > 1 ? (parts[parts.length - 1] ?? "") : "";
  const out = last ? `${first.charAt(0)}${last.charAt(0)}` : first.slice(0, 2);
  return out.toUpperCase() || "?";
}

/** One of the four avatar fills (`--av-1..4`), stable per person. */
export function avatarTone(id: number): 1 | 2 | 3 | 4 {
  return ((((id % 4) + 4) % 4) + 1) as 1 | 2 | 3 | 4;
}
