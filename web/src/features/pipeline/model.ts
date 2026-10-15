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
export type Loop = Schemas["Loop"];

export const PHASES: readonly StagePhase[] = ["plan", "build", "verify", "review"];

// Limits from models.py (Stage, Pipeline).
export const ID_RE = /^[a-z][a-z0-9-]{0,31}$/;
export const AGENT_RE = /^[A-Za-z0-9._:/-]{1,100}$/;
/** A reasoning effort: a free per-CLI token (Claude takes low|medium|high|…, others differ). */
export const EFFORT_RE = /^[A-Za-z0-9._-]{1,32}$/;
export const NAME_MAX = 60;
export const PROMPT_MAX = 65536; // characters (pydantic max_length)
export const MIN_STAGES = 1;
export const MAX_STAGES = 20;
export const TIMEOUT_MIN = 60;
export const TIMEOUT_MAX = 21600;
export const TIMEOUT_DEFAULT = 3600;
// Loop blocks (models.py `Loop`, `Stage.verdict_repairs`).
export const LOOP_MAX_ROUNDS = 5;
export const LOOP_DEFAULT_ROUNDS = 3;
export const VERDICT_REPAIRS_MAX = 3;
export const VERDICT_REPAIRS_DEFAULT = 2;

// ------------------------------------------------------------------ loop roles

/** Inside a loop block: a shell stage, or an agent stage with `check` (segment.py `is_check`).
 *  A check passes or fails the round; every other stage is a maker. */
export function isCheck(st: Stage): boolean {
  return st.kind === "shell" || st.check;
}

export type LoopRole = "check" | "maker";

export function loopRole(st: Stage): LoopRole {
  return isCheck(st) ? "check" : "maker";
}

/** Whether the stage resumes its session in later rounds: its own `resume`, else by role
 *  (makers resume, checks start fresh). */
export function resumes(st: Stage): boolean {
  return st.resume ?? !isCheck(st);
}

// ------------------------------------------------------------------ draft

/** One editor row. `fresh` = added in this editor and never saved, so its id may still change. */
export interface Row {
  uid: number;
  fresh: boolean;
  stage: Stage;
}

export interface Draft {
  rows: Row[];
  /** Loop blocks, by stage id (as the server stores them). Edits keep them consistent: a
   *  move keeps each block's positions, a remove shrinks the block, a rename follows the stage. */
  loops: Loop[];
  nextUid: number;
  /** The pipeline the draft came from: `toPipeline` builds the PUT body on top of it. */
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
  return { rows, loops: p.loops ?? [], nextUid, base: p };
}

/** The draft as a PUT body. `loops` is left out only when the base had none and none were added. */
export function toPipeline(d: Draft): Pipeline {
  const p: Pipeline = { ...d.base, stages: d.rows.map((r) => r.stage) };
  if (d.base.loops !== undefined || d.loops.length > 0) p.loops = d.loops;
  return p;
}

/** Editable stage fields. `id` changes go through `setId` (only fresh rows), `kind` never changes. */
export type StagePatch = Partial<Omit<Stage, "id" | "kind">>;

export type Action =
  | { type: "move"; uid: number; delta: -1 | 1 }
  | { type: "add"; kind: StageKind }
  | { type: "remove"; uid: number }
  | { type: "patch"; uid: number; patch: StagePatch }
  | { type: "setId"; uid: number; id: string }
  | { type: "addLoop" }
  | { type: "patchLoop"; index: number; patch: Partial<Loop> }
  | { type: "removeLoop"; index: number };

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
    effort: null,
    command: "",
    timeout_s: TIMEOUT_DEFAULT,
    check: false,
    verdict_repairs: VERDICT_REPAIRS_DEFAULT,
    resume: null,
  };
}

