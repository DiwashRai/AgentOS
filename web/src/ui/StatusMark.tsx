import { displayWords } from "../domain/words";
import { cx } from "./cx";
import s from "./StatusMark.module.css";
import { TickBadge } from "./TickBadge";

export type MarkKind = "run" | "wait" | "you" | "blk" | "done" | "abandoned" | "queued" | "idle";

/** Fallback accessible names for a glyph-only mark: the domain's status words. */
export const MARK_WORD: Record<MarkKind, string> = displayWords;

export interface StatusMarkProps {
  kind: MarkKind;
  /** Sentence-case label in the mark's colour ("Builder · run #42", "Sam", "Blocked"). Ignored for
   *  `you`, which always shows the YOU pill. */
  name?: string | undefined;
  /** The ask: after the YOU pill in 600 `--you-text` ("Plan sign-off"); after a name, "· ask". */
  ask?: string | undefined;
  /** Plain 12.5px `--ink-2` text after the label. */
  text?: string | undefined;
  /** The glyph alone (status columns, counters, legends). */
  glyphOnly?: boolean | undefined;
  /** Glyph size: 8 (default) or 7, the small mark of counters, pills and group heads (the refresh's
   *  6px status dot; the Inbox ring is 7px). */
  size?: 7 | 8 | undefined;
  title?: string | undefined;
  className?: string | undefined;
}

/** The YOU pill: the only YOU form, and the only mono caps in the product. */
export function YouPill({ className }: { className?: string | undefined }) {
  return <b className={cx(s.youPill, className)}>YOU</b>;
}

function Glyph({ kind, label }: { kind: MarkKind; label?: string | undefined }) {
  if (kind === "done") return <TickBadge size={12} label={label} className={s.tick} />;
  return label ? <i role="img" aria-label={label} /> : <i aria-hidden="true" />;
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
      <span className={cls} title={title}>
        <Glyph kind={kind} label={label} />
      </span>
    );
  }
  const label = kind === "you" ? <YouPill /> : name ? <b>{name}</b> : null;
  return (
    <span className={cls} title={title}>
      {/* The YOU pill stands alone (refresh: no dot beside it). */}
      {kind === "you" ? null : <Glyph kind={kind} />}
      {label}
      {ask ? (
        <span className={s.ask}>
          {kind !== "you" && name ? "· " : ""}
          {ask}
        </span>
      ) : null}
      {text ? <span>{text}</span> : null}
    </span>
  );
}
