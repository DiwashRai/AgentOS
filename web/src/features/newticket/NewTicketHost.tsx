// New ticket (not designed: built in the same quiet language with kit controls). A dialog over any
// view while `?new=1` is in the URL (a `Dialog`, centred); mounted once in the Shell. Closing
// removes `new`; creating navigates to the new ticket. It never dispatches.
import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router";

import type { Schemas } from "../../api/client";
import {
  useConfig,
  useCreateTicket,
  useImportTicket,
  useMe,
  usePeople,
  useWorkflows,
} from "../../api/queries";
import { words } from "../../domain/words";
import { Field, Select, fieldStyles as f } from "../../editor";
import { Button, CloseIcon, Dialog, DialogTitle, IconButton, cx, text } from "../../ui";
import { withoutParam } from "../peek/params";
import { WorkflowPicker } from "../workflows";
import { defaultWorkflow, workflowIdOf } from "../workflows/model";
import {
  PRIORITIES,
  TITLE_MAX,
  NO_REPO,
  TYPES,
  branchNote,
  branchPlaceholder,
  hasErrors,
  initialForm,
  repoOptions,
  toCreateBody,
  validate,
  type Defaults,
  type NewTicketForm,
} from "./form";
import { importErrorText, offersJiraImport, toImportBody } from "./importForm";
import s from "./NewTicket.module.css";

/** Focuses the dialog's first field when the forms mount (they may load after the dialog opened),
 *  unless focus has already moved on inside the dialog. */
function focusOnMount(el: HTMLInputElement | null) {
  const panel = el?.closest('[role="dialog"]');
  if (el && (document.activeElement === panel || !panel?.contains(document.activeElement)))
    el.focus({ preventScroll: true });
}

/** "Import from Jira": a key or link → the ticket (new or imported before). Its own form, so
 *  Enter in the field imports instead of creating. */
function ImportRow({
  focusFirst,
  workflowId,
  onImported,
}: {
  focusFirst: boolean;
  workflowId: number | null;
  onImported: (key: string) => void;
}) {
  const imp = useImportTicket();
  const [ref, setRef] = useState("");
  const body = toImportBody(ref, workflowId);
  return (
    <form
      className={s.import}
      noValidate
      aria-label="Import from Jira"
      onSubmit={(e) => {
        e.preventDefault();
        if (!body || imp.isPending) return;
        imp.mutate(body, {
          onSuccess: (r) => {
            onImported(r.ticket.key);
          },
        });
      }}
    >
      <label htmlFor="import-ref" className={text.label}>
        Import from Jira
      </label>
      <div className={s.importRow}>
        <input
          id="import-ref"
          className={cx(f.input, f.mono)}
          value={ref}
          placeholder="SCRUM-1 or a Jira link"
          spellCheck={false}
          autoComplete="off"
          aria-describedby={imp.error ? "import-err" : undefined}
          aria-invalid={imp.error ? true : undefined}
          ref={focusFirst ? focusOnMount : undefined}
          onChange={(e) => {
            setRef(e.target.value);
            if (imp.error) imp.reset();
          }}
        />
        <Button type="submit" disabled={!body || imp.isPending}>
          {imp.isPending ? "Importing…" : "Import"}
        </Button>
      </div>
      {imp.error ? (
        <p id="import-err" className={s.err} role="alert">
          {importErrorText(imp.error)}
        </p>
      ) : null}
    </form>
  );
}

function TicketForm({
  defaults,
  repos,
  people,
  focusFirst,
  workflowId,
  onCreated,
}: {
  defaults: Defaults;
  repos: readonly Schemas["RepoInfo"][];
  people: readonly Schemas["Person"][];
  focusFirst: boolean;
  workflowId: number | null;
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
            .querySelector<HTMLElement>(errs.title ? "[data-title]" : "[data-branch]")
            ?.focus();
          return;
        }
        create.mutate(toCreateBody(form, workflowId), {
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
            data-title=""
            ref={focusFirst ? focusOnMount : undefined}
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
          <Field
            label="Base branch"
            error={errors.baseBranch}
            hint={branchNote(repos, form.repo, form.baseBranch)}
          >
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

function NewTicketForms() {
  const navigate = useNavigate();
  const config = useConfig();
  const me = useMe();
  const people = usePeople();
  const workflows = useWorkflows();
  // null = not picked: the default workflow, preselected. If the list fails to load, the picker
  // stays hidden and the server applies the default.
  const [picked, setPicked] = useState<string | null>(null);

  const ready = !config.isPending && !me.isPending && !workflows.isPending;
  const workflowList = workflows.data ?? [];
  const workflowValue = picked ?? String(defaultWorkflow(workflowList)?.id ?? "");
  const workflowId = workflowIdOf(workflowValue);
  const canImport = offersJiraImport(config.data?.sources ?? []);
  const open = (key: string) => {
    void navigate(`/t/${encodeURIComponent(key)}`);
  };

  if (!ready) return <p className={s.note}>Loading</p>;
  return (
    <>
      {workflowList.length > 0 ? (
        <WorkflowPicker
          className={s.workflow}
          workflows={workflowList}
          value={workflowValue}
          onChange={setPicked}
        />
      ) : null}
      {canImport ? (
        <>
          <ImportRow focusFirst workflowId={workflowId} onImported={open} />
          <p className={s.or} aria-hidden="true">
            or create one here
          </p>
        </>
      ) : null}
      <TicketForm
        defaults={{
          repo: config.data?.default_repo,
          meId: me.data?.id,
        }}
        repos={config.data?.repos ?? []}
        people={people.data ?? (me.data ? [me.data] : [])}
        focusFirst={!canImport}
        workflowId={workflowId}
        onCreated={open}
      />
    </>
  );
}

/** The dialog, labelled by its "New ticket" heading. `Dialog` traps Tab, closes on Esc or the
 *  scrim, and returns focus; the first field focuses itself. */
export function NewTicketHost() {
  const [params, setParams] = useSearchParams();
  if (params.get("new") !== "1") return null;

  return (
    <Dialog
      onClose={() => {
        setParams((p) => withoutParam(p, "new"));
      }}
    >
      {(close) => (
        <>
          <div className={s.head}>
            <DialogTitle className={s.title}>{words.newTicket}</DialogTitle>
            <IconButton label="Close" bare onClick={close}>
              <CloseIcon />
            </IconButton>
          </div>
          <NewTicketForms />
        </>
      )}
    </Dialog>
  );
}
