// The repo catalog on the kit's ListEditor: one row per repo (name, URL, default branch, open
// tickets), Add and Remove. A saved repo's name is fixed (rename = remove + add); only a new row's
// name is editable. Removing a repo still in use is refused by the server on save (409 `in_use`);
// the confirmation shows its open-ticket count. Controlled; the page owns the document and the save.
import {
  errorAt,
  Field,
  Input,
  ListEditor,
  messagesAt,
  pathOf,
  type Issues,
} from "../../../editor";
import { cx, Tag, text } from "../../../ui";
import {
  isBlankRepo,
  newRepoRow,
  openTickets,
  removeText,
  repoLabel,
  urlHint,
  type RepoRow,
  type ReposForm,
} from "./model";
import s from "./Space.module.css";

export interface RepoListProps {
  value: ReposForm;
  issues: Issues;
  readOnly: boolean;
  /** Open tickets per saved repo name, as the server counted them. */
  openCounts: Readonly<Record<string, number>>;
  /** The saved Space default repo (tagged on its row). */
  savedDefault: string | null;
  onChange: (value: ReposForm) => void;
}

export function RepoList(props: RepoListProps) {
  const { value, issues, readOnly, openCounts, savedDefault, onChange } = props;
  const countOf = (row: RepoRow) => (row.fresh ? 0 : (openCounts[row.name] ?? 0));

  const patch = (key: string, p: Partial<RepoRow>) => {
    onChange({ repos: value.repos.map((r) => (r.key === key ? { ...r, ...p } : r)) });
  };

  return (
    <ListEditor
      name="Repos"
      className={s.repos}
      rows={value.repos}
      readOnly={readOnly}
      label={repoLabel}
      create={newRepoRow}
      addLabel="Add repo"
      isBlank={isBlankRepo}
      confirmText={(row, label) => removeText(label, countOf(row))}
      empty="No repos. Tickets run without a repo until one is added."
      issue={messagesAt(issues, "repos")}
      onChange={(repos) => {
        onChange({ repos });
      }}
    >
      {(row) => {
        const at = (field?: string) =>
          field === undefined ? pathOf("repos", row.key) : pathOf("repos", row.key, field);
        // A saved row has no name field, so issues about its name show beside it.
        const rowIssues = [
          ...messagesAt(issues, at()),
          ...(row.fresh ? [] : messagesAt(issues, at("name"))),
        ];
        return (
          <div className={s.repo}>
            <div className={s.repoHead}>
              <span className={cx(s.repoName, row.fresh && s.unnamed)}>
                {row.fresh ? row.name.trim() || "New repo" : row.name}
              </span>
              {row.fresh ? (
                <Tag>New</Tag>
              ) : (
                <span className={s.count}>{openTickets(countOf(row))}</span>
              )}
              {!row.fresh && row.name === savedDefault ? <Tag>Default</Tag> : null}
            </div>

            {rowIssues.map((msg) => (
              <p key={msg} className={text.error} role="alert">
                {msg}
              </p>
            ))}

            <div className={s.repoFields} data-fresh={row.fresh ? "" : undefined}>
              {row.fresh ? (
                <Field
                  label="Name"
                  error={errorAt(issues, at("name"))}
                  hint="Tickets refer to it by this name. It can't change once saved."
                >
                  {({ id, describedBy, invalid }) => (
                    <Input
                      id={id}
                      mono
                      value={row.name}
                      readOnly={readOnly}
                      placeholder="e.g. agentos"
                      spellCheck={false}
                      autoComplete="off"
                      aria-describedby={describedBy}
                      aria-invalid={invalid}
                      onChange={(e) => {
                        patch(row.key, { name: e.target.value.toLowerCase() });
                      }}
                    />
                  )}
                </Field>
              ) : null}
              <Field label="URL" error={errorAt(issues, at("url"))} hint={urlHint(row.url)}>
                {({ id, describedBy, invalid }) => (
                  <Input
                    id={id}
                    mono
                    value={row.url}
                    readOnly={readOnly}
                    placeholder="https://github.com/owner/repo.git"
                    spellCheck={false}
                    autoComplete="off"
                    aria-describedby={describedBy}
                    aria-invalid={invalid}
                    onChange={(e) => {
                      patch(row.key, { url: e.target.value });
                    }}
                  />
                )}
              </Field>
              <Field label="Default branch" error={errorAt(issues, at("default_branch"))}>
                {({ id, describedBy, invalid }) => (
                  <Input
                    id={id}
                    mono
                    value={row.default_branch}
                    placeholder="None: each ticket sets its own"
                    readOnly={readOnly}
                    spellCheck={false}
                    autoComplete="off"
                    aria-describedby={describedBy}
                    aria-invalid={invalid}
                    onChange={(e) => {
                      patch(row.key, { default_branch: e.target.value });
                    }}
                  />
                )}
              </Field>
            </div>
          </div>
        );
      }}
    </ListEditor>
  );
}
