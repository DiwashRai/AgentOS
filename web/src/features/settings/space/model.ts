// Settings › Space's pure core: the two documents (Space settings, the repo catalog) as editable
// forms, their PUT bodies, client-side checks that mirror the backend (models.py
// `SpaceSettingsUpdate` / `RepoEntryIn`, pipeline/space.py, validate.py `validate_repo_url` /
// `validate_base_branch`, settings/service.py), and when a repo may be removed.
// The server stays the authority: it re-validates on save and its message is shown as-is.
import type { Schemas } from "../../../api/client";

export type SpaceSettings = Schemas["SpaceSettings"];
export type RepoSettings = Schemas["RepoSettings"];
export type SpaceBody = Schemas["SpaceSettingsUpdate"];
export type ReposBody = Schemas["RepoSettingsUpdate"];

// Limits from models.py.
export const KEY_PREFIX_RE = /^[A-Z][A-Z0-9]{0,9}$/;
export const PROJECT_RE = /^[A-Z][A-Z0-9_]+$/;
export const REPO_NAME_RE = /^[a-z0-9][a-z0-9._-]{0,63}$/;
export const SPACE_NAME_MAX = 100;
export const CLAIM_MIN_S = 60;
export const CLAIM_MAX_S = 86400;
export const MAX_PROJECTS = 50;
export const MAX_REPOS = 100;
export const URL_MAX = 500;
export const BRANCH_MAX = 200;

// ------------------------------------------------------------------ Space

/** The Space settings as edited. The timeout is text so the field can be cleared while typing. */
export interface SpaceForm {
  name: string;
  key_prefix: string;
  jira_projects: string[];
  default_repo: string | null;
  claim_timeout_s: string;
}

export type SpaceField = keyof SpaceForm;
export type SpaceIssues = Partial<Record<SpaceField, string>>;

export function spaceForm(s: SpaceSettings): SpaceForm {
  return {
    name: s.name,
    key_prefix: s.key_prefix,
    jira_projects: [...s.jira_projects],
    default_repo: s.default_repo,
    claim_timeout_s: String(s.claim_timeout_s),
  };
}

export function sameSpaceForm(a: SpaceForm, b: SpaceForm): boolean {
  return (
    a.name === b.name &&
    a.key_prefix === b.key_prefix &&
    a.default_repo === b.default_repo &&
    a.claim_timeout_s === b.claim_timeout_s &&
    sameList(a.jira_projects, b.jira_projects)
  );
}

export function spaceBody(f: SpaceForm, baseVersion: number): SpaceBody {
  return {
    name: f.name.trim(),
    key_prefix: f.key_prefix.trim(),
    jira_projects: f.jira_projects,
    default_repo: f.default_repo,
    claim_timeout_s: Number(f.claim_timeout_s.trim()),
    base_version: baseVersion,
  };
}

export interface SpaceContext {
  /** `AppConfig.auth_mode === "jira"`: the projects list can't be empty. */
  jiraAuth: boolean;
  /** The saved repo catalog's names (the server checks `default_repo` against it). */
  repoNames: readonly string[];
}

export function validateSpace(f: SpaceForm, ctx: SpaceContext): SpaceIssues {
  const issues: SpaceIssues = {};
  const name = f.name.trim();
  if (name === "") issues.name = "Required.";
  else if (name.length > SPACE_NAME_MAX) {
    issues.name = `At most ${String(SPACE_NAME_MAX)} characters.`;
  }

  const prefix = f.key_prefix.trim();
  if (!KEY_PREFIX_RE.test(prefix)) {
    issues.key_prefix = "1–10 capital letters or digits, starting with a letter.";
  } else if (f.jira_projects.includes(prefix)) {
    issues.key_prefix = prefixClash(prefix);
  }

  const projects = f.jira_projects;
  const bad = projects.find((k) => !PROJECT_RE.test(k));
  const twice = projects.find((k, i) => projects.indexOf(k) !== i);
  if (bad !== undefined) issues.jira_projects = `${bad} isn't a Jira project key.`;
  else if (twice !== undefined) issues.jira_projects = `${twice} is listed twice.`;
  else if (projects.length > MAX_PROJECTS) {
    issues.jira_projects = `At most ${String(MAX_PROJECTS)} projects.`;
  } else if (projects.length === 0 && ctx.jiraAuth) {
    issues.jira_projects = "At least one is needed while people sign in with Jira.";
  }

  if (f.default_repo !== null && !ctx.repoNames.includes(f.default_repo)) {
    issues.default_repo = `${f.default_repo} isn't in the saved repos.`;
  }

  const timeout = f.claim_timeout_s.trim();
  const seconds = Number(timeout);
  if (timeout === "" || !/^\d+$/.test(timeout))
    issues.claim_timeout_s = "A whole number of seconds.";
  else if (seconds < CLAIM_MIN_S || seconds > CLAIM_MAX_S) {
    issues.claim_timeout_s = `Between ${String(CLAIM_MIN_S)} and ${String(CLAIM_MAX_S)} seconds (1 minute to 24 hours).`;
  }
  return issues;
}

