// One stage in the PipelineEditor: a line (move, name, kind, phase, Enabled, Sign-off) that
// expands to the stage's settings. A disabled stage reads as "skipped", as everywhere else.
import { useEffect, useId, useRef } from "react";

import { Button, ChevronIcon, cx, IconButton, Tag } from "../../ui";
import { Field, fieldStyles as f, Select, Toggle } from "./fields";
import {
  agentHint,
  formatTimeout,
  idEditable,
  optional,
  PHASES,
  stageModelPlaceholder,
  stageRuntimeOptions,
  TIMEOUT_MAX,
  TIMEOUT_MIN,
  unsetIfEmpty,
  type Row,
  type SpaceDefaults,
  type StageIssues,
  type StagePatch,
} from "./model";
import s from "./PipelineEditor.module.css";

const PHASE_OPTIONS = PHASES.map((p) => ({
  value: p,
  label: p.charAt(0).toUpperCase() + p.slice(1),
}));

export interface StageRowProps {
  row: Row;
  index: number;
  count: number;
  issues: StageIssues;
  open: boolean;
  readOnly: boolean;
  runtimes: readonly string[];
  space: SpaceDefaults;
  /** Focus the name field once mounted (a stage just added). */
  focusName: boolean;
  /** Prefix for the move buttons' ids, so the editor can return focus after a move. */
  moveId: (uid: number, delta: -1 | 1) => string;
  onToggleOpen: () => void;
  onMove: (delta: -1 | 1) => void;
  onPatch: (patch: StagePatch) => void;
  onSetId: (id: string) => void;
  onRemove: () => void;
}

