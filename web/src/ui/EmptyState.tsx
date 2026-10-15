// EmptyState: every "nothing here" in one primitive. `row` is the dashed EmptyRow; the other forms
// are a sentence (an optional 600 lead, then the note, which may hold a link) and an optional
// action after it:
//   line   a view's own empty line with its way out: 12.5 --ink-2, padded 14 2
//   inset  where a list's rows would be: 12.5 --ink-3, padded to the row inset (10 14)
//   note   inside a section ("No plan yet."): 12 --ink-3, no padding
// Loading, permission and error lines are StatusLine, not this.
import type { ReactNode } from "react";
import { cx } from "./cx";
import { EmptyRow } from "./EmptyRow";
import s from "./EmptyState.module.css";

export type EmptyForm = "row" | "line" | "inset" | "note";

export interface EmptyStateProps {
  /** row (default), line, inset or note. */
  form?: EmptyForm | undefined;
  /** The lead word in 600 ("All quiet"). The row form always draws one. */
  lead?: string | undefined;
  /** The note: text, or text with a link in it. */
  children?: ReactNode;
  /** A button or link after the sentence (at the far end of a row). */
  action?: ReactNode;
  className?: string | undefined;
}

export function EmptyState({ form = "row", lead, children, action, className }: EmptyStateProps) {
  if (form === "row") {
    return (
      <EmptyRow lead={lead ?? ""} action={action} className={className}>
        {children}
      </EmptyRow>
    );
  }

  return (
    <div className={cx(s.state, s[form], className)}>
      <p>
        {lead ? <b>{lead}</b> : null}
        {lead && children ? " " : null}
        {children}
      </p>
      {action}
    </div>
  );
}