/** Why the key prefix may not be a Jira project key (the server's rule, in the UI's words). */
export function prefixClash(prefix: string): string {
  return `${prefix} is also a Jira project. New tickets would take real Jira keys (${prefix}-1, ${prefix}-2…), and those issues could then never be imported. Pick another prefix.`;
}

/**
 * Typed or pasted project keys (comma- or space-separated, any case) → the keys to add, plus the
 * first problem and the text to leave in the input (the parts that weren't added).
 */
export function addProjectKeys(
  raw: string,
  current: readonly string[],
): { keys: string[]; error: string | undefined; rest: string } {
  const keys: string[] = [];
  const rest: string[] = [];
  let error: string | undefined;
  for (const part of raw.split(/[\s,;]+/)) {
    const key = part.trim().toUpperCase();
    if (key === "") continue;
    if (current.includes(key) || keys.includes(key)) {
      // A repeat is dropped; anything else that fails stays in the input to be fixed.
      error ??= `${key} is already listed.`;
    } else if (!PROJECT_RE.test(key)) {
      error ??= `${key} isn't a Jira project key (capital letters, digits or _, at least 2).`;
      rest.push(key);
    } else if (current.length + keys.length >= MAX_PROJECTS) {
      error ??= `At most ${String(MAX_PROJECTS)} projects.`;
      rest.push(key);
    } else {
      keys.push(key);
    }
  }
  return { keys, error, rest: rest.join(", ") };
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

/** The claim timeout's reading aid: what it means, and the value in words when it is valid. */
export function claimHint(value: string): string {
  const v = value.trim();
  const words = /^\d+$/.test(v) && Number(v) > 0 ? ` (${duration(Number(v))})` : "";
  return `A dispatched run whose worker hasn't connected after this long fails${words}.`;
}

// ------------------------------------------------------------------ Repos

/** One catalog row. `fresh` = added here and never saved, so its name may still change. */
export interface RepoRow {
  /** Stable React key: `repo:<name>` for saved rows, `new:<n>` for added ones. */
  key: string;
  fresh: boolean;
  name: string;
  url: string;
  /** The form's text; blank = no default (tickets then need their own base branch). */
  default_branch: string;
  /** From the server; 0 for a fresh row. */
  open_ticket_count: number;
}

export type RepoField = "name" | "url" | "default_branch";
export type RepoIssues = Partial<Record<RepoField, string>>;

export interface ReposValidation {
  rows: RepoIssues[];
  /** Problems with the list as a whole. */
  form: string[];
}

export function repoRows(r: RepoSettings): RepoRow[] {
  return r.repos.map((e) => ({
    key: `repo:${e.name}`,
    fresh: false,
    name: e.name,
    url: e.url,
    default_branch: e.default_branch ?? "",
    open_ticket_count: e.open_ticket_count,
  }));
}

/** A new, empty row with a key no other row has. */
export function newRepoRow(rows: readonly RepoRow[]): RepoRow {
  const taken = new Set(rows.map((r) => r.key));
  let n = rows.length + 1;
  while (taken.has(`new:${String(n)}`)) n += 1;
  return {
    key: `new:${String(n)}`,
    fresh: true,
    name: "",
    url: "",
    default_branch: "main",
    open_ticket_count: 0,
  };
}

export function sameRepoRows(a: readonly RepoRow[], b: readonly RepoRow[]): boolean {
  return (
    a.length === b.length &&
    a.every(
      (r, i) =>
        r.name === b[i]?.name && r.url === b[i].url && r.default_branch === b[i].default_branch,
    )
  );
}

export function reposBody(rows: readonly RepoRow[], baseVersion: number): ReposBody {
  return {
    repos: rows.map((r) => ({
      name: r.name.trim(),
      url: r.url.trim(),
      default_branch: r.default_branch.trim() || null,
    })),
    base_version: baseVersion,
  };
}

export function validateRepos(rows: readonly RepoRow[]): ReposValidation {
  const form: string[] = [];
  if (rows.length > MAX_REPOS) form.push(`At most ${String(MAX_REPOS)} repos.`);
  const issues = rows.map((r, i): RepoIssues => {
    const out: RepoIssues = {};
    const name = r.name.trim();
    if (name === "") out.name = "Required.";
    else if (!REPO_NAME_RE.test(name)) {
      out.name = "Lowercase letters, digits, . _ or -, starting with a letter or digit (max 64).";
    } else if (rows.findIndex((o) => o.name.trim() === name) !== i) {
      out.name = `${name} is listed twice.`;
    }
    const url = checkRepoUrl(r.url.trim());
    if (url !== undefined) out.url = url;
    const branchText = r.default_branch.trim();
    const branch = branchText === "" ? undefined : checkBranch(branchText);
    if (branch !== undefined) out.default_branch = branch;
    return out;
  });
  return { rows: issues, form };
}

export function repoIssueCount(v: ReposValidation): number {
  return v.form.length + v.rows.reduce((n, r) => n + Object.keys(r).length, 0);
}

const SCP_RE = /^git@([^:/@]+):(.+)$/;
const SCHEME_RE = /^([A-Za-z][A-Za-z0-9+.-]*):\/\/([^/]*)(.*)$/;
const HOST_RE = /^[A-Za-z0-9][A-Za-z0-9.-]*$/;
const PORT_RE = /^[0-9]{1,5}$/;
// Whitespace or unprintable characters (validate.py: `isspace()` or not `isprintable()`, which is
// Unicode's Other and Separator categories).
const UNPRINTABLE_RE = /[\s\p{C}\p{Z}]/u;
const URL_FORMS = "Use https://host/owner/repo, ssh://git@host/owner/repo or git@host:owner/repo.";

/** validate.py `validate_repo_url`: https://, ssh://[git@]host[:port]/path, or git@host:path. */
export function checkRepoUrl(url: string): string | undefined {
  if (url === "") return "Required.";
  if (url.length > URL_MAX) return `At most ${String(URL_MAX)} characters.`;
  if (url.startsWith("-")) return "Must not start with -.";
  if (UNPRINTABLE_RE.test(url)) return "No spaces or control characters.";

  const scp = SCP_RE.exec(url);
  if (scp && !url.includes("://")) {
    if (!HOST_RE.test(scp[1] ?? "")) return `${scp[1] ?? ""} isn't a valid host.`;
    if ((scp[2] ?? "").startsWith("-")) return "The path must not start with -.";
    return undefined;
  }

  const m = SCHEME_RE.exec(url);
  const scheme = m?.[1]?.toLowerCase();
  if (!m || (scheme !== "https" && scheme !== "ssh")) return URL_FORMS;
  if (url.includes("?") || url.includes("#")) return "No query (?) or fragment (#).";
  let netloc = m[2] ?? "";
  const at = netloc.lastIndexOf("@");
  if (at >= 0) {
    if (scheme !== "ssh" || netloc.slice(0, at) !== "git") {
      return "No credentials in the URL (only git@ on ssh://).";
    }
    netloc = netloc.slice(at + 1);
  }
  const colon = netloc.indexOf(":");
  const host = colon >= 0 ? netloc.slice(0, colon) : netloc;
  if (!HOST_RE.test(host)) return host === "" ? "Missing the host." : `${host} isn't a valid host.`;
  if (colon >= 0 && !PORT_RE.test(netloc.slice(colon + 1))) return "Invalid port.";
  if ((m[3] ?? "").replace(/\//g, "") === "") return "Include the repository path.";
  return undefined;
}

/** A valid URL that a GitHub Actions runner can't clone with its token (a hint, not an error). */
export function urlHint(url: string): string | undefined {
  const u = url.trim();
  if (u === "" || /^https:\/\//i.test(u)) return undefined;
  return "GitHub Actions runners clone over https:// only.";
}

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

/**
 * Why a row can't be removed (the server would answer 409 `in_use`), or undefined if it can.
 * `savedDefault` is the *saved* Space default repo: that is what the server checks.
 */
export function removeBlock(row: RepoRow, savedDefault: string | null): string | undefined {
  if (row.fresh) return undefined;
  const uses: string[] = [];
  const n = row.open_ticket_count;
  if (n > 0) uses.push(`${openTickets(n)} ${n === 1 ? "uses" : "use"} it`);
  if (row.name === savedDefault) uses.push("it's the Space's default repo");
  if (uses.length === 0) return undefined;
  const why = uses.join(" and ");
  return `Can't remove: ${why}.`;
}

/** "No open tickets", "1 open ticket", "3 open tickets". */
export function openTickets(n: number): string {
  if (n === 0) return "No open tickets";
  return n === 1 ? "1 open ticket" : `${String(n)} open tickets`;
}

function sameList(a: readonly string[], b: readonly string[]): boolean {
  return a.length === b.length && a.every((x, i) => x === b[i]);
}
