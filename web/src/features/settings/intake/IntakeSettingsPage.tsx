// Settings › Intake (WEEKEND.md "Settings in the app", request 18): the JQL queries the Intake
// tab lists, edited as one document with `useDraft` + `SaveBar` like the Template page. Admin
// only; everyone else gets one quiet line (the API would 403 anyway). Renders a content column;
// the route wraps it in the Settings top bar + Surface.
import { useMemo } from "react";
import { Link as RouterLink } from "react-router";

import { useIntakeSettings, useMe, useSaveIntakeSettings } from "../../../api/queries";
import {
  describeSaveError,
  fixFirst,
  pageStyles as pages,
  SaveBar,
  useDraft,
} from "../../../editor";
import { linkClass, StatusLine } from "../../../ui";
import { useNow } from "../../../ui/useNow";
import { isForbidden, savedLine } from "../common";
import { formOf, issueCount, sameForm, toBody, validate, type IntakeForm } from "./model";
import s from "./IntakeSettings.module.css";
import { QueryEditor } from "./QueryEditor";

const ADMINS_ONLY = "Only admins can change intake queries.";

export function IntakeSettingsPage() {
  const me = useMe();
  const isAdmin = me.data?.role === "admin";
  const settings = useIntakeSettings(isAdmin);
  const save = useSaveIntakeSettings();
  const now = useNow(60_000);
  const data = settings.data;
  const saved = useMemo<IntakeForm | undefined>(() => (data ? formOf(data) : undefined), [data]);
  const draft = useDraft(saved, data?.version, sameForm);

  if (me.isPending) return <StatusLine>Loading intake queries</StatusLine>;
  if (!isAdmin) return <StatusLine>{ADMINS_ONLY}</StatusLine>;
  if (settings.isPending) return <StatusLine>Loading intake queries</StatusLine>;
  if (settings.isError) {
    if (isForbidden(settings.error)) return <StatusLine>{ADMINS_ONLY}</StatusLine>;
    return <StatusLine error>{`Can't load intake queries · ${settings.error.message}`}</StatusLine>;
  }

  const doc = settings.data;
  const value = draft.value ?? formOf(doc);
  const issues = issueCount(validate(value));

  return (
    <div className={pages.page}>
      <SaveBar
        title="Intake queries"
        meta={savedLine(doc, now)}
        dirty={draft.dirty}
        readOnly={false}
        saveDisabledReason={fixFirst(issues)}
        saving={save.isPending}
        problem={save.error ? describeSaveError(save.error) : null}
        onDiscard={() => {
          draft.discard();
          save.reset();
        }}
        onReload={() => {
          draft.discard();
          save.reset();
          void settings.refetch();
        }}
        onSave={() => {
          save.mutate(toBody(value, draft.baseVersion ?? doc.version), {
            onSuccess: () => {
              draft.discard();
            },
          });
        }}
      />

      <p className={pages.banner} role="note">
        <span className={pages.k}>Intake</span>
        <span>
          The server adds <code className={s.code}>ORDER BY updated DESC</code> unless the JQL has
          its own ORDER BY. Issues outside the Space&apos;s Jira projects are never offered. Saving
          doesn&apos;t check the JQL against Jira: use Test query.{" "}
          <RouterLink className={linkClass} to="/intake">
            Open Intake →
          </RouterLink>
        </span>
      </p>

      <QueryEditor
        value={value}
        readOnly={save.isPending}
        onChange={(next) => {
          draft.edit(next);
        }}
      />
    </div>
  );
}
