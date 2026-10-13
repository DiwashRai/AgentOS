// The New ticket form's pure model: fields, validation and the POST /api/tickets body (SPEC §10,
// `TicketCreate`). Display defaults come from GET /api/config and the signed-in user.
import type { Schemas } from "../../api/client";

type Create = Schemas["TicketCreate"];

export const TITLE_MAX = 200;

export const TYPES: readonly { value: Create["type"]; label: string }[] = [
  { value: "task", label: "Task" },
  { value: "feature", label: "Feature" },
  { value: "bug", label: "Bug" },
  { value: "improvement", label: "Improvement" },
];

export const PRIORITIES: readonly { value: Create["priority"]; label: string }[] = [
  { value: "high", label: "High" },
  { value: "normal", label: "Normal" },
  { value: "low", label: "Low" },
];

/** The repo select's value for "no repo" (the ticket runs without a checkout). */
export const NO_REPO = "";

/** The repo picker: the Space's catalog by name, then "No repo". */
export function repoOptions(
  repos: readonly Schemas["RepoInfo"][],
): readonly { value: string; label: string }[] {
  return [
    ...repos.map((r) => ({ value: r.name, label: r.name })),
    { value: NO_REPO, label: "No repo" },
  ];
}

type RepoRef = Pick<Schemas["RepoInfo"], "name" | "default_branch">;

/** True when the picked catalog repo has no default branch (blank = none), so a blank field
 *  leaves the ticket without a base branch. False for No repo or a repo the catalog doesn't list. */
export function needsBranch(repos: readonly RepoRef[], repo: string): boolean {
  if (repo === NO_REPO) return false;
  const found = repos.find((r) => r.name === repo);
  return found !== undefined && !found.default_branch;
}

export const NEEDS_BRANCH_PLACEHOLDER = "Required: this repo has no default";
export const NEEDS_BRANCH_NOTE =
  "Leave it blank and the ticket can't be dispatched until one is set.";

/** The base branch a blank field means: the picked repo's default branch, or a prompt to type one
 *  when the repo has none. */
export function branchPlaceholder(repos: readonly RepoRef[], repo: string): string {
  if (needsBranch(repos, repo)) return NEEDS_BRANCH_PLACEHOLDER;
  return repos.find((r) => r.name === repo)?.default_branch ?? "";
}

/** The note under the base branch field: only while it is blank on a repo with no default. The
 *  form still creates the ticket (the server allows a blank one; it just can't be dispatched). */
export function branchNote(
  repos: readonly RepoRef[],
  repo: string,
  value: string,
): string | undefined {
  return needsBranch(repos, repo) && value.trim() === "" ? NEEDS_BRANCH_NOTE : undefined;
}

export interface NewTicketForm {
  title: string;
  description: string;
  type: Create["type"];
  priority: Create["priority"];
  /** A catalog repo name, or NO_REPO. */
  repo: string;
  baseBranch: string;
  /** A person id as a string, or "" for Unassigned. */
  ownerId: string;
}

export interface Defaults {
  repo: string | null | undefined;
  meId: number | null | undefined;
}

export function initialForm(d: Defaults): NewTicketForm {
  return {
    title: "",
    description: "",
    type: "task",
    priority: "normal",
    repo: d.repo ?? NO_REPO,
    baseBranch: "",
    ownerId: d.meId == null ? "" : String(d.meId),
  };
}

export type FormErrors = Partial<Record<"title" | "baseBranch", string>>;

export function validate(f: NewTicketForm): FormErrors {
  const errors: FormErrors = {};
  const title = f.title.trim();
  if (!title) errors.title = "Give the ticket a title.";
  else if (title.length > TITLE_MAX) errors.title = `At most ${String(TITLE_MAX)} characters.`;
  if (f.repo !== NO_REPO && /\s/.test(f.baseBranch.trim())) {
    errors.baseBranch = "A branch name has no spaces.";
  }
  return errors;
}

export function hasErrors(e: FormErrors): boolean {
  return Object.values(e).some(Boolean);
}

/** The request body. A blank branch goes as null (the repo's default branch applies, if it has
 *  one; otherwise the ticket is created without one). `workflowId` null = the default workflow. */
export function toCreateBody(f: NewTicketForm, workflowId: number | null = null): Create {
  const repo = f.repo === NO_REPO ? null : f.repo;
  const branch = f.baseBranch.trim();
  return {
    title: f.title.trim(),
    description: f.description,
    type: f.type,
    priority: f.priority,
    repo,
    base_branch: repo && branch ? branch : null,
    owner_id: f.ownerId === "" ? null : Number(f.ownerId),
    workflow_id: workflowId,
  };
}

/** Tab inside a dialog: the index to focus next, wrapping at both ends (-1 = nothing to focus). */
export function trapIndex(current: number, count: number, back: boolean): number {
  if (count <= 0) return -1;
  if (current < 0) return back ? count - 1 : 0;
  return back ? (current - 1 + count) % count : (current + 1) % count;
}
