// Settings › Intake: the queries as an ordered list (order = the Intake tab's group order). Each
// row edits its name and JQL, moves, is removed, and tests its draft JQL live ("Test query", one
// preview mutation per row so its result belongs to that row). Controlled: the page owns the draft.
import { useEffect, useId, useMemo, useRef, useState } from "react";

import { usePreviewIntake } from "../../../api/queries";
import { OpenIcon } from "../../../app/icons";
import { Field, fieldStyles as f } from "../../../editor";
import { Button, ChevronIcon, IconButton, ZoneHead } from "../../../ui";
import { typeText } from "../../intake/model";
import s from "./IntakeSettings.module.css";
import {
  addQuery,
  jqlIssue,
  MAX_QUERIES,
  moveQuery,
  patchQuery,
  previewLine,
  removeQuery,
  validate,
  type IntakeForm,
  type PreviewLine,
  type QueryRow,
  type RowIssues,
} from "./model";

const JQL_PLACEHOLDER = 'project = PLAT AND status = "To Do"';

export function PreviewResult({ line }: { line: PreviewLine }) {
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

export interface QueryRowViewProps {
  row: QueryRow;
  index: number;
  count: number;
  issues: RowIssues;
  readOnly: boolean;
  preview: PreviewLine;
  testing: boolean;
  focusName: boolean;
  moveId: (key: string, delta: -1 | 1) => string;
  onPatch: (patch: Partial<Pick<QueryRow, "name" | "jql">>) => void;
  onMove: (delta: -1 | 1) => void;
  onRemove: () => void;
  onTest: () => void;
}

export function QueryRowView(props: QueryRowViewProps) {
  const { row, index, count, issues, readOnly } = props;
  const nameRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (props.focusName) nameRef.current?.focus();
  }, [props.focusName]);

  const label = row.name.trim() || `query ${String(index + 1)}`;
  const cantTest = jqlIssue(row.jql);

  return (
    <li className={s.row}>
      <div className={s.line}>
        <span className={s.lead}>
          {readOnly ? null : (
            <span className={s.move}>
              <IconButton
                id={props.moveId(row.key, -1)}
                bare
                className={s.up}
                label={`Move ${label} up`}
                disabled={index === 0}
                onClick={() => {
                  props.onMove(-1);
                }}
              >
                <ChevronIcon />
              </IconButton>
              <IconButton
                id={props.moveId(row.key, 1)}
                bare
                label={`Move ${label} down`}
                disabled={index === count - 1}
                onClick={() => {
                  props.onMove(1);
                }}
              >
                <ChevronIcon />
              </IconButton>
            </span>
          )}
          <span className={s.num} aria-hidden="true">
            {String(index + 1).padStart(2, "0")}
          </span>
        </span>
        <Field label="Name" error={issues.name} className={s.name}>
          {({ id, describedBy, invalid }) => (
            <input
              ref={nameRef}
              id={id}
              className={f.input}
              value={row.name}
              placeholder="Ready for agents"
              readOnly={readOnly}
              autoComplete="off"
              aria-describedby={describedBy}
              aria-invalid={invalid}
              onChange={(e) => {
                props.onPatch({ name: e.target.value });
              }}
            />
          )}
        </Field>
        <span className={s.remove}>
          {readOnly ? null : (
            <Button
              size="sm"
              variant="quiet"
              aria-label={`Remove ${label}`}
              onClick={props.onRemove}
            >
              Remove
            </Button>
          )}
        </span>
      </div>

      <div className={s.body}>
        <Field
          label="JQL"
          hint={row.id ? `id · ${row.id}` : "New: its id comes from the name when you save"}
          error={issues.jql}
        >
          {({ id, describedBy, invalid }) => (
            <textarea
              id={id}
              className={f.textarea}
              value={row.jql}
              rows={3}
              placeholder={JQL_PLACEHOLDER}
              readOnly={readOnly}
              spellCheck={false}
              autoComplete="off"
              aria-describedby={describedBy}
              aria-invalid={invalid}
              onChange={(e) => {
                props.onPatch({ jql: e.target.value });
              }}
            />
          )}
        </Field>

        <div className={s.test}>
          <div className={s.testBar}>
            <Button
              size="sm"
              disabled={cantTest !== undefined || props.testing}
              title={cantTest}
              aria-label={`Test ${label}`}
              onClick={props.onTest}
            >
              {props.testing ? "Testing…" : "Test query"}
            </Button>
          </div>
          <PreviewResult line={props.preview} />
        </div>
      </div>
    </li>
  );
}

