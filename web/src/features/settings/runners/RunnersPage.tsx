// Settings › Runners (WEEKEND.md "Settings in the app"): where the GitHub launcher dispatches runs
// (Runner) and the agent runtimes stages run on (Runtimes). Two documents, each with its own draft
// and Save, like the Template page. Admin only; everyone else gets one quiet line (the API would
// 403 anyway). Renders a content column; the route wraps it in the shell's TopBar + Surface.
import { useMemo } from "react";

import {
  useMe,
  useRunnerSettings,
  useRuntimeSettings,
  useSaveRunnerSettings,
  useSaveRuntimeSettings,
} from "../../../api/queries";
import { describeSaveError } from "../../pipeline/model";
import pages from "../../pipeline/pages.module.css";
import { SaveBar } from "../../pipeline/SaveBar";
import { useDraft, useUnsavedWarning } from "../../pipeline/useDraft";
import { useNow } from "../../runs/useNow";
import { isForbidden, savedLine } from "../common";
import {
  fixFirst,
  rowsFrom,
  runnerBody,
  runnerForm,
  runtimesBody,
  runtimesIssueCount,
  sameRunner,
  sameRuntimes,
  validateRunner,
  validateRuntimes,
} from "./model";
import { RunnerSection } from "./RunnerSection";
import s from "./Runners.module.css";
import { RuntimesEditor } from "./RuntimesEditor";

const ADMINS_ONLY = "Only admins can change runners.";

function Line({ children, error }: { children: string; error?: boolean }) {
  return (
    <p
      className={pages.quiet}
      role={error ? "alert" : "status"}
      data-error={error ? "" : undefined}
    >
      {children}
    </p>
  );
}

export function RunnersPage() {
  const me = useMe();
  const isAdmin = me.data?.role === "admin";
  const runnerQ = useRunnerSettings(isAdmin);
  const runtimesQ = useRuntimeSettings(isAdmin);
  const saveRunner = useSaveRunnerSettings();
  const saveRuntimes = useSaveRuntimeSettings();
  const now = useNow(60_000);

  const r = runnerQ.data;
  const rt = runtimesQ.data;
  const savedRunner = useMemo(() => (r ? runnerForm(r) : undefined), [r]);
  const savedRows = useMemo(() => (rt ? rowsFrom(rt.runtimes) : undefined), [rt]);
  const runner = useDraft(savedRunner, r?.version, sameRunner, { warn: false });
  const runtimes = useDraft(savedRows, rt?.version, sameRuntimes, { warn: false });
  useUnsavedWarning(runner.dirty || runtimes.dirty);

  if (me.isPending) return <Line>Loading runners</Line>;
  if (!isAdmin) return <Line>{ADMINS_ONLY}</Line>;
  if (runnerQ.isError || runtimesQ.isError) {
    const e = runnerQ.error ?? runtimesQ.error;
    if (isForbidden(e)) return <Line>{ADMINS_ONLY}</Line>;
    return <Line error>{`Can't load runners · ${e?.message ?? ""}`}</Line>;
  }
  if (!r || !rt) return <Line>Loading runners</Line>;

  const runnerValue = runner.value ?? runnerForm(r);
  const runnerIssues = validateRunner(runnerValue);
  const rows = runtimes.value ?? rowsFrom(rt.runtimes);
  const runtimeIssues = validateRuntimes(rows);

  return (
    <div className={pages.page}>
      <section className={s.section} aria-label="Runner">
        <SaveBar
          title="Runner"
          meta={savedLine(r, now)}
          dirty={runner.dirty}
          readOnly={false}
          saveDisabledReason={fixFirst(Object.keys(runnerIssues).length)}
          saving={saveRunner.isPending}
          problem={saveRunner.error ? describeSaveError(saveRunner.error) : null}
          onDiscard={() => {
            runner.discard();
            saveRunner.reset();
          }}
          onReload={() => {
            runner.discard();
            saveRunner.reset();
            void runnerQ.refetch();
          }}
          onSave={() => {
            saveRunner.mutate(runnerBody(runnerValue, runner.baseVersion ?? r.version), {
              onSuccess: () => {
                runner.discard();
              },
            });
          }}
        />
        <RunnerSection
          settings={r}
          value={runnerValue}
          issues={runnerIssues}
          readOnly={saveRunner.isPending}
          onChange={runner.edit}
        />
      </section>

      <section className={s.section} aria-label="Runtimes">
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
          Agent stages run one of these commands. The first is the server default: stages use it
          when neither they nor the Space defaults name a runtime. Move a runtime to the top to make
          it the default. Removing one a workflow or the Space defaults use is refused.
        </p>
        <RuntimesEditor
          rows={rows}
          issues={runtimeIssues}
          readOnly={saveRuntimes.isPending}
          onChange={runtimes.edit}
        />
      </section>
    </div>
  );
}
