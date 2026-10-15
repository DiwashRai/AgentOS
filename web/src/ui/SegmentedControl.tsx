// SegmentedControl: a row of pressed buttons on a recessed track, one of them pressed (the Compact
// filter pills, refresh mock §4.13 `.filter-chips`). A `role="group"` with an accessible name;
// each option is an `aria-pressed` button with an optional leading mark and a trailing count
// (Badge tone `count`, zero drawn quiet). When its container runs out of room the track scrolls
// sideways instead of widening the page.
import type { ReactNode } from "react";
import { Badge } from "./Badge";
import { cx } from "./cx";
import s from "./SegmentedControl.module.css";

export interface SegmentOption<V extends string | number> {
  /** The value `onChange` reports; also the option's React key. */
  value: V;
  /** The visible label (sentence case). */
  label: ReactNode;
  /** A leading glyph (a glyph-only StatusMark), hidden from assistive tech. */
  mark?: ReactNode;
  /** A trailing count; zero draws quiet. Omit for no count. */
  count?: number | undefined;
  /** The you hue on the label and the count (the Needs you filter). */
  you?: boolean | undefined;
}

export interface SegmentedControlProps<V extends string | number> {
  /** The group's accessible name ("Filter"). */
  label: string;
  options: readonly SegmentOption<V>[];
  /** The pressed option's value. */
  value: V;
  onChange: (value: V) => void;
  className?: string | undefined;
}

export function SegmentedControl<V extends string | number>({
  label,
  options,
  value,
  onChange,
  className,
}: SegmentedControlProps<V>) {
  return (
    <div className={cx(s.track, className)} role="group" aria-label={label}>
      {options.map((o) => (
        <button
          key={String(o.value)}
          type="button"
          className={cx(s.option, o.you && s.you)}
          aria-pressed={o.value === value}
          onClick={() => {
            onChange(o.value);
          }}
        >
          {o.mark ? (
            <span className={s.mark} aria-hidden="true">
              {o.mark}
            </span>
          ) : null}
          {o.label}
          {o.count === undefined ? null : <Badge tone="count" count={o.count} you={o.you} />}
        </button>
      ))}
    </div>
  );
}
