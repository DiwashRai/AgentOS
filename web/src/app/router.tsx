// Routes (web/AGENTS.md "IA / routes"). Peek, filter and tab state live in the URL.
import { createBrowserRouter, Navigate } from "react-router";

import { LoginPage } from "../features/auth/LoginPage";
import { CompactView } from "../features/compact/CompactView";
import { IntakeView } from "../features/intake/IntakeView";
import { QueueView } from "../features/queue/QueueView";
import { TicketPage } from "../features/ticket/TicketPage";
import { Shell } from "./Shell";
import { TeamsView } from "./views/placeholders";
import {
  ClientsView,
  IntakeSettingsView,
  MappingsView,
  RunnersView,
  SettingsIndex,
  SpaceView,
  TemplateView,
  UsersView,
  WorkflowsView,
} from "./views/Settings";

export const router = createBrowserRouter([
  { path: "/login", element: <LoginPage /> },
  // The component sheet (both themes side by side), outside the shell and the auth guard.
  { path: "/kit", lazy: async () => ({ Component: (await import("../ui/KitSheet")).default }) },
  {
    element: <Shell />,
    children: [
      { index: true, element: <QueueView /> },
      { path: "compact", element: <CompactView /> },
      { path: "intake", element: <IntakeView /> },
      { path: "teams", element: <TeamsView /> },
      { path: "t/:key/:tab?", element: <TicketPage /> },
      // The rail's Settings link points at /settings, so it stays current on every section.
      { path: "settings", element: <SettingsIndex /> },
      { path: "settings/space", element: <SpaceView /> },
      { path: "settings/intake", element: <IntakeSettingsView /> },
      { path: "settings/mappings", element: <MappingsView /> },
      { path: "settings/workflows", element: <WorkflowsView /> },
      { path: "settings/template", element: <TemplateView /> },
      { path: "settings/runners", element: <RunnersView /> },
      { path: "settings/users", element: <UsersView /> },
      { path: "settings/client", element: <ClientsView /> },
    ],
  },
  { path: "*", element: <Navigate to="/" replace /> },
]);
