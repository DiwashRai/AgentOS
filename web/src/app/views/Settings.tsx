// Settings: Template and Users, one top bar with the section pills (kit .ao-pills, as links).
// Sign out lives here until the avatar gets a menu.
import type { ReactNode } from "react";
import { NavLink } from "react-router";

import { useLogout } from "../../api/queries";
import { TemplatePage } from "../../features/pipeline";
import { UsersPage } from "../../features/users";
import { Quiet } from "../Quiet";
import { Surface } from "../Surface";
import { TopBar } from "../TopBar";
import s from "./Settings.module.css";
import v from "./views.module.css";

const SECTIONS = [
  { to: "/settings/template", label: "Template" },
  { to: "/settings/users", label: "Users" },
] as const;

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
