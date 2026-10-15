// The Users page: every person who has signed in, with their role and whether they may sign in at
// all. DocumentPage draws its loading, admins-only (a non-admin, or a 403) and error states and its
// header; there is no document and no save bar, because each change applies at once. Renders a
// content column; the route wraps it in the shell's TopBar + Surface.
import { useMe, usePatchUser, useUsers } from "../../api/queries";
import { DocumentPage } from "../../editor";
import { text, useNow } from "../../ui";
import { ADMINS_ONLY, rowErrors, sortUsers, summary, type User } from "./model";
import { UserRow } from "./UserRow";
import s from "./Users.module.css";

interface PatchableRowProps {
  user: User;
  meId: number | undefined;
  now: number;
}

/** One row with its own mutation, so pending and problems belong to that row only. */
function PatchableRow({ user, meId, now }: PatchableRowProps) {
  const patch = usePatchUser();
  return (
    <UserRow
      user={user}
      self={user.id === meId}
      now={now}
      pending={patch.isPending}
      pendingRole={patch.variables?.role}
      errors={rowErrors(patch.error)}
      onPatch={(body) => {
        patch.mutate({ id: user.id, ...body });
      }}
    />
  );
}

export function UsersPage() {
  const me = useMe();
  const users = useUsers(me.data?.role === "admin");
  const now = useNow(60_000);

  const list = users.data === undefined ? [] : sortUsers(users.data);
  const meId = me.data?.id;

  return (
    <DocumentPage
      viewer={me.data}
      adminsOnly={ADMINS_ONLY}
      noun="users"
      sources={[users]}
      title="Users"
      meta={summary(list)}
      description={
        <>
          <span className={text.label}>Access</span>
          People appear here after their first sign-in, waiting for approval: they see nothing until
          an admin approves them. Changes apply at once; disabling someone signs them out.
        </>
      }
    >
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
    </DocumentPage>
  );
}
