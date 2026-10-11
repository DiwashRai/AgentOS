import type { CSSProperties, ReactNode } from "react";
import { cx } from "./cx";
import { meterOfText, meterSegments, stripLabel, type MeterState, type SegState } from "./meter";
import s from "./RunMeter.module.css";

export interface RunMeterProps {
  /** The step in progress, 1-based (3 while step 3 of 5 runs). Ignored when finished or queued. */
  k: number;
  /** Number of steps. */
  n: number;
  state: MeterState;
  size: "tile" | "row" | "hair" | "strip";
  /** Row size only: total width in px (56 default; 48 inline; 36 in the queue rack). */
  width?: number | undefined;
  /** Strip size only: the step names under the segments. */
  labels?: string[] | undefined;
  className?: string | undefined;
}

const SEG: Record<SegState, string | undefined> = { done: s.isDone, cur: s.isCur, up: undefined };

/** Let long step names (paths, dotted names) wrap after `.`, `_` and `/`. */
function breakable(text: string): ReactNode[] {
  return text.split(/(?<=[._/])/).map((part, i) => (
    <span key={i}>
      {i > 0 ? <wbr /> : null}
      {part}
    </span>
  ));
}

export function RunMeter({ k, n, state, size, width, labels, className }: RunMeterProps) {
  const segs = meterSegments(k, n, state);
  const cls = cx(
    s.rm,
    s[size],
    state === "finished" && s.isFinished,
    state === "queued" && s.isQueued,
    className,
  );
  const label = `${meterOfText(k, n, state)} steps`;
  const style =
    width !== undefined ? ({ "--rm-w": `${String(width)}px` } as CSSProperties) : undefined;

  if (size === "strip") {
    return (
      <ol className={cls} role="img" aria-label={label} style={style}>
        {segs.map((seg, i) => {
          const name = labels?.[i] ?? "";
          return (
            <li key={i} className={SEG[seg]}>
              <i className={cx(s.seg, SEG[seg])} />
              <span className={s.k}>{stripLabel(segs, i, state)}</span>
              <span title={name}>{breakable(name)}</span>
            </li>
          );
        })}
      </ol>
    );
  }
  return (
    <div className={cls} role="img" aria-label={label} style={style}>
      {segs.map((seg, i) => (
        <i key={i} className={cx(s.seg, SEG[seg])} />
      ))}
    </div>
  );
}
