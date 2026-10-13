// The shared pipeline editor (SPEC §13 "Pipeline"), used by the per-ticket Pipeline tab and
// Settings › Workflows. Controlled and pure UI: no fetching. The parent owns the draft and saving.
import { useId, useMemo, useState } from "react";

import { Button, Stepper, ZoneHead } from "../../ui";
import {
  draftFrom,
  MAX_STAGES,
  previewModel,
  reduce,
  toPipeline,
  validatePipeline,
  type Action,
  type Draft,
  type Pipeline,
  type SpaceDefaults,
} from "./model";
import s from "./PipelineEditor.module.css";
import { StageRow } from "./StageRow";

export interface PipelineEditorProps {
  value: Pipeline;
  onChange: (p: Pipeline) => void;
  readOnly?: boolean | undefined;
  /** Runtime names from /api/config. */
  runtimes: string[];
  /** What a stage's unset runtime / model / effort falls back to (shown as placeholders);
   *  build it with `configDefaults(config)`. */
  space: SpaceDefaults;
}

/**
 * Stages the editor added and that haven't come back in a new `value` yet are "new": only their
 * id is editable. Any `value` the editor didn't emit itself (the server's copy after a save, a
 * discard, a reload) counts as saved, so pass the saved pipeline back once a save succeeds.
 */
export function PipelineEditor({
  value,
  onChange,
  readOnly = false,
  runtimes,
  space,
}: PipelineEditorProps) {
  // `seen` is the last value this draft matches; a different `value` came from outside.
  const [state, setState] = useState<{ draft: Draft; seen: Pipeline }>(() => ({
    draft: draftFrom(value),
    seen: value,
  }));
  let draft = state.draft;
  if (state.seen !== value) {
    draft = draftFrom(value, state.draft);
    setState({ draft, seen: value });
  }

  const [open, setOpen] = useState<ReadonlySet<number>>(() => new Set());
  const [focusUid, setFocusUid] = useState<number | null>(null);
  const prefix = useId();
  const moveId = (uid: number, delta: -1 | 1) =>
    `${prefix}-${String(uid)}-${delta < 0 ? "up" : "down"}`;

  const stages = useMemo(() => draft.rows.map((r) => r.stage), [draft]);
  const issues = useMemo(() => validatePipeline(stages, runtimes), [stages, runtimes]);
  const preview = useMemo(() => previewModel({ stages }), [stages]);

  const apply = (a: Action) => {
    const next = reduce(draft, a);
    if (next === draft) return;
    const p = toPipeline(next);
    setState({ draft: next, seen: p });
    onChange(p);
  };

  const add = (kind: "agent" | "shell") => {
    const uid = draft.nextUid;
    apply({ type: "add", kind });
    setOpen((o) => new Set(o).add(uid));
    setFocusUid(uid);
  };

  const move = (uid: number, delta: -1 | 1, index: number) => {
    apply({ type: "move", uid, delta });
    // Reordering can drop focus from the moved row's button; put it back (or on the other
    // arrow, if this one is now disabled at an end).
    const to = index + delta;
    const atEnd = delta < 0 ? to === 0 : to === draft.rows.length - 1;
    const other: -1 | 1 = delta < 0 ? 1 : -1;
    const target = moveId(uid, atEnd ? other : delta);
    requestAnimationFrame(() => {
      document.getElementById(target)?.focus();
    });
  };

  const remove = (uid: number, name: string) => {
    if (!window.confirm(`Remove the stage "${name}"? It is gone once you save.`)) return;
    apply({ type: "remove", uid });
  };

  const atLimit = draft.rows.length >= MAX_STAGES;

  return (
    <div className={s.editor}>
      <section className={s.preview} aria-label="Preview">
        <span className={s.k}>Preview</span>
        <Stepper model={preview} />
      </section>

      <ZoneHead
        title="Stages"
        count={draft.rows.length}
        note="Run top to bottom. A sign-off pauses the run for approval."
        right={
          readOnly ? null : (
            <span className={s.adds}>
              <Button
                size="sm"
                disabled={atLimit}
                title={atLimit ? `At most ${String(MAX_STAGES)} stages` : undefined}
                onClick={() => {
                  add("agent");
                }}
              >
                + Agent stage
              </Button>
              <Button
                size="sm"
                disabled={atLimit}
                title={atLimit ? `At most ${String(MAX_STAGES)} stages` : undefined}
                onClick={() => {
                  add("shell");
                }}
              >
                + Shell stage
              </Button>
            </span>
          )
        }
      />

      {issues.form.map((msg) => (
        <p key={msg} className={s.formError} role="alert">
          {msg}
        </p>
      ))}

      <ol className={s.list}>
        {draft.rows.map((row, index) => {
          const name = row.stage.name.trim() || row.stage.id;
          return (
            <StageRow
              key={row.uid}
              row={row}
              index={index}
              count={draft.rows.length}
              issues={issues.stages[index] ?? {}}
              open={open.has(row.uid)}
              readOnly={readOnly}
              runtimes={runtimes}
              space={space}
              focusName={focusUid === row.uid}
              moveId={moveId}
              onToggleOpen={() => {
                setOpen((o) => {
                  const next = new Set(o);
                  if (!next.delete(row.uid)) next.add(row.uid);
                  return next;
                });
              }}
              onMove={(delta) => {
                move(row.uid, delta, index);
              }}
              onPatch={(patch) => {
                apply({ type: "patch", uid: row.uid, patch });
              }}
              onSetId={(id) => {
                apply({ type: "setId", uid: row.uid, id });
              }}
              onRemove={() => {
                remove(row.uid, name);
              }}
            />
          );
        })}
      </ol>
    </div>
  );
}
