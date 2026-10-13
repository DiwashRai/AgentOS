// Settings: Space, Intake, Field mappings, Template, Runners, Users; one top bar with the section
// pills (kit .ao-pills, as links). Everything but Template is admin-only (those pages say so to
// others).
// Sign out lives here until the avatar gets a menu.
import type { ReactNode } from "react";
import { Navigate, NavLink } from "react-router";

import { useLogout, useMe } from "../../api/queries";
import { TemplatePage } from "../../features/pipeline";
import { IntakeSettingsPage, MappingsPage, RunnersPage, SpacePage } from "../../features/settings";
import { UsersPage } from "../../features/users";
import { cx } from "../../ui";
import { Quiet } from "../Quiet";
import { Surface } from "../Surface";
import { TopBar } from "../TopBar";
import s from "./Settings.module.css";
import v from "./views.module.css";

const SECTIONS = [
  { to: "/settings/space", label: "Space" },
  { to: "/settings/intake", label: "Intake" },
  { to: "/settings/mappings", label: "Field mappings" },
  { to: "/settings/template", label: "Template" },
  { to: "/settings/runners", label: "Runners" },
  { to: "/settings/users", label: "Users" },
] as const;

/** /settings: admins land on Space, everyone else on the Template (the one page they can read). */
export function SettingsIndex() {
  const me = useMe();
  if (me.isPending) return null;
  return (
    <Navigate to={me.data?.role === "admin" ? "/settings/space" : "/settings/template"} replace />
  );
}

/** Rendered twice: in the top bar, and (below 760px, instead) as a row at the top of the surface.
 *  CSS shows one at a time, so only one is ever in the accessibility tree. */
function SettingsNav({ place }: { place: "inBar" | "below" }) {
  return (
    <nav className={cx(s.pills, s[place])} aria-label="Settings">
      {SECTIONS.map((sec) => (
        <NavLink key={sec.to} to={sec.to} end>
          {sec.label}
        </NavLink>
      ))}
    </nav>
  );
}

function SettingsView({ children }: { children: ReactNode }) {
  const logout = useLogout();
  return (
    <>
      <TopBar
        title="Settings"
        middle={<SettingsNav place="inBar" />}
        search={false}
        action={
          <button
            type="button"
            className={v.quietButton}
            disabled={logout.isPending}
            onClick={() => {
              logout.mutate();
            }}
          >
            Sign out
          </button>
        }
      />
      <Surface className={v.scroll}>
        <SettingsNav place="below" />
        {logout.error && <Quiet error>{logout.error.message}</Quiet>}
        {children}
      </Surface>
    </>
  );
}

/** Settings › Space: the Space's name, keys, Jira projects, and its repo catalog (admin only). */
export function SpaceView() {
  return (
    <SettingsView>
      <SpacePage />
    </SettingsView>
  );
}

/** Settings › Intake: the JQL queries the Intake view lists (admin only). */
export function IntakeSettingsView() {
  return (
    <SettingsView>
      <IntakeSettingsPage />
    </SettingsView>
  );
}

/** Settings › Field mappings: Jira fields → ticket fields by rules, e.g. the base branch (admin only). */
export function MappingsView() {
  return (
    <SettingsView>
      <MappingsPage />
    </SettingsView>
  );
}

/** Settings › Runners: where runs launch, and the agent runtimes (admin only). */
export function RunnersView() {
  return (
    <SettingsView>
      <RunnersPage />
    </SettingsView>
  );
}

/** Settings › Template: the pipeline every new ticket starts from. */
export function TemplateView() {
  return (
    <SettingsView>
      <TemplatePage />
    </SettingsView>
  );
}

/** Settings › Users: roles and access (admin only). */
export function UsersView() {
  return (
    <SettingsView>
      <UsersPage />
    </SettingsView>
  );
}
