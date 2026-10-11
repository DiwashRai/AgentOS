// The left rail (DESIGN §4, kit.js Kit.rail). Order: logo · All teams · My queue · Compact · spacer
// · Theme · Settings · avatar. Sources is omitted (no sources yet). Labels are native titles plus
// aria-label; nothing is revealed on hover.
import type { ReactNode } from "react";
import { NavLink } from "react-router";

import { avatarTone, initials } from "../domain/format";
import { CompactIcon, LogoIcon, QueueIcon, SettingsIcon, TeamsIcon } from "./icons";
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
      <div className={s.logo} aria-hidden="true">
        <LogoIcon />
      </div>
      <RailLink to="/teams" label="All teams">
        <TeamsIcon />
      </RailLink>
      <RailLink to="/" label="My queue" badge={counts.needYou > 0}>
        <QueueIcon />
      </RailLink>
      <RailLink to="/compact" label="Compact">
        <CompactIcon />
      </RailLink>
      <div className={s.spacer} />
      <ThemeMenu />
      <RailLink to="/settings" label="Settings" section>
        <SettingsIcon />
      </RailLink>
      {me && (
        <span className={s.avatar} data-av={avatarTone(me.id)} title={me.display_name}>
          {initials(me.display_name)}
        </span>
      )}
    </nav>
  );
}
