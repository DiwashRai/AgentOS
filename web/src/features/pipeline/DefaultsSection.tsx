// The "Space defaults" page's fields: the runtime, model and effort every agent stage uses unless
// it sets its own. Controlled; saved with the template. Unset is null (never "").
import { Field, fieldStyles as f, Select } from "../../editor";
import { cx, ZoneHead } from "../../ui";
import {
  CLI_DEFAULT,
  optional,
  runtimeOptions,
  serverRuntimeLabel,
  unsetIfEmpty,
  type Defaults,
  type DefaultsBody,
  type DefaultsIssues,
} from "./model";
import s from "./pipelinePages.module.css";

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
  const current: DefaultsBody = {
    runtime: value.runtime ?? null,
    model: value.model ?? null,
    effort: value.effort ?? null,
  };
  const options = runtimeOptions(
    runtimes,
    serverRuntimeLabel({ server_default_runtime: props.serverRuntime }),
    current.runtime,
  );
  const set = (patch: Partial<DefaultsBody>) => {
    onChange({ ...current, ...patch });
  };

  return (
    <section className={s.defaults} aria-labelledby="defaults-head">
      <ZoneHead
        id="defaults-head"
        title="Space defaults"
        note="Agent stages use these unless they set their own. The model and effort apply only to stages on the Space runtime."
      />
      <div className={s.sheet}>
        <Field label="Runtime" error={issues.runtime}>
          {({ id, describedBy, invalid }) => (
            <Select
              id={id}
              aria-describedby={describedBy}
              aria-invalid={invalid}
              value={current.runtime ?? ""}
              disabled={readOnly}
              options={options}
              onChange={(v) => {
                set({ runtime: unsetIfEmpty(v) });
              }}
            />
          )}
        </Field>
        <Field label="Model" error={issues.model}>
          {({ id, describedBy, invalid }) => (
            <input
              id={id}
              className={cx(f.input, f.mono)}
              value={current.model ?? ""}
              placeholder={CLI_DEFAULT}
              readOnly={readOnly}
              spellCheck={false}
              autoComplete="off"
              aria-describedby={describedBy}
              aria-invalid={invalid}
              onChange={(e) => {
                set({ model: optional(e.target.value) });
              }}
            />
          )}
        </Field>
        <Field label="Effort" error={issues.effort}>
          {({ id, describedBy, invalid }) => (
            <input
              id={id}
              className={cx(f.input, f.mono)}
              value={current.effort ?? ""}
              placeholder={CLI_DEFAULT}
              readOnly={readOnly}
              spellCheck={false}
              autoComplete="off"
              aria-describedby={describedBy}
              aria-invalid={invalid}
              onChange={(e) => {
                set({ effort: optional(e.target.value) });
              }}
            />
          )}
        </Field>
      </div>
    </section>
  );
}
