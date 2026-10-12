// The Space settings fields (name, key prefix, Jira projects, default repo, claim timeout).
// Controlled; the page owns the draft and the save.
import { useState } from "react";

import { cx } from "../../../ui";
import { Field, fieldStyles as f, Select } from "../../pipeline/fields";
import { claimHint, CLAIM_MAX_S, CLAIM_MIN_S, type SpaceForm, type SpaceIssues } from "./model";
import { ProjectKeys } from "./ProjectKeys";
import s from "./Space.module.css";

const NONE = "";

export interface SpaceFieldsProps {
  value: SpaceForm;
  issues: SpaceIssues;
  readOnly: boolean;
  /** The saved repo catalog's names: `default_repo` must be one of them. */
  repoNames: readonly string[];
  /** Sign-in is with Jira, so the projects decide who may ask for access. */
  jiraAuth: boolean;
  /** The repos below have unsaved changes (a new repo can't be the default until it is saved). */
  reposDirty: boolean;
  onChange: (value: SpaceForm) => void;
}

export function SpaceFields(props: SpaceFieldsProps) {
  const { value, issues, readOnly, onChange } = props;
  const [typingError, setTypingError] = useState<string | null>(null);
  const set = (patch: Partial<SpaceForm>) => {
    onChange({ ...value, ...patch });
  };

  const repoOptions = [
    { value: NONE, label: "None" },
    ...props.repoNames.map((n) => ({ value: n, label: n })),
  ];
  // A saved default that left the catalog still shows (the error says why).
  if (value.default_repo !== null && !props.repoNames.includes(value.default_repo)) {
    repoOptions.push({ value: value.default_repo, label: value.default_repo });
  }

  const prefix = value.key_prefix.trim();
  return (
    <div className={s.sheet}>
      <Field label="Name" error={issues.name} hint="Shown in the top bar.">
        {({ id, describedBy, invalid }) => (
          <input
            id={id}
            className={f.input}
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
        error={issues.key_prefix}
        hint={`New tickets made here are numbered ${prefix}-1, ${prefix}-2… Existing keys never change.`}
      >
        {({ id, describedBy, invalid }) => (
          <input
            id={id}
            className={cx(f.input, f.mono)}
            value={value.key_prefix}
            readOnly={readOnly}
            spellCheck={false}
            autoComplete="off"
            maxLength={10}
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
        error={typingError ?? issues.jira_projects}
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
            readOnly={readOnly}
            onChange={(jira_projects) => {
              set({ jira_projects });
            }}
            onTypingError={setTypingError}
          />
        )}
      </Field>

      <Field
        label="Default repo"
        error={issues.default_repo}
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
            value={value.default_repo ?? NONE}
            disabled={readOnly}
            options={repoOptions}
            onChange={(v) => {
              set({ default_repo: v === NONE ? null : v });
            }}
          />
        )}
      </Field>

      <Field
        label="Claim timeout (seconds)"
        error={issues.claim_timeout_s}
        hint={claimHint(value.claim_timeout_s)}
      >
        {({ id, describedBy, invalid }) => (
          <input
            id={id}
            className={cx(f.input, f.mono, s.timeout)}
            type="number"
            inputMode="numeric"
            min={CLAIM_MIN_S}
            max={CLAIM_MAX_S}
            step={1}
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
