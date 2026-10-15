// Settings › Field mappings: one mapping's row in the mappings list. It edits the Jira field, the
// ordered rules and runs "Try it" on the unsaved mapping (its input and result belong to the row,
// so they follow it). Issues are the document's, placed by row key.
import { useId, useState } from "react";

import {
  errorAt,
  Field,
  Input,
  messagesAt,
  Select,
  usePreview,
  type Issues,
} from "../../../editor";
import { Button, LinkButton, text } from "../../../ui";
import s from "./Mappings.module.css";
import {
  fieldKind,
  fieldOptions,
  fieldsProblem,
  kindWord,
  listMissing,
  mappingPath,
  previewRequest,
  specOf,
  tryLine,
  typedField,
  type JiraField,
  type MappingForm,
  type MappingPreview,
  type MappingPreviewRequest,
  type MappingRow,
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
  row: MappingRow;
  issues: Issues;
  fields: FieldsState;
  /** Runs the preview ("Try it"); nothing is saved. */
  preview: (body: MappingPreviewRequest) => Promise<MappingPreview>;
  readOnly: boolean;
  onChange: (item: MappingForm) => void;
}

export function MappingCard(props: MappingCardProps) {
  const { row, issues, readOnly, onChange } = props;
  const mapping = row.item;
  const spec = specOf(mapping.target);
  const headId = useId();
  const [mode, setMode] = useState<TryMode>("values");
  const [input, setInput] = useState("");

  const request = previewRequest(mapping, mode, input);
  const preview = usePreview(props.preview, request);
  const ruleKeys = mapping.rules.map((r) => r.key);
  const line = tryLine(preview, ruleKeys, spec);

  return (
    <section className={s.card} aria-labelledby={headId}>
      <h3 id={headId} className={s.cardTitle}>
        {spec.title}
      </h3>
      <p className={s.lead}>{spec.explainer}</p>
      {messagesAt(issues, mappingPath(row.key)).map((msg) => (
        <p key={msg} className={text.error} role="alert">
          {msg}
        </p>
      ))}

      <FieldPicker
        mapping={mapping}
        fieldError={errorAt(issues, mappingPath(row.key, "field"))}
        nameError={errorAt(issues, mappingPath(row.key, "field_name"))}
        fields={props.fields}
        readOnly={readOnly}
        onPick={(field, field_name) => {
          onChange({ ...mapping, field, field_name });
        }}
      />
      <RulesEditor
        spec={spec}
        mappingKey={row.key}
        rules={mapping.rules}
        issues={issues}
        matched={line.kind === "result" ? line.rule : null}
        readOnly={readOnly}
        onChange={(rules) => {
          onChange({ ...mapping, rules });
        }}
      />
      <TryIt
        spec={spec}
        fieldName={mapping.field_name}
        mode={mode}
        text={input}
        line={line}
        canRun={request !== undefined}
        onMode={setMode}
        onText={setInput}
        onRun={preview.run}
      />
    </section>
  );
}

interface FieldPickerProps {
  mapping: MappingForm;
  fieldError: string | undefined;
  nameError: string | undefined;
  fields: FieldsState;
  readOnly: boolean;
  onPick: (field: string, fieldName: string) => void;
}

function FieldPicker({
  mapping,
  fieldError,
  nameError,
  fields,
  readOnly,
  onPick,
}: FieldPickerProps) {
  const list = fields.data;
  // Typing the id by hand: chosen, or the only way when Jira's list can't load.
  const [typing, setTyping] = useState(false);
  const manual = typing || listMissing(fields);
  const options = fieldOptions(list, mapping);
  const kind = fieldKind(list, mapping.field);
  const problem = fields.error ? fieldsProblem(fields.error) : null;
  const hint =
    mapping.field === "" ? (
      "The Jira field the rules read, e.g. Fix versions."
    ) : (
      <>
        id <code className={s.code}>{mapping.field}</code>
        {kind ? ` · ${kindWord(kind)}` : null}
      </>
    );
  const pickedError =
    fieldError === undefined && nameError === undefined
      ? undefined
      : [fieldError, nameError].filter((e) => e !== undefined).join(" ");
  const switcher = readOnly ? null : manual ? (
    list ? (
      <LinkButton
        className={s.switch}
        onClick={() => {
          setTyping(false);
        }}
      >
        Pick from Jira&apos;s list
      </LinkButton>
    ) : null
  ) : (
    <LinkButton
      className={s.switch}
      onClick={() => {
        setTyping(true);
      }}
    >
      Enter the id by hand
    </LinkButton>
  );

  return (
    <div className={s.fieldRow}>
      {manual ? (
        <div className={s.typed}>
          <Field
            label="Jira field id"
            hint="As Jira names it, e.g. fixVersions or customfield_10042."
            error={fieldError}
            className={s.picker}
          >
            {({ id, describedBy, invalid }) => (
              <Input
                id={id}
                mono
                value={mapping.field}
                placeholder="customfield_10042"
                readOnly={readOnly}
                spellCheck={false}
                autoComplete="off"
                aria-describedby={describedBy}
                aria-invalid={invalid}
                onChange={(e) => {
                  const next = typedField(list, e.target.value, mapping.field_name);
                  onPick(next.field, next.field_name);
                }}
              />
            )}
          </Field>
          <Field
            label="Shown as"
            hint="The name tickets show, e.g. Fix versions."
            error={nameError}
            className={s.picker}
          >
            {({ id, describedBy, invalid }) => (
              <Input
                id={id}
                value={mapping.field_name}
                placeholder="Fix versions"
                readOnly={readOnly}
                autoComplete="off"
                aria-describedby={describedBy}
                aria-invalid={invalid}
                onChange={(e) => {
                  onPick(mapping.field, e.target.value);
                }}
              />
            )}
          </Field>
        </div>
      ) : (
        <Field label="Jira field" hint={hint} error={pickedError} className={s.picker}>
          {({ id, describedBy, invalid }) => (
            <Select
              id={id}
              value={mapping.field}
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
      )}
      {switcher}
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
