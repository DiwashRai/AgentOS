// Settings › Field mappings, pure: the draft (one mapping per target, each with ordered rules that
// carry a client-side `key`), the PUT body, checks that mirror the server (models.py FieldMapping /
// MappingRule, validate.py `validate_base_branch`), where a 422 points, the field picker's options,
// and what "Try it" shows. The server owns the wildcard matcher: nothing here matches rules.
import { ApiError, type Schemas } from "../../../api/client";
import { describeSaveError } from "../../pipeline/model";
import { checkBranch } from "../space/model";

export type FieldMappings = Schemas["FieldMappings"];
export type FieldMappingsUpdate = Schemas["FieldMappingsUpdate"];
export type FieldMapping = Schemas["FieldMapping"];
export type MappingTarget = FieldMapping["target"];
export type JiraField = Schemas["JiraField"];
export type MappingPreview = Schemas["MappingPreview"];
export type MappingPreviewRequest = Schemas["MappingPreviewRequest"];

/** Server limits (models.py MAPPING_MATCH_MAX, MAX_MAPPING_RULES, MAX_PREVIEW_VALUES, …). */
export const MATCH_MAX = 200;
export const MAX_RULES = 100;
export const MAX_PREVIEW_VALUES = 100;
export const PREVIEW_VALUE_MAX = 1000;
export const ISSUE_MAX = 2000;
const FIELD_ID_RE = /^[A-Za-z0-9_.:-]{1,255}$/;

/** The targets a mapping can set, in card order (the closed `MappingTarget` set). */
export interface TargetSpec {
  target: MappingTarget;
  title: string;
  explainer: string;
  /** What the rule's value column is called. */
  valueLabel: string;
  valuePlaceholder: string;
  /** What an import does without this mapping. */
  without: string;
}

export const TARGETS: readonly TargetSpec[] = [
  {
    target: "base_branch",
    title: "Base branch",
    explainer:
      "Set from a Jira field when a ticket is imported or taken in. The first matching rule wins; no match leaves it blank, and the ticket can't run until someone sets it.",
    valueLabel: "Branch",
    valuePlaceholder: "App-1.Latest",
    without: "No mapping: imported tickets start from their repo's default branch.",
  },
];

// ------------------------------------------------------------------ draft

/** One rule in the draft. `key` is the React key: `s:<n>` for a saved rule, `n:<n>` for a new one. */
export interface RuleRow {
  key: string;
  match: string;
  value: string;
}

export interface MappingDraft {
  target: MappingTarget;
  field: string;
  field_name: string;
  rules: readonly RuleRow[];
}

/** In saved order, so an index here is the index in the PUT body (and in a 422's path). */
export type MappingsForm = readonly MappingDraft[];

export function formOf(doc: Pick<FieldMappings, "mappings">): MappingsForm {
  return doc.mappings.map((m) => ({
    target: m.target,
    field: m.field,
    field_name: m.field_name,
    rules: m.rules.map((r, i) => ({ key: `s:${String(i)}`, match: r.match, value: r.value })),
  }));
}

/** Same content, ignoring the rule keys (so undoing an edit makes the draft clean again). */
export function sameForm(a: MappingsForm, b: MappingsForm): boolean {
  return (
    a.length === b.length &&
    a.every((m, i) => {
      const o = b[i];
      return (
        o !== undefined &&
        sameTarget(m.target, o.target) &&
        m.field === o.field &&
        m.field_name === o.field_name &&
        m.rules.length === o.rules.length &&
        m.rules.every((r, j) => r.match === o.rules[j]?.match && r.value === o.rules[j].value)
      );
    })
  );
}

/** The server's shape for one draft mapping, trimmed (the server strips too). */
export function toMapping(d: MappingDraft): FieldMapping {
  return {
    target: d.target,
    source: "jira",
    field: d.field.trim(),
    field_name: d.field_name.trim(),
    rules: d.rules.map((r) => ({ match: r.match.trim(), value: r.value.trim() })),
  };
}

