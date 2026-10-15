// Settings › Field mappings: one mapping's rules, an ordered list (order = priority, the first match
// wins) on the kit's ListEditor. Each row is `[match] → [value]`. The row the current "Try it"
// matched is lit (the server says which; nothing here matches). Controlled.
import { useId } from "react";

import { errorAt, Input, ListEditor, messagesAt, type Issues } from "../../../editor";
import { cx, text } from "../../../ui";
import s from "./Mappings.module.css";
import {
  isBlankRule,
  newRule,
  ruleFieldName,
  rulePath,
  rulesPath,
  ruleWord,
  type RuleRow,
  type TargetSpec,
} from "./model";

export interface RulesEditorProps {
  spec: TargetSpec;
  mappingKey: string;
  rules: readonly RuleRow[];
  issues: Issues;
  /** The rule the current Try it result came from, if any. */
  matched: number | null;
  readOnly: boolean;
  onChange: (rules: readonly RuleRow[]) => void;
}

export function RulesEditor(props: RulesEditorProps) {
  const { spec, mappingKey, rules, issues, readOnly, onChange } = props;

  return (
    <div className={s.rules}>
      <div className={s.rulesHead}>
        <span className={s.k}>Rules</span>
        <span className={s.note}>
          First match wins. <code className={s.code}>*</code> matches any run of characters;
          everything else is literal, ignoring case.
        </span>
      </div>

      <ListEditor
        name={`${spec.title} rules`}
        ordered
        rows={rules}
        onChange={onChange}
        label={(_, index) => ruleWord(index).toLowerCase()}
        create={newRule}
        addLabel="Add rule"
        isBlank={isBlankRule}
        readOnly={readOnly}
        empty="No rules: every ticket is left blank."
        issue={messagesAt(issues, rulesPath(mappingKey))}
      >
        {(rule, ctx) => (
          <RuleLine
            spec={spec}
            rule={rule}
            index={ctx.index}
            issues={issues}
            mappingKey={mappingKey}
            matched={props.matched === ctx.index}
            readOnly={readOnly}
            onChange={(item) => {
              onChange(rules.map((r) => (r.key === rule.key ? { ...r, item } : r)));
            }}
          />
        )}
      </ListEditor>
    </div>
  );
}

interface RuleLineProps {
  spec: TargetSpec;
  rule: RuleRow;
  index: number;
  issues: Issues;
  mappingKey: string;
  matched: boolean;
  readOnly: boolean;
  onChange: (item: RuleRow["item"]) => void;
}

function RuleLine({
  spec,
  rule,
  index,
  issues,
  mappingKey,
  matched,
  readOnly,
  onChange,
}: RuleLineProps) {
  const id = useId();
  const word = ruleWord(index);
  const matchError = errorAt(issues, rulePath(mappingKey, rule.key, "match"));
  const valueError = errorAt(issues, rulePath(mappingKey, rule.key, "value"));
  const ruleErrors = messagesAt(issues, rulePath(mappingKey, rule.key));
  const matchId = `${id}-match`;
  const valueId = `${id}-value`;

  return (
    <div className={cx(s.rule, matched && s.matched)} data-matched={matched || undefined}>
      <Input
        mono
        value={rule.item.match}
        placeholder="version_1*"
        readOnly={readOnly}
        spellCheck={false}
        autoComplete="off"
        aria-label={`${word}: ${ruleFieldName(spec, "match")}`}
        aria-invalid={matchError !== undefined}
        aria-describedby={matchError === undefined ? undefined : matchId}
        onChange={(e) => {
          onChange({ ...rule.item, match: e.target.value });
        }}
      />
      <span className={s.arrow} aria-hidden="true">
        →
      </span>
      <Input
        mono
        value={rule.item.value}
        placeholder={spec.valuePlaceholder}
        readOnly={readOnly}
        spellCheck={false}
        autoComplete="off"
        aria-label={`${word}: ${ruleFieldName(spec, "value")}`}
        aria-invalid={valueError !== undefined}
        aria-describedby={valueError === undefined ? undefined : valueId}
        onChange={(e) => {
          onChange({ ...rule.item, value: e.target.value });
        }}
      />
      <span className={s.hit}>{matched ? "Matched" : null}</span>
      {matchError === undefined ? null : (
        <span id={matchId} className={cx(text.error, s.matchError)}>
          {matchError}
        </span>
      )}
      {valueError === undefined ? null : (
        <span id={valueId} className={cx(text.error, s.valueError)}>
          {valueError}
        </span>
      )}
      {ruleErrors.map((msg, i) => (
        <span key={`${String(i)}:${msg}`} className={cx(text.error, s.ruleError)} role="alert">
          {msg}
        </span>
      ))}
    </div>
  );
}
