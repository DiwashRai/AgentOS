// Settings: Space, Intake, Field mappings, Workflows, Space defaults, Runners, Users, My client;
// one top bar with the section pills (kit .ao-pills, as links). Everything but Workflows, Space
// defaults and My client is admin-only (those pages say so to others; Workflows and Space defaults
// are read-only to them; My client is each user's own tokens). Sign out lives here until the
// avatar gets a menu.
import type { ReactNode } from "react";
import { Navigate, NavLink } from "react-router";

import { useLogout, useMe } from "../../api/queries";
import { TemplatePage } from "../../features/pipeline";
import {
  ClientsPage,
  IntakeSettingsPage,
  MappingsPage,
  RunnersPage,
  SpacePage,
} from "../../features/settings";
import { UsersPage } from "../../features/users";
import { WorkflowsPage } from "../../features/workflows";
import { cx, StatusLine } from "../../ui";
import { Surface } from "../Surface";
import { TopBar } from "../TopBar";
import s from "./Settings.module.css";
import v from "./views.module.css";

const SECTIONS = [
  { to: "/settings/space", label: "Space" },
  { to: "/settings/intake", label: "Intake" },
  { to: "/settings/mappings", label: "Field mappings" },
  { to: "/settings/workflows", label: "Workflows" },
  { to: "/settings/template", label: "Space defaults" },
  { to: "/settings/runners", label: "Runners" },
  { to: "/settings/users", label: "Users" },
  { to: "/settings/client", label: "My client" },
] as const;

/** /settings: admins land on Space, everyone else on Workflows (a page they can read). */
export function SettingsIndex() {
  const me = useMe();
  if (me.isPending) return null;
  return (
    <Navigate to={me.data?.role === "admin" ? "/settings/space" : "/settings/workflows"} replace />
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
        {logout.error && (
          <StatusLine inset error>
            {logout.error.message}
          </StatusLine>
        )}
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

/** Settings › Workflows: the named pipelines a new ticket copies from (everyone reads; admins edit). */
export function WorkflowsView() {
  return (
    <SettingsView>
      <WorkflowsPage />
    </SettingsView>
  );
}

/** Settings › Space defaults: the runtime, model and effort agent stages default to, and the
 *  scaffold files every new ticket starts with. */
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

/** Settings › My client: your own client tokens for running your tickets on your machine. */
export function ClientsView() {
  return (
    <SettingsView>
      <ClientsPage />
    </SettingsView>
  );
}
