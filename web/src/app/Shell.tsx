// The signed-in app: auth guard + the shell grid (rail + main column). Signed out → /login?next=…
import { Navigate, Outlet, useLocation } from "react-router";

import { useMe } from "../api/queries";
import { PendingPage } from "../features/auth/PendingPage";
import { NewTicketHost } from "../features/newticket/NewTicketHost";
import { PeekHost } from "../features/peek/PeekHost";
import { StatusLine } from "../ui";
import { Rail } from "./Rail";
import s from "./Shell.module.css";

export function Shell() {
  const me = useMe();
  const location = useLocation();
  if (me.isPending) return <StatusLine inset>Loading</StatusLine>;
  if (me.isError)
    return <StatusLine inset error>{`Can't reach the server · ${me.error.message}`}</StatusLine>;
  if (!me.data) {
    const next = `${location.pathname}${location.search}`;
    const to = next === "/" ? "/login" : `/login?next=${encodeURIComponent(next)}`;
    return <Navigate to={to} replace />;
  }
  // Not approved yet: nothing else mounts, so no board query fires (they'd all answer 403).
  if (me.data.role === "pending") return <PendingPage me={me.data} />;
  return (
    <div className={s.app}>
      <Rail />
      <main className={s.main}>
        <Outlet />
      </main>
      {/* URL-driven overlays over every view: ?peek=KEY and ?new=1 */}
      <PeekHost />
      <NewTicketHost />
    </div>
  );
}