export function StageRow(props: StageRowProps) {
  const { row, index, count, issues, open, readOnly, onPatch } = props;
  const { stage } = row;
  const bodyId = useId();
  const nameRef = useRef<HTMLInputElement>(null);
  const focusName = props.focusName;
  useEffect(() => {
    if (focusName) nameRef.current?.select();
  }, [focusName]);

  const label = stage.name.trim() || stage.id;
  const nIssues = Object.keys(issues).length;
  const skipped = !stage.enabled;

  return (
    <li className={cx(s.row, open && s.isOpen, skipped && s.isSkipped)}>
      <div className={s.line}>
        {readOnly ? (
          <span className={s.num} aria-hidden="true">
            {String(index + 1).padStart(2, "0")}
          </span>
        ) : (
          <span className={s.move}>
            <IconButton
              id={props.moveId(row.uid, -1)}
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
              id={props.moveId(row.uid, 1)}
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
          <span className={s.id}>{stage.id}</span>
          {skipped ? <span className={s.cap}>Skipped</span> : null}
          {row.fresh ? <span className={s.cap}>New</span> : null}
          {nIssues > 0 ? (
            <span className={cx(s.cap, s.bad)}>
              {nIssues === 1 ? "1 issue" : `${String(nIssues)} issues`}
            </span>
          ) : null}
        </button>

        <span className={s.tags}>
          <Tag>{stage.kind}</Tag>
          {stage.requires_repo ? (
            <Tag title="Needs a repo: off on tickets without one">Repo</Tag>
          ) : null}
        </span>

        <Select
          size="sm"
          aria-label={`Phase of ${label}`}
          value={stage.phase}
          options={PHASE_OPTIONS}
          disabled={readOnly}
          onChange={(v) => {
            const phase = PHASES.find((p) => p === v);
            if (phase) onPatch({ phase });
          }}
        />

        <Toggle
          checked={stage.enabled}
          disabled={readOnly}
          aria-label={`Run ${label}`}
          title="Off = the stage is skipped"
          onChange={(enabled) => {
            onPatch({ enabled });
          }}
        >
          Enabled
        </Toggle>

        <Toggle
          checked={stage.gate_after}
          disabled={readOnly}
          aria-label={`Pause for approval after ${label}`}
          title="Pause for approval after this stage"
          className={s.gate}
          onChange={(gate_after) => {
            onPatch({ gate_after });
          }}
        >
          <u className={cx(s.diamond, stage.gate_after && s.on)} aria-hidden="true" />
          Sign-off
        </Toggle>
      </div>

      {open ? (
        <div id={bodyId} className={s.body}>
          <div className={s.grid2}>
            <Field label="Name" error={issues.name}>
              {({ id, describedBy, invalid }) => (
                <input
                  ref={nameRef}
                  id={id}
                  className={f.input}
                  value={stage.name}
                  readOnly={readOnly}
                  aria-describedby={describedBy}
                  aria-invalid={invalid}
                  onChange={(e) => {
                    onPatch({ name: e.target.value });
                  }}
                />
              )}
            </Field>
            <Field
              label="Id"
              error={issues.id}
              hint={
                idEditable(row)
                  ? "Lowercase letters, digits, dashes. Fixed once saved."
                  : "Fixed: runs refer to the stage by its id."
              }
            >
              {({ id, describedBy, invalid }) => (
                <input
                  id={id}
                  className={cx(f.input, f.mono)}
                  value={stage.id}
                  readOnly={readOnly || !idEditable(row)}
                  spellCheck={false}
                  autoComplete="off"
                  aria-describedby={describedBy}
                  aria-invalid={invalid}
                  onChange={(e) => {
                    props.onSetId(e.target.value);
                  }}
                />
              )}
            </Field>
          </div>

          {stage.kind === "agent" ? (
            <>
              <Field
                label="Prompt"
                error={issues.prompt}
                hint="A Jinja template: {{ticket.key}}, {{ticket.title}}, {{paths.skill_md}}, {{paths.tasks_md}}…"
              >
                {({ id, describedBy, invalid }) => (
                  <textarea
                    id={id}
                    className={f.textarea}
                    rows={9}
                    value={stage.prompt}
                    readOnly={readOnly}
                    spellCheck={false}
                    aria-describedby={describedBy}
                    aria-invalid={invalid}
                    onChange={(e) => {
                      onPatch({ prompt: e.target.value });
                    }}
                  />
                )}
              </Field>
              <div className={s.grid4}>
                <Field label="Runtime" error={issues.runtime}>
                  {({ id, describedBy, invalid }) => (
                    <Select
                      id={id}
                      aria-describedby={describedBy}
                      aria-invalid={invalid}
                      value={stage.runtime ?? ""}
                      disabled={readOnly}
                      options={stageRuntimeOptions(props.runtimes, props.space, stage.runtime)}
                      onChange={(v) => {
                        onPatch({ runtime: unsetIfEmpty(v) });
                      }}
                    />
                  )}
                </Field>
                <Field label="Agent" hint={agentHint(stage.agent)} error={issues.agent}>
                  {({ id, describedBy, invalid }) => (
                    <input
                      id={id}
                      className={cx(f.input, f.mono)}
                      value={stage.agent ?? ""}
                      placeholder="none"
                      readOnly={readOnly}
                      spellCheck={false}
                      autoComplete="off"
                      aria-describedby={describedBy}
                      aria-invalid={invalid}
                      onChange={(e) => {
                        onPatch({ agent: optional(e.target.value) });
                      }}
                    />
                  )}
                </Field>
                <Field label="Model" error={issues.model}>
                  {({ id, describedBy, invalid }) => (
                    <input
                      id={id}
                      className={cx(f.input, f.mono)}
                      value={stage.model ?? ""}
                      placeholder={stageModelPlaceholder(stage.runtime, props.space)}
                      readOnly={readOnly}
                      spellCheck={false}
                      autoComplete="off"
                      aria-describedby={describedBy}
                      aria-invalid={invalid}
                      onChange={(e) => {
                        onPatch({ model: optional(e.target.value) });
                      }}
                    />
                  )}
                </Field>
                <TimeoutField
                  value={stage.timeout_s}
                  error={issues.timeout_s}
                  readOnly={readOnly}
                  onChange={(timeout_s) => {
                    onPatch({ timeout_s });
                  }}
                />
              </div>
            </>
          ) : (
            <>
              <Field
                label="Command"
                error={issues.command}
                hint="Run by the worker's shell in the workspace."
              >
                {({ id, describedBy, invalid }) => (
                  <textarea
                    id={id}
                    className={f.textarea}
                    rows={3}
                    value={stage.command}
                    placeholder="npm test"
                    readOnly={readOnly}
                    spellCheck={false}
                    aria-describedby={describedBy}
                    aria-invalid={invalid}
                    onChange={(e) => {
                      onPatch({ command: e.target.value });
                    }}
                  />
                )}
              </Field>
              <div className={s.grid4}>
                <TimeoutField
                  value={stage.timeout_s}
                  error={issues.timeout_s}
                  readOnly={readOnly}
                  onChange={(timeout_s) => {
                    onPatch({ timeout_s });
                  }}
                />
              </div>
            </>
          )}

          {readOnly ? null : (
            <div className={s.foot}>
              <Button
                size="sm"
                variant="quiet"
                disabled={count <= 1}
                title={count <= 1 ? "A pipeline needs at least one stage" : undefined}
                onClick={props.onRemove}
              >
                Remove stage
              </Button>
            </div>
          )}
        </div>
      ) : null}
    </li>
  );
}

function TimeoutField(props: {
  value: number;
  error: string | undefined;
  readOnly: boolean;
  onChange: (v: number) => void;
}) {
  return (
    <Field
      label="Timeout (s)"
      error={props.error}
      hint={formatTimeout(props.value) || `${String(TIMEOUT_MIN)}–${String(TIMEOUT_MAX)}`}
    >
      {({ id, describedBy, invalid }) => (
        <input
          id={id}
          className={cx(f.input, f.mono)}
          type="number"
          inputMode="numeric"
          min={TIMEOUT_MIN}
          max={TIMEOUT_MAX}
          step={60}
          value={Number.isNaN(props.value) ? "" : props.value}
          readOnly={props.readOnly}
          aria-describedby={describedBy}
          aria-invalid={invalid}
          onChange={(e) => {
            props.onChange(e.target.valueAsNumber);
          }}
        />
      )}
    </Field>
  );
}