/** Pure edit step. Impossible edits (moving past an end, renaming a saved stage, removing the
 *  last stage, adding a 21st, adding a loop with no room for one) return the draft unchanged. */
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
      return { ...d, rows, loops: loopsAfterMove(d, rows) };
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
      const gone = d.rows.find((r) => r.uid === a.uid);
      if (!gone) return d;
      const rows = d.rows.filter((r) => r !== gone);
      return { ...d, rows, loops: loopsAfterRemove(d, gone.stage.id) };
    }
    case "patch":
      return mapRow(d, a.uid, (r) => ({ ...r, stage: { ...r.stage, ...a.patch } }));
    case "setId": {
      const row = d.rows.find((r) => r.uid === a.uid);
      if (!row?.fresh) return d;
      const next = mapRow(d, a.uid, (r) => ({ ...r, stage: { ...r.stage, id: a.id } }));
      // Loops follow the renamed stage, unless another stage shares its old id (mid-typing).
      const old = row.stage.id;
      if (d.rows.filter((r) => r.stage.id === old).length > 1) return next;
      const rename = (id: string) => (id === old ? a.id : id);
      return {
        ...next,
        loops: d.loops.map((l) =>
          l.start === old || l.end === old
            ? { ...l, start: rename(l.start), end: rename(l.end) }
            : l,
        ),
      };
    }
    case "addLoop": {
      const loop = suggestLoop(
        d.rows.map((r) => r.stage),
        d.loops,
      );
      return loop ? { ...d, loops: [...d.loops, loop] } : d;
    }
    case "patchLoop": {
      const loop = d.loops[a.index];
      if (!loop) return d;
      return { ...d, loops: d.loops.with(a.index, { ...loop, ...a.patch }) };
    }
    case "removeLoop":
      return a.index in d.loops ? { ...d, loops: d.loops.filter((_, i) => i !== a.index) } : d;
  }
}

/** A move keeps each block where it is (the same positions), so a stage moves into or out of a
 *  block across its edge rather than dragging the edge along. Blocks whose ids aren't found are
 *  left alone (validation flags them). */
function loopsAfterMove(d: Draft, rows: readonly Row[]): Loop[] {
  const pos = positions(d.rows.map((r) => r.stage));
  return d.loops.map((l) => {
    const start = rows[pos.get(l.start) ?? -1]?.stage.id;
    const end = rows[pos.get(l.end) ?? -1]?.stage.id;
    return start !== undefined && end !== undefined ? { ...l, start, end } : l;
  });
}

/** Removing a block's first or last stage shrinks the block to the next one in; a block left
 *  with no stage is dropped. */
function loopsAfterRemove(d: Draft, id: string): Loop[] {
  const stages = d.rows.map((r) => r.stage);
  const pos = positions(stages);
  const at = pos.get(id) ?? -1;
  const out: Loop[] = [];
  for (const l of d.loops) {
    if (l.start !== id && l.end !== id) {
      out.push(l);
      continue;
    }
    const start = pos.get(l.start) ?? -1;
    const end = pos.get(l.end) ?? -1;
    if (start < 0 || end < 0 || start >= end) continue; // its only stage, or not a block
    if (l.start === id) {
      const next = stages[at + 1];
      if (next) out.push({ ...l, start: next.id });
    } else {
      const prev = stages[at - 1];
      if (prev) out.push({ ...l, end: prev.id });
    }
  }
  return out;
}

/** Each stage id's position (the first, if ids repeat). */
function positions(stages: readonly Stage[]): Map<string, number> {
  const pos = new Map<string, number>();
  stages.forEach((st, i) => {
    if (!pos.has(st.id)) pos.set(st.id, i);
  });
  return pos;
}

/**
 * The loop "+ Loop" adds: from the first free, enabled maker (an agent stage that isn't a check
 * and has no sign-off, which only a block's last stage may have) to the first enabled check after
 * it, else the next enabled agent stage (to be marked Check), else the stage right after it, all
 * within stages no other block covers. Else any two free stages in a row; null if there are none.
 */
