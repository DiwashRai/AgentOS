// The byline's "Base branch" (repo tickets only): the resolved branch in mono, "· repo default"
// when it is the repo's own default. Writers click it to edit inline (Enter / Save saves, Esc or
// blur cancels); a run locks it. The logic is in `branch.ts`.
import { useCallback, useId, useState, type MouseEvent } from "react";

import type { Schemas } from "../../api/client";
import { useConfig, usePatchTicket } from "../../api/queries";
import { Button, LinkButton, cx } from "../../ui";
import s from "./BaseBranchField.module.css";
import {
  branchError,
  branchErrorText,
  branchHint,
  branchLock,
  branchPatch,
  branchView,
  canSaveBranch,
  canUseDefault,
  repoDefault,
} from "./branch";

type Detail = Schemas["TicketDetail"];

/** Keep focus in the input when a button is pressed, so its blur doesn't cancel the edit. */
const keepFocus = (e: MouseEvent) => {
  e.preventDefault();
};

export function BaseBranchField({ t, readOnly }: { t: Detail; readOnly: boolean }) {
  const config = useConfig();
  const patch = usePatchTicket(t.key);
  const [draft, setDraft] = useState<string | null>(null); // null = at rest
  const ids = useId();
  const focusInput = useCallback((el: HTMLInputElement | null) => {
    el?.focus();
    el?.select();
  }, []);

  const def = repoDefault(t.repo_name, config.data?.repos);
  const view = branchView(t.base_branch, def);
  const lock = branchLock(t);
  // A run that starts mid-edit closes the editor.
  if (lock !== null && draft !== null) setDraft(null);

  const value = (
    <>
      <span className={cx(view.named && s.mono)}>{view.text}</span>
      {view.suffix ? <span className={s.suffix}>{view.suffix}</span> : null}
    </>
  );

  if (readOnly) return <span className={s.rest}>{value}</span>;

  if (draft === null || lock !== null) {
    return (
      <button
        type="button"
        className={s.value}
        disabled={lock !== null}
        title={lock ?? "Change the base branch"}
        onClick={() => {
          patch.reset();
          setDraft(t.base_branch ?? "");
        }}
      >
        {value}
      </button>
    );
  }

  const cancel = () => {
    patch.reset();
    setDraft(null);
  };
  const save = (body: Schemas["TicketPatch"]) => {
    patch.mutate(body, {
      onSuccess: () => {
        setDraft(null);
      },
    });
  };
  const invalid = branchError(draft);
  const canSave = canSaveBranch(draft, t.base_branch) && !patch.isPending;
  const hint = branchHint(t);
  const errId = `${ids}-err`;
  const hintId = `${ids}-hint`;
  const message = patch.error ? branchErrorText(patch.error) : invalid;
  const describedBy = [message ? errId : "", hint ? hintId : ""].filter(Boolean).join(" ");

  return (
    <form
      className={s.edit}
      onSubmit={(e) => {
        e.preventDefault();
        if (canSave) save(branchPatch(draft, def));
      }}
      onBlur={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget)) cancel();
      }}
    >
      <span className={s.row}>
        <input
          ref={focusInput}
          className={s.input}
          aria-label="Base branch"
          aria-invalid={message ? true : undefined}
          aria-describedby={describedBy || undefined}
          value={draft}
          readOnly={patch.isPending}
          spellCheck={false}
          autoComplete="off"
          onChange={(e) => {
            patch.reset();
            setDraft(e.target.value);
          }}
          onKeyDown={(e) => {
            if (e.key === "Escape") {
              e.preventDefault();
              e.stopPropagation();
              cancel();
            }
          }}
        />
        <Button type="submit" variant="ink" size="xs" disabled={!canSave} onMouseDown={keepFocus}>
          Save
        </Button>
        {canUseDefault(t.base_branch, def) ? (
          <LinkButton
            className={s.useDefault}
            disabled={patch.isPending}
            onMouseDown={keepFocus}
            onClick={() => {
              save({ base_branch: null });
            }}
          >
            Use repo default
          </LinkButton>
        ) : null}
      </span>
      {message ? (
        <span id={errId} className={s.err} role={patch.error ? "alert" : undefined}>
          {message}
        </span>
      ) : null}
      {hint ? (
        <span id={hintId} className={s.note}>
          {hint}
        </span>
      ) : null}
    </form>
  );
}
