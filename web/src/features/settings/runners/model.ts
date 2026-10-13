// Settings › Runners, pure core: the runner target form and the runtimes list (rows with a stable
// client key, stripped from the PUT body), edits, client checks that mirror models.py
// (`RunnerSettingsUpdate`, `RuntimeEntry`, `RuntimeSettingsUpdate`) and pipeline/runtime.py
// (`RuntimeSpec`), and the command preview, which assembles argv the way manifest.py
// `resolve_agent` does. The server stays the authority: its 422 / 409 message is shown as-is.
import type { Schemas } from "../../../api/client";

export type RunnerSettings = Schemas["RunnerSettings"];
export type RunnerUpdate = Schemas["RunnerSettingsUpdate"];
export type RuntimeEntry = Schemas["RuntimeEntry"];
export type RuntimeSettings = Schemas["RuntimeSettings"];
export type RuntimesUpdate = Schemas["RuntimeSettingsUpdate"];
export type PromptVia = RuntimeEntry["prompt_via"];
export type VerdictFrom = RuntimeEntry["verdict_from"];

// Limits from models.py.
export const RUNTIME_NAME_RE = /^[a-z0-9][a-z0-9._-]{0,31}$/;
export const MAX_RUNTIMES = 20;
export const MAX_RUNTIME_ARGS = 50;
export const RUNTIME_ARG_MAX = 1000;
export const GITHUB_REPO_RE = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/;
export const GITHUB_WORKFLOW_RE = /^[A-Za-z0-9_.-]+\.ya?ml$/;
export const GITHUB_FIELD_MAX = 200;
export const GIT_REF_MAX = 255;

/** The placeholder each argument group replaces (manifest.py `_substitute`, `_with_session`).
 *  argv has none. `{session}` may be part of an argument; the others replace a whole one. */
export const PLACEHOLDER = {
  model_args: "{model}",
  effort_args: "{effort}",
  agent_args: "{agent}",
  session_args: "{session}",
  resume_args: "{session}",
  verdict_args: "{verdict_schema}",
  prompt_args: "{prompt}",
} as const;
const PLACEHOLDERS = [...new Set(Object.values(PLACEHOLDER))];
const SESSION = PLACEHOLDER.session_args;

const NO_ARGS: readonly string[] = Object.freeze([]);

/** A runtime's effort arguments. Optional in the schema (an older PUT may omit them; GET always
 *  sends them), so missing reads as none (one shared empty list, so the value is stable). */
export function effortArgs(e: RuntimeEntry): readonly string[] {
  return e.effort_args ?? NO_ARGS;
}

/** Session, resume and verdict arguments: optional like `effortArgs`, missing = none. */
export function sessionArgs(e: RuntimeEntry): readonly string[] {
  return e.session_args ?? NO_ARGS;
}

export function resumeArgs(e: RuntimeEntry): readonly string[] {
  return e.resume_args ?? NO_ARGS;
}

export function verdictArgs(e: RuntimeEntry): readonly string[] {
  return e.verdict_args ?? NO_ARGS;
}

/** The runtime can start a session with a known id and resume it (runtime.py `sessions`). */
export function hasSessions(e: RuntimeEntry): boolean {
  return sessionArgs(e).length > 0 && resumeArgs(e).length > 0;
}

// ------------------------------------------------------------------ runner

/** What the Runner section edits: the three text fields as typed ("" repo = none set). */
export interface RunnerForm {
  github_repo: string;
  workflow: string;
  ref: string;
}

export function runnerForm(s: RunnerSettings): RunnerForm {
  return { github_repo: s.github_repo ?? "", workflow: s.workflow, ref: s.ref };
}

/** Raw comparison, so a draft keeps exactly what was typed (the body trims). */
export function sameRunner(a: RunnerForm, b: RunnerForm): boolean {
  return a.github_repo === b.github_repo && a.workflow === b.workflow && a.ref === b.ref;
}

export function runnerBody(f: RunnerForm, baseVersion: number): RunnerUpdate {
  const repo = f.github_repo.trim();
  return {
    github_repo: repo === "" ? null : repo,
    workflow: f.workflow.trim(),
    ref: f.ref.trim(),
    base_version: baseVersion,
  };
}

export type RunnerField = keyof RunnerForm;
export type RunnerIssues = Partial<Record<RunnerField, string>>;

