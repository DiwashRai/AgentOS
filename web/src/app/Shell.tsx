// The signed-in app: auth guard + the shell grid (rail + main column). Signed out → /login?next=…
import { Navigate, Outlet, useLocation } from "react-router";

import { useMe } from "../api/queries";
import { NewTicketHost } from "../features/newticket/NewTicketHost";
import { PeekHost } from "../features/peek/PeekHost";
import { Quiet } from "./Quiet";
import { Rail } from "./Rail";
import s from "./Shell.module.css";

export function Shell() {
  const me = useMe();
  const location = useLocation();
  if (me.isPending) return <Quiet>Loading</Quiet>;
  if (me.isError) return <Quiet error>{`Can't reach the server · ${me.error.message}`}</Quiet>;
  if (!me.data) {
    const next = `${location.pathname}${location.search}`;
    const to = next === "/" ? "/login" : `/login?next=${encodeURIComponent(next)}`;
    return <Navigate to={to} replace />;
  }
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
