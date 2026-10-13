// The Pipeline tab's workflow strip (request 23B): where this ticket's pipeline came from ("From
// Standard dev · v3"), "Switch workflow…" for writers (pick one, then confirm; once the ticket has
// run the confirm says what happens to its branch and its rail), and "Save as workflow…" for
// admins (POSTs the ticket's saved pipeline, then links to it in Settings › Workflows).
import { useState } from "react";
import { Link as RouterLink } from "react-router";

import type { Schemas } from "../../api/client";
import { useCreateWorkflow, useSwitchWorkflow, useWorkflows } from "../../api/queries";
import { Button, linkClass } from "../../ui";
import { Field, Select, fieldStyles as f } from "../pipeline/fields";
import {
  createBody,
  DESCRIPTION_MAX,
  NAME_MAX,
  saveAsBlock,
  sortWorkflows,
  switchBlock,
  switchConfirmText,
  switchErrorText,
  validateWorkflowForm,
  workflowErrorText,
  workflowHint,
  workflowHref,
  workflowLine,
  workflowOptions,
  type Workflow,
} from "./model";
import s from "./Workflows.module.css";

export interface WorkflowStripProps {
  ticket: Schemas["TicketDetail"];
  canWrite: boolean;
  isAdmin: boolean;
  /** The Pipeline tab has unsaved edits. */
  dirty: boolean;
}

export function WorkflowStrip({ ticket, canWrite, isAdmin, dirty }: WorkflowStripProps) {
  const workflows = useWorkflows();
  const [open, setOpen] = useState<"switch" | "saveAs" | null>(null);
  const [saved, setSaved] = useState<Workflow | null>(null);
  const line = workflowLine(ticket.workflow);
  const list = sortWorkflows(workflows.data ?? []);
  const switchOff = switchBlock({ active: ticket.active_run_id !== null, dirty });
  const saveAsOff = saveAsBlock(dirty);

  if (line === null && !canWrite && !isAdmin) return null;
  return (
    <>
      <div className={s.strip}>
        {line ? <span className={s.from}>{line}</span> : null}
        <span className={s.spacer} />
        {canWrite && list.length > 0 ? (
          <Button
            size="sm"
            variant="quiet"
            disabled={switchOff !== undefined}
            title={switchOff ?? "Replace this ticket's pipeline with a copy of a workflow"}
            aria-expanded={open === "switch"}
            onClick={() => {
              setOpen(open === "switch" ? null : "switch");
            }}
          >
            Switch workflow…
          </Button>
        ) : null}
        {isAdmin ? (
          <Button
            size="sm"
            variant="quiet"
            disabled={saveAsOff !== undefined}
            title={saveAsOff ?? "Keep this ticket's saved pipeline as a new workflow"}
            aria-expanded={open === "saveAs"}
            onClick={() => {
              setSaved(null);
              setOpen(open === "saveAs" ? null : "saveAs");
            }}
          >
            Save as workflow…
          </Button>
        ) : null}
      </div>

      {open === "switch" && switchOff === undefined ? (
        <SwitchPanel
          ticket={ticket}
          workflows={list}
          onDone={() => {
            setOpen(null);
          }}
        />
      ) : null}
      {open === "saveAs" && saveAsOff === undefined ? (
        <SaveAsPanel
          ticket={ticket}
          workflows={list}
          onCancel={() => {
            setOpen(null);
          }}
          onSaved={(w) => {
            setSaved(w);
            setOpen(null);
          }}
        />
      ) : null}
      {saved ? (
        <p className={s.done} role="status">
          {`Saved as ${saved.name}. `}
          <RouterLink className={linkClass} to={workflowHref(saved.id)}>
            Open in Settings › Workflows
          </RouterLink>
        </p>
      ) : null}
    </>
  );
}

