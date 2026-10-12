// The repo catalog: one card per repo (name, URL, default branch, open tickets), Add and Remove.
// A saved repo's name is fixed (rename = remove + add); only a new row's name is editable.
// Controlled; the page owns the draft and the save.
import { useEffect, useRef, useState } from "react";

import { Button, cx, Tag } from "../../../ui";
import { Field, fieldStyles as f } from "../../pipeline/fields";
import {
  newRepoRow,
  openTickets,
  removeBlock,
  urlHint,
  type RepoRow,
  type ReposValidation,
} from "./model";
import s from "./Space.module.css";

export interface RepoListProps {
  rows: readonly RepoRow[];
  issues: ReposValidation;
  readOnly: boolean;
  /** The *saved* Space default repo (the server refuses to remove it). */
  savedDefault: string | null;
  onChange: (rows: RepoRow[]) => void;
}

export function RepoList(props: RepoListProps) {
  const { rows, issues, readOnly, savedDefault, onChange } = props;
  const [focusKey, setFocusKey] = useState<string | null>(null);
  const names = useRef(new Map<string, HTMLInputElement>());

  useEffect(() => {
    if (focusKey === null) return;
    names.current.get(focusKey)?.focus();
    setFocusKey(null);
  }, [focusKey]);

  const patch = (key: string, p: Partial<RepoRow>) => {
    onChange(rows.map((r) => (r.key === key ? { ...r, ...p } : r)));
  };

  return (
    <>
      {rows.length === 0 ? (
        <p className={cx(s.repos, s.empty)}>
          No repos. Tickets run without a repo until one is added.
        </p>
      ) : (
        <ul className={s.repos}>
          {rows.map((row, i) => {
            const rowIssues = issues.rows[i] ?? {};
            const block = removeBlock(row, savedDefault);
            const label = row.fresh ? row.name.trim() || "new repo" : row.name;
            return (
              <li key={row.key} className={s.repo}>
                <div className={s.repoHead}>
                  <span className={cx(s.repoName, row.fresh && s.unnamed)}>
                    {row.fresh ? row.name.trim() || "New repo" : row.name}
                  </span>
                  {row.fresh ? (
                    <Tag>New</Tag>
                  ) : (
                    <span className={s.count}>{openTickets(row.open_ticket_count)}</span>
                  )}
                  {!row.fresh && row.name === savedDefault ? <Tag>Default</Tag> : null}
                  <span className={s.spacer} />
                  <Button
                    size="sm"
                    variant="quiet"
                    disabled={readOnly || block !== undefined}
                    title={block ?? `Remove ${label}`}
                    aria-label={`Remove ${label}`}
                    onClick={() => {
                      onChange(rows.filter((r) => r.key !== row.key));
                    }}
                  >
                    Remove
                  </Button>
                </div>
                <div className={s.repoFields} data-fresh={row.fresh ? "" : undefined}>
                  {row.fresh ? (
                    <Field
                      label="Name"
                      error={rowIssues.name}
                      hint="Tickets refer to it by this name. It can't change once saved."
                    >
                      {({ id, describedBy, invalid }) => (
                        <input
                          id={id}
                          ref={(el) => {
                            if (el) names.current.set(row.key, el);
                            else names.current.delete(row.key);
                          }}
                          className={cx(f.input, f.mono)}
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
                  <Field label="URL" error={rowIssues.url} hint={urlHint(row.url)}>
                    {({ id, describedBy, invalid }) => (
                      <input
                        id={id}
                        className={cx(f.input, f.mono)}
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
                  <Field label="Default branch" error={rowIssues.default_branch}>
                    {({ id, describedBy, invalid }) => (
                      <input
                        id={id}
                        className={cx(f.input, f.mono)}
                        value={row.default_branch}
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
              </li>
            );
          })}
        </ul>
      )}

      {issues.form.map((problem) => (
        <p key={problem} className={s.problem} role="alert">
          {problem}
        </p>
      ))}

      <Button
        size="sm"
        className={s.add}
        disabled={readOnly}
        onClick={() => {
          const row = newRepoRow(rows);
          onChange([...rows, row]);
          setFocusKey(row.key);
        }}
      >
        Add repo
      </Button>
    </>
  );
}
