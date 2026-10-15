// Settings › Field mappings: Jira field → AgentOS field, by ordered wildcard rules. One versioned
// document on the kit: the mappings are a list (one per target, today only Base branch), each
// with its own ordered list of rules; live issues come from the server's validate-only call.
// Renders a content column; the route wraps it in the Settings top bar + Surface.
import {
  useJiraFields,
  useMe,
  usePreviewMapping,
  useSaveSettings,
  useSettings,
  useValidateSettings,
} from "../../../api/queries";
import {
  DocumentPage,
  DocumentSection,
  ListEditor,
  messagesAt,
  pageStyles as pages,
  useDocument,
} from "../../../editor";
import { MappingCard, type FieldsState } from "./MappingCard";
import s from "./Mappings.module.css";
import {
  formKeys,
  freeTarget,
  fromForm,
  isBlankMapping,
  newMapping,
  sameForm,
  SHOWN,
  specOf,
  TARGETS,
  toForm,
  type MappingRow,
  type MappingsForm,
} from "./model";

const ADMINS_ONLY = "Only admins can change field mappings.";

export function MappingsPage() {
  const me = useMe();
  const isAdmin = me.data?.role === "admin";
  const jiraFields = useJiraFields(isAdmin);
  const preview = usePreviewMapping();
  const doc = useDocument({
    query: useSettings("mappings", isAdmin),
    save: useSaveSettings("mappings"),
    validate: useValidateSettings("mappings"),
    toForm,
    fromForm,
    same: sameForm,
    keys: formKeys,
    shown: SHOWN,
  });

  const fields: FieldsState = {
    data: jiraFields.data,
    pending: jiraFields.isPending,
    fetching: jiraFields.isFetching,
    error: jiraFields.error,
    retry: () => {
      void jiraFields.refetch();
    },
  };

  return (
    <DocumentPage viewer={me.data} adminsOnly={ADMINS_ONLY} noun="field mappings" sources={[doc]}>
      {() => (
        <DocumentSection
          title="Field mappings"
          document={doc}
          description={
            <p className={pages.banner} role="note">
              <span className={pages.k}>Jira</span>
              <span>
                Rules apply when a Jira issue is imported or taken in. Saving re-applies them to
                tickets a mapping set that haven&apos;t started a run yet; branches set by hand and
                locked ones stay as they are.
              </span>
            </p>
          }
        >
          {(form) => {
            const edit = (mappings: readonly MappingRow[]) => {
              doc.edit({ ...form, mappings });
            };
            const target = freeTarget(form);
            return (
              <ListEditor
                name="Mappings"
                className={s.mappings}
                rows={form.mappings}
                onChange={edit}
                label={(row) => `${specOf(row.item.target).title.toLowerCase()} mapping`}
                create={target === undefined ? undefined : () => newMapping(target)}
                addLabel="Add a mapping"
                isBlank={(row) => isBlankMapping(row.item)}
                confirmText={(_, name) =>
                  `Remove the ${name}? Tickets it already set keep their branch; new imports won't use it once you save.`
                }
                readOnly={doc.saving}
                empty={TARGETS.map((t) => t.without).join(" ")}
                issue={messagesAt(doc.issues, "mappings")}
              >
                {(row) => (
                  <MappingCard
                    row={row}
                    issues={doc.issues}
                    fields={fields}
                    preview={preview.mutateAsync}
                    readOnly={doc.saving}
                    onChange={(item) => {
                      edit(replaceRow(form, row.key, item));
                    }}
                  />
                )}
              </ListEditor>
            );
          }}
        </DocumentSection>
      )}
    </DocumentPage>
  );
}

function replaceRow(form: MappingsForm, key: string, item: MappingRow["item"]): MappingRow[] {
  return form.mappings.map((row) => (row.key === key ? { ...row, item } : row));
}
