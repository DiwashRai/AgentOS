// Runs a ticket action from src/domain/actions.ts against the API. Shared by My queue rows, the
// Compact expansion and the Ticket page header so every surface does the same thing for the same
// button.
//   approve / retry / dispatch = POST runs {} (the server picks next_stage) · cancel = POST cancel
//   complete / abandon = open the inline confirm (ResolveConfirm); its confirm POSTs resolve
//   reopen = PATCH state=open · request_changes = the Context tab (edit TASKS.md, then Approve)
//   open = the peek (rows) — the header never offers it.
import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router";

import type { ApiError } from "../../api/client";
import { useCancelRun, useDispatch, usePatchTicket, useResolveTicket } from "../../api/queries";
import type { Action, ActionId } from "../../domain/actions";
import type { Outcome } from "../../domain/outcome";

interface ActionTarget {
  key: string;
  latest_run: { id: number } | null;
}

export function useTicketAction(t: ActionTarget) {
  const navigate = useNavigate();
  const [, setParams] = useSearchParams();
  const dispatch = useDispatch(t.key);
  const cancel = useCancelRun(t.key);
  const patch = usePatchTicket(t.key);
  const resolve = useResolveTicket(t.key);
  // Complete / Abandon ask first: the outcome being confirmed, or null when no confirm is open.
  const [confirming, setConfirming] = useState<Outcome | null>(null);

  const reset = () => {
    dispatch.reset();
    cancel.reset();
    patch.reset();
    resolve.reset();
  };

  const run = (id: ActionId) => {
    switch (id) {
      case "approve":
      case "retry":
      case "dispatch":
        dispatch.mutate(undefined);
        return;
      case "cancel":
        if (t.latest_run) cancel.mutate(t.latest_run.id);
        return;
      case "complete":
        setConfirming("completed");
        return;
      case "abandon":
        setConfirming("abandoned");
        return;
      case "reopen":
        patch.mutate({ state: "open" });
        return;
      case "request_changes":
        void navigate(`/t/${encodeURIComponent(t.key)}/context`);
        return;
      case "open":
        setParams((p) => {
          p.set("peek", t.key);
          return p;
        });
        return;
    }
  };

  const error = (dispatch.error ?? cancel.error ?? patch.error) as ApiError | null;
  return {
    run: (a: Action) => {
      run(a.id);
    },
    pending: dispatch.isPending || cancel.isPending || patch.isPending || resolve.isPending,
    /** Errors of the plain actions; a resolve error shows inside the confirm instead. */
    error,
    reset,
    /** The open Complete / Abandon confirm (src/features/actions/ResolveConfirm.tsx). */
    confirm: confirming && {
      outcome: confirming,
      pending: resolve.isPending,
      error: resolve.error as ApiError | null,
      submit: (note: string) => {
        const trimmed = note.trim();
        resolve.mutate(
          { outcome: confirming, note: trimmed === "" ? null : trimmed },
          {
            onSuccess: () => {
              setConfirming(null);
            },
          },
        );
      },
      dismiss: () => {
        setConfirming(null);
        resolve.reset();
      },
    },
  };
}

export type ResolveConfirmState = NonNullable<ReturnType<typeof useTicketAction>["confirm"]>;
