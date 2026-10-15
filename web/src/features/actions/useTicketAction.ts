// Runs a ticket action from src/domain/actions.ts against the API. Shared by My queue rows, the
// Compact expansion and the Ticket page header so every surface does the same thing for the same
// button.
//   approve / retry / dispatch = POST runs {target} (the server picks next_stage) · cancel = POST
//   cancel · complete / abandon = open the inline confirm (ResolveConfirm); its confirm POSTs
//   resolve · reopen = PATCH state=open · request_changes = the Context tab (edit TASKS.md, then
//   Approve) · open = the peek (rows) — the header never offers it.
// Only a surface that shows the target choice (the Ticket page header, through TargetPicker) passes
// its target state; every other surface (My queue rows, Compact rows) always sends `server`, so a
// run only goes to a client from a click beside the visible choice.
import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router";

import type { ApiError } from "../../api/client";
import { useCancelRun, useDispatch, usePatchTicket, useResolveTicket } from "../../api/queries";
import type { Action, ActionId } from "../../domain/actions";
import type { Outcome } from "../../domain/outcome";
import { sentTarget } from "./target";
import type { DispatchTargetState } from "./useDispatchTarget";

interface ActionTarget {
  key: string;
  owner: { id: number } | null;
  latest_run: { id: number } | null;
}

/** `shown`: the target state of the choice this surface shows; without it the target is `server`. */
export function useTicketAction(t: ActionTarget, shown?: DispatchTargetState) {
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
        dispatch.mutate({ target: sentTarget(shown) });
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
