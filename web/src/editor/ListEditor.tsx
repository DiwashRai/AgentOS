// A list of editable rows: the page renders each row's fields, the kit draws the row chrome (move
// up / down, expand, remove with an inline confirmation, Add) and keeps focus where the user was.
// Rows carry a stable `key` (see list.ts), so a row keeps its state and its issues across moves.
// Controlled and presentational: the page owns the rows; nothing here talks to the server.
import { useEffect, useId, useRef, useState, type ReactNode } from "react";

import { Button, ChevronIcon, cx, IconButton, text } from "../ui";
import { moveRow } from "./list";
import s from "./ListEditor.module.css";

/** What a row's render props know about the row. */
export interface ListRowContext {
  index: number;
  count: number;
  open: boolean;
  readOnly: boolean;
}

export interface ListEditorProps<T extends { key: string }> {
  /** The list's accessible name ("Repos", "Rules"). */
  name: string;
  rows: readonly T[];
  onChange: (rows: T[]) => void;
  /** The row as its controls name it: "Move {label} up", "Remove {label}". */
  label: (row: T, index: number) => string;
  /** The row's own fields. */
  children: (row: T, ctx: ListRowContext) => ReactNode;
  /** The row's details, shown while it is expanded; given, each row gets an expand toggle. */
  details?: ((row: T, ctx: ListRowContext) => ReactNode) | undefined;
  /** A new row with a fresh key; without it there is no Add. */
  create?: (() => T) | undefined;
  /** The Add button's text. */
  addLabel?: string | undefined;
  /** Order matters: an ordered list with row numbers and Move up / down. */
  ordered?: boolean | undefined;
  /** A blank row is removed without asking. */
  isBlank?: ((row: T) => boolean) | undefined;
  /** What the removal confirmation says (e.g. how many open tickets use the row). */
  confirmText?: ((row: T, label: string) => ReactNode) | undefined;
  /** Remove is off at `min` rows, Add at `max`. */
  min?: number | undefined;
  max?: number | undefined;
  readOnly?: boolean | undefined;
  /** Shown instead of the list when there are no rows. */
  empty?: ReactNode;
  /** List-level problems (e.g. the server's issue on the list itself), shown under it. */
  issue?: string | readonly string[] | undefined;
  className?: string | undefined;
}

type FocusTarget = "field" | "up" | "down" | "remove";

/** Where focus goes once the rows the user just changed are rendered. `key: null` = Add. */
interface FocusRequest {
  key: string | null;
  index: number;
  target: FocusTarget;
}