export function validateRunner(f: RunnerForm): RunnerIssues {
  const out: RunnerIssues = {};
  const repo = f.github_repo.trim();
  if (repo !== "") {
    if (repo.length > GITHUB_FIELD_MAX) out.github_repo = `At most ${String(GITHUB_FIELD_MAX)}.`;
    else if (!GITHUB_REPO_RE.test(repo)) out.github_repo = "Use owner/name, e.g. acme/agentos.";
  }
  const workflow = f.workflow.trim();
  if (workflow.length > GITHUB_FIELD_MAX) out.workflow = `At most ${String(GITHUB_FIELD_MAX)}.`;
  else if (!GITHUB_WORKFLOW_RE.test(workflow)) {
    out.workflow = "A file name ending in .yml or .yaml, e.g. agentos-worker.yml.";
  }
  const ref = f.ref.trim();
  if (ref === "") out.ref = "Name the branch or tag the workflow file is on.";
  else if (ref.length > GIT_REF_MAX) out.ref = `At most ${String(GIT_REF_MAX)} characters.`;
  return out;
}

export type Launcher = RunnerSettings["launcher"];

const LAUNCHER_LABEL: Record<Launcher, string> = {
  local: "Local process",
  github: "GitHub Actions",
  mailbox: "GitHub Actions via Artifactory",
};

export function launcherLabel(launcher: Launcher): string {
  return LAUNCHER_LABEL[launcher];
}

/** The launchers that dispatch a GitHub workflow, so the repo, workflow and ref apply. */
export function dispatchesWorkflow(launcher: Launcher): boolean {
  return launcher === "github" || launcher === "mailbox";
}

// ------------------------------------------------------------------ runtimes: draft

/** One runtime in the editor. `key` is client-only (React identity while the name is typed). */
export interface RuntimeRow {
  key: string;
  entry: RuntimeEntry;
}

/** Rows for the server's list. Saved rows are keyed by name (unique on the server). Every entry
 *  carries its optional argument groups (missing = none), so the PUT body always sends them. */
export function rowsFrom(entries: readonly RuntimeEntry[]): RuntimeRow[] {
  const seen = new Set<string>();
  return entries.map((entry, i) => {
    let key = `s:${entry.name}`;
    if (seen.has(key)) key = `s:${entry.name}#${String(i)}`;
    seen.add(key);
    return { key, entry: withAllGroups(entry) };
  });
}

function withAllGroups(e: RuntimeEntry): RuntimeEntry {
  if (e.effort_args && e.session_args && e.resume_args && e.verdict_args) return e;
  return {
    ...e,
    effort_args: e.effort_args ?? [],
    session_args: e.session_args ?? [],
    resume_args: e.resume_args ?? [],
    verdict_args: e.verdict_args ?? [],
  };
}

export function toEntries(rows: readonly RuntimeRow[]): RuntimeEntry[] {
  return rows.map((r) => r.entry);
}

export function runtimesBody(rows: readonly RuntimeRow[], baseVersion: number): RuntimesUpdate {
  return { runtimes: toEntries(rows), base_version: baseVersion };
}

/** Same list, same order? (Keys don't count.) */
export function sameRuntimes(a: readonly RuntimeRow[], b: readonly RuntimeRow[]): boolean {
  return canonical(a) === canonical(b);
}

function canonical(rows: readonly RuntimeRow[]): string {
  return JSON.stringify(
    rows.map(({ entry: e }) => [
      e.name,
      e.argv,
      e.model_args,
      effortArgs(e),
      e.agent_args,
      e.prompt_via,
      e.prompt_args,
      sessionArgs(e),
      resumeArgs(e),
      verdictArgs(e),
      e.verdict_from,
    ]),
  );
}

/** A new runtime's name: `runtime`, `runtime-2`, … unique in the list. */
export function freshName(taken: Iterable<string>): string {
  const used = new Set(taken);
  if (!used.has("runtime")) return "runtime";
  for (let n = 2; ; n++) {
    const name = `runtime-${String(n)}`;
    if (!used.has(name)) return name;
  }
}

/** The next client key for a new row: `n:1`, `n:2`, … past every key in use. */
export function freshKey(rows: readonly RuntimeRow[]): string {
  let max = 0;
  for (const r of rows) {
    const m = /^n:(\d+)$/.exec(r.key);
    if (m?.[1]) max = Math.max(max, Number(m[1]));
  }
  return `n:${String(max + 1)}`;
}

/** An empty draft (no program yet, so it fails validation until one is typed). */
export function newRuntime(rows: readonly RuntimeRow[]): RuntimeRow {
  return {
    key: freshKey(rows),
    entry: {
      name: freshName(rows.map((r) => r.entry.name)),
      argv: [],
      model_args: [],
      effort_args: [],
      agent_args: [],
      prompt_via: "stdin",
      prompt_args: [],
      session_args: [],
      resume_args: [],
      verdict_args: [],
      verdict_from: "file",
    },
  };
}