export function suggestLoop(stages: readonly Stage[], loops: readonly Loop[]): Loop | null {
  const taken = new Set<number>();
  for (const b of drawnBlocks(stages, loops)) {
    for (let i = b.from; i <= b.to; i++) taken.add(i);
  }
  const free = (i: number) => i < stages.length && !taken.has(i);
  const make = (i: number, j: number): Loop | null => {
    const a = stages[i];
    const b = stages[j];
    return a && b ? { start: a.id, end: b.id, max_rounds: LOOP_DEFAULT_ROUNDS } : null;
  };
  for (let i = 0; i < stages.length; i++) {
    const st = stages[i];
    if (!st || !free(i) || !st.enabled || isCheck(st) || st.gate_after) continue;
    let check: number | null = null;
    let agent: number | null = null;
    for (let j = i + 1; free(j); j++) {
      const next = stages[j];
      if (!next) break;
      if (next.enabled && isCheck(next)) {
        check = j;
        break;
      }
      if (agent === null && next.enabled && next.kind === "agent") agent = j;
      if (next.gate_after) break; // only a block's last stage may have a sign-off
    }
    const end = check ?? agent ?? (free(i + 1) ? i + 1 : null);
    if (end !== null) return make(i, end);
  }
  for (let i = 0; i + 1 < stages.length; i++) {
    if (free(i) && free(i + 1)) return make(i, i + 1);
  }
  return null;
}

/** A block the editor can draw: positions `from`..`to` (inclusive) of loop `loop`. */
export interface Block {
  loop: number;
  from: number;
  to: number;
}

/**
 * The blocks to draw around the stage rows, in order: loops whose ids exist with start at or
 * before end, skipping one that overlaps a block already taken (validation flags those).
 */
export function drawnBlocks(stages: readonly Stage[], loops: readonly Loop[]): Block[] {
  const pos = positions(stages);
  const spans: Block[] = [];
  loops.forEach((l, loop) => {
    const from = pos.get(l.start);
    const to = pos.get(l.end);
    if (from !== undefined && to !== undefined && from <= to) spans.push({ loop, from, to });
  });
  spans.sort((a, b) => a.from - b.from || a.loop - b.loop);
  const out: Block[] = [];
  for (const b of spans) {
    const last = out.at(-1);
    if (!last || b.from > last.to) out.push(b);
  }
  return out;
}

/** The stage list cut into runs: single rows outside blocks, and each drawn block's rows. */
export type Segment =
  { kind: "row"; index: number } | { kind: "block"; block: Block; indexes: number[] };

export function segments(stages: readonly Stage[], loops: readonly Loop[]): Segment[] {
  const blocks = drawnBlocks(stages, loops);
  const out: Segment[] = [];
  let i = 0;
  for (const block of blocks) {
    for (; i < block.from; i++) out.push({ kind: "row", index: i });
    const indexes: number[] = [];
    for (; i <= block.to; i++) indexes.push(i);
    out.push({ kind: "block", block, indexes });
  }
  for (; i < stages.length; i++) out.push({ kind: "row", index: i });
  return out;
}

/** A loop's stages by role (names), or null when its ids don't make a block. Disabled stages
 *  count, as in validation. */
export function blockRoles(
  stages: readonly Stage[],
  loop: Loop,
): { makers: string[]; checks: string[] } | null {
  const pos = positions(stages);
  const from = pos.get(loop.start);
  const to = pos.get(loop.end);
  if (from === undefined || to === undefined || from > to) return null;
  const makers: string[] = [];
  const checks: string[] = [];
  for (const st of stages.slice(from, to + 1)) {
    (isCheck(st) ? checks : makers).push(st.name.trim() || st.id);
  }
  return { makers, checks };
}

