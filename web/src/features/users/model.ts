// The Users page's pure core: role words, sorting (pending people first), what an admin may not
// change about a row, and the display strings for sign-in and creation times. The server stays the
// authority: it rejects self-demotion, self-disabling and demoting an env admin with a 422 whose
// message is shown as-is.
import { ApiError, type Schemas } from "../../api/client";
import { age, type Instant } from "../../domain/format";

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

/** Why a control on this row is locked, or undefined when the admin may use it. */
export interface Locks {
  role: string | undefined;
  disabled: string | undefined;
  /** A lock the row says out loud (not just as a tooltip), in place of the role hint. */
  note: string | undefined;
}

/** The server refuses to demote or disable an admin listed in its `AGENTOS_ADMINS` (a 422). */
export const ENV_ADMIN_LOCK = "Set in server config (AGENTOS_ADMINS)";

export function locksFor(user: Pick<User, "id" | "env_admin">, meId: number | undefined): Locks {
  if (user.env_admin) {
    return { role: ENV_ADMIN_LOCK, disabled: ENV_ADMIN_LOCK, note: ENV_ADMIN_LOCK };
  }
  if (user.id !== meId) return { role: undefined, disabled: undefined, note: undefined };
  return {
    role: "You can't change your own role. Another admin can.",
    disabled: "You can't disable yourself. Another admin can.",
    note: undefined,
  };
}

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

/** One line for a failed change. 403 means the viewer lost admin since the page loaded. */
export function describePatchError(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 403) return "Only admins can manage users.";
    return e.message;
  }
  return e instanceof Error ? e.message : "Something went wrong.";
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