/** Appends a new runtime (no-op at the limit). */
export function addRuntime(rows: readonly RuntimeRow[]): RuntimeRow[] {
  if (rows.length >= MAX_RUNTIMES) return [...rows];
  return [...rows, newRuntime(rows)];
}

/** Removes a runtime; the last one stays (at least one is needed). */
export function removeRuntime(rows: readonly RuntimeRow[], key: string): RuntimeRow[] {
  if (rows.length <= 1) return [...rows];
  return rows.filter((r) => r.key !== key);
}

/** Moves a runtime up (-1) or down (+1); moving to the top makes it the server default. */
export function moveRuntime(rows: readonly RuntimeRow[], key: string, delta: -1 | 1): RuntimeRow[] {
  const i = rows.findIndex((r) => r.key === key);
  const j = i + delta;
  const out = [...rows];
  const a = out[i];
  const b = out[j];
  if (i < 0 || !a || !b) return out;
  out[i] = b;
  out[j] = a;
  return out;
}

export type RuntimePatch = Partial<Omit<RuntimeEntry, "prompt_via">>;

export function patchRuntime(
  rows: readonly RuntimeRow[],
  key: string,
  patch: RuntimePatch,
): RuntimeRow[] {
  return rows.map((r) => (r.key === key ? { ...r, entry: { ...r.entry, ...patch } } : r));
}

/**
 * Switching how the prompt is passed: "stdin" takes no prompt arguments (they're cleared, the
 * server rejects them), "arg" starts with a lone `{prompt}` line when there are none.
 */
export function setPromptVia(
  rows: readonly RuntimeRow[],
  key: string,
  via: PromptVia,
): RuntimeRow[] {
  return rows.map((r) => {
    if (r.key !== key || r.entry.prompt_via === via) return r;
    const prompt_args =
      via === "stdin" ? [] : r.entry.prompt_args.length > 0 ? r.entry.prompt_args : ["{prompt}"];
    return { ...r, entry: { ...r.entry, prompt_via: via, prompt_args } };
  });
}

// ------------------------------------------------------------------ lines ↔ arguments

/** One argument per line; blank (whitespace-only) lines are ignored. Lines are kept as typed. */
export function linesToArgs(text: string): string[] {
  return text.split("\n").filter((line) => line.trim() !== "");
}

export function argsToLines(args: readonly string[]): string {
  return args.join("\n");
}

export function sameArgs(a: readonly string[], b: readonly string[]): boolean {
  return a.length === b.length && a.every((x, i) => x === b[i]);
}

// ------------------------------------------------------------------ validation

export type RuntimeField =
  | "name"
  | "argv"
  | "model_args"
  | "effort_args"
  | "agent_args"
  | "prompt_args"
  | "session_args"
  | "resume_args"
  | "verdict_args"
  | "verdict_from";
export type RuntimeIssues = Partial<Record<RuntimeField, string>>;

export interface RuntimesIssues {
  /** Whole-list problems (count). */
  form: string[];
  /** One entry per row, same order. */
  rows: RuntimeIssues[];
}

function argsIssue(args: readonly string[]): string | undefined {
  if (args.length > MAX_RUNTIME_ARGS) return `At most ${String(MAX_RUNTIME_ARGS)} arguments.`;
  if (args.some((a) => a.length > RUNTIME_ARG_MAX)) {
    return `An argument is over ${String(RUNTIME_ARG_MAX)} characters.`;
  }
  if (args.some((a) => a.includes("\0"))) return "Arguments can't contain NUL characters.";
  return undefined;
}

