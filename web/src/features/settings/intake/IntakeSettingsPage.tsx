// Settings › Intake (WEEKEND.md "Settings in the app", request 18): the JQL queries the Intake
// tab lists, edited as one document with `useDraft` + `SaveBar` like the Template page. Admin
// only; everyone else gets one quiet line (the API would 403 anyway). Renders a content column;
// the route wraps it in the Settings top bar + Surface.
import { useMemo } from "react";
import { Link as RouterLink } from "react-router";

import { useIntakeSettings, useMe, useSaveIntakeSettings } from "../../../api/queries";
import { linkClass } from "../../../ui";
import { describeSaveError } from "../../pipeline/model";
import pages from "../../pipeline/pages.module.css";
import { SaveBar } from "../../pipeline/SaveBar";
import { useDraft } from "../../pipeline/useDraft";
import { useNow } from "../../runs/useNow";
import { isForbidden, savedLine } from "../common";
import { fixLine, formOf, issueCount, sameForm, toBody, validate, type IntakeForm } from "./model";
import s from "./IntakeSettings.module.css";
import { QueryEditor } from "./QueryEditor";

const ADMINS_ONLY = "Only admins can change intake queries.";

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

export function IntakeSettingsPage() {
  const me = useMe();
  const isAdmin = me.data?.role === "admin";
  const settings = useIntakeSettings(isAdmin);
  const save = useSaveIntakeSettings();
  const now = useNow(60_000);
  const data = settings.data;
  const saved = useMemo<IntakeForm | undefined>(() => (data ? formOf(data) : undefined), [data]);
  const draft = useDraft(saved, data?.version, sameForm);

  if (me.isPending) return <Line>Loading intake queries</Line>;
  if (!isAdmin) return <Line>{ADMINS_ONLY}</Line>;
  if (settings.isPending) return <Line>Loading intake queries</Line>;
  if (settings.isError) {
    if (isForbidden(settings.error)) return <Line>{ADMINS_ONLY}</Line>;
    return <Line error>{`Can't load intake queries · ${settings.error.message}`}</Line>;
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
        saveDisabledReason={fixLine(issues)}
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
