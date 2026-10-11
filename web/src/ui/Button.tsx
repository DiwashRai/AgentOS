import type { ComponentPropsWithRef } from "react";
import s from "./Button.module.css";
import { cx } from "./cx";
import { ArrowIcon } from "./icons";

export type ButtonVariant = "default" | "ink" | "accent" | "quiet";

export interface ButtonProps extends ComponentPropsWithRef<"button"> {
  /** ink = primary. accent is only ever Dispatch run, only on a ticket (gets the nudging arrow). */
  variant?: ButtonVariant | undefined;
  size?: "sm" | "md" | "lg" | undefined;
}

export function Button({
  variant = "default",
  size = "md",
  type = "button",
  className,
  children,
  ...rest
}: ButtonProps) {
  return (
    <button
      type={type}
      className={cx(
        s.btn,
        variant !== "default" && s[variant],
        size !== "md" && s[size],
        className,
      )}
      {...rest}
    >
      {children}
      {variant === "accent" ? <ArrowIcon /> : null}
    </button>
  );
}

export interface IconButtonProps extends ComponentPropsWithRef<"button"> {
  /** Accessible name (also the native tooltip). */
  label: string;
  /** No border or fill until hovered. */
  bare?: boolean | undefined;
}

export function IconButton({
  label,
  bare = false,
  type = "button",
  className,
  title,
  children,
  ...rest
}: IconButtonProps) {
  return (
    <button
      type={type}
      aria-label={label}
      title={title ?? label}
      className={cx(s.ib, bare && s.bare, className)}
      {...rest}
    >
      {children}
    </button>
  );
}
