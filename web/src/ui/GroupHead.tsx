import type { ReactNode } from "react";
import { cx } from "./cx";
import s from "./GroupHead.module.css";
import { ChevronIcon } from "./icons";
import { StatusMark, type MarkKind } from "./StatusMark";

export interface GroupHeadProps {
  name: string;
  count?: number | undefined;
  /** Status glyph shown at 7px before the name. */
  glyph?: MarkKind | undefined;
  /** Mono summary after the hairline; `<b>` inside is emphasised. */
  summary?: ReactNode;
  collapsed: boolean;
  onToggle: () => void;
  /** id of the group body this head controls. */
  controls?: string | undefined;
  className?: string | undefined;
}

export function GroupHead({
  name,
  count,
  glyph,
  summary,
  collapsed,
  onToggle,
  controls,
  className,
}: GroupHeadProps) {
  return (
    <button
      type="button"
      className={cx(s.gh, className)}
      aria-expanded={!collapsed}
      aria-controls={controls}
      onClick={onToggle}
    >
      <ChevronIcon />
      {glyph ? (
        <span className={s.g} aria-hidden="true">
          <StatusMark kind={glyph} glyphOnly size={7} />
        </span>
      ) : null}
      <span>{name}</span>
      {count !== undefined ? <span className={s.c}>{count}</span> : null}
      <span className={s.r} aria-hidden="true" />
      {summary ? <span className={s.s}>{summary}</span> : null}
    </button>
  );
}
