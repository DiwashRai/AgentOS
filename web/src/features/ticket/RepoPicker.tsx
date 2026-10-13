// The byline's "Repo": an inline select over the Space's repo catalog plus "No repo", drawn like
// the owner picker. A change that would lose something (the base branch the ticket set itself, the
// stages that need a repo) opens a one-line confirm under it first; otherwise it saves at once. An
// active run disables it; the server's lock turns it into plain text (the "Locked by run #N" note
// shows once, on the base branch beside it). Viewers see plain text. The logic is in `repo.ts`.
import { useMemo, useState, type KeyboardEvent } from "react";

import type { Schemas } from "../../api/client";
import { useConfig, usePatchTicket } from "../../api/queries";
import { Button, ChevronIcon } from "../../ui";
import hs from "./Header.module.css";
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

type Detail = Schemas["TicketDetail"];

export function RepoPicker({ t, readOnly }: { t: Detail; readOnly: boolean }) {
  const config = useConfig();
  const repos = config.data?.repos;
  const patch = usePatchTicket(t.key);
  // The value picked but not saved yet (awaiting the confirm, or the save itself); null = at rest.
  const [chosen, setChosen] = useState<string | null>(null);
  const options = useMemo(() => repoOptions(t.repo_name, repos), [t.repo_name, repos]);

  const current = repoValue(t.repo_name);
  const lock = repoLock(t);
  const locked = t.base_branch_info?.locked === true;
  // A run that starts mid-confirm closes it.
  if (lock !== null && chosen !== null && !patch.isPending) setChosen(null);

  if (readOnly || locked) {
    return (
      <span title={locked ? (lock ?? undefined) : undefined}>{t.repo_name ?? NO_REPO_LABEL}</span>
    );
  }

  const save = (value: string) => {
    patch.mutate(repoPatch(value), {
      onSettled: () => {
        setChosen(null);
      },
    });
  };
  const cancel = () => {
    setChosen(null);
  };
  const warning = chosen === null || patch.isPending ? null : repoWarning(t, chosen, repos);
  /** Esc cancels from the confirm's buttons (and doesn't also leave the page). */
  const onKeyDown = (e: KeyboardEvent) => {
    if (e.key === "Escape") {
      e.stopPropagation();
      cancel();
    }
  };

  return (
    <span className={s.repo}>
      <span className={hs.pick}>
        <select
          aria-label="Repo"
          value={chosen ?? current}
          disabled={lock !== null || patch.isPending}
          title={lock ?? undefined}
          aria-invalid={patch.error ? true : undefined}
          onChange={(e) => {
            const v = e.target.value;
            patch.reset();
            if (v === current) {
              setChosen(null);
              return;
            }
            setChosen(v);
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
        <span className={s.confirm} role="group" aria-label="Change repo">
          <span>{warning}</span>
          <span className={s.buttons}>
            <Button
              size="xs"
              variant="ink"
              onKeyDown={onKeyDown}
              onClick={() => {
                save(chosen);
              }}
            >
              Change repo
            </Button>
            <Button size="xs" variant="quiet" onKeyDown={onKeyDown} onClick={cancel}>
              Cancel
            </Button>
          </span>
        </span>
      ) : null}
      {patch.error ? (
        <span className={s.err} role="alert">
          {repoErrorText(patch.error)}
        </span>
      ) : null}
    </span>
  );
}
