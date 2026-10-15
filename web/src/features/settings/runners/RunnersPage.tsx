// Settings › Runners: where the GitHub launcher dispatches runs (Runner) and the agent runtimes
// stages run on (Runtimes). Two documents, each with its own Save:
// - the runner is a versioned document (`useDocument`): the server checks it as it is typed and on
//   save, and its issues show on their fields;
// - the runtimes keep their own draft, save bar and client checks. Their draft holds no leave
//   warning: DocumentPage warns once for both (the router allows one blocker).
// DocumentPage draws the loading, admins-only and error states. Renders a content column; the route
// wraps it in the shell's TopBar + Surface.
import { useMemo } from "react";

import {
  useMe,
  useRuntimeSettings,
  useSaveRuntimeSettings,
  useSaveSettings,
  useSettings,
  useValidateSettings,
} from "../../../api/queries";
import {
  describeSaveError,
  DocumentPage,
  DocumentSection,
  fixFirst,
  pageStyles as pages,
  SaveBar,
  savedLine,
  useDocument,
  useDraftState,
} from "../../../editor";
import { cx, useNow } from "../../../ui";
import {
  RUNNER_FIELDS,
  runnerForm,
  runnerValue,
  rowsFrom,
  runtimesBody,
  runtimesIssueCount,
  sameRunner,
  sameRuntimes,
  validateRuntimes,
} from "./model";
import { RunnerSection } from "./RunnerSection";
import s from "./Runners.module.css";
import { RuntimesEditor } from "./RuntimesEditor";

const ADMINS_ONLY = "Only admins can change runners.";

export function RunnersPage() {
  const me = useMe();
  const isAdmin = me.data?.role === "admin";

  const runner = useDocument({
    query: useSettings("runner", isAdmin),
    save: useSaveSettings("runner"),
    validate: useValidateSettings("runner"),
    toForm: runnerForm,
    fromForm: runnerValue,
    same: sameRunner,
    shown: RUNNER_FIELDS,
  });

  const runtimesQ = useRuntimeSettings(isAdmin);
  const saveRuntimes = useSaveRuntimeSettings();
  const rt = runtimesQ.data;
  const savedRows = useMemo(() => (rt ? rowsFrom(rt.runtimes) : undefined), [rt]);
  const runtimes = useDraftState(savedRows, rt?.version, sameRuntimes);
  const now = useNow(60_000);

  return (
    <DocumentPage
      viewer={me.data}
      adminsOnly={ADMINS_ONLY}
      noun="runners"
      sources={[runner, runtimesQ]}
      dirty={runtimes.dirty}
    >
      {() => {
        if (!rt) return null;
        const rows = runtimes.value ?? rowsFrom(rt.runtimes);
        const runtimeIssues = validateRuntimes(rows);

        return (
          <>
            <DocumentSection title="Runner" document={runner}>
              {(form, read) => (
                <RunnerSection
                  settings={read}
                  value={form}
                  issues={runner.issues}
                  readOnly={runner.saving}
                  onChange={runner.edit}
                />
              )}
            </DocumentSection>

            <section className={cx(pages.section, s.runtimes)} aria-label="Runtimes">
              <SaveBar
                title="Runtimes"
                meta={savedLine(rt, now)}
                dirty={runtimes.dirty}
                readOnly={false}
                saveDisabledReason={fixFirst(runtimesIssueCount(runtimeIssues))}
                saving={saveRuntimes.isPending}
                problem={saveRuntimes.error ? describeSaveError(saveRuntimes.error) : null}
                onDiscard={() => {
                  runtimes.discard();
                  saveRuntimes.reset();
                }}
                onReload={() => {
                  runtimes.discard();
                  saveRuntimes.reset();
                  void runtimesQ.refetch();
                }}
                onSave={() => {
                  saveRuntimes.mutate(runtimesBody(rows, runtimes.baseVersion ?? rt.version), {
                    onSuccess: () => {
                      runtimes.discard();
                    },
                  });
                }}
              />
              <p className={s.lead} role="note">
                Agent stages run one of these commands. The first is the server default: stages use
                it when neither they nor the Space defaults name a runtime. Move a runtime to the
                top to make it the default. Removing one a workflow or the Space defaults use is
                refused.
              </p>
              <RuntimesEditor
                rows={rows}
                issues={runtimeIssues}
                readOnly={saveRuntimes.isPending}
                onChange={runtimes.edit}
              />
            </section>
          </>
        );
      }}
    </DocumentPage>
  );
}
