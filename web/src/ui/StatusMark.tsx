import { cx } from "./cx";
import s from "./StatusMark.module.css";

export type MarkKind = "run" | "wait" | "you" | "blk" | "done" | "abandoned" | "queued" | "idle";

/** Fallback accessible names for a glyph-only mark (DESIGN §16 vocabulary). */
export const MARK_WORD: Record<MarkKind, string> = {
  run: "Running",
  wait: "Waiting on someone",
  you: "Needs you",
  blk: "Blocked",
  done: "Done",
  abandoned: "Abandoned",
  queued: "Queued",
  idle: "Inbox",
};

export interface StatusMarkProps {
  kind: MarkKind;
  /** Mono caps label ("Builder · run #42", "Sam", "Blocked"). Ignored for `you`, which always shows the YOU pill. */
  name?: string | undefined;
  /** Mono caps tail in the mark's colour: "· sign off the plan". */
  ask?: string | undefined;
  /** Plain 12px text after the label. */
  text?: string | undefined;
  /** The glyph alone (status columns, counters, legends). */
  glyphOnly?: boolean | undefined;
  /** Glyph size: 8 (default) or 7 (counters, pills, group heads). */
  size?: 7 | 8 | undefined;
  title?: string | undefined;
  className?: string | undefined;
}

/** The YOU pill: the only YOU form. */
export function YouPill({ className }: { className?: string | undefined }) {
  return <b className={cx(s.youPill, className)}>YOU</b>;
}

export function StatusMark({
  kind,
  name,
  ask,
  text,
  glyphOnly = false,
  size = 8,
  title,
  className,
}: StatusMarkProps) {
  const cls = cx(s.mark, s[kind], size === 7 && s.s7, className);
  if (glyphOnly) {
    const label = title ?? name ?? MARK_WORD[kind];
    return (
      <span className={cls} role="img" aria-label={label} title={title}>
        <i />
      </span>
    );
  }
  const label = kind === "you" ? <b className={s.youPill}>YOU</b> : name ? <b>{name}</b> : null;
  return (
    <span className={cls} title={title}>
      <i aria-hidden="true" />
      {label}
      {ask ? (
        <span className={s.ask}>
          {kind === "you" || name ? "· " : ""}
          {ask}
        </span>
      ) : null}
      {text ? <span>{text}</span> : null}
    </span>
  );
}