/** "Loop · up to 3 rounds" (the band over a block). */
export function loopLabel(maxRounds: number): string {
  return maxRounds === 1 ? "Loop · 1 round" : `Loop · up to ${String(maxRounds)} rounds`;
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
  | "id"
  | "name"
  | "prompt"
  | "runtime"
  | "agent"
  | "model"
  | "effort"
  | "command"
  | "timeout_s"
  | "verdict_repairs"
  | "gate_after";
export type StageIssues = Partial<Record<StageField, string>>;

export type LoopField = "start" | "end" | "max_rounds" | "block";
export type LoopIssues = Partial<Record<LoopField, string>>;

export interface Issues {
  /** Whole-pipeline problems (stage count). */
  form: string[];
  /** One entry per stage, same order as `stages`. */
  stages: StageIssues[];
  /** One entry per loop, same order as `loops`. */
  loops: LoopIssues[];
}

/** `validatePipeline` over a whole pipeline (its loops included): what a SaveBar counts. */
export function pipelineIssues(p: Pipeline, runtimes: readonly string[]): Issues {
  return validatePipeline(p.stages, runtimes, p.loops ?? []);
}

/**
 * Client-side mirror of the server's checks, for inline messages. `runtimes` = /api/config.
 * Loop checks (segment.py `_check_loops`) count disabled stages, as the server does.
 */
export function validatePipeline(
  stages: readonly Stage[],
  runtimes: readonly string[],
  loops: readonly Loop[] = [],
): Issues {
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
      if (st.effort != null && !EFFORT_RE.test(st.effort)) out.effort = EFFORT_HINT;
    }
    if (
      !Number.isInteger(st.verdict_repairs) ||
      st.verdict_repairs < 0 ||
      st.verdict_repairs > VERDICT_REPAIRS_MAX
    ) {
      out.verdict_repairs = `A whole number from 0 to ${String(VERDICT_REPAIRS_MAX)}.`;
    }
    return out;
  });
  const loopIssues = validateLoops(stages, loops, issues);
  return { form, stages: issues, loops: loopIssues };
}

const PATTERN_HINT = "Letters, digits and . _ : / - only; at most 100.";
const EFFORT_HINT = "Letters, digits and . _ - only; at most 32.";

/** The loop rules; a sign-off inside a block goes on that stage's issues (`gate_after`). */
function validateLoops(
  stages: readonly Stage[],
  loops: readonly Loop[],
  stageIssues: StageIssues[],
): LoopIssues[] {
  const pos = positions(stages);
  const label = (st: Stage) => st.name.trim() || st.id;
  const spans: { loop: number; from: number; to: number }[] = [];
  const out = loops.map((l, n) => {
    const issues: LoopIssues = {};
    if (!Number.isInteger(l.max_rounds) || l.max_rounds < 1 || l.max_rounds > LOOP_MAX_ROUNDS) {
      issues.max_rounds = `A whole number from 1 to ${String(LOOP_MAX_ROUNDS)}.`;
    }
    const from = pos.get(l.start);
    const to = pos.get(l.end);
    if (from === undefined) issues.start = `No stage "${l.start}".`;
    if (to === undefined) issues.end = `No stage "${l.end}".`;
    if (from === undefined || to === undefined) return issues;
    if (from > to) {
      issues.end = "Pick a stage at or after the first one.";
      return issues;
    }
    const block = stages.slice(from, to + 1);
    if (!block.some(isCheck)) {
      issues.block = "The loop needs a check: a shell stage, or an agent stage marked Check.";
    } else if (block.every(isCheck)) {
      issues.block =
        "The loop needs an agent stage that isn't a check, to fix what the checks find.";
    }
    const last = stages[to];
    for (let i = from; i < to; i++) {
      const st = stages[i];
      const own = stageIssues[i];
      if (st?.gate_after && last && own && own.gate_after === undefined) {
        own.gate_after = `Only the loop's last stage (${label(last)}) may have a sign-off.`;
      }
    }
    spans.push({ loop: n, from, to });
    return issues;
  });
  // Overlaps: each block against the ones that start before it.
  spans.sort((a, b) => a.from - b.from || a.loop - b.loop);
  spans.forEach((b, k) => {
    const earlier = spans.slice(0, k).find((e) => b.from <= e.to);
    const issues = out[b.loop];
    if (earlier && issues && issues.block === undefined) {
      issues.block = `Overlaps loop ${String(earlier.loop + 1)}.`;
    }
  });
  return out;
}

