// A row's own buttons (gate primacy, src/domain/actions.ts), run through useTicketAction. While an
// action is pending the buttons are disabled; a failure shows as a quiet inline error.
import type { Schemas } from "../../api/client";
import { type Action, ticketActions } from "../../domain/actions";
import { Button } from "../../ui";
import { useTicketAction } from "../actions/useTicketAction";
import s from "./Queue.module.css";

export interface RowActionsProps {
  ticket: Schemas["TicketSummary"];
  /** Only the primary (the quiet Blocked rows show just Retry). */
  primaryOnly?: boolean | undefined;
}

export function RowActions({ ticket, primaryOnly = false }: RowActionsProps) {
  const action = useTicketAction(ticket);
  const set = ticketActions(ticket, "row");
  const buttons: Action[] = [
    ...(primaryOnly ? [] : set.secondary),
    ...(set.primary ? [set.primary] : []),
  ];
  if (buttons.length === 0) return null;
  const message = action.error ? `Failed · ${action.error.message}` : null;
  return (
    <div className={s.acts}>
      <div className={s.btns}>
        {buttons.map((a) => (
          <Button
            key={a.id}
            size="sm"
            variant={a.variant === "ink" ? "ink" : "default"}
            title={a.title}
            disabled={a.disabled === true || action.pending}
            onClick={() => {
              action.reset();
              action.run(a);
            }}
          >
            {a.label}
          </Button>
        ))}
      </div>
      {message && (
        <span className={s.err} role="alert" title={message}>
          {message}
        </span>
      )}
    </div>
  );
}
