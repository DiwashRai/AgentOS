// The Users page's pure core: role words, sorting (pending people first), the display strings for
// sign-in and creation times, and what a failed change says on its row. The server is the only
// judge of a change: what it refuses (a 422, e.g. demoting yourself) shows on the row.
import type { Schemas } from "../../api/client";
import { age, type Instant } from "../../domain/format";
import { issuesOf } from "../../editor/issues";
import { isForbidden } from "../../editor/save";

export type User = Schemas["User"];
export type Role = User["role"];

/** The roles in rank order, each with its one-line reach (SPEC §4, §17 D3). */
export const ROLES: readonly { value: Role; label: string; hint: string }[] = [
  { value: "pending", label: "Pending", hint: "Sees nothing until approved" },
  { value: "viewer", label: "Viewer", hint: "Reads everything and talks" },
  { value: "writer", label: "Writer", hint: "Also tickets, pipeline, files and runs" },
  { value: "admin", label: "Admin", hint: "Also users and the template" },
];

/** What Approve makes a pending person: a normal user who can create and dispatch tickets. */
export const APPROVED_ROLE: Role = "writer";

export function roleHint(role: Role): string {
  return ROLES.find((r) => r.value === role)?.hint ?? "";
}

/** A pending person who may still sign in: the ones an admin has to act on. */
export function isWaiting(user: Pick<User, "role" | "disabled">): boolean {
  return user.role === "pending" && !user.disabled;
}

/** 0 waiting for approval, 1 active, 2 disabled. */
function group(user: User): number {
  if (user.disabled) return 2;
  return isWaiting(user) ? 0 : 1;
}

/**
 * People waiting for approval first, then active people, then disabled ones; within a group by
 * name (case-insensitive), then by id so the order is stable.
 */
export function sortUsers(users: readonly User[]): User[] {
  return [...users].sort(
    (a, b) =>
      group(a) - group(b) ||
      a.display_name.localeCompare(b.display_name, undefined, { sensitivity: "base" }) ||
      a.id - b.id,
  );
}

export const ADMINS_ONLY = "Only admins can manage users.";

/** Shown in place of the role hint for a user the server marks `env_admin`. */
export const ENV_ADMIN_NOTE = "Set in server config (AGENTOS_ADMINS)";

/** The Last sign-in cell: `never signed in`, `just now`, `3h 10m ago`, `12d ago`. */
export function lastSignIn(at: string | null, now: Instant): string {
  if (at === null) return "never signed in";
  const a = age(at, now);
  return a === "now" ? "just now" : `${a} ago`;
}

const DATE = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric" });

/** `11 Oct 2026` (the viewer's time zone). */
export function shortDate(at: Instant): string {
  const d = new Date(at);
  return Number.isNaN(d.getTime()) ? "" : DATE.format(d);
}

/**
 * What a failed change says under its row, one line per problem. A row has no fields to address,
 * so every issue of a 422 is form level ("role: …" when it names a field; a message-only refusal
 * as is). A 403 means the viewer lost admin since the page loaded; any other failure shows its
 * message.
 */
export function rowErrors(e: unknown): string[] {
  if (e === null || e === undefined) return [];
  if (isForbidden(e)) return [ADMINS_ONLY];

  const issues = issuesOf(e, { root: [], shown: [] });
  if (issues.form.length > 0) return [...issues.form];

  return [e instanceof Error ? e.message : "Something went wrong."];
}

/** `6 people · 2 waiting for approval · 1 disabled` */
export function summary(users: readonly User[]): string {
  const n = users.length;
  const waiting = users.filter(isWaiting).length;
  const off = users.filter((u) => u.disabled).length;
  const parts = [n === 1 ? "1 person" : `${String(n)} people`];
  if (waiting > 0) parts.push(`${String(waiting)} waiting for approval`);
  if (off > 0) parts.push(`${String(off)} disabled`);
  return parts.join(" · ");
}