export function toBody(form: MappingsForm, baseVersion: number): FieldMappingsUpdate {
  return { mappings: form.map(toMapping), base_version: baseVersion };
}

/** Targets compared as plain strings: the closed set has one member today and grows later. */
function sameTarget(a: string, b: string): boolean {
  return a === b;
}

export function indexOf(form: MappingsForm, target: MappingTarget): number {
  return form.findIndex((m) => sameTarget(m.target, target));
}

/** A new mapping for `target` with one empty rule to fill in (no-op if it has one already). */
export function addMapping(form: MappingsForm, target: MappingTarget): MappingsForm {
  if (indexOf(form, target) >= 0) return form;
  return [...form, { target, field: "", field_name: "", rules: [newRule([])] }];
}

/** Nothing typed yet (no field, every rule empty): removing it needs no confirm. */
export function isBlankMapping(d: MappingDraft): boolean {
  return d.field === "" && d.rules.every((r) => r.match.trim() === "" && r.value.trim() === "");
}

export function removeMapping(form: MappingsForm, target: MappingTarget): MappingsForm {
  return form.filter((m) => !sameTarget(m.target, target));
}

export function patchMapping(
  form: MappingsForm,
  target: MappingTarget,
  patch: Partial<Omit<MappingDraft, "target">>,
): MappingsForm {
  return form.map((m) => (sameTarget(m.target, target) ? { ...m, ...patch } : m));
}

function newRule(rules: readonly RuleRow[]): RuleRow {
  let n = 0;
  for (const r of rules) {
    const m = /^n:(\d+)$/.exec(r.key);
    if (m) n = Math.max(n, Number(m[1]));
  }
  return { key: `n:${String(n + 1)}`, match: "", value: "" };
}

/** Append an empty rule (last = lowest priority). */
export function addRule(rules: readonly RuleRow[]): readonly RuleRow[] {
  return [...rules, newRule(rules)];
}

export function removeRule(rules: readonly RuleRow[], index: number): readonly RuleRow[] {
  return rules.filter((_, i) => i !== index);
}

/** Swap with the rule above (-1) or below (1); out of range = unchanged. Order is priority. */
export function moveRule(
  rules: readonly RuleRow[],
  index: number,
  delta: -1 | 1,
): readonly RuleRow[] {
  const to = index + delta;
  const a = rules[index];
  const b = rules[to];
  if (a === undefined || b === undefined) return rules;
  const next = [...rules];
  next[index] = b;
  next[to] = a;
  return next;
}

export function patchRule(
  rules: readonly RuleRow[],
  index: number,
  patch: Partial<Pick<RuleRow, "match" | "value">>,
): readonly RuleRow[] {
  return rules.map((r, i) => (i === index ? { ...r, ...patch } : r));
}

// ------------------------------------------------------------------ validation

export interface RuleIssues {
  match?: string;
  value?: string;
}

export interface MappingIssues {
  field?: string;
  /** By index, one entry per rule (empty when the rule is fine). */
  rules: RuleIssues[];
  /** About the mapping as a whole (too many rules). */
  form: string[];
}

/** Each target's check of a rule's `value` (pipeline/mapping.py `_check_value`). */
const VALUE_CHECKS: Record<MappingTarget, (value: string) => string | undefined> = {
  base_branch: checkBranch, // validate.py `validate_base_branch`
};

export function valueIssue(target: MappingTarget, value: string): string | undefined {
  return VALUE_CHECKS[target](value.trim());
}

export function matchIssue(match: string): string | undefined {
  const v = match.trim();
  if (v === "") return "Required.";
  if (v.length > MATCH_MAX) return `At most ${String(MATCH_MAX)} characters.`;
  return undefined;
}

/** The field issue about the display name (shown under "Shown as" when the id is typed by hand). */
export const NAME_ISSUE = "Name the field, e.g. Fix versions.";

