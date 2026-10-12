import { cx } from "./cx";
import { personHue } from "./identity";
import hue from "./identity.module.css";
import { avatarSlot, initials } from "./initials";
import s from "./Avatar.module.css";

export type AvatarSize = 20 | 24 | 26 | 30;

/** identity = a flat `--cat-*` hue by person id (rows; refresh). talk = the pack's `--av-*` faces. */
export type AvatarPalette = "identity" | "talk";

export interface AvatarProps {
  /** Full name: initials on the face, full name in the title. */
  name: string;
  /** Person id: picks the colour (a stable hash over `--cat-*`, or `--av-1..4` by id % 4 for talk). */
  id: number;
  size?: AvatarSize | undefined;
  /** Default: `talk` at 26px (the Talk size), else `identity`. */
  palette?: AvatarPalette | undefined;
  className?: string | undefined;
}

const SLOT = { 1: undefined, 2: s.av2, 3: s.av3, 4: s.av4 } as const;

export function Avatar({ name, id, size = 20, palette, className }: AvatarProps) {
  const talk = (palette ?? (size === 26 ? "talk" : "identity")) === "talk";
  return (
    <span
      className={cx(
        s.av,
        size !== 20 && s[`s${String(size)}`],
        talk ? SLOT[avatarSlot(id)] : cx(s.identity, hue[personHue(id)]),
        className,
      )}
      role="img"
      aria-label={name}
      title={name}
    >
      {initials(name)}
    </span>
  );
}

export interface AvatarSlotProps {
  /** dashed = an empty face (Intake's "Unassigned"); else an invisible 20px spacer. */
  dashed?: boolean | undefined;
  className?: string | undefined;
}

/** The reserved 20px avatar slot (refresh: always reserved, so columns align). Decorative. */
export function AvatarSlot({ dashed = false, className }: AvatarSlotProps) {
  return <span className={cx(s.slot, dashed && s.empty, className)} aria-hidden="true" />;
}