export function ListEditor<T extends { key: string }>(props: ListEditorProps<T>) {
  const { rows, onChange, label, details, create, isBlank } = props;
  const readOnly = props.readOnly ?? false;
  const ordered = props.ordered ?? false;
  const min = props.min ?? 0;
  const max = props.max ?? Number.POSITIVE_INFINITY;

  const prefix = useId();
  const [open, setOpen] = useState<ReadonlySet<string>>(() => new Set());
  const [confirming, setConfirming] = useState<string | null>(null);
  const pending = useRef<FocusRequest | null>(null);
  const addRef = useRef<HTMLButtonElement>(null);
  const confirmRef = useRef<HTMLButtonElement>(null);

  const idOf = (key: string, part: FocusTarget | "details" | "confirm") =>
    `${prefix}-${key}-${part}`;

  // A move or remove re-renders the rows (a moved row's DOM node is re-inserted, which drops its
  // focus); once the page has applied the change, put focus back where the user is working.
  useEffect(() => {
    const req = pending.current;
    if (req === null) return;
    if (req.key === null) {
      pending.current = null;
      addRef.current?.focus();
      return;
    }
    if (rows[req.index]?.key !== req.key) return;
    pending.current = null;
    focusRow(req.key, req.target, idOf);
  });

  useEffect(() => {
    if (confirming !== null) confirmRef.current?.focus();
  }, [confirming]);

  const add = () => {
    if (!create) return;
    const row = create();
    pending.current = { key: row.key, index: rows.length, target: "field" };
    if (details) setOpen((o) => new Set(o).add(row.key));
    onChange([...rows, row]);
  };

  const move = (index: number, delta: -1 | 1) => {
    const row = rows[index];
    if (!row) return;
    pending.current = { key: row.key, index: index + delta, target: delta < 0 ? "up" : "down" };
    onChange(moveRow(rows, index, delta));
  };

  const remove = (index: number) => {
    const next = rows.filter((_, i) => i !== index);
    const at = Math.min(index, next.length - 1);
    const neighbour = next[at];
    pending.current = neighbour
      ? { key: neighbour.key, index: at, target: "remove" }
      : { key: null, index: 0, target: "remove" };
    setConfirming(null);
    onChange(next);
  };

  const cancel = (key: string) => {
    setConfirming(null);
    document.getElementById(idOf(key, "remove"))?.focus();
  };

  const issues = props.issue === undefined ? [] : [props.issue].flat();
  const atMax = rows.length >= max;
  const atMin = rows.length <= min;
  const List = ordered ? "ol" : "ul";

  return (
    <div className={cx(s.editor, props.className)}>
      {rows.length === 0 && props.empty !== undefined ? (
        <p className={cx(text.meta, s.empty)}>{props.empty}</p>
      ) : (
        <List className={s.list} aria-label={props.name}>
          {rows.map((row, index) => {
            const name = label(row, index);
            const isOpen = details !== undefined && open.has(row.key);
            const ctx: ListRowContext = { index, count: rows.length, open: isOpen, readOnly };
            const asking = confirming === row.key;
            return (
              <li key={row.key} className={cx(s.row, isOpen && s.isOpen)}>
                <div className={s.line}>
                  {ordered ? (
                    <span className={s.lead}>
                      {readOnly ? null : (
                        <span className={s.move}>
                          <IconButton
                            id={idOf(row.key, "up")}
                            bare
                            className={s.up}
                            label={`Move ${name} up`}
                            disabled={index === 0}
                            onClick={() => {
                              move(index, -1);
                            }}
                          >
                            <ChevronIcon />
                          </IconButton>
                          <IconButton
                            id={idOf(row.key, "down")}
                            bare
                            label={`Move ${name} down`}
                            disabled={index === rows.length - 1}
                            onClick={() => {
                              move(index, 1);
                            }}
                          >
                            <ChevronIcon />
                          </IconButton>
                        </span>
                      )}
                      <span className={s.num} aria-hidden="true">
                        {String(index + 1).padStart(2, "0")}
                      </span>
                    </span>
                  ) : null}

                  <div id={idOf(row.key, "field")} className={s.fields}>
                    {props.children(row, ctx)}
                  </div>

                  <span className={s.tail}>
                    {details ? (
                      <IconButton
                        bare
                        className={s.toggle}
                        label={`${name} details`}
                        aria-expanded={isOpen}
                        aria-controls={isOpen ? idOf(row.key, "details") : undefined}
                        onClick={() => {
                          setOpen((o) => {
                            const next = new Set(o);
                            if (!next.delete(row.key)) next.add(row.key);
                            return next;
                          });
                        }}
                      >
                        <ChevronIcon />
                      </IconButton>
                    ) : null}
                    {readOnly ? null : (
                      <Button
                        id={idOf(row.key, "remove")}
                        size="sm"
                        variant="quiet"
                        aria-label={`Remove ${name}`}
                        aria-expanded={asking}
                        aria-controls={asking ? idOf(row.key, "confirm") : undefined}
                        disabled={atMin}
                        title={atMin ? `At least ${String(min)}` : undefined}
                        onClick={() => {
                          if (isBlank?.(row)) remove(index);
                          else setConfirming(asking ? null : row.key);
                        }}
                      >
                        Remove
                      </Button>
                    )}
                  </span>
                </div>

                {isOpen ? (
                  <div id={idOf(row.key, "details")} className={s.details}>
                    {details(row, ctx)}
                  </div>
                ) : null}

                {asking && !readOnly ? (
                  <div
                    id={idOf(row.key, "confirm")}
                    className={s.confirm}
                    role="group"
                    aria-label={`Remove ${name}?`}
                  >
                    <span className={text.meta}>
                      {props.confirmText?.(row, name) ??
                        `Remove ${name}? It is gone once you save.`}
                    </span>
                    <Button
                      ref={confirmRef}
                      size="sm"
                      variant="danger"
                      onKeyDown={(e) => {
                        if (e.key === "Escape") cancel(row.key);
                      }}
                      onClick={() => {
                        remove(index);
                      }}
                    >
                      Remove
                    </Button>
                    <Button
                      size="sm"
                      variant="quiet"
                      onKeyDown={(e) => {
                        if (e.key === "Escape") cancel(row.key);
                      }}
                      onClick={() => {
                        cancel(row.key);
                      }}
                    >
                      Cancel
                    </Button>
                  </div>
                ) : null}
              </li>
            );
          })}
        </List>
      )}

      {issues.map((msg, i) => (
        <p key={`${String(i)}:${msg}`} className={text.error} role="alert">
          {msg}
        </p>
      ))}

      {create && !readOnly ? (
        <div className={s.adds}>
          <Button
            ref={addRef}
            size="sm"
            disabled={atMax}
            title={atMax ? `At most ${String(max)}` : undefined}
            onClick={add}
          >
            {props.addLabel ?? "Add"}
          </Button>
        </div>
      ) : null}
    </div>
  );
}

