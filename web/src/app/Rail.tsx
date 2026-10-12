// The left rail (DESIGN §4, kit.js Kit.rail; the refresh's dark brand rail in both themes). Order:
// logo · All teams · My queue · Compact · Intake · spacer · Theme · Settings · avatar. Sources is
// omitted (no sources yet). Labels are native titles plus aria-label; nothing is revealed on hover.
import type { ReactNode } from "react";
import { NavLink } from "react-router";

import { Avatar } from "../ui";
import { CompactIcon, IntakeIcon, QueueIcon, SettingsIcon, TeamsIcon } from "./icons";
import s from "./Rail.module.css";
import { ThemeMenu } from "./ThemeMenu";
import { useBoard } from "./useBoard";

function RailLink(props: {
  to: string;
  label: string;
  badge?: boolean;
  /** Also current on sub-paths (Settings covers /settings/*). */
  section?: boolean;
  children: ReactNode;
}) {
  return (
    <NavLink
      to={props.to}
      end={!props.section}
      className={s.link}
      aria-label={props.label}
      title={props.label}
    >
      {props.children}
      {props.badge && <span className={s.badge} />}
    </NavLink>
  );
}

export function Rail() {
  const { me, counts } = useBoard();
  return (
    <nav className={s.rail} aria-label="Views">
      {/* The logo: the accent's one other place (refresh: accent = Dispatch run and the logo). */}
      <div className={s.logo} aria-hidden="true">
        <i />
      </div>
      <RailLink to="/teams" label="All teams">
        <TeamsIcon />
      </RailLink>
      <RailLink to="/" label="My queue" badge={counts.yourMove > 0}>
        <QueueIcon />
      </RailLink>
      <RailLink to="/compact" label="Compact">
        <CompactIcon />
      </RailLink>
      {/* No badge: new candidates aren't "Needs you". */}
      <RailLink to="/intake" label="Intake">
        <IntakeIcon />
      </RailLink>
      <div className={s.spacer} />
      <ThemeMenu />
      <RailLink to="/settings" label="Settings" section>
        <SettingsIcon />
      </RailLink>
      {me && (
        <Avatar
          className={s.avatar}
          name={me.display_name}
          id={me.id}
          size={30}
          palette="identity"
        />
      )}
    </nav>
  );
}
