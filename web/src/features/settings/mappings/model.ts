// Settings › Field mappings, pure: the form (keyed mappings, each with keyed ordered rules) and the
// document value it stands for, the row keys a 422's issues map back through, the field picker's
// options, and what "Try it" sends and shows. The server owns every rule and the wildcard matcher:
// nothing here checks or matches rules.
import { ApiError, type Schemas } from "../../../api/client";
import {
  issuesOf,
  pathOf,
  type FieldPath,
  type Issues,
  type SentKeys,
} from "../../../editor/issues";
import { keyed, keysOf, newKey, type Keyed } from "../../../editor/list";
import { describeSaveError } from "../../../editor/save";

export type MappingsDoc = Schemas["MappingsDoc"];
export type MappingEntry = Schemas["FieldMappingEntry"];
export type MappingTarget = MappingEntry["target"];
export type RuleEntry = Schemas["MappingRuleEntry"];
export type JiraField = Schemas["JiraField"];
export type MappingPreview = Schemas["MappingPreview"];
export type MappingPreviewRequest = Schemas["MappingPreviewRequest"];

/** The targets a mapping can set (the closed `MappingTarget` set), in list order. */
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

const SPECS: Record<MappingTarget, TargetSpec> = {
  base_branch: {
    target: "base_branch",
    title: "Base branch",
    explainer:
      "Set from a Jira field when a ticket is imported or taken in. The first matching rule wins; no match leaves it blank, and the ticket can't run until someone sets it.",
    valueLabel: "Branch",
    valuePlaceholder: "App-1.Latest",
    without: "No mapping: imported tickets start from their repo's default branch.",
  },
};

export const TARGETS: readonly TargetSpec[] = Object.values(SPECS);

export function specOf(target: MappingTarget): TargetSpec {
  return SPECS[target];
}

// ------------------------------------------------------------------ form

export type RuleRow = Keyed<RuleEntry>;

export interface MappingForm {
  target: MappingTarget;
  field: string;
  field_name: string;
  rules: readonly RuleRow[];
}

export type MappingRow = Keyed<MappingForm>;

export interface MappingsForm {
  mappings: readonly MappingRow[];
}

export function toForm(value: MappingsDoc): MappingsForm {
  return {
    mappings: value.mappings.map((m) => ({
      key: newKey(),
      item: { target: m.target, field: m.field, field_name: m.field_name, rules: keyed(m.rules) },
    })),
  };
}

/** One mapping as the server takes it, trimmed. */
export function toMapping(m: MappingForm): MappingEntry {
  return {
    target: m.target,
    source: "jira",
    field: m.field.trim(),
    field_name: m.field_name.trim(),
    rules: m.rules.map((r) => ({ match: r.item.match.trim(), value: r.item.value.trim() })),
  };
}

export function fromForm(form: MappingsForm): MappingsDoc {
  return { mappings: form.mappings.map((row) => toMapping(row.item)) };
}

function content(form: MappingsForm) {
  return form.mappings.map(({ item }) => ({ ...item, rules: item.rules.map((r) => r.item) }));
}

/** Same content, ignoring the row keys (so undoing an edit makes the draft clean again). */
export function sameForm(a: MappingsForm, b: MappingsForm): boolean {
  return JSON.stringify(content(a)) === JSON.stringify(content(b));
}

/** The row keys of every list in the form, by list path: the mappings and each one's rules. */
export function formKeys(form: MappingsForm): SentKeys {
  const keys: Record<FieldPath, readonly string[]> = { mappings: keysOf(form.mappings) };
  for (const row of form.mappings) keys[rulesPath(row.key)] = keysOf(row.item.rules);
  return keys;
}

export function mappingPath(key: string, ...rest: readonly string[]): FieldPath {
  return pathOf("mappings", key, ...rest);
}

export function rulesPath(mappingKey: string): FieldPath {
  return mappingPath(mappingKey, "rules");
}

export function rulePath(mappingKey: string, ruleKey: string, part?: "match" | "value"): FieldPath {
  return part === undefined
    ? pathOf(rulesPath(mappingKey), ruleKey)
    : pathOf(rulesPath(mappingKey), ruleKey, part);
}

/** Where the page shows issues; anything deeper lands on the nearest of these. */
export const SHOWN: readonly FieldPath[] = [
  "mappings",
  "mappings.*",
  "mappings.*.field",
  "mappings.*.field_name",
  "mappings.*.rules",
  "mappings.*.rules.*",
  "mappings.*.rules.*.match",
  "mappings.*.rules.*.value",
];

export function newRule(): RuleRow {
  return { key: newKey(), item: { match: "", value: "" } };
}

/** The first target without a mapping, or undefined when every target has one. */
export function freeTarget(form: MappingsForm): MappingTarget | undefined {
  const taken = new Set<string>(form.mappings.map((row) => row.item.target));
  return TARGETS.find((t) => !taken.has(t.target))?.target;
}

/** A new mapping for `target` with one empty rule to fill in. */
export function newMapping(target: MappingTarget): MappingRow {
  return { key: newKey(), item: { target, field: "", field_name: "", rules: [newRule()] } };
}

