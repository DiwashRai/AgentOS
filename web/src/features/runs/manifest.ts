// Pure helpers for "How each stage was called" (Runs tab): read a run's manifest, exactly what the
// worker received, into display pieces. The argv is the runtime's template already resolved by the
// server (backend `pipeline/manifest.py` `resolve_agent`: `argv`, then `model_args`, then
// `agent_args`, then `prompt_args`), so nothing here knows which CLI a runtime runs: the chips
// are picked out by flag name on any program.
import { ApiError, type Schemas } from "../../api/client";

export type Manifest = Schemas["Manifest"];
export type ManifestStage = Schemas["ManifestStage"];

/** The flags worth a chip, in chip order: flag → label. Add a row to pick out another flag. */
export const FLAG_LABELS: readonly { flag: string; label: string }[] = [
  { flag: "--agent", label: "Agent" },
  { flag: "--model", label: "Model" },
  { flag: "--effort", label: "Effort" },
];

/** Stands in for a prompt passed as an argument, which is long. */
export const PROMPT_PLACEHOLDER = "‹prompt›";

export interface ArgChip {
  label: string;
  value: string;
}

/** The value of each `FLAG_LABELS` flag in `argv` (as `--flag value` or `--flag=value`), in
 *  `FLAG_LABELS` order. A repeated flag shows its last value (what CLIs usually keep). Elements in
 *  `skip` (the prompt) are never read as a flag or a value. */
export function argChips(argv: readonly string[], skip: number | null = null): ArgChip[] {
  const found = new Map<string, string>();
  for (let i = 1; i < argv.length; i++) {
    if (i === skip) continue;
    const arg = argv[i] ?? "";
    for (const { flag, label } of FLAG_LABELS) {
      if (arg === flag) {
        const next = argv[i + 1];
        if (next !== undefined && i + 1 !== skip) found.set(label, next);
      } else if (arg.startsWith(`${flag}=`)) {
        found.set(label, arg.slice(flag.length + 1));
      }
    }
  }
  return FLAG_LABELS.flatMap(({ label }) => {
    const value = found.get(label);
    return value === undefined ? [] : [{ label, value }];
  });
}

/** Where an agent stage's prompt sits in its argv, or null when the prompt is the stdin (or the
 *  stage is a shell stage). The server appends `prompt_args` last, so the prompt is the last
 *  element, unless an earlier one is multi-line (a prompt the template put before its tail). */
export function promptIndex(stage: ManifestStage): number | null {
  const argv = stage.argv ?? [];
  if (stage.kind !== "agent" || (stage.stdin ?? null) !== null || argv.length < 2) return null;
  for (let i = argv.length - 1; i >= 1; i--) {
    if ((argv[i] ?? "").includes("\n")) return i;
  }
  const last = argv.length - 1;
  return (argv[last] ?? "").startsWith("-") ? null : last;
}

const PLAIN = /^[A-Za-z0-9_@%+=:,./-]+$/;

