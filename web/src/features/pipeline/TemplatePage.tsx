// Settings › Space defaults & scaffold (the template, SPEC §13 "Other pages"): the Space defaults
// (runtime, model, effort) every agent stage falls back to, saved here (admins save; everyone else
// reads), plus the scaffold files listed read-only. A new ticket's pipeline comes from a workflow
// (Settings › Workflows), not from here. Renders a content column; the route wraps it in the
// shell's TopBar + Surface.
import { Link as RouterLink } from "react-router";

import { useConfig, useMe, useSaveTemplate, useTemplate } from "../../api/queries";
import { linkClass, Tag, ZoneHead } from "../../ui";
import { DefaultsSection } from "./DefaultsSection";
import { defaultsBody, describeSaveError, sameDefaults, validateDefaults } from "./model";
import s from "./pages.module.css";
import { SaveBar } from "./SaveBar";
import { useDraft } from "./useDraft";

const WORKFLOWS_PATH = "/settings/workflows";

export function TemplatePage() {
  const template = useTemplate();
  const config = useConfig();
  const me = useMe();
  const save = useSaveTemplate();
  const draft = useDraft(template.data?.defaults, template.data?.version, sameDefaults);

  if (template.isPending || config.isPending) {
    return (
      <p className={s.quiet} role="status">
        Loading Space defaults
      </p>
    );
  }
  if (template.isError || config.isError) {
    return (
      <p className={s.quiet} data-error="" role="alert">
        {`Can't load the Space defaults · ${(template.error ?? config.error)?.message ?? ""}`}
      </p>
    );
  }
  const data = template.data;
  const value = draft.value ?? data.defaults;
  const { runtimes, server_default_runtime } = config.data;
  const isAdmin = me.data?.role === "admin";
  const defaultsIssues = validateDefaults(value, runtimes);
  const issues = Object.keys(defaultsIssues).length;

  return (
    <div className={s.page}>
      <SaveBar
        title="Space defaults & scaffold"
        meta={`version ${String(data.version)}`}
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
              scaffold: data.scaffold,
              defaults: defaultsBody(value),
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
        <span className={s.k}>{isAdmin ? "Space-wide" : "Read-only"}</span>
        <span>
          {isAdmin ? "" : "Only admins can change these. "}
          These apply from the next run of every ticket. A new ticket&apos;s pipeline comes from a
          workflow:{" "}
          <RouterLink className={linkClass} to={WORKFLOWS_PATH}>
            Settings › Workflows
          </RouterLink>
          .
        </span>
      </p>

      <DefaultsSection
        value={value}
        runtimes={runtimes}
        serverRuntime={server_default_runtime}
        issues={defaultsIssues}
        readOnly={!isAdmin || save.isPending}
        onChange={draft.edit}
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
