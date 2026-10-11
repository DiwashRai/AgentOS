// The filter pills in the top bar (DESIGN §6, kit .ao-pills). The pressed pill is `?f=`; counts
// come from model.pills() (the shared counts), so a pill always agrees with its group.
import { StatusMark } from "../../ui";
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
          <em>{p.count}</em>
        </button>
      ))}
    </div>
  );
}