/** Focus a row's control: its first field, a move arrow (the other one if this end is reached),
 *  or its Remove (its first field if Remove is off). */
function focusRow(
  key: string,
  target: FocusTarget,
  idOf: (key: string, part: FocusTarget) => string,
) {
  const byId = (part: FocusTarget) => document.getElementById(idOf(key, part));
  const usable = (el: HTMLElement | null) =>
    el !== null && !(el instanceof HTMLButtonElement && el.disabled) ? el : null;
  const firstField = () =>
    byId("field")?.querySelector<HTMLElement>(
      "input:not([type=hidden]):not([disabled]), textarea:not([disabled]), select:not([disabled])",
    ) ?? null;

  let el: HTMLElement | null = null;
  if (target === "up") el = usable(byId("up")) ?? usable(byId("down"));
  else if (target === "down") el = usable(byId("down")) ?? usable(byId("up"));
  else if (target === "remove") el = usable(byId("remove")) ?? firstField();
  else el = firstField();
  el?.focus();
}

// ------------------------------------------------------------------ live preview

/** A preview run, as the row should show it right now. */
export interface Preview<R> {
  /** Run the preview on the current input (nothing without one). */
  run: () => void;
  /** The result for the current input, if a run of exactly this input finished. */
  result: R | undefined;
  /** The failure for the current input, if its run failed. */
  error: unknown;
  /** A run of the current input is in flight. */
  pending: boolean;
}

type Ran<I, R> =
  | { input: I; status: "pending" }
  | { input: I; status: "done"; result: R }
  | { input: I; status: "failed"; error: unknown };

function sameJson(a: unknown, b: unknown): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

/**
 * A live preview ("Test query", "Try it") for one input. Its result and error show only while the
 * input still equals what ran, so an edit hides them at once and undoing the edit brings them
 * back. Only the latest run counts; an older one finishing late is dropped.
 */
export function usePreview<I, R>(
  run: (input: I) => Promise<R>,
  input: I | undefined,
  same: (a: I, b: I) => boolean = sameJson,
): Preview<R> {
  const [last, setLast] = useState<Ran<I, R> | null>(null);
  const seq = useRef(0);

  const start = () => {
    if (input === undefined) return;
    seq.current += 1;
    const mine = seq.current;
    const ran = input;
    setLast({ input: ran, status: "pending" });
    const settled = new Promise<R>((resolve) => {
      resolve(run(ran));
    });
    settled.then(
      (result) => {
        if (seq.current === mine) setLast({ input: ran, status: "done", result });
      },
      (error: unknown) => {
        if (seq.current === mine) setLast({ input: ran, status: "failed", error });
      },
    );
  };

  const current = last !== null && input !== undefined && same(last.input, input) ? last : null;
  return {
    run: start,
    result: current?.status === "done" ? current.result : undefined,
    error: current?.status === "failed" ? current.error : undefined,
    pending: current?.status === "pending",
  };
}
