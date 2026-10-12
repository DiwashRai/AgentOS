// Settings: Space, Intake, Template, Runners, Users; one top bar with the section pills (kit
// .ao-pills, as links). Everything but Template is admin-only (those pages say so to others).
// Sign out lives here until the avatar gets a menu.
import type { ReactNode } from "react";
import { Navigate, NavLink } from "react-router";

import { useLogout, useMe } from "../../api/queries";
import { TemplatePage } from "../../features/pipeline";
import { IntakeSettingsPage, RunnersPage, SpacePage } from "../../features/settings";
import { UsersPage } from "../../features/users";
import { Quiet } from "../Quiet";
import { Surface } from "../Surface";
import { TopBar } from "../TopBar";
import s from "./Settings.module.css";
import v from "./views.module.css";

const SECTIONS = [
  { to: "/settings/space", label: "Space" },
  { to: "/settings/intake", label: "Intake" },
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

function SettingsNav() {
  return (
    <nav className={s.pills} aria-label="Settings">
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
        middle={<SettingsNav />}
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
