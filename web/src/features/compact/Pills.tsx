// The filter pills in the top bar (refresh mock §4.13 `.filter-chips`, HANDOFF §3.9): Everything ·
// Needs you · Blocked · Running · Queued · Waiting on others · Inbox; zero counts stay, in --ink-3.
// The pressed pill is `?f=`; counts come from model.pills() (the shared counts), so a pill always
// agrees with its group. The track scrolls sideways when the top bar runs out of room.
import { cx, StatusMark } from "../../ui";
import type { Filter, Pill } from "./model";
import s from "./Compact.module.css";

export function Pills({
  items,
  active,
  onPick,
}: {
  items: Pill[];
  active: Filter;
  onPick: (filter: Filter) => void;
}) {
  return (
    <div className={s.pills} role="group" aria-label="Filter">
      {items.map((p) => (
        <button
          key={p.filter}
          type="button"
          className={cx(p.filter === "needs_you" && s.pillYou)}
          aria-pressed={p.filter === active}
          onClick={() => {
            onPick(p.filter);
          }}
        >
          {p.mark && (
            <span className={s.pillMark} aria-hidden="true">
              <StatusMark kind={p.mark} glyphOnly size={7} />
            </span>
          )}
          {p.label}
          <em className={cx(p.count === 0 && s.isZero)}>{p.count}</em>
        </button>
      ))}
    </div>
  );
}
