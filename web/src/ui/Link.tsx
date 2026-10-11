import type { ComponentPropsWithRef } from "react";
import { cx } from "./cx";
import s from "./Link.module.css";

/** The link style, for router links: `<RouterLink className={linkClass} …>`. */
export const linkClass: string = s.link ?? "";

/** An underlined text link (`<a>`). */
export function Link({ className, children, ...rest }: ComponentPropsWithRef<"a">) {
  return (
    <a className={cx(s.link, className)} {...rest}>
      {children}
    </a>
  );
}

/** A button that looks like a Link (an action, not a navigation). */
export function LinkButton({
  className,
  type = "button",
  ...rest
}: ComponentPropsWithRef<"button">) {
  return <button type={type} className={cx(s.link, className)} {...rest} />;
}
