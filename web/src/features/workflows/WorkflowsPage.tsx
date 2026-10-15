// Settings › Workflows (request 23B): the named pipelines a new ticket copies from. A list, and
// the open one (`?w=ID`, or `?w=new`) in an editor below it: name, description and the shared
// PipelineEditor, saved with useDraft + SaveBar; Make default and Delete. Admins edit; everyone
// else reads. Renders a content column; the route wraps it in the shell's TopBar + Surface.
import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router";

import type { Schemas } from "../../api/client";
import {
  useConfig,
  useCreateWorkflow,
  useDeleteWorkflow,
  useMe,
  useSaveWorkflow,
  useSetDefaultWorkflow,
  useWorkflows,
} from "../../api/queries";
import {
  Field,
  fieldStyles as f,
  fixFirst,
  pageStyles as pages,
  SaveBar,
  useDraft,
} from "../../editor";
import { Button, StatusLine, Tag, ZoneHead } from "../../ui";
import { withoutParam, withParam } from "../peek/params";
import { configDefaults, issueCount, pipelineIssues } from "../pipeline/model";
import { PipelineEditor } from "../pipeline/PipelineEditor";
import {
  createBody,
  deleteBlock,
  deleteConfirmText,
  DESCRIPTION_MAX,
  NAME_MAX,
  NEW,
  newWorkflowForm,
  parseSelection,
  sameWorkflowForm,
  sortWorkflows,
  stageCount,
  updateBody,
  validateWorkflowForm,
  W_PARAM,
  workflowErrorText,
  workflowForm,
  workflowMeta,
  type Workflow,
  type WorkflowForm,
} from "./model";
import s from "./Workflows.module.css";

const LEAVE = "Leave without saving your changes?";

export function WorkflowsPage() {
  const me = useMe();
  const config = useConfig();
  const workflows = useWorkflows();
  const [params, setParams] = useSearchParams();
  const isAdmin = me.data?.role === "admin";
  // The open editor's unsaved state: a `?w=` change keeps the page, so the router never asks.
  const dirty = useRef(false);

  if (workflows.isPending || config.isPending || me.isPending) {
    return <StatusLine>Loading workflows</StatusLine>;
  }
  if (workflows.isError || config.isError) {
    return (
      <StatusLine
        error
      >{`Can't load workflows · ${(workflows.error ?? config.error)?.message ?? ""}`}</StatusLine>
    );
  }

  const list = sortWorkflows(workflows.data);
  const selection = parseSelection(params.get(W_PARAM));
  const open = typeof selection === "number" ? list.find((w) => w.id === selection) : undefined;

  const select = (next: number | typeof NEW | null) => {
    if (dirty.current && !window.confirm(LEAVE)) return;
    dirty.current = false;
    setParams((p) =>
      next === null ? withoutParam(p, W_PARAM) : withParam(p, W_PARAM, String(next)),
    );
  };

  return (
    <div className={pages.page}>
      <p className={pages.banner} role="note">
        <span className={pages.k}>{isAdmin ? "Workflows" : "Read-only"}</span>
        {isAdmin
          ? "A new ticket copies its workflow's pipeline. Editing a workflow never changes existing tickets."
          : "Only admins can change workflows. A new ticket copies its workflow's pipeline."}
      </p>

      <section className={s.editor} aria-labelledby="workflows-head">
        <ZoneHead
          id="workflows-head"
          title="Workflows"
          count={list.length}
          right={
            isAdmin ? (
              <Button
                size="sm"
                variant="outline"
                onClick={() => {
                  select(NEW);
                }}
              >
                New workflow
              </Button>
            ) : undefined
          }
        />
        <ul className={s.list}>
          {list.map((w) => (
            <li key={w.id}>
              <button
                type="button"
                className={s.row}
                aria-current={w.id === selection ? "true" : undefined}
                onClick={() => {
                  select(w.id === selection ? null : w.id);
                }}
              >
                <span className={s.head}>
                  <span className={s.name}>{w.name}</span>
                  {w.default ? <Tag>Default</Tag> : null}
                </span>
                {w.description ? <span className={s.desc}>{w.description}</span> : null}
                <span className={s.meta}>{workflowMeta(w)}</span>
              </button>
            </li>
          ))}
        </ul>
      </section>

      {selection === NEW && isAdmin ? (
        <WorkflowEditor
          key="new"
          workflow={null}
          all={list}
          isAdmin
          config={config.data}
          dirtyRef={dirty}
          onReload={() => {
            void workflows.refetch();
          }}
          onCreated={(id) => {
            dirty.current = false;
            setParams((p) => withParam(p, W_PARAM, String(id)));
          }}
          onDeleted={() => undefined}
        />
      ) : open ? (
        <WorkflowEditor
          key={open.id}
          workflow={open}
          all={list}
          isAdmin={isAdmin}
          config={config.data}
          dirtyRef={dirty}
          onReload={() => {
            void workflows.refetch();
          }}
          onCreated={() => undefined}
          onDeleted={() => {
            dirty.current = false;
            setParams((p) => withoutParam(p, W_PARAM));
          }}
        />
      ) : selection !== null ? (
        <StatusLine>
          {selection === NEW ? "Only admins can add workflows." : "That workflow no longer exists."}
        </StatusLine>
      ) : null}
    </div>
  );
}

interface EditorProps {
  /** null = a new workflow (admin only). */
  workflow: Workflow | null;
  all: readonly Workflow[];
  isAdmin: boolean;
  config: Schemas["AppConfig"];
  dirtyRef: { current: boolean };
  /** Refetch the list (after a 409 `stale_version` or a 404). */
  onReload: () => void;
  onCreated: (id: number) => void;
  onDeleted: () => void;
}

