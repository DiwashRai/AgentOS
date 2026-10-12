import { ageParts } from "../domain/format";
import s from "./Age.module.css";
import { cx } from "./cx";

export interface AgeProps {
  /** An age as `domain/format.ts` `age()` writes it: `38m`, `3h 05m`, `2d`, `now`. */
  text: string;
  /** `md` (default) takes its size and colour from the cell; `lg` is the My queue Needs-you age
   *  (14/600 ink-1, units 12px). */
  size?: "md" | "lg" | undefined;
  title?: string | undefined;
  className?: string | undefined;
}

/** An age (refresh HANDOFF §5 "Age"): tabular numbers, each unit a size smaller. */
export function Age({ text, size = "md", title, className }: AgeProps) {
  return (
    <span className={cx(s.age, size === "lg" && s.lg, className)} title={title}>
      {ageParts(text).map((p, i) => (
        <span key={i}>
          {i > 0 ? " " : null}
          {p.n}
          {p.unit ? <span className={s.unit}>{p.unit}</span> : null}
        </span>
      ))}
    </span>
  );
}
