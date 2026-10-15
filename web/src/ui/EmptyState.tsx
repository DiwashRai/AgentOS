// EmptyState: every "nothing here" in one primitive. The forms:
//   row    the dashed empty row (refresh mock §4.3): 34px, one line, a small dotted ring at the
//          left, the 600 lead, the note, and the action at the far end
// The others are a sentence (an optional 600 lead, then the note, which may hold a link) and an
// optional action after it:
//   line   a view's own empty line with its way out: 12.5 --ink-2, padded 14 2
//   inset  where a list's rows would be: 12.5 --ink-3, padded to the row inset (10 14)
//   note   inside a section ("No plan yet."): 12 --ink-3, no padding
// Loading, permission and error lines are StatusLine, not this.
import type { ReactNode } from "react";
import { cx } from "./cx";
import s from "./EmptyState.module.css";

type EmptyForm = "row" | "line" | "inset" | "note";

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
      <div className={cx(s.row, className)}>
        <i aria-hidden="true" />
        <b>{lead}</b>
        {children ? <span>{children}</span> : null}
        {action ? <div className={s.action}>{action}</div> : null}
      </div>
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
