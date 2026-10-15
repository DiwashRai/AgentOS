// The byline's "Base branch" (repo tickets only): the resolved branch in mono with the server's
// source line ("· from Jira · version_1.0", "· set by Sam", "· repo default"), or "Not set · <why>"
// drawn as needing action. Writers click it to edit inline (Enter / Save saves, Esc or blur
// cancels). An active run disables it; the server's lock (a run started the ticket branch from
// it) turns it into plain text with "Locked by run #N". The logic is in `branch.ts`.
import { useCallback, useId, type MouseEvent } from "react";
import { Link as RouterLink } from "react-router";

import type { Schemas } from "../../api/client";
import { useConfig, usePatchTicket } from "../../api/queries";
import { fieldStyles as f } from "../../editor";
import { Button, Inline, LinkButton, cx, linkClass, text } from "../../ui";
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
  lockNote,
  repoDefault,
} from "./branch";
import ie from "./InlineEdit.module.css";
import { useInlineEdit } from "./useInlineEdit";

type Detail = Schemas["TicketDetail"];

/** Keep focus in the input when a button is pressed, so its blur doesn't cancel the edit. */
const keepFocus = (e: MouseEvent) => {
  e.preventDefault();
};

function LockNote({ t }: { t: Detail }) {
  const note = lockNote(t);
  if (!note) return null;
  return (
    <span className={cx(text.meta, s.lock)}>
      {note.href ? (
        <RouterLink className={linkClass} to={note.href}>
          {note.text}
        </RouterLink>
      ) : (
        note.text
      )}
    </span>
  );
}

export function BaseBranchField({ t, readOnly }: { t: Detail; readOnly: boolean }) {
  const config = useConfig();
  const patch = usePatchTicket(t.key);
  const lock = branchLock(t);
  // A run that starts mid-edit closes the editor.
  const edit = useInlineEdit<string, Schemas["TicketPatch"]>(patch, { locked: lock !== null });
  const ids = useId();
  const focusInput = useCallback((el: HTMLInputElement | null) => {
    el?.focus();
    el?.select();
  }, []);

  const def = repoDefault(t.repo_name, config.data?.repos);
  const view = branchView(t);
  const locked = t.base_branch_info?.locked === true;
  const draft = edit.draft;

  const value = (
    <>
      <span className={cx(view.named && s.mono, view.missing && s.missing)}>{view.text}</span>
      {view.source ? (
        <span className={cx(s.suffix, view.missing && s.missing)}>{`· ${view.source}`}</span>
      ) : null}
    </>
  );

  // Locked (or a viewer): plain text, with the lock note beside it.
  if (readOnly || locked) {
    return (
      <Inline as="span" gap={2} align="baseline" title={locked ? (lock ?? undefined) : undefined}>
        {value}
        <LockNote t={t} />
      </Inline>
    );
  }

  if (draft === null || lock !== null) {
    return (
      <button
        type="button"
        className={ie.value}
        disabled={lock !== null}
        title={lock ?? "Change the base branch"}
        onClick={() => {
          edit.start(t.base_branch ?? "");
        }}
      >
        {value}
      </button>
    );
  }

  // An empty input (a blank ticket's editor opens empty) just can't save; no "Required." yet.
  const invalid = draft.trim() === "" ? undefined : branchError(draft);
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
        if (canSave) edit.save(branchPatch(draft, def));
      }}
      onBlur={edit.onBlur}
    >
      <Inline as="span" gap={4}>
        <input
          ref={focusInput}
          className={cx(f.input, f.mono, s.input)}
          aria-label="Base branch"
          aria-invalid={message ? true : undefined}
          aria-describedby={describedBy || undefined}
          value={draft}
          readOnly={patch.isPending}
          spellCheck={false}
          autoComplete="off"
          onChange={(e) => {
            patch.reset();
            edit.change(e.target.value);
          }}
          onKeyDown={edit.onKeyDown}
        />
        <Button type="submit" variant="ink" size="xs" disabled={!canSave} onMouseDown={keepFocus}>
          Save
        </Button>
        {canUseDefault(t.base_branch_info, def) ? (
          <LinkButton
            className={s.useDefault}
            disabled={patch.isPending}
            onMouseDown={keepFocus}
            onClick={() => {
              edit.save({ base_branch: null });
            }}
          >
            Use repo default
          </LinkButton>
        ) : null}
      </Inline>
      {message ? (
        <span
          id={errId}
          className={cx(text.error, ie.msg)}
          role={patch.error ? "alert" : undefined}
        >
          {message}
        </span>
      ) : null}
      {hint ? (
        <span id={hintId} className={cx(text.meta, ie.msg)}>
          {hint}
        </span>
      ) : null}
    </form>
  );
}
