// The pipeline editor's pure core: the draft (rows with a stable uid, so a row keeps its React
// identity while its id is being typed), the edit reducer, client-side checks that mirror
// models.py `Stage`/`Pipeline` and segment.py `validate_pipeline`, and the preview model.
// The server stays the authority: it re-validates on save and its 422 message is shown as-is.
import { ApiError, type Schemas } from "../../api/client";
import type { GlyphModel, GlyphStage } from "../../domain/stage";

export type Pipeline = Schemas["Pipeline"];
export type Stage = Schemas["Stage"];
export type StageKind = Stage["kind"];
export type StagePhase = Stage["phase"];

export const PHASES: readonly StagePhase[] = ["plan", "build", "verify", "review"];

// Limits from models.py (Stage, Pipeline).
export const ID_RE = /^[a-z][a-z0-9-]{0,31}$/;
export const AGENT_RE = /^[A-Za-z0-9._:/-]{1,100}$/;
export const NAME_MAX = 60;
export const PROMPT_MAX = 65536; // characters (pydantic max_length)
export const MIN_STAGES = 1;
export const MAX_STAGES = 20;
export const TIMEOUT_MIN = 60;
export const TIMEOUT_MAX = 21600;
export const TIMEOUT_DEFAULT = 3600;

// ------------------------------------------------------------------ draft

/** One editor row. `fresh` = added in this editor and never saved, so its id may still change. */
export interface Row {
  uid: number;
  fresh: boolean;
  stage: Stage;
}

export interface Draft {
  rows: Row[];
  nextUid: number;
  /** The pipeline the draft came from: fields the editor doesn't touch (library_sets) are carried
   *  through from it unchanged. */
  base: Pipeline;
}

/**
 * A draft for a pipeline that came from outside the editor (the server's copy, a discard, a
 * reload). Every stage in it counts as saved, so its id is fixed. Rows whose id matches a row of
 * `prev` keep that row's uid, so expanded rows and focus survive a save or a refetch.
 */
export function draftFrom(p: Pipeline, prev?: Draft): Draft {
  const uidById = new Map(prev?.rows.map((r) => [r.stage.id, r.uid]));
  let nextUid = prev?.nextUid ?? 1;
  const taken = new Set<number>();
  const rows = p.stages.map((stage) => {
    const kept = uidById.get(stage.id);
    let uid: number;
    if (kept !== undefined && !taken.has(kept)) {
      uid = kept;
    } else {
      uid = nextUid;
      nextUid += 1;
    }
    taken.add(uid);
    return { uid, fresh: false, stage };
  });
  return { rows, nextUid, base: p };
}

export function toPipeline(d: Draft): Pipeline {
  return { ...d.base, stages: d.rows.map((r) => r.stage) };
}

/** Editable stage fields. `id` changes go through `setId` (only fresh rows), `kind` never changes. */
export type StagePatch = Partial<Omit<Stage, "id" | "kind">>;

export type Action =
  | { type: "move"; uid: number; delta: -1 | 1 }
  | { type: "add"; kind: StageKind }
  | { type: "remove"; uid: number }
  | { type: "patch"; uid: number; patch: StagePatch }
  | { type: "setId"; uid: number; id: string };

/** A new stage's id: `stage`, `stage-2`, … (or `shell`, `shell-2`, …), unique in the draft. */
export function freshId(kind: StageKind, taken: Iterable<string>): string {
  const used = new Set(taken);
  const base = kind === "agent" ? "stage" : "shell";
  if (!used.has(base)) return base;
  for (let n = 2; ; n++) {
    const id = `${base}-${String(n)}`;
    if (!used.has(id)) return id;
  }
}

export function newStage(kind: StageKind, taken: Iterable<string>): Stage {
  return {
    id: freshId(kind, taken),
    name: kind === "agent" ? "New stage" : "Shell step",
    kind,
    phase: kind === "agent" ? "build" : "verify",
    enabled: true,
    gate_after: false,
    requires_repo: false,
    prompt: "",
    runtime: null,
    agent: null,
    model: null,
    command: "",
    timeout_s: TIMEOUT_DEFAULT,
  };
}

