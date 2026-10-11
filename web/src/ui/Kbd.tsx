import type { ReactNode } from "react";
import { cx } from "./cx";
import s from "./Kbd.module.css";

export function Kbd({
  children,
  className,
}: {
  children: ReactNode;
  className?: string | undefined;
}) {
  return <kbd className={cx(s.kbd, className)}>{children}</kbd>;
}
