// Settings › Runners › Runtimes: the ordered list of agent runtimes (the first is the server
// default). Each runtime is a summary line (name, the command it builds) that expands to edit its
// argument lists, one argument per line, with a preview of the assembled command. Controlled; the
// page owns the draft and the save.
import { Fragment, useEffect, useId, useMemo, useRef, useState } from "react";

import { Button, ChevronIcon, cx, IconButton, Tag } from "../../../ui";
import { Field, fieldStyles as f, Select } from "../../pipeline/fields";
import {
  addRuntime,
  argsToLines,
  commandPreview,
  effortArgs,
  freshKey,
  linesToArgs,
  MAX_RUNTIMES,
  moveRuntime,
  patchRuntime,
  placeholderNotes,
  previewCalls,
  previewLine,
  removeRuntime,
  resumeArgs,
  sameArgs,
  sessionArgs,
  setPromptVia,
  shownArg,
  verdictArgs,
  type PreviewCall,
  type PromptVia,
  type RuntimeEntry,
  type RuntimeIssues,
  type RuntimePatch,
  type RuntimeRow,
  type RuntimesIssues,
  type VerdictFrom,
} from "./model";
import s from "./Runners.module.css";

const PROMPT_VIA_OPTIONS: readonly { value: PromptVia; label: string }[] = [
  { value: "stdin", label: "On stdin" },
  { value: "arg", label: "As an argument" },
];

const VERDICT_FROM_OPTIONS: readonly { value: VerdictFrom; label: string }[] = [
  { value: "file", label: "A file the check writes" },
  { value: "structured_output", label: "The CLI's structured output" },
];

const CALL_LABEL: Record<PreviewCall, string> = {
  stage: "A stage",
  check: "A check in a loop, round 1",
  repair: "A verdict repair",
};

export interface RuntimesEditorProps {
  rows: readonly RuntimeRow[];
  issues: RuntimesIssues;
  readOnly: boolean;
  onChange: (rows: RuntimeRow[]) => void;
}

export function RuntimesEditor({ rows, issues, readOnly, onChange }: RuntimesEditorProps) {
  const [open, setOpen] = useState<ReadonlySet<string>>(() => new Set());
  const [focusKey, setFocusKey] = useState<string | null>(null);
  const prefix = useId();
  const moveId = (key: string, delta: -1 | 1) => `${prefix}-${key}-${delta < 0 ? "up" : "down"}`;
  const atLimit = rows.length >= MAX_RUNTIMES;

  const add = () => {
    const key = freshKey(rows);
    onChange(addRuntime(rows));
    setOpen((o) => new Set(o).add(key));
    setFocusKey(key);
  };

  const move = (key: string, delta: -1 | 1, index: number) => {
    onChange(moveRuntime(rows, key, delta));
    // Reordering can drop focus from the moved row's button; put it back (or on the other arrow,
    // if this one is now disabled at an end).
    const to = index + delta;
    const atEnd = delta < 0 ? to === 0 : to === rows.length - 1;
    const target = moveId(key, atEnd ? (delta < 0 ? 1 : -1) : delta);
    requestAnimationFrame(() => {
      document.getElementById(target)?.focus();
    });
  };

  const remove = (key: string, name: string) => {
    if (!window.confirm(`Remove the runtime "${name}"? It is gone once you save.`)) return;
    onChange(removeRuntime(rows, key));
  };

  return (
    <>
      {issues.form.map((msg) => (
        <p key={msg} className={s.formError} role="alert">
          {msg}
        </p>
      ))}

      <ol className={s.list} aria-label="Runtimes">
        {rows.map((row, index) => (
          <RuntimeItem
            key={row.key}
            row={row}
            index={index}
            count={rows.length}
            issues={issues.rows[index] ?? {}}
            open={open.has(row.key)}
            readOnly={readOnly}
            focusName={focusKey === row.key}
            moveId={moveId}
            onToggleOpen={() => {
              setOpen((o) => {
                const next = new Set(o);
                if (!next.delete(row.key)) next.add(row.key);
                return next;
              });
            }}
            onMove={(delta) => {
              move(row.key, delta, index);
            }}
            onPatch={(patch) => {
              onChange(patchRuntime(rows, row.key, patch));
            }}
            onPromptVia={(via) => {
              onChange(setPromptVia(rows, row.key, via));
            }}
            onRemove={() => {
              remove(row.key, row.entry.name || "unnamed");
            }}
          />
        ))}
      </ol>

      {readOnly ? null : (
        <div className={s.adds}>
          <Button
            size="sm"
            disabled={atLimit}
            title={atLimit ? `At most ${String(MAX_RUNTIMES)} runtimes` : undefined}
            onClick={add}
          >
            + Add runtime
          </Button>
        </div>
      )}
    </>
  );
}

