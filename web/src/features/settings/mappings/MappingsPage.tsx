// Settings › Field mappings (request 22B): Jira field → AgentOS field, by ordered wildcard rules.
// One document edited with `useDraft` + `SaveBar` like the other Settings pages, one card per
// target (today only Base branch). Admin only; everyone else gets one quiet line (the API would
// 403 anyway). Renders a content column; the route wraps it in the Settings top bar + Surface.
import { useMemo } from "react";

import {
  useJiraFields,
  useMappingSettings,
  useMe,
  useSaveMappingSettings,
} from "../../../api/queries";
import { describeSaveError } from "../../pipeline/model";
import pages from "../../pipeline/pages.module.css";
import { SaveBar } from "../../pipeline/SaveBar";
import { useDraft } from "../../pipeline/useDraft";
import { useNow } from "../../runs/useNow";
import { isForbidden, savedLine } from "../common";
import { MappingCard, type FieldsState } from "./MappingCard";
import s from "./Mappings.module.css";
import {
  addMapping,
  fixLine,
  formOf,
  indexOf,
  isBlankMapping,
  issueCount,
  mergeIssues,
  patchMapping,
  removeMapping,
  sameForm,
  serverIssues,
  TARGETS,
  toBody,
  validate,
  type MappingsForm,
} from "./model";

const ADMINS_ONLY = "Only admins can change field mappings.";

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

export function MappingsPage() {
  const me = useMe();
  const isAdmin = me.data?.role === "admin";
  const settings = useMappingSettings(isAdmin);
  const jiraFields = useJiraFields(isAdmin);
  const save = useSaveMappingSettings();
  const now = useNow(60_000);
  const data = settings.data;
  const saved = useMemo<MappingsForm | undefined>(() => (data ? formOf(data) : undefined), [data]);
  const draft = useDraft(saved, data?.version, sameForm);

  if (me.isPending) return <Line>Loading field mappings</Line>;
  if (!isAdmin) return <Line>{ADMINS_ONLY}</Line>;
  if (settings.isPending) return <Line>Loading field mappings</Line>;
  if (settings.isError) {
    if (isForbidden(settings.error)) return <Line>{ADMINS_ONLY}</Line>;
    return <Line error>{`Can't load field mappings · ${settings.error.message}`}</Line>;
  }

  const doc = settings.data;
  const value = draft.value ?? formOf(doc);
  const issues = validate(value);
  const fromServer = serverIssues(save.error, save.variables, value);
  const fields: FieldsState = {
    data: jiraFields.data,
    pending: jiraFields.isPending,
    fetching: jiraFields.isFetching,
    error: jiraFields.error,
    retry: () => {
      void jiraFields.refetch();
    },
  };
  const readOnly = save.isPending;

  return (
    <div className={pages.page}>
      <SaveBar
        title="Field mappings"
        meta={savedLine(doc, now)}
        dirty={draft.dirty}
        readOnly={false}
        saveDisabledReason={fixLine(issueCount(issues))}
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
        <span className={pages.k}>Jira</span>
        <span>
          Rules apply when a Jira issue is imported or taken in. Saving re-applies them to tickets a
          mapping set that haven&apos;t started a run yet; branches set by hand and locked ones stay
          as they are.
        </span>
      </p>

      <div className={s.cards}>
        {TARGETS.map((spec) => {
          const index = indexOf(value, spec.target);
          const mapping = value[index];
          const client = issues[index];
          return (
            <MappingCard
              key={spec.target}
              spec={spec}
              draft={mapping}
              issues={
                client &&
                mergeIssues(client, fromServer?.index === index ? fromServer.issues : undefined)
              }
              fields={fields}
              readOnly={readOnly}
              onAdd={() => {
                draft.edit(addMapping(value, spec.target));
              }}
              onRemove={() => {
                const ok =
                  (mapping !== undefined && isBlankMapping(mapping)) ||
                  window.confirm(
                    `Remove the ${spec.title.toLowerCase()} mapping? Tickets it already set keep their branch; new imports won't use it once you save.`,
                  );
                if (ok) draft.edit(removeMapping(value, spec.target));
              }}
              onChange={(patch) => {
                draft.edit(patchMapping(value, spec.target, patch));
              }}
            />
          );
        })}
      </div>
    </div>
  );
}
