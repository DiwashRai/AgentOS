// The Jira project keys as chips plus an input: Enter, comma or leaving the field adds what was
// typed (several at once when pasted, upper-cased), × removes one. The server checks the keys; a
// chip it named in an issue is marked. Controlled; what's typed but not yet added stays local.
import { useState } from "react";

import { CloseIcon } from "../../../ui";
import { splitProjectKeys } from "./model";
import s from "./Space.module.css";

export interface ProjectKeysProps {
  id: string;
  describedBy: string | undefined;
  invalid: boolean;
  value: readonly string[];
  /** Whether the server named the key at this position in an issue. */
  flagged: (index: number) => boolean;
  readOnly: boolean;
  onChange: (keys: string[]) => void;
}

export function ProjectKeys(props: ProjectKeysProps) {
  const { value, readOnly, onChange } = props;
  const [text, setText] = useState("");

  const commit = () => {
    if (readOnly) return;
    const keys = splitProjectKeys(text);
    if (keys.length > 0) onChange([...value, ...keys]);
    setText("");
  };

  return (
    <div
      className={s.keys}
      data-invalid={props.invalid ? "" : undefined}
      data-readonly={readOnly ? "" : undefined}
    >
      {value.map((key, index) => (
        <span
          // The same key twice is the server's to report; the position keeps the chips apart.
          key={`${String(index)}-${key}`}
          className={s.chip}
          data-invalid={props.flagged(index) ? "" : undefined}
        >
          {key}
          {readOnly ? null : (
            <button
              type="button"
              aria-label={`Remove ${key}`}
              title={`Remove ${key}`}
              onClick={() => {
                onChange(value.filter((_, i) => i !== index));
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
