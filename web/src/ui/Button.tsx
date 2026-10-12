import type { ComponentPropsWithRef } from "react";
import s from "./Button.module.css";
import { cx } from "./cx";
import { ArrowIcon } from "./icons";

/**
 * default = neutral (raised, `--rule` edge) · ink = a primary that isn't a gate (Retry, forms) ·
 * you = the gate primary (Approve, Complete; blue fill + `--you-ring` halo) · accent = only ever
 * Dispatch run, only on a ticket (gets the nudging arrow) · quiet = transparent, 500 · outline =
 * transparent with an inset `--rule-strong` edge (Take in, Add context) · danger = confirming a
 * destructive choice (Abandon), never a resting button.
 */
export type ButtonVariant = "default" | "ink" | "you" | "accent" | "quiet" | "outline" | "danger";

export interface ButtonProps extends ComponentPropsWithRef<"button"> {
  variant?: ButtonVariant | undefined;
  /** xs 24 (Add context) · sm 28 · md 34 (default) · lg 40. */
  size?: "xs" | "sm" | "md" | "lg" | undefined;
  /**
   * Fill like the `you` button while the row around it is hovered or keyboard-focused (Take in).
   * The row carries `litRowClass`. Same width and weight at rest and lit.
   */
  litOnRow?: boolean | undefined;
}

/** Put on a row whose `litOnRow` button should fill blue on the row's hover / focus. */
export const litRowClass: string = s.litRow ?? "";

export function Button({
  variant = "default",
  size = "md",
  litOnRow = false,
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
        litOnRow && s.lit,
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
