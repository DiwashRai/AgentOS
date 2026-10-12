import { cx } from "./cx";
import s from "./CountPill.module.css";

export interface CountPillProps {
  count: number;
  /** neutral (default), you (Needs you heads) or done (Done heads). */
  tone?: "neutral" | "you" | "done" | undefined;
  className?: string | undefined;
}

/** The count pill of zone and group heads (refresh mock §4.2 `.count-pill`): 20×18 min, 12/600. */
export function CountPill({ count, tone = "neutral", className }: CountPillProps) {
  return <span className={cx(s.pill, tone !== "neutral" && s[tone], className)}>{count}</span>;
}
