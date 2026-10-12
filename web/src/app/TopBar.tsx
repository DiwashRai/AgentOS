// The top bar (DESIGN §4, kit.js Kit.topbar): h1 · context · counters · view middle · gap ·
// search · one button. Counters read the shared counts; views pass only which ones to show. The
// context leads with the Space name (the design's workspace slot); the tab title follows the view.
import { type ReactNode, useEffect, useRef } from "react";
import { useNavigate, useSearchParams } from "react-router";

import { useConfig } from "../api/queries";
import { COUNTERS, type CountKey } from "../domain/counts";
import { jumpTarget } from "../domain/search";
import type { StatusKind } from "../domain/status";
import { words } from "../domain/words";
import { Button, cx, Kbd, type MarkKind, StatusMark } from "../ui";
import { SearchIcon } from "./icons";
import { isShortcut } from "./keys";
import s from "./TopBar.module.css";
import { useBoard } from "./useBoard";
import { useSearchQuery } from "./useSearchQuery";

export type CounterSlot = CountKey | "|";

/** A count's status kind → its mark (the kinds share names with the marks). */
const MARK_OF: Record<StatusKind, MarkKind> = {
  you: "you",
  wait: "wait",
  blk: "blk",
  run: "run",
  queued: "queued",
  done: "done",
  idle: "idle",
};

export interface TopBarProps {
  title: string;
  context?: string | undefined;
  /** Count keys to show, in order; "|" draws a separator. */
  counters?: readonly CounterSlot[] | undefined;
  /** View-specific controls after the counters (filter pills, queue button, crumb). */
  middle?: ReactNode;
  /** Search placeholder, or false for no search. */
  search?: string | false | undefined;
  /** Replaces the "New ticket" button (e.g. the ticket page's close button). */
  action?: ReactNode;
}

/** Counters (refresh mock §3 `.counter`): the number carries the weight (15/600) and a zero recedes
 *  (13/500 `--ink-3`); a non-zero "need you" is the one tinted pill. */
function Counters({ slots }: { slots: readonly CounterSlot[] }) {
  const { counts } = useBoard();
  return (
    <div className={s.counters} aria-live="polite">
      {slots.map((slot, i) => {
        if (slot === "|") return <span key={`sep-${String(i)}`} className={s.sep} />;
        const { mark, label } = COUNTERS[slot];
        const n = counts[slot];
        return (
          <span
            key={slot}
            className={cx(s.counter, n === 0 && s.isZero, n > 0 && mark === "you" && s.isYou)}
          >
            {mark && (
              <span className={s.mark} aria-hidden="true">
                <StatusMark kind={MARK_OF[mark]} glyphOnly size={7} />
              </span>
            )}
            <b>{n}</b> {label}
          </span>
        );
      })}
    </div>
  );
}

/**
 * The search box: `?q=` filters the lists that read it (My queue, Compact). Enter jumps: a
 * key-shaped query, or the one ticket that matches (src/domain/search.ts). Esc clears, then blurs.
 */
function Search({ placeholder }: { placeholder: string }) {
  const inputRef = useRef<HTMLInputElement>(null);
  const { q, setQ } = useSearchQuery();
  const { tickets } = useBoard();
  const navigate = useNavigate();
  // "/" focuses search from anywhere, but never while typing in a field.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "/" || !isShortcut(e)) return;
      e.preventDefault();
      inputRef.current?.focus();
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
    };
  }, []);
  return (
    <label className={s.search}>
      <SearchIcon />
      <input
        ref={inputRef}
        type="search"
        placeholder={placeholder}
        aria-label={placeholder}
        value={q}
        onChange={(e) => {
          setQ(e.currentTarget.value);
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            const key = jumpTarget(q, tickets);
            if (key === null) return;
            e.preventDefault();
            void navigate(`/t/${encodeURIComponent(key)}`);
          } else if (e.key === "Escape") {
            // A non-empty search clears first; that Esc is ours, not the peek's or a row's.
            if (q !== "") {
              e.preventDefault();
              e.stopPropagation();
              setQ("");
            } else e.currentTarget.blur();
          }
        }}
      />
      <Kbd className={s.kbd}>/</Kbd>
    </label>
  );
}

function NewTicketButton() {
  const [, setParams] = useSearchParams();
  return (
    <Button
      onClick={() => {
        setParams((p) => {
          p.set("new", "1");
          return p;
        });
      }}
    >
      {words.newTicket}
    </Button>
  );
}

export function TopBar(props: TopBarProps) {
  const search = props.search ?? "Search or jump to a ticket";
  const space = useConfig().data?.space_name ?? "";
  const context = [space, props.context ?? ""].filter(Boolean).join(" · ");
  useEffect(() => {
    document.title = [props.title, space || "AgentOS"].join(" · ");
  }, [props.title, space]);
  return (
    <header className={s.top}>
      <h1>{props.title}</h1>
      {context && <span className={s.ctx}>{context}</span>}
      {props.counters && props.counters.length > 0 && <Counters slots={props.counters} />}
      {props.middle}
      <span className={s.grow} />
      {search !== false && <Search placeholder={search} />}
      {props.action ?? <NewTicketButton />}
    </header>
  );
}