/** Client mirror of the server's checks, for inline messages. */
export function validateRuntimes(rows: readonly RuntimeRow[]): RuntimesIssues {
  const form: string[] = [];
  if (rows.length < 1) form.push("Add at least one runtime.");
  if (rows.length > MAX_RUNTIMES) form.push(`At most ${String(MAX_RUNTIMES)} runtimes.`);

  const counts = new Map<string, number>();
  for (const r of rows) counts.set(r.entry.name, (counts.get(r.entry.name) ?? 0) + 1);

  const issues = rows.map(({ entry: e }) => {
    const out: RuntimeIssues = {};
    if (!RUNTIME_NAME_RE.test(e.name)) {
      out.name = "Lowercase letters, digits and . _ -; starts with a letter or digit; at most 32.";
    } else if ((counts.get(e.name) ?? 0) > 1) {
      out.name = "Another runtime already uses this name.";
    }

    const first = e.argv[0];
    if (first === undefined) out.argv = "Add the program, then one argument per line.";
    else if (first.trim() === "") out.argv = "The first line (the program) can't be blank.";
    else {
      const argv = argsIssue(e.argv);
      if (argv) out.argv = argv;
    }
    const model = argsIssue(e.model_args);
    if (model) out.model_args = model;
    const effort = argsIssue(effortArgs(e));
    if (effort) out.effort_args = effort;
    const agent = argsIssue(e.agent_args);
    if (agent) out.agent_args = agent;

    const prompt = argsIssue(e.prompt_args);
    const prompts = e.prompt_args.filter((a) => a === PLACEHOLDER.prompt_args).length;
    if (prompt) out.prompt_args = prompt;
    else if (e.prompt_via === "arg" && prompts === 0) {
      out.prompt_args = "Needs a line that is exactly {prompt}.";
    } else if (e.prompt_via === "arg" && prompts > 1) {
      out.prompt_args = "Only one {prompt} line.";
    } else if (e.prompt_via === "stdin" && e.prompt_args.length > 0) {
      out.prompt_args = "Only used when the prompt is passed as an argument.";
    }

    for (const [field, args] of [
      ["session_args", sessionArgs(e)],
      ["resume_args", resumeArgs(e)],
    ] as const) {
      const issue = argsIssue(args);
      if (issue) out[field] = issue;
      else if (args.length > 0 && !args.some((a) => a.includes(SESSION))) {
        out[field] = "Needs {session}: a line of its own, or inside one (--resume={session}).";
      }
    }
    const verdict = argsIssue(verdictArgs(e));
    if (verdict) out.verdict_args = verdict;
    if (e.verdict_from === "structured_output" && verdictArgs(e).length === 0) {
      out.verdict_from = "Structured output needs verdict arguments that ask the CLI for it.";
    }
    return out;
  });
  return { form, rows: issues };
}

export function runtimesIssueCount(issues: RuntimesIssues): number {
  return issues.form.length + issues.rows.reduce((n, r) => n + Object.keys(r).length, 0);
}

/** "Fix 1 issue first" / "Fix N issues first", or undefined when there are none. */
export function fixFirst(n: number): string | undefined {
  if (n === 0) return undefined;
  return `Fix ${n === 1 ? "1 issue" : `${String(n)} issues`} first`;
}

/**
 * Placeholders that won't be replaced, and groups that won't work as typed, as advice (the
 * server accepts them; they'd reach the CLI literally). A placeholder is replaced only in its own
 * group: `{model}` in model arguments, `{effort}` in effort arguments, `{agent}` in agent
 * arguments, `{session}` in session and resume arguments, `{verdict_schema}` in verdict
 * arguments, `{prompt}` in prompt arguments. All but `{session}` only as a whole line.
 */
export function placeholderNotes(e: RuntimeEntry): string[] {
  const notes: string[] = [];
  const effort = effortArgs(e);
  const groups: [keyof typeof GROUP_LABEL, readonly string[]][] = [
    ["argv", e.argv],
    ["model_args", e.model_args],
    ["effort_args", effort],
    ["agent_args", e.agent_args],
    ["session_args", sessionArgs(e)],
    ["resume_args", resumeArgs(e)],
    ["verdict_args", verdictArgs(e)],
    ["prompt_args", e.prompt_via === "arg" ? e.prompt_args : []],
  ];
  for (const [group, args] of groups) {
    const own = group === "argv" ? undefined : PLACEHOLDER[group];
    for (const arg of args) {
      if (arg === own) continue;
      for (const ph of PLACEHOLDERS) {
        if (!arg.includes(ph)) continue;
        if (ph === own) {
          if (ph === SESSION) continue; // may be part of an argument
          notes.push(`"${arg}" stays as typed: ${ph} is replaced only when it is a whole line.`);
        } else {
          notes.push(
            `${ph} in ${GROUP_LABEL[group]} stays as typed: it is replaced only in ${PLACEHOLDER_HOME[ph]}.`,
          );
        }
      }
    }
  }
  if (e.model_args.length > 0 && !e.model_args.includes(PLACEHOLDER.model_args)) {
    notes.push("Model arguments have no {model} line, so the model isn't passed.");
  }
  if (effort.length > 0 && !effort.includes(PLACEHOLDER.effort_args)) {
    notes.push("Effort arguments have no {effort} line, so the effort isn't passed.");
  }
  if (e.agent_args.length > 0 && !e.agent_args.includes(PLACEHOLDER.agent_args)) {
    notes.push("Agent arguments have no {agent} line, so the agent isn't passed.");
  }
  const verdict = verdictArgs(e);
  if (verdict.length > 0 && !verdict.includes(PLACEHOLDER.verdict_args)) {
    notes.push("Verdict arguments have no {verdict_schema} line, so the schema isn't passed.");
  }
  if (sessionArgs(e).length > 0 !== resumeArgs(e).length > 0) {
    notes.push(
      "Sessions need both session and resume arguments: with one, loop stages start fresh each round and invalid verdicts aren't repaired.",
    );
  }
  return notes;
}