interface RuntimeItemProps {
  row: RuntimeRow;
  index: number;
  count: number;
  issues: RuntimeIssues;
  open: boolean;
  readOnly: boolean;
  focusName: boolean;
  moveId: (key: string, delta: -1 | 1) => string;
  onToggleOpen: () => void;
  onMove: (delta: -1 | 1) => void;
  onPatch: (patch: RuntimePatch) => void;
  onPromptVia: (via: PromptVia) => void;
  onRemove: () => void;
}

function RuntimeItem(props: RuntimeItemProps) {
  const { row, index, count, issues, open, readOnly, onPatch } = props;
  const e = row.entry;
  const bodyId = useId();
  const nameRef = useRef<HTMLInputElement>(null);
  const focusName = props.focusName;
  useEffect(() => {
    if (focusName) nameRef.current?.select();
  }, [focusName]);

  const preview = useMemo(() => commandPreview(e), [e]);
  const label = e.name || "unnamed";
  const nIssues = Object.keys(issues).length;
  const isDefault = index === 0;

  return (
    <li className={cx(s.row, open && s.isOpen)}>
      <div className={s.line}>
        <span className={s.move}>
          <IconButton
            id={props.moveId(row.key, -1)}
            bare
            className={s.up}
            label={`Move ${label} up`}
            title={index === 1 ? `Move ${label} up (makes it the server default)` : undefined}
            disabled={readOnly || index === 0}
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
            disabled={readOnly || index === count - 1}
            onClick={() => {
              props.onMove(1);
            }}
          >
            <ChevronIcon />
          </IconButton>
        </span>

        <button
          type="button"
          className={s.head}
          aria-expanded={open}
          aria-controls={bodyId}
          onClick={props.onToggleOpen}
        >
          <span className={s.chev}>
            <ChevronIcon />
          </span>
          <span className={s.name}>{label}</span>
          {isDefault ? (
            <Tag title="Stages use it when neither they nor the Space defaults name a runtime">
              Server default
            </Tag>
          ) : null}
          <span className={s.summary}>{previewLine(preview)}</span>
          {nIssues > 0 ? (
            <span className={cx(s.cap, s.bad)}>
              {nIssues === 1 ? "1 issue" : `${String(nIssues)} issues`}
            </span>
          ) : (
            <span className={cx(s.cap, s.mono)}>{e.prompt_via === "stdin" ? "stdin" : "arg"}</span>
          )}
        </button>
      </div>

      {open ? (
        <div id={bodyId} className={s.body}>
          <div className={s.grid2}>
            <Field
              label="Name"
              error={issues.name}
              hint="Stages and the Space defaults refer to the runtime by this name."
            >
              {({ id, describedBy, invalid }) => (
                <input
                  ref={nameRef}
                  id={id}
                  className={cx(f.input, f.mono)}
                  value={e.name}
                  readOnly={readOnly}
                  spellCheck={false}
                  autoComplete="off"
                  aria-describedby={describedBy}
                  aria-invalid={invalid}
                  onChange={(ev) => {
                    onPatch({ name: ev.target.value });
                  }}
                />
              )}
            </Field>
            <Field
              label="Prompt"
              hint={
                e.prompt_via === "stdin"
                  ? "The rendered prompt is the program's stdin."
                  : "The prompt arguments carry the rendered prompt."
              }
            >
              {({ id, describedBy }) => (
                <Select
                  id={id}
                  aria-describedby={describedBy}
                  value={e.prompt_via}
                  disabled={readOnly}
                  options={PROMPT_VIA_OPTIONS}
                  onChange={(v) => {
                    const via = PROMPT_VIA_OPTIONS.find((o) => o.value === v)?.value;
                    if (via) props.onPromptVia(via);
                  }}
                />
              )}
            </Field>
          </div>

          <LinesField
            label="Command"
            value={e.argv}
            rows={7}
            error={issues.argv}
            hint="The program, then one argument per line. No shell: each line is passed as typed."
            placeholder={"claude\n-p"}
            readOnly={readOnly}
            onChange={(argv) => {
              onPatch({ argv });
            }}
          />

          <div className={s.args}>
            <LinesField
              label="Model arguments"
              value={e.model_args}
              rows={3}
              error={issues.model_args}
              hint="Added when a model resolves; a {model} line becomes the model."
              placeholder={"--model\n{model}"}
              readOnly={readOnly}
              onChange={(model_args) => {
                onPatch({ model_args });
              }}
            />
            <LinesField
              label="Effort arguments"
              value={effortArgs(e)}
              rows={3}
              error={issues.effort_args}
              hint="Added when an effort resolves; an {effort} line becomes the effort. Empty: efforts are ignored."
              placeholder={"--effort\n{effort}"}
              readOnly={readOnly}
              onChange={(effort_args) => {
                onPatch({ effort_args });
              }}
            />
            <LinesField
              label="Agent arguments"
              value={e.agent_args}
              rows={3}
              error={issues.agent_args}
              hint="Added when the stage names an agent; an {agent} line becomes the agent."
              placeholder={"--agent\n{agent}"}
              readOnly={readOnly}
              onChange={(agent_args) => {
                onPatch({ agent_args });
              }}
            />
            {e.prompt_via === "arg" ? (
              <LinesField
                label="Prompt arguments"
                value={e.prompt_args}
                rows={3}
                error={issues.prompt_args}
                hint="Added last; the {prompt} line becomes the rendered prompt."
                placeholder={"-p\n{prompt}"}
                readOnly={readOnly}
                onChange={(prompt_args) => {
                  onPatch({ prompt_args });
                }}
              />
            ) : null}
          </div>

          <section className={s.loopArgs} aria-label="Loops">
            <span className={s.k}>Loops</span>
            <p className={s.lead}>
              Loop stages run again each round. With sessions, a maker resumes its own session and
              an invalid verdict is repaired by resuming the check; without them, every round starts
              fresh and there is no repair.
            </p>
            <div className={s.args}>
              <LinesField
                label="Session arguments"
                value={sessionArgs(e)}
                rows={3}
                error={issues.session_args}
                hint="Start a session with a known id. {session} becomes that id, alone or inside an argument."
                placeholder={"--session-id\n{session}"}
                readOnly={readOnly}
                onChange={(session_args) => {
                  onPatch({ session_args });
                }}
              />
              <LinesField
                label="Resume arguments"
                value={resumeArgs(e)}
                rows={3}
                error={issues.resume_args}
                hint="Resume that session in a later round or a repair. {session} as above."
                placeholder={"--resume\n{session}"}
                readOnly={readOnly}
                onChange={(resume_args) => {
                  onPatch({ resume_args });
                }}
              />
              <LinesField
                label="Verdict arguments"
                value={verdictArgs(e)}
                rows={3}
                error={issues.verdict_args}
                hint="Added to a check's calls to ask for schema-checked output; a {verdict_schema} line becomes the verdict's JSON Schema. Empty: none."
                placeholder={"--json-schema\n{verdict_schema}"}
                readOnly={readOnly}
                onChange={(verdict_args) => {
                  onPatch({ verdict_args });
                }}
              />
              <Field
                label="Verdict from"
                error={issues.verdict_from}
                hint={
                  e.verdict_from === "file"
                    ? "The check writes its verdict to .agentos/verdict.json; works with any CLI."
                    : "Read from the CLI's final result line (needs verdict arguments)."
                }
              >
                {({ id, describedBy, invalid }) => (
                  <Select
                    id={id}
                    aria-describedby={describedBy}
                    aria-invalid={invalid}
                    value={e.verdict_from}
                    disabled={readOnly}
                    options={VERDICT_FROM_OPTIONS}
                    onChange={(v) => {
                      const from = VERDICT_FROM_OPTIONS.find((o) => o.value === v)?.value;
                      if (from) onPatch({ verdict_from: from });
                    }}
                  />
                )}
              </Field>
            </div>
          </section>

          <Preview entry={e} />

          {readOnly ? null : (
            <div className={s.foot}>
              <Button
                size="sm"
                variant="quiet"
                disabled={count <= 1}
                title={count <= 1 ? "At least one runtime is needed" : undefined}
                onClick={props.onRemove}
              >
                Remove runtime
              </Button>
            </div>
          )}
        </div>
      ) : null}
    </li>
  );
}

