// Loading and error states in the quiet language (DESIGN §18): a mono caps line in --ink-3.
// No spinners, no toasts.
import s from "./Quiet.module.css";

export function Quiet({ children, error }: { children: string; error?: boolean }) {
  return (
    <p className={s.quiet} role={error ? "alert" : "status"} data-error={error ? "" : undefined}>
      {children}
    </p>
  );
}