/** Nothing typed yet (no field, every rule empty): removing it needs no confirmation. */
export function isBlankMapping(m: MappingForm): boolean {
  return m.field === "" && m.rules.every(isBlankRule);
}

export function isBlankRule(r: RuleRow): boolean {
  return r.item.match.trim() === "" && r.item.value.trim() === "";
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
  current: Pick<MappingForm, "field" | "field_name">,
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
): Pick<MappingForm, "field" | "field_name"> {
  const known = fields?.find((f) => f.id === id.trim());
  return { field: id, field_name: known ? known.name : name };
}

/** The picker starts on typing the id when Jira's list isn't there once loading is over. */
export function listMissing(fields: { pending: boolean; data: unknown }): boolean {
  return !fields.pending && fields.data === undefined;
}

/** What kind of value Jira holds in the picked field (when the list has it). */
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
  mapping: MappingForm,
  mode: TryMode,
  text: string,
): MappingPreviewRequest | undefined {
  const sent = toMapping(mapping);
  if (mode === "issue") {
    const issue = text.trim();
    return issue === "" ? undefined : { mapping: sent, issue, values: null };
  }
  const values = parseValues(text);
  return values.length === 0 ? undefined : { mapping: sent, issue: null, values };
}

/** Where a preview's 422 issues show: the input, and the mapping's field, name and rules. */
const TRY_SHOWN: readonly FieldPath[] = [
  "issue",
  "values",
  "mapping",
  "mapping.field",
  "mapping.field_name",
  "mapping.rules",
  "mapping.rules.*",
  "mapping.rules.*.match",
  "mapping.rules.*.value",
];

/**
 * A failed preview's 422 as lines: the input's issues as they are, the mapping's prefixed with
 * the field they are about, named as the page names it ("Rule 2 branch: ..."). `ruleKeys` are the
 * rules' keys in the order sent.
 */
export function tryIssueLines(
  issues: Issues,
  ruleKeys: readonly string[],
  spec: TargetSpec,
): string[] {
  const lines: string[] = [];
  for (const [path, messages] of issues.fields) {
    const where = tryPlace(path, ruleKeys, spec);
    for (const message of messages) lines.push(where === "" ? message : `${where}: ${message}`);
  }
  return [...lines, ...issues.form];
}

function tryPlace(path: FieldPath, ruleKeys: readonly string[], spec: TargetSpec): string {
  if (path === "mapping.field") return "Jira field";
  if (path === "mapping.field_name") return "Shown as";
  if (path === "mapping.rules") return "Rules";
  if (path === "mapping") return "Mapping";
  for (const [i, key] of ruleKeys.entries()) {
    const rule = pathOf("mapping", "rules", key);
    if (path === rule) return ruleWord(i);
    if (path === pathOf(rule, "match")) return `${ruleWord(i)} ${ruleFieldName(spec, "match")}`;
    if (path === pathOf(rule, "value")) return `${ruleWord(i)} ${ruleFieldName(spec, "value")}`;
  }
  return "";
}

/** Why a preview failed, in the admin's words: one line, or one per issue of a 422. */
export function tryErrorLines(e: unknown, ruleKeys: readonly string[], spec: TargetSpec): string[] {
  if (e instanceof ApiError) {
    switch (e.code) {
      case "not_implemented":
        return [
          "Jira import isn't configured on this server, so issues can't be read. Try a value instead.",
        ];
      case "ticket_not_found":
        return [`${e.message}. Check the key, or that the service account can see it.`];
      case "upstream_error":
        return [`Jira didn't answer · ${e.message}`];
      case "validation_error": {
        const issues = issuesOf(e, {
          root: [],
          keys: { "mapping.rules": ruleKeys },
          shown: TRY_SHOWN,
        });
        const lines = tryIssueLines(issues, ruleKeys, spec);
        return lines.length > 0 ? lines : [e.message];
      }
      default:
        return [describeSaveError(e).text];
    }
  }
  return [describeSaveError(e).text];
}

/** What the Try it box shows. `rule` is the matched rule's index (to light its row). */
export type TryLine =
  | { kind: "none" }
  | { kind: "pending" }
  | { kind: "error"; lines: readonly string[] }
  | {
      kind: "result";
      read: readonly string[];
      value: string | null;
      rule: number | null;
      reason: string | null;
    };

/** The Try it line for the current input (`usePreview` drops a result the input moved past). */
export function tryLine(
  preview: { pending: boolean; result: MappingPreview | undefined; error: unknown },
  ruleKeys: readonly string[],
  spec: TargetSpec,
): TryLine {
  if (preview.pending) return { kind: "pending" };
  if (preview.error !== undefined) {
    return { kind: "error", lines: tryErrorLines(preview.error, ruleKeys, spec) };
  }
  const d = preview.result;
  if (!d) return { kind: "none" };
  return { kind: "result", read: d.values, value: d.value, rule: d.rule, reason: d.reason };
}

/** "Rule 2" for a rule's index. */
export function ruleWord(index: number): string {
  return `Rule ${String(index + 1)}`;
}

/** What a rule's match or value input is called ("Jira value matches", "branch"). */
export function ruleFieldName(spec: TargetSpec, part: "match" | "value"): string {
  return part === "match" ? "Jira value matches" : spec.valueLabel.toLowerCase();
}
