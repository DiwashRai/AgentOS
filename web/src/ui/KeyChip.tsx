import { cx } from "./cx";
import { keyHue } from "./identity";
import hue from "./identity.module.css";
import s from "./KeyChip.module.css";

export interface KeyChipProps {
  /** A ticket key ("AOS-14") or a bare project key ("SCRUM"). The hue follows the project prefix. */
  children: string;
  /** sm 18px (group heads), md 20px (rows; default), lg 24px (the Ticket page kicker tag). */
  size?: "sm" | "md" | "lg" | undefined;
  title?: string | undefined;
  className?: string | undefined;
}

/** The project-tinted key chip (refresh mock §4.6): mono 11.5/600 on the project's `--cat-*` tint. */
export function KeyChip({ children, size = "md", title, className }: KeyChipProps) {
  return (
    <span
      className={cx(s.chip, hue[keyHue(children)], size !== "md" && s[size], className)}
      title={title}
    >
      {children}
    </span>
  );
}
