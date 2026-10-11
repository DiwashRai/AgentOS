// The Users page's pure core: role words, sorting, what an admin may not change about a row, and
// the display strings for sign-in and creation times. The server stays the authority: it rejects
// self-demotion, self-disabling and demoting an env admin with a 422 whose message is shown as-is.
import { ApiError, type Schemas } from "../../api/client";
import { age, type Instant } from "../../domain/format";

export type User = Schemas["User"];
export type Role = User["role"];

/** The roles in rank order, each with its one-line reach (SPEC §4). */
export const ROLES: readonly { value: Role; label: string; hint: string }[] = [
  { value: "viewer", label: "Viewer", hint: "Reads everything and talks" },
  { value: "writer", label: "Writer", hint: "Also tickets, pipeline, files and runs" },
  { value: "admin", label: "Admin", hint: "Also users and the template" },
];

export function roleHint(role: Role): string {
  return ROLES.find((r) => r.value === role)?.hint ?? "";
}

/** Active people first, then by name (case-insensitive), then by id so the order is stable. */
export function sortUsers(users: readonly User[]): User[] {
  return [...users].sort(
    (a, b) =>
      Number(a.disabled) - Number(b.disabled) ||
      a.display_name.localeCompare(b.display_name, undefined, { sensitivity: "base" }) ||
      a.id - b.id,
  );
}

/** Why a control on this row is locked, or undefined when the admin may use it. */
export interface Locks {
  role: string | undefined;
  disabled: string | undefined;
}

export function locksFor(user: Pick<User, "id">, meId: number | undefined): Locks {
  if (user.id !== meId) return { role: undefined, disabled: undefined };
  return {
    role: "You can't change your own role. Another admin can.",
    disabled: "You can't disable yourself. Another admin can.",
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

/** `6 people · 1 disabled` */
export function summary(users: readonly User[]): string {
  const n = users.length;
  const off = users.filter((u) => u.disabled).length;
  const head = n === 1 ? "1 person" : `${String(n)} people`;
  return off > 0 ? `${head} · ${String(off)} disabled` : head;
}