const GROUP_LABEL = {
  argv: "the command",
  model_args: "model arguments",
  effort_args: "effort arguments",
  agent_args: "agent arguments",
  session_args: "session arguments",
  resume_args: "resume arguments",
  verdict_args: "verdict arguments",
  prompt_args: "prompt arguments",
} as const;

/** Where each placeholder is replaced. */
const PLACEHOLDER_HOME: Record<(typeof PLACEHOLDERS)[number], string> = {
  "{model}": GROUP_LABEL.model_args,
  "{effort}": GROUP_LABEL.effort_args,
  "{agent}": GROUP_LABEL.agent_args,
  "{session}": "session and resume arguments",
  "{verdict_schema}": GROUP_LABEL.verdict_args,
  "{prompt}": GROUP_LABEL.prompt_args,
};

// ------------------------------------------------------------------ command preview

export interface PreviewToken {
  text: string;
  /** A value filled in at dispatch (the stage's model, effort, agent or prompt). */
  slot: boolean;
}

export interface CommandPreview {
  tokens: PreviewToken[];
  /** The prompt goes to the program's stdin. */
  stdin: boolean;
}

const SLOT = {
  "{model}": "<model>",
  "{effort}": "<effort>",
  "{agent}": "<agent>",
  "{verdict_schema}": "<verdict schema>",
  "{prompt}": "<prompt>",
} as const;

/**
 * Which call to preview: any stage's; a loop check's first round (it starts a session and asks
 * for a verdict); or a verdict repair (it resumes that session and asks again). A maker's later
 * round resumes like a repair, without the verdict arguments.
 */
export type PreviewCall = "stage" | "check" | "repair";

/**
 * The argv a stage with a model, an effort and an agent would run (manifest.py `_agent`, `_check`):
 * `argv`, then `model_args`, `effort_args`, `agent_args`, then for loop calls `session_args` or
 * `resume_args` and `verdict_args`, then `prompt_args` when the prompt is an argument. Each
 * group's own placeholder, as a whole element, stands for the value (`{session}` also inside
 * one); anything else is literal.
 */
export function commandPreview(e: RuntimeEntry, call: PreviewCall = "stage"): CommandPreview {
  const lit = (args: readonly string[]) => args.map((text) => ({ text, slot: false }));
  const sub = (args: readonly string[], ph: keyof typeof SLOT) =>
    args.map((a) => (a === ph ? { text: SLOT[ph], slot: true } : { text: a, slot: false }));
  const session = (args: readonly string[]) =>
    args.map((a) =>
      a.includes(SESSION)
        ? { text: a.replaceAll(SESSION, "<session>"), slot: true }
        : { text: a, slot: false },
    );
  const loop =
    call === "stage"
      ? []
      : [
          ...session(call === "check" ? sessionArgs(e) : resumeArgs(e)),
          ...sub(verdictArgs(e), "{verdict_schema}"),
        ];
  const tokens = [
    ...lit(e.argv),
    ...sub(e.model_args, "{model}"),
    ...sub(effortArgs(e), "{effort}"),
    ...sub(e.agent_args, "{agent}"),
    ...loop,
    ...(e.prompt_via === "arg" ? sub(e.prompt_args, "{prompt}") : []),
  ];
  return { tokens, stdin: e.prompt_via === "stdin" };
}

/** The calls worth previewing: a loop check's when the runtime has session or verdict
 *  arguments, a repair's when it can resume. */
export function previewCalls(e: RuntimeEntry): PreviewCall[] {
  const calls: PreviewCall[] = ["stage"];
  if (sessionArgs(e).length > 0 || verdictArgs(e).length > 0) calls.push("check");
  if (hasSessions(e)) calls.push("repair");
  return calls;
}

/** How an argument reads in the preview: quoted when it's empty or has spaces or quotes. */
export function shownArg(text: string): string {
  return text === "" || /[\s"']/.test(text) ? JSON.stringify(text) : text;
}

/** The preview's argv as one line of text (the collapsed row's summary). */
export function previewLine(p: CommandPreview): string {
  return p.tokens.map((t) => (t.slot ? t.text : shownArg(t.text))).join(" ");
}
