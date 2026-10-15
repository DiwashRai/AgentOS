// Settings › Field mappings: one mapping's rules as an ordered list (order = priority, the first
// match wins). Each row is `[match] → [value]`, moves up/down and is removed. The row the last
// "Try it" matched is lit (the server says which; nothing here matches). Controlled.
import { useEffect, useId, useRef, useState } from "react";

import { fieldStyles as f } from "../../../editor";
import { Button, ChevronIcon, cx, IconButton } from "../../../ui";
import s from "./Mappings.module.css";
import {
  addRule,
  MAX_RULES,
  moveRule,
  patchRule,
  removeRule,
  ruleWord,
  type RuleIssues,
  type RuleRow,
  type TargetSpec,
} from "./model";

export interface RulesEditorProps {
  spec: TargetSpec;
  rules: readonly RuleRow[];
  issues: readonly RuleIssues[];
  form: readonly string[];
  /** The rule the current Try it result came from, if any. */
  matched: number | null;
  readOnly: boolean;
  onChange: (rules: readonly RuleRow[]) => void;
}

export function RulesEditor({
  spec,
  rules,
  issues,
  form,
  matched,
  readOnly,
  onChange,
}: RulesEditorProps) {
  const prefix = useId();
  const [focusKey, setFocusKey] = useState<string | null>(null);
  const moveId = (key: string, delta: -1 | 1) => `${prefix}-${key}-${delta < 0 ? "up" : "down"}`;
  const atLimit = rules.length >= MAX_RULES;

  const move = (key: string, index: number, delta: -1 | 1) => {
    onChange(moveRule(rules, index, delta));
    // Reordering can drop focus from the moved row's arrow; put it back (or on the other arrow,
    // if this one is now disabled at an end).
    const to = index + delta;
    const atEnd = delta < 0 ? to === 0 : to === rules.length - 1;
    const target = moveId(key, atEnd ? (delta < 0 ? 1 : -1) : delta);
    requestAnimationFrame(() => {
      document.getElementById(target)?.focus();
    });
  };

  return (
    <div className={s.rules}>
      <div className={s.rulesHead}>
        <span className={s.k}>Rules</span>
        <span className={s.note}>
          First match wins. <code className={s.code}>*</code> matches any run of characters;
          everything else is literal, ignoring case.
        </span>
      </div>

      {form.map((msg) => (
        <p key={msg} className={s.formError} role="alert">
          {msg}
        </p>
      ))}

      {rules.length === 0 ? (
        <p className={s.none}>No rules: every ticket is left blank.</p>
      ) : (
        <ol className={s.ruleList} aria-label="Rules">
          <li className={s.colHead} aria-hidden="true">
            <span />
            <span>Jira value matches</span>
            <span />
            <span>{spec.valueLabel}</span>
            <span />
          </li>
          {rules.map((rule, index) => (
            <RuleLine
              key={rule.key}
              rule={rule}
              index={index}
              count={rules.length}
              issues={issues[index] ?? {}}
              spec={spec}
              matched={matched === index}
              readOnly={readOnly}
              focus={focusKey === rule.key}
              moveId={moveId}
              onPatch={(patch) => {
                onChange(patchRule(rules, index, patch));
              }}
              onMove={(delta) => {
                move(rule.key, index, delta);
              }}
              onRemove={() => {
                onChange(removeRule(rules, index));
              }}
            />
          ))}
        </ol>
      )}

      {readOnly ? null : (
        <div className={s.adds}>
          <Button
            size="sm"
            disabled={atLimit}
            title={atLimit ? `At most ${String(MAX_RULES)} rules` : undefined}
            onClick={() => {
              const next = addRule(rules);
              setFocusKey(next[next.length - 1]?.key ?? null);
              onChange(next);
            }}
          >
            + Add rule
          </Button>
        </div>
      )}
    </div>
  );
}

interface RuleLineProps {
  rule: RuleRow;
  index: number;
  count: number;
  issues: RuleIssues;
  spec: TargetSpec;
  matched: boolean;
  readOnly: boolean;
  focus: boolean;
  moveId: (key: string, delta: -1 | 1) => string;
  onPatch: (patch: Partial<Pick<RuleRow, "match" | "value">>) => void;
  onMove: (delta: -1 | 1) => void;
  onRemove: () => void;
}

function RuleLine(props: RuleLineProps) {
  const { rule, index, count, issues, spec, readOnly } = props;
  const id = useId();
  const matchRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (props.focus) matchRef.current?.focus();
  }, [props.focus]);

  const word = ruleWord(index);
  const errors = [issues.match, issues.value].filter((e) => e !== undefined);
  const errId = `${id}-err`;

  return (
    <li
      className={cx(s.rule, props.matched && s.matched)}
      data-matched={props.matched || undefined}
    >
      <span className={s.handle}>
        {readOnly ? null : (
          <span className={s.move}>
            <IconButton
              id={props.moveId(rule.key, -1)}
              bare
              className={s.up}
              label={`Move ${word.toLowerCase()} up`}
              disabled={index === 0}
              onClick={() => {
                props.onMove(-1);
              }}
            >
              <ChevronIcon />
            </IconButton>
            <IconButton
              id={props.moveId(rule.key, 1)}
              bare
              label={`Move ${word.toLowerCase()} down`}
              disabled={index === count - 1}
              onClick={() => {
                props.onMove(1);
              }}
            >
              <ChevronIcon />
            </IconButton>
          </span>
        )}
        <span className={s.num} aria-hidden="true">
          {String(index + 1).padStart(2, "0")}
        </span>
      </span>
      <input
        ref={matchRef}
        className={cx(f.input, f.mono)}
        value={rule.match}
        placeholder="version_1*"
        readOnly={readOnly}
        spellCheck={false}
        autoComplete="off"
        aria-label={`${word}: Jira value matches`}
        aria-invalid={issues.match !== undefined}
        aria-describedby={errors.length > 0 ? errId : undefined}
        onChange={(e) => {
          props.onPatch({ match: e.target.value });
        }}
      />
      <span className={s.arrow} aria-hidden="true">
        →
      </span>
      <input
        className={cx(f.input, f.mono)}
        value={rule.value}
        placeholder={spec.valuePlaceholder}
        readOnly={readOnly}
        spellCheck={false}
        autoComplete="off"
        aria-label={`${word}: ${spec.valueLabel.toLowerCase()}`}
        aria-invalid={issues.value !== undefined}
        aria-describedby={errors.length > 0 ? errId : undefined}
        onChange={(e) => {
          props.onPatch({ value: e.target.value });
        }}
      />
      <span className={s.remove}>
        {props.matched ? <span className={s.hit}>Matched</span> : null}
        {readOnly ? null : (
          <Button
            size="sm"
            variant="quiet"
            aria-label={`Remove ${word.toLowerCase()}`}
            onClick={props.onRemove}
          >
            Remove
          </Button>
        )}
      </span>
      {errors.length > 0 ? (
        <span id={errId} className={s.ruleErrors}>
          <span className={s.ruleError}>{issues.match ?? ""}</span>
          <span className={s.ruleError}>{issues.value ?? ""}</span>
        </span>
      ) : null}
    </li>
  );
}
