// Settings › Space's pure core: the two documents (the Space's own settings, the repo catalog) as
// the forms the page edits, and the reading aids around them. Every rule lives on the server: its
// validate-only call and its save report the problems, and the page shows them on their fields.
import type { Schemas } from "../../../api/client";
import type { FieldPath, SentKeys } from "../../../editor/issues";
import { keysOf, newKey } from "../../../editor/list";

export type SpaceDoc = Schemas["SpaceDoc"];
export type ReposDoc = Schemas["ReposDoc"];

// ------------------------------------------------------------------ Space

/** The Space settings as edited. The timeout is text so the field can be cleared while typing. */
export interface SpaceForm {
  name: string;
  key_prefix: string;
  jira_projects: string[];
  default_repo: string | null;
  claim_timeout_s: string;
}

/** The Space fields the page shows; an issue anywhere else is shown in the save bar. */
export const SPACE_SHOWN: readonly FieldPath[] = [
  "name",
  "key_prefix",
  "jira_projects",
  "jira_projects.*",
  "default_repo",
  "claim_timeout_s",
];

export function spaceToForm(value: SpaceDoc): SpaceForm {
  return {
    name: value.name,
    key_prefix: value.key_prefix,
    jira_projects: [...value.jira_projects],
    default_repo: value.default_repo,
    claim_timeout_s: String(value.claim_timeout_s),
  };
}

export function spaceFromForm(f: SpaceForm): SpaceDoc {
  return {
    name: f.name.trim(),
    key_prefix: f.key_prefix.trim(),
    jira_projects: f.jira_projects,
    default_repo: f.default_repo,
    claim_timeout_s: Number(f.claim_timeout_s.trim()),
  };
}

/** Typed or pasted project keys (comma-, semicolon- or space-separated, any case), upper-cased. */
export function splitProjectKeys(raw: string): string[] {
  return raw
    .split(/[\s,;]+/)
    .map((part) => part.trim().toUpperCase())
    .filter((key) => key !== "");
}

/** The select's value for "no default repo". */
export const NO_REPO = "";

/** The default repo's choices: None, then the saved catalog, then a current value that left it. */
export function defaultRepoOptions(
  savedNames: readonly string[],
  current: string | null,
): { value: string; label: string }[] {
  const options = [
    { value: NO_REPO, label: "None" },
    ...savedNames.map((n) => ({ value: n, label: n })),
  ];
  if (current !== null && !savedNames.includes(current)) {
    options.push({ value: current, label: current });
  }
  return options;
}

/** 900 → "15 minutes", 5400 → "1 h 30 min", 86400 → "24 hours". */
export function duration(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  if (h > 0 && m === 0 && s === 0) return h === 1 ? "1 hour" : `${String(h)} hours`;
  if (h === 0 && s === 0) return m === 1 ? "1 minute" : `${String(m)} minutes`;
  const parts: string[] = [];
  if (h > 0) parts.push(`${String(h)} h`);
  if (m > 0) parts.push(`${String(m)} min`);
  if (s > 0) parts.push(`${String(s)} s`);
  return parts.join(" ");
}

/** The claim timeout's reading aid: what it means, and the value in words when it reads as one. */
export function claimHint(value: string): string {
  const v = value.trim();
  const words = /^\d+$/.test(v) && Number(v) > 0 ? ` (${duration(Number(v))})` : "";
  return `A dispatched run whose worker hasn't started after this long fails${words}.`;
}

// ------------------------------------------------------------------ Repos

/** One catalog row. `fresh` = added here and never saved, so its name may still change. */
export interface RepoRow {
  key: string;
  fresh: boolean;
  name: string;
  url: string;
  /** The form's text; blank = no default (tickets then need their own base branch). */
  default_branch: string;
}

export interface ReposForm {
  repos: RepoRow[];
}

/** The repo fields the page shows; an issue anywhere else is shown in the save bar. */
export const REPOS_SHOWN: readonly FieldPath[] = [
  "repos",
  "repos.*",
  "repos.*.name",
  "repos.*.url",
  "repos.*.default_branch",
];

export function reposToForm(value: ReposDoc): ReposForm {
  return {
    repos: value.repos.map((e) => ({
      key: newKey(),
      fresh: false,
      name: e.name,
      url: e.url,
      default_branch: e.default_branch ?? "",
    })),
  };
}

export function reposFromForm(f: ReposForm): ReposDoc {
  return {
    repos: f.repos.map((r) => ({
      name: r.name.trim(),
      url: r.url.trim(),
      default_branch: r.default_branch.trim() || null,
    })),
  };
}

/** The same edit: the rows' contents in order; their keys are the client's own. */
export function sameRepos(a: ReposForm, b: ReposForm): boolean {
  const content = (f: ReposForm) =>
    JSON.stringify(f.repos.map((r) => [r.fresh, r.name, r.url, r.default_branch]));
  return content(a) === content(b);
}

/** The row keys sent with the value, so the server's issues land on their rows. */
export function repoKeys(f: ReposForm): SentKeys {
  return { repos: keysOf(f.repos) };
}

export function newRepoRow(): RepoRow {
  return { key: newKey(), fresh: true, name: "", url: "", default_branch: "main" };
}

/** A new row nothing has been typed into (removed without asking). */
export function isBlankRepo(row: RepoRow): boolean {
  return row.fresh && row.name.trim() === "" && row.url.trim() === "";
}

/** How the row's controls name it: "agentos", or "new repo" for an unnamed new row. */
export function repoLabel(row: RepoRow): string {
  return row.name.trim() || "new repo";
}

/** "No open tickets", "1 open ticket", "3 open tickets". */
export function openTickets(n: number): string {
  if (n === 0) return "No open tickets";
  return n === 1 ? "1 open ticket" : `${String(n)} open tickets`;
}

/** What removing a saved row asks: how many open tickets use it, as the server counted them. */
export function removeText(label: string, openCount: number): string {
  const use =
    openCount === 0 ? "" : ` ${openTickets(openCount)} ${openCount === 1 ? "uses" : "use"} it.`;
  return `Remove ${label}?${use} It is gone once you save.`;
}

/** A URL a GitHub Actions runner can't clone with its token (a hint, not an error). */
export function urlHint(url: string): string | undefined {
  const u = url.trim();
  if (u === "" || /^https:\/\//i.test(u)) return undefined;
  return "GitHub Actions runners clone over https:// only.";
}
