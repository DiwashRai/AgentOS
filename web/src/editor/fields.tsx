// Form controls in kit values (.ao-input, .ao-select, .ao-check): a labelled Field that wires its
// control to the label, hint and error, and the Input / Textarea / Select / Toggle controls.
import { useId, type ComponentPropsWithRef, type ReactNode } from "react";

import { ChevronIcon, cx, text } from "../ui";
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
      <label className={text.label} htmlFor={id}>
        {label}
      </label>
      {children({ id, describedBy: note ? noteId : undefined, invalid: error !== undefined })}
      {note ? (
        <span id={noteId} className={error !== undefined ? text.error : text.meta}>
          {note}
        </span>
      ) : null}
    </div>
  );
}

type InputProps = ComponentPropsWithRef<"input"> & {
  /** Monospace text (ids, branches, commands). */
  mono?: boolean | undefined;
};

/** A native input in the kit's `.ao-input` look; every other prop (and the ref) goes through. */
export function Input({ mono, className, ...rest }: InputProps) {
  return <input {...rest} className={cx(s.input, mono && s.mono, className)} />;
}

type TextareaProps = ComponentPropsWithRef<"textarea">;

/** A native textarea in the kit's monospace editor look; every other prop (and the ref) goes through. */
export function Textarea({ className, ...rest }: TextareaProps) {
  return <textarea {...rest} className={cx(s.textarea, className)} />;
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