/** Pure edit step. Impossible edits (moving past an end, renaming a saved stage, removing the
 *  last stage, adding a 21st) return the draft unchanged. */
export function reduce(d: Draft, a: Action): Draft {
  switch (a.type) {
    case "move": {
      const i = d.rows.findIndex((r) => r.uid === a.uid);
      const j = i + a.delta;
      if (i < 0 || j < 0 || j >= d.rows.length) return d;
      const rows = [...d.rows];
      const moved = rows[i];
      const other = rows[j];
      if (!moved || !other) return d;
      rows[i] = other;
      rows[j] = moved;
      return { ...d, rows };
    }
    case "add": {
      if (d.rows.length >= MAX_STAGES) return d;
      const stage = newStage(
        a.kind,
        d.rows.map((r) => r.stage.id),
      );
      return {
        ...d,
        rows: [...d.rows, { uid: d.nextUid, fresh: true, stage }],
        nextUid: d.nextUid + 1,
      };
    }
    case "remove": {
      if (d.rows.length <= MIN_STAGES) return d;
      const rows = d.rows.filter((r) => r.uid !== a.uid);
      return rows.length === d.rows.length ? d : { ...d, rows };
    }
    case "patch":
      return mapRow(d, a.uid, (r) => ({ ...r, stage: { ...r.stage, ...a.patch } }));
    case "setId":
      return mapRow(d, a.uid, (r) => (r.fresh ? { ...r, stage: { ...r.stage, id: a.id } } : r));
  }
}

function mapRow(d: Draft, uid: number, f: (r: Row) => Row): Draft {
  const i = d.rows.findIndex((r) => r.uid === uid);
  const row = d.rows[i];
  if (!row) return d;
  const next = f(row);
  return next === row ? d : { ...d, rows: d.rows.with(i, next) };
}

/** A row's id field is editable only until the stage is first saved (SPEC §6: ids are immutable). */
export function idEditable(row: Row): boolean {
  return row.fresh;
}

// ------------------------------------------------------------------ validation

export type StageField =
  "id" | "name" | "prompt" | "runtime" | "agent" | "model" | "command" | "timeout_s";
export type StageIssues = Partial<Record<StageField, string>>;

export interface Issues {
  /** Whole-pipeline problems (stage count). */
  form: string[];
  /** One entry per stage, same order as `stages`. */
  stages: StageIssues[];
}

/** Client-side mirror of the server's checks, for inline messages. `runtimes` = /api/config. */
export function validatePipeline(stages: readonly Stage[], runtimes: readonly string[]): Issues {
  const form: string[] = [];
  if (stages.length < MIN_STAGES) form.push("A pipeline needs at least one stage.");
  if (stages.length > MAX_STAGES) form.push(`A pipeline has at most ${String(MAX_STAGES)} stages.`);

  const counts = new Map<string, number>();
  for (const st of stages) counts.set(st.id, (counts.get(st.id) ?? 0) + 1);

  const issues = stages.map((st) => {
    const out: StageIssues = {};
    if (!ID_RE.test(st.id)) {
      out.id = "Lowercase letters, digits and dashes; starts with a letter; at most 32.";
    } else if ((counts.get(st.id) ?? 0) > 1) {
      out.id = "Another stage already uses this id.";
    }
    if (st.name.trim().length === 0) out.name = "Give the stage a name.";
    else if (st.name.length > NAME_MAX) out.name = `At most ${String(NAME_MAX)} characters.`;
    if (
      !Number.isInteger(st.timeout_s) ||
      st.timeout_s < TIMEOUT_MIN ||
      st.timeout_s > TIMEOUT_MAX
    ) {
      out.timeout_s = `A whole number of seconds from ${String(TIMEOUT_MIN)} to ${String(TIMEOUT_MAX)}.`;
    }
    if (st.kind === "shell") {
      if (st.enabled && st.command.trim().length === 0) {
        out.command = "An enabled shell stage needs a command.";
      }
    } else {
      if (st.prompt.length > PROMPT_MAX) out.prompt = "The prompt is over 64 KiB.";
      if (st.runtime != null && !runtimes.includes(st.runtime)) {
        out.runtime = `Unknown runtime "${st.runtime}".`;
      }
      if (st.agent != null && !AGENT_RE.test(st.agent)) out.agent = PATTERN_HINT;
      if (st.model != null && !AGENT_RE.test(st.model)) out.model = PATTERN_HINT;
    }
    return out;
  });
  return { form, stages: issues };
}

