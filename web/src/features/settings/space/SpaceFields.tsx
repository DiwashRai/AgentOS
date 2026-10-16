// The Space settings fields (name, key prefix, Jira projects, default repo, claim timeout), each
// showing the server's issues at its path. Controlled; the page owns the document and the save.
import { errorAt, Field, Input, messagesAt, pathOf, Select, type Issues } from "../../../editor";
import { claimHint, defaultRepoOptions, NO_REPO, type SpaceForm } from "./model";
import { ProjectKeys } from "./ProjectKeys";
import s from "./Space.module.css";

export interface SpaceFieldsProps {
  value: SpaceForm;
  issues: Issues;
  readOnly: boolean;
  /** The saved repo catalog's names: what the default repo can be. */
  repoNames: readonly string[];
  /** Sign-in is with Jira, so the projects decide who may ask for access. */
  jiraAuth: boolean;
  /** The repos below have unsaved changes (a new repo can't be the default until it is saved). */
  reposDirty: boolean;
  onChange: (value: SpaceForm) => void;
}

export function SpaceFields(props: SpaceFieldsProps) {
  const { value, issues, readOnly, onChange } = props;
  const set = (patch: Partial<SpaceForm>) => {
    onChange({ ...value, ...patch });
  };

  // Issues about one project key name it, since the message alone doesn't.
  const keyIssues = (index: number) => messagesAt(issues, pathOf("jira_projects", index));
  const projectMessages = [
    ...messagesAt(issues, "jira_projects"),
    ...value.jira_projects.flatMap((key, i) => keyIssues(i).map((m) => `${key}: ${m}`)),
  ];

  const prefix = value.key_prefix.trim();
  return (
    <div className={s.sheet}>
      <Field label="Name" error={errorAt(issues, "name")} hint="Shown in the top bar.">
        {({ id, describedBy, invalid }) => (
          <Input
            id={id}
            value={value.name}
            readOnly={readOnly}
            autoComplete="off"
            aria-describedby={describedBy}
            aria-invalid={invalid}
            onChange={(e) => {
              set({ name: e.target.value });
            }}
          />
        )}
      </Field>

      <Field
        label="Key prefix"
        error={errorAt(issues, "key_prefix")}
        hint={`New tickets made here are numbered ${prefix}-1, ${prefix}-2… Existing keys never change.`}
      >
        {({ id, describedBy, invalid }) => (
          <Input
            id={id}
            mono
            value={value.key_prefix}
            readOnly={readOnly}
            spellCheck={false}
            autoComplete="off"
            aria-describedby={describedBy}
            aria-invalid={invalid}
            onChange={(e) => {
              set({ key_prefix: e.target.value.toUpperCase() });
            }}
          />
        )}
      </Field>

      <Field
        label="Jira projects"
        className={s.wide}
        error={projectMessages.length === 0 ? undefined : projectMessages.join(" ")}
        hint={
          props.jiraAuth
            ? "Signing in needs access to one of these (admins excepted; new people still wait for approval). Intake and imports only take issues from them."
            : "Intake and imports only take issues from these. Sign-in here is dev-only, so they don't decide who gets in."
        }
      >
        {({ id, describedBy, invalid }) => (
          <ProjectKeys
            id={id}
            describedBy={describedBy}
            invalid={invalid}
            value={value.jira_projects}
            flagged={(i) => keyIssues(i).length > 0}
            readOnly={readOnly}
            onChange={(jira_projects) => {
              set({ jira_projects });
            }}
          />
        )}
      </Field>

      <Field
        label="Default repo"
        error={errorAt(issues, "default_repo")}
        hint={
          props.reposDirty
            ? "Save the repos below first to choose a new one."
            : "What a new ticket gets when it names no repo."
        }
      >
        {({ id, describedBy, invalid }) => (
          <Select
            id={id}
            aria-describedby={describedBy}
            aria-invalid={invalid}
            value={value.default_repo ?? NO_REPO}
            disabled={readOnly}
            options={defaultRepoOptions(props.repoNames, value.default_repo)}
            onChange={(v) => {
              set({ default_repo: v === NO_REPO ? null : v });
            }}
          />
        )}
      </Field>

      <Field
        label="Start timeout (seconds)"
        error={errorAt(issues, "claim_timeout_s")}
        hint={claimHint(value.claim_timeout_s)}
      >
        {({ id, describedBy, invalid }) => (
          <Input
            id={id}
            mono
            className={s.timeout}
            type="number"
            inputMode="numeric"
            value={value.claim_timeout_s}
            readOnly={readOnly}
            aria-describedby={describedBy}
            aria-invalid={invalid}
            onChange={(e) => {
              set({ claim_timeout_s: e.target.value });
            }}
          />
        )}
      </Field>
    </div>
  );
}
