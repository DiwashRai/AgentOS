// The byline's "Base branch" (repo tickets only): the resolved branch in mono with the server's
// source line ("· from Jira · version_1.0", "· set by Sam", "· repo default"), or "Not set · <why>"
// drawn as needing action. Writers click it to edit inline (Enter / Save saves, Esc or blur
// cancels). An active run disables it; the server's lock (a run started the ticket branch from
// it) turns it into plain text with "Locked by run #N". The logic is in `branch.ts`.
import { useCallback, useId, useState, type MouseEvent } from "react";
import { Link as RouterLink } from "react-router";

import type { Schemas } from "../../api/client";
import { useConfig, usePatchTicket } from "../../api/queries";
import { Button, LinkButton, cx, linkClass } from "../../ui";
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

type Detail = Schemas["TicketDetail"];

/** Keep focus in the input when a button is pressed, so its blur doesn't cancel the edit. */
const keepFocus = (e: MouseEvent) => {
  e.preventDefault();
};

function LockNote({ t }: { t: Detail }) {
  const note = lockNote(t);
  if (!note) return null;
  return (
    <span className={s.lock}>
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
  const [draft, setDraft] = useState<string | null>(null); // null = at rest
  const ids = useId();
  const focusInput = useCallback((el: HTMLInputElement | null) => {
    el?.focus();
    el?.select();
  }, []);

  const def = repoDefault(t.repo_name, config.data?.repos);
  const view = branchView(t);
  const lock = branchLock(t);
  const locked = t.base_branch_info?.locked === true;
  // A run that starts mid-edit closes the editor.
  if (lock !== null && draft !== null) setDraft(null);

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
      <span className={s.rest} title={locked ? (lock ?? undefined) : undefined}>
        {value}
        <LockNote t={t} />
      </span>
    );
  }

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
        {canUseDefault(t.base_branch_info, def) ? (
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