function WorkflowEditor({
  workflow,
  all,
  isAdmin,
  config,
  dirtyRef,
  onReload,
  onCreated,
  onDeleted,
}: EditorProps) {
  const create = useCreateWorkflow();
  const save = useSaveWorkflow(workflow?.id ?? 0);
  const remove = useDeleteWorkflow();
  const setDefault = useSetDefaultWorkflow();
  // A new one starts from a copy of the default's pipeline, fixed when the editor opens.
  const [start] = useState(() => (workflow ? undefined : newWorkflowForm(all)));
  const saved = useMemo(() => (workflow ? workflowForm(workflow) : start), [workflow, start]);
  const draft = useDraft<WorkflowForm>(saved, workflow?.version ?? 0, sameWorkflowForm);
  useEffect(() => {
    dirtyRef.current = draft.dirty;
  }, [draft.dirty, dirtyRef]);

  if (!saved) return <StatusLine>No workflow to start from.</StatusLine>;
  const value = draft.value ?? saved;
  const { runtimes } = config;
  const busy = create.isPending || save.isPending || remove.isPending || setDefault.isPending;
  const readOnly = !isAdmin || busy;
  const formIssues = validateWorkflowForm(value, all, workflow?.id ?? null);
  const issues =
    issueCount(pipelineIssues(value.pipeline, runtimes)) + Object.keys(formIssues).length;
  const isNew = workflow === null;
  const mutation = isNew ? create : save;
  const problem = mutation.error ?? remove.error;
  const blockDelete = workflow ? deleteBlock(workflow) : undefined;

  const set = (patch: Partial<WorkflowForm>) => {
    draft.edit({ ...value, ...patch });
  };

  const makeDefault = () => {
    if (!workflow) return;
    setDefault.mutate(workflow.id);
  };

  const extra =
    isAdmin && workflow ? (
      <span className={s.inline}>
        {workflow.default ? null : (
          <Button
            size="sm"
            disabled={busy || draft.dirty}
            title={
              draft.dirty
                ? "Save or discard your changes first"
                : "New tickets get this workflow unless they pick another"
            }
            onClick={makeDefault}
          >
            {setDefault.isPending ? "Making default…" : "Make default"}
          </Button>
        )}
        <Button
          size="sm"
          variant="quiet"
          disabled={busy || blockDelete !== undefined}
          title={blockDelete ?? "Delete this workflow"}
          onClick={() => {
            if (!window.confirm(deleteConfirmText(workflow.name))) return;
            remove.mutate(workflow.id, { onSuccess: onDeleted });
          }}
        >
          {remove.isPending ? "Deleting…" : "Delete"}
        </Button>
      </span>
    ) : null;

  return (
    <section className={s.editor} aria-label={isNew ? "New workflow" : `Workflow ${workflow.name}`}>
      <SaveBar
        title={isNew ? "New workflow" : workflow.name}
        meta={
          isNew
            ? `${stageCount(value.pipeline)} · from the default`
            : `${workflowMeta(workflow)}${workflow.default ? " · default" : ""}`
        }
        dirty={draft.dirty}
        readOnly={!isAdmin}
        saveDisabledReason={fixFirst(issues)}
        saving={mutation.isPending}
        problem={problem ? workflowErrorText(problem) : null}
        onDiscard={() => {
          draft.discard();
          mutation.reset();
          remove.reset();
          setDefault.reset();
        }}
        onReload={() => {
          draft.discard();
          mutation.reset();
          remove.reset();
          onReload();
        }}
        onSave={() => {
          setDefault.reset();
          remove.reset();
          if (isNew) {
            create.mutate(createBody(value), {
              onSuccess: (w) => {
                onCreated(w.id);
              },
            });
          } else {
            save.mutate(updateBody(value, draft.baseVersion ?? workflow.version), {
              onSuccess: () => {
                draft.discard();
              },
            });
          }
        }}
        extra={extra}
      />
      {setDefault.error ? (
        <p className={s.problem} role="alert">
          {workflowErrorText(setDefault.error).text}
        </p>
      ) : null}

      <div className={s.sheet}>
        <Field label="Name" error={formIssues.name}>
          {({ id, describedBy, invalid }) => (
            <input
              id={id}
              className={f.input}
              value={value.name}
              maxLength={NAME_MAX + 20}
              readOnly={readOnly}
              placeholder="Research only"
              aria-describedby={describedBy}
              aria-invalid={invalid}
              aria-required="true"
              onChange={(e) => {
                set({ name: e.target.value });
              }}
            />
          )}
        </Field>
        <Field
          label="Description"
          error={formIssues.description}
          hint="One line, shown in the New ticket picker."
        >
          {({ id, describedBy, invalid }) => (
            <input
              id={id}
              className={f.input}
              value={value.description}
              maxLength={DESCRIPTION_MAX + 50}
              readOnly={readOnly}
              placeholder="What this workflow is for"
              aria-describedby={describedBy}
              aria-invalid={invalid}
              onChange={(e) => {
                set({ description: e.target.value });
              }}
            />
          )}
        </Field>
      </div>

      <PipelineEditor
        value={value.pipeline}
        onChange={(pipeline) => {
          set({ pipeline });
        }}
        readOnly={readOnly}
        runtimes={runtimes}
        space={configDefaults(config)}
      />
    </section>
  );
}
