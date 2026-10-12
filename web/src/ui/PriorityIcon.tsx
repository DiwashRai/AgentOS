import type { Schemas } from "../api/client";
import { cx } from "./cx";
import s from "./PriorityIcon.module.css";

export type Priority = Schemas["TicketSummary"]["priority"];

export interface PriorityIconProps {
  priority: Priority | null | undefined;
  className?: string | undefined;
}

/**
 * The 12px priority chevrons (refresh mock §4.8). High = double chevron up, low = chevron down;
 * normal is an empty slot of the same width, always reserved so titles share one x. The empty slot
 * is hidden from assistive tech.
 */
export function PriorityIcon({ priority, className }: PriorityIconProps) {
  if (priority !== "high" && priority !== "low") {
    return <span className={cx(s.slot, className)} aria-hidden="true" />;
  }
  const word = priority === "high" ? "High priority" : "Low priority";
  return (
    <span className={cx(s.slot, s[priority], className)} role="img" aria-label={word} title={word}>
      <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
        <path
          d={
            priority === "high"
              ? "M2.5 6.2 6 3l3.5 3.2M2.5 9.7 6 6.5l3.5 3.2"
              : "M2.5 4.5 6 7.7l3.5-3.2"
          }
        />
      </svg>
    </span>
  );
}