function fieldIssue(d: MappingDraft): string | undefined {
  if (d.field.trim() === "") return "Pick a Jira field, or enter its id.";
  if (!FIELD_ID_RE.test(d.field.trim())) return `${d.field} isn't a Jira field id.`;
  if (d.field_name.trim() === "") return NAME_ISSUE;
  return undefined;
}

export function validateMapping(d: MappingDraft): MappingIssues {
  const out: MappingIssues = { rules: [], form: [] };
  const field = fieldIssue(d);
  if (field !== undefined) out.field = field;
  out.rules = d.rules.map((r) => {
    const issues: RuleIssues = {};
    const match = matchIssue(r.match);
    const value = valueIssue(d.target, r.value);
    if (match !== undefined) issues.match = match;
    if (value !== undefined) issues.value = value;
    return issues;
  });
  if (d.rules.length > MAX_RULES) out.form.push(`At most ${String(MAX_RULES)} rules.`);
  return out;
}

/** By index, one entry per mapping. */
export function validate(form: MappingsForm): MappingIssues[] {
  return form.map(validateMapping);
}

function rulesCount(rules: readonly RuleIssues[]): number {
  return rules.reduce((n, r) => n + Object.keys(r).length, 0);
}

export function mappingIssueCount(m: MappingIssues): number {
  return (m.field === undefined ? 0 : 1) + rulesCount(m.rules) + m.form.length;
}

export function issueCount(issues: readonly MappingIssues[]): number {
  return issues.reduce((n, m) => n + mappingIssueCount(m), 0);
}

/** SaveBar's reason Save is off, or undefined. */
export function fixLine(count: number): string | undefined {
  if (count === 0) return undefined;
  return `Fix ${count === 1 ? "1 issue" : `${String(count)} issues`} first`;
}

// ------------------------------------------------------------------ the server's 422

/** Where a 422 `validation_error` points: `mappings.0.rules.3.value: …` (a save; FastAPI's own
 *  check prefixes `body.`) or `mapping.rules.3.value: …` (a preview, so no mapping index). */
export interface ServerIssue {
  mapping: number | null;
  rule: number | null;
  part: "match" | "value" | "field" | "field_name" | null;
  text: string;
}

const ISSUE_RE =
  /^(?:body\.)?(?:mappings\.(\d+)|mapping)\.(?:rules\.(\d+)\.(match|value)|(field_name|field))\s*:\s*([\s\S]*)$/;

export function locateIssue(message: string): ServerIssue | null {
  const m = ISSUE_RE.exec(message);
  if (!m) return null;
  const part = (m[3] ?? m[4] ?? null) as ServerIssue["part"];
  return {
    mapping: m[1] === undefined ? null : Number(m[1]),
    rule: m[2] === undefined ? null : Number(m[2]),
    part,
    text: m[5] ?? "",
  };
}

/**
 * A failed save's 422, put on the field it names, for as long as that field still holds what was
 * sent (an edit there drops it). By mapping index, like `validate`; null when it names nothing.
 */
export function serverIssues(
  error: unknown,
  sent: FieldMappingsUpdate | undefined,
  form: MappingsForm,
): { index: number; issues: MappingIssues } | null {
  if (!(error instanceof ApiError) || error.code !== "validation_error" || !sent) return null;
  const where = locateIssue(error.message);
  if (where?.mapping == null) return null;
  const draft = form[where.mapping];
  const was = sent.mappings[where.mapping];
  if (!draft || !was || !sameTarget(was.target, draft.target)) return null;
  const now = toMapping(draft);
  const issues: MappingIssues = { rules: draft.rules.map(() => ({})), form: [] };
  const text = sentence(where.text);
  if (where.rule !== null && (where.part === "match" || where.part === "value")) {
    const part = where.part;
    const sentRule = was.rules[where.rule];
    const nowRule = now.rules[where.rule];
    const row = issues.rules[where.rule];
    if (!sentRule || !nowRule || !row || sentRule[part] !== nowRule[part]) return null;
    row[part] = text;
  } else if (where.part === "field" || where.part === "field_name") {
    if (was.field !== now.field || was.field_name !== now.field_name) return null;
    issues.field = text;
  } else {
    return null;
  }
  return { index: where.mapping, issues };
}

