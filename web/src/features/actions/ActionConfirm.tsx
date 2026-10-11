// The open Complete / Abandon confirm of a useTicketAction, if any. Every surface that renders the
// action group renders this right after it.
import { actionErrorText } from "../ticket/model";
import { ResolveConfirm } from "./ResolveConfirm";
import type { ResolveConfirmState } from "./useTicketAction";

export function ActionConfirm({
  confirm,
  className,
}: {
  confirm: ResolveConfirmState | null;
  className?: string | undefined;
}) {
  if (!confirm) return null;
  return (
    <ResolveConfirm
      outcome={confirm.outcome}
      pending={confirm.pending}
      error={confirm.error ? actionErrorText(confirm.error) : null}
      onSubmit={confirm.submit}
      onCancel={confirm.dismiss}
      className={className}
    />
  );
}
