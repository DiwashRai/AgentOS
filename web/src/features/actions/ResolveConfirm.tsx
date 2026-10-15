// The inline confirm for Complete / Abandon: a quiet strip under the action buttons with an
// optional short note. Enter confirms (Shift+Enter is a new line), Esc cancels. Abandon confirms in
// the blocked tone, Complete in ink. Presentational: the caller owns the mutation (useTicketAction).
import { useState, type KeyboardEvent } from "react";

import type { Outcome } from "../../domain/outcome";
import { words } from "../../domain/words";
import { fieldStyles as f } from "../../editor";
import { Button, cx, Inline, Kbd, StatusLine, StatusMark, text } from "../../ui";
import s from "./ResolveConfirm.module.css";

/** The API's limit on a resolution note. */
export const NOTE_MAX = 2000;

export interface ResolveConfirmProps {
  outcome: Outcome;
  pending: boolean;
  /** A failed resolve, already worded ("A run is already active"). */
  error: string | null;
  onSubmit: (note: string) => void;
  onCancel: () => void;
  className?: string | undefined;
}

const COPY: Record<Outcome, { head: string; verb: string; busy: string }> = {
  completed: { head: "Close as complete", verb: words.complete, busy: "Completing…" },
  abandoned: { head: "Close as abandoned", verb: words.abandon, busy: "Abandoning…" },
};

export function ResolveConfirm({
  outcome,
  pending,
  error,
  onSubmit,
  onCancel,
  className,
}: ResolveConfirmProps) {
  const [note, setNote] = useState("");
  const copy = COPY[outcome];
  const abandon = outcome === "abandoned";
  const submit = () => {
    if (!pending) onSubmit(note);
  };
  /** Esc cancels from any of the confirm's controls; Enter confirms from the note. */
  const onKeyDown = (e: KeyboardEvent<HTMLElement>) => {
    if (e.key === "Escape") {
      // Don't let Esc also close the peek or leave the page.
      e.stopPropagation();
      onCancel();
    } else if (
      e.key === "Enter" &&
      !e.shiftKey &&
      !e.nativeEvent.isComposing &&
      e.currentTarget instanceof HTMLTextAreaElement
    ) {
      e.preventDefault();
      submit();
    }
  };
  return (
    // A form, so a row's "click anywhere opens the peek" treats it as an inner control.
    <form
      className={cx(s.confirm, abandon ? s.abandon : s.complete, className)}
      aria-label={copy.head}
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
    >
      <StatusMark kind={abandon ? "abandoned" : "done"} name={copy.head} />
      <textarea
        className={cx(f.textarea, s.note)}
        aria-label="Note (optional)"
        placeholder={words.resolveNote}
        rows={2}
        maxLength={NOTE_MAX}
        value={note}
        // eslint-disable-next-line jsx-a11y/no-autofocus -- opened by an explicit Complete/Abandon click
        autoFocus
        onKeyDown={onKeyDown}
        onChange={(e) => {
          setNote(e.target.value);
        }}
      />
      {error ? <StatusLine error>{error}</StatusLine> : null}
      <Inline gap={3} justify="end">
        <span className={cx(text.meta, s.keys)}>
          <Kbd>Enter</Kbd> {copy.verb.toLowerCase()} · <Kbd>Esc</Kbd> cancel
        </span>
        <Button size="sm" variant="quiet" onClick={onCancel} onKeyDown={onKeyDown}>
          {words.cancel}
        </Button>
        <Button
          size="sm"
          type="submit"
          variant={abandon ? "danger" : "ink"}
          disabled={pending}
          onKeyDown={onKeyDown}
        >
          {pending ? copy.busy : copy.verb}
        </Button>
      </Inline>
    </form>
  );
}
