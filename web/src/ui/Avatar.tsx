import { avatarSlot, initials } from "./initials";
import s from "./Avatar.module.css";
import { cx } from "./cx";

export type AvatarSize = 20 | 24 | 26 | 30;

export interface AvatarProps {
  /** Full name: initials on the face, full name in the title. */
  name: string;
  /** Person id: picks the colour (`--av-1..4` by id % 4). */
  id: number;
  size?: AvatarSize | undefined;
  className?: string | undefined;
}

const SLOT = { 1: undefined, 2: s.av2, 3: s.av3, 4: s.av4 } as const;

export function Avatar({ name, id, size = 20, className }: AvatarProps) {
  return (
    <span
      className={cx(s.av, size !== 20 && s[`s${String(size)}`], SLOT[avatarSlot(id)], className)}
      role="img"
      aria-label={name}
      title={name}
    >
      {initials(name)}
    </span>
  );
}
