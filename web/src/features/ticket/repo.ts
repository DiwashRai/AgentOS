// The Ticket header's repo (byline "Repo"): pure display and edit logic. It reads `repo_name`, the
// resolved `base_branch` and `base_branch_info` (its source and the server's lock) from the ticket,
// and the repo catalog from `/api/config`. What a change does is the server's (PATCH `repo`): another
// repo drops the ticket's own base branch back to that repo's default; no repo clears the base
// branch and turns off the stages that need one. This only words it before the change.
import { ApiError, type Schemas } from "../../api/client";
import { words } from "../../domain/words";
import { actionErrorText } from "./model";

type Detail = Schemas["TicketDetail"];
type Repo = Pick<Schemas["RepoInfo"], "name" | "default_branch">;

/** The select's value for no repo. */
export const NO_REPO = "";

export interface RepoOption {
  value: string;
  label: string;
}

/** The select's value for a ticket's repo. */
export function repoValue(repoName: string | null): string {
  return repoName ?? NO_REPO;
}

/** The select's options: "No repo", then the catalog sorted by name. The ticket's own repo stays
 *  selectable even if it left the catalog or the config hasn't loaded. */
export function repoOptions(
  repoName: string | null,
  repos: readonly Repo[] | undefined,
): RepoOption[] {
  const names = (repos ?? []).map((r) => r.name);
  if (repoName !== null && !names.includes(repoName)) names.push(repoName);
  names.sort((a, b) => a.localeCompare(b));
  return [{ value: NO_REPO, label: words.noRepo }, ...names.map((n) => ({ value: n, label: n }))];
}

/** The PATCH body for a select value. */
export function repoPatch(value: string): Schemas["TicketPatch"] {
  return { repo: value === NO_REPO ? null : value };
}

export const REPO_RUN_LOCK_TITLE = "Can't change the repo during a run";
export const REPO_LOCKED_TITLE =
  "A run has started the ticket branch from this repo, so the repo can't change now";

/** Why the repo can't be changed right now (a title on the value), or null: the server's lock
 *  first, else an active run. */
export function repoLock(t: Pick<Detail, "active_run_id" | "base_branch_info">): string | null {
  if (t.base_branch_info?.locked) return REPO_LOCKED_TITLE;
  return t.active_run_id !== null ? REPO_RUN_LOCK_TITLE : null;
}

export const NO_REPO_WARNING =
  "Without a repo, stages that need one are turned off and the base branch is cleared.";

/** What a change to `next` would lose, to confirm before saving; null when nothing is lost (the
 *  same repo, or a ticket that follows the repo default moving to a repo with one). */
export function repoWarning(
  t: Pick<Detail, "repo_name" | "base_branch" | "base_branch_info">,
  next: string,
  repos: readonly Repo[] | undefined,
): string | null {
  if (next === repoValue(t.repo_name)) return null;
  if (next === NO_REPO) return NO_REPO_WARNING;
  const target = repos?.find((r) => r.name === next);
  if (!target) return null; // not in the catalog: the server refuses it
  const def = target.default_branch;
  const source = t.base_branch_info?.source;
  const branch = t.base_branch;
  // The ticket's own branch (set by hand or from Jira) is dropped unless it is the new default.
  if ((source === "manual" || source === "mapping") && branch !== null && branch !== def) {
    return def !== null
      ? `${branch} goes back to ${next}'s default, ${def}.`
      : `${branch} is dropped: ${next} has no default branch, so set one before dispatch.`;
  }
  return def === null ? `${next} has no default branch, so set one before dispatch.` : null;
}

export const REPO_LOCKED_ERROR = "The repo is locked: a run has started the ticket branch from it.";

/** A failed change, as the line under the select. */
export function repoErrorText(e: unknown): string {
  if (e instanceof ApiError && e.code === "run_active") return REPO_RUN_LOCK_TITLE;
  if (e instanceof ApiError && e.code === "base_branch_locked") return REPO_LOCKED_ERROR;
  return actionErrorText(e);
}