type TestableRowProps = Omit<QueryRowViewProps, "preview" | "testing" | "onTest">;

/** One row with its own preview mutation; editing the JQL drops the result. */
function TestableRow(props: TestableRowProps) {
  const preview = usePreviewIntake();
  const line = previewLine(
    {
      ran: preview.variables?.jql,
      pending: preview.isPending,
      data: preview.data,
      error: preview.error,
    },
    props.row.jql,
  );
  return (
    <QueryRowView
      {...props}
      preview={line}
      testing={line.kind === "pending"}
      onPatch={(patch) => {
        if (patch.jql !== undefined && patch.jql !== props.row.jql) preview.reset();
        props.onPatch(patch);
      }}
      onTest={() => {
        preview.mutate({ jql: props.row.jql.trim() });
      }}
    />
  );
}

export interface QueryEditorProps {
  value: IntakeForm;
  onChange: (value: IntakeForm) => void;
  readOnly: boolean;
}

export function QueryEditor({ value, onChange, readOnly }: QueryEditorProps) {
  const issues = useMemo(() => validate(value), [value]);
  const [focusKey, setFocusKey] = useState<string | null>(null);
  const prefix = useId();
  const moveId = (key: string, delta: -1 | 1) => `${prefix}-${key}-${delta < 0 ? "up" : "down"}`;
  const atLimit = value.length >= MAX_QUERIES;

  const move = (key: string, index: number, delta: -1 | 1) => {
    onChange(moveQuery(value, index, delta));
    // Reordering can drop focus from the moved row's arrow; put it back (or on the other arrow,
    // if this one is now disabled at an end).
    const to = index + delta;
    const atEnd = delta < 0 ? to === 0 : to === value.length - 1;
    const target = moveId(key, atEnd ? (delta < 0 ? 1 : -1) : delta);
    requestAnimationFrame(() => {
      document.getElementById(target)?.focus();
    });
  };

  const remove = (row: QueryRow, index: number) => {
    const blank = row.name.trim() === "" && row.jql.trim() === "";
    const name = row.name.trim() || `query ${String(index + 1)}`;
    if (!blank && !window.confirm(`Remove the query "${name}"? It is gone once you save.`)) return;
    onChange(removeQuery(value, index));
  };

  return (
    <section className={s.queries} aria-labelledby="intake-queries-head">
      <ZoneHead
        id="intake-queries-head"
        title="Queries"
        count={value.length}
        note="Each is a group on the Intake tab, in this order."
        right={
          readOnly ? null : (
            <Button
              size="sm"
              disabled={atLimit}
              title={atLimit ? `At most ${String(MAX_QUERIES)} queries` : undefined}
              onClick={() => {
                const next = addQuery(value);
                setFocusKey(next[next.length - 1]?.key ?? null);
                onChange(next);
              }}
            >
              + Add query
            </Button>
          )
        }
      />

      {issues.form.map((msg) => (
        <p key={msg} className={s.formError} role="alert">
          {msg}
        </p>
      ))}

      {value.length === 0 ? (
        <p className={s.none}>No intake queries: the Intake tab is empty until you add one.</p>
      ) : (
        <ol className={s.list}>
          {value.map((row, index) => (
            <TestableRow
              key={row.key}
              row={row}
              index={index}
              count={value.length}
              issues={issues.rows[index] ?? {}}
              readOnly={readOnly}
              focusName={focusKey === row.key}
              moveId={moveId}
              onPatch={(patch) => {
                onChange(patchQuery(value, index, patch));
              }}
              onMove={(delta) => {
                move(row.key, index, delta);
              }}
              onRemove={() => {
                remove(row, index);
              }}
            />
          ))}
        </ol>
      )}
    </section>
  );
}