/** What a stage with a model, an effort and an agent would run, and placeholders that won't be
 *  replaced. */
function Preview({ entry }: { entry: RuntimeEntry }) {
  const calls = previewCalls(entry);
  const notes = placeholderNotes(entry);
  return (
    <section className={s.preview} aria-label="Command preview">
      <span className={s.k}>Command preview</span>
      {entry.argv.length === 0 ? (
        <span className={s.stdin}>No command yet.</span>
      ) : (
        calls.map((call) => (
          <div key={call} className={s.call}>
            {calls.length > 1 ? <span className={s.callLabel}>{CALL_LABEL[call]}</span> : null}
            <p className={s.cmd}>
              {commandPreview(entry, call).tokens.map((t, i) => (
                // Tokens are positional and may repeat, so the index is their identity.
                <Fragment key={i}>
                  {i > 0 ? " " : null}
                  <span className={cx(s.tok, t.slot && s.slot)}>
                    {t.slot ? t.text : shownArg(t.text)}
                  </span>
                </Fragment>
              ))}
            </p>
          </div>
        ))
      )}
      <span className={s.stdin}>
        {entry.prompt_via === "stdin"
          ? "The prompt goes to stdin."
          : "The prompt is passed as an argument (no stdin)."}{" "}
        For a stage with a model, an effort and an agent; without them, their arguments are left
        out.
        {calls.includes("repair")
          ? " A maker's later rounds resume like a repair, without the verdict arguments."
          : null}
      </span>
      {notes.length > 0 ? (
        <ul className={s.notes}>
          {notes.map((n) => (
            <li key={n}>{n}</li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}

interface LinesFieldProps {
  label: string;
  value: readonly string[];
  rows: number;
  error: string | undefined;
  hint: string;
  placeholder: string;
  readOnly: boolean;
  onChange: (args: string[]) => void;
}

/**
 * A list of arguments as a textarea, one per line. The text is kept as typed (a trailing newline,
 * a blank line being filled in) and re-synced only when `value` changes to something the text
 * doesn't already say (a discard, a reload, a save).
 */
function LinesField({ value, onChange, ...props }: LinesFieldProps) {
  const [text, setText] = useState(() => argsToLines(value));
  const [seen, setSeen] = useState(value);
  if (seen !== value) {
    setSeen(value);
    if (!sameArgs(linesToArgs(text), value)) setText(argsToLines(value));
  }
  return (
    <Field label={props.label} error={props.error} hint={props.hint}>
      {({ id, describedBy, invalid }) => (
        <textarea
          id={id}
          className={f.textarea}
          rows={props.rows}
          value={text}
          placeholder={props.placeholder}
          readOnly={props.readOnly}
          spellCheck={false}
          autoComplete="off"
          wrap="off"
          aria-describedby={describedBy}
          aria-invalid={invalid}
          onChange={(ev) => {
            setText(ev.target.value);
            const args = linesToArgs(ev.target.value);
            if (!sameArgs(args, value)) onChange(args);
          }}
        />
      )}
    </Field>
  );
}
