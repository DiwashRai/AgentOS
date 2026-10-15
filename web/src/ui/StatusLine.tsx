// Loading, permission and error states in the quiet language: one sentence-case line in --ink-3
// (--blocked for an error), announced as a status or an alert. No spinners, no toasts.
import { cx } from "./cx";
import s from "./StatusLine.module.css";

export interface StatusLineProps {
  children: string;
  error?: boolean | undefined;
  /** Stands alone in a view (padded, control size), not inside a page's content column. */
  inset?: boolean | undefined;
}

export function StatusLine({ children, error, inset }: StatusLineProps) {
  return (
    <p
      className={cx(s.line, inset && s.inset)}
      role={error ? "alert" : "status"}
      data-error={error ? "" : undefined}
    >
      {children}
    </p>
  );
}
