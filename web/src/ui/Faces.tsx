import { Avatar, type AvatarSize } from "./Avatar";
import { cx } from "./cx";
import s from "./Faces.module.css";

export interface FacesProps {
  people: { id: number; name: string }[];
  /** Show at most this many faces (the title still names everyone). Default 4. */
  max?: number | undefined;
  size?: AvatarSize | undefined;
  className?: string | undefined;
}

export function Faces({ people, max = 4, size, className }: FacesProps) {
  const names = people.map((p) => p.name).join(", ");
  return (
    <span className={cx(s.faces, className)} role="group" aria-label={names} title={names}>
      {people.slice(0, max).map((p) => (
        <Avatar key={p.id} name={p.name} id={p.id} size={size} />
      ))}
    </span>
  );
}
