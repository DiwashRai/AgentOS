// Routes (web/AGENTS.md "IA / routes"). Peek, filter and tab state live in the URL.
import { createBrowserRouter, Navigate } from "react-router";

import { LoginPage } from "../features/auth/LoginPage";
import { Shell } from "./Shell";
import { CompactView, TeamsView, TemplateView, TicketView } from "./views/placeholders";
import { QueueView } from "./views/QueueView";

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
      { path: "t/:key/:tab?", element: <TicketView /> },
      { path: "settings/template", element: <TemplateView /> },
    ],
  },
  { path: "*", element: <Navigate to="/" replace /> },
]);
