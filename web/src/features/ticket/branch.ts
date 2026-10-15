// The Ticket header's base branch (byline "Base branch"): pure display and edit logic. It reads
// `TicketDetail.base_branch` (the resolved value, null when blank) and `base_branch_info` (where it
// came from, and the server's lock); the repo catalog from `/api/config` only decides whether "Use
// repo default" can be offered. It never derives a lock or the source itself.
import { ApiError, type Schemas } from "../../api/client";
import { firstName } from "../../domain/format";
import { actionErrorText, ticketPath } from "./model";

type Detail = Schemas["TicketDetail"];
type Info = Schemas["BaseBranchInfo"];
type Repo = Pick<Schemas["RepoInfo"], "name" | "default_branch">;

/** The ticket's repo's `default_branch` from the catalog, or null (no repo, none set, or not
 *  loaded). */
export function repoDefault(
  repoName: string | null,
  repos: readonly Repo[] | undefined,
): string | null {
  if (repoName === null) return null;
  return repos?.find((r) => r.name === repoName)?.default_branch ?? null;
}

export interface BranchView {
  /** The branch name (mono), or "Not set". */
  text: string;
  /** True when `text` is a branch name (drawn in mono). */
  named: boolean;
  /** Where it came from ("from Jira · version_1.0", "set by Sam", "repo default"), or why it is
   *  blank ("No rule matches version_3.0"); null when the server says neither. */
  source: string | null;
  /** True when there is no branch: the ticket can't be dispatched until someone sets one. */
  missing: boolean;
}

/** The source line for a set branch, from the server's `base_branch_info.source`. */
function sourceLine(info: Info): string | null {
  switch (info.source) {
    case "mapping":
      return info.source_value ? `from Jira · ${info.source_value}` : "from Jira";
    case "manual":
      return info.set_by ? `set by ${firstName(info.set_by)}` : "set by hand";
    case "repo_default":
      return "repo default";
    case "none":
      return null;
  }
}

/** The value at rest, with its source line. */
export function branchView(t: Pick<Detail, "base_branch" | "base_branch_info">): BranchView {
  const info = t.base_branch_info;
  if (t.base_branch === null) {
    return { text: "Not set", named: false, source: info?.reason ?? null, missing: true };
  }
  return {
    text: t.base_branch,
    named: true,
    source: info ? sourceLine(info) : null,
    missing: false,
  };
}

const BRANCH_MAX = 200;
const BRANCH_RE = /^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$/;

/** validate.py `validate_base_branch`. */
export function checkBranch(branch: string): string | undefined {
  if (branch === "") return "Required.";
  if (!BRANCH_RE.test(branch)) {
    return `Letters, digits, . _ / or -, starting with a letter or digit (max ${String(BRANCH_MAX)}).`;
  }
  if (branch.includes("..") || branch.includes("//")) return "Must not contain .. or //.";
  if (branch.endsWith("/") || branch.endsWith(".") || branch.endsWith(".lock")) {
    return "Must not end in /, . or .lock.";
  }
  if (branch.split("/").some((c) => c.startsWith(".") || c.endsWith(".lock"))) {
    return "No part may start with . or end in .lock.";
  }
  return undefined;
}

/** The inline message for an input, or undefined when it is a valid branch name. */
export function branchError(input: string): string | undefined {
  return checkBranch(input.trim());
}

/** Save is enabled when the trimmed input is valid and differs from the current branch. */
export function canSaveBranch(input: string, current: string | null): boolean {
  const v = input.trim();
  return branchError(v) === undefined && v !== (current ?? "");
}

/** The PATCH body for a saved input: the repo's default itself is sent as null (follow the repo). */
export function branchPatch(input: string, def: string | null): Schemas["TicketPatch"] {
  const v = input.trim();
  return { base_branch: def !== null && v === def ? null : v };
}

/** "Use repo default" shows only when the repo has a default and the server says the ticket
 *  doesn't already follow it. */
export function canUseDefault(info: Pick<Info, "source"> | null, def: string | null): boolean {
  return def !== null && info?.source !== "repo_default";
}

export const RUN_LOCK_TITLE = "Can't change the base branch during a run";
export const LOCKED_TITLE =
  "A run has started the ticket branch from this base, so the base branch can't change now";

/** Why the value can't be edited right now (a title on the value), or null: the server's lock
 *  first, else an active run. */
export function branchLock(t: Pick<Detail, "active_run_id" | "base_branch_info">): string | null {
  if (t.base_branch_info?.locked) return LOCKED_TITLE;
  return t.active_run_id !== null ? RUN_LOCK_TITLE : null;
}

export interface LockNote {
  /** "Locked by run #12", or "Locked" when the server names no run. */
  text: string;
  /** The Runs tab with that run selected, or null without a run. */
  href: string | null;
}

/** The lock note after the value, from `base_branch_info.locked` / `locked_by_run`. */
export function lockNote(t: Pick<Detail, "key" | "base_branch_info">): LockNote | null {
  const info = t.base_branch_info;
  if (!info?.locked) return null;
  const run = info.locked_by_run;
  if (run === null) return { text: "Locked", href: null };
  return {
    text: `Locked by run #${String(run)}`,
    href: ticketPath(t.key, "runs", `?run=${String(run)}`),
  };
}

export const RAN_HINT =
  "Runs continue from this ticket's branch; a new base changes what Review compares against and where the PR goes.";

/** The one-line hint under the input while editing, once the ticket has run. */
export function branchHint(t: Pick<Detail, "latest_run">): string | null {
  return t.latest_run !== null ? RAN_HINT : null;
}

export const LOCKED_ERROR = "The base branch is locked: a run has started from it.";

/** A failed save, as the line under the input. */
export function branchErrorText(e: unknown): string {
  if (e instanceof ApiError && e.code === "run_active") return RUN_LOCK_TITLE;
  if (e instanceof ApiError && e.code === "base_branch_locked") return LOCKED_ERROR;
  return actionErrorText(e);
}