export function issueCount(issues: Issues): number {
  const count = (list: readonly object[]) =>
    list.reduce((n, item) => n + Object.keys(item).length, 0);
  return issues.form.length + count(issues.stages) + count(issues.loops);
}

/** An optional text field: blank → null (the server's "use the default"). */
export function optional(value: string): string | null {
  const v = value.trim();
  return v === "" ? null : v;
}

/** A select whose first option ("") means unset → null. */
export function unsetIfEmpty(value: string): string | null {
  return value === "" ? null : value;
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
    loops: (p.loops ?? []).map((l) => [l.start, l.end, l.max_rounds]),
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
      st.effort ?? null,
      st.command,
      st.timeout_s,
      st.check,
      st.verdict_repairs,
      st.resume ?? null,
    ]),
  });
}

// ------------------------------------------------------------------ loop settings (choices)

/** The Resume select's values: "" = by role (null), else "yes" / "no". */
export type ResumeChoice = "" | "yes" | "no";

export function resumeChoice(resume: boolean | null | undefined): ResumeChoice {
  return resume == null ? "" : resume ? "yes" : "no";
}

export function resumeFromChoice(choice: string): boolean | null {
  return choice === "yes" ? true : choice === "no" ? false : null;
}

/** The Resume select's options; the default one says what the stage's role makes it do. */
export function resumeOptions(st: Stage): { value: ResumeChoice; label: string }[] {
  const byRole = isCheck(st) ? "start fresh" : "resume";
  return [
    { value: "", label: `By role (${byRole})` },
    { value: "yes", label: "Resume" },
    { value: "no", label: "Start fresh" },
  ];
}

/** 1..max as select options. */
export function countOptions(min: number, max: number): { value: string; label: string }[] {
  const out: { value: string; label: string }[] = [];
  for (let n = min; n <= max; n++) out.push({ value: String(n), label: String(n) });
  return out;
}

/** A loop end select's choices: every stage, keeping an unknown current id selectable so it
 *  shows (and fails validation) instead of vanishing. */
