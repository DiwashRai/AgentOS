// The Jira project keys as chips plus an input: Enter, comma or leaving the field adds what was
// typed (several at once when pasted), × removes one. Controlled; what's typed but not yet added
// stays local.
import { useState } from "react";

import { CloseIcon } from "../../../ui";
import { addProjectKeys } from "./model";
import s from "./Space.module.css";

export interface ProjectKeysProps {
  id: string;
  describedBy: string | undefined;
  invalid: boolean;
  value: readonly string[];
  readOnly: boolean;
  onChange: (keys: string[]) => void;
  /** A problem with what was typed (null clears it); shown by the surrounding Field. */
  onTypingError: (error: string | null) => void;
}

export function ProjectKeys(props: ProjectKeysProps) {
  const { value, readOnly, onChange, onTypingError } = props;
  const [text, setText] = useState("");

  const commit = () => {
    if (readOnly) return;
    if (text.trim() === "") {
      onTypingError(null);
      return;
    }
    const { keys, error, rest } = addProjectKeys(text, value);
    if (keys.length > 0) onChange([...value, ...keys]);
    setText(rest);
    onTypingError(error ?? null);
  };

  return (
    <div
      className={s.keys}
      data-invalid={props.invalid ? "" : undefined}
      data-readonly={readOnly ? "" : undefined}
    >
      {value.map((key) => (
        <span key={key} className={s.chip}>
          {key}
          {readOnly ? null : (
            <button
              type="button"
              aria-label={`Remove ${key}`}
              title={`Remove ${key}`}
              onClick={() => {
                onChange(value.filter((k) => k !== key));
              }}
            >
              <CloseIcon />
            </button>
          )}
        </span>
      ))}
      <input
        id={props.id}
        className={s.keyInput}
        value={text}
        readOnly={readOnly}
        placeholder={
          readOnly
            ? undefined
            : value.length === 0
              ? "Add a project key, e.g. SCRUM"
              : "Add another"
        }
        spellCheck={false}
        autoComplete="off"
        aria-describedby={props.describedBy}
        aria-invalid={props.invalid}
        onChange={(e) => {
          setText(e.target.value.toUpperCase());
          onTypingError(null);
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === ",") {
            e.preventDefault();
            commit();
          }
        }}
        onBlur={commit}
      />
    </div>
  );
}
