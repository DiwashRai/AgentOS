// The ticket action group (DESIGN §13.8, gate primacy; web/AGENTS.md "Actions"), shared by queue
// rows and the Ticket page header. Pure: it reads server fields (`attention`, which rail gate is
// parked on you, whether an enabled stage follows it, `latest_run.cancel_requested_at`) and picks
// buttons. It never derives board state. What each action does lives with the caller:
//   approve = POST runs {} (the server picks next_stage) · mark_done = PATCH state=done ·
//   retry = POST runs {} · dispatch = POST runs {from_stage} ("Run from…") · cancel = POST cancel ·
//   reopen = PATCH state=open · request_changes = go to the Context tab · open = go to the ticket.
import type { Schemas } from "../api/client";
import { firstName } from "./format";
import { words } from "./words";

export type ActionId =
  "approve" | "mark_done" | "retry" | "dispatch" | "cancel" | "reopen" | "request_changes" | "open";

/** `ink` = the one primary; `accent` = Dispatch run on a ticket only; `quiet` = transparent. */
export type ActionVariant = "ink" | "accent" | "default" | "quiet";

export interface Action {
  id: ActionId;
  label: string;
  variant: ActionVariant;
  title?: string | undefined;
  disabled?: boolean | undefined;
}

export interface ActionSet {
  /** The one strong button, rendered rightmost. */
  primary: Action | null;
  /** Rendered left → right before the primary. */
  secondary: Action[];
  /** A cancel was requested and the run hasn't settled yet. */
  cancelling: boolean;
}

/** Where the group renders: a list row (no accent, no dispatch) or the Ticket page header. */
export type ActionContext = "row" | "header";

export type ActionTicket = Pick<
  Schemas["TicketSummary"],
  "attention" | "rail" | "latest_run" | "now"
>;

type Gate = Schemas["GateInfo"];

const act = (
  id: ActionId,
  label: string,
  variant: ActionVariant,
  extra: Pick<Action, "title" | "disabled"> = {},
): Action => ({ id, label, variant, ...extra });

/** The gate the server says is parked in `state`, and whether an enabled stage follows it. */
function parkedGate(t: ActionTicket, state: Gate["state"]): { gate: Gate; more: boolean } | null {
  const i = t.rail.findIndex((s) => s.gate?.state === state);
  const gate = t.rail[i]?.gate;
  if (!gate) return null;
  return { gate, more: t.rail.slice(i + 1).some((s) => s.state !== "skipped") };
}

function waitingTitle(t: ActionTicket): string {
  const parked = parkedGate(t, "waiting");
  const who = parked?.gate.person ?? t.now.person;
  const name = who ? firstName(who) : "someone";
  return parked ? `Waiting on ${name} at ${parked.gate.label}` : `Waiting on ${name}`;
}

export function ticketActions(t: ActionTicket, ctx: ActionContext): ActionSet {
  const header = ctx === "header";
  const open = act("open", words.open, "default");
  // No quiet Dispatch beside an ink primary: POST runs {} would do exactly what Approve or Retry
  // does ("Run from…" lives in the Pipeline tab).
  const set = (
    primary: Action | null,
    secondary: Action[] = [],
    cancelling = false,
  ): ActionSet => ({
    primary,
    secondary,
    cancelling,
  });

  switch (t.attention) {
    case "needs_you": {
      const parked = parkedGate(t, "you");
      const changes = parked ? [act("request_changes", words.requestChanges, "default")] : [];
      if (parked?.more) {
        const approve = act("approve", words.approve, "ink", { title: parked.gate.label });
        return set(approve, changes);
      }
      // The last gate, or no gate at all (a succeeded run): the only step left is done.
      return set(act("mark_done", words.markDone, "ink"), changes);
    }
    case "waiting":
      return set(
        null,
        header ? [act("dispatch", words.dispatch, "default", { title: waitingTitle(t) })] : [open],
      );
    case "blocked":
      return set(act("retry", words.retry, "ink"), header ? [] : [open]);
    case "running":
    case "queued": {
      const cancelling = t.latest_run?.cancel_requested_at != null;
      const cancel = cancelling
        ? act("cancel", words.cancelling, "default", { disabled: true })
        : act("cancel", words.cancel, "default");
      return set(null, [cancel], cancelling);
    }
    case "inbox":
      // The accent belongs only on a ticket; a row just opens it.
      return header
        ? set(act("dispatch", words.dispatch, "accent"))
        : set(act("open", words.open, "ink"));
    case "done":
      return set(null, [act("reopen", words.reopen, "default")]);
  }
}