export function loopStageOptions(
  stages: readonly Stage[],
  current: string,
): { value: string; label: string }[] {
  const seen = new Set<string>();
  const opts: { value: string; label: string }[] = [];
  for (const st of stages) {
    if (seen.has(st.id)) continue; // a duplicate id (flagged on the stage) is one choice
    seen.add(st.id);
    opts.push({ value: st.id, label: st.name.trim() || st.id });
  }
  return seen.has(current) ? opts : [...opts, { value: current, label: `${current} (missing)` }];
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

// ------------------------------------------------------------------ Space defaults

/** The template's Space defaults as the admin set them (missing or null = not set). */
export type Defaults = Schemas["TemplateDefaults"];

/** Unset is always `null` (never "" or missing), as the PUT body sends it. */
export interface DefaultsBody {
  runtime: string | null;
  model: string | null;
  effort: string | null;
}

/**
 * The effective Space defaults a stage with an unset runtime/model/effort falls back to.
 * `runtime: null` = a default whose name isn't known; `model: null` / `effort: null` = no Space
 * model / effort (the CLI's own default).
 */
export interface SpaceDefaults {
  runtime: string | null;
  model: string | null;
  effort: string | null;
}

export const CLI_DEFAULT = "The CLI's default";

export function defaultsBody(d: Defaults): DefaultsBody {
  return { runtime: d.runtime ?? null, model: d.model ?? null, effort: d.effort ?? null };
}

export function sameDefaults(a: Defaults, b: Defaults): boolean {
  return (
    (a.runtime ?? null) === (b.runtime ?? null) &&
    (a.model ?? null) === (b.model ?? null) &&
    (a.effort ?? null) === (b.effort ?? null)
  );
}

/** /api/config's effective defaults (the template's, else the server's runtime, no model and no
 *  effort). The way every pipeline editor builds its `space` prop. */
export function configDefaults(config: {
  default_runtime: string;
  default_model: string | null;
  default_effort: string | null;
}): SpaceDefaults {
  return {
    runtime: config.default_runtime,
    model: config.default_model,
    effort: config.default_effort,
  };
}

/** The Space runtime select's unset option, naming the server's own runtime. */
export function serverRuntimeLabel(config: { server_default_runtime: string }): string {
  return `Server default (${config.server_default_runtime})`;
}

/** Runtime choices: unset ("") first, then every runtime, keeping an unknown current value
 *  selectable so it shows (and fails validation) instead of vanishing. */
export function runtimeOptions(
  runtimes: readonly string[],
  unsetLabel: string,
  current: string | null | undefined,
): { value: string; label: string }[] {
  const names = current && !runtimes.includes(current) ? [...runtimes, current] : runtimes;
  return [{ value: "", label: unsetLabel }, ...names.map((r) => ({ value: r, label: r }))];
}

/** A stage's runtime choices; unset = the Space default. */
export function stageRuntimeOptions(
  runtimes: readonly string[],
  space: SpaceDefaults,
  current: string | null | undefined,
): { value: string; label: string }[] {
  const unset = space.runtime ? `Space default (${space.runtime})` : "Space default";
  return runtimeOptions(runtimes, unset, current);
}

/** A stage's unset-model placeholder. The Space model only applies to stages on the Space
 *  runtime; a stage on another runtime without its own model gets the CLI's default. */
export function stageModelPlaceholder(
  stageRuntime: string | null | undefined,
  space: SpaceDefaults,
): string {
  return spacePlaceholder(space.model, stageRuntime, space);
}

/** A stage's unset-effort placeholder: like the model, the Space effort only applies to stages
 *  on the Space runtime. */
export function stageEffortPlaceholder(
  stageRuntime: string | null | undefined,
  space: SpaceDefaults,
): string {
  return spacePlaceholder(space.effort, stageRuntime, space);
}

function spacePlaceholder(
  value: string | null,
  stageRuntime: string | null | undefined,
  space: SpaceDefaults,
): string {
  if (value == null) return CLI_DEFAULT;
  const onSpaceRuntime = stageRuntime == null || stageRuntime === space.runtime;
  return onSpaceRuntime ? `Space default (${value})` : CLI_DEFAULT;
}

/** The Agent field's hint: the runtime passes the name on (`--agent`), and Claude Code reads the
 *  agent from `.claude/agents/<name>.md` in the workspace, so say where that file can come from. */
export function agentHint(agent: string | null | undefined): string {
  const name = agent?.trim() ?? "";
  const file = `.claude/agents/${name || "<name>"}.md`;
  return name === ""
    ? `Optional. Claude Code reads it from ${file}.`
    : `Needs ${file}: in the repo, or added on the ticket's Context tab.`;
}

export type DefaultsIssues = Partial<Record<"runtime" | "model" | "effort", string>>;

/** Mirrors models.py `TemplateDefaults` (runtime must be configured; model and effort patterns). */
export function validateDefaults(d: Defaults, runtimes: readonly string[]): DefaultsIssues {
  const out: DefaultsIssues = {};
  if (d.runtime != null && !runtimes.includes(d.runtime)) {
    out.runtime = `Unknown runtime "${d.runtime}".`;
  }
  if (d.model != null && !AGENT_RE.test(d.model)) out.model = PATTERN_HINT;
  if (d.effort != null && !EFFORT_RE.test(d.effort)) out.effort = EFFORT_HINT;
  return out;
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
