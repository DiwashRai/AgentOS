// The pipeline editor's loops: each loop block's first and last stage and its round cap, with the
// block's roles and its problems inline. The block is also drawn around its stage rows.
import { Button, ZoneHead } from "../../ui";
import { Field, Select } from "./fields";
import {
  blockRoles,
  countOptions,
  LOOP_MAX_ROUNDS,
  loopStageOptions,
  type Loop,
  type LoopIssues,
  type Stage,
} from "./model";
import s from "./PipelineEditor.module.css";

const ROUND_OPTIONS = countOptions(1, LOOP_MAX_ROUNDS);

export interface LoopsSectionProps {
  loops: readonly Loop[];
  stages: readonly Stage[];
  issues: readonly LoopIssues[];
  readOnly: boolean;
  /** False when no two stages outside a loop are left for a new one. */
  canAdd: boolean;
  onAdd: () => void;
  onPatch: (index: number, patch: Partial<Loop>) => void;
  onRemove: (index: number) => void;
}

export function LoopsSection(props: LoopsSectionProps) {
  const { loops, stages, readOnly } = props;
  return (
    <section className={s.loopsZone} aria-label="Loops">
      <ZoneHead
        title="Loops"
        count={loops.length}
        note="Run a range of stages again until every check passes."
        right={
          readOnly ? null : (
            <Button
              size="sm"
              disabled={!props.canAdd}
              title={props.canAdd ? undefined : "No two stages in a row are left outside a loop"}
              onClick={props.onAdd}
            >
              + Loop
            </Button>
          )
        }
      />
      {loops.length === 0 ? (
        <p className={s.loopsEmpty}>
          No loops. A loop runs its makers, then its checks, and goes round again with the checks'
          notes until they all pass.
        </p>
      ) : (
        <ol className={s.loops}>
          {loops.map((loop, i) => (
            <LoopItem
              // Loops have no id; their position is their identity while the list is edited.
              key={i}
              index={i}
              loop={loop}
              stages={stages}
              issues={props.issues[i] ?? {}}
              readOnly={readOnly}
              onPatch={(patch) => {
                props.onPatch(i, patch);
              }}
              onRemove={() => {
                props.onRemove(i);
              }}
            />
          ))}
        </ol>
      )}
    </section>
  );
}

function LoopItem(props: {
  index: number;
  loop: Loop;
  stages: readonly Stage[];
  issues: LoopIssues;
  readOnly: boolean;
  onPatch: (patch: Partial<Loop>) => void;
  onRemove: () => void;
}) {
  const { loop, issues, readOnly } = props;
  const n = String(props.index + 1);
  const roles = blockRoles(props.stages, loop);
  return (
    <li className={s.loop}>
      <div className={s.loopLine}>
        <span className={s.loopName}>{`Loop ${n}`}</span>
        <Field label="From" error={issues.start}>
          {({ id, describedBy, invalid }) => (
            <Select
              id={id}
              aria-describedby={describedBy}
              aria-invalid={invalid}
              value={loop.start}
              disabled={readOnly}
              options={loopStageOptions(props.stages, loop.start)}
              onChange={(start) => {
                props.onPatch({ start });
              }}
            />
          )}
        </Field>
        <Field label="To" error={issues.end}>
          {({ id, describedBy, invalid }) => (
            <Select
              id={id}
              aria-describedby={describedBy}
              aria-invalid={invalid}
              value={loop.end}
              disabled={readOnly}
              options={loopStageOptions(props.stages, loop.end)}
              onChange={(end) => {
                props.onPatch({ end });
              }}
            />
          )}
        </Field>
        <Field label="Rounds, at most" error={issues.max_rounds}>
          {({ id, describedBy, invalid }) => (
            <Select
              id={id}
              aria-describedby={describedBy}
              aria-invalid={invalid}
              value={String(loop.max_rounds)}
              disabled={readOnly}
              options={ROUND_OPTIONS}
              onChange={(v) => {
                props.onPatch({ max_rounds: Number(v) });
              }}
            />
          )}
        </Field>
        {readOnly ? null : (
          <Button size="sm" variant="quiet" className={s.loopRemove} onClick={props.onRemove}>
            Remove loop
          </Button>
        )}
      </div>
      {roles ? (
        <p className={s.loopRoles}>
          <span>
            <span className={s.k}>Makers</span> {roles.makers.join(", ") || "none"}
          </span>
          <span>
            <span className={s.k}>Checks</span> {roles.checks.join(", ") || "none"}
          </span>
        </p>
      ) : null}
      {issues.block ? (
        <p className={s.stageError} role="alert">
          {issues.block}
        </p>
      ) : null}
    </li>
  );
}
