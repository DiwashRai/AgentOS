// The Ticket header's base branch (byline "Base branch"): pure display and edit logic. It reads
// `TicketDetail.base_branch` (the resolved value: the ticket's own, else the repo's default) and the
// repo catalog from `/api/config`; it never derives a lock state. A later "source" / "locked" line
// belongs beside `branchHint`.
import { ApiError, type Schemas } from "../../api/client";
import { checkBranch } from "../settings/space/model";
import { actionErrorText } from "./model";

type Detail = Schemas["TicketDetail"];
type Repo = Pick<Schemas["RepoInfo"], "name" | "default_branch">;

/** The ticket's repo's `default_branch` from the catalog, or null (no repo, or not loaded). */
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
  /** "· repo default" when the branch is the repo's default, else null. */
  suffix: string | null;
}

/** The value at rest. */
export function branchView(base: string | null, def: string | null): BranchView {
  if (base === null) return { text: "Not set", named: false, suffix: null };
  return {
    text: base,
    named: true,
    suffix: def !== null && base === def ? "· repo default" : null,
  };
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

/** "Use repo default" shows only when the repo's default is known and isn't the current branch. */
export function canUseDefault(current: string | null, def: string | null): boolean {
  return def !== null && current !== def;
}

export const RUN_LOCK_TITLE = "Can't change the base branch during a run";

/** Why the value can't be edited right now (a title on the disabled control), or null. */
export function branchLock(t: Pick<Detail, "active_run_id">): string | null {
  return t.active_run_id !== null ? RUN_LOCK_TITLE : null;
}

export const RAN_HINT =
  "Runs continue from this ticket's branch; a new base changes what Review compares against and where the PR goes.";

/** The one-line hint under the input while editing, once the ticket has run. */
export function branchHint(t: Pick<Detail, "latest_run">): string | null {
  return t.latest_run !== null ? RAN_HINT : null;
}

/** A failed save, as the line under the input. */
export function branchErrorText(e: unknown): string {
  if (e instanceof ApiError && e.code === "run_active") return RUN_LOCK_TITLE;
  return actionErrorText(e);
}
