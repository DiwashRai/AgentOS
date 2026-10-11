// Routes (web/AGENTS.md "IA / routes"). Peek, filter and tab state live in the URL.
import { createBrowserRouter, Navigate } from "react-router";

import { LoginPage } from "../features/auth/LoginPage";
import { Shell } from "./Shell";
import { CompactView, TeamsView, TemplateView, TicketView } from "./views/placeholders";
import { QueueView } from "./views/QueueView";

export const router = createBrowserRouter([
  { path: "/login", element: <LoginPage /> },
  // ── Orchestrator: add the component sheet here, e.g. { path: "/kit", element: <KitSheet /> } ──
  {
    element: <Shell />,
    children: [
      { index: true, element: <QueueView /> },
      { path: "compact", element: <CompactView /> },
      { path: "teams", element: <TeamsView /> },
      { path: "t/:key/:tab?", element: <TicketView /> },
      { path: "settings/template", element: <TemplateView /> },
    ],
  },
  { path: "*", element: <Navigate to="/" replace /> },
]);
