// Settings › Field mappings: "Try it" runs the unsaved rules through the server's matcher, on a
// Jira issue's field or on typed values, and shows what was read, the rule that matched and the
// result (or why it's blank). Controlled: the card owns the input and the preview mutation.
import { useId } from "react";

import { fieldStyles as f } from "../../../editor";
import { Button, cx } from "../../../ui";
import s from "./Mappings.module.css";
import { ruleWord, type TargetSpec, type TryLine, type TryMode } from "./model";

const MODES: readonly { mode: TryMode; label: string; placeholder: string; hint: string }[] = [
  {
    mode: "issue",
    label: "Issue",
    placeholder: "PLAT-123 or a link to it",
    hint: "Reads the field from Jira now.",
  },
  {
    mode: "values",
    label: "Value",
    placeholder: "version_1.0, version_1.2",
    hint: "Several values: separate them with commas.",
  },
];

export interface TryItProps {
  spec: TargetSpec;
  fieldName: string;
  mode: TryMode;
  text: string;
  line: TryLine;
  /** Why Try it is off, or undefined. */
  block: string | undefined;
  onMode: (mode: TryMode) => void;
  onText: (text: string) => void;
  onRun: () => void;
}

export function TryIt(props: TryItProps) {
  const id = useId();
  const current = MODES.find((m) => m.mode === props.mode) ?? MODES[0];
  const pending = props.line.kind === "pending";
  const disabled = props.block !== undefined || pending;

  return (
    <div className={s.try} role="group" aria-labelledby={`${id}-k`}>
      <div className={s.tryHead}>
        <span id={`${id}-k`} className={s.k}>
          Try it
        </span>
        <span className={s.note}>Runs these rules as they are now, before you save.</span>
      </div>
      <form
        className={s.tryBar}
        onSubmit={(e) => {
          e.preventDefault();
          if (!disabled) props.onRun();
        }}
      >
        <span className={s.seg} role="radiogroup" aria-label="Try it on">
          {MODES.map((m) => (
            <button
              key={m.mode}
              type="button"
              role="radio"
              aria-checked={props.mode === m.mode}
              className={s.segBtn}
              onClick={() => {
                props.onMode(m.mode);
              }}
            >
              {m.label}
            </button>
          ))}
        </span>
        <input
          className={cx(f.input, f.mono, s.tryInput)}
          value={props.text}
          placeholder={current?.placeholder}
          spellCheck={false}
          autoComplete="off"
          aria-label={props.mode === "issue" ? "Jira issue key or link" : "Jira values"}
          aria-describedby={`${id}-hint`}
          onChange={(e) => {
            props.onText(e.target.value);
          }}
        />
        <Button type="submit" size="sm" disabled={disabled} title={props.block}>
          {pending ? "Trying…" : "Try it"}
        </Button>
      </form>
      <span id={`${id}-hint`} className={s.note}>
        {props.mode === "issue" && props.fieldName
          ? `Reads ${props.fieldName} from Jira now.`
          : current?.hint}
      </span>
      <TryResult spec={props.spec} line={props.line} />
    </div>
  );
}

function TryResult({ spec, line }: { spec: TargetSpec; line: TryLine }) {
  switch (line.kind) {
    case "none":
      return null;
    case "pending":
      return (
        <p className={s.k} role="status">
          Asking the server…
        </p>
      );
    case "error":
      return (
        <div className={s.result} role="status">
          <p className={s.resultError}>{line.text}</p>
        </div>
      );
    case "result":
      return (
        <dl className={s.result} role="status">
          <dt className={s.k}>Read</dt>
          <dd>
            {line.read.length === 0 ? (
              <span className={s.muted}>Nothing (the field is empty)</span>
            ) : (
              line.read.map((v, i) => (
                <span key={`${String(i)}:${v}`} className={s.val}>
                  {v}
                </span>
              ))
            )}
          </dd>
          <dt className={s.k}>{spec.title}</dt>
          <dd>
            {line.value !== null ? (
              <>
                <span className={s.branch}>{line.value}</span>
                {line.rule !== null ? (
                  <span className={s.muted}> · from {ruleWord(line.rule).toLowerCase()}</span>
                ) : null}
              </>
            ) : (
              <span className={s.blank}>Blank{line.reason ? ` · ${line.reason}` : ""}</span>
            )}
          </dd>
        </dl>
      );
  }
}
