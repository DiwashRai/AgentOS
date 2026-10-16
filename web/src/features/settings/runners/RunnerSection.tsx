// Settings › Runners › Runner: where the GitHub launchers (github, and mailbox: GitHub Actions
// with Artifactory carrying each run in and out; none = clients only) dispatch run workflows.
// The launcher and the token come from the server's env (read-only here); the repo, workflow file
// and ref are read at every dispatch. Controlled; the page owns the document and the save,
// and the server's issues show on their fields.
import { errorAt, Field, Input, type Issues } from "../../../editor";
import { cx } from "../../../ui";
import { launcherLabel, type RunnerForm, type RunnerSettings } from "./model";
import s from "./Runners.module.css";

export interface RunnerSectionProps {
  settings: Pick<RunnerSettings, "launcher" | "token_set">;
  value: RunnerForm;
  issues: Issues;
  readOnly: boolean;
  onChange: (value: RunnerForm) => void;
}

export function RunnerSection({ settings, value, issues, readOnly, onChange }: RunnerSectionProps) {
  const github = settings.launcher !== null;
  const noRepo = value.github_repo.trim() === "";

  /** A Field's control: one mono text input for `field`. */
  const text =
    (field: keyof RunnerForm, placeholder: string) =>
    (ids: { id: string; describedBy: string | undefined; invalid: boolean }) => (
      <Input
        id={ids.id}
        mono
        value={value[field]}
        placeholder={placeholder}
        readOnly={readOnly}
        spellCheck={false}
        autoComplete="off"
        aria-describedby={ids.describedBy}
        aria-invalid={ids.invalid}
        onChange={(e) => {
          onChange({ ...value, [field]: e.target.value });
        }}
      />
    );

  return (
    <>
      <dl className={s.facts}>
        <dt className={s.k}>Launcher</dt>
        <dd>
          {launcherLabel(settings.launcher)}
          <span className={s.envNote}>AGENTOS_LAUNCHER, set on the server</span>
        </dd>
        <dt className={s.k}>Token</dt>
        <dd className={cx(!settings.token_set && github && s.missing)}>
          {settings.token_set ? (
            "GitHub token set"
          ) : (
            <>
              <span className={s.mono}>AGENTOS_GITHUB_TOKEN</span> isn&apos;t set on the server
            </>
          )}
        </dd>
      </dl>

      <p className={s.lead} role="note">
        {github
          ? "Read at every dispatch: changes apply to the next run, no restart."
          : "This server launches no workers: runs go to personal clients. These apply only with a GitHub launcher."}
      </p>

      <div className={s.sheet}>
        <Field
          label="GitHub repo"
          error={errorAt(issues, "github_repo")}
          hint={
            noRepo && github ? (
              <span className={s.warn}>None set: runs fail at launch until it is.</span>
            ) : (
              "owner/name of the repo that holds the workflow."
            )
          }
        >
          {text("github_repo", "owner/name")}
        </Field>
        <Field
          label="Workflow"
          error={errorAt(issues, "workflow")}
          hint="A file in that repo's .github/workflows/."
        >
          {text(
            "workflow",
            settings.launcher === "mailbox" ? "agentos-mailbox.yml" : "agentos-worker.yml",
          )}
        </Field>
        <Field
          label="Ref"
          error={errorAt(issues, "ref")}
          hint="The branch or tag the workflow file is on."
        >
          {text("ref", "main")}
        </Field>
      </div>
    </>
  );
}
