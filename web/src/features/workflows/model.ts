// Workflows' pure core (request 23B): named pipeline presets a ticket copies its pipeline from.
// The list's order and labels, the Settings editor's form and its POST/PUT bodies, client-side
// checks that mirror models.py `WorkflowCreate`, the words for each error code, and the words of
// the Pipeline tab's "Switch workflow…" and "Save as workflow…". The server stays the authority:
// it re-validates (prompts render, runtimes exist) and its message is shown when we have no better.
import { ApiError, type Schemas } from "../../api/client";
import { samePipeline, type Pipeline, type SaveProblem } from "../pipeline/model";

export type Workflow = Schemas["Workflow"];
export type TicketWorkflow = Schemas["TicketWorkflow"];

/** The Settings › Workflows search param holding the open workflow: an id, or NEW. */
export const W_PARAM = "w";
export const NEW = "new";

// Limits from models.py (`WorkflowCreate`).
export const NAME_MAX = 60;
export const DESCRIPTION_MAX = 500;

// ------------------------------------------------------------------ list

/** The default first, then by name (ignoring case), so the picker's preselected one leads. */
export function sortWorkflows(ws: readonly Workflow[]): Workflow[] {
  return [...ws].sort(
    (a, b) =>
      Number(b.default) - Number(a.default) ||
      a.name.localeCompare(b.name, undefined, { sensitivity: "base" }),
  );
}

export function defaultWorkflow(ws: readonly Workflow[]): Workflow | undefined {
  return ws.find((w) => w.default);
}

/** "1 stage", "4 stages". */
export function stageCount(p: Pipeline): string {
  const n = p.stages.length;
  return n === 1 ? "1 stage" : `${String(n)} stages`;
}

/** A list row's meta: "4 stages · v3". */
export function workflowMeta(w: Workflow): string {
  return `${stageCount(w.pipeline)} · v${String(w.version)}`;
}

/** The `?w=` value → what the page opens: a workflow's id, NEW, or nothing. An unknown id opens
 *  nothing (the list shows "no longer exists"), so a stale link never edits the wrong one. */
export function parseSelection(raw: string | null): number | typeof NEW | null {
  if (raw === NEW) return NEW;
  if (raw === null || !/^\d+$/.test(raw)) return null;
  return Number(raw);
}

/** The picker in New ticket / Import: every workflow (default first), labelled "Name (default)". */
export function workflowOptions(ws: readonly Workflow[]): { value: string; label: string }[] {
  return sortWorkflows(ws).map((w) => ({
    value: String(w.id),
    label: w.default ? `${w.name} (default)` : w.name,
  }));
}

/** The picker's hint under the select: the chosen workflow's one-line description. */
export function workflowHint(ws: readonly Workflow[], id: string): string | undefined {
  const w = ws.find((x) => String(x.id) === id);
  const text = w?.description.trim() ?? "";
  return text === "" ? undefined : text;
}

/** The id a new ticket sends: the picked one, or null (the server's default) when none is picked. */
export function workflowIdOf(picked: string): number | null {
  return /^\d+$/.test(picked) ? Number(picked) : null;
}

// ------------------------------------------------------------------ editor form

export interface WorkflowForm {
  name: string;
  description: string;
  pipeline: Pipeline;
}

export function workflowForm(w: Workflow): WorkflowForm {
  return { name: w.name, description: w.description, pipeline: w.pipeline };
}

/** A new workflow starts from a copy of the default's pipeline (always valid), with no name. */
export function newWorkflowForm(ws: readonly Workflow[]): WorkflowForm | undefined {
  const base = defaultWorkflow(ws) ?? ws[0];
  return base ? { name: "", description: "", pipeline: base.pipeline } : undefined;
}

export function sameWorkflowForm(a: WorkflowForm, b: WorkflowForm): boolean {
  return (
    a.name === b.name && a.description === b.description && samePipeline(a.pipeline, b.pipeline)
  );
}

export function createBody(f: WorkflowForm): Schemas["WorkflowCreate"] {
  return { name: f.name.trim(), description: f.description.trim(), pipeline: f.pipeline };
}

export function updateBody(f: WorkflowForm, baseVersion: number): Schemas["WorkflowUpdate"] {
  return { ...createBody(f), base_version: baseVersion };
}

export type FormIssues = Partial<Record<"name" | "description", string>>;

