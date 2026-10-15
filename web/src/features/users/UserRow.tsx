// One user in the Users table: who, role, status, last sign-in, created. Changes apply at once
// (no Save); disabling asks first, inline, because it signs the person out. A pending person gets
// Approve (→ writer); the role picker can still approve straight to another role. Every control
// stays enabled except while a change is in flight: the server refuses what it won't do, and its
// answer shows under the row. Presentational: the page wires `onPatch` to the mutation and passes
// its pending state and problems back in.
import { useState } from "react";

import { Select } from "../../editor";
import { Avatar, Button, Tag } from "../../ui";
import {
  APPROVED_ROLE,
  ENV_ADMIN_NOTE,
  isWaiting,
  lastSignIn,
  type Role,
  ROLES,
  roleHint,
  shortDate,
  type User,
} from "./model";
import s from "./Users.module.css";

export interface UserRowProps {
  user: User;
  self: boolean;
  /** Epoch ms, for the last sign-in age. */
  now: number;
  /** A change is in flight: controls are disabled; `pendingRole` shows the role being set. */
  pending: boolean;
  pendingRole?: Role | undefined;
  /** The last change's problems (`rowErrors`), shown under the row. */
  errors: readonly string[];
  onPatch: (patch: { role?: Role; disabled?: boolean }) => void;
}

const ROLE_OPTIONS = ROLES.map((r) => ({ value: r.value, label: r.label }));
const COLUMNS = 5;

function isRole(v: string): v is Role {
  return ROLES.some((r) => r.value === v);
}

function stateWord(user: User): string {
  if (user.disabled) return "Disabled";
  return isWaiting(user) ? "Waiting for approval" : "Active";
}

export function UserRow({ user, self, now, pending, pendingRole, errors, onPatch }: UserRowProps) {
  const [confirming, setConfirming] = useState(false);
  const role = pending && pendingRole ? pendingRole : user.role;
  const name = user.display_name;
  const waiting = isWaiting(user);

  return (
    <tbody
      className={s.user}
      data-disabled={user.disabled ? "" : undefined}
      data-waiting={waiting ? "" : undefined}
      aria-busy={pending}
    >
      <tr>
        <th scope="row">
          <span className={s.who}>
            <Avatar name={name} id={user.id} size={26} />
            <span className={s.names}>
              <span className={s.name}>
                {name}
                {self ? <Tag>You</Tag> : null}
              </span>
              {user.email ? <span className={s.email}>{user.email}</span> : null}
            </span>
          </span>
        </th>
        <td>
          <span className={s.role}>
            <Select
              size="sm"
              aria-label={`Role for ${name}`}
              value={role}
              options={ROLE_OPTIONS}
              disabled={pending}
              onChange={(v) => {
                if (isRole(v) && v !== user.role) onPatch({ role: v });
              }}
            />
            {user.env_admin ? (
              <span className={s.config}>{ENV_ADMIN_NOTE}</span>
            ) : (
              <span className={s.hint}>{roleHint(role)}</span>
            )}
          </span>
        </td>
        <td>
          <span className={s.status}>
            <span className={s.state}>{stateWord(user)}</span>
            {waiting ? (
              <Button
                size="sm"
                variant="ink"
                aria-label={`Approve ${name}`}
                title={`Make ${name} a writer: they can create and dispatch tickets`}
                disabled={pending}
                onClick={() => {
                  onPatch({ role: APPROVED_ROLE });
                }}
              >
                Approve
              </Button>
            ) : null}
            {user.disabled ? (
              <Button
                size="sm"
                variant="quiet"
                aria-label={`Enable ${name}`}
                disabled={pending}
                onClick={() => {
                  onPatch({ disabled: false });
                }}
              >
                Enable
              </Button>
            ) : (
              <Button
                size="sm"
                variant="quiet"
                aria-label={`Disable ${name}`}
                aria-expanded={confirming}
                disabled={pending || confirming}
                onClick={() => {
                  setConfirming(true);
                }}
              >
                Disable
              </Button>
            )}
          </span>
        </td>
        <td className={s.time}>{lastSignIn(user.last_login_at, now)}</td>
        <td className={s.time}>{shortDate(user.created_at)}</td>
      </tr>
      {confirming && !user.disabled ? (
        <tr className={s.note}>
          <td colSpan={COLUMNS}>
            <span className={s.confirm} role="group" aria-label={`Disable ${name}`}>
              <span>{`Disable ${name}? They're signed out now.`}</span>
              <Button
                size="sm"
                variant="ink"
                onClick={() => {
                  setConfirming(false);
                  onPatch({ disabled: true });
                }}
              >
                Disable
              </Button>
              <Button
                size="sm"
                variant="quiet"
                onClick={() => {
                  setConfirming(false);
                }}
              >
                Cancel
              </Button>
            </span>
          </td>
        </tr>
      ) : null}
      {errors.length > 0 ? (
        <tr className={s.note}>
          <td colSpan={COLUMNS}>
            <span className={s.error} role="alert">
              {errors.join(" ")}
            </span>
          </td>
        </tr>
      ) : null}
    </tbody>
  );
}
