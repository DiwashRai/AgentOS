// Settings › Intake: the JQL queries the Intake tab lists, one versioned document on the kit's
// DocumentPage. Issues come from the server: live from validate-only while editing, and from the
// save. Admin only (DocumentPage shows everyone else one quiet line). Renders a content column;
// the route wraps it in the Settings top bar + Surface.
import { Link as RouterLink } from "react-router";

import {
  useMe,
  usePreviewIntake,
  useSaveSettings,
  useSettings,
  useValidateSettings,
} from "../../../api/queries";
import { DocumentPage, DocumentSection, pageStyles as pages, useDocument } from "../../../editor";
import { linkClass, text } from "../../../ui";
import s from "./IntakeSettings.module.css";
import { fromForm, sameForm, sentKeys, SHOWN, toForm } from "./model";
import { QueryEditor } from "./QueryEditor";

const ADMINS_ONLY = "Only admins can change intake queries.";

export function IntakeSettingsPage() {
  const me = useMe();
  const isAdmin = me.data?.role === "admin";
  const query = useSettings("intake", isAdmin);
  const save = useSaveSettings("intake");
  const validate = useValidateSettings("intake");
  const previewIntake = usePreviewIntake();

  const doc = useDocument({
    query,
    save,
    validate,
    toForm,
    fromForm,
    same: sameForm,
    keys: sentKeys,
    shown: SHOWN,
  });

  const runPreview = (jql: string) => previewIntake.mutateAsync({ jql });

  return (
    <DocumentPage viewer={me.data} adminsOnly={ADMINS_ONLY} noun="intake queries" sources={[doc]}>
      {() => (
        <DocumentSection
          title="Intake queries"
          document={doc}
          description={
            <p className={pages.banner} role="note">
              <span className={text.label}>Intake</span>
              <span>
                The server adds <code className={s.code}>ORDER BY updated DESC</code> unless the JQL
                has its own ORDER BY. Issues outside the Space&apos;s Jira projects are never
                offered. Saving doesn&apos;t check the JQL against Jira: use Test query.{" "}
                <RouterLink className={linkClass} to="/intake">
                  Open Intake →
                </RouterLink>
              </span>
            </p>
          }
        >
          {(form) => (
            <QueryEditor
              form={form}
              issues={doc.issues}
              readOnly={doc.saving}
              preview={runPreview}
              onChange={doc.edit}
            />
          )}
        </DocumentSection>
      )}
    </DocumentPage>
  );
}
