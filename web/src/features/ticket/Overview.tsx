// Ticket page · Overview tab (DESIGN §7.3): the description (plain paragraphs; markdown later) with
// an edit affordance, the TASKS.md plan (it stands in for the design's acceptance criteria) and
// "The agent reads" context chips.
import { useLocation, useNavigate } from "react-router";

import type { Schemas } from "../../api/client";
import { useMe, usePatchTicket } from "../../api/queries";
import { firstParagraph, paragraphs, planView } from "../../domain/plan";
import { words } from "../../domain/words";
import { fieldStyles as f } from "../../editor";
import {
  Button,
  Checklist,
  Chip,
  EmptyState,
  Inline,
  LinkButton,
  Spacer,
  cx,
  text,
} from "../../ui";
import { sortFiles } from "../context/files";
import { actionErrorText, chipName, stripKind, ticketPath } from "./model";
import s from "./Overview.module.css";
import { useInlineEdit } from "./useInlineEdit";

type Detail = Schemas["TicketDetail"];

function Description({ t, readOnly }: { t: Detail; readOnly: boolean }) {
  const patch = usePatchTicket(t.key);
  const edit = useInlineEdit<string, Schemas["TicketPatch"]>(patch);
  const draft = edit.draft;
  // The header shows the lead; the body is every other paragraph.
  const paras = paragraphs(t.description);
  const rest = firstParagraph(t.description).rest;

  if (draft !== null) {
    return (
      <form
        className={s.edit}
        onSubmit={(e) => {
          e.preventDefault();
          edit.save({ description: draft });
        }}
      >
        <textarea
          className={cx(f.textarea, s.text)}
          aria-label="Description"
          value={draft}
          rows={Math.min(24, Math.max(6, draft.split("\n").length + 1))}
          // eslint-disable-next-line jsx-a11y/no-autofocus -- opened by an explicit Edit click
          autoFocus
          onChange={(e) => {
            edit.change(e.target.value);
          }}
          onKeyDown={edit.onKeyDown}
        />
        <Inline gap={4}>
          {patch.error ? (
            <span className={text.error} role="alert">
              {actionErrorText(patch.error)}
            </span>
          ) : null}
          <Spacer />
          <Button size="sm" variant="quiet" onClick={edit.cancel}>
            Cancel
          </Button>
          <Button
            size="sm"
            variant="ink"
            type="submit"
            disabled={patch.isPending || draft === t.description}
          >
            {patch.isPending ? "Saving…" : "Save"}
          </Button>
        </Inline>
      </form>
    );
  }

  return (
    <div className={s.desc}>
      {rest.length > 0 ? (
        <div className={s.body}>
          {rest.map((p, i) => (
            <p key={i}>{p}</p>
          ))}
        </div>
      ) : paras.length === 0 ? (
        <EmptyState form="note">No description yet.</EmptyState>
      ) : null}
      {readOnly ? null : (
        <LinkButton
          className={s.editLink}
          onClick={() => {
            edit.start(t.description);
          }}
        >
          {paras.length > 0 ? "Edit description" : "Add a description"}
        </LinkButton>
      )}
    </div>
  );
}

function AgentReads({ t, files }: { t: Detail; files: readonly Schemas["FileInfo"][] }) {
  const navigate = useNavigate();
  const { search } = useLocation();
  const toFile = (path: string) => {
    const q = new URLSearchParams(search);
    q.set("file", path);
    void navigate(ticketPath(t.key, "context", `?${q.toString()}`));
  };
  return (
    <section className={s.sec} aria-labelledby="ticket-reads">
      <h2 id="ticket-reads" className={cx(text.heading, s.heading)}>
        {words.agentReads}
      </h2>
      <Inline wrap gap={4}>
        {sortFiles(files).map((file) => (
          <Chip
            key={file.path}
            title={file.path}
            onClick={() => {
              toFile(file.path);
            }}
          >
            {chipName(file.path)}
          </Chip>
        ))}
        <Chip
          add
          onClick={() => {
            void navigate(ticketPath(t.key, "context", search));
          }}
        >
          {words.addContext}
        </Chip>
      </Inline>
    </section>
  );
}

export function Overview({ t, files }: { t: Detail; files: readonly Schemas["FileInfo"][] }) {
  const me = useMe();
  const readOnly = me.data?.role === "viewer";
  return (
    <div className={s.read}>
      <Description key={t.key} t={t} readOnly={readOnly} />
      {/* Parked at a gate, the plan already sits under the now band (what's being signed off). */}
      {stripKind(t) === "gate" ? null : (
        <section className={s.sec} aria-labelledby="ticket-plan">
          <Checklist
            plan={planView(t.plan)}
            title="Plan"
            id="ticket-plan"
            empty={<EmptyState form="note">No plan yet.</EmptyState>}
          />
        </section>
      )}
      <AgentReads t={t} files={files} />
    </div>
  );
}