/** The client's issues, with the server's filling the places the client found fine. */
export function mergeIssues(
  client: MappingIssues,
  server: MappingIssues | undefined,
): MappingIssues {
  if (!server) return client;
  return {
    ...(client.field !== undefined
      ? { field: client.field }
      : server.field !== undefined
        ? { field: server.field }
        : {}),
    rules: client.rules.map((r, i) => ({ ...server.rules[i], ...r })),
    form: client.form,
  };
}

function sentence(text: string): string {
  const t = text.trim();
  if (t === "") return "Not valid.";
  return t.charAt(0).toUpperCase() + t.slice(1);
}

// ------------------------------------------------------------------ the field picker

export interface FieldOption {
  value: string;
  label: string;
}

/**
 * The picker's options: Jira's fields by name (the server sorts them), a name shared by several
 * fields followed by its id. The mapping's current field is always there, even when the list
 * failed or no longer has it, and a mapping with no field yet starts on a "Choose" option.
 */
export function fieldOptions(
  fields: readonly JiraField[] | undefined,
  current: Pick<MappingDraft, "field" | "field_name">,
): FieldOption[] {
  const list = fields ?? [];
  const names = new Map<string, number>();
  for (const f of list) names.set(f.name, (names.get(f.name) ?? 0) + 1);
  const options = list.map((f) => ({
    value: f.id,
    label: (names.get(f.name) ?? 0) > 1 ? `${f.name} (${f.id})` : f.name,
  }));
  if (current.field === "") return [{ value: "", label: "Choose a Jira field" }, ...options];
  if (!list.some((f) => f.id === current.field)) {
    options.unshift({ value: current.field, label: current.field_name || current.field });
  }
  return options;
}

/**
 * A field id typed by hand (when Jira's list can't load, or the field isn't in it): the id as
 * typed, and the name Jira's list gives that id when it has it, else the name typed so far.
 */
export function typedField(
  fields: readonly JiraField[] | undefined,
  id: string,
  name: string,
): Pick<MappingDraft, "field" | "field_name"> {
  const known = fields?.find((f) => f.id === id.trim());
  return { field: id, field_name: known ? known.name : name };
}

/** The picker starts on typing the id when Jira's list isn't there once loading is over. */
export function listMissing(fields: { pending: boolean; data: unknown }): boolean {
  return !fields.pending && fields.data === undefined;
}

/** The picked field: its id, and what kind of value Jira holds there (when the list has it). */
export function fieldKind(
  fields: readonly JiraField[] | undefined,
  id: string,
): JiraField["kind"] | undefined {
  return fields?.find((f) => f.id === id)?.kind;
}

const KIND_WORD: Record<JiraField["kind"], string> = {
  version: "versions",
  option: "options",
  array: "a list",
  string: "text",
  other: "other",
};

export function kindWord(kind: JiraField["kind"]): string {
  return KIND_WORD[kind];
}

/** Why the field list isn't there, and whether Retry can help. */
export function fieldsProblem(e: unknown): { text: string; retry: boolean } {
  if (e instanceof ApiError && e.code === "not_implemented") {
    return {
      text: "Jira import isn't configured on this server, so its fields can't be listed.",
      retry: false,
    };
  }
  const why = e instanceof Error ? e.message : "Something went wrong.";
  return { text: `Can't load Jira's fields · ${why}`, retry: true };
}

// ------------------------------------------------------------------ Try it

export type TryMode = "issue" | "values";

/** Several values typed comma-separated: trimmed, blanks dropped. */
export function parseValues(text: string): string[] {
  return text
    .split(",")
    .map((v) => v.trim())
    .filter((v) => v !== "");
}

