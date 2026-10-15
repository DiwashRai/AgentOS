// The ticket page's Pipeline tab: this ticket's copy of a workflow's pipeline. Toggle stages and
// sign-offs, tweak prompts, Save (PUT pipeline with base_version), then "Run from…" a stage, on the
// dispatch target rule's target (features/actions/target.ts). The workflow strip
// (features/workflows) switches workflow or saves this pipeline as a new one.
import { useState } from "react";

import { useConfig, useDispatch, useMe, useSavePipeline, useTicket } from "../../api/queries";
import {
  describeSaveError,
  fixFirst,
  pageStyles as s,
  SaveBar,
  Select,
  useDraft,
} from "../../editor";
import { Button } from "../../ui";
import { TargetPicker } from "../actions/TargetPicker";
import type { DispatchTargetState } from "../actions/useDispatchTarget";
import { WorkflowStrip } from "../workflows";
import { configDefaults, issueCount, runnableStages, pipelineIssues, samePipeline } from "./model";
import { PipelineEditor } from "./PipelineEditor";
import p from "./pipelinePages.module.css";

/** `target`: the Ticket page's dispatch target, shared with its header. */
export function PipelineTab({
  ticketKey,
  target,
}: {
  ticketKey: string;
  target: DispatchTargetState;
}) {
  const ticket = useTicket(ticketKey);
  const config = useConfig();
  const me = useMe();
  const save = useSavePipeline(ticketKey);
  const dispatch = useDispatch(ticketKey);
  const t = ticket.data;
  const draft = useDraft(t?.pipeline, t?.pipeline_version, samePipeline);
  const [from, setFrom] = useState<string | null>(null);

  if (ticket.isPending || config.isPending) {
    return (
      <p className={s.quiet} role="status">
        Loading pipeline
      </p>
    );
  }
  if (ticket.isError || config.isError) {
    return (
      <p className={s.quiet} data-error="" role="alert">
        {`Can't load the pipeline · ${(ticket.error ?? config.error)?.message ?? ""}`}
      </p>
    );
  }
  const value = draft.value ?? ticket.data.pipeline;
  const { runtimes } = config.data;

  const canWrite = me.data?.role === "writer" || me.data?.role === "admin";
  const active = ticket.data.active_run_id !== null;
  const readOnly = !canWrite || active || save.isPending;
  const issues = issueCount(pipelineIssues(value, runtimes));

  // "Run from…" offers the saved pipeline's enabled stages; the server's next stage is the default.
  const runnable = runnableStages(ticket.data.pipeline);
  const fallback =
    runnable.find((st) => st.id === ticket.data.next_stage)?.id ?? runnable[0]?.id ?? "";
  const fromStage = runnable.some((st) => st.id === from) && from !== null ? from : fallback;
  const runBlocked = draft.dirty
    ? "Save or discard your changes first"
    : active
      ? "A run is active"
      : runnable.length === 0
        ? "No stage is enabled"
        : undefined;

  const reload = () => {
    draft.discard();
    save.reset();
    void ticket.refetch();
  };

  return (
    <div className={s.page}>
      <SaveBar
        title="Pipeline"
        meta={`${String(value.stages.length)} stages · this ticket's copy`}
        dirty={draft.dirty}
        readOnly={!canWrite}
        saveDisabledReason={active ? "A run is active" : fixFirst(issues)}
        saving={save.isPending}
        problem={save.error ? describeSaveError(save.error) : null}
        onDiscard={() => {
          draft.discard();
          save.reset();
        }}
        onReload={reload}
        onSave={() => {
          save.mutate(
            { pipeline: value, base_version: draft.baseVersion ?? ticket.data.pipeline_version },
            {
              // The ticket is refetched before this runs, so the editor gets the saved copy.
              onSuccess: () => {
                draft.discard();
              },
            },
          );
        }}
        extra={
          canWrite ? (
            <span className={p.runFrom}>
              <Select
                size="sm"
                aria-label="Stage to run from"
                value={fromStage}
                disabled={runBlocked !== undefined || dispatch.isPending}
                options={runnable.map((st) => ({ value: st.id, label: st.name }))}
                onChange={setFrom}
              />
              <TargetPicker
                target={target}
                size="sm"
                disabled={runBlocked !== undefined || dispatch.isPending}
              />
              <Button
                size="sm"
                disabled={runBlocked !== undefined || dispatch.isPending || fromStage === ""}
                title={runBlocked ?? `Dispatch a run starting at this stage`}
                onClick={() => {
                  dispatch.mutate({ fromStage, target: target.value });
                }}
              >
                {dispatch.isPending ? "Starting…" : "Run from"}
              </Button>
            </span>
          ) : null
        }
      />

      <WorkflowStrip
        ticket={ticket.data}
        canWrite={canWrite}
        isAdmin={me.data?.role === "admin"}
        dirty={draft.dirty}
      />

      {dispatch.error ? (
        <p className={s.problem} role="alert">
          <span>{`Couldn't start the run · ${dispatch.error.message}`}</span>
        </p>
      ) : null}

      {active ? (
        <p className={s.banner} role="status">
          <span className={s.k}>Run active</span>
          The pipeline is read-only until the run finishes.
        </p>
      ) : !canWrite && me.data ? (
        <p className={s.banner} role="status">
          <span className={s.k}>Read-only</span>
          Viewers can't change a ticket's pipeline.
        </p>
      ) : null}

      <PipelineEditor
        value={value}
        onChange={draft.edit}
        readOnly={readOnly}
        runtimes={runtimes}
        space={configDefaults(config.data)}
      />
    </div>
  );
}