const PATTERN_HINT = "Letters, digits and . _ : / - only; at most 100.";

export function issueCount(issues: Issues): number {
  return issues.form.length + issues.stages.reduce((n, st) => n + Object.keys(st).length, 0);
}

/** An optional text field: blank → null (the server's "use the default"). */
export function optional(value: string): string | null {
  const v = value.trim();
  return v === "" ? null : v;
}

// ------------------------------------------------------------------ preview

/**
 * The stepper preview of a draft *configuration* (not ticket state): Inbox, every stage
 * (disabled → skipped), Done, and a pending sign-off diamond after each enabled gated stage.
 */
export function previewModel(p: Pipeline): GlyphModel {
  const stages: GlyphStage[] = [
    { name: "Inbox", state: "upcoming" },
    ...p.stages.map((st): GlyphStage => {
      const name = st.name.trim() || st.id;
      return {
        name,
        state: st.enabled ? "upcoming" : "skipped",
        gate:
          st.enabled && st.gate_after
            ? { label: `${name} sign-off`, short: "Sign-off", state: "pending" }
            : undefined,
      };
    }),
    { name: "Done", state: "upcoming" },
  ];
  return { stages, label: "", live: false };
}

/** Enabled stages, in order: the choices for "Run from…". */
export function runnableStages(p: Pipeline): Stage[] {
  return p.stages.filter((st) => st.enabled);
}

/** Same configuration? (Missing optional fields equal null, as the server reads them.) */
export function samePipeline(a: Pipeline, b: Pipeline): boolean {
  return canonical(a) === canonical(b);
}

function canonical(p: Pipeline): string {
  return JSON.stringify({
    library_sets: p.library_sets ?? [],
    stages: p.stages.map((st) => [
      st.id,
      st.name,
      st.kind,
      st.phase,
      st.enabled,
      st.gate_after,
      st.requires_repo,
      st.prompt,
      st.runtime ?? null,
      st.agent ?? null,
      st.model ?? null,
      st.command,
      st.timeout_s,
    ]),
  });
}

/** What a failed save tells the user, and whether it offers Reload (a 409 `stale_version`). */
export interface SaveProblem {
  text: string;
  reload: boolean;
}

export function describeSaveError(e: unknown): SaveProblem {
  if (e instanceof ApiError) {
    if (e.code === "stale_version") {
      return { text: "Changed by someone else. Reload (your draft will be lost).", reload: true };
    }
    if (e.code === "run_active") {
      return { text: "A run is active; edit the pipeline when it finishes.", reload: false };
    }
    return { text: e.message, reload: false };
  }
  return { text: e instanceof Error ? e.message : "Something went wrong.", reload: false };
}

/** "1 h", "45 min", "1 h 30 min", "90 s": a reading aid next to the seconds field. */
export function formatTimeout(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return "";
  if (seconds % 60 !== 0) return `${String(seconds)} s`;
  const min = seconds / 60;
  const h = Math.floor(min / 60);
  const m = min % 60;
  if (h === 0) return `${String(m)} min`;
  return m === 0 ? `${String(h)} h` : `${String(h)} h ${String(m)} min`;
}