/** The preview request for the unsaved mapping and what was typed, or undefined if nothing is. */
export function previewRequest(
  draft: MappingDraft,
  mode: TryMode,
  text: string,
): MappingPreviewRequest | undefined {
  const mapping = toMapping(draft);
  if (mode === "issue") {
    const issue = text.trim();
    return issue === "" ? undefined : { mapping, issue };
  }
  const values = parseValues(text);
  return values.length === 0 ? undefined : { mapping, values };
}

/** Why Try it is off, or undefined. */
export function tryBlock(issues: MappingIssues, mode: TryMode, text: string): string | undefined {
  if (issues.field !== undefined) return "Pick a Jira field first";
  if (rulesCount(issues.rules) > 0 || issues.form.length > 0) return "Fix the rules first";
  if (mode === "issue") {
    const t = text.trim();
    if (t === "") return "Type an issue key or link";
    if (t.length > ISSUE_MAX) return `At most ${String(ISSUE_MAX)} characters`;
    return undefined;
  }
  const values = parseValues(text);
  if (values.length === 0) return "Type a value";
  if (values.length > MAX_PREVIEW_VALUES) return `At most ${String(MAX_PREVIEW_VALUES)} values`;
  if (values.some((v) => v.length > PREVIEW_VALUE_MAX)) {
    return `Each value at most ${String(PREVIEW_VALUE_MAX)} characters`;
  }
  return undefined;
}

export function sameRequest(a: MappingPreviewRequest, b: MappingPreviewRequest): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

/** What the Try it box shows. `rule` is the matched rule's index (to light its row). */
export type TryLine =
  | { kind: "none" }
  | { kind: "pending" }
  | { kind: "error"; text: string }
  | {
      kind: "result";
      read: readonly string[];
      value: string | null;
      rule: number | null;
      reason: string | null;
    };

export interface TryState {
  /** The request the last Try ran (the mutation's variables), if any. */
  ran: MappingPreviewRequest | undefined;
  pending: boolean;
  data: MappingPreview | undefined;
  error: unknown;
}

/** Why a preview failed, in the admin's words. */
export function tryErrorText(e: unknown): string {
  if (e instanceof ApiError) {
    switch (e.code) {
      case "not_implemented":
        return "Jira import isn't configured on this server, so issues can't be read. Try a value instead.";
      case "ticket_not_found":
        return `${e.message}. Check the key, or that the service account can see it.`;
      case "upstream_error":
        return `Jira didn't answer · ${e.message}`;
      case "validation_error": {
        const where = locateIssue(e.message);
        if (where !== null && where.rule !== null) {
          return `Rule ${String(where.rule + 1)}: ${sentence(where.text)}`;
        }
        // "issue: not a Jira issue key or link to one" → "Not a Jira issue key or link to one."
        const issue = /^(?:body\.)?issue\s*:\s*([\s\S]+)$/.exec(e.message);
        if (issue?.[1] !== undefined) return sentence(issue[1]).replace(/\.?$/, ".");
        return e.message;
      }
      default:
        return describeSaveError(e).text;
    }
  }
  return describeSaveError(e).text;
}

/**
 * The Try it line. A result only counts while the mapping and the input are still the ones that
 * ran, so any edit (a rule, the field, the text, Discard) hides it at once.
 */
export function tryLine(state: TryState, current: MappingPreviewRequest | undefined): TryLine {
  if (state.ran === undefined || current === undefined || !sameRequest(state.ran, current)) {
    return { kind: "none" };
  }
  if (state.pending) return { kind: "pending" };
  if (state.error !== null && state.error !== undefined) {
    return { kind: "error", text: tryErrorText(state.error) };
  }
  const d = state.data;
  if (!d) return { kind: "none" };
  return { kind: "result", read: d.values, value: d.value, rule: d.rule, reason: d.reason };
}

/** "Rule 2" for the matched rule's index. */
export function ruleWord(index: number): string {
  return `Rule ${String(index + 1)}`;
}
