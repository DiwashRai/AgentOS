// A signed-in user an admin hasn't approved yet (role "pending"). The API answers 403
// pending_approval to everything but /api/me, /api/config and logout, so the shell shows only
// this sheet. It re-checks /api/me every 15 s, so approval takes effect without a reload.
import type { Schemas } from "../../api/client";
import { useLogout, useMe } from "../../api/queries";
import { Button } from "../../ui/Button";
import s from "./LoginPage.module.css";
import { Logo } from "./Logo";
import p from "./PendingPage.module.css";

export const PENDING_POLL_MS = 15_000;

export function PendingPage({ me }: { me: Schemas["Me"] }) {
  useMe({ refetchInterval: PENDING_POLL_MS });
  const logout = useLogout();
  return (
    <main className={s.page}>
      <section className={s.sheet} aria-labelledby="pending-title">
        <div className={s.head}>
          <Logo />
          <h1 id="pending-title" className={s.title}>
            Waiting for an admin to approve you
          </h1>
        </div>
        <p className={p.who}>
          <span className={p.name}>{me.display_name}</span>
          {me.email && me.email !== me.display_name ? <span>{me.email}</span> : null}
        </p>
        <p className={s.note}>
          An admin approves new people in Settings › Users. This page opens the app as soon as they
          do.
        </p>
        {logout.error && (
          <p className={s.error} role="alert">
            {logout.error.message}
          </p>
        )}
        <div className={p.actions}>
          <Button
            disabled={logout.isPending}
            onClick={() => {
              logout.mutate();
            }}
          >
            {logout.isPending ? "Signing out…" : "Sign out"}
          </Button>
        </div>
      </section>
    </main>
  );
}
