import { cx } from "./cx";
import s from "./TickBadge.module.css";

export interface TickBadgeProps {
  /** 12 inline (marks, heads, "2 shipped"), 14 in the plan list and the stepper's final stage. */
  size?: 12 | 14 | undefined;
  /** Accessible name; without one the badge is decorative (the text beside it says it). */
  label?: string | undefined;
  className?: string | undefined;
}

/** The green tick in a circle (refresh mock §4.9 `.check-badge`): `--ck` circle, `--ck-glyph` tick. */
export function TickBadge({ size = 12, label, className }: TickBadgeProps) {
  const a11y = label
    ? ({ role: "img", "aria-label": label } as const)
    : ({ "aria-hidden": true } as const);
  return (
    <span className={cx(s.badge, className)} {...a11y}>
      <svg width={size} height={size} viewBox="0 0 14 14" aria-hidden="true">
        <circle cx="7" cy="7" r="7" />
        <path d="m4 7.2 2 2 4-4.4" />
      </svg>
    </span>
  );
}
