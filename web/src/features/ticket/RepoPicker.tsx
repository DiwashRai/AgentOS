// The byline's "Repo": an inline select over the Space's repo catalog plus "No repo", drawn like
// the owner picker. A change that would lose something (the base branch the ticket set itself, the
// stages that need a repo) opens a one-line confirm under it first; otherwise it saves at once. An
// active run disables it; the server's lock turns it into plain text (the "Locked by run #N" note
// shows once, on the base branch beside it). Viewers see plain text. The logic is in `repo.ts`.
import { useMemo } from "react";

import type { Schemas } from "../../api/client";
import { useConfig, usePatchTicket } from "../../api/queries";
import { Button, ChevronIcon, Inline, Stack, cx, text } from "../../ui";
import ie from "./InlineEdit.module.css";
import {
  NO_REPO_LABEL,
  repoErrorText,
  repoLock,
  repoOptions,
  repoPatch,
  repoValue,
  repoWarning,
} from "./repo";
import s from "./RepoPicker.module.css";
import { useInlineEdit } from "./useInlineEdit";

type Detail = Schemas["TicketDetail"];

export function RepoPicker({ t, readOnly }: { t: Detail; readOnly: boolean }) {
  const config = useConfig();
  const repos = config.data?.repos;
  const patch = usePatchTicket(t.key);
  const lock = repoLock(t);
  // The draft is the value picked but not saved yet (awaiting the confirm, or the save itself). A
  // run that starts mid-confirm closes it; a save closes it however it ends (the error shows at rest).
  const edit = useInlineEdit<string, Schemas["TicketPatch"]>(patch, {
    locked: lock !== null,
    closeOnError: true,
  });
  const options = useMemo(() => repoOptions(t.repo_name, repos), [t.repo_name, repos]);

  const current = repoValue(t.repo_name);
  const locked = t.base_branch_info?.locked === true;
  const chosen = edit.draft;

  if (readOnly || locked) {
    return (
      <span title={locked ? (lock ?? undefined) : undefined}>{t.repo_name ?? NO_REPO_LABEL}</span>
    );
  }

  const save = (value: string) => {
    edit.save(repoPatch(value));
  };
  const warning = chosen === null || patch.isPending ? null : repoWarning(t, chosen, repos);

  return (
    <Stack as="span" gap={2} align="start">
      <span className={ie.pick}>
        <select
          className={ie.value}
          aria-label="Repo"
          value={chosen ?? current}
          disabled={lock !== null || patch.isPending}
          title={lock ?? undefined}
          aria-invalid={patch.error ? true : undefined}
          onChange={(e) => {
            const v = e.target.value;
            if (v === current) {
              edit.cancel();
              return;
            }
            edit.start(v);
            if (repoWarning(t, v, repos) === null) save(v);
          }}
        >
          {options.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
        {lock !== null ? null : <ChevronIcon />}
      </span>
      {warning !== null && chosen !== null ? (
        <Inline
          as="span"
          wrap
          gap={4}
          rowGap={3}
          className={cx(text.meta, ie.msg, s.confirm)}
          role="group"
          aria-label="Change repo"
        >
          <span>{warning}</span>
          {/* The buttons stay together; the text wraps first. Esc on either cancels. */}
          <Inline as="span" gap={4} className={s.buttons}>
            <Button
              size="xs"
              variant="ink"
              onKeyDown={edit.onKeyDown}
              onClick={() => {
                save(chosen);
              }}
            >
              Change repo
            </Button>
            <Button size="xs" variant="quiet" onKeyDown={edit.onKeyDown} onClick={edit.cancel}>
              Cancel
            </Button>
          </Inline>
        </Inline>
      ) : null}
      {patch.error ? (
        <span className={cx(text.error, ie.msg)} role="alert">
          {repoErrorText(patch.error)}
        </span>
      ) : null}
    </Stack>
  );
}
