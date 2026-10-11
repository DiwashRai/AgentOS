// The Users page (SPEC §4 roles, §10 users routes): every person who has signed in, with their
// role and whether they may sign in at all. Admin only; everyone else gets one quiet line (the API
// would 403 anyway). Renders a content column; the route wraps it in the shell's TopBar + Surface.
import { ApiError } from "../../api/client";
import { useMe, usePatchUser, useUsers } from "../../api/queries";
import pages from "../pipeline/pages.module.css";
import { useNow } from "../runs/useNow";
import { describePatchError, locksFor, sortUsers, summary, type User } from "./model";
import { UserRow } from "./UserRow";
import s from "./Users.module.css";

const ADMINS_ONLY = "Only admins can manage users.";

function Line({ children, error }: { children: string; error?: boolean }) {
  return (
    <p
      className={pages.quiet}
      role={error ? "alert" : "status"}
      data-error={error ? "" : undefined}
    >
      {children}
    </p>
  );
}

/** One row with its own mutation, so pending and error belong to that row only. */
function PatchableRow({ user, meId, now }: { user: User; meId: number | undefined; now: number }) {
  const patch = usePatchUser();
  return (
    <UserRow
      user={user}
      self={user.id === meId}
      locks={locksFor(user, meId)}
      now={now}
      pending={patch.isPending}
      pendingRole={patch.variables?.role}
      error={patch.error ? describePatchError(patch.error) : null}
      onPatch={(body) => {
        patch.mutate({ id: user.id, ...body });
      }}
    />
  );
}

export function UsersPage() {
  const me = useMe();
  const isAdmin = me.data?.role === "admin";
  const users = useUsers(isAdmin);
  const now = useNow(60_000);

  if (me.isPending) return <Line>Loading users</Line>;
  if (!isAdmin) return <Line>{ADMINS_ONLY}</Line>;
  if (users.isPending) return <Line>Loading users</Line>;
  if (users.isError) {
    const e = users.error;
    if (e instanceof ApiError && e.status === 403) return <Line>{ADMINS_ONLY}</Line>;
    return <Line error>{`Can't load users · ${e.message}`}</Line>;
  }

  const list = sortUsers(users.data);
  const meId = me.data?.id;
  return (
    <div className={pages.page}>
      <header className={pages.bar}>
        <h2 className={pages.title}>Users</h2>
        <span className={pages.meta}>{summary(list)}</span>
      </header>

      <p className={pages.banner} role="note">
        <span className={pages.k}>Access</span>
        People appear here after their first sign-in, as viewers. Changes apply at once; disabling
        someone signs them out.
      </p>

      <div className={s.frame}>
        <table className={s.table}>
          <thead>
            <tr>
              <th scope="col">Person</th>
              <th scope="col">Role</th>
              <th scope="col">Status</th>
              <th scope="col">Last sign-in</th>
              <th scope="col">Created</th>
            </tr>
          </thead>
          {list.map((u) => (
            <PatchableRow key={u.id} user={u} meId={meId} now={now} />
          ))}
        </table>
      </div>
    </div>
  );
}
