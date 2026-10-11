// Ticket page · Overview tab (DESIGN §7.3): the description (plain paragraphs; markdown later) with
// an edit affordance, the TASKS.md plan (it stands in for the design's acceptance criteria) and
// "The agent reads" context chips.
import { useState } from "react";
import { useLocation, useNavigate } from "react-router";

import type { Schemas } from "../../api/client";
import { useMe, usePatchTicket } from "../../api/queries";
import { words } from "../../domain/words";
import { Button, Chip, LinkButton, StatusMark, cx } from "../../ui";
import { sortFiles } from "../context/files";
import {
  actionErrorText,
  chipName,
  paragraphs,
  planNumber,
  planSummary,
  ticketPath,
} from "./model";
import s from "./Overview.module.css";

type Detail = Schemas["TicketDetail"];

function Description({ t, readOnly }: { t: Detail; readOnly: boolean }) {
  const patch = usePatchTicket(t.key);
  const [draft, setDraft] = useState<string | null>(null);
  // The header's lead is the first paragraph; the body continues from the second.
  const paras = paragraphs(t.description);
  const rest = paras.slice(1);

  if (draft !== null) {
    return (
      <form
        className={s.edit}
        onSubmit={(e) => {
          e.preventDefault();
          patch.mutate(
            { description: draft },
            {
              onSuccess: () => {
                setDraft(null);
              },
            },
          );
        }}
      >
        <textarea
          className={s.textarea}
          aria-label="Description"
          value={draft}
          rows={Math.min(24, Math.max(6, draft.split("\n").length + 1))}
          // eslint-disable-next-line jsx-a11y/no-autofocus -- opened by an explicit Edit click
          autoFocus
          onChange={(e) => {
            setDraft(e.target.value);
          }}
          onKeyDown={(e) => {
            if (e.key === "Escape") {
              e.stopPropagation();
              setDraft(null);
              patch.reset();
            }
          }}
        />
        <div className={s.editBar}>
          {patch.error ? (
            <span className={s.err} role="alert">
              {actionErrorText(patch.error)}
            </span>
          ) : null}
          <Button
            size="sm"
            variant="quiet"
            onClick={() => {
              setDraft(null);
              patch.reset();
            }}
          >
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
        </div>
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
        <p className={s.empty}>No description yet.</p>
      ) : null}
      {readOnly ? null : (
        <LinkButton
          className={s.editLink}
          onClick={() => {
            setDraft(t.description);
          }}
        >
          {paras.length > 0 ? "Edit description" : "Add a description"}
        </LinkButton>
      )}
    </div>
  );
}

function Plan({ plan }: { plan: Detail["plan"] }) {
  return (
    <section className={s.sec} aria-labelledby="ticket-plan">
      <h2 id="ticket-plan" className={s.heading}>
        Plan
        {plan && plan.length > 0 ? <span className={s.k}>{planSummary(plan)}</span> : null}
      </h2>
      {plan && plan.length > 0 ? (
        <ol className={s.plan}>
          {plan.map((p, i) => (
            <li key={i} className={cx(p.done && s.isDone)}>
              <span className={s.k}>{planNumber(i)}</span>
              <span>{p.text}</span>
              <span className={s.st}>
                {p.done ? (
                  <StatusMark kind="done" name="Done" />
                ) : (
                  <StatusMark kind="idle" name="Open" />
                )}
              </span>
            </li>
          ))}
        </ol>
      ) : (
        <p className={s.empty}>No plan yet.</p>
      )}
    </section>
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
      <h2 id="ticket-reads" className={s.heading}>
        {words.agentReads}
      </h2>
      <div className={s.ctx}>
        {sortFiles(files).map((f) => (
          <Chip
            key={f.path}
            title={f.path}
            onClick={() => {
              toFile(f.path);
            }}
          >
            {chipName(f.path)}
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
      </div>
    </section>
  );
}

export function Overview({ t, files }: { t: Detail; files: readonly Schemas["FileInfo"][] }) {
  const me = useMe();
  const readOnly = me.data?.role === "viewer";
  return (
    <div className={s.read}>
      <Description key={t.key} t={t} readOnly={readOnly} />
      <Plan plan={t.plan} />
      <AgentReads t={t} files={files} />
    </div>
  );
}
