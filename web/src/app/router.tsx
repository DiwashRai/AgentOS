// Routes (web/AGENTS.md "IA / routes"). Peek, filter and tab state live in the URL.
import { createBrowserRouter, Navigate } from "react-router";

import { LoginPage } from "../features/auth/LoginPage";
import { CompactView } from "../features/compact/CompactView";
import { QueueView } from "../features/queue/QueueView";
import { TicketPage } from "../features/ticket/TicketPage";
import { Shell } from "./Shell";
import { TeamsView, TemplateView } from "./views/placeholders";

export const router = createBrowserRouter([
  { path: "/login", element: <LoginPage /> },
  // The component sheet (both themes side by side), outside the shell and the auth guard.
  { path: "/kit", lazy: async () => ({ Component: (await import("../ui/KitSheet")).default }) },
  {
    element: <Shell />,
    children: [
      { index: true, element: <QueueView /> },
      { path: "compact", element: <CompactView /> },
      { path: "teams", element: <TeamsView /> },
      { path: "t/:key/:tab?", element: <TicketPage /> },
      { path: "settings/template", element: <TemplateView /> },
    ],
  },
  { path: "*", element: <Navigate to="/" replace /> },
]);
