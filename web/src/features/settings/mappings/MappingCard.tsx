// Settings › Field mappings: one card per target (today only Base branch). Without a mapping it
// says what imports do instead and offers "Add a mapping"; with one, it edits the Jira field, the
// ordered rules and runs "Try it" (one preview mutation per card, so its result belongs here).
import { useState } from "react";

import { usePreviewMapping } from "../../../api/queries";
import { Button } from "../../../ui";
import { Field, Select } from "../../pipeline/fields";
import s from "./Mappings.module.css";
import {
  fieldKind,
  fieldOptions,
  fieldsProblem,
  kindWord,
  previewRequest,
  tryBlock,
  tryLine,
  type JiraField,
  type MappingDraft,
  type MappingIssues,
  type RuleRow,
  type TargetSpec,
  type TryMode,
} from "./model";
import { RulesEditor } from "./RulesEditor";
import { TryIt } from "./TryIt";

/** The Jira field list as the card needs it (from `useJiraFields`). */
export interface FieldsState {
  data: readonly JiraField[] | undefined;
  pending: boolean;
  fetching: boolean;
  error: unknown;
  retry: () => void;
}

export interface MappingCardProps {
  spec: TargetSpec;
  draft: MappingDraft | undefined;
  issues: MappingIssues | undefined;
  fields: FieldsState;
  readOnly: boolean;
  onAdd: () => void;
  onRemove: () => void;
  onChange: (patch: Partial<Omit<MappingDraft, "target">>) => void;
}

export function MappingCard(props: MappingCardProps) {
  const { spec, draft } = props;
  const headId = `mapping-${spec.target}`;
  return (
    <section className={s.card} aria-labelledby={headId}>
      <div className={s.cardHead}>
        <h3 id={headId} className={s.cardTitle}>
          {spec.title}
        </h3>
        <span className={s.spacer} />
        {draft && !props.readOnly ? (
          <Button
            size="sm"
            variant="quiet"
            aria-label={`Remove the ${spec.title.toLowerCase()} mapping`}
            onClick={props.onRemove}
          >
            Remove mapping
          </Button>
        ) : null}
      </div>
      <p className={s.lead}>{spec.explainer}</p>
      {draft ? (
        <MappingBody {...props} draft={draft} issues={props.issues ?? { rules: [], form: [] }} />
      ) : (
        <div className={s.empty}>
          <p className={s.none}>{spec.without}</p>
          {props.readOnly ? null : (
            <Button size="sm" onClick={props.onAdd}>
              Add a mapping
            </Button>
          )}
        </div>
      )}
    </section>
  );
}

function MappingBody(props: MappingCardProps & { draft: MappingDraft; issues: MappingIssues }) {
  const { spec, draft, issues, fields } = props;
  const preview = usePreviewMapping();
  const [mode, setMode] = useState<TryMode>("values");
  const [text, setText] = useState("");

  const request = previewRequest(draft, mode, text);
  const line = tryLine(
    {
      ran: preview.variables,
      pending: preview.isPending,
      data: preview.data,
      error: preview.error,
    },
    request,
  );

  return (
    <>
      <FieldPicker
        draft={draft}
        error={issues.field}
        fields={fields}
        readOnly={props.readOnly}
        onPick={(field, field_name) => {
          props.onChange({ field, field_name });
        }}
      />
      <RulesEditor
        spec={spec}
        rules={draft.rules}
        issues={issues.rules}
        form={issues.form}
        matched={line.kind === "result" ? line.rule : null}
        readOnly={props.readOnly}
        onChange={(rules: readonly RuleRow[]) => {
          props.onChange({ rules });
        }}
      />
      <TryIt
        spec={spec}
        fieldName={draft.field_name}
        mode={mode}
        text={text}
        line={line}
        block={tryBlock(issues, mode, text)}
        onMode={(next) => {
          setMode(next);
          preview.reset();
        }}
        onText={setText}
        onRun={() => {
          if (request) preview.mutate(request);
        }}
      />
    </>
  );
}

interface FieldPickerProps {
  draft: MappingDraft;
  error: string | undefined;
  fields: FieldsState;
  readOnly: boolean;
  onPick: (field: string, fieldName: string) => void;
}

function FieldPicker({ draft, error, fields, readOnly, onPick }: FieldPickerProps) {
  const list = fields.data;
  const options = fieldOptions(list, draft);
  const kind = fieldKind(list, draft.field);
  const problem = fields.error ? fieldsProblem(fields.error) : null;
  const hint =
    draft.field === "" ? (
      "The Jira field the rules read, e.g. Fix versions."
    ) : (
      <>
        id <code className={s.code}>{draft.field}</code>
        {kind ? ` · ${kindWord(kind)}` : null}
      </>
    );

  return (
    <div className={s.fieldRow}>
      <Field label="Jira field" hint={hint} error={error} className={s.picker}>
        {({ id, describedBy, invalid }) => (
          <Select
            id={id}
            value={draft.field}
            options={options}
            disabled={readOnly || !list}
            aria-describedby={describedBy}
            aria-invalid={invalid}
            className={s.select}
            onChange={(value) => {
              const picked = list?.find((x) => x.id === value);
              if (picked) onPick(picked.id, picked.name);
            }}
          />
        )}
      </Field>
      {fields.pending ? (
        <p className={s.k} role="status">
          Loading Jira&apos;s fields…
        </p>
      ) : problem ? (
        <p className={s.fieldProblem} role="status">
          <span>{problem.text}</span>
          {problem.retry ? (
            <Button size="sm" disabled={fields.fetching} onClick={fields.retry}>
              {fields.fetching ? "Retrying…" : "Retry"}
            </Button>
          ) : null}
        </p>
      ) : null}
    </div>
  );
}