/** One argv element as a shell would need it written: bare when safe, else single-quoted. */
export function shellWord(arg: string): string {
  return PLAIN.test(arg) ? arg : `'${arg.replace(/'/g, `'\\''`)}'`;
}

export type TokenKind = "program" | "flag" | "arg" | "prompt";

export interface CommandToken {
  text: string;
  kind: TokenKind;
}

/** The argv as words to show: the program, flags, other arguments, and the prompt as a
 *  placeholder. */
export function commandTokens(argv: readonly string[], prompt: number | null): CommandToken[] {
  return argv.map((arg, i) => {
    if (i === prompt) return { text: PROMPT_PLACEHOLDER, kind: "prompt" };
    const text = shellWord(arg);
    if (i === 0) return { text, kind: "program" };
    return { text, kind: arg.startsWith("-") ? "flag" : "arg" };
  });
}

export type ManifestInvocation = Schemas["ManifestInvocation"];

/** Stands in for what the worker appends to a later call's prompt before its `tail`. */
export const FEEDBACK_PLACEHOLDER = "‹the failing checks' notes›";
export const ERROR_PLACEHOLDER = "‹why the verdict was invalid›";

/** Another call of a loop stage: a later round, or a verdict repair (request 24). */
export interface LaterCall {
  key: string;
  kind: "round" | "repair";
  /** The round it runs in. */
  round: number;
  tokens: CommandToken[];
  /** The prompt as the worker sends it, with the text it appends shown as a placeholder. */
  prompt: string;
}

function laterCall(inv: ManifestInvocation, kind: LaterCall["kind"], round: number): LaterCall {
  const at = inv.stdin === null ? inv.prompt_arg : null;
  const base = inv.stdin ?? (at === null ? "" : (inv.argv[at] ?? ""));
  const added = kind === "round" ? FEEDBACK_PLACEHOLDER : ERROR_PLACEHOLDER;
  return {
    key: `${kind}${String(round)}`,
    kind,
    round,
    tokens: commandTokens(inv.argv, at),
    prompt: `${base}${added}${inv.tail}`,
  };
}

/** A loop stage's later calls: rounds 2..max (`rounds`), then one repair call per round. */
export function laterCalls(stage: Pick<ManifestStage, "rounds" | "repairs">): LaterCall[] {
  return [
    ...(stage.rounds ?? []).map((inv, i) => laterCall(inv, "round", i + 2)),
    ...(stage.repairs ?? []).map((inv, i) => laterCall(inv, "repair", i + 1)),
  ];
}

/** The later calls' fold-out label: "Later rounds (2)", "Repairs (3)", or both. */
export function laterLabel(calls: readonly Pick<LaterCall, "kind">[]): string {
  const rounds = calls.filter((c) => c.kind === "round").length;
  const repairs = calls.length - rounds;
  if (rounds === 0) return `Repairs (${String(repairs)})`;
  const head = `Later rounds (${String(rounds)})`;
  return repairs === 0 ? head : `${head} and repairs (${String(repairs)})`;
}

/** How a loop stage decides a round, as short meta words ("check · verdict file · 2 repairs");
 *  empty for a stage that isn't a check. */
export function checkMeta(
  stage: Pick<ManifestStage, "check" | "kind" | "verdict_from" | "verdict_repairs">,
): string[] {
  if (!stage.check) return [];
  if (stage.kind === "shell") return ["check", "exit code"];
  const from = stage.verdict_from ?? null;
  const n = stage.verdict_repairs;
  return [
    "check",
    from === "structured_output" ? "structured output" : from === "file" ? "verdict file" : "",
    n > 0 ? `up to ${String(n)} repair${n === 1 ? "" : "s"}` : "no repair",
  ].filter(Boolean);
}

export interface StageCall {
  id: string;
  name: string;
  kind: ManifestStage["kind"];
  timeoutS: number;
  chips: ArgChip[];
  /** The agent's command line (null for a shell stage). */
  tokens: CommandToken[] | null;
  /** The agent's prompt and how it was given (null for a shell stage, or no prompt found). */
  prompt: { text: string; via: "stdin" | "arg" } | null;
  /** A shell stage's command. */
  command: string | null;
  /** A check's meta words (empty otherwise). */
  check: string[];
  /** A loop stage's later-round and repair calls. */
  later: LaterCall[];
}

export function stageCall(stage: ManifestStage): StageCall {
  const base = {
    id: stage.id,
    name: stage.name,
    kind: stage.kind,
    timeoutS: stage.timeout_s,
    check: checkMeta(stage),
    later: laterCalls(stage),
  };
  if (stage.kind === "shell") {
    return { ...base, chips: [], tokens: null, prompt: null, command: stage.command ?? "" };
  }
  const argv = stage.argv ?? [];
  const at = promptIndex(stage);
  const stdin = stage.stdin ?? null;
  const prompt =
    stdin !== null
      ? { text: stdin, via: "stdin" as const }
      : at !== null
        ? { text: argv[at] ?? "", via: "arg" as const }
        : null;
  return {
    ...base,
    chips: argChips(argv, at),
    tokens: commandTokens(argv, at),
    prompt,
    command: null,
  };
}

/** The fold-out's label for a prompt. */
export function promptLabel(via: "stdin" | "arg"): string {
  return via === "stdin" ? "Prompt (stdin)" : "Prompt";
}

/** The context files' paths (never their contents), sorted so a path is easy to find. */
export function filePaths(manifest: Pick<Manifest, "files">): string[] {
  return manifest.files.map((f) => f.path).sort((a, b) => (a < b ? -1 : a > b ? 1 : 0));
}

/** `30s`, `15m`, `2h`, `1h 30m`: a stage's time limit. */
export function timeoutText(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${String(s)}s`;
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (h === 0) return `${String(m)}m`;
  return m === 0 ? `${String(h)}h` : `${String(h)}h ${String(m)}m`;
}

/** What the section says when the manifest can't be shown. The server answers 404 `not_found`
 *  until a worker claims the run (backend `runs/service.py` `get_manifest`). */
export function manifestErrorText(error: Error): { text: string; quiet: boolean } {
  if (error instanceof ApiError && error.status === 404 && error.code === "not_found") {
    return { text: "Built when a worker picks the run up", quiet: true };
  }
  return { text: `Can't load how the stages were called · ${error.message}`, quiet: false };
}
