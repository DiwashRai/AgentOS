// Settings › Space: the Space's own settings and its repo catalog, two versioned documents each
// saved on its own, under one leave warning (DocumentPage). The server checks every value: its
// validate-only call reports problems while the admin types, and they show on their fields.
// Admin only. Renders a content column; the route wraps it in the shell's TopBar + Surface.
import {
  useConfig,
  useMe,
  useSaveSettings,
  useSettings,
  useValidateSettings,
} from "../../../api/queries";
import { DocumentPage, DocumentSection, useDocument } from "../../../editor";
import { text } from "../../../ui";
import {
  repoKeys,
  REPOS_SHOWN,
  reposFromForm,
  reposToForm,
  sameRepos,
  SPACE_SHOWN,
  spaceFromForm,
  spaceToForm,
} from "./model";
import { RepoList } from "./RepoList";
import s from "./Space.module.css";
import { SpaceFields } from "./SpaceFields";

const ADMINS_ONLY = "Only admins can change Space settings.";

export function SpacePage() {
  const me = useMe();
  const isAdmin = me.data?.role === "admin";
  const config = useConfig();

  const space = useDocument({
    query: useSettings("space", isAdmin),
    save: useSaveSettings("space"),
    validate: useValidateSettings("space"),
    toForm: spaceToForm,
    fromForm: spaceFromForm,
    shown: SPACE_SHOWN,
  });
  const repos = useDocument({
    query: useSettings("repos", isAdmin),
    save: useSaveSettings("repos"),
    validate: useValidateSettings("repos"),
    toForm: reposToForm,
    fromForm: reposFromForm,
    same: sameRepos,
    keys: repoKeys,
    shown: REPOS_SHOWN,
  });

  return (
    <DocumentPage
      viewer={me.data}
      adminsOnly={ADMINS_ONLY}
      noun="Space settings"
      sources={[space, repos, config]}
      description={
        <>
          <span className={text.label}>Space</span>
          Changes apply at once, without a restart. The Space and its repos save separately.
        </>
      }
    >
      {() => (
        <>
          <DocumentSection title="Space" document={space}>
            {(form) => (
              <SpaceFields
                value={form}
                issues={space.issues}
                readOnly={space.saving}
                repoNames={repos.data?.value.repos.map((r) => r.name) ?? []}
                jiraAuth={config.data?.auth_mode === "jira"}
                reposDirty={repos.dirty}
                onChange={space.edit}
              />
            )}
          </DocumentSection>

          <DocumentSection
            title="Repos"
            document={repos}
            description={
              <p className={s.note}>
                Tickets refer to a repo by name, so a new URL or branch reaches every ticket that
                uses it. A name can&apos;t change: remove the repo and add it again.
              </p>
            }
          >
            {(form, read) => (
              <RepoList
                value={form}
                issues={repos.issues}
                readOnly={repos.saving}
                openCounts={read.open_ticket_counts}
                savedDefault={space.data?.value.default_repo ?? null}
                onChange={repos.edit}
              />
            )}
          </DocumentSection>
        </>
      )}
    </DocumentPage>
  );
}
