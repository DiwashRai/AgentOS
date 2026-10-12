// The Template page's "Space defaults": the runtime and model every agent stage uses unless it
// sets its own. Controlled; saved with the template. Unset is null (never "").
import { cx, ZoneHead } from "../../ui";
import { Field, fieldStyles as f, Select } from "./fields";
import {
  CLI_DEFAULT,
  optional,
  runtimeOptions,
  serverRuntimeLabel,
  unsetIfEmpty,
  type Defaults,
  type DefaultsIssues,
} from "./model";
import s from "./pages.module.css";

export interface DefaultsSectionProps {
  value: Defaults;
  runtimes: readonly string[];
  /** The server's own runtime (`AppConfig.server_default_runtime`), named by the unset option. */
  serverRuntime: string;
  issues: DefaultsIssues;
  readOnly: boolean;
  onChange: (d: Defaults) => void;
}

export function DefaultsSection(props: DefaultsSectionProps) {
  const { value, runtimes, issues, readOnly, onChange } = props;
  const runtime = value.runtime ?? null;
  const model = value.model ?? null;
  const options = runtimeOptions(
    runtimes,
    serverRuntimeLabel({ server_default_runtime: props.serverRuntime }),
    runtime,
  );

  return (
    <section className={s.defaults} aria-labelledby="defaults-head">
      <ZoneHead
        id="defaults-head"
        title="Space defaults"
        note="Agent stages use these unless they set their own. The model applies only to stages on the Space runtime."
      />
      <div className={s.sheet}>
        <Field label="Runtime" error={issues.runtime}>
          {({ id, describedBy, invalid }) => (
            <Select
              id={id}
              aria-describedby={describedBy}
              aria-invalid={invalid}
              value={runtime ?? ""}
              disabled={readOnly}
              options={options}
              onChange={(v) => {
                onChange({ runtime: unsetIfEmpty(v), model });
              }}
            />
          )}
        </Field>
        <Field label="Model" error={issues.model}>
          {({ id, describedBy, invalid }) => (
            <input
              id={id}
              className={cx(f.input, f.mono)}
              value={model ?? ""}
              placeholder={CLI_DEFAULT}
              readOnly={readOnly}
              spellCheck={false}
              autoComplete="off"
              aria-describedby={describedBy}
              aria-invalid={invalid}
              onChange={(e) => {
                onChange({ runtime, model: optional(e.target.value) });
              }}
            />
          )}
        </Field>
      </div>
    </section>
  );
}
