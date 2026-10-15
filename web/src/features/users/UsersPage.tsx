// The Users page: every person who has signed in, with their role and whether they may sign in at
// all. DocumentPage draws its loading, admins-only (a non-admin, or a 403) and error states and its
// header; there is no document and no save bar, because each change applies at once. Renders a
// content column; the route wraps it in the shell's TopBar + Surface.
import { useState } from "react";

import { useMe, usePatchUser, useUsers } from "../../api/queries";
import { DocumentPage, pageStyles } from "../../editor";
import { useNow } from "../../ui";
import { ADMINS_ONLY, rowErrors, sortUsers, summary, type User } from "./model";
import { UserRow } from "./UserRow";
import s from "./Users.module.css";

interface PatchableRowProps {
  user: User;
  meId: number | undefined;
  now: number;
  /** This row made the page's latest change: only then are its problems shown. */
  latest: boolean;
  onStart: () => void;
}

/** One row with its own mutation, so pending and problems belong to that row only. */
function PatchableRow({ user, meId, now, latest, onStart }: PatchableRowProps) {
  const patch = usePatchUser();
  return (
    <UserRow
      user={user}
      self={user.id === meId}
      now={now}
      pending={patch.isPending}
      pendingRole={patch.variables?.role}
      errors={latest ? rowErrors(patch.error) : []}
      onPatch={(body) => {
        onStart();
        patch.mutate({ id: user.id, ...body });
      }}
    />
  );
}

export function UsersPage() {
  const me = useMe();
  const users = useUsers(me.data?.role === "admin");
  const now = useNow(60_000);
  // The row whose change started last; a change on another row clears this one's problems.
  const [latest, setLatest] = useState<number | null>(null);

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
          <span className={pageStyles.k}>Access</span>
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
            <PatchableRow
              key={u.id}
              user={u}
              meId={meId}
              now={now}
              latest={latest === u.id}
              onStart={() => {
                setLatest(u.id);
              }}
            />
          ))}
        </table>
      </div>
    </DocumentPage>
  );
}
