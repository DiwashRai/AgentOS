// The Template page (SPEC §13 "Other pages"): the pipeline every new ticket starts from, edited
// with the same PipelineEditor (admins save; everyone else reads), plus the scaffold files
// listed read-only. Renders a content column; the route wraps it in the shell's TopBar + Surface.
import { useConfig, useMe, useSaveTemplate, useTemplate } from "../../api/queries";
import { Tag, ZoneHead } from "../../ui";
import { describeSaveError, issueCount, validatePipeline } from "./model";
import s from "./pages.module.css";
import { PipelineEditor } from "./PipelineEditor";
import { SaveBar } from "./SaveBar";
import { usePipelineDraft } from "./useDraft";

export function TemplatePage() {
  const template = useTemplate();
  const config = useConfig();
  const me = useMe();
  const save = useSaveTemplate();
  const draft = usePipelineDraft(template.data?.pipeline, template.data?.version);

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
  const t = template.data;
  const value = draft.value ?? t.pipeline;
  const { runtimes, default_runtime } = config.data;
  const isAdmin = me.data?.role === "admin";
  const issues = issueCount(validatePipeline(value.stages, runtimes));

  return (
    <div className={s.page}>
      <SaveBar
        title="Template pipeline"
        meta={`${String(value.stages.length)} stages · version ${String(t.version)}`}
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
              pipeline: value,
              scaffold: t.scaffold,
              base_version: draft.baseVersion ?? t.version,
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

      <PipelineEditor
        value={value}
        onChange={draft.edit}
        readOnly={!isAdmin || save.isPending}
        runtimes={runtimes}
        defaultRuntime={default_runtime}
      />

      <section className={s.scaffold} aria-labelledby="scaffold-head">
        <ZoneHead
          id="scaffold-head"
          title="Scaffold"
          count={t.scaffold.length}
          note="Files written into every new ticket's context, rendered once at creation."
        />
        {t.scaffold.length === 0 ? (
          <p className={s.quiet}>No scaffold files.</p>
        ) : (
          <ul className={s.files}>
            {t.scaffold.map((file) => (
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
