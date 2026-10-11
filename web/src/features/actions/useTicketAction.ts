// Runs a ticket action from src/domain/actions.ts against the API. Shared by My queue rows and the
// Ticket page header so every surface does the same thing for the same button.
//   approve / retry / dispatch = POST runs {} (the server picks next_stage) · cancel = POST cancel
//   mark_done / reopen = PATCH state · request_changes = the Context tab (edit TASKS.md, then Approve)
//   open = the peek (rows) — the header never offers it.
import { useNavigate, useSearchParams } from "react-router";

import type { ApiError } from "../../api/client";
import { useCancelRun, useDispatch, usePatchTicket } from "../../api/queries";
import type { Action, ActionId } from "../../domain/actions";

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
      case "mark_done":
        patch.mutate({ state: "done" });
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
    pending: dispatch.isPending || cancel.isPending || patch.isPending,
    error,
    reset: () => {
      dispatch.reset();
      cancel.reset();
      patch.reset();
    },
  };
}
