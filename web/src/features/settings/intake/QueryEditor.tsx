// Settings › Intake: the queries as an ordered list (order = the Intake tab's group order) on the
// kit's ListEditor. Each row edits its name and JQL and tests its draft JQL live ("Test query");
// the result shows only while the row's JQL still equals what ran. Controlled: the page owns the
// draft and the issues.
import { useId } from "react";

import { OpenIcon } from "../../../app/icons";
import {
  errorAt,
  Field,
  Input,
  ListEditor,
  messagesAt,
  pathOf,
  Textarea,
  usePreview,
  type Issues,
} from "../../../editor";
import { Button, CountPill, text } from "../../../ui";
import { typeText } from "../../intake/model";
import s from "./IntakeSettings.module.css";
import {
  isBlank,
  newQuery,
  patchQuery,
  previewLine,
  queryLabels,
  type IntakeForm,
  type IntakePreview,
  type PreviewLine,
  type QueryRow,
} from "./model";

const JQL_PLACEHOLDER = 'project = PLAT AND status = "To Do"';

/** Runs a JQL live against Jira (nothing saved). */
export type RunPreview = (jql: string) => Promise<IntakePreview>;

export interface QueryEditorProps {
  form: IntakeForm;
  issues: Issues;
  readOnly: boolean;
  onChange: (form: IntakeForm) => void;
  preview: RunPreview;
}

export function QueryEditor({ form, issues, readOnly, onChange, preview }: QueryEditorProps) {
  const headId = useId();
  const labels = queryLabels(form.queries);
  const labelAt = (index: number) => labels[index] ?? `query ${String(index + 1)}`;

  // A region inside the page's "Intake queries" section, so its heading is one level below.
  return (
    <section className={s.zone} aria-labelledby={headId}>
      <div className={s.head}>
        <h3 id={headId}>Queries</h3>
        <CountPill count={form.queries.length} />
        <span className={s.note}>Each is a group on the Intake tab, in this order.</span>
        <span className={s.rule} aria-hidden="true" />
      </div>

      <ListEditor
        name="Queries"
        rows={form.queries}
        ordered
        readOnly={readOnly}
        label={(_, index) => labelAt(index)}
        create={newQuery}
        addLabel="Add query"
        isBlank={isBlank}
        empty="No intake queries: the Intake tab is empty until you add one."
        issue={messagesAt(issues, "queries")}
        className={s.queries}
        onChange={(queries) => {
          onChange({ ...form, queries });
        }}
      >
        {(row, ctx) => (
          <QueryFields
            row={row}
            label={labelAt(ctx.index)}
            issues={issues}
            readOnly={readOnly}
            preview={preview}
            onPatch={(patch) => {
              onChange(patchQuery(form, row.key, patch));
            }}
          />
        )}
      </ListEditor>
    </section>
  );
}

interface QueryFieldsProps {
  row: QueryRow;
  label: string;
  issues: Issues;
  readOnly: boolean;
  preview: RunPreview;
  onPatch: (patch: { name?: string; jql?: string }) => void;
}

/** One row's fields and its Test query line (a component of its own: the preview is a hook). */
function QueryFields({ row, label, issues, readOnly, preview, onPatch }: QueryFieldsProps) {
  const jql = row.item.jql.trim();
  const test = usePreview(preview, jql === "" ? undefined : jql);
  const line = previewLine(test);

  // Issues about the row as a whole and about its id (which has no field) show under the JQL,
  // beside the id hint.
  const rowIssues = [
    ...messagesAt(issues, pathOf("queries", row.key)),
    ...messagesAt(issues, pathOf("queries", row.key, "id")),
  ];

  return (
    <div className={s.fields}>
      <Field label="Name" error={errorAt(issues, pathOf("queries", row.key, "name"))}>
        {({ id, describedBy, invalid }) => (
          <Input
            id={id}
            value={row.item.name}
            placeholder="Ready for agents"
            readOnly={readOnly}
            autoComplete="off"
            aria-describedby={describedBy}
            aria-invalid={invalid}
            onChange={(e) => {
              onPatch({ name: e.target.value });
            }}
          />
        )}
      </Field>

      <Field
        label="JQL"
        hint={row.item.id ? `id · ${row.item.id}` : "New: its id comes from the name when you save"}
        error={errorAt(issues, pathOf("queries", row.key, "jql"))}
      >
        {({ id, describedBy, invalid }) => (
          <Textarea
            id={id}
            value={row.item.jql}
            rows={3}
            placeholder={JQL_PLACEHOLDER}
            readOnly={readOnly}
            spellCheck={false}
            autoComplete="off"
            aria-describedby={describedBy}
            aria-invalid={invalid}
            onChange={(e) => {
              onPatch({ jql: e.target.value });
            }}
          />
        )}
      </Field>

      {rowIssues.map((msg) => (
        <p key={msg} className={text.error} role="alert">
          {msg}
        </p>
      ))}

      <div className={s.test}>
        <div className={s.testBar}>
          <Button
            size="sm"
            disabled={jql === "" || test.pending}
            aria-label={`${test.pending ? "Testing" : "Test query"} ${label}`}
            onClick={test.run}
          >
            {test.pending ? "Testing…" : "Test query"}
          </Button>
        </div>
        <PreviewResult line={line} />
      </div>
    </div>
  );
}

function PreviewResult({ line }: { line: PreviewLine }) {
  switch (line.kind) {
    case "none":
      return null;
    case "pending":
      return (
        <p className={s.k} role="status">
          Asking Jira…
        </p>
      );
    case "error":
      return (
        <div className={s.result} role="status">
          <span className={s.k} data-error="">
            Jira says
          </span>
          <p className={s.msg}>{line.text}</p>
        </div>
      );
    case "empty":
      return (
        <p className={s.k} role="status">
          Nothing to take in
        </p>
      );
    case "items":
      return (
        <div className={s.result} role="status">
          <span className={s.k}>
            <b>{line.count}</b>
            {line.more ? ` · ${line.more}` : null}
          </span>
          <ul className={s.items}>
            {line.items.map((item) => {
              const { type, priority } = typeText(item);
              return (
                <li key={item.key} className={s.item}>
                  <a
                    className={s.key}
                    href={item.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    title={`Open ${item.key} in Jira`}
                  >
                    {item.key}
                    <OpenIcon />
                  </a>
                  <span className={s.type}>
                    {type}
                    {priority && <em> · {priority}</em>}
                  </span>
                  <span className={s.ti} title={item.title}>
                    {item.title}
                  </span>
                </li>
              );
            })}
          </ul>
        </div>
      );
  }
}
