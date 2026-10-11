// Small form controls for the pipeline editor, in kit values (.ao-input, .ao-select, .ao-check).
// Not in src/ui because the pack doesn't design forms; promote them if other features need them.
import { useId, type ReactNode } from "react";

import { ChevronIcon, cx } from "../../ui";
import s from "./fields.module.css";

interface FieldProps {
  label: string;
  /** Reading aid under the control (hidden while there is an error). */
  hint?: ReactNode;
  error?: string | undefined;
  className?: string | undefined;
  /** Renders the control, wired to the label, hint and error. */
  children: (ids: { id: string; describedBy: string | undefined; invalid: boolean }) => ReactNode;
}

export function Field({ label, hint, error, className, children }: FieldProps) {
  const id = useId();
  const noteId = `${id}-note`;
  const note = error ?? hint;
  return (
    <div className={cx(s.field, className)}>
      <label className={s.label} htmlFor={id}>
        {label}
      </label>
      {children({ id, describedBy: note ? noteId : undefined, invalid: error !== undefined })}
      {note ? (
        <span id={noteId} className={cx(s.note, error !== undefined && s.error)}>
          {note}
        </span>
      ) : null}
    </div>
  );
}

interface SelectProps {
  id?: string | undefined;
  value: string;
  onChange: (value: string) => void;
  options: readonly { value: string; label: string }[];
  disabled?: boolean | undefined;
  "aria-label"?: string | undefined;
  "aria-describedby"?: string | undefined;
  "aria-invalid"?: boolean | undefined;
  size?: "sm" | "md" | undefined;
  className?: string | undefined;
}

/** A native select dressed as the kit's `.ao-select` (the chevron is drawn over it). */
export function Select({ value, onChange, options, size = "md", className, ...rest }: SelectProps) {
  return (
    <span className={cx(s.select, size === "sm" && s.sm, className)}>
      <select
        {...rest}
        value={value}
        onChange={(e) => {
          onChange(e.target.value);
        }}
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
      <ChevronIcon />
    </span>
  );
}

interface ToggleProps {
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean | undefined;
  /** Visible text. */
  children: ReactNode;
  /** Accessible name when the visible text needs context (e.g. which stage). */
  "aria-label"?: string | undefined;
  title?: string | undefined;
  className?: string | undefined;
}

/** A labelled native checkbox (kit `.ao-check`). */
export function Toggle({
  checked,
  onChange,
  disabled,
  children,
  title,
  className,
  ...rest
}: ToggleProps) {
  return (
    <label className={cx(s.check, disabled && s.isDisabled, className)} title={title}>
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        aria-label={rest["aria-label"]}
        onChange={(e) => {
          onChange(e.target.checked);
        }}
      />
      {children}
    </label>
  );
}

export { s as fieldStyles };
