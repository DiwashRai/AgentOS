import type { Schemas } from "../api/client";
import { cx } from "./cx";
import s from "./TypeIcon.module.css";

export type TicketType = Schemas["TicketSummary"]["type"];

const TYPE_WORD: Record<TicketType, string> = {
  feature: "Feature",
  bug: "Bug",
  improvement: "Improvement",
  task: "Task",
};

// Glyphs from the refresh mock (§4.8 `TYPE`), drawn on a 14px rounded square.
function Glyph({ type }: { type: TicketType }) {
  switch (type) {
    case "feature":
      return <path className={s.fill} d="M7.9 2.6 4.3 7.7h2.6l-.8 3.7 3.6-5.1H7.1z" />;
    case "bug":
      return <circle className={s.fill} cx="7" cy="7" r="2.6" />;
    case "improvement":
      return <path className={s.stroke} d="M7 10.6V3.9M4.3 6.5 7 3.8l2.7 2.7" />;
    case "task":
      return <path className={s.stroke} d="m4.1 7.3 2 2 3.8-4.3" />;
  }
}

export interface TypeIconProps {
  type: TicketType;
  /** Show the type's word beside the square ("Bug", "Feature"), as Intake does. */
  label?: boolean | undefined;
  className?: string | undefined;
}

/** The ticket type square (refresh mock §4.8): 14px, `--t-<type>` fill, `--t-glyph` glyph. */
export function TypeIcon({ type, label = false, className }: TypeIconProps) {
  const word = TYPE_WORD[type];
  const svg = (
    <svg
      className={cx(s.icon, s[type])}
      width="14"
      height="14"
      viewBox="0 0 14 14"
      aria-hidden="true"
    >
      <rect width="14" height="14" rx="3.5" />
      <Glyph type={type} />
    </svg>
  );
  if (label) {
    return (
      <span className={cx(s.labelled, className)}>
        {svg}
        <span>{word}</span>
      </span>
    );
  }
  return (
    <span className={cx(s.wrap, className)} role="img" aria-label={word} title={word}>
      {svg}
    </span>
  );
}
