// New ticket (not designed: built in the same quiet language with kit controls). A dialog over any
// view while `?new=1` is in the URL; mounted once in the Shell. Closing removes `new`; creating
// navigates to the new ticket. It never dispatches.
import { useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router";

import type { Schemas } from "../../api/client";
import { useConfig, useCreateTicket, useMe, usePeople } from "../../api/queries";
import { words } from "../../domain/words";
import { Button, CloseIcon, IconButton, cx } from "../../ui";
import { Field, Select, fieldStyles as f } from "../pipeline/fields";
import {
  PRIORITIES,
  TITLE_MAX,
  NO_REPO,
  TYPES,
  branchPlaceholder,
  hasErrors,
  initialForm,
  repoOptions,
  toCreateBody,
  trapIndex,
  validate,
  type Defaults,
  type NewTicketForm,
} from "./form";
import s from "./NewTicket.module.css";

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

function TicketForm({
  defaults,
  repos,
  people,
  onCreated,
}: {
  defaults: Defaults;
  repos: readonly Schemas["RepoInfo"][];
  people: readonly Schemas["Person"][];
  onCreated: (key: string) => void;
}) {
  const create = useCreateTicket();
  const [form, setForm] = useState<NewTicketForm>(() => initialForm(defaults));
  const [touched, setTouched] = useState(false);
  const errors = touched ? validate(form) : {};
  const set = <K extends keyof NewTicketForm>(k: K, v: NewTicketForm[K]) => {
    setForm((prev) => ({ ...prev, [k]: v }));
  };

  return (
    <form
      className={s.form}
      noValidate
      onSubmit={(e) => {
        e.preventDefault();
        setTouched(true);
        if (hasErrors(validate(form))) {
          // Send focus to the first field with a problem (the title, or the base branch).
          const errs = validate(form);
          e.currentTarget
            .querySelector<HTMLElement>(errs.title ? "[data-autofocus]" : "[data-branch]")
            ?.focus();
          return;
        }
        create.mutate(toCreateBody(form), {
          onSuccess: (t) => {
            onCreated(t.key);
          },
        });
      }}
    >
      <Field label="Title" error={errors.title}>
        {({ id, describedBy, invalid }) => (
          <input
            id={id}
            className={f.input}
            value={form.title}
            maxLength={TITLE_MAX + 50}
            aria-describedby={describedBy}
            aria-invalid={invalid}
            aria-required="true"
            placeholder="What should change?"
            data-autofocus=""
            onChange={(e) => {
              set("title", e.target.value);
            }}
          />
        )}
      </Field>
      <Field
        label="Description"
        hint="The agent reads this. Plain text; blank lines split paragraphs."
      >
        {({ id, describedBy }) => (
          <textarea
            id={id}
            className={cx(f.textarea, s.desc)}
            rows={6}
            value={form.description}
            aria-describedby={describedBy}
            onChange={(e) => {
              set("description", e.target.value);
            }}
          />
        )}
      </Field>
      <div className={s.row3}>
        <Field label="Type">
          {({ id }) => (
            <Select
              id={id}
              value={form.type}
              options={TYPES}
              onChange={(v) => {
                set("type", v as NewTicketForm["type"]);
              }}
            />
          )}
        </Field>
        <Field label="Priority">
          {({ id }) => (
            <Select
              id={id}
              value={form.priority}
              options={PRIORITIES}
              onChange={(v) => {
                set("priority", v as NewTicketForm["priority"]);
              }}
            />
          )}
        </Field>
        <Field label="Owner">
          {({ id }) => (
            <Select
              id={id}
              value={form.ownerId}
              options={[
                { value: "", label: "Unassigned" },
                ...people.map((p) => ({ value: String(p.id), label: p.display_name })),
              ]}
              onChange={(v) => {
                set("ownerId", v);
              }}
            />
          )}
        </Field>
      </div>
      <div className={s.row2}>
        <Field label="Repo">
          {({ id }) => (
            <Select
              id={id}
              value={form.repo}
              options={repoOptions(repos)}
              onChange={(v) => {
                set("repo", v);
              }}
            />
          )}
        </Field>
        {form.repo === NO_REPO ? null : (
          <Field label="Base branch" error={errors.baseBranch}>
            {({ id, describedBy, invalid }) => (
              <input
                id={id}
                className={cx(f.input, f.mono)}
                value={form.baseBranch}
                placeholder={branchPlaceholder(repos, form.repo)}
                aria-describedby={describedBy}
                aria-invalid={invalid}
                spellCheck={false}
                data-branch=""
                onChange={(e) => {
                  set("baseBranch", e.target.value);
                }}
              />
            )}
          </Field>
        )}
      </div>
      <div className={s.foot}>
        {create.error ? (
          <p className={s.err} role="alert">
            {create.error.message}
          </p>
        ) : (
          <p className={s.note}>It lands in the inbox. Dispatch it from the ticket.</p>
        )}
        <Button variant="ink" type="submit" disabled={create.isPending}>
          {create.isPending ? "Creating…" : "Create ticket"}
        </Button>
      </div>
    </form>
  );
}

function NewTicketDialog({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate();
  const config = useConfig();
  const me = useMe();
  const people = usePeople();
  const panel = useRef<HTMLDivElement>(null);

  // Focus the first field on open; give focus back to the opener on close.
  useEffect(() => {
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const first = panel.current?.querySelector<HTMLElement>("[data-autofocus]");
    (first ?? panel.current)?.focus();
    return () => {
      if (opener?.isConnected) opener.focus();
    };
  }, []);

  const ready = !config.isPending && !me.isPending;
  useEffect(() => {
    if (!ready) return;
    panel.current?.querySelector<HTMLElement>("[data-autofocus]")?.focus();
  }, [ready]);

  // Esc closes and Tab stays inside the panel. Captured on the document, so it wins over any
  // view's own Esc handling while the dialog is up.
  const close = useRef(onClose);
  useEffect(() => {
    close.current = onClose;
  });
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        close.current();
        return;
      }
      if (e.key !== "Tab" || !panel.current) return;
      const items = [...panel.current.querySelectorAll<HTMLElement>(FOCUSABLE)];
      const active = document.activeElement;
      const current = items.findIndex((el) => el === active);
      e.preventDefault();
      items[trapIndex(current, items.length, e.shiftKey)]?.focus();
    };
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("keydown", onKey, true);
    };
  }, []);

  return (
    <div className={s.layer}>
      <div className={s.scrim} aria-hidden="true" onClick={onClose} />
      <div
        ref={panel}
        className={s.panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby="new-ticket-title"
        tabIndex={-1}
      >
        <div className={s.head}>
          <h2 id="new-ticket-title">{words.newTicket}</h2>
          <IconButton label="Close" bare onClick={onClose}>
            <CloseIcon />
          </IconButton>
        </div>
        {ready ? (
          <TicketForm
            defaults={{
              repo: config.data?.default_repo,
              meId: me.data?.id,
            }}
            repos={config.data?.repos ?? []}
            people={people.data ?? (me.data ? [me.data] : [])}
            onCreated={(key) => {
              void navigate(`/t/${encodeURIComponent(key)}`);
            }}
          />
        ) : (
          <p className={s.note}>Loading</p>
        )}
      </div>
    </div>
  );
}

export function NewTicketHost() {
  const [params, setParams] = useSearchParams();
  if (params.get("new") !== "1") return null;
  return (
    <NewTicketDialog
      onClose={() => {
        setParams((p) => {
          p.delete("new");
          return p;
        });
      }}
    />
  );
}