/** Name 1–60 and unique ignoring case among the *other* workflows; description ≤ 500. */
export function validateWorkflowForm(
  f: Pick<WorkflowForm, "name" | "description">,
  others: readonly Pick<Workflow, "id" | "name">[],
  selfId: number | null,
): FormIssues {
  const issues: FormIssues = {};
  const name = f.name.trim();
  if (name === "") issues.name = "Give the workflow a name.";
  else if (name.length > NAME_MAX) issues.name = `At most ${String(NAME_MAX)} characters.`;
  else if (others.some((w) => w.id !== selfId && foldName(w.name) === foldName(name))) {
    issues.name = "Another workflow already has this name.";
  }
  if (f.description.trim().length > DESCRIPTION_MAX) {
    issues.description = `At most ${String(DESCRIPTION_MAX)} characters.`;
  }
  return issues;
}

function foldName(name: string): string {
  return name.trim().toLocaleLowerCase();
}

/** Why Delete is off, or undefined: the default can't be deleted (the server's 409 `in_use`). */
export function deleteBlock(w: Pick<Workflow, "default">): string | undefined {
  return w.default
    ? "The default workflow can't be deleted. Make another the default first."
    : undefined;
}

export function deleteConfirmText(name: string): string {
  return `Delete the workflow "${name}"? Tickets copied from it keep their pipeline.`;
}

// ------------------------------------------------------------------ errors

/** The server's "field: message" detail without the field, as a sentence. */
function detail(message: string): string {
  const m = /^[a-z_]+:\s*(.+)$/s.exec(message);
  const text = (m?.[1] ?? message).trim();
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** What a failed workflow save, create, delete or default change tells the admin. */
export function workflowErrorText(e: unknown): SaveProblem {
  if (!(e instanceof ApiError)) {
    return { text: e instanceof Error ? e.message : "Something went wrong.", reload: false };
  }
  switch (e.code) {
    case "stale_version":
      return { text: "Changed by someone else. Reload (your draft will be lost).", reload: true };
    case "validation_error":
      return { text: detail(e.message), reload: false };
    case "in_use":
      return {
        text: "The default workflow can't be deleted. Make another the default first.",
        reload: false,
      };
    case "invalid_pipeline":
      return { text: `The pipeline isn't valid · ${e.message}`, reload: false };
    case "render_error":
      return { text: `A stage prompt doesn't render · ${e.message}`, reload: false };
    case "not_found":
      return { text: "This workflow no longer exists.", reload: true };
    case "forbidden":
      return { text: "Only admins can change workflows.", reload: false };
    default:
      return { text: e.message, reload: false };
  }
}

/** "Make default" PUTs the whole Space document, so its 409 is about the Space, not the draft. */
export function makeDefaultErrorText(e: unknown): string {
  if (e instanceof ApiError && e.code === "stale_version") {
    return "The Space settings changed meanwhile. Try again.";
  }
  return workflowErrorText(e).text;
}

/** What a failed switch tells the writer on the Pipeline tab. */
export function switchErrorText(e: unknown, target: string): string {
  if (!(e instanceof ApiError)) return e instanceof Error ? e.message : "Something went wrong.";
  switch (e.code) {
    case "run_active":
      return "A run is active. Switch when it finishes.";
    case "stale_version":
      return "The pipeline changed since you opened it. Reload the page and try again.";
    case "not_found":
      return `${target} no longer exists.`;
    case "invalid_pipeline":
    case "render_error":
      return `${target} can't be applied to this ticket · ${e.message}`;
    default:
      return e.message;
  }
}

// ------------------------------------------------------------------ Pipeline tab

/** "From Standard dev · v3", "(deleted)" once the workflow is gone; null = no record (an older
 *  ticket), and the line is left out. */
export function workflowLine(w: TicketWorkflow | null): string | null {
  if (w === null) return null;
  return `From ${w.name} · v${String(w.version)}${w.id === null ? " (deleted)" : ""}`;
}

/** Why "Switch workflow…" is off for a writer, or undefined. */
export function switchBlock(s: { active: boolean; dirty: boolean }): string | undefined {
  if (s.active) return "A run is active";
  if (s.dirty) return "Save or discard your changes first";
  return undefined;
}

/** The switch confirm. Once the ticket has run, say what happens to its branch and its rail. */
export function switchConfirmText(target: string, hasRun: boolean): string {
  const base = `This ticket's pipeline becomes a copy of ${target}. Its current stages, and any edits to them, are replaced.`;
  return hasRun
    ? `${base} The ticket has run: its branch carries on, and stages keep their run history only where their ids match. The others start as not run.`
    : base;
}

/** Why "Save as workflow…" is off, or undefined: it saves the ticket's *saved* pipeline. */
export function saveAsBlock(dirty: boolean): string | undefined {
  return dirty ? "Save or discard your changes first" : undefined;
}

/** The Settings link for one workflow. */
export function workflowHref(id: number): string {
  return `/settings/workflows?${W_PARAM}=${String(id)}`;
}
