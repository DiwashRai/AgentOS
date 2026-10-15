// Settings › Space (WEEKEND.md "Settings in the app"): the Space's own settings and its repo
// catalog, two documents each saved on its own (useDraft + SaveBar, like the Template page).
// Admin only; everyone else gets one quiet line (the API would 403 anyway). Renders a content
// column; the route wraps it in the shell's TopBar + Surface.
import { useMemo } from "react";

import {
  useConfig,
  useMe,
  useRepoSettings,
  useSaveRepoSettings,
  useSaveSpaceSettings,
  useSpaceSettings,
} from "../../../api/queries";
import {
  describeSaveError,
  fixFirst,
  pageStyles as pages,
  SaveBar,
  useDraft,
  useUnsavedWarning,
} from "../../../editor";
import { StatusLine } from "../../../ui";
import { useNow } from "../../../ui/useNow";
import { isForbidden, savedLine } from "../common";
import {
  repoIssueCount,
  repoRows,
  reposBody,
  sameRepoRows,
  sameSpaceForm,
  spaceBody,
  spaceForm,
  validateRepos,
  validateSpace,
} from "./model";
import { RepoList } from "./RepoList";
import s from "./Space.module.css";
import { SpaceFields } from "./SpaceFields";

const ADMINS_ONLY = "Only admins can change Space settings.";

export function SpacePage() {
  const me = useMe();
  const isAdmin = me.data?.role === "admin";
  const config = useConfig();
  const spaceDoc = useSpaceSettings(isAdmin);
  const reposDoc = useRepoSettings(isAdmin);
  const saveSpace = useSaveSpaceSettings();
  const saveRepos = useSaveRepoSettings();
  const now = useNow(60_000);

  const savedSpace = useMemo(
    () => (spaceDoc.data ? spaceForm(spaceDoc.data) : undefined),
    [spaceDoc.data],
  );
  const savedRepos = useMemo(
    () => (reposDoc.data ? repoRows(reposDoc.data) : undefined),
    [reposDoc.data],
  );
  const space = useDraft(savedSpace, spaceDoc.data?.version, sameSpaceForm, { warn: false });
  const repos = useDraft(savedRepos, reposDoc.data?.version, sameRepoRows, { warn: false });
  useUnsavedWarning(space.dirty || repos.dirty);

  if (me.isPending) return <StatusLine>Loading Space settings</StatusLine>;
  if (!isAdmin) return <StatusLine>{ADMINS_ONLY}</StatusLine>;
  const failed = spaceDoc.error ?? reposDoc.error ?? config.error;
  if (isForbidden(spaceDoc.error) || isForbidden(reposDoc.error)) {
    return <StatusLine>{ADMINS_ONLY}</StatusLine>;
  }
  if (failed)
    return <StatusLine error>{`Can't load Space settings · ${failed.message}`}</StatusLine>;
  if (!spaceDoc.data || !reposDoc.data || !config.data) {
    return <StatusLine>Loading Space settings</StatusLine>;
  }

  const spaceData = spaceDoc.data;
  const reposData = reposDoc.data;
  const spaceValue = space.value ?? spaceForm(spaceData);
  const repoValue = repos.value ?? repoRows(reposData);
  const repoNames = reposData.repos.map((r) => r.name);

  const spaceIssues = validateSpace(spaceValue, {
    jiraAuth: config.data.auth_mode === "jira",
    repoNames,
  });
  const repoIssues = validateRepos(repoValue);

  return (
    <div className={pages.page}>
      <p className={pages.banner} role="note">
        <span className={pages.k}>Space</span>
        Changes apply at once, without a restart. The Space and its repos save separately.
      </p>

      <section className={s.section} aria-label="Space">
        <SaveBar
          title="Space"
          meta={savedLine(spaceData, now)}
          dirty={space.dirty}
          readOnly={false}
          saveDisabledReason={fixFirst(Object.keys(spaceIssues).length)}
          saving={saveSpace.isPending}
          problem={saveSpace.error ? describeSaveError(saveSpace.error) : null}
          onDiscard={() => {
            space.discard();
            saveSpace.reset();
          }}
          onReload={() => {
            space.discard();
            saveSpace.reset();
            void spaceDoc.refetch();
          }}
          onSave={() => {
            saveSpace.mutate(spaceBody(spaceValue, space.baseVersion ?? spaceData.version), {
              onSuccess: () => {
                space.discard();
              },
            });
          }}
        />
        <SpaceFields
          value={spaceValue}
          issues={spaceIssues}
          readOnly={saveSpace.isPending}
          repoNames={repoNames}
          jiraAuth={config.data.auth_mode === "jira"}
          reposDirty={repos.dirty}
          onChange={(v) => {
            space.edit(v);
          }}
        />
      </section>

      <section className={s.section} aria-label="Repos">
        <SaveBar
          title="Repos"
          meta={savedLine(reposData, now)}
          dirty={repos.dirty}
          readOnly={false}
          saveDisabledReason={fixFirst(repoIssueCount(repoIssues))}
          saving={saveRepos.isPending}
          problem={saveRepos.error ? describeSaveError(saveRepos.error) : null}
          onDiscard={() => {
            repos.discard();
            saveRepos.reset();
          }}
          onReload={() => {
            repos.discard();
            saveRepos.reset();
            void reposDoc.refetch();
          }}
          onSave={() => {
            saveRepos.mutate(reposBody(repoValue, repos.baseVersion ?? reposData.version), {
              onSuccess: () => {
                repos.discard();
              },
            });
          }}
        />
        <p className={s.note}>
          Tickets refer to a repo by name, so a new URL or branch reaches every ticket that uses it.
          A name can&apos;t change: remove the repo and add it again.
        </p>
        <RepoList
          rows={repoValue}
          issues={repoIssues}
          readOnly={saveRepos.isPending}
          savedDefault={spaceData.default_repo}
          onChange={(rows) => {
            repos.edit(rows);
          }}
        />
      </section>
    </div>
  );
}
