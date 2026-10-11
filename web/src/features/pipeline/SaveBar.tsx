// The header of a pipeline page: title, meta, the unsaved marker, extra controls, Discard / Save,
// and the save error (with Reload on a 409 stale_version).
import type { ReactNode } from "react";

import { Button } from "../../ui";
import type { SaveProblem } from "./model";
import s from "./pages.module.css";

export interface SaveBarProps {
  title: string;
  meta: string;
  dirty: boolean;
  /** Hide Save/Discard entirely (no permission, or a run is active). */
  readOnly: boolean;
  saveDisabledReason: string | undefined;
  saving: boolean;
  problem: SaveProblem | null;
  onSave: () => void;
  onDiscard: () => void;
  onReload: () => void;
  /** Controls before Discard / Save (the Pipeline tab's "Run from…"). */
  extra?: ReactNode;
}

export function SaveBar(props: SaveBarProps) {
  return (
    <>
      <header className={s.bar}>
        <h2 className={s.title}>{props.title}</h2>
        <span className={s.meta}>{props.meta}</span>
        {props.dirty ? (
          <span className={s.unsaved} role="status">
            <i aria-hidden="true" />
            Unsaved changes
          </span>
        ) : null}
        <span className={s.spacer} />
        {props.extra}
        {props.readOnly ? null : (
          <span className={s.actions}>
            <Button
              variant="quiet"
              disabled={!props.dirty || props.saving}
              onClick={props.onDiscard}
            >
              Discard
            </Button>
            <Button
              variant="ink"
              disabled={!props.dirty || props.saving || props.saveDisabledReason !== undefined}
              title={props.saveDisabledReason}
              onClick={props.onSave}
            >
              {props.saving ? "Saving…" : "Save"}
            </Button>
          </span>
        )}
      </header>
      {props.problem ? (
        <p className={s.problem} role="alert">
          <span>{props.problem.text}</span>
          {props.problem.reload ? (
            <Button size="sm" onClick={props.onReload}>
              Reload
            </Button>
          ) : null}
        </p>
      ) : null}
    </>
  );
}
