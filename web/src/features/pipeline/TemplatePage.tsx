// The Template page (SPEC §13 "Other pages"): the Space defaults (runtime, model) and the pipeline
// every new ticket starts from, edited with the same PipelineEditor and saved together (admins
// save; everyone else reads), plus the scaffold files listed read-only. Renders a content column;
// the route wraps it in the shell's TopBar + Surface.
import { useMemo } from "react";

import { useConfig, useMe, useSaveTemplate, useTemplate } from "../../api/queries";
import { Tag, ZoneHead } from "../../ui";
import { DefaultsSection } from "./DefaultsSection";
import {
  defaultsBody,
  describeSaveError,
  effectiveDefaults,
  issueCount,
  sameTemplateForm,
  validateDefaults,
  validatePipeline,
  type TemplateForm,
} from "./model";
import s from "./pages.module.css";
import { PipelineEditor } from "./PipelineEditor";
import { SaveBar } from "./SaveBar";
import { useDraft } from "./useDraft";

export function TemplatePage() {
  const template = useTemplate();
  const config = useConfig();
  const me = useMe();
  const save = useSaveTemplate();
  const t = template.data;
  const saved = useMemo<TemplateForm | undefined>(
    () => (t ? { pipeline: t.pipeline, defaults: t.defaults } : undefined),
    [t],
  );
  const draft = useDraft(saved, t?.version, sameTemplateForm);

  if (template.isPending || config.isPending) {
    return (
      <p className={s.quiet} role="status">
        Loading template
      </p>
    );
  }
  if (template.isError || config.isError) {
    return (
      <p className={s.quiet} data-error="" role="alert">
        {`Can't load the template · ${(template.error ?? config.error)?.message ?? ""}`}
      </p>
    );
  }
  const data = template.data;
  const value = draft.value ?? { pipeline: data.pipeline, defaults: data.defaults };
  const { runtimes, server_default_runtime } = config.data;
  const isAdmin = me.data?.role === "admin";
  const readOnly = !isAdmin || save.isPending;
  const defaultsIssues = validateDefaults(value.defaults, runtimes);
  const issues =
    issueCount(validatePipeline(value.pipeline.stages, runtimes)) +
    Object.keys(defaultsIssues).length;

  return (
    <div className={s.page}>
      <SaveBar
        title="Template pipeline"
        meta={`${String(value.pipeline.stages.length)} stages · version ${String(data.version)}`}
        dirty={draft.dirty}
        readOnly={!isAdmin}
        saveDisabledReason={
          issues > 0
            ? `Fix ${issues === 1 ? "1 issue" : `${String(issues)} issues`} first`
            : undefined
        }
        saving={save.isPending}
        problem={save.error ? describeSaveError(save.error) : null}
        onDiscard={() => {
          draft.discard();
          save.reset();
        }}
        onReload={() => {
          draft.discard();
          save.reset();
          void template.refetch();
        }}
        onSave={() => {
          save.mutate(
            {
              pipeline: value.pipeline,
              scaffold: data.scaffold,
              defaults: defaultsBody(value.defaults),
              base_version: draft.baseVersion ?? data.version,
            },
            {
              onSuccess: () => {
                draft.discard();
              },
            },
          );
        }}
      />

      <p className={s.banner} role="note">
        <span className={s.k}>{isAdmin ? "Template" : "Read-only"}</span>
        {isAdmin
          ? "New tickets copy this pipeline when they are created. Existing tickets keep their own copy."
          : "Only admins can change the template. New tickets copy this pipeline when they are created."}
      </p>

      <DefaultsSection
        value={value.defaults}
        runtimes={runtimes}
        serverRuntime={server_default_runtime}
        issues={defaultsIssues}
        readOnly={readOnly}
        onChange={(defaults) => {
          draft.edit({ ...value, defaults });
        }}
      />

      <PipelineEditor
        value={value.pipeline}
        onChange={(pipeline) => {
          draft.edit({ ...value, pipeline });
        }}
        readOnly={readOnly}
        runtimes={runtimes}
        space={effectiveDefaults(value.defaults, config.data)}
      />

      <section className={s.scaffold} aria-labelledby="scaffold-head">
        <ZoneHead
          id="scaffold-head"
          title="Scaffold"
          count={data.scaffold.length}
          note="Files written into every new ticket's context, rendered once at creation."
        />
        {data.scaffold.length === 0 ? (
          <p className={s.quiet}>No scaffold files.</p>
        ) : (
          <ul className={s.files}>
            {data.scaffold.map((file) => (
              <li key={file.path} className={s.file}>
                <code className={s.path}>{file.path}</code>
                <Tag
                  title={file.mode === "block" ? "Appended as a marked block" : "Replaces the file"}
                >
                  {file.mode}
                </Tag>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
