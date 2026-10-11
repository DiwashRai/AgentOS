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

export const WORKSPACES: readonly { value: Create["workspace_mode"]; label: string }[] = [
  { value: "repo", label: "A repo" },
  { value: "none", label: "None" },
];

export interface NewTicketForm {
  title: string;
  description: string;
  type: Create["type"];
  priority: Create["priority"];
  workspace: Create["workspace_mode"];
  repoUrl: string;
  baseBranch: string;
  /** A person id as a string, or "" for Unassigned. */
  ownerId: string;
}

export interface Defaults {
  repoUrl: string | null | undefined;
  baseBranch: string | null | undefined;
  meId: number | null | undefined;
}

export function initialForm(d: Defaults): NewTicketForm {
  return {
    title: "",
    description: "",
    type: "task",
    priority: "normal",
    workspace: "repo",
    repoUrl: d.repoUrl ?? "",
    baseBranch: d.baseBranch ?? "",
    ownerId: d.meId == null ? "" : String(d.meId),
  };
}

export type FormErrors = Partial<Record<"title" | "repoUrl", string>>;

export function validate(f: NewTicketForm): FormErrors {
  const errors: FormErrors = {};
  const title = f.title.trim();
  if (!title) errors.title = "Give the ticket a title.";
  else if (title.length > TITLE_MAX) errors.title = `At most ${String(TITLE_MAX)} characters.`;
  if (f.workspace === "repo" && /\s/.test(f.repoUrl.trim())) {
    errors.repoUrl = "A repo URL has no spaces.";
  }
  return errors;
}

export function hasErrors(e: FormErrors): boolean {
  return Object.values(e).some(Boolean);
}

/** The request body. Empty repo fields go as null (the server's defaults apply). */
export function toCreateBody(f: NewTicketForm): Create {
  const repo = f.workspace === "repo";
  const repoUrl = f.repoUrl.trim();
  const branch = f.baseBranch.trim();
  return {
    title: f.title.trim(),
    description: f.description,
    type: f.type,
    priority: f.priority,
    workspace_mode: f.workspace,
    repo_url: repo && repoUrl ? repoUrl : null,
    base_branch: repo && branch ? branch : null,
    owner_id: f.ownerId === "" ? null : Number(f.ownerId),
  };
}

/** Tab inside a dialog: the index to focus next, wrapping at both ends (-1 = nothing to focus). */
export function trapIndex(current: number, count: number, back: boolean): number {
  if (count <= 0) return -1;
  if (current < 0) return back ? count - 1 : 0;
  return back ? (current - 1 + count) % count : (current + 1) % count;
}
