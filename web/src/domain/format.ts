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

/** An age split for display (refresh HANDOFF §5 "Age"): each number tabular, each unit a size
 *  smaller. `3h 05m` → `[{n:"3",unit:"h"},{n:"05",unit:"m"}]`. Text that isn't all number+unit
 *  pairs (`now`) is one unit-less part, so nothing is ever dropped. */
export function ageParts(text: string): { n: string; unit: string }[] {
  const parts = text.split(" ").map((p) => /^(\d+)([a-z]+)$/.exec(p));
  if (parts.some((m) => m === null)) return [{ n: text, unit: "" }];
  return parts.map((m) => ({ n: m?.[1] ?? "", unit: m?.[2] ?? "" }));
}

/** An age of 90 minutes or more is shown strong (`.is-old`). */
export function isOld(from: Instant, now: Instant): boolean {
  return minutesBetween(from, now) >= 90;
}

/** "just now" under a minute, else the age plus "ago": "5m ago", "3h 10m ago", "2d ago". */
export function ago(from: Instant, now: Instant): string {
  const a = age(from, now);
  return a === "now" ? "just now" : `${a} ago`;
}

/** Every duration on screen: `0:42`, `12:05` under an hour; `1h 4m` from there. Negative or
 *  non-finite input (a clock skewed past the start) reads `0:00`. Callers with no start yet draw
 *  nothing rather than `0:00` (`runSeconds` returns null then). */
export function duration(seconds: number): string {
  const s = Number.isFinite(seconds) ? Math.max(0, Math.floor(seconds)) : 0;
  if (s < 3600) return `${String(Math.floor(s / 60))}:${String(s % 60).padStart(2, "0")}`;
  return `${String(Math.floor(s / 3600))}h ${String(Math.floor((s % 3600) / 60))}m`;
}

/** Whole seconds a run has been going, from its `started_at` to `now` (never negative); null
 *  before it has started (no run, queued, or an unreadable time). */
export function runSeconds(
  run: Pick<Schemas["RunSummary"], "started_at"> | null | undefined,
  now: number,
): number | null {
  const started = run?.started_at;
  if (!started) return null;

  const ms = Date.parse(started);
  return Number.isFinite(ms) ? Math.max(0, Math.floor((now - ms) / 1000)) : null;
}

/** `constellation/web` → `web`; also strips a trailing `.git` and slash from URLs. */
export function repoShort(name: string | null | undefined): string {
  if (!name) return "";
  const last = name.replace(/\/+$/, "").split("/").pop() ?? "";
  return last.replace(/\.git$/, "");
}

/** A PR link's caption: `…/pull/12` → `PR #12`; any other URL → `PR`. */
export function prLabel(url: string): string {
  const n = /\/pull\/(\d+)\/?$/.exec(url)?.[1];
  return n ? `PR #${n}` : "PR";
}

/** The word after a count: "ticket" for 1, else "tickets". */
export function plural(n: number, one: string): string {
  return n === 1 ? one : `${one}s`;
}

/** `812 B`, `1.2 KB`, `3.4 MB`. */
export function formatSize(bytes: number): string {
  if (bytes < 1024) return `${String(bytes)} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** A person's short name for captions and titles: `Sam Lee` → `Sam`. */
export function firstName(p: Pick<Schemas["Person"], "display_name">): string {
  return p.display_name.trim().split(/\s+/)[0] ?? p.display_name;
}