function SwitchPanel({
  ticket,
  workflows,
  onDone,
}: {
  ticket: Schemas["TicketDetail"];
  workflows: readonly Workflow[];
  onDone: () => void;
}) {
  const doSwitch = useSwitchWorkflow(ticket.key);
  // Preselect the first workflow that isn't the one this ticket came from.
  const [pick, setPick] = useState(() =>
    String((workflows.find((w) => w.id !== ticket.workflow?.id) ?? workflows[0])?.id ?? ""),
  );
  const target = workflows.find((w) => String(w.id) === pick);
  if (!target) return null;
  return (
    <form
      className={s.panel}
      aria-label="Switch workflow"
      onSubmit={(e) => {
        e.preventDefault();
        if (doSwitch.isPending) return;
        doSwitch.mutate(
          { workflow_id: target.id, base_version: ticket.pipeline_version },
          { onSuccess: onDone },
        );
      }}
    >
      <p className={s.panelHead}>Switch workflow</p>
      <Field label="Workflow" hint={workflowHint(workflows, pick)}>
        {({ id, describedBy }) => (
          <Select
            id={id}
            value={pick}
            aria-describedby={describedBy}
            options={workflowOptions(workflows)}
            onChange={(v) => {
              setPick(v);
              doSwitch.reset();
            }}
          />
        )}
      </Field>
      <p className={s.text}>{switchConfirmText(target.name, ticket.latest_run !== null)}</p>
      {doSwitch.error ? (
        <p className={s.problem} role="alert">
          {switchErrorText(doSwitch.error, target.name)}
        </p>
      ) : null}
      <div className={s.actions}>
        <Button size="sm" variant="quiet" onClick={onDone}>
          Cancel
        </Button>
        <Button size="sm" variant="ink" type="submit" disabled={doSwitch.isPending}>
          {doSwitch.isPending ? "Switching…" : `Switch to ${target.name}`}
        </Button>
      </div>
    </form>
  );
}

function SaveAsPanel({
  ticket,
  workflows,
  onCancel,
  onSaved,
}: {
  ticket: Schemas["TicketDetail"];
  workflows: readonly Workflow[];
  onCancel: () => void;
  onSaved: (w: Workflow) => void;
}) {
  const create = useCreateWorkflow();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [touched, setTouched] = useState(false);
  const issues = validateWorkflowForm({ name, description }, workflows, null);
  const shown = touched ? issues : {};
  return (
    <form
      className={s.panel}
      aria-label="Save as workflow"
      noValidate
      onSubmit={(e) => {
        e.preventDefault();
        setTouched(true);
        if (Object.keys(issues).length > 0 || create.isPending) return;
        create.mutate(createBody({ name, description, pipeline: ticket.pipeline }), {
          onSuccess: onSaved,
        });
      }}
    >
      <p className={s.panelHead}>Save as workflow</p>
      <p className={s.text}>
        {`A new workflow with this ticket's saved pipeline (${String(ticket.pipeline.stages.length)} stages). New tickets can start from it; this ticket doesn't change.`}
      </p>
      <Field label="Name" error={shown.name}>
        {({ id, describedBy, invalid }) => (
          <input
            id={id}
            className={f.input}
            value={name}
            maxLength={NAME_MAX + 20}
            placeholder="Verification heavy"
            aria-describedby={describedBy}
            aria-invalid={invalid}
            aria-required="true"
            // eslint-disable-next-line jsx-a11y/no-autofocus -- opened by an explicit "Save as workflow…" click
            autoFocus
            onChange={(e) => {
              setName(e.target.value);
              create.reset();
            }}
          />
        )}
      </Field>
      <Field label="Description" error={shown.description} hint="One line, shown in pickers.">
        {({ id, describedBy, invalid }) => (
          <input
            id={id}
            className={f.input}
            value={description}
            maxLength={DESCRIPTION_MAX + 50}
            aria-describedby={describedBy}
            aria-invalid={invalid}
            onChange={(e) => {
              setDescription(e.target.value);
            }}
          />
        )}
      </Field>
      {create.error ? (
        <p className={s.problem} role="alert">
          {workflowErrorText(create.error).text}
        </p>
      ) : null}
      <div className={s.actions}>
        <Button size="sm" variant="quiet" onClick={onCancel}>
          Cancel
        </Button>
        <Button size="sm" variant="ink" type="submit" disabled={create.isPending}>
          {create.isPending ? "Saving…" : "Save workflow"}
        </Button>
      </div>
    </form>
  );
}
